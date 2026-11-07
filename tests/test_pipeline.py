"""Tests de bout en bout du pipeline et scenarios d'incident (mini-projet)."""

import hashlib
import json
from datetime import datetime

import requests

from conftest import HEADER, season_rows, write_raw
from pipeline import run

REF = datetime(2024, 9, 25)  # 4 jours apres le dernier match synthetique : donnees fraiches


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def curated_file(root):
    return root / "data" / "curated" / "matches_curated.csv"


def test_run_nominal_produit_curated_mart_et_rapports(mini_project):
    report = run(root=mini_project, use_db=False, reference_date=REF)
    assert report["status"] == "PASS_WITH_WARNINGS"  # seule alerte : base non chargee
    assert report["input_rows"] == 24 and report["accepted_rows"] == 24 and report["rejected_rows"] == 0
    assert curated_file(mini_project).exists()
    assert (mini_project / "data" / "curated" / "matches_curated.parquet").exists()
    assert (mini_project / "data" / "curated" / "mart" / "kpi_tranche_elo.csv").exists()
    for name in ("run_report.json", "quality_report.json", "profiling_report.json", "run_history.jsonl"):
        assert (mini_project / "reports" / name).exists()
    saved = json.loads((mini_project / "reports" / "run_report.json").read_text(encoding="utf-8"))
    assert saved["run_id"] == report["run_id"]


def test_rejeu_idempotent_curated_identique_octet_pour_octet(mini_project):
    run(root=mini_project, use_db=False, reference_date=REF)
    first = sha(curated_file(mini_project))
    second_report = run(root=mini_project, use_db=False, reference_date=REF)
    assert sha(curated_file(mini_project)) == first
    assert second_report["accepted_rows"] == 24


def test_incident_valeurs_invalides_rejetees_avec_motif(mini_project):
    rows = season_rows(2024)
    rows[0][5] = "42"          # score impossible
    rows[1][7] = "A"           # resultat incoherent
    write_raw(mini_project / "data/raw/ligue1", "2024-2025", "20260201T000000Z", rows)
    report = run(root=mini_project, use_db=False, reference_date=REF)
    # 2 rejets sur 24 = 8,3 % > seuil 5 % : quality gate
    assert report["status"] == "FAIL"
    rejected = list((mini_project / "data" / "rejected").glob("rejected_*.csv"))
    assert len(rejected) == 1
    content = rejected[0].read_text(encoding="utf-8")
    assert "validite_buts" in content and "coherence_resultat" in content


def test_incident_doublons_massifs_bloque_la_publication(mini_project):
    run(root=mini_project, use_db=False, reference_date=REF)
    before = sha(curated_file(mini_project))
    rows = season_rows(2024)
    write_raw(mini_project / "data/raw/ligue1", "2024-2025", "20260201T000000Z", rows + rows)
    report = run(root=mini_project, use_db=False, reference_date=REF)
    assert report["status"] == "FAIL"
    assert report["duplicates_removed"] == 12
    assert "Quality gate" in report["errors"][0]
    assert sha(curated_file(mini_project)) == before  # le curated precedent est intact


def test_incident_changement_de_schema_quarantaine_et_repli(mini_project):
    header = [h if h != "FTHG" else "HomeGoals" for h in HEADER]
    bad = write_raw(mini_project / "data/raw/ligue1", "2024-2025", "20260201T000000Z",
                    season_rows(2024), header=header)
    report = run(root=mini_project, use_db=False, reference_date=REF)
    assert report["status"] == "PASS_WITH_WARNINGS"
    assert report["accepted_rows"] == 24  # repli sur la version precedente valide
    quarantine = mini_project / "data" / "rejected" / "quarantine"
    assert (quarantine / bad.name).exists() and not bad.exists()
    assert "schema_invalide" in (quarantine / f"{bad.name}.reason.json").read_text(encoding="utf-8")
    assert any("quarantaine" in w for w in report["warnings"])


def test_incident_fichier_vide(mini_project):
    path = mini_project / "data/raw/ligue1" / "ligue1_2024-2025_20260201T000000Z.csv"
    path.write_bytes(b"")
    report = run(root=mini_project, use_db=False, reference_date=REF)
    assert report["status"] == "PASS_WITH_WARNINGS"
    assert any("fichier_vide" in w for w in report["warnings"])


def test_incident_saison_sans_aucun_raw_valide_echoue(mini_project):
    for path in (mini_project / "data/raw/ligue1").glob("ligue1_2024-2025_*.csv"):
        path.write_bytes(b"")
    report = run(root=mini_project, use_db=False, reference_date=REF)
    assert report["status"] == "FAIL"
    assert "RawDataError" in report["errors"][0]


def test_incident_source_indisponible_repli_sur_raw(mini_project):
    class DownSession:
        def get(self, *args, **kwargs):
            raise requests.ConnectionError("source indisponible")

    report = run(root=mini_project, collect_mode="url", use_db=False, reference_date=REF,
                 session=DownSession())
    assert report["status"] == "PASS_WITH_WARNINGS"
    assert report["steps"]["collect"]["unavailable"] == 2
    assert report["accepted_rows"] == 24


def test_incident_echec_de_publication_base(mini_project):
    report = run(root=mini_project, dsn="postgresql://nobody@127.0.0.1:1/none", reference_date=REF)
    assert report["status"] == "FAIL"
    assert report["steps"]["publish"]["status"] == "failed"
