"""Runs the six required questions plus edge cases. Exits non-zero if any expectation fails.
    python run_questions.py
"""
import sys

from mgc import Assistant

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def show(q, a):
    print("=" * 78)
    print("Q:", q)
    print(f"[{a.status}]")
    print(a.text)
    if a.notes:
        print("\nFlags / assumptions:")
        for n in a.notes:
            print(" *", n)
    print("\nSources:")
    seen = set()
    for s in a.sources:
        if (s.doc, s.section) not in seen:
            seen.add((s.doc, s.section))
            print(f"  - {s.doc} > {s.section}")


# (question, expected status, strings that must appear in the answer text)
CASES = [
    ("What's the base price of a 2-bed in Block B?", "answered", ["22,425,000", "26,855,000"]),
    ("What's the total for a Margalla-facing corner unit on floor 15, 2-bed Block B?",
     "answered", ["30,346,150", "3,491,150"]),
    ("An overseas Pakistani buyer pays 50% upfront on a 3-Bed Executive — what's the price after "
     "discounts, and what's due at booking?", "answered", ["36,321,600", "3,632,160"]),
    ("What's the transfer fee?", "conflict", ["2%", "2.5%", "disagree"]),
    ("What's the rental yield on a 1-bed?", "restricted", ["marketing manager"]),
    ("Who is the anchor tenant?", "unconfirmed", ["no anchor tenant has been confirmed"]),
    # edge cases
    ("What is the weather in Islamabad?", "not_in_documents", []),
    ("Who is the CEO of MGC?", "not_in_documents", []),
    ("What's the price of a 1-bed Margalla facing on floor 15 Block B?", "cannot_price", []),
    ("Price of a 1-bed?", "needs_clarification", ["13,320,000", "13,680,000"]),
    ("Is a home loan available and what is the mark-up rate?", "restricted", []),
    ("Is the project gas-connected?", "unconfirmed", []),
    ("What are the office opening hours?", "answered", ["10:00"]),
    ("Who is the head of marketing at MGC?", "not_in_documents", []),
    ("What is the possession date for Block B?", "not_in_documents", []),
    ("What is the possession date for Block A?", "answered", ["Q3 2027"]),
    ("What happens if I cancel after 30 days?", "answered", ["non-refundable"]),
]


def main():
    bot = Assistant(use_llm=False)   # offline, deterministic
    failures = 0
    for q, status, must in CASES:
        a = bot.ask(q)
        show(q, a)
        ok = a.status == status and all(m in a.text for m in must)
        if a.status in {"answered", "conflict", "unconfirmed", "restricted"} and not a.sources:
            ok = False
        print("\nCHECK:", "PASS" if ok else f"FAIL (expected status={status}, contains={must})")
        failures += not ok
    print("=" * 78)
    print(f"{len(CASES) - failures}/{len(CASES)} checks passed")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
