"""Plain data types shared by every module."""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Optional


@dataclass
class Source:
    doc: str      # e.g. "Price List & Payment Plan"
    section: str  # e.g. "Discounts"
    quote: str    # verbatim text from that section (verified at runtime)


@dataclass
class Answer:
    # answered | conflict | unconfirmed | restricted | not_in_documents
    # | needs_clarification | cannot_price
    status: str
    text: str
    sources: list = field(default_factory=list)
    notes: list = field(default_factory=list)   # assumptions / flags for the salesperson
    calc: Optional[dict] = None                 # raw numbers from the pricing engine

    def to_dict(self) -> dict:
        return asdict(self)
