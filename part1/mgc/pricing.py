"""Deterministic pricing engine. Every rupee figure in a pricing answer comes from here.

The LLM (if enabled) only turns the question into parameters; it never sees or
produces a number. Base prices are PARSED from the price-list tables (and each row is
checked: area x rate == base price). The rule percentages are coded below and
verified against the document text at startup, so a changed price list fails loudly
instead of silently producing wrong quotes.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal as D, ROUND_HALF_UP

from .ingest import Corpus, clean
from .models import Answer, Source

PL, BR, BP = "price_list", "brochure", "booking_policy"

# (first floor, last floor, percent, verbatim text in the price list)
FLOOR_PREMIUMS = [
    (13, 19, D(4), "Floors 13–19: +4%"),
    (20, 22, D(7), "Floors 20–22: +7%"),
]
CORNER_PCT, CORNER_Q = D(3), "Corner unit: +3%"
MARGALLA_PCT, MARGALLA_Q = D(6), "Margalla-facing: +6%"

DISCOUNTS = {  # key: (label, percent, verbatim text in the price list)
    "cash": ("Full cash payment", D(12), "Full cash payment: 12% discount on base price."),
    "upfront50": ("50% upfront", D(6), "50% upfront: 6% discount on base price."),
    "overseas": ("Overseas Pakistani", D(2),
                 "Overseas Pakistani (Roshan Digital Account): additional 2%, stackable with the above."),
    "second_unit": ("Second unit by same buyer", D("1.5"),
                    "Second unit by the same buyer: 1.5% on the second unit only."),
}
TOKEN_PCT = D(10)
PLAN = [  # label, percent
    ("Booking / token", D(10)),
    ("Confirmation (due within 45 days of booking)", D(10)),
    ("16 quarterly instalments (3.75% each) - total", D(60)),
    ("On possession", D(20)),
]
NO_MARGALLA = ("Studio", "1-Bed Standard")

# Phrases that must exist in the price list for the constants above to be trusted.
RULE_EVIDENCE = ["+4%", "+7%", "+3%", "+6%", "12% discount", "6% discount", "additional 2%",
                 "1.5% on the second unit", "3.75%", "285,000", "1,450,000", "9.50 per sq ft",
                 "Booking / token | 10%", "Confirmation | 10%", "On possession | 20%"]

DEFAULTS = dict(unit_type=None, block=None, floor=None, margalla=False, corner=False,
                cash=False, upfront50=False, overseas=False, second_unit=False,
                wants_booking=False, wants_plan=False)


def pkr(x) -> int:
    return int(D(x).quantize(D(1), rounding=ROUND_HALF_UP))


def fmt(n: int) -> str:
    return f"PKR {n:,}"


def pct(p: D) -> str:
    return format(p.normalize(), "f")


@dataclass(frozen=True)
class Unit:
    type: str
    block: str
    area: int
    base: int
    psf: int
    row: str  # the raw table row, used as the citation quote


def verify_rules(corpus: Corpus) -> None:
    text = " ".join(clean(s.text) for s in corpus.sections if s.doc_id == PL)
    text = re.sub(r"\s+", " ", text)
    missing = [e for e in RULE_EVIDENCE if e not in text]
    if missing:
        raise RuntimeError(f"Price list no longer contains {missing}; update pricing.py")


def load_base_prices(corpus: Corpus) -> dict:
    units = {}
    for s in corpus.sections:
        if s.doc_id != PL or not s.heading.startswith("Base Prices"):
            continue
        block = re.search(r"Block ([AB])", s.heading).group(1)
        for line in s.text.splitlines():
            if not line.startswith("|"):
                continue
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if len(cells) < 4 or not re.search(r"\d", cells[1]):   # header / separator
                continue
            area = int(re.sub(r"\D", "", cells[1]))
            base, psf = int(cells[2].replace(",", "")), int(cells[3].replace(",", ""))
            if area * psf != base:
                raise ValueError(f"Price list row inconsistent: {line}")
            units[(block, cells[0])] = Unit(cells[0], block, area, base, psf, line.strip())
    return units


def _row(corpus: Corpus, doc_id: str, heading: str, first_cell: str) -> str:
    for line in corpus.find(doc_id, heading).text.splitlines():
        if line.startswith("|") and line.strip("| ").split("|")[0].strip() == first_cell:
            return line.strip()
    raise KeyError(first_cell)


def _line(label: str, value: str) -> str:
    return f"  {label:<46}{value:>18}"


def quote(req: dict, corpus: Corpus, units: dict) -> Answer:
    r = {**DEFAULTS, **req}
    ut, block, floor = r["unit_type"], r["block"], r["floor"]
    S: list = []
    notes: list = []

    def add(doc, heading, q):
        s = corpus.source(doc, heading, q)
        if s not in S:
            S.append(s)

    def stop(status, text):
        return Answer(status, text, S, notes)

    if not ut:
        return stop("needs_clarification",
                    "Which unit type? Studio, 1-Bed Standard, 2-Bed Standard, 2-Bed Corner, "
                    "3-Bed Executive or 4-Bed Penthouse.")

    generic2 = ut == "2-Bed"
    if generic2 and r["corner"]:
        ut, generic2 = "2-Bed Corner", False
    if r["corner"] and ut != "2-Bed Corner":
        add(BR, "Unit Configuration", _row(corpus, BR, "Unit Configuration", "2-Bed Corner"))
        return stop("cannot_price", f"The documents list a corner variant only for the 2-bed "
                                    f"(2-Bed Corner, Block B). There is no corner {ut}.")

    pure = not any([floor, r["margalla"], r["cash"], r["upfront50"], r["overseas"],
                    r["second_unit"], r["wants_booking"], r["wants_plan"]])

    # ---- resolve block -------------------------------------------------------
    probe = "2-Bed Standard" if generic2 else ut
    if block is None:
        blocks = [b for b in "AB" if (b, probe) in units]
        if not blocks:
            return stop("cannot_price", f"'{ut}' is not in the price list.")
        if len(blocks) == 1:
            block = blocks[0]
            add(BR, "Unit Configuration", _row(corpus, BR, "Unit Configuration", probe))
            notes.append(f"Block not given: the {probe} is only listed in Block {block}.")
        else:
            for b in blocks:
                add(PL, f"Base Prices (Block {b})", units[(b, probe)].row)
            opts = "; ".join(f"Block {b}: {fmt(units[(b, probe)].base)}" for b in blocks)
            return stop("needs_clarification",
                        f"Which block? The {probe} is priced differently by block - {opts}.")

    # ---- resolve generic "2-bed" ---------------------------------------------
    if generic2:
        cands = [t for t in ("2-Bed Standard", "2-Bed Corner") if (block, t) in units]
        if not cands:
            return stop("cannot_price", f"No 2-bed is listed in Block {block}.")
        if len(cands) > 1:
            for t in cands:
                add(PL, f"Base Prices (Block {block})", units[(block, t)].row)
            if not pure:
                opts = "; ".join(f"{t}: {fmt(units[(block, t)].base)}" for t in cands)
                return stop("needs_clarification",
                            f"Block {block} has two 2-bed types - {opts}. Standard or Corner?")
            first, rest = units[(block, cands[0])], units[(block, cands[1])]
            notes.append("Base prices only. Location premiums and discounts change the final price.")
            return Answer(
                "answered",
                f"The base price of a 2-bed in Block {block} is {fmt(first.base)} for the "
                f"{first.type} ({first.area:,} sq ft). Block {block} also has a {rest.type} at "
                f"{fmt(rest.base)} ({rest.area:,} sq ft).",
                S, notes, {"base_prices": {t: units[(block, t)].base for t in cands}})
        ut = cands[0]

    u = units.get((block, ut))
    if u is None:
        add(BR, "Unit Configuration", _row(corpus, BR, "Unit Configuration", ut))
        return stop("cannot_price", f"The {ut} is not listed in Block {block}.")
    add(PL, f"Base Prices (Block {block})", u.row)

    # ---- pure base-price lookup ----------------------------------------------
    if pure:
        if ut == "2-Bed Corner":
            add(PL, "Location Premiums", CORNER_Q)
            notes.append("A +3% corner premium applies on top of this base price.")
        notes.append("Base price only. Location premiums (floor, corner, Margalla) and "
                     "discounts change the final price.")
        return Answer("answered",
                      f"The base price of the {ut} in Block {block} is {fmt(u.base)} "
                      f"({u.area:,} sq ft at PKR {u.psf:,} per sq ft).",
                      S, notes, {"base": u.base})

    # ---- validate floor / orientation ------------------------------------------
    penthouse = ut == "4-Bed Penthouse"
    if floor is not None:
        if not 1 <= floor <= 22:
            return stop("cannot_price", "The tower has floors 1 to 22.")
        if floor <= 3:
            add(BR, "Overview", "with a commercial podium occupying floors 1 to 3")
            return stop("cannot_price", f"Floors 1-3 are the commercial podium, so there is no "
                                        f"residential unit on floor {floor}.")
        if penthouse and floor < 20:
            add(BR, "Unit Configuration", _row(corpus, BR, "Unit Configuration", ut))
            return stop("cannot_price", "Penthouses are on floors 20-22 only.")

    above12 = block == "B" and ((floor is not None and floor > 12) or (floor is None and penthouse))
    if ut in NO_MARGALLA and (r["margalla"] or above12):
        add(BR, "Unit Configuration",
            "Studio and 1-Bed units are not available with a Margalla-facing orientation.")
        if above12:
            add(BR, "Unit Configuration", "All units above floor 12 in Block B face the Margalla range.")
            msg = (f"I can't price this reliably. The brochure says Studio and 1-Bed units are not "
                   f"available Margalla-facing, and that all Block B units above floor 12 face "
                   f"Margalla. The documents don't say how a {ut} on floor {floor} is priced - "
                   f"check with your manager.")
        else:
            msg = f"The brochure says {ut} units are not available with a Margalla-facing orientation."
        return stop("cannot_price", msg)

    # ---- premiums ----------------------------------------------------------------
    prem = []  # (label, percent)
    if floor is None and penthouse:
        add(PL, "Location Premiums", "Floors 20–22: +7%")
        prem.append(("Floors 20-22 (all penthouses)", D(7)))
        notes.append("No floor given: penthouses are on floors 20-22, so the +7% floor premium applies.")
    elif floor is not None:
        for lo, hi, p, q in FLOOR_PREMIUMS:
            if lo <= floor <= hi:
                add(PL, "Location Premiums", q)
                prem.append((f"Floor {floor}", p))
    if ut == "2-Bed Corner":
        add(PL, "Location Premiums", CORNER_Q)
        prem.append(("Corner unit", CORNER_PCT))
        notes.append("The 2-Bed Corner base price already has a higher per-sq-ft rate (20,500 vs 19,500). "
                     "The +3% corner premium was still applied, as the price list states premiums are cumulative.")
    margalla = r["margalla"] or above12
    if margalla:
        add(PL, "Location Premiums", MARGALLA_Q)
        prem.append(("Margalla-facing", MARGALLA_PCT))
        if above12:
            add(BR, "Unit Configuration", "All units above floor 12 in Block B face the Margalla range.")
        if above12 and not r["margalla"]:
            notes.append("All Block B units above floor 12 face Margalla (brochure), so +6% was applied.")
        if r["margalla"] and block == "A":
            notes.append("The brochure only states Margalla-facing units for Block B above floor 12. "
                         "+6% was applied as you asked - verify availability.")
        if r["margalla"] and block == "B" and floor is not None and floor <= 12:
            notes.append("The brochure only states Margalla-facing units above floor 12 in Block B. "
                         "+6% was applied as you asked - verify availability.")
    if prem:
        add(PL, "Location Premiums", "Premiums are cumulative.")
    if floor is None and not penthouse:
        extra = (" and the +6% Margalla premium (Block B above floor 12)"
                 if block == "B" and ut not in NO_MARGALLA else "")
        notes.append(f"No floor given: floor premiums (+4% floors 13-19, +7% floors 20-22){extra} "
                     f"are NOT included.")

    # ---- discounts -----------------------------------------------------------------
    if r["cash"] and r["upfront50"]:
        return stop("needs_clarification", "Full cash and 50% upfront are separate discount tiers "
                                           "(12% vs 6%). Which one applies?")
    disc = [k for k in ("cash", "upfront50", "overseas", "second_unit") if r[k]]
    for k in disc:
        add(PL, "Discounts", DISCOUNTS[k][2])
    if r["overseas"]:
        add(BP, "Overseas Buyers", "Overseas buyers receive an additional 2% discount")
        if len(disc) == 1:
            notes.append("Overseas discount applied on its own; the price list words it as "
                         "'additional ... stackable with the above'. Confirm.")
    if disc:
        notes.append("Discounts are taken on the BASE price only; location premiums are not discounted "
                     "(the price list states this for full cash; applied to all tiers).")
    if len(disc) > 1:
        notes.append("Multiple discounts were ADDED together (the price list says 'stackable').")

    # ---- the arithmetic ------------------------------------------------------------------
    base = D(u.base)
    prem_items = [(lab, p, pkr(base * p / 100)) for lab, p in prem]
    disc_items = [(DISCOUNTS[k][0], DISCOUNTS[k][1], pkr(base * DISCOUNTS[k][1] / 100)) for k in disc]
    prem_total = pkr(base * sum((p for _, p in prem), D(0)) / 100)
    disc_total = pkr(base * sum((d[1] for d in disc_items), D(0)) / 100)
    net = u.base - disc_total + prem_total
    token = pkr(D(net) * TOKEN_PCT / 100)

    if len(disc) > 1:
        factor = D(1)
        for _, p, _ in disc_items:
            factor *= (1 - p / 100)
        alt = pkr(base * factor) + prem_total
        notes.append(f"If MGC instead applies the discounts one after another, the price would be "
                     f"{fmt(alt)} (difference {fmt(abs(alt - net))}). Confirm which method MGC uses.")

    lines = [f"{ut} - Block {block} ({u.area:,} sq ft)", _line("Base price", fmt(u.base))]
    for lab, p, amt in prem_items:
        lines.append(_line(f"+ {lab} ({pct(p)}% of base)", fmt(amt)))
    if len(prem_items) > 1:
        total_p = sum((p for _, p, _ in prem_items), D(0))
        lines.append(_line(f"  = Location premiums, cumulative ({pct(total_p)}%)", fmt(prem_total)))
    for lab, p, amt in disc_items:
        lines.append(_line(f"- {lab} ({pct(p)}% of base)", "-" + fmt(amt)))
    label = ("Price after discounts" if disc else
             "Unit price (base + premiums)" if prem else "Unit price")
    lines.append(_line(label, fmt(net)))

    if r["wants_booking"] or r["upfront50"] or r["wants_plan"]:
        add(PL, "Standard Payment Plan", "Booking / token | 10% | Non-refundable after 30 days")
        lines.append(_line(f"Due at booking ({pct(TOKEN_PCT)}% token, standard plan)", fmt(token)))
        add(BP, "Booking Process", "Token amount is paid into the project escrow account. "
                                   "Cash is not accepted above PKR 500,000.")
        if token > 500_000:
            notes.append(f"The token ({fmt(token)}) is above PKR 500,000, so it cannot be paid in cash; "
                         f"it must go into the escrow account.")
    if r["upfront50"]:
        notes.append(f"The documents define a 10% booking token (standard plan) but do NOT define a payment "
                     f"schedule for the 50%-upfront option. 50% of the price above is "
                     f"{fmt(pkr(D(net) * 50 / 100))}. Confirm with management whether that is due at booking "
                     f"or by the confirmation milestone.")
    if r["wants_plan"]:
        add(PL, "Standard Payment Plan", "Confirmation | 10% | Due within 45 days of booking")
        add(PL, "Standard Payment Plan", "On possession | 20% | Due at handover")
        lines.append("  Standard payment plan on this price:")
        for lab, p in PLAN:
            lines.append(_line("  " + lab, fmt(pkr(D(net) * p / 100))))
        notes.append("Payment plan percentages were applied to the price after discounts and premiums.")

    # separate charges: shown, never silently added
    maint = pkr(D(12) * D("9.50") * u.area)
    add(PL, "Other Charges", "Utility connection and meter charges: 285,000 per residential unit")
    add(PL, "Other Charges", "Parking slot (mandatory, 1 per unit): 1,450,000")
    add(PL, "Other Charges", "Maintenance advance: 12 months at 9.50 per sq ft, payable at possession")
    lines += ["  Not included above (separate charges):",
              _line("  Utility connection & meter", fmt(285_000)),
              _line("  Parking slot (mandatory, 1 per unit)", fmt(1_450_000)),
              _line("  Maintenance advance (at possession)", fmt(maint))]

    calc = {"base": u.base, "premiums": [dict(label=l, pct=float(p), amount=a) for l, p, a in prem_items],
            "discounts": [dict(label=l, pct=float(p), amount=a) for l, p, a in disc_items],
            "price": net, "booking_token": token, "maintenance_advance": maint}
    return Answer("answered", "\n".join(lines), S, notes, calc)
