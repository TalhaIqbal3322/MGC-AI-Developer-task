"""Load the three markdown documents and split them into citable sections.

A section = one `## Heading` block. FAQ questions (a bold line ending in `?`)
become their own sections named "<parent heading> > <question>", so a citation can
point at the exact FAQ entry.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .models import Source

DOCS_DIR = Path(__file__).resolve().parent.parent / "docs"

DOC_FILES = [
    ("brochure", "brochure.md", "Project Brochure"),
    ("price_list", "price_list.md", "Price List & Payment Plan"),
    ("booking_policy", "booking_policy.md", "Booking Policy & Sales FAQ"),
]

_MONTHS = ("January February March April May June July August September "
           "October November December").split()
_DATE_RE = re.compile(r"(%s)\s+(\d{4})" % "|".join(_MONTHS))
_FAQ_RE = re.compile(r"^\*\*(.+\?)\*\*\s*$")


def clean(text: str) -> str:
    """Strip markdown emphasis so matching and quoting ignore it."""
    return text.replace("**", "").replace("`", "")


def norm(text: str) -> str:
    """Whitespace-collapsed, markdown-free text. Used to verify quotes."""
    return re.sub(r"\s+", " ", clean(text)).strip()


@dataclass(frozen=True)
class Section:
    doc_id: str
    doc_title: str
    heading: str
    text: str
    date_label: str   # e.g. "May 2025" - parsed from the document header
    date_key: tuple   # (year, month_index) for comparing recency


def _parse(doc_id: str, filename: str, title: str) -> list:
    raw = (DOCS_DIR / filename).read_text(encoding="utf-8")
    m = _DATE_RE.search(raw[:600])
    date_label = m.group(0) if m else ""
    date_key = (int(m.group(2)), _MONTHS.index(m.group(1))) if m else (0, 0)

    sections, parent, heading, buf = [], None, None, []

    def flush():
        text = "\n".join(buf).strip()
        if heading is not None and text:
            sections.append(Section(doc_id, title, heading, text, date_label, date_key))

    for line in raw.splitlines():
        if line.startswith("## "):
            flush()
            parent = heading = line[3:].strip()
            buf = []
        elif heading is not None and _FAQ_RE.match(line):
            flush()
            heading = f"{parent} > {_FAQ_RE.match(line).group(1)}"
            buf = []
        elif heading is not None:
            buf.append(line)
    flush()
    return sections


class Corpus:
    def __init__(self):
        self.sections = [s for args in DOC_FILES for s in _parse(*args)]

    def find(self, doc_id: str, heading_prefix: str) -> Section:
        for s in self.sections:
            if s.doc_id == doc_id and s.heading.startswith(heading_prefix):
                return s
        raise KeyError(f"No section {doc_id!r} / {heading_prefix!r}")

    def cite(self, section: Section, quote: str) -> Source:
        """Build a Source, refusing any quote that is not literally in the section."""
        if norm(quote) not in norm(section.text):
            raise ValueError(f"Quote not found in {section.heading!r}: {quote!r}")
        return Source(section.doc_title, section.heading, norm(quote))

    def source(self, doc_id: str, heading_prefix: str, quote: str) -> Source:
        return self.cite(self.find(doc_id, heading_prefix), quote)
