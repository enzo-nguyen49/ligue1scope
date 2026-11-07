# Dictionnaire de données (contrat v2.0)

**Produit principal :** `data/curated/matches_curated.csv` et `.parquet`, plus la vue PostgreSQL `v_match_analysis`.
**Grain :** une ligne = un match de Ligue 1 disputé (saison, date, domicile, extérieur).
**Clé :** `match_id`. Codes de résultat : `H` = victoire à domicile, `D` = nul, `A` = victoire à l'extérieur.

## Identification et résultat

| Champ | Type | Oblig. | Domaine / unité | Description | Exemple |
|---|---|---|---|---|---|
| `match_id` | texte | oui | unique | `<saison>_<AAAAMMJJ>_<domicile>_<extérieur>`, déterministe | `2025-2026_20250815_Rennes_Marseille` |
| `season` | texte | oui | `AAAA-AAAA` | saison, déduite du fichier raw | `2025-2026` |
| `match_date` | date | oui | ISO 8601 | date du match (source `Date`, JJ/MM/AA ou JJ/MM/AAAA) | `2025-08-15` |
| `kickoff_time` | heure | non | `HH:MM`, heure de Paris | coup d'envoi ; absent de la source avant 2019-2020 | `19:45` |
| `home_team`, `away_team` | texte | oui | nom Football-Data | clubs recevant et visiteur (différents) | `Rennes` |
| `home_goals`, `away_goals` | entier | oui | 0–15 | buts en fin de match | `1` |
| `total_goals` | entier | oui | ≥ 0 | `home_goals + away_goals` | `1` |
| `result` | texte | oui | H, D, A | résultat final, cohérent avec le score | `H` |

## Force Elo (calculée, avant le match)

| Champ | Type | Description |
|---|---|---|
| `home_elo`, `away_elo` | réel | note Elo de chaque club **avant** le match (1 500 à la première apparition, K = 20, multiplicateur selon l'écart de buts) |
| `elo_diff` | réel | `home_elo - away_elo`, sans avantage du terrain |
| `elo_home_win_expectancy` | réel 0–1 | score attendu du club recevant, avec +100 points d'avantage du terrain |
| `elo_favorite` | H / A | `H` si l'espérance ≥ 0,5, sinon `A` |
| `elo_favorite_won` | booléen | le favori Elo a gagné (un nul compte comme un échec) |
| `elo_warmup` | booléen | saison de chauffe (2016-2017) : Elo pas encore stabilisé, **exclue des KPI** |

## Marché (cotes moyennes avant-match)

| Champ | Type | Description |
|---|---|---|
| `odds_home_avg`, `odds_draw_avg`, `odds_away_avg` | réel | cote décimale moyenne des bookmakers (`AvgH/D/A`, ou `BbAvH/D/A` avant 2019-2020) ; NULL si hors [1,01 ; 100] |
| `odds_valid` | booléen | les trois cotes sont présentes et valides (règle R8) |
| `market_prob_home`, `_draw`, `_away` | réel 0–1 | probabilités implicites `(1/cote) / Σ(1/cotes)`, marge du bookmaker retirée |
| `market_favorite` | H / D / A | issue la plus probable selon le marché |
| `market_favorite_won` | booléen | le favori du marché a gagné |

## Météo (Open-Meteo, réanalyse sur grille autour du stade)

| Champ | Type | Unité | Description |
|---|---|---|---|
| `weather_temp_c` | réel | °C | température horaire à l'heure du coup d'envoi arrondie, sinon moyenne du jour |
| `weather_precip_mm` | réel | mm | précipitation de l'heure, sinon **cumul du jour** (non équivalents) |
| `weather_wind_kph` | réel | km/h | vent de l'heure, sinon **maximum du jour** |
| `weather_granularity` | texte | `hourly`, `daily`, `none` | granularité réellement utilisée |
| `weather_missing` | booléen | — | météo absente (stade non collecté) ; 0 ligne au 07/11/2026 |

## Qualité et lignage

| Champ | Type | Description |
|---|---|---|
| `kickoff_time_missing` | booléen | heure absente de la source (jamais imputée) |
| `data_quality_status` | texte | `complete` si heure, météo et cotes sont présentes, sinon `partial` |
| `source_file` | texte | fichier raw d'origine (`ligue1_<saison>_<horodatage>.csv`) |
| `raw_sha256` | texte | empreinte SHA-256 du fichier raw d'origine |
| `source_row` | entier | numéro de ligne dans le fichier raw (en-tête = 1) |

Dans PostgreSQL, `football_match` ajoute `row_hash` (empreinte du contenu, sert à l'UPSERT), `last_run_id` et `updated_at`. `v_match_analysis` ajoute aussi les classes `elo_band`, `temperature_class` et `precipitation_class`.

## Fichier de rejets (`data/rejected/rejected_<run_id>.csv`)

Ce fichier reprend les colonnes raw telles que reçues, en texte. Il ajoute `match_id`, `rejection_reasons` (identifiants des règles séparés par `;`), `run_id` et `rejected_at`.

## Mart (`data/curated/mart/`, mêmes calculs que les vues `v_kpi_*`)

| Table | Grain | Mesures |
|---|---|---|
| `kpi_favori_par_saison` | saison | `elo_favorite_win_rate`, `market_favorite_win_rate`, `elo_market_agreement_rate`, `home_win_rate`, `draw_rate` |
| `kpi_tranche_elo` | tranche de `elo_diff` | `matches`, `home_win_rate`, `draw_rate`, `away_win_rate`, `avg_goals`, `avg_goal_diff` |
| `kpi_meteo_temperature` | classe de température | idem |
| `kpi_meteo_precipitations` | classe de précipitations | idem |

## Modèle relationnel (MLD)

- `season(season_id PK)`
- `team(team_id PK, team_name UNIQUE)`
- `football_match(match_id PK, season_id FK, home_team_id FK, away_team_id FK, …, UNIQUE(season_id, match_date, home_team_id, away_team_id))`, avec des CHECK sur les buts, le résultat, `home ≠ away` et la cohérence de `elo_diff`
- `match_weather(match_id PK/FK → football_match ON DELETE CASCADE, …)` : relation 0..1 qui garde la météo séparée des faits de match
- `pipeline_run(run_id PK, status, compteurs, …)` : journal des exécutions

DDL complet : [`sql/schema.sql`](../sql/schema.sql).
