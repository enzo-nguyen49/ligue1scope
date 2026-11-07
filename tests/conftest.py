"""Fixtures : un mini-projet autonome (2 saisons synthetiques) dans tmp_path."""

import csv
import itertools
import shutil
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

HEADER = ["Div", "Date", "Time", "HomeTeam", "AwayTeam", "FTHG", "FTAG", "FTR", "AvgH", "AvgD", "AvgA"]
TEAMS = ["Lyon", "Nice", "Lens", "Brest"]


def season_rows(start_year: int) -> list[list[str]]:
    """Championnat aller-retour a 4 equipes : 12 matchs, scores deterministes."""
    rows = []
    for i, (home, away) in enumerate(itertools.permutations(TEAMS, 2)):
        day = 10 + i
        hg, ag = (i % 3, (i + 1) % 2)
        ftr = "H" if hg > ag else ("A" if hg < ag else "D")
        rows.append(["F1", f"{day:02d}/09/{start_year}", "21:00", home, away, str(hg), str(ag), ftr,
                     "2.10", "3.30", "3.60"])
    return rows


def write_raw(raw_dir: Path, season: str, stamp: str, rows: list[list[str]], header=HEADER) -> Path:
    raw_dir.mkdir(parents=True, exist_ok=True)
    path = raw_dir / f"ligue1_{season}_{stamp}.csv"
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        writer.writerows(rows)
    return path


@pytest.fixture
def mini_project(tmp_path):
    (tmp_path / "config").mkdir()
    config = yaml.safe_load((ROOT / "config" / "pipeline.yaml").read_text(encoding="utf-8"))
    config["source"]["ligue1"]["seasons"] = ["2023-2024", "2024-2025"]
    config["source"]["ligue1"]["backoff_seconds"] = 0
    config["elo"]["warmup_seasons"] = 0
    (tmp_path / "config" / "pipeline.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")
    shutil.copy(ROOT / "config" / "data_contract.yaml", tmp_path / "config" / "data_contract.yaml")
    (tmp_path / "config" / "stadiums.csv").write_text("team,stadium,city,latitude,longitude,verified\n",
                                                      encoding="utf-8")
    raw_dir = tmp_path / "data" / "raw" / "ligue1"
    write_raw(raw_dir, "2023-2024", "20260101T000000Z", season_rows(2023))
    write_raw(raw_dir, "2024-2025", "20260101T000000Z", season_rows(2024))
    return tmp_path
