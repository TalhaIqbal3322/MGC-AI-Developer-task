"""Question -> pricing parameters.

Two interchangeable extractors:
  * regex_extract  - deterministic, works offline (default)
  * LLM extraction - used when ANTHROPIC_API_KEY is set; output passes through sanitize()
Either way the result is a plain dict of parameters. No numbers are extracted or computed here.
"""
from __future__ import annotations

import re

UNIT_TYPES = ["Studio", "1-Bed Standard", "2-Bed", "2-Bed Standard", "2-Bed Corner",
              "3-Bed Executive", "4-Bed Penthouse"]
BOOLS = ["margalla", "corner", "cash", "upfront50", "overseas", "second_unit",
         "wants_booking", "wants_plan"]

_PRICE_WORDS = re.compile(
    r"\b(price|prices|cost|costs|total|how much|due|pay|pays|paying|payable|discount|discounts|"
    r"token|booking amount|down ?payment|worth)\b", re.I)


def _unit(ql: str):
    if "studio" in ql:
        return "Studio"
    if "penthouse" in ql or re.search(r"\b4\s*-?\s*bed", ql):
        return "4-Bed Penthouse"
    if "executive" in ql or re.search(r"\b(3|three)\s*-?\s*bed", ql):
        return "3-Bed Executive"
    if re.search(r"\b(2|two)\s*-?\s*bed", ql):
        return "2-Bed Corner" if "corner" in ql else "2-Bed"
    if re.search(r"\b(1|one)\s*-?\s*bed", ql):
        return "1-Bed Standard"
    return None


def regex_extract(question: str):
    """Return a params dict if this is a pricing question, else None."""
    ql = question.lower()
    unit = _unit(ql)
    if not unit or not _PRICE_WORDS.search(ql) or re.search(r"\b(commercial|shop|office)\b", ql):
        return None
    block = re.search(r"\bblock\s*([ab])\b", ql)
    floor = (re.search(r"\bfloor\s*(?:no\.?\s*)?(\d{1,2})\b", ql)
             or re.search(r"\b(\d{1,2})(?:st|nd|rd|th)\s+floor\b", ql))
    return dict(
        unit_type=unit,
        block=block.group(1).upper() if block else None,
        floor=int(floor.group(1)) if floor else None,
        margalla="margalla" in ql,
        corner="corner" in ql,
        cash=bool(re.search(r"\b(full|all)[- ]cash\b|cash payment|pays? (?:in )?cash|lump[- ]?sum", ql)),
        upfront50=bool(re.search(r"\b50\s*(?:%|percent|per cent)\s*(?:upfront|up front|up-front|down|advance)", ql)
                       or "half upfront" in ql),
        overseas=bool(re.search(r"overseas|roshan|abroad|non[- ]resident", ql)),
        second_unit=bool(re.search(r"second (?:unit|apartment|flat)", ql)),
        wants_booking=bool(re.search(r"at booking|booking amount|booking token|\btoken\b|due now|"
                                     r"down ?payment|on booking|to book", ql)),
        wants_plan=bool(re.search(r"payment plan|instal+ments?|quarterly|schedule|milestone", ql)),
    )


def sanitize(d: dict):
    """Validate LLM-produced parameters. Anything unexpected becomes None/False."""
    if not d.get("is_pricing_question"):
        return None
    out = {}
    ut = d.get("unit_type")
    out["unit_type"] = ut if ut in UNIT_TYPES else None
    b = str(d.get("block") or "").upper()
    out["block"] = b if b in ("A", "B") else None
    f = d.get("floor")
    out["floor"] = int(f) if isinstance(f, (int, float)) and not isinstance(f, bool) else None
    for k in BOOLS:
        out[k] = bool(d.get(k))
    return out


LLM_SYSTEM = """You convert a salesperson's question about apartment pricing into JSON parameters.
Return ONLY a JSON object with exactly these keys:
is_pricing_question (bool: true only if they want a price, total, discount result or amount due for a residential unit),
unit_type (one of "Studio","1-Bed Standard","2-Bed","2-Bed Standard","2-Bed Corner","3-Bed Executive","4-Bed Penthouse", or null; use "2-Bed" if they say 2-bed without saying standard or corner),
block ("A","B" or null), floor (integer or null),
margalla, corner, cash (full cash payment), upfront50 (50% paid upfront), overseas (overseas Pakistani / Roshan Digital Account),
second_unit, wants_booking (asks what is due at booking / the token), wants_plan (asks for the instalment schedule) - all booleans.
Only record what the question states. Never calculate or output any price."""
