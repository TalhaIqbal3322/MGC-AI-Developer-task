"""The assistant: route -> (pricing engine | grounded lookup) -> enforce citations."""
from __future__ import annotations

import logging
import re

from . import extract, llm, pricing
from .ingest import Corpus, norm
from .models import Answer, Source
from .retrieve import Index, classify, find_conflict, select_units, tokens, _BLOCK

log = logging.getLogger("mgc")

CITED = {"answered", "conflict", "unconfirmed", "restricted"}   # statuses that MUST carry a source

REFUSAL = ("I don't have that in the documents, so I won't guess. "
           "Please ask the marketing manager or your sales manager.")

ANSWER_SYSTEM = """You answer questions for MGC Developments sales staff using ONLY the numbered sections provided.
Never use outside knowledge. Never calculate or estimate figures. If the sections do not contain the answer, return {"answer": null, "citations": []}.
Every claim needs a citation whose "quote" is copied VERBATIM (contiguous text, at least 4 words) from the section it names.
Reply with JSON only: {"answer": string or null, "citations": [{"section_id": "S1", "quote": "..."}]}"""


class Assistant:
    def __init__(self, use_llm=None):
        self.corpus = Corpus()
        self.index = Index(self.corpus.sections)
        pricing.verify_rules(self.corpus)
        self.units = pricing.load_base_prices(self.corpus)
        self.use_llm = llm.available() if use_llm is None else use_llm

    # ------------------------------------------------------------------ public
    def ask(self, question: str) -> Answer:
        q = (question or "").strip()
        if not q:
            return Answer("needs_clarification",
                          "Ask about MGC Aurora Heights prices, payment plans or booking policy.")
        try:
            req = self._extract(q)
            ans = pricing.quote(req, self.corpus, self.units) if req else self._lookup(q)
        except Exception:                                   # never turn a bug into a confident answer
            log.exception("assistant error")
            ans = Answer("not_in_documents",
                         "I couldn't process that reliably. Please check with your manager.")
        return self._enforce(ans)

    # ------------------------------------------------------------------ routing
    def _extract(self, q: str):
        if self.use_llm:
            try:
                return extract.sanitize(llm.complete_json(extract.LLM_SYSTEM, q, 300))
            except Exception:
                log.exception("LLM extraction failed; using regex extractor")
        return extract.regex_extract(q)

    # ------------------------------------------------------------------ lookups
    def _lookup(self, q: str) -> Answer:
        Q = set(tokens(q))
        hits = self.index.search(q, k=5)
        if not Q or not hits:
            return Answer("not_in_documents", REFUSAL)

        conflict = find_conflict(Q, hits)
        if conflict:
            return self._conflict(conflict)

        asked = _BLOCK.search(q)
        asked = asked.group(1).upper() if asked else None
        best = None
        for sec, _ in hits:
            chosen, strength = select_units(Q, sec, asked)
            if chosen and (best is None or strength > best[2]):
                best = (sec, chosen, strength)
        if best is None:                       # nothing in any section actually supports the question
            return Answer("not_in_documents", REFUSAL)
        sec, chosen, _ = best

        status = classify(chosen)
        sources = [self.corpus.cite(sec, u) for u in chosen]
        bullets = "\n".join(f'- "{u}"' for u in chosen)
        if status == "restricted":
            return Answer(status, "I don't have a figure I'm allowed to give. What the documents say:\n" + bullets, sources)
        if status == "unconfirmed":
            return Answer(status, "This is not confirmed in the documents. What they say:\n" + bullets, sources)

        if self.use_llm:
            synth = self._llm_answer(q, [s for s, _ in hits[:3]])
            if synth:
                return synth
        return Answer("answered", bullets, sources)

    def _conflict(self, items) -> Answer:
        lines = ["The documents disagree on this, so I can't give you one answer:"]
        sources = []
        for sec, u in items:
            lines.append(f'- {sec.doc_title} > {sec.heading} ({sec.date_label}): "{u}"')
            sources.append(self.corpus.cite(sec, u))
        newest = max(items, key=lambda x: x[0].date_key)[0]
        lines.append("Don't quote either figure to a customer until management confirms which applies. "
                     f"(For context only: {newest.doc_title} is the more recently dated document, "
                     "which does not prove its figure is the current one.)")
        return Answer("conflict", "\n".join(lines), sources)

    def _llm_answer(self, q: str, sections: list):
        """LLM-written answer, accepted ONLY if every quote is verbatim from the section it cites."""
        try:
            blocks = [f"[S{i}] {s.doc_title} > {s.heading}\n{s.text}" for i, s in enumerate(sections, 1)]
            data = llm.complete_json(ANSWER_SYSTEM, "SECTIONS:\n" + "\n\n".join(blocks) + f"\n\nQUESTION: {q}")
            if not data.get("answer") or not data.get("citations"):
                return None
            sources = []
            for c in data["citations"]:
                sec = sections[int(str(c["section_id"]).lstrip("S")) - 1]
                if len(c["quote"].split()) < 4 or norm(c["quote"]) not in norm(sec.text):
                    return None                # fabricated or paraphrased quote -> reject whole answer
                sources.append(Source(sec.doc_title, sec.heading, norm(c["quote"])))
            return Answer("answered", data["answer"], sources)
        except Exception:
            log.exception("LLM answer failed; using extractive answer")
            return None

    # ------------------------------------------------------------------ guard
    @staticmethod
    def _enforce(a: Answer) -> Answer:
        """An answer without a source counts as wrong -> convert it to a refusal."""
        if a.status in CITED and not a.sources:
            return Answer("not_in_documents", REFUSAL)
        return a
