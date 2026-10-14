"""Run every query in queries.sql against leads.db and print the results.

Usage:  python run_queries.py [path/to/leads.db]
Run load_leads.py first. Only the standard library is needed.
"""
import sqlite3
import sys
from pathlib import Path

here = Path(__file__).parent
db_path = sys.argv[1] if len(sys.argv) > 1 else here / "leads.db"
con = sqlite3.connect(db_path)

# Split queries.sql into statements, skipping comment lines.
statements, buf = [], ""
for line in (here / "queries.sql").read_text(encoding="utf-8").splitlines(keepends=True):
    if line.strip().startswith("--") or not line.strip():
        continue
    buf += line
    if sqlite3.complete_statement(buf):
        statements.append(buf.strip())
        buf = ""

titles = ["Query 1: conversion by source",
          "Query 2a: duplicate rows, KEEP/DROP",
          "Query 2b: one canonical row per pair",
          "Query 3: conversion by city (normalised)"]

for title, sql in zip(titles, statements):
    cur = con.execute(sql)
    rows = cur.fetchall()
    cols = [d[0] for d in cur.description]
    print(f"\n=== {title}  ({len(rows)} rows) ===")
    print(" | ".join(cols[:7]))
    for r in rows[:15]:                       # long outputs are truncated for readability
        print(" | ".join(str(v) for v in r[:7]))
    if len(rows) > 15:
        print(f"... {len(rows) - 15} more rows")
con.close()
