"""Create leads.db from schema.sql and load leads.csv into it.

Usage:  python load_leads.py [path/to/leads.csv] [path/to/leads.db]
Only the standard library is needed.
"""
import csv
import sqlite3
import sys
from pathlib import Path

here = Path(__file__).parent
csv_path = sys.argv[1] if len(sys.argv) > 1 else here / "leads.csv"
db_path = sys.argv[2] if len(sys.argv) > 2 else here / "leads.db"

INT_COLS = {"bedrooms", "calls_made", "whatsapp_replies", "site_visits",
            "is_overseas", "referred_by_existing_client", "has_financing_approved",
            "crm_record_hash", "converted"}
REAL_COLS = {"budget_pkr_lac", "first_response_minutes", "total_call_seconds",
             "agent_experience_years", "token_amount_received_pkr"}


def convert(col, value):
    value = value.strip()
    if value == "":
        return None                      # empty CSV cell -> SQL NULL
    if col in INT_COLS:
        return int(float(value))         # CSV stores bedrooms as '2.0'
    if col in REAL_COLS:
        return float(value)
    return value


con = sqlite3.connect(db_path)
con.executescript((here / "schema.sql").read_text())

with open(csv_path, newline="", encoding="utf-8") as f:
    reader = csv.DictReader(f)
    cols = reader.fieldnames
    rows = [[convert(c, r[c]) for c in cols] for r in reader]

con.executemany(
    f"INSERT INTO leads ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})", rows
)
con.commit()
n = con.execute("SELECT COUNT(*) FROM leads").fetchone()[0]
print(f"Loaded {n} rows into {db_path}")
con.close()
