"""
Transformation : lignes acceptees -> dataset curated + mart de KPI.

Etapes (aucune ecriture disque ici, voir publish.py) :
1. Elo pre-match (compute_elo.py) et indicateur de saison de chauffe ;
2. probabilites implicites du marche a partir des cotes moyennes,
   normalisees pour retirer la marge du bookmaker ;
3. favori Elo / favori marche et resultat obtenu ;
4. jointure meteo Open-Meteo (horaire si l'heure est connue, sinon journaliere) ;
5. indicateurs de qualite par ligne et colonnes de lignage (source_file,
   raw_sha256, source_row) ;
6. tables du mart (KPI agreges) qui repondent a la question centrale.

Pas d'imputation : une valeur absente reste NULL et est signalee.
"""

from __future__ import annotations

import json
import logging

import numpy as np
import pandas as pd

from common import latest_weather_file_by_team
from compute_elo import compute_elo_table
from enrich_weather import TeamWeatherLookup
from settings import Settings

log = logging.getLogger("ligue1scope")

CURATED_COLUMNS = [
    "match_id", "season", "match_date", "kickoff_time", "home_team", "away_team",
    "home_goals", "away_goals", "total_goals", "result",
    "home_elo", "away_elo", "elo_diff", "elo_home_win_expectancy", "elo_favorite",
    "elo_favorite_won", "elo_warmup",
    "odds_home_avg", "odds_draw_avg", "odds_away_avg",
    "market_prob_home", "market_prob_draw", "market_prob_away",
    "market_favorite", "market_favorite_won", "odds_valid",
    "weather_temp_c", "weather_precip_mm", "weather_wind_kph", "weather_granularity",
    "kickoff_time_missing", "weather_missing", "data_quality_status",
    "source_file", "raw_sha256", "source_row",
]

ELO_BANDS = [(-np.inf, -200, "< -200"), (-200, -100, "-200 à -100"), (-100, 0, "-100 à 0"),
             (0, 100, "0 à 100"), (100, 200, "100 à 200"), (200, np.inf, "≥ 200")]
TEMP_BANDS = [(-np.inf, 5, "< 5 °C"), (5, 10, "5 à < 10 °C"), (10, 15, "10 à < 15 °C"),
              (15, 20, "15 à < 20 °C"), (20, 25, "20 à < 25 °C"), (25, np.inf, "≥ 25 °C")]
UNKNOWN = "Non renseignée"


def load_weather_lookups(settings: Settings, warnings: list[str]) -> dict[str, TeamWeatherLookup]:
    lookups = {}
    for team, path in latest_weather_file_by_team(settings).items():
        try:
            lookups[team] = TeamWeatherLookup(json.loads(path.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError) as exc:
            message = f"meteo illisible pour {team} ({path.name}) : {exc}"
            log.warning(message)
            warnings.append(message)
    return lookups


def add_weather(df: pd.DataFrame, lookups: dict[str, TeamWeatherLookup]) -> pd.DataFrame:
    values = []
    for team, date, time in zip(df["HomeTeam"], df["match_date"], df["Time"]):
        lookup = lookups.get(team)
        kickoff = time if isinstance(time, str) and time.strip() else None
        if lookup is None:
            values.append((None, None, None, "none"))
            continue
        temp, precip, wind, granularity = lookup.lookup_with_source(date.strftime("%Y-%m-%d"), kickoff)
        values.append((temp, precip, wind, granularity if temp is not None else "none"))
    weather = pd.DataFrame(values, index=df.index,
                           columns=["weather_temp_c", "weather_precip_mm", "weather_wind_kph",
                                    "weather_granularity"])
    for col in ("weather_temp_c", "weather_precip_mm", "weather_wind_kph"):
        weather[col] = pd.to_numeric(weather[col], errors="coerce")
    return pd.concat([df, weather], axis=1)


def add_market(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    inverse = 1.0 / df[["odds_home_avg", "odds_draw_avg", "odds_away_avg"]].astype(float)
    total = inverse.sum(axis=1, min_count=3)
    df["market_prob_home"] = inverse["odds_home_avg"] / total
    df["market_prob_draw"] = inverse["odds_draw_avg"] / total
    df["market_prob_away"] = inverse["odds_away_avg"] / total
    probs = df[["market_prob_home", "market_prob_draw", "market_prob_away"]]
    favorite = probs.fillna(-1).to_numpy().argmax(axis=1)
    df["market_favorite"] = np.where(probs.notna().all(axis=1), np.array(["H", "D", "A"])[favorite], None)
    df["market_favorite_won"] = np.where(df["market_favorite"].notna(), df["market_favorite"] == df["FTR"], None)
    return df


def build_curated(accepted: pd.DataFrame, settings: Settings, warnings: list[str]) -> pd.DataFrame:
    elo_cfg = settings.elo
    df = compute_elo_table(
        accepted,
        initial_rating=float(elo_cfg["initial_rating"]),
        home_advantage=float(elo_cfg["home_advantage"]),
        k_factor=float(elo_cfg["k_factor"]),
    )
    seasons = sorted(df["season"].unique())
    warmup = set(seasons[: int(elo_cfg.get("warmup_seasons", 1))])
    df["elo_warmup"] = df["season"].isin(warmup)
    df["elo_favorite"] = np.where(df["elo_home_win_expectancy"] >= 0.5, "H", "A")
    df["elo_favorite_won"] = df["elo_favorite"] == df["FTR"]

    df = add_market(df)

    lookups = load_weather_lookups(settings, warnings)
    if not lookups:
        warnings.append("aucun fichier meteo brut : colonnes meteo vides (lancer collect_weather.py)")
    df = add_weather(df, lookups)

    kickoff = df["Time"].where(df["Time"].notna(), None)
    curated = pd.DataFrame({
        "match_id": df["match_id"],
        "season": df["season"],
        "match_date": df["match_date"].dt.strftime("%Y-%m-%d"),
        "kickoff_time": kickoff,
        "home_team": df["HomeTeam"].astype(str),
        "away_team": df["AwayTeam"].astype(str),
        "home_goals": df["home_goals"].astype(int),
        "away_goals": df["away_goals"].astype(int),
        "total_goals": (df["home_goals"] + df["away_goals"]).astype(int),
        "result": df["FTR"].astype(str),
        "home_elo": df["home_elo"].round(1),
        "away_elo": df["away_elo"].round(1),
        "elo_diff": (df["home_elo"].round(1) - df["away_elo"].round(1)).round(1),
        "elo_home_win_expectancy": df["elo_home_win_expectancy"].round(4),
        "elo_favorite": df["elo_favorite"],
        "elo_favorite_won": df["elo_favorite_won"].astype(bool),
        "elo_warmup": df["elo_warmup"].astype(bool),
        "odds_home_avg": df["odds_home_avg"],
        "odds_draw_avg": df["odds_draw_avg"],
        "odds_away_avg": df["odds_away_avg"],
        "market_prob_home": df["market_prob_home"].round(4),
        "market_prob_draw": df["market_prob_draw"].round(4),
        "market_prob_away": df["market_prob_away"].round(4),
        "market_favorite": df["market_favorite"],
        "market_favorite_won": df["market_favorite_won"],
        "odds_valid": df["odds_valid"].astype(bool),
        "weather_temp_c": df["weather_temp_c"],
        "weather_precip_mm": df["weather_precip_mm"],
        "weather_wind_kph": df["weather_wind_kph"],
        "weather_granularity": df["weather_granularity"],
        "source_file": df["source_file"],
        "raw_sha256": df["raw_sha256"],
        "source_row": df["source_row"].astype(int),
    })
    curated["kickoff_time_missing"] = curated["kickoff_time"].isna()
    curated["weather_missing"] = curated[["weather_temp_c", "weather_precip_mm", "weather_wind_kph"]].isna().any(axis=1)
    complete = ~curated["kickoff_time_missing"] & ~curated["weather_missing"] & curated["odds_valid"]
    curated["data_quality_status"] = np.where(complete, "complete", "partial")

    curated = curated.sort_values(["match_date", "match_id"], kind="stable").reset_index(drop=True)
    if curated["match_id"].duplicated().any():
        raise ValueError("match_id duplique apres transformation : contrat viole")
    return curated[CURATED_COLUMNS]


# ---------------------------------------------------------------------------
# Mart : KPI agreges (hors saison de chauffe Elo)
# ---------------------------------------------------------------------------

def _band(values: pd.Series, bands) -> pd.Series:
    labels = pd.Series(UNKNOWN, index=values.index, dtype="object")
    for low, high, label in bands:
        labels[(values >= low) & (values < high)] = label
    return labels


def precipitation_class(values: pd.Series) -> pd.Series:
    labels = pd.Series(UNKNOWN, index=values.index, dtype="object")
    labels[values == 0] = "0 mm"
    labels[(values > 0) & (values <= 1)] = "> 0 à 1 mm"
    labels[(values > 1) & (values <= 5)] = "> 1 à 5 mm"
    labels[values > 5] = "> 5 mm"
    return labels


def _outcome_rates(group: pd.DataFrame) -> pd.Series:
    return pd.Series({
        "matches": len(group),
        "home_win_rate": round((group["result"] == "H").mean(), 4),
        "draw_rate": round((group["result"] == "D").mean(), 4),
        "away_win_rate": round((group["result"] == "A").mean(), 4),
        "avg_goals": round(group["total_goals"].mean(), 3),
        "avg_goal_diff": round((group["home_goals"] - group["away_goals"]).mean(), 3),
    })


def build_mart(curated: pd.DataFrame) -> dict[str, pd.DataFrame]:
    scope = curated.loc[~curated["elo_warmup"]].copy()
    with_odds = scope.loc[scope["market_favorite"].notna()]

    by_season = scope.groupby("season").apply(lambda g: pd.Series({
        "matches": len(g),
        "elo_favorite_win_rate": round(g["elo_favorite_won"].mean(), 4),
        "market_favorite_win_rate": round(g.loc[g["market_favorite"].notna(), "market_favorite_won"].astype(bool).mean(), 4),
        "elo_market_agreement_rate": round((g["elo_favorite"] == g["market_favorite"]).mean(), 4),
        "home_win_rate": round((g["result"] == "H").mean(), 4),
        "draw_rate": round((g["result"] == "D").mean(), 4),
    }), include_groups=False).reset_index()

    scope["elo_band"] = _band(scope["elo_diff"], ELO_BANDS)
    by_band = scope.groupby("elo_band").apply(_outcome_rates, include_groups=False).reindex(
        [label for _, _, label in ELO_BANDS]).dropna(how="all").reset_index()

    scope["temperature_class"] = _band(scope["weather_temp_c"], TEMP_BANDS)
    by_temp = scope.groupby("temperature_class").apply(_outcome_rates, include_groups=False).reindex(
        [label for _, _, label in TEMP_BANDS] + [UNKNOWN]).dropna(how="all").reset_index()

    scope["precipitation_class"] = precipitation_class(scope["weather_precip_mm"])
    by_rain = scope.groupby("precipitation_class").apply(_outcome_rates, include_groups=False).reindex(
        ["0 mm", "> 0 à 1 mm", "> 1 à 5 mm", "> 5 mm", UNKNOWN]).dropna(how="all").reset_index()

    for table in (by_band, by_temp, by_rain, by_season):
        table["matches"] = table["matches"].astype(int)

    return {
        "kpi_favori_par_saison": by_season,
        "kpi_tranche_elo": by_band,
        "kpi_meteo_temperature": by_temp,
        "kpi_meteo_precipitations": by_rain,
    }


def kpi_summary(curated: pd.DataFrame) -> dict:
    scope = curated.loc[~curated["elo_warmup"]]
    with_odds = scope.loc[scope["market_favorite"].notna()]
    return {
        "scope": f"{scope['season'].min()} a {scope['season'].max()} (hors saison de chauffe Elo)",
        "matches": int(len(scope)),
        "elo_favorite_win_rate": round(float(scope["elo_favorite_won"].mean()), 4),
        "market_favorite_win_rate": round(float(with_odds["market_favorite_won"].astype(bool).mean()), 4),
        "elo_market_agreement_rate": round(float((with_odds["elo_favorite"] == with_odds["market_favorite"]).mean()), 4),
        "home_win_rate": round(float((scope["result"] == "H").mean()), 4),
        "avg_goals_per_match": round(float(scope["total_goals"].mean()), 3),
        "weather_coverage_ratio": round(float((~curated["weather_missing"]).mean()), 4),
        "odds_coverage_ratio": round(float(curated["odds_valid"].mean()), 4),
    }
