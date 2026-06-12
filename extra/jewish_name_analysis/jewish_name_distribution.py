#!/usr/bin/env python3
"""
Estimate Jewish vs non-Jewish distribution of unique attendees since Sept 1, 2025
based on first+last name heuristics.

Outputs:
- Per-person probability (Jewish 0..1)
- Aggregate: expected Jewish count (sum of probabilities) and category bins
"""

import os
import sys
import re
import csv
import psycopg2
import pandas as pd
from typing import Optional, Tuple
from dotenv import load_dotenv

# Load .env from repo root (this file lives in extra/jewish_name_analysis/)
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.abspath(os.path.join(_SCRIPT_DIR, "..", ".."))
load_dotenv(os.path.join(_REPO_ROOT, ".env"))

# ----------------------------------------------------------------------------
# Heuristic name scoring -- priors loaded from CSV files (authoritative source)
# ----------------------------------------------------------------------------
# Editing a CSV in this directory and re-running the script is sufficient to
# change the scoring. The four name CSVs share the schema:
#     <last_name|first_name>,p_jewish
# The suffix-hints CSV is:
#     suffix,boost
# Distinctively non-Jewish names live in their own CSVs only for editorial
# clarity; they are merged into the same lookup dicts at load time.

def _load_name_priors(filename: str, key_col: str) -> dict:
    """Load a CSV of `<name>,p_jewish` rows into a {name: float} dict."""
    path = os.path.join(_SCRIPT_DIR, filename)
    out = {}
    with open(path, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            name = (row.get(key_col) or "").strip().lower()
            if not name:
                continue
            try:
                p = float(row["p_jewish"])
            except (KeyError, ValueError, TypeError):
                continue
            out[name] = p
    return out


def _load_suffix_hints(filename: str) -> list:
    """Load suffix,boost pairs as a list of (str, float) tuples."""
    path = os.path.join(_SCRIPT_DIR, filename)
    out = []
    with open(path, newline="") as f:
        reader = csv.reader(f)
        next(reader, None)  # skip header
        for row in reader:
            if not row or len(row) < 2:
                continue
            suffix = row[0].strip().lower()
            try:
                boost = float(row[1])
            except ValueError:
                continue
            if suffix:
                out.append((suffix, boost))
    return out


# Lookup dicts are merged from the "Jewish-leaning" and "non-Jewish-leaning"
# CSVs into a single {name: p_jewish} dict per name type.
LASTNAME_HIGH = {
    **_load_name_priors("lastname_priors.csv", "last_name"),
    **_load_name_priors("lastname_nonjewish_priors.csv", "last_name"),
}
FIRSTNAME_HIGH = {
    **_load_name_priors("firstname_priors.csv", "first_name"),
    **_load_name_priors("firstname_nonjewish_priors.csv", "first_name"),
}

# Kept for API back-compat with previous version of this module: sets of
# distinctively-non-Jewish names. score_lastname / score_firstname check
# LASTNAME_HIGH / FIRSTNAME_HIGH first so these are now redundant, but exposing
# them lets other code import them.
LASTNAME_NONJEWISH = set(_load_name_priors("lastname_nonjewish_priors.csv", "last_name"))
FIRSTNAME_NONJEWISH = set(_load_name_priors("firstname_nonjewish_priors.csv", "first_name"))

SUFFIX_HINTS = _load_suffix_hints("lastname_suffix_hints.csv")




def normalize(name: str) -> str:
    if not name:
        return ""
    name = name.strip().lower()
    # remove accents (basic)
    import unicodedata
    name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    name = re.sub(r"[^a-z'\- ]", "", name)
    return name


def score_lastname(last: str) -> Optional[float]:
    """Return Jewish probability prior in [0,1] for the last name, or None if unknown."""
    last_n = normalize(last)
    if not last_n:
        return None
    # exact whole-name lookup
    if last_n in LASTNAME_HIGH:
        return LASTNAME_HIGH[last_n]
    if last_n in LASTNAME_NONJEWISH:
        return 0.03
    # multi-part (hyphenated or two-word): take max of parts
    parts = re.split(r"[-\s]+", last_n)
    if len(parts) > 1:
        sub = [score_lastname(p) for p in parts if p]
        sub = [s for s in sub if s is not None]
        if sub:
            # take max signal
            return max(sub)
    # suffix-based heuristic
    suffix_boost = 0.0
    for suf, b in SUFFIX_HINTS:
        if last_n.endswith(suf):
            suffix_boost = max(suffix_boost, b)
    if suffix_boost > 0:
        # base prior ~0.20 + suffix boost, capped
        return min(0.20 + suffix_boost, 0.85)
    return None  # unknown


def score_firstname(first: str) -> Optional[float]:
    first_n = normalize(first)
    if not first_n:
        return None
    # take token before space (handles "Sarah Jane")
    first_n = first_n.split()[0] if first_n else ""
    if not first_n:
        return None
    if first_n in FIRSTNAME_HIGH:
        return FIRSTNAME_HIGH[first_n]
    if first_n in FIRSTNAME_NONJEWISH:
        return 0.03
    return None  # unknown


def combined_probability(first: str, last: str) -> Tuple[float, str]:
    """
    Combine first+last name signals into a single probability.
    Returns (probability, reasoning_label).

    Strategy:
    - If we have both signals, combine via odds ratio (Bayesian-ish).
    - If only one signal, use it but pull toward base rate.
    - If neither signal, return base rate (0.20).
    """
    base = 0.20  # base prior for unknown (college org context, agnostic)

    p_l = score_lastname(last)
    p_f = score_firstname(first)

    if p_l is None and p_f is None:
        return base, "unknown:both"

    def to_logit(p):
        p = min(max(p, 0.001), 0.999)
        import math
        return math.log(p / (1 - p))

    def from_logit(x):
        import math
        return 1 / (1 + math.exp(-x))

    base_logit = to_logit(base)

    if p_l is not None and p_f is not None:
        # both: sum log-odds adjustments from base
        l_logit = to_logit(p_l) - base_logit
        f_logit = to_logit(p_f) - base_logit
        combined = from_logit(base_logit + l_logit + f_logit)
        return combined, "both"
    if p_l is not None:
        # last name only — last name is more diagnostic; trust it but soften
        # slight pull toward base
        l_logit = to_logit(p_l) - base_logit
        combined = from_logit(base_logit + 0.85 * l_logit)
        return combined, "lastname_only"
    # first name only
    f_logit = to_logit(p_f) - base_logit
    combined = from_logit(base_logit + 0.7 * f_logit)
    return combined, "firstname_only"


# ----------------------------------------------------------------------------
# DB query
# ----------------------------------------------------------------------------

def get_db_connection():
    return psycopg2.connect(
        host=os.getenv('PGHOST'),
        port=os.getenv('PGPORT'),
        database=os.getenv('PGDATABASE'),
        user=os.getenv('PGUSER'),
        password=os.getenv('PGPASSWORD')
    )


def get_unique_attendees_since(date_str: str) -> pd.DataFrame:
    conn = get_db_connection()
    try:
        query = """
            SELECT DISTINCT p.id, p.first_name, p.last_name
            FROM people p
            INNER JOIN attendance a ON p.id = a.person_id
            INNER JOIN events e ON a.event_id = e.id
            WHERE a.checked_in = true
              AND e.start_datetime >= %s
            ORDER BY p.last_name, p.first_name
        """
        df = pd.read_sql(query, conn, params=(date_str,))
        return df
    finally:
        conn.close()


def main():
    print("=" * 70)
    print("JEWISH vs NON-JEWISH ATTENDEE DISTRIBUTION (name-based estimate)")
    print("Filter: unique checked-in attendees at events since 2025-09-01")
    print("=" * 70)
    print()

    df = get_unique_attendees_since("2025-09-01 00:00:00")
    if df.empty:
        print("No attendees found.")
        return

    rows = []
    for _, r in df.iterrows():
        p, reason = combined_probability(r["first_name"] or "", r["last_name"] or "")
        rows.append({
            "first_name": r["first_name"],
            "last_name": r["last_name"],
            "p_jewish": round(p, 3),
            "signal": reason,
        })

    out = pd.DataFrame(rows)

    # Categorize
    def bucket(p):
        if p >= 0.80:
            return "very likely Jewish (>=0.80)"
        if p >= 0.60:
            return "likely Jewish (0.60-0.80)"
        if p >= 0.40:
            return "uncertain (0.40-0.60)"
        if p >= 0.20:
            return "likely non-Jewish (0.20-0.40)"
        return "very likely non-Jewish (<0.20)"

    out["bucket"] = out["p_jewish"].apply(bucket)

    total = len(out)
    expected_jewish = out["p_jewish"].sum()
    expected_nonjewish = total - expected_jewish

    print(f"Total unique attendees since 2025-09-01: {total}")
    print()
    print(f"Expected Jewish (sum of probabilities):    {expected_jewish:.1f}  "
          f"({100*expected_jewish/total:.1f}%)")
    print(f"Expected non-Jewish (sum of 1 - p):        {expected_nonjewish:.1f}  "
          f"({100*expected_nonjewish/total:.1f}%)")
    print()

    print("Distribution by confidence bucket:")
    print("-" * 70)
    bucket_order = [
        "very likely Jewish (>=0.80)",
        "likely Jewish (0.60-0.80)",
        "uncertain (0.40-0.60)",
        "likely non-Jewish (0.20-0.40)",
        "very likely non-Jewish (<0.20)",
    ]
    counts = out["bucket"].value_counts().to_dict()
    for b in bucket_order:
        n = counts.get(b, 0)
        pct = 100 * n / total if total else 0
        print(f"  {b:<42} {n:>5}  ({pct:5.1f}%)")
    print()

    # Signal breakdown
    print("Coverage / signal source:")
    print("-" * 70)
    sig_counts = out["signal"].value_counts().to_dict()
    for s in ["both", "lastname_only", "firstname_only", "unknown:both"]:
        n = sig_counts.get(s, 0)
        pct = 100 * n / total if total else 0
        print(f"  {s:<20} {n:>5}  ({pct:5.1f}%)")
    print()

    # Write CSV
    out_path = os.path.join(_SCRIPT_DIR, "attendee_jewish_estimates.csv")
    out.sort_values("p_jewish", ascending=False).to_csv(out_path, index=False)
    print(f"Per-person estimates written to: {out_path}")

    # Top 30 most likely Jewish & most likely non-Jewish (for spot-check)
    print()
    print("Top 30 highest-probability Jewish (spot-check):")
    print("-" * 70)
    top = out.sort_values("p_jewish", ascending=False).head(30)
    for _, r in top.iterrows():
        print(f"  {r['p_jewish']:.2f}  {r['first_name']:<15} {r['last_name']:<25} "
              f"[{r['signal']}]")
    print()
    print("Bottom 30 lowest-probability Jewish (spot-check):")
    print("-" * 70)
    bot = out.sort_values("p_jewish", ascending=True).head(30)
    for _, r in bot.iterrows():
        print(f"  {r['p_jewish']:.2f}  {r['first_name']:<15} {r['last_name']:<25} "
              f"[{r['signal']}]")

    print()
    print("=" * 70)
    print("NOTE: Estimates are heuristic priors from a curated name list. They are")
    print("noisy for ambiguous names (Schwartz, Klein, Stein appear in both Jewish")
    print("and German-Christian populations). The 'expected' counts assume the")
    print("priors are well-calibrated; the bucket counts are usually more robust.")
    print("=" * 70)


if __name__ == "__main__":
    main()
