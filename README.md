# MGC Developments: AI Developer build task

A grounded document assistant (Part 1), a CRM schema with queries (Part 2), a lead-scoring model (Part 3)
and one web page that ties them together (Part 4). Python 3.10 or newer. Everything below is run **from the repo root**.

```
.
├── README.md
├── requirements.txt
├── leads.csv                 # the provided CRM dump
├── part1/                    # document assistant
│   ├── docs/                 #   the three MGC documents
│   ├── mgc/                  #   the assistant package
│   ├── run_questions.py      #   the 6 required questions + 11 edge cases
│   └── chat.py               #   interactive command-line chat
├── part2/                    # schema.sql, queries.sql, load_leads.py, run_queries.py
├── part3/                    # part3_model.py (writes model.joblib + metrics.json)
└── part4/                    # app.py: the web page
```

## How to run

```
pip install -r requirements.txt

python part1/run_questions.py             # Part 1: expect "17/17 checks passed"
python part2/load_leads.py leads.csv       # Part 2: builds part2/leads.db
python part2/run_queries.py                # Part 2: prints the three queries' results
python part3/part3_model.py leads.csv      # Part 3: trains, prints metrics, writes part3/model.joblib
python part4/app.py                        # Part 4: open http://127.0.0.1:5000
```
Run Part 3 **before** Part 4: the page loads `part3/model.joblib` (the page says so if it is missing).
On macOS/Linux use `python3` and `pip3` instead of `python` and `pip`; on Windows use `python` (or `py` if `python` is not found). Part 1 runs offline with no extra packages.

### Try the document assistant
Either use the web page (Part 4), or the command line: `python part1/chat.py`. Type a question in plain English after the `>` prompt and press Enter
(no quotes needed); a blank line quits. Every answer ends with a "Sources:" list (document and section). Questions covered by `run_questions.py`:

| Ask | What you should see |
|---|---|
| What's the base price of a 2-bed in Block B? | `answered`: PKR 22,425,000 (Standard), also mentions the Corner at 26,855,000 |
| What's the total for a Margalla-facing corner unit on floor 15, 2-bed Block B? | `answered`: PKR 30,346,150 with the premium breakdown (+13%) |
| An overseas Pakistani buyer pays 50% upfront on a 3-Bed Executive — what's the price after discounts, and what's due at booking? | `answered`: PKR 36,321,600 after discounts, 3,632,160 token, with flags |
| What's the transfer fee? | `conflict`: shows 2% and 2.5% with both sources, picks neither |
| What's the rental yield on a 1-bed? | `restricted`: cites the FAQ, directs to the marketing manager |
| Who is the anchor tenant? | `unconfirmed`: cites the brochure |
| Price of a 1-bed? | `needs_clarification`: asks which block (A or B), shows both prices |
| What is the possession date for Block B? | `not_in_documents`: refuses (only Block A's date is stated) |
| What is the weather in Islamabad? | `not_in_documents`: refuses |

## Part 1: Document assistant

Answers come only from the three documents, and every answer names its document and section.
- **Arithmetic is done in code**, never by an LLM (`part1/mgc/pricing.py`, `Decimal`). Base prices are parsed from the price-list
  tables and each row is checked (area x rate = price). The pricing rules are verified against the document text at startup, so a changed
  price list fails loudly instead of giving silently wrong numbers.
- **Citations are enforced in code.** Every quote must appear verbatim in the section it cites; an answer with no source is turned into a refusal.
- **Conflicts are shown, not resolved.** "Transfer fee" returns both values (2% in the price list, 2.5% in the booking policy) with their
  sections and dates, says they disagree and tells the salesperson to confirm with management. Detection is a general heuristic
  (two documents give different percentages for the same query terms), not a hardcoded special case.
- **Refusals are structural.** Rental yield returns the FAQ's "must not give projections, ask the marketing manager"; the anchor tenant
  returns the brochure's "not confirmed"; anything unsupported returns "not in the documents".
- Retrieval is plain BM25 over about 30 short sections (no vector database): exact, explainable, enough for this corpus size.
- Judgment calls are flagged in the answer text: discounts are **added** (6% + 2%) with the compounded alternative shown (PKR 47,376 difference);
  the documents define a 10% token but no schedule for the "50% upfront" option, so the answer says to confirm with management.

## Part 2: Database (SQLite)

- `part2/schema.sql`: one table, `leads` (the CSV is one row per lead with no repeating group). Primary key `lead_id`, typed columns,
  `CHECK` constraints on flags and non-negative numbers. The raw dump is stored as it arrived; duplicates are resolved in the view
  `leads_canonical`.
- **Duplicates:** 9,160 rows, 9,000 distinct `crm_record_hash`, so 160 pairs, identical except for `lead_id` (the copy ends in `-B`).
  Winner rule: earliest `created_at`, then lowest `lead_id` (deterministic because `lead_id` is the primary key).
- `part2/queries.sql`: (1) conversion by source, 200+ leads, best first; (2) duplicate pairs with one canonical row per pair, plus a note on
  preventing them with a `UNIQUE` index on `crm_record_hash` (tested: a duplicate insert is refused); (3) conversion by city after normalising
  `ISB`, `Rwp`, `khi` and capitalisation inside the query (21 spellings become 9 cities).
- Queries 1 and 3 read the de-duplicated view so a lead entered twice is not counted twice.

## Part 3: Lead scoring

### Data decisions
- **Dropped `token_amount_received_pkr` (leakage).** It is above zero for all 626 converted leads: it is the booking money, recorded as the
  outcome happens. Training on it gives a fake PR-AUC of 0.92 and 93 of the top 100.
- **Dropped `lead_id` and `crm_record_hash`** (identifiers) and `created_at` (used only to order the split).
- **De-duplicated** to 9,000 rows (same rule as Part 2) *before* splitting, so a lead and its `-B` copy can never be on both sides of the split.
- **`bedrooms` empty means not applicable** (empty for every plot and shop), so it is filled with 0, not imputed.
- **Other missing values:** `area` becomes an "Unknown" category; `budget` and `agent_experience_years` use the median plus a "was missing" flag.
- **Fixed** city spellings (as in Part 2) and log-transformed `budget` and `first_response_minutes` (max about 3,100 minutes).
- **Two feature sets, on purpose.** *Intake-only* (source, city, area, property type, budget, bedrooms, overseas, referred, financing approved,
  agent experience) is known when a lead arrives, which is when the team decides whom to call first. **This is the model shipped to the web page.**
  *Intake + activity* adds calls, call seconds, WhatsApp replies, site visits and first-response time. Those only exist after the team has worked
  the lead, so they cannot be used to choose whom to call; reported for comparison only.

### Split
Sorted by `created_at`: oldest 80% train (7,200 leads, 2024-01-01 to 2025-05-27, 6.7% convert), newest 20% test (1,800 leads, 2025-05-28 to 2025-10-01,
7.9% convert). This matches real use: the model is trained on the past and scores leads it has not seen. A random split would let it learn from leads
that arrived alongside the ones it is tested on (same campaigns, same period). *Honest note: on this dataset a random split gave PR-AUC 0.196,
no higher than the time split (0.217), so I see no drift here; I split by time because it is the correct simulation of deployment, not because it changed the number.*

### Metric (test set: 1,800 leads, 143 conversions)
Untuned logistic regression. Metric: **PR-AUC**, because only about 8% of leads convert and PR-AUC rewards putting the real converters at the top.

| | PR-AUC | Top 100 leads: converted |
|---|---|---|
| No-skill (random order) | 0.079 | about 8 |
| **Intake-only model (shipped)** | **0.217** (95% CI 0.16 to 0.28) | **33** (95% CI 23 to 42) |
| Intake + activity (not usable for prioritising) | 0.450 | 47 |

**In sales terms:** if the team can only call the top 100 scored leads, about 33 convert versus about 8 for 100 random calls, roughly 4x better.
The web page uses three priority bands defined on the test scores: top 100 ("call first", 33% converted), next 260 ("medium", 12.7%), rest ("low", 5.4%).

## Part 4: Web page

`python part4/app.py`, then open http://127.0.0.1:5000. One page with two forms: ask a question (answer, status, flags and sources shown) and enter a
lead's details (probability and priority band shown, with the conversion rate that band achieved on the held-out test set). Plain HTML served by Flask; no styling.
The assistant runs offline by default; set `MGC_USE_LLM=1` to enable Part 1's optional LLM path.

## Known limits / not finished
- The LLM path in Part 1 (`ANTHROPIC_API_KEY`) is written but untested; the offline path passes all 17 checks.
- Part 3 is an untuned logistic regression. With only 143 test conversions the numbers carry real uncertainty (intervals above).
  Next steps: gradient boosting, calibration, and refitting on all data before deployment.
- The score bands are defined on the same test set used to report them, so their rates are descriptive, not a fresh out-of-sample estimate.
- `agent_experience_years` is assumed known when the lead is scored; drop it if agents are assigned after scoring.
- Part 4 reloads the full page on each submit (no JavaScript), and has no auth or tests, as the brief allows.
