"""Thin, optional wrapper around the Anthropic API. The project works without it."""
from __future__ import annotations

import json
import os
import re


def available() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def complete_json(system: str, user: str, max_tokens: int = 800) -> dict:
    import anthropic  # imported lazily so the offline path needs no dependency

    client = anthropic.Anthropic()
    msg = client.messages.create(
        model=os.environ.get("MGC_MODEL", "claude-sonnet-5-5"),
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    text = "".join(b.text for b in msg.content if b.type == "text")
    return json.loads(re.search(r"\{.*\}", text, re.S).group(0))
