"""Retrieval, support checks and conflict detection - all deterministic, no LLM.

Why plain BM25 and no vector DB: the corpus is 3 short documents (~30 sections).
Keyword search over section text + heading is exact, explainable and has no
dependencies. An embedding index would add cost and failure modes for no gain here.
"""
from __future__ import annotations

import math
import re
from collections import Counter

from .ingest import Section, clean

STOP = set("""
a an the of on in at to for is are was were be been what whats who whom which when where
how do does did can could should would will i we you it its this that these those there
and or any with by from about me my our us as if so tell please give know much many s
mgc aurora heights developments
""".split())

ALIAS = {
    "tenancy": "tenant", "rent": "rental", "yields": "yield",
    "instalments": "instalment", "installment": "instalment", "installments": "instalment",
    "opening": "open", "hours": "open", "hour": "open", "timings": "open", "timing": "open",
}


def _stem(w: str) -> str:
    w = ALIAS.get(w, w)
    if len(w) > 4 and w.endswith("ies"):
        return w[:-3] + "y"
    if len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
        return w[:-1]
    return w


def tokens(text: str) -> list:
    words = re.findall(r"[a-z0-9]+", clean(text).lower())
    return [_stem(w) for w in words if w not in STOP and len(w) > 1]


# ---------------------------------------------------------------- sentence units
_SENT = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9])")


def units(text: str) -> list:
    """Split a section into quotable units: sentences, bullets, numbered steps, table rows."""
    out, para = [], []

    def flush():
        if para:
            joined = " ".join(para)
            out.extend(s.strip() for s in _SENT.split(joined) if s.strip())
            para.clear()

    for raw in clean(text).splitlines():
        line = raw.strip()
        if not line:
            flush()
        elif re.fullmatch(r"\|[\s:|-]+\|", line):          # table separator row
            continue
        elif line.startswith("|"):
            flush()
            out.append(" | ".join(c.strip() for c in line.strip("|").split("|")))
        elif re.match(r"^(-|\d+\.)\s+", line):
            flush()
            out.append(re.sub(r"^(-|\d+\.)\s+", "", line))
        else:
            para.append(line)
    flush()
    return out


# ---------------------------------------------------------------- BM25 index
class Index:
    def __init__(self, sections: list, k1: float = 1.5, b: float = 0.75):
        self.sections, self.k1, self.b = sections, k1, b
        # heading counted 3x: a matching heading is strong evidence
        self.docs = [tokens(s.heading) * 3 + tokens(s.text) for s in sections]
        self.avgdl = sum(len(d) for d in self.docs) / len(self.docs)
        self.df = Counter(t for d in self.docs for t in set(d))

    def search(self, query: str, k: int = 5) -> list:
        q, n, scored = set(tokens(query)), len(self.docs), []
        for sec, doc in zip(self.sections, self.docs):
            tf, score = Counter(doc), 0.0
            for t in q:
                if t in tf:
                    idf = math.log(1 + (n - self.df[t] + 0.5) / (self.df[t] + 0.5))
                    score += idf * tf[t] * (self.k1 + 1) / (
                        tf[t] + self.k1 * (1 - self.b + self.b * len(doc) / self.avgdl))
            if score > 0:
                scored.append((sec, score))
        return sorted(scored, key=lambda x: -x[1])[:k]


# ---------------------------------------------------------------- support check
def need(n: int, strict: bool = False) -> int:
    """How many query terms a unit must contain to count as supporting evidence."""
    if n <= 1:
        return 1
    if strict:
        return n if n <= 3 else math.ceil(0.75 * n)
    return max(2, math.ceil(0.5 * n))


_BLOCK = re.compile(r"\bblock\s*([ab])\b", re.I)


def select_units(Q: set, sec: Section, block=None):
    """Return (supporting units, strength) for one section. Empty list = no support.

    A unit's evidence = its own words + its section heading. If the question names a
    block, units that talk only about the OTHER block are dropped (a Block A answer
    must never be returned for a Block B question).
    """
    req = need(len(Q))
    ht = set(tokens(sec.heading))
    heading_hits = len(Q & ht)
    us = units(sec.text)
    if block:
        us = [u for u in us
              if not _BLOCK.findall(u) or block in {b.upper() for b in _BLOCK.findall(u)}]
    if heading_hits >= req:                      # e.g. an FAQ entry whose question matches
        return us[:10], 100 + heading_hits
    scored = [(len(Q & (set(tokens(u)) | ht)), u) for u in us]
    good = [u for sc, u in scored if sc >= req]
    return good[:3], max((sc for sc, _ in scored), default=0)


# ---------------------------------------------------------------- classification
RESTRICTED = re.compile(r"\b(must not|does not publish|do not publish)\b", re.I)
UNCONFIRMED = re.compile(
    r"\b(no [\w' -]{0,40}confirmed|not (?:yet )?(?:been )?confirmed|no confirmed|unconfirmed)\b",
    re.I)


def classify(us: list) -> str:
    joined = " ".join(us)
    if RESTRICTED.search(joined):
        return "restricted"
    if UNCONFIRMED.search(joined):
        return "unconfirmed"
    return "answered"


# ---------------------------------------------------------------- conflict detection
_PCT = re.compile(r"\d+(?:\.\d+)?\s?%")
_PKR = re.compile(r"\d{1,3}(?:,\d{3})+|\d{4,}")


def _vals(unit: str) -> dict:
    return {
        "pct": {m.replace(" ", "") for m in _PCT.findall(unit)},
        "pkr": {m.replace(",", "") for m in _PKR.findall(unit)},
    }


def find_conflict(Q: set, hits: list):
    """If two DIFFERENT documents state different percentages (or amounts) for the
    same thing, return the conflicting (section, unit) pairs; else None.

    Heuristic, deliberately strict: a unit only counts if it contains (nearly) ALL
    the query terms and a number, and values are only compared like-for-like.
    """
    if not Q:
        return None
    n = need(len(Q), strict=True)
    claims = {}
    for sec, _ in hits:
        for u in units(sec.text):
            if len(Q & set(tokens(u))) >= n and (_PCT.search(u) or _PKR.search(u)):
                claims.setdefault(sec.doc_id, []).append((sec, u))
    if len(claims) < 2:
        return None
    for kind in ("pct", "pkr"):
        per_doc = {d: set().union(*[_vals(u)[kind] for _, u in items])
                   for d, items in claims.items()}
        per_doc = {d: v for d, v in per_doc.items() if v}
        if len(per_doc) >= 2 and len({frozenset(v) for v in per_doc.values()}) > 1:
            seen, out = set(), []
            for items in claims.values():
                for sec, u in items:
                    if (sec.heading, u) not in seen:
                        seen.add((sec.heading, u))
                        out.append((sec, u))
            return out
    return None
