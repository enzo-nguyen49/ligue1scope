"""
Lecture de la zone raw Ligue 1 (aucun nettoyage ici).

Responsabilites :
- choisir, pour chaque saison configuree, le fichier raw le plus recent dont
  le schema est conforme au contrat ;
- mettre en quarantaine (sans les supprimer) les fichiers raw invalides :
  fichier vide, colonnes obligatoires absentes, encodage illisible ;
- empiler les saisons en gardant les valeurs sous forme de texte : le typage
  et les controles sont faits par validate.py, jamais ici.

Reprise apres incident : si le fichier le plus recent d'une saison est
invalide, il est deplace en quarantaine et le pipeline se replie sur la
version precedente valide de la meme saison.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from settings import Settings

log = logging.getLogger("ligue1scope")

RAW_FILE_RE = re.compile(r"^ligue1_(\d{4}-\d{4})_(\d{8}T\d{6}Z)\.csv$")

# Colonnes sans lesquelles un fichier saison est inexploitable (contrat).
REQUIRED_SOURCE_COLUMNS = ["Date", "HomeTeam", "AwayTeam", "FTHG", "FTAG", "FTR"]

# Colonnes conservees si presentes (Time n'existe qu'a partir de 2019-2020).
OPTIONAL_SOURCE_COLUMNS = ["Div", "Time", "HTHG", "HTAG", "HS", "AS", "HST", "AST"]

# Cotes moyennes du marche avant-match : le nom change selon la saison
# (BbAv* jusqu'en 2018-2019, Avg* ensuite). On harmonise ici, sans convertir.
ODDS_ALIASES = {
    "odds_home_avg": ("AvgH", "BbAvH"),
    "odds_draw_avg": ("AvgD", "BbAvD"),
    "odds_away_avg": ("AvgA", "BbAvA"),
}


class RawDataError(RuntimeError):
    """Erreur bloquante sur la zone raw (saison sans aucun fichier valide)."""


@dataclass
class RawFile:
    season: str
    path: Path
    sha256: str
    fallback: bool = False  # True si une version plus recente a ete ecartee


def sha256_of_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def check_raw_schema(path: Path) -> str | None:
    """Retourne None si le fichier est exploitable, sinon le motif d'ecart."""
    if path.stat().st_size == 0:
        return "fichier_vide"
    try:
        header = pd.read_csv(path, encoding="utf-8-sig", nrows=5, dtype=str)
    except (UnicodeDecodeError, pd.errors.ParserError, pd.errors.EmptyDataError) as exc:
        return f"fichier_illisible: {exc.__class__.__name__}"
    missing = [col for col in REQUIRED_SOURCE_COLUMNS if col not in header.columns]
    if missing:
        return f"schema_invalide: colonnes absentes {missing}"
    if header.empty:
        return "fichier_vide: en-tete sans aucune ligne"
    return None


def quarantine(path: Path, reason: str, settings: Settings, run_id: str) -> Path:
    """Deplace un fichier raw invalide hors de la zone raw, avec son motif."""
    settings.quarantine_dir.mkdir(parents=True, exist_ok=True)
    target = settings.quarantine_dir / path.name
    shutil.move(str(path), target)
    (settings.quarantine_dir / f"{path.name}.reason.json").write_text(
        json.dumps(
            {
                "file": path.name,
                "reason": reason,
                "run_id": run_id,
                "quarantined_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return target


def raw_files_by_season(raw_dir: Path) -> dict[str, list[Path]]:
    """Fichiers raw par saison, du plus recent au plus ancien."""
    grouped: dict[str, list[Path]] = {}
    for path in raw_dir.glob("ligue1_*.csv"):
        match = RAW_FILE_RE.match(path.name)
        if match:
            grouped.setdefault(match.group(1), []).append(path)
    # Le suffixe AAAAMMJJTHHMMSSZ est ordonne chronologiquement.
    return {season: sorted(paths, reverse=True) for season, paths in grouped.items()}


def select_raw_files(settings: Settings, run_id: str, warnings: list[str]) -> list[RawFile]:
    by_season = raw_files_by_season(settings.raw_ligue1_dir)
    selected: list[RawFile] = []
    missing: list[str] = []

    for season in settings.seasons:
        chosen = None
        skipped = 0
        for path in by_season.get(season, []):
            reason = check_raw_schema(path)
            if reason is None:
                chosen = RawFile(season, path, sha256_of_file(path), fallback=skipped > 0)
                break
            skipped += 1
            target = quarantine(path, reason, settings, run_id)
            message = f"{path.name} mis en quarantaine ({reason}) -> {target.relative_to(settings.root)}"
            log.warning(message)
            warnings.append(message)
        if chosen is None:
            missing.append(season)
            continue
        if chosen.fallback:
            message = f"Saison {season} : repli sur la version valide precedente {chosen.path.name}"
            log.warning(message)
            warnings.append(message)
        selected.append(chosen)

    if missing:
        raise RawDataError(
            f"Aucun fichier raw valide pour les saisons {missing} dans "
            f"{settings.raw_ligue1_dir}. Lancer la collecte (src/pipeline.py --collect)."
        )
    return selected


def load_raw_ligue1(files: list[RawFile]) -> pd.DataFrame:
    """Empile les saisons. Toutes les valeurs restent du texte brut."""
    frames = []
    for raw in files:
        df = pd.read_csv(raw.path, encoding="utf-8-sig", dtype=str, keep_default_na=False)
        df = df.replace({"": pd.NA})
        out = pd.DataFrame(index=df.index)
        out["season"] = raw.season
        out["source_file"] = raw.path.name
        out["raw_sha256"] = raw.sha256
        out["source_row"] = df.index + 2  # numero de ligne dans le CSV (en-tete = 1)
        for col in REQUIRED_SOURCE_COLUMNS + OPTIONAL_SOURCE_COLUMNS:
            out[col] = df[col] if col in df.columns else pd.NA
        for target, aliases in ODDS_ALIASES.items():
            source_col = next((alias for alias in aliases if alias in df.columns), None)
            out[target] = df[source_col] if source_col else pd.NA
        frames.append(out)
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


def latest_weather_file_by_team(settings: Settings) -> dict[str, Path]:
    """Fichier meteo brut le plus recent pour chaque equipe du referentiel."""
    teams: list[str] = []
    if settings.stadiums_file.exists():
        teams = pd.read_csv(settings.stadiums_file)["team"].tolist()
    all_files = list(settings.raw_weather_dir.glob("weather_*.json"))
    result: dict[str, Path] = {}
    for team in teams:
        prefix = f"weather_{team.replace(' ', '_')}_"
        matching = sorted(p for p in all_files if p.name.startswith(prefix))
        if matching:
            result[team] = matching[-1]
    return result
