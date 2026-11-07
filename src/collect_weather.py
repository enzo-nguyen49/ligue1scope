"""
Collecteur - Source Open-Meteo (archive-api.open-meteo.com)

Role : pour chaque stade du referentiel config/stadiums.csv, interroger
l'API archive Open-Meteo sur toute la periode ou le club a joue a domicile
(bornes deduites des fichiers raw Ligue 1 deja collectes) et sauvegarder la
reponse brute JSON (donnees horaires ET journalieres dans le meme appel).

Pourquoi horaire ET journalier dans le meme fichier raw :
- saisons 2019-2020 a 2025-2026 : heure de coup d'envoi connue -> on pourra
  utiliser la valeur horaire au transform.
- saisons 2016-2017 a 2018-2019 : heure inconnue -> on utilisera l'agregat
  journalier (moyenne/somme sur la journee), compromis valide avec l'enseignant.
La zone raw reste donc complete ; c'est l'etape de transformation qui choisit.

Aucun secret : Open-Meteo est gratuit et sans cle pour un usage non commercial.

Usage:
    python src/collect_weather.py
    python src/collect_weather.py --team "Lyon"   # un seul stade, pour tester
"""

import argparse
import csv
import glob
import json
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

RAW_LIGUE1_DIR = Path("data/raw/ligue1")
RAW_WEATHER_DIR = Path("data/raw/weather")
STADIUMS_FILE = Path("config/stadiums.csv")
API_URL = "https://archive-api.open-meteo.com/v1/archive"
REQUEST_TIMEOUT = 30
DELAY_BETWEEN_CALLS_SECONDS = 1.0

MAX_RETRIES = 8
RETRY_BACKOFF_BASE_SECONDS = 3
RETRYABLE_STATUS_CODES = {429, 502, 503, 504}
RATE_LIMIT_BACKOFF_SECONDS = 60
HEADERS = {"User-Agent": "ligue1scope-eseo-project/1.0 (usage academique)"}

HOURLY_VARS = "temperature_2m,precipitation,wind_speed_10m"
DAILY_VARS = "temperature_2m_mean,precipitation_sum,wind_speed_10m_max"

DATE_FORMATS = ("%d/%m/%y", "%d/%m/%Y")
SEASON_FILE_RE = re.compile(r"^ligue1_(\d{4})-(\d{4})_.*\.csv$")


def parse_match_date(raw_value: str) -> datetime | None:
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(raw_value.strip(), fmt)
        except ValueError:
            continue
    return None


def load_stadiums() -> list[dict]:
    if not STADIUMS_FILE.exists():
        print(f"[ERREUR] Referentiel introuvable : {STADIUMS_FILE}", file=sys.stderr)
        sys.exit(1)
    with STADIUMS_FILE.open(encoding="utf-8") as f:
        return list(csv.DictReader(f))


def split_date_range_by_season(start_date: datetime, end_date: datetime) -> list[tuple[str, datetime, datetime]]:
    """Decoupe une historique de club en tranches de saison (Août -> Juin)."""
    ranges: list[tuple[str, datetime, datetime]] = []
    current = start_date

    while current <= end_date:
        if current.month >= 8:
            season_start = datetime(current.year, 8, 1)
            season_end = datetime(current.year + 1, 6, 30)
        else:
            season_start = datetime(current.year - 1, 8, 1)
            season_end = datetime(current.year, 6, 30)

        season_key = f"{season_start.year}-{season_end.year}"
        chunk_end = min(end_date, season_end)
        if chunk_end < current:
            current = season_start + timedelta(days=366)
            continue
        ranges.append((season_key, current, chunk_end))
        current = chunk_end + timedelta(days=1)

    return ranges


def home_date_range_per_team() -> dict[str, list[tuple[datetime, datetime]]]:
    """Retourne, par equipe, les intervalles de saisons a appeler pour la meteo."""
    csv_files = sorted(RAW_LIGUE1_DIR.glob("ligue1_*.csv"))
    if not csv_files:
        print(f"[ERREUR] Aucun fichier raw Ligue 1 trouve dans {RAW_LIGUE1_DIR}. "
              f"Lancez d'abord collect_ligue1.py.", file=sys.stderr)
        sys.exit(1)

    ranges: dict[str, list[tuple[datetime, datetime]]] = {}
    for csv_file in csv_files:
        match = SEASON_FILE_RE.match(csv_file.name)
        if not match:
            continue
        start_year = int(match.group(1))
        end_year = int(match.group(2))
        season_start = datetime(start_year, 8, 1)
        season_end = datetime(end_year, 6, 30)

        with csv_file.open(encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            found = set()
            for row in reader:
                home_team = (row.get("HomeTeam") or "").strip()
                if not home_team:
                    continue
                found.add(home_team)

        for team in sorted(found):
            ranges.setdefault(team, []).append((season_start, season_end))

    return ranges


def merge_weather_payloads(payloads: list[dict]) -> dict:
    merged: dict = {"hourly": {"time": [], "temperature_2m": [], "precipitation": [], "wind_speed_10m": []},
                    "daily": {"time": [], "temperature_2m_mean": [], "precipitation_sum": [], "wind_speed_10m_max": []}}

    for payload in payloads:
        for section in ("hourly", "daily"):
            source = payload.get(section) or {}
            for key, values in source.items():
                if key not in merged[section]:
                    merged[section][key] = []
                merged[section][key].extend(values)

    return {k: v for k, v in merged.items() if v}


def collect_one_stadium(team: str, latitude: str, longitude: str,
                         start_date: str, end_date: str, stamp: str) -> dict:
    params = {
        "latitude": latitude,
        "longitude": longitude,
        "start_date": start_date,
        "end_date": end_date,
        "hourly": HOURLY_VARS,
        "daily": DAILY_VARS,
        "timezone": "Europe/Paris",
    }
    result = {"team": team, "params": params, "status": None, "raw_file": None, "error": None, "attempts": 0, "payload": None}

    response = None
    for attempt in range(1, MAX_RETRIES + 1):
        result["attempts"] = attempt
        try:
            response = requests.get(API_URL, params=params, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        except requests.RequestException as exc:
            result["status"] = "network_error"
            result["error"] = str(exc)
            print(f"[ERREUR] Reseau pour {team} (essai {attempt}/{MAX_RETRIES}) : {exc}", file=sys.stderr)
            response = None
        else:
            if response.status_code == 200:
                break
            if response.status_code in RETRYABLE_STATUS_CODES:
                result["status"] = f"http_{response.status_code}"
                result["error"] = f"HTTP {response.status_code} (erreur serveur transitoire)"
                print(
                    f"[AVERTISSEMENT] {team} -> HTTP {response.status_code} "
                    f"(essai {attempt}/{MAX_RETRIES}), nouvelle tentative...",
                    file=sys.stderr,
                )
            else:
                result["status"] = f"http_{response.status_code}"
                result["error"] = response.text[:300]
                print(
                    f"[ERREUR] {team} -> HTTP {response.status_code} (pas de nouvelle tentative) : "
                    f"{result['error']}",
                    file=sys.stderr,
                )
                return result

        if attempt < MAX_RETRIES:
            wait_seconds = RETRY_BACKOFF_BASE_SECONDS * (2 ** (attempt - 1))
            if response is not None and response.status_code == 429:
                retry_after = response.headers.get("Retry-After")
                if retry_after is not None:
                    try:
                        wait_seconds = max(int(float(retry_after)), 1)
                    except (TypeError, ValueError):
                        wait_seconds = RATE_LIMIT_BACKOFF_SECONDS
                else:
                    wait_seconds = RATE_LIMIT_BACKOFF_SECONDS
            time.sleep(wait_seconds)

    if response is None or response.status_code != 200:
        print(f"[ERREUR] {team} -> echec definitif apres {MAX_RETRIES} tentatives", file=sys.stderr)
        return result

    payload = response.json()
    safe_name = team.replace(" ", "_")
    dest_path = RAW_WEATHER_DIR / f"weather_{safe_name}_{stamp}.json"
    dest_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    result["status"] = "ok"
    result["payload"] = payload
    result["raw_file"] = str(dest_path)
    print(f"[OK] {team} ({start_date} -> {end_date}) -> {dest_path}")
    return result


def collect(single_team: str | None = None) -> list[dict]:
    RAW_WEATHER_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    stadiums = load_stadiums()
    ranges = home_date_range_per_team()

    if single_team:
        stadiums = [s for s in stadiums if s["team"] == single_team]
        if not stadiums:
            print(f"[ERREUR] '{single_team}' absent de config/stadiums.csv", file=sys.stderr)
            sys.exit(1)

    results = []
    for stadium in stadiums:
        team = stadium["team"]
        season_ranges = ranges.get(team)
        if not season_ranges:
            print(f"[AVERTISSEMENT] Aucune saison de match a domicile trouvee pour {team}, ignore.",
                  file=sys.stderr)
            continue

        payloads: list[dict] = []
        for season_start, season_end in season_ranges:
            start_date = season_start.strftime("%Y-%m-%d")
            end_date = season_end.strftime("%Y-%m-%d")
            result = collect_one_stadium(
                team=team,
                latitude=stadium["latitude"],
                longitude=stadium["longitude"],
                start_date=start_date,
                end_date=end_date,
                stamp=stamp,
            )
            results.append(result)
            if result["status"] == "ok" and result["payload"]:
                payloads.append(result["payload"])
            time.sleep(DELAY_BETWEEN_CALLS_SECONDS)

        if payloads:
            merged = merge_weather_payloads(payloads)
            safe_name = team.replace(" ", "_")
            merged_path = RAW_WEATHER_DIR / f"weather_{safe_name}_{stamp}.json"
            merged_path.write_text(json.dumps(merged, ensure_ascii=False), encoding="utf-8")
            print(f"[OK] {team} (saisons agregées) -> {merged_path}")

    ok_count = sum(1 for r in results if r["status"] == "ok")
    print(f"\n{ok_count}/{len(results)} appels meteo collectes avec succes.")
    return results


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Collecteur Open-Meteo (historique par stade)")
    parser.add_argument("--team", type=str, default=None, help="Nom d'un seul club (pour tester un stade)")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    collect(single_team=args.team)
