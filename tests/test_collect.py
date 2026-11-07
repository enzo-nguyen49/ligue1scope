"""Tests du collecteur : URL, idempotence par empreinte, source indisponible."""

import requests

from collect import collect, season_code
from settings import load_settings


class FakeResponse:
    def __init__(self, status_code, content=b""):
        self.status_code = status_code
        self.content = content


class FakeSession:
    """Simule Football-Data : renvoie un contenu par saison ou une erreur."""

    def __init__(self, contents=None, error=None, status=200):
        self.contents = contents or {}
        self.error = error
        self.status = status
        self.calls = []

    def get(self, url, timeout, headers):
        self.calls.append(url)
        if self.error:
            raise self.error
        code = url.split("/")[-2]
        return FakeResponse(self.status, self.contents.get(code, b""))


def test_season_code():
    assert season_code("2025-2026") == "2526"


def test_collecte_url_ecrit_un_raw_horodate_puis_ne_reecrit_pas(mini_project):
    settings = load_settings(mini_project)
    session = FakeSession({"2324": b"Date,HomeTeam\n", "2425": b"Date,AwayTeam\n"})
    first = collect(settings, "url", session=session)
    assert first["collected"] == 2
    assert "mmz4281/2324/F1.csv" in session.calls[0]

    second = collect(settings, "url", session=session)
    assert second["collected"] == 0 and second["unchanged"] == 2  # meme SHA-256 : rien d'ecrit


def test_source_indisponible_est_journalisee_sans_lever(mini_project):
    settings = load_settings(mini_project)
    before = sorted(p.name for p in settings.raw_ligue1_dir.glob("ligue1_*.csv"))
    session = FakeSession(error=requests.ConnectionError("DNS failure"))
    summary = collect(settings, "url", session=session)
    assert summary["unavailable"] == 2
    assert len(session.calls) == 2 * settings.source["max_retries"]  # nouvelles tentatives
    assert sorted(p.name for p in settings.raw_ligue1_dir.glob("ligue1_*.csv")) == before


def test_erreur_404_definitive_sans_nouvel_essai(mini_project):
    settings = load_settings(mini_project)
    session = FakeSession(status=404)
    summary = collect(settings, "url", session=session)
    assert summary["unavailable"] == 2 and len(session.calls) == 2


def test_collecte_locale(mini_project, tmp_path):
    settings = load_settings(mini_project)
    source = tmp_path / "downloads"
    source.mkdir()
    (source / "23-24.csv").write_bytes(b"Date,HomeTeam\n01/09/2023,Lyon\n")
    summary = collect(settings, "local", source_dir=source)
    assert summary["collected"] == 1 and summary["unavailable"] == 1  # 24-25.csv absent
