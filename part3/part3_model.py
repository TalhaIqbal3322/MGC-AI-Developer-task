"""Part 3: lead scoring baseline with an honest, time-based evaluation.

Usage:   python part3_model.py leads.csv
Outputs: model.joblib   (the fitted intake-time model, used by the web app in Part 4)
         metrics.json   (the numbers quoted in the README)
Needs:   pandas, numpy, scikit-learn, joblib   (pip install pandas numpy scikit-learn joblib)
"""
import json
import sys
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, roc_auc_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

here = Path(__file__).parent
csv_path = sys.argv[1] if len(sys.argv) > 1 else here / "leads.csv"

# ---------------------------------------------------------------- 1. load + clean
df = pd.read_csv(csv_path, parse_dates=["created_at"])

# (a) Duplicates: keep one row per crm_record_hash, using the same rule as Part 2
#     (earliest created_at, then lowest lead_id). Otherwise a lead and its '-B' copy could
#     land on both sides of the split and the test score would be inflated.
df = (df.sort_values(["crm_record_hash", "created_at", "lead_id"])
        .drop_duplicates("crm_record_hash", keep="first"))

# (b) City spellings: same mapping as Part 2 (ISB/Rwp/khi + capitalisation).
CITY_ABBR = {"isb": "Islamabad", "rwp": "Rawalpindi", "khi": "Karachi", "lhr": "Lahore"}


def clean_city(value):
    key = str(value).strip().lower()
    return CITY_ABBR.get(key, key.capitalize())


df["city"] = df["city"].map(clean_city)

# (c) bedrooms is empty for every plot and shop: "not applicable", not "unknown" -> 0.
df["bedrooms"] = df["bedrooms"].fillna(0)

# (d) area: missing is its own category.
df["area"] = df["area"].fillna("Unknown")

# (e) first_response_minutes has extreme outliers (max ~3,100 min); log1p tames them.
df["first_response_log"] = np.log1p(df["first_response_minutes"])
df["budget_log"] = np.log1p(df["budget_pkr_lac"])

# ---------------------------------------------------------------- 2. which columns
# NEVER used:
#   lead_id, crm_record_hash       identifiers, carry no information about the buyer
#   created_at                     only used to order the split
#   token_amount_received_pkr      LEAKAGE: > 0 for every converted lead (it IS the booking money)
#   converted                      the target
# INTAKE features: known when the lead arrives, i.e. when the team decides who to call first.
CAT = ["source", "city", "area", "property_type"]
NUM_INTAKE = ["budget_log", "bedrooms", "agent_experience_years"]
BIN_INTAKE = ["is_overseas", "referred_by_existing_client", "has_financing_approved"]
INTAKE = CAT + NUM_INTAKE + BIN_INTAKE
# ACTIVITY features: only exist AFTER the team has worked the lead, so they cannot be used to
# decide who to call first. Trained here only to show how much they would (misleadingly) add.
ACTIVITY = ["first_response_log", "calls_made", "total_call_seconds", "whatsapp_replies", "site_visits"]


def make_pipeline(features):
    cat = [c for c in features if c in CAT]
    num = [c for c in features if c not in CAT and c not in BIN_INTAKE]
    binary = [c for c in features if c in BIN_INTAKE]
    pre = ColumnTransformer([
        ("cat", OneHotEncoder(handle_unknown="ignore"), cat),
        # median-fill missing numbers; add_indicator keeps "was missing" as a feature
        ("num", Pipeline([("imp", SimpleImputer(strategy="median", add_indicator=True)),
                          ("sc", StandardScaler())]), num),
        ("bin", "passthrough", binary),
    ])
    return Pipeline([("pre", pre), ("clf", LogisticRegression(max_iter=1000))])


# ---------------------------------------------------------------- 3. split BY TIME
df = df.sort_values("created_at").reset_index(drop=True)
cut = int(len(df) * 0.8)
train, test = df.iloc[:cut], df.iloc[cut:]
print(f"rows after de-dup: {len(df)}")
print(f"train: {len(train)} leads, {train.created_at.min():%Y-%m-%d} to {train.created_at.max():%Y-%m-%d}, "
      f"conversion {train.converted.mean():.2%}")
print(f"test : {len(test)} leads, {test.created_at.min():%Y-%m-%d} to {test.created_at.max():%Y-%m-%d}, "
      f"conversion {test.converted.mean():.2%}")

# ---------------------------------------------------------------- 4. fit + evaluate
TOP_N = 100
results = {}
for name, feats in [("intake_only", INTAKE), ("intake_plus_activity", INTAKE + ACTIVITY)]:
    pipe = make_pipeline(feats).fit(train[feats], train["converted"])
    scores = pipe.predict_proba(test[feats])[:, 1]
    base_rate = test["converted"].mean()
    top = test.assign(score=scores).sort_values("score", ascending=False).head(TOP_N)
    results[name] = {
        "pr_auc": round(average_precision_score(test["converted"], scores), 4),
        "no_skill_pr_auc": round(base_rate, 4),             # a random scorer's PR-AUC = base rate
        "roc_auc": round(roc_auc_score(test["converted"], scores), 4),
        "top100_converted": int(top["converted"].sum()),
        "top100_random_expectation": round(TOP_N * base_rate, 1),
        "test_conversions_total": int(test["converted"].sum()),
    }
    if name == "intake_only":
        # Priority bands for the web app, defined on the held-out test scores:
        #   "call first" = the score of the 100th-best test lead, "medium" = top 20%.
        order = np.sort(scores)[::-1]
        hi, mid = float(order[TOP_N - 1]), float(np.quantile(scores, 0.80))
        band = np.where(scores >= hi, "call_first", np.where(scores >= mid, "medium", "low"))
        band_stats = {b: {"leads": int((band == b).sum()),
                          "conversion_rate": round(float(test["converted"].values[band == b].mean()), 4)}
                      for b in ("call_first", "medium", "low")}
        cat_cols = [c for c in INTAKE if c in CAT]
        encoder = pipe.named_steps["pre"].named_transformers_["cat"]
        joblib.dump({
            "pipeline": pipe,
            "features": INTAKE,
            "city_abbr": CITY_ABBR,
            "categories": {c: list(v) for c, v in zip(cat_cols, encoder.categories_)},  # dropdown options
            "band_thresholds": {"call_first": hi, "medium": mid},
            "band_stats": band_stats,
        }, here / "model.joblib")
        results[name]["band_stats"] = band_stats

print(json.dumps(results, indent=2))
(here / "metrics.json").write_text(json.dumps(results, indent=2))
