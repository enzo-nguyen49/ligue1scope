"""Tests d'integration PostgreSQL (UPSERT). Actifs si TEST_POSTGRES_DSN est defini."""

import os
from datetime import datetime
from uuid import uuid4

import pandas as pd
import pytest

from pipeline import run
from publish import publish_postgres

DSN = os.getenv("TEST_POSTGRES_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="definir TEST_POSTGRES_DSN pour les tests PostgreSQL")
REF = datetime(2024, 9, 25)


@pytest.fixture
def schema():
    import psycopg
    from psycopg import sql

    name = f"test_{uuid4().hex[:10]}"
    yield name
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(name)))


def count(schema_name, query):
    import psycopg

    with psycopg.connect(DSN, options=f"-c search_path={schema_name}") as conn:
        return conn.execute(query).fetchone()[0]


def curated(root):
    return pd.read_parquet(root / "data" / "curated" / "matches_curated.parquet")


def test_upsert_idempotent_puis_mise_a_jour_ciblee(mini_project, schema):
    run(root=mini_project, use_db=False, reference_date=REF)
    df = curated(mini_project)

    first = publish_postgres(df, DSN, "run-1", schema)
    second = publish_postgres(df, DSN, "run-2", schema)
    assert first["inserted"] == 24 and first["updated"] == 0
    assert second == {"inserted": 0, "updated": 0, "unchanged": 24, "rows_in_view": 24}

    corrected = df.copy()
    corrected.loc[0, "odds_home_avg"] = 9.99  # correction de la source sur un seul match
    third = publish_postgres(corrected, DSN, "run-3", schema)
    assert third["updated"] == 1 and third["unchanged"] == 23
    assert count(schema, "SELECT COUNT(*) FROM football_match") == 24
    assert count(schema, "SELECT COUNT(*) FROM v_kpi_tranche_elo") >= 1


def test_violation_de_contrainte_annule_toute_la_transaction(mini_project, schema):
    import psycopg

    run(root=mini_project, use_db=False, reference_date=REF)
    df = curated(mini_project)
    publish_postgres(df, DSN, "run-1", schema)

    broken = df.copy()
    broken.loc[5, "result"] = "X"  # viole le CHECK sur result
    broken.loc[0, "odds_home_avg"] = 9.99  # modification qui doit etre annulee aussi
    with pytest.raises(psycopg.errors.CheckViolation):
        publish_postgres(broken, DSN, "run-2", schema)
    assert count(schema, "SELECT COUNT(*) FROM football_match WHERE odds_home_avg = 9.99") == 0
    assert count(schema, "SELECT COUNT(*) FROM football_match") == 24


def test_pipeline_complet_avec_base_et_journal_des_runs(mini_project, schema):
    report = run(root=mini_project, dsn=DSN, db_schema=schema, reference_date=REF)
    assert report["status"] == "PASS_WITH_WARNINGS"  # seule alerte : pas de meteo dans le mini-projet
    assert report["steps"]["publish"]["database"]["inserted"] == 24
    assert count(schema, "SELECT COUNT(*) FROM pipeline_run") == 1
