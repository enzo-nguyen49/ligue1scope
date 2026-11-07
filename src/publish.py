"""
Publication du produit de donnees.

1. Fichiers : data/curated/matches_curated.csv + .parquet et le mart
   data/curated/mart/*.csv. Ecriture atomique (fichier temporaire puis
   os.replace) : un lecteur ne voit jamais un fichier a moitie ecrit, et un
   echec laisse la version precedente intacte.
2. PostgreSQL (si DATABASE_URL est defini) : UPSERT transactionnel
   INSERT ... ON CONFLICT (match_id) DO UPDATE ... WHERE row_hash differe.
   - rejouer le meme run => 0 insertion, 0 mise a jour (idempotence prouvee) ;
   - une correction de la source => mise a jour de la seule ligne concernee ;
   - un echec => ROLLBACK complet, la base reste dans son etat precedent.

Usage autonome (rechargement du curated existant) :
    python src/publish.py --dsn postgresql://...
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
from pathlib import Path

import pandas as pd

from settings import PROJECT_ROOT, Settings, load_settings

log = logging.getLogger("ligue1scope")

SCHEMA_SQL = PROJECT_ROOT / "sql" / "schema.sql"
CURATED_NAME = "matches_curated"

MATCH_COLUMNS = [
    "match_id", "season_id", "match_date", "kickoff_time", "home_team_id", "away_team_id",
    "home_goals", "away_goals", "result", "home_elo", "away_elo", "elo_diff",
    "elo_home_win_expectancy", "elo_favorite", "elo_warmup",
    "odds_home_avg", "odds_draw_avg", "odds_away_avg",
    "market_prob_home", "market_prob_draw", "market_prob_away", "market_favorite", "odds_valid",
    "kickoff_time_missing", "data_quality_status", "source_file", "raw_sha256", "source_row",
    "row_hash", "last_run_id",
]
WEATHER_COLUMNS = ["weather_temp_c", "weather_precip_mm", "weather_wind_kph", "weather_missing",
                   "weather_granularity"]


def _atomic_write(path: Path, writer) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    writer(tmp)
    os.replace(tmp, path)


def write_files(curated: pd.DataFrame, mart: dict[str, pd.DataFrame], settings: Settings) -> dict[str, str]:
    out_dir = settings.curated_dir
    csv_path = out_dir / f"{CURATED_NAME}.csv"
    parquet_path = out_dir / f"{CURATED_NAME}.parquet"
    _atomic_write(csv_path, lambda p: curated.to_csv(p, index=False))
    _atomic_write(parquet_path, lambda p: curated.to_parquet(p, index=False))
    outputs = {
        "curated_csv": str(csv_path.relative_to(settings.root)),
        "curated_parquet": str(parquet_path.relative_to(settings.root)),
        "curated_csv_bytes": csv_path.stat().st_size,
        "curated_parquet_bytes": parquet_path.stat().st_size,
    }
    for name, table in mart.items():
        path = out_dir / "mart" / f"{name}.csv"
        _atomic_write(path, lambda p, t=table: t.to_csv(p, index=False))
        outputs[name] = str(path.relative_to(settings.root))
    return outputs


def _none(value):
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    return value.item() if hasattr(value, "item") else value


def row_hash(row: dict) -> str:
    """Empreinte du contenu metier : sert a ne mettre a jour que ce qui change."""
    payload = json.dumps({k: _none(v) for k, v in row.items()}, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def publish_postgres(curated: pd.DataFrame, dsn: str, run_id: str, schema_name: str = "public",
                     run_record: dict | None = None) -> dict[str, int]:
    import psycopg
    from psycopg import sql

    if curated.empty:
        raise ValueError("Dataset curated vide : la base ne sera pas modifiee.")

    records = [{k: _none(v) for k, v in row.items()} for row in curated.to_dict(orient="records")]
    stats = {"inserted": 0, "updated": 0, "unchanged": 0}

    with psycopg.connect(dsn, connect_timeout=10) as conn:  # une seule transaction
        with conn.cursor() as cur:
            cur.execute(sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(sql.Identifier(schema_name)))
            cur.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema_name)))
            cur.execute(SCHEMA_SQL.read_text(encoding="utf-8"))

            cur.executemany("INSERT INTO season(season_id) VALUES (%s) ON CONFLICT DO NOTHING",
                            [(s,) for s in sorted({r["season"] for r in records})])
            teams = sorted({r[k] for r in records for k in ("home_team", "away_team")})
            cur.executemany("INSERT INTO team(team_name) VALUES (%s) ON CONFLICT (team_name) DO NOTHING",
                            [(t,) for t in teams])
            cur.execute("SELECT team_name, team_id FROM team")
            team_ids = dict(cur.fetchall())

            cols = ", ".join(MATCH_COLUMNS)
            placeholders = ", ".join(["%s"] * len(MATCH_COLUMNS))
            updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in MATCH_COLUMNS if c != "match_id")
            upsert_match = (
                f"INSERT INTO football_match ({cols}) VALUES ({placeholders}) "
                f"ON CONFLICT (match_id) DO UPDATE SET {updates}, updated_at = now() "
                f"WHERE football_match.row_hash IS DISTINCT FROM EXCLUDED.row_hash "
                f"RETURNING (xmax = 0) AS inserted"
            )
            wcols = ", ".join(["match_id", *WEATHER_COLUMNS])
            wupdates = ", ".join(f"{c} = EXCLUDED.{c}" for c in WEATHER_COLUMNS)
            upsert_weather = (
                f"INSERT INTO match_weather ({wcols}) VALUES ({', '.join(['%s'] * (len(WEATHER_COLUMNS) + 1))}) "
                f"ON CONFLICT (match_id) DO UPDATE SET {wupdates}"
            )

            for r in records:
                business = {k: v for k, v in r.items()}
                values = {
                    "match_id": r["match_id"], "season_id": r["season"], "match_date": r["match_date"],
                    "kickoff_time": r["kickoff_time"],
                    "home_team_id": team_ids[r["home_team"]], "away_team_id": team_ids[r["away_team"]],
                    **{k: r[k] for k in ("home_goals", "away_goals", "result", "home_elo", "away_elo",
                                         "elo_diff", "elo_home_win_expectancy", "elo_favorite",
                                         "elo_warmup", "odds_home_avg", "odds_draw_avg", "odds_away_avg",
                                         "market_prob_home", "market_prob_draw", "market_prob_away",
                                         "market_favorite", "odds_valid", "kickoff_time_missing",
                                         "data_quality_status", "source_file", "raw_sha256", "source_row")},
                    "row_hash": row_hash(business),
                    "last_run_id": run_id,
                }
                cur.execute(upsert_match, [values[c] for c in MATCH_COLUMNS])
                returned = cur.fetchone()
                if returned is None:
                    stats["unchanged"] += 1
                    continue
                stats["inserted" if returned[0] else "updated"] += 1
                cur.execute(upsert_weather, [r["match_id"], *[r[c] for c in WEATHER_COLUMNS]])

            cur.execute("SELECT COUNT(*) FROM v_match_analysis")
            stats["rows_in_view"] = cur.fetchone()[0]

            if run_record:
                cur.execute(
                    """INSERT INTO pipeline_run (run_id, started_at, finished_at, status, contract_version,
                           input_rows, accepted_rows, rejected_rows, duplicates_removed,
                           rows_inserted, rows_updated, rows_unchanged, latest_match_date, duration_seconds)
                       VALUES (%(run_id)s, %(started_at)s, now(), %(status)s, %(contract_version)s,
                           %(input_rows)s, %(accepted_rows)s, %(rejected_rows)s, %(duplicates_removed)s,
                           %(inserted)s, %(updated)s, %(unchanged)s, %(latest_match_date)s, %(duration_seconds)s)
                       ON CONFLICT (run_id) DO NOTHING""",
                    {**run_record, **stats},
                )
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description="Recharge le curated existant dans PostgreSQL (UPSERT)")
    parser.add_argument("--dsn", default=os.getenv("DATABASE_URL"))
    parser.add_argument("--schema-name", default="public")
    args = parser.parse_args()
    if not args.dsn:
        parser.error("definir DATABASE_URL ou --dsn")
    settings = load_settings()
    curated = pd.read_parquet(settings.curated_dir / f"{CURATED_NAME}.parquet")
    stats = publish_postgres(curated, args.dsn, run_id="manual-publish", schema_name=args.schema_name)
    print(json.dumps(stats, indent=2))


if __name__ == "__main__":
    main()
