"""
Calcul interne du rating Elo pre-match (remplace ClubElo, indisponible en
HTTP 502 depuis le 25/09/2026, cf. config/data_contract.yaml).

Methode World Football Elo, appliquee dans l'ordre chronologique :
- note initiale `initial_rating` a la premiere apparition d'une equipe ;
- avantage du terrain `home_advantage` ajoute au club recevant pour le
  score attendu seulement (il ne modifie pas la note stockee) ;
- facteur multiplicateur selon l'ecart de buts ;
- chaque ligne recoit la note AVANT le match : aucune fuite du resultat.

Le calcul est deterministe (tri stable date puis ligne source) : memes
entrees => memes notes, ce qui rend le rejeu idempotent.
"""

from __future__ import annotations

import pandas as pd


def goal_diff_multiplier(goal_diff: int) -> float:
    goal_diff = abs(goal_diff)
    if goal_diff <= 1:
        return 1.0
    if goal_diff == 2:
        return 1.5
    return (11 + goal_diff) / 8.0


def expected_home_score(home_elo: float, away_elo: float, home_advantage: float) -> float:
    return 1.0 / (1.0 + 10 ** ((away_elo - (home_elo + home_advantage)) / 400.0))


def compute_elo_table(
    matches: pd.DataFrame,
    initial_rating: float = 1500.0,
    home_advantage: float = 100.0,
    k_factor: float = 20.0,
) -> pd.DataFrame:
    """Ajoute home_elo, away_elo, elo_diff et elo_home_win_expectancy (pre-match)."""
    sort_cols = [c for c in ("match_date", "season", "source_row") if c in matches.columns]
    matches = matches.sort_values(sort_cols, kind="stable").reset_index(drop=True)

    ratings: dict[str, float] = {}
    home_pre, away_pre, expectancy = [], [], []

    for row in matches.itertuples(index=False):
        home_before = ratings.get(row.HomeTeam, initial_rating)
        away_before = ratings.get(row.AwayTeam, initial_rating)
        expected_home = expected_home_score(home_before, away_before, home_advantage)
        home_pre.append(home_before)
        away_pre.append(away_before)
        expectancy.append(expected_home)

        goal_diff = int(row.home_goals) - int(row.away_goals)
        actual_home = 1.0 if goal_diff > 0 else (0.0 if goal_diff < 0 else 0.5)
        delta = k_factor * goal_diff_multiplier(goal_diff) * (actual_home - expected_home)
        ratings[row.HomeTeam] = home_before + delta
        ratings[row.AwayTeam] = away_before - delta

    result = matches.copy()
    result["home_elo"] = home_pre
    result["away_elo"] = away_pre
    result["elo_diff"] = result["home_elo"] - result["away_elo"]
    result["elo_home_win_expectancy"] = expectancy
    return result
