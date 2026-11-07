"""
Orchestrateur Ligue1Scope : collecte -> profilage -> validation ->
transformation -> publication, avec run_id, logs et rapport d'execution.

Statuts :
- PASS               : aucune alerte ;
- PASS_WITH_WARNINGS : publie, mais au moins une alerte (rejets, fraicheur,
                       repli sur un raw precedent, base non chargee...) ;
- FAIL               : erreur bloquante ou quality gate depasse ; RIEN n'est
                       publie, le curated et la base precedents sont conserves.

Exemples :
    python src/pipeline.py                      # rejoue depuis la zone raw
    python src/pipeline.py --collect url        # telecharge Football-Data puis rejoue
    python src/pipeline.py --collect local --source-dir data/source_seasons
    python src/pipeline.py --no-db              # n'essaie pas PostgreSQL

Code de sortie : 0 si PASS ou PASS_WITH_WARNINGS, 1 si FAIL.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
import traceback
import uuid
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from collect import collect  # noqa: E402
from common import load_raw_ligue1, select_raw_files  # noqa: E402
from profile_data import build_profile  # noqa: E402
from publish import publish_postgres, write_files  # noqa: E402
from settings import load_settings  # noqa: E402
from transform import build_curated, build_mart, kpi_summary  # noqa: E402
from validate import apply_quality_rules  # noqa: E402

log = logging.getLogger("ligue1scope")


def new_run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:6]


def setup_logging(log_file: Path) -> None:
    log_file.parent.mkdir(parents=True, exist_ok=True)
    log.handlers.clear()
    log.setLevel(logging.INFO)
    log.propagate = False
    fmt = logging.Formatter("%(asctime)s | %(levelname)-7s | %(message)s", "%Y-%m-%dT%H:%M:%S")
    for handler in (logging.StreamHandler(sys.stdout), logging.FileHandler(log_file, encoding="utf-8")):
        handler.setFormatter(fmt)
        log.addHandler(handler)


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    os.replace(tmp, path)


def run(
    root: Path | None = None,
    collect_mode: str | None = None,
    source_dir: Path | None = None,
    dsn: str | None = None,
    use_db: bool = True,
    db_schema: str = "public",
    reference_date: datetime | None = None,
    session=None,
) -> dict:
    settings = load_settings(root)
    run_id = new_run_id()
    reports = settings.reports_dir
    setup_logging(reports / "logs" / f"pipeline_{run_id}.log")
    started = time.perf_counter()
    started_at = datetime.now(timezone.utc)

    report: dict = {
        "pipeline": settings.pipeline_name,
        "run_id": run_id,
        "started_at": started_at.isoformat(timespec="seconds"),
        "contract_version": settings.contract_version,
        "status": None,
        "steps": {},
        "warnings": [],
        "errors": [],
    }
    warnings: list[str] = report["warnings"]

    def step(name: str):
        report["steps"][name] = {"status": "running"}
        return time.perf_counter()

    def done(name: str, t0: float, status: str = "ok", **extra):
        report["steps"][name] = {"status": status, "duration_seconds": round(time.perf_counter() - t0, 3), **extra}

    log.info("=== Run %s | contrat v%s | racine %s ===", run_id, settings.contract_version, settings.root)
    try:
        # 1. Collecte (optionnelle : sinon on rejoue la zone raw existante)
        if collect_mode:
            t0 = step("collect")
            kwargs = {"session": session} if session is not None else {}
            summary = collect(settings, collect_mode, source_dir, run_id=run_id, **kwargs)
            if summary["unavailable"]:
                warnings.append(
                    f"source indisponible pour {summary['unavailable']} saison(s) : "
                    f"repli sur la derniere version raw collectee"
                )
            done("collect", t0, **{k: v for k, v in summary.items() if k != "entries"},
                 unavailable_seasons=[e["season"] for e in summary["entries"] if e["status"] == "source_unavailable"])
        else:
            report["steps"]["collect"] = {"status": "skipped", "reason": "rejeu depuis la zone raw"}

        # 2. Lecture raw + profilage
        t0 = step("profile")
        raw_files = select_raw_files(settings, run_id, warnings)
        raw = load_raw_ligue1(raw_files)
        report["inputs"] = [
            {"season": f.season, "raw_file": f.path.name, "sha256": f.sha256, "fallback": f.fallback}
            for f in raw_files
        ]
        profile = build_profile(raw)
        write_json(reports / "profiling_report.json", {"run_id": run_id, **profile})
        done("profile", t0, rows=profile["shape"]["rows"], output="reports/profiling_report.json")

        # 3. Validation
        t0 = step("validate")
        result = apply_quality_rules(raw, settings.quality, reference_date=reference_date)
        input_rows, rejected_rows = len(raw), len(result.rejected)
        rejected_ratio = rejected_rows / input_rows if input_rows else 1.0
        report.update(
            input_rows=input_rows,
            accepted_rows=len(result.accepted),
            rejected_rows=rejected_rows,
            duplicates_removed=result.duplicates_removed,
            rejected_ratio=round(rejected_ratio, 4),
            freshness=result.freshness,
        )
        rejected_file = None
        if rejected_rows:
            rejected = result.rejected.assign(run_id=run_id,
                                              rejected_at=datetime.now(timezone.utc).isoformat(timespec="seconds"))
            rejected_path = settings.rejected_dir / f"rejected_{run_id}.csv"
            rejected_path.parent.mkdir(parents=True, exist_ok=True)
            rejected.to_csv(rejected_path, index=False)
            rejected_file = str(rejected_path.relative_to(settings.root))
            warnings.append(f"{rejected_rows} ligne(s) rejetee(s), conservees dans {rejected_file}")
            for rule in result.rules:
                if rule["failures"] and rule["action"] in ("REJETER", "DEDUPLIQUER"):
                    log.warning("Regle %s %-32s %s echec(s) -> %s", rule["id"], rule["rule"],
                                rule["failures"], rule["action"])
        signalled = next(r for r in result.rules if r["rule"] == "validite_cotes")["failures"]
        if signalled:
            warnings.append(f"validite_cotes : {signalled} ligne(s) avec cotes neutralisees (NULL)")
        warnings.extend(result.alerts)

        max_ratio = float(settings.quality["max_rejected_ratio"])
        gate_ok = rejected_ratio <= max_ratio and not result.blocking
        quality_report = {
            "run_id": run_id,
            "contract_version": settings.contract_version,
            "input_rows": input_rows,
            "accepted_rows": len(result.accepted),
            "rejected_rows": rejected_rows,
            "duplicates_removed": result.duplicates_removed,
            "rejected_ratio": round(rejected_ratio, 4),
            "quality_gate": {"max_rejected_ratio": max_ratio, "passed": gate_ok},
            "freshness": result.freshness,
            "rules": result.rules,
            "rejected_file": rejected_file,
        }
        write_json(reports / "quality_report.json", quality_report)
        done("validate", t0, rejected_file=rejected_file, quality_gate_passed=gate_ok)
        report["rejection_counts_by_rule"] = result.counters

        if not gate_ok:
            reasons = result.blocking or [
                f"taux de rejet {rejected_ratio:.1%} > seuil {max_ratio:.0%} (quality gate)"
            ]
            raise RuntimeError("Quality gate non franchi : " + " ; ".join(reasons))

        # 4. Transformation
        t0 = step("transform")
        curated = build_curated(result.accepted, settings, warnings)
        mart = build_mart(curated)
        kpi = kpi_summary(curated)
        if kpi["weather_coverage_ratio"] < float(settings.quality["min_weather_coverage"]):
            warnings.append(f"couverture meteo {kpi['weather_coverage_ratio']:.0%} sous le seuil")
        report["kpi"] = kpi
        done("transform", t0, curated_rows=len(curated), columns=len(curated.columns))

        # 5. Publication fichiers (atomique) puis base (transactionnelle)
        t0 = step("publish")
        outputs = write_files(curated, mart, settings)
        report["outputs"] = outputs
        dsn = dsn or os.getenv("DATABASE_URL")
        db_stats = None
        if use_db and dsn:
            provisional = "PASS_WITH_WARNINGS" if warnings else "PASS"
            db_stats = publish_postgres(curated, dsn, run_id, schema_name=db_schema, run_record={
                "run_id": run_id, "started_at": started_at, "status": provisional,
                "contract_version": settings.contract_version, "input_rows": input_rows,
                "accepted_rows": len(result.accepted), "rejected_rows": rejected_rows,
                "duplicates_removed": result.duplicates_removed,
                "latest_match_date": result.freshness.get("latest_match_date"),
                "duration_seconds": round(time.perf_counter() - started, 3),
            })
            log.info("PostgreSQL : %s insere(s), %s mis a jour, %s inchange(s)",
                     db_stats["inserted"], db_stats["updated"], db_stats["unchanged"])
        else:
            warnings.append("PostgreSQL non charge (DATABASE_URL absent ou --no-db) : fichiers curated seulement")
        done("publish", t0, database=db_stats or "skipped")

        report["status"] = "PASS_WITH_WARNINGS" if warnings else "PASS"
    except Exception as exc:  # noqa: BLE001 - on veut tracer toute erreur dans le rapport
        report["status"] = "FAIL"
        report["errors"].append(f"{exc.__class__.__name__}: {exc}")
        for name, info in report["steps"].items():
            if info.get("status") == "running":
                info["status"] = "failed"
        log.error("Run en echec : %s", exc)
        log.debug(traceback.format_exc())
        log.error("Aucune publication : le curated et la base precedents sont conserves.")

    report["finished_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    report["duration_seconds"] = round(time.perf_counter() - started, 3)
    report["log_file"] = f"reports/logs/pipeline_{run_id}.log"

    write_json(reports / "run_report.json", report)
    write_json(reports / "runs" / f"run_report_{run_id}.json", report)
    with (reports / "run_history.jsonl").open("a", encoding="utf-8") as history:
        history.write(json.dumps({
            "run_id": run_id, "status": report["status"], "finished_at": report["finished_at"],
            "input_rows": report.get("input_rows"), "accepted_rows": report.get("accepted_rows"),
            "rejected_rows": report.get("rejected_rows"), "duplicates_removed": report.get("duplicates_removed"),
            "duration_seconds": report["duration_seconds"],
        }, ensure_ascii=False) + "\n")

    for message in warnings:
        log.warning("Alerte : %s", message)
    log.info("=== Statut %s en %.2fs | %s lues, %s acceptees, %s rejetees | rapport reports/run_report.json ===",
             report["status"], report["duration_seconds"], report.get("input_rows"),
             report.get("accepted_rows"), report.get("rejected_rows"))
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Pipeline Ligue1Scope")
    parser.add_argument("--collect", choices=["url", "local"], default=None,
                        help="collecter avant de traiter (sinon rejoue la zone raw)")
    parser.add_argument("--source-dir", type=Path, default=None, help="dossier pour --collect local")
    parser.add_argument("--dsn", default=None, help="DSN PostgreSQL (defaut : DATABASE_URL)")
    parser.add_argument("--no-db", action="store_true", help="ne pas charger PostgreSQL")
    parser.add_argument("--db-schema", default="public", help="schema PostgreSQL cible")
    parser.add_argument("--root", type=Path, default=None, help="racine du projet (bac a sable)")
    args = parser.parse_args()
    report = run(root=args.root, collect_mode=args.collect, source_dir=args.source_dir,
                 dsn=args.dsn, use_db=not args.no_db, db_schema=args.db_schema)
    sys.exit(1 if report["status"] == "FAIL" else 0)


if __name__ == "__main__":
    main()
