"""
Collecteur Ligue 1 - Football-Data.co.uk -> zone raw.

Deux modes, choisis par configuration ou en ligne de commande :
- url   : telechargement de https://www.football-data.co.uk/mmz4281/{code}/F1.csv
          (code = "2526" pour 2025-2026), avec timeout et nouvelles tentatives ;
- local : copie de fichiers deja telecharges (16-17.csv, 17-18.csv, ...).

Garanties :
- la reponse est ecrite octet pour octet (aucune transformation) ;
- nom horodate ligue1_<saison>_<AAAAMMJJTHHMMSSZ>.csv, ecriture atomique ;
- idempotence : si le contenu (SHA-256) est identique au dernier fichier
  raw de la saison, rien n'est ecrit (statut "unchanged") ;
- une source indisponible n'interrompt pas le pipeline : l'erreur est
  journalisee et le pipeline se replie sur la derniere version raw ;
- un manifeste JSON par execution trace source, empreinte et taille.

Usage autonome :
    python src/collect.py --mode url
    python src/collect.py --mode local --source-dir data/source_seasons
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

from common import raw_files_by_season, sha256_of_file
from settings import Settings, load_settings

log = logging.getLogger("ligue1scope")


class SourceUnavailable(RuntimeError):
    pass


def season_code(season: str) -> str:
    """'2025-2026' -> '2526' (format des URL Football-Data)."""
    start, end = season.split("-")
    return f"{start[2:]}{end[2:]}"


def local_file_name(season: str) -> str:
    """'2025-2026' -> '25-26.csv' (nommage des telechargements manuels)."""
    start, end = season.split("-")
    return f"{start[2:]}-{end[2:]}.csv"


def fetch_url(url: str, cfg: dict, session=requests) -> bytes:
    """Telecharge avec timeout et backoff ; leve SourceUnavailable sinon."""
    last_error = "inconnue"
    for attempt in range(1, int(cfg["max_retries"]) + 1):
        try:
            response = session.get(
                url,
                timeout=cfg["timeout_seconds"],
                headers={"User-Agent": cfg["user_agent"]},
            )
            if response.status_code == 200 and response.content:
                return response.content
            last_error = f"HTTP {response.status_code}"
            if response.status_code in (400, 401, 403, 404):
                break  # erreur definitive : inutile de reessayer
        except requests.RequestException as exc:
            last_error = f"{exc.__class__.__name__}: {exc}"
        if attempt < int(cfg["max_retries"]):
            wait = float(cfg["backoff_seconds"]) * attempt
            log.warning("Tentative %s/%s echouee pour %s (%s) ; nouvel essai dans %.0fs",
                        attempt, cfg["max_retries"], url, last_error, wait)
            time.sleep(wait)
    raise SourceUnavailable(f"{url} : {last_error}")


def write_atomic(target: Path, content: bytes) -> None:
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_bytes(content)
    os.replace(tmp, target)


def collect(
    settings: Settings,
    mode: str,
    source_dir: Path | None = None,
    run_id: str | None = None,
    session=requests,
) -> dict:
    """Collecte toutes les saisons configurees ; ne leve jamais pour une saison isolee."""
    cfg = settings.source
    raw_dir = settings.raw_ligue1_dir
    raw_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    existing = raw_files_by_season(raw_dir)
    source_dir = source_dir or (settings.root / cfg["local_dir"])

    entries = []
    for season in settings.seasons:
        if mode == "url":
            origin = cfg["url_template"].format(code=season_code(season))
        else:
            origin = str(source_dir / local_file_name(season))
        entry = {"season": season, "origin": origin, "mode": mode}
        try:
            if mode == "url":
                content = fetch_url(origin, cfg, session=session)
            else:
                path = Path(origin)
                if not path.exists():
                    raise SourceUnavailable(f"fichier local introuvable : {path}")
                content = path.read_bytes()
        except SourceUnavailable as exc:
            entry.update(status="source_unavailable", error=str(exc))
            log.error("Saison %s : source indisponible (%s)", season, exc)
            entries.append(entry)
            continue

        digest = hashlib.sha256(content).hexdigest()
        previous = existing.get(season, [])
        if previous and sha256_of_file(previous[0]) == digest:
            entry.update(status="unchanged", sha256=digest, raw_file=previous[0].name)
            log.info("Saison %s : contenu identique au raw existant (%s), aucune ecriture",
                     season, previous[0].name)
        else:
            target = raw_dir / f"ligue1_{season}_{stamp}.csv"
            write_atomic(target, content)
            entry.update(status="collected", sha256=digest, raw_file=target.name,
                         size_bytes=len(content))
            log.info("Saison %s : %s octets -> %s", season, len(content), target.name)
        entries.append(entry)

    manifest = {
        "run_id": run_id,
        "collected_at": stamp,
        "mode": mode,
        "entries": entries,
    }
    (raw_dir / f"manifest_{stamp}.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    summary = {
        "mode": mode,
        "collected": sum(e["status"] == "collected" for e in entries),
        "unchanged": sum(e["status"] == "unchanged" for e in entries),
        "unavailable": sum(e["status"] == "source_unavailable" for e in entries),
        "manifest": f"manifest_{stamp}.json",
        "entries": entries,
    }
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Collecteur Ligue 1 (Football-Data)")
    parser.add_argument("--mode", choices=["url", "local"], default=None)
    parser.add_argument("--source-dir", type=Path, default=None)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    settings = load_settings()
    summary = collect(settings, args.mode or settings.source["default_mode"], args.source_dir)
    print(json.dumps({k: v for k, v in summary.items() if k != "entries"}, indent=2))
    sys.exit(1 if summary["unavailable"] == len(settings.seasons) else 0)


if __name__ == "__main__":
    main()
