"""Tests unitaires des regles de qualite (src/validate.py).

Chaque test injecte une anomalie precise et verifie : la bonne regle compte
l'echec, la ligne part en rejet avec son motif, et rien n'est supprime en silence.
"""

from datetime import datetime

import pandas as pd
import pytest

from validate import apply_quality_rules

REF = datetime(2024, 9, 1)


def row(**overrides):
    base = {
        "season": "2024-2025", "source_file": "f.csv", "raw_sha256": "x" * 64, "source_row": 2,
        "Date": "10/08/2024", "Time": "21:00", "HomeTeam": "Lyon", "AwayTeam": "Nice",
        "FTHG": "2", "FTAG": "1", "FTR": "H",
        "odds_home_avg": "2.1", "odds_draw_avg": "3.3", "odds_away_avg": "3.6",
    }
    base.update(overrides)
    return base


def run(*rows):
    return apply_quality_rules(pd.DataFrame(list(rows)), reference_date=REF)


def failures(result, rule):
    return result.counters[rule]


def test_ligne_propre_acceptee():
    result = run(row())
    assert len(result.accepted) == 1 and len(result.rejected) == 0
    row_rules = [r for r in result.rules if r["action"] in ("REJETER", "DEDUPLIQUER", "SIGNALER")]
    assert all(r["failures"] == 0 for r in row_rules)
    assert result.freshness["status"] == "FRESH"


@pytest.mark.parametrize("field", ["Date", "HomeTeam", "AwayTeam", "FTHG", "FTAG", "FTR"])
def test_completude_rejette_champ_obligatoire_vide(field):
    result = run(row(**{field: pd.NA}))
    assert failures(result, "completude_champs_obligatoires") == 1
    assert "completude_champs_obligatoires" in result.rejected.iloc[0]["rejection_reasons"]


def test_validite_date_rejette_date_impossible():
    result = run(row(Date="31/02/2024"))
    assert failures(result, "validite_date") == 1
    assert len(result.accepted) == 0


@pytest.mark.parametrize("goals", ["42", "-1", "1.5", "deux"])
def test_validite_buts_rejette_valeur_hors_domaine(goals):
    result = run(row(FTHG=goals))
    assert failures(result, "validite_buts") == 1


def test_coherence_saison_rejette_date_hors_saison():
    result = run(row(Date="10/08/2019"))
    assert failures(result, "coherence_saison") == 1


def test_coherence_resultat_rejette_ftr_contradictoire():
    result = run(row(FTHG="3", FTAG="0", FTR="A"))
    assert failures(result, "coherence_resultat") == 1


def test_coherence_equipes_rejette_meme_club():
    result = run(row(AwayTeam="Lyon"))
    assert failures(result, "coherence_equipes") == 1


def test_unicite_garde_la_premiere_occurrence_et_compte_les_copies():
    result = run(row(), row(source_row=3), row(source_row=4))
    assert result.duplicates_removed == 2
    assert len(result.accepted) == 1 and result.accepted.iloc[0]["source_row"] == 2
    assert (result.rejected["rejection_reasons"] == "unicite_match").all()


def test_une_ligne_invalide_ne_vole_pas_la_cle_d_une_ligne_valide():
    result = run(row(FTR="A"), row(source_row=3))  # meme cle, la 1re est incoherente
    assert len(result.accepted) == 1 and result.accepted.iloc[0]["source_row"] == 3
    assert result.duplicates_removed == 0


def test_cotes_invalides_signalees_sans_rejet():
    result = run(row(odds_home_avg="0.5"))
    assert failures(result, "validite_cotes") == 1
    assert len(result.accepted) == 1
    assert pd.isna(result.accepted.iloc[0]["odds_home_avg"])
    assert not result.accepted.iloc[0]["odds_valid"]


def test_plusieurs_motifs_cumules_sur_une_meme_ligne():
    result = run(row(FTHG="42", AwayTeam="Lyon"))
    reasons = result.rejected.iloc[0]["rejection_reasons"].split(";")
    assert {"validite_buts", "coherence_equipes"} <= set(reasons)


def test_fraicheur_alerte_si_donnees_anciennes():
    result = apply_quality_rules(pd.DataFrame([row()]), {"freshness_max_days": 30},
                                 reference_date=datetime(2025, 1, 1))
    assert result.freshness["status"] == "STALE"
    assert any("fraicheur" in alert for alert in result.alerts)
    assert len(result.accepted) == 1  # une alerte ne rejette rien


def test_tracabilite_bloque_si_origine_absente():
    result = run(row(source_file=pd.NA))
    assert failures(result, "tracabilite") == 1
    assert result.blocking
