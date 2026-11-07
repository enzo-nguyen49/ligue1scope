"""
Profilage de la zone raw, AVANT tout nettoyage.

Produit : dimensions, types inferes, taux de valeurs manquantes, doublons
(lignes completes et cle metier), plages min/max, modalites frequentes et
volumes par saison. Le rapport est ecrit par pipeline.py dans
reports/profiling_report.json.

Usage autonome :
    python src/profile_data.py
"""

from __future__ import annotations

import json

import pandas as pd

from common import load_raw_ligue1, select_raw_files
from settings import load_settings

NUMERIC_FIELDS = ["FTHG", "FTAG", "HTHG", "HTAG", "HS", "AS", "HST", "AST",
                  "odds_home_avg", "odds_draw_avg", "odds_away_avg"]
CATEGORICAL_FIELDS = ["FTR", "HomeTeam", "AwayTeam", "Div"]
LINEAGE_FIELDS = {"season", "source_file", "raw_sha256", "source_row"}


def build_profile(raw: pd.DataFrame) -> dict:
    data_cols = [c for c in raw.columns if c not in LINEAGE_FIELDS]
    numeric = {c: pd.to_numeric(raw[c], errors="coerce") for c in NUMERIC_FIELDS if c in raw}
    inferred_types = {}
    for col in data_cols:
        non_null = raw[col].dropna()
        if col in numeric and non_null.size and numeric[col].notna().sum() == non_null.size:
            inferred_types[col] = "number"
        elif col == "Date":
            inferred_types[col] = "date (JJ/MM/AA ou JJ/MM/AAAA)"
        elif col == "Time":
            inferred_types[col] = "time (HH:MM)"
        else:
            inferred_types[col] = "string"

    business_key = raw[["season", "Date", "HomeTeam", "AwayTeam"]]
    return {
        "shape": {"rows": int(len(raw)), "columns": len(data_cols)},
        "inferred_types": inferred_types,
        "missing_ratio": {
            c: round(float(raw[c].isna().mean()), 4)
            for c in sorted(data_cols, key=lambda c: -raw[c].isna().mean())
            if raw[c].isna().any()
        },
        "duplicate_rows_full": int(raw[data_cols].duplicated().sum()),
        "duplicate_rows_business_key": int(business_key.duplicated().sum()),
        "rows_per_season": {k: int(v) for k, v in raw.groupby("season").size().items()},
        "numeric_ranges": {
            c: {"min": float(s.min()), "max": float(s.max()), "non_numeric": int((raw[c].notna() & s.isna()).sum())}
            for c, s in numeric.items() if s.notna().any()
        },
        "top_values": {
            c: {str(k): int(v) for k, v in raw[c].value_counts().head(5).items()}
            for c in CATEGORICAL_FIELDS if c in raw
        },
        "distinct_teams": int(pd.concat([raw["HomeTeam"], raw["AwayTeam"]]).nunique()),
        "kickoff_time_available_from": (
            raw.loc[raw["Time"].notna(), "season"].min() if raw["Time"].notna().any() else None
        ),
    }


def main() -> None:
    settings = load_settings()
    warnings: list[str] = []
    raw = load_raw_ligue1(select_raw_files(settings, "manual-profile", warnings))
    print(json.dumps(build_profile(raw), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
