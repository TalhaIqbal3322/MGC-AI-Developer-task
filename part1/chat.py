"""Interactive command-line chat:  python chat.py"""
import sys

from run_questions import show
from mgc import Assistant

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

bot = Assistant()
print("MGC document assistant (blank line to quit). LLM mode:", bot.use_llm)
while True:
    q = input("\n> ").strip()
    if not q:
        break
    show(q, bot.ask(q))
