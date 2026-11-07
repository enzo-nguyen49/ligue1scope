"""
Exercice d'incident (test d'incident obligatoire du projet final).

Chaque scenario est joue dans un bac a sable (copie de config/ et de la zone
raw Ligue 1 ; la meteo est lue en place, en lecture seule) : les donnees
reelles du projet ne sont jamais modifiees.

Scenarios :
  1. valeurs_invalides   lignes impossibles injectees -> rejet avec motif
  2. doublons_massifs    saison livree en double      -> quality gate, FAIL, curated intact
  3. changement_schema   colonne FTHG renommee        -> quarantaine + repli sur la version precedente
  4. fichier_vide        livraison vide               -> quarantaine + repli
  5. source_indisponible hote injoignable             -> erreurs journalisees, repli sur le raw
  6. echec_publication   base injoignable             -> FAIL sans publication, puis reprise
  7. rejeu_idempotent    deux executions identiques   -> curated identique octet pour octet

Sorties : reports/incidents/incident_report.json et .md, rapports de run de
chaque scenario, et data/rejected/sample_rejected.csv (rejets du scenario 1).

Usage :
    python src/incident_drill.py
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))

from pipeline import run  # noqa: E402
from settings import PROJECT_ROOT  # noqa: E402

OUT_DIR = PROJECT_ROOT / "reports" / "incidents"
TARGET_SEASON = "2025-2026"
INJECT_STAMP = "20991231T235959Z"  # plus recent que tout fichier reel : c'est lui que le pipeline lira


def sha(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def make_sandbox(base: Path, name: str, overrides: dict | None = None) -> Path:
    root = base / name
    shutil.copytree(PROJECT_ROOT / "config", root / "config")
    shutil.copytree(PROJECT_ROOT / "data" / "raw" / "ligue1", root / "data" / "raw" / "ligue1",
                    ignore=shutil.ignore_patterns("manifest_*"))
    config = yaml.safe_load((root / "config" / "pipeline.yaml").read_text(encoding="utf-8"))
    config["paths"]["raw_weather"] = str(PROJECT_ROOT / config["paths"]["raw_weather"])
    for dotted, value in (overrides or {}).items():
        node = config
        *parents, leaf = dotted.split(".")
        for key in parents:
            node = node[key]
        node[leaf] = value
    (root / "config" / "pipeline.yaml").write_text(yaml.safe_dump(config, allow_unicode=True), encoding="utf-8")
    return root


def latest_raw(root: Path, season: str) -> Path:
    return sorted((root / "data" / "raw" / "ligue1").glob(f"ligue1_{season}_*.csv"))[-1]


def inject(root: Path, mutate) -> Path:
    """Ecrit une nouvelle livraison raw de la saison cible, modifiee par `mutate`."""
    source = latest_raw(root, TARGET_SEASON)
    with source.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.reader(handle))
    target = source.with_name(f"ligue1_{TARGET_SEASON}_{INJECT_STAMP}.csv")
    content = mutate(rows)
    if content is None:
        target.write_bytes(b"")
    else:
        with target.open("w", encoding="utf-8", newline="") as handle:
            csv.writer(handle).writerows(content)
    return target


def invalid_values(rows):
    header, data = rows[0], rows[1:]
    col = {name: i for i, name in enumerate(header)}
    bad = []
    template = data[0]

    def variant(**changes):
        new = list(template)
        for name, value in changes.items():
            new[col[name]] = value
        bad.append(new)

    variant(Date="16/05/2026", HomeTeam="Lyon", AwayTeam="Nice", FTHG="42", FTAG="0", FTR="H")
    variant(Date="16/05/2026", HomeTeam="Lens", AwayTeam="Brest", FTHG="3", FTAG="0", FTR="A")
    variant(Date="31/02/2026", HomeTeam="Lille", AwayTeam="Nantes")
    variant(Date="16/05/2026", HomeTeam="", AwayTeam="Metz")
    variant(Date="16/05/2026", HomeTeam="Rennes", AwayTeam="Rennes", FTHG="1", FTAG="1", FTR="D")
    variant(Date="16/08/2019", HomeTeam="Monaco", AwayTeam="Lorient")
    bad.append(list(data[10]))  # doublon exact d'un match reel
    return [header, *data, *bad]


def double_rows(rows):
    return [rows[0], *rows[1:], *rows[1:]]


def rename_column(rows):
    header = ["HomeGoals" if h == "FTHG" else h for h in rows[0]]
    return [header, *rows[1:]]


def summarize(name: str, expected: str, report: dict, extra: dict | None = None) -> dict:
    return {
        "scenario": name,
        "expected": expected,
        "status": report["status"],
        "run_id": report["run_id"],
        "input_rows": report.get("input_rows"),
        "accepted_rows": report.get("accepted_rows"),
        "rejected_rows": report.get("rejected_rows"),
        "duplicates_removed": report.get("duplicates_removed"),
        "rejection_counts": {k: v for k, v in (report.get("rejection_counts_by_rule") or {}).items() if v},
        "warnings": report["warnings"],
        "errors": report["errors"],
        **(extra or {}),
    }


def keep_evidence(root: Path, name: str, report: dict) -> None:
    target = OUT_DIR / name
    target.mkdir(parents=True, exist_ok=True)
    for rel in ("reports/run_report.json", "reports/quality_report.json", report.get("log_file", "")):
        path = root / rel
        if rel and path.exists():
            shutil.copy(path, target / path.name)
    quarantine = root / "data" / "rejected" / "quarantine"
    if quarantine.exists():
        for reason in quarantine.glob("*.reason.json"):
            shutil.copy(reason, target / reason.name)
    for rejected in (root / "data" / "rejected").glob("rejected_*.csv"):
        shutil.copy(rejected, target / rejected.name)


def main() -> None:
    dsn = os.getenv("DATABASE_URL")
    if OUT_DIR.exists():
        shutil.rmtree(OUT_DIR)
    OUT_DIR.mkdir(parents=True)
    results = []
    ref = None  # date du jour : la fraicheur reste en alerte, comme en reel

    with tempfile.TemporaryDirectory(prefix="ligue1scope_incident_") as tmp:
        base = Path(tmp)

        # 1. Valeurs invalides
        root = make_sandbox(base, "1_valeurs_invalides")
        inject(root, invalid_values)
        report = run(root=root, use_db=False, reference_date=ref)
        keep_evidence(root, "1_valeurs_invalides", report)
        rejected = sorted((root / "data" / "rejected").glob("rejected_*.csv"))
        if rejected:
            shutil.copy(rejected[-1], PROJECT_ROOT / "data" / "rejected" / "sample_rejected.csv")
        results.append(summarize("1_valeurs_invalides",
                                 "7 lignes rejetees avec motif, publication maintenue (PASS_WITH_WARNINGS)", report))

        # 2. Doublons massifs
        root = make_sandbox(base, "2_doublons_massifs")
        run(root=root, use_db=False, reference_date=ref)
        curated = root / "data" / "curated" / "matches_curated.csv"
        before = sha(curated)
        inject(root, double_rows)
        report = run(root=root, use_db=False, reference_date=ref)
        keep_evidence(root, "2_doublons_massifs", report)
        results.append(summarize("2_doublons_massifs", "FAIL par quality gate, curated precedent intact", report,
                                 {"curated_unchanged": sha(curated) == before}))

        # 3. Changement de schema
        root = make_sandbox(base, "3_changement_schema")
        inject(root, rename_column)
        report = run(root=root, use_db=False, reference_date=ref)
        keep_evidence(root, "3_changement_schema", report)
        results.append(summarize("3_changement_schema",
                                 "fichier en quarantaine, repli sur la version precedente, 3 477 lignes", report))

        # 4. Fichier vide
        root = make_sandbox(base, "4_fichier_vide")
        inject(root, lambda rows: None)
        report = run(root=root, use_db=False, reference_date=ref)
        keep_evidence(root, "4_fichier_vide", report)
        results.append(summarize("4_fichier_vide", "fichier en quarantaine, repli sur la version precedente", report))

        # 5. Source indisponible
        root = make_sandbox(base, "5_source_indisponible", {
            "source.ligue1.url_template": "https://source-indisponible.invalid/mmz4281/{code}/F1.csv",
            "source.ligue1.max_retries": 2,
            "source.ligue1.backoff_seconds": 0.5,
        })
        report = run(root=root, collect_mode="url", use_db=False, reference_date=ref)
        keep_evidence(root, "5_source_indisponible", report)
        results.append(summarize("5_source_indisponible",
                                 "erreurs journalisees, repli sur la zone raw existante", report))

        # 6. Echec de publication puis reprise
        root = make_sandbox(base, "6_echec_publication")
        failed = run(root=root, dsn="postgresql://ligue1scope@127.0.0.1:1/injoignable", reference_date=ref)
        keep_evidence(root, "6_echec_publication", failed)
        recovery = run(root=root, dsn=dsn, use_db=bool(dsn), db_schema="incident_drill", reference_date=ref)
        results.append(summarize("6_echec_publication", "FAIL sans publication, puis reprise par simple rejeu",
                                 failed, {"recovery_status": recovery["status"],
                                          "recovery_database": recovery["steps"].get("publish", {}).get("database")}))

        # 7. Rejeu idempotent
        root = make_sandbox(base, "7_rejeu_idempotent")
        first = run(root=root, dsn=dsn, use_db=bool(dsn), db_schema="incident_drill_replay", reference_date=ref)
        h1 = sha(root / "data" / "curated" / "matches_curated.csv")
        second = run(root=root, dsn=dsn, use_db=bool(dsn), db_schema="incident_drill_replay", reference_date=ref)
        h2 = sha(root / "data" / "curated" / "matches_curated.csv")
        keep_evidence(root, "7_rejeu_idempotent", second)
        results.append(summarize("7_rejeu_idempotent", "curated identique et 0 ligne inseree au 2e passage",
                                 second, {"curated_sha256_run1": h1, "curated_sha256_run2": h2,
                                          "identical": h1 == h2,
                                          "database_run1": first["steps"]["publish"].get("database"),
                                          "database_run2": second["steps"]["publish"].get("database")}))

    if dsn:  # nettoyage des schemas de l'exercice
        import psycopg
        with psycopg.connect(dsn, autocommit=True) as conn:
            for schema in ("incident_drill", "incident_drill_replay"):
                conn.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")

    payload = {"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
               "database_used": bool(dsn), "scenarios": results}
    (OUT_DIR / "incident_report.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str),
                                                  encoding="utf-8")
    write_markdown(payload)
    print(f"\nRapport : {OUT_DIR.relative_to(PROJECT_ROOT)}/incident_report.md")


def write_markdown(payload: dict) -> None:
    lines = ["# Rapport d'incident - exercice automatise", "",
             f"Genere le {payload['generated_at']} par `python src/incident_drill.py`. "
             f"PostgreSQL utilise : {'oui' if payload['database_used'] else 'non'}.", "",
             "| Scenario | Attendu | Statut obtenu | Lues | Acceptees | Rejetees | Detection |",
             "|---|---|---|---|---|---|---|"]
    for r in payload["scenarios"]:
        detection = "; ".join((r["errors"] or [w for w in r["warnings"] if "fraicheur" not in w and "PostgreSQL" not in w])[:2])
        lines.append(f"| {r['scenario']} | {r['expected']} | **{r['status']}** | {r['input_rows']} | "
                     f"{r['accepted_rows']} | {r['rejected_rows']} | {' '.join(detection.replace('|', '/').split())} |")
    lines += ["", "Preuves par scenario (run report, quality report, log, motifs de quarantaine, rejets) : "
              "sous-dossiers de `reports/incidents/`.", ""]
    for r in payload["scenarios"]:
        extras = {k: v for k, v in r.items() if k not in {"scenario", "expected", "status", "run_id", "input_rows",
                                                           "accepted_rows", "rejected_rows", "warnings", "errors"}}
        lines.append(f"- **{r['scenario']}** (run `{r['run_id']}`) : " + json.dumps(extras, ensure_ascii=False, default=str))
    (OUT_DIR / "incident_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
