# Power BI : connexion et mesures (contrat v2.0)

## Connexion

1. Lancer le pipeline avec la base : `$env:DATABASE_URL = "postgresql://…"` puis `python src/pipeline.py`.
2. Dans Power BI Desktop : **Accueil → Transformer les données → Paramètres de la source de données**, et vérifier que la source pointe vers PostgreSQL `localhost / ligue1scope`.
3. Pour une nouvelle connexion : **Obtenir les données → Base de données PostgreSQL**, puis cocher `v_match_analysis`, les quatre vues `v_kpi_*` et `pipeline_run`.
4. Cliquer sur **Actualiser** : les nouvelles colonnes (`elo_favorite_won`, `market_favorite`, `elo_band`, `elo_warmup`…) apparaissent. La vue garde son nom ; les visuels existants restent branchés.

Sans base, on peut importer `data/curated/matches_curated.csv` : il contient les mêmes colonnes, sauf les classes calculées en SQL.

## Mesures DAX (table `v_match_analysis`)

```DAX
Matchs = COUNTROWS ( v_match_analysis )

Matchs analyses = CALCULATE ( [Matchs], v_match_analysis[elo_warmup] = FALSE () )

Taux favori Elo =
DIVIDE (
    CALCULATE ( [Matchs], v_match_analysis[elo_favorite_won] = TRUE (), v_match_analysis[elo_warmup] = FALSE () ),
    [Matchs analyses]
)

Taux favori marche =
DIVIDE (
    CALCULATE ( [Matchs], v_match_analysis[market_favorite_won] = TRUE (), v_match_analysis[elo_warmup] = FALSE () ),
    CALCULATE ( [Matchs analyses], NOT ISBLANK ( v_match_analysis[market_favorite] ) )
)

Accord Elo marche =
DIVIDE (
    CALCULATE (
        [Matchs analyses],
        FILTER ( v_match_analysis, v_match_analysis[elo_favorite] = v_match_analysis[market_favorite] )
    ),
    [Matchs analyses]
)

Ecart Elo vs marche (pts) = ( [Taux favori Elo] - [Taux favori marche] ) * 100

Taux victoire domicile =
DIVIDE ( CALCULATE ( [Matchs analyses], v_match_analysis[result] = "H" ), [Matchs analyses] )

Buts moyens = AVERAGE ( v_match_analysis[total_goals] )

Couverture meteo =
DIVIDE ( CALCULATE ( [Matchs], v_match_analysis[weather_missing] = FALSE () ), [Matchs] )
```

Mettre les taux au format pourcentage (1 décimale).

## Ajustements conseillés sur les 4 pages existantes

| Page | Changement | Pourquoi |
|---|---|---|
| Vue d'ensemble | Remplacer la carte `MoyenneEloDiff` (1,62) par **Taux favori Elo** (50,8 %) et **Taux favori marché** (52,2 %) | La moyenne de l'écart Elo vaut presque 0 par construction, elle n'apprend rien. Les deux taux répondent à la question centrale |
| Elo | Filtre de page `elo_warmup = FALSE` ; axe `elo_band` de la vue (tri déjà correct par la vue `v_kpi_tranche_elo`) | Exclure la saison de chauffe 2016-2017, où tous les clubs partent à 1 500 |
| Elo | Ajouter un graphique en courbes par `season` : Taux favori Elo et Taux favori marché | Montre que l'Elo suit le marché à environ 1,4 point près |
| Météo | Actualiser : la classe « Non renseignée » disparaît (couverture 100 %) | Marseille, Nice et Strasbourg ont été collectés le 07/11 |
| Nouvelle page Qualité | Cartes depuis `pipeline_run` : dernier `status`, `rejected_rows`, `duplicates_removed`, `latest_match_date` ; courbe des runs | Montre l'observabilité pendant la soutenance |

## Valeurs de contrôle (run du 07/11/2026, hors 2016-2017)

Si Power BI affiche d'autres valeurs, un filtre est manquant.

- Matchs analysés : 3 097
- Taux favori Elo : 50,8 %
- Taux favori marché : 52,2 %
- Accord Elo-marché : 89,4 %
- Taux de victoire à domicile : 43,3 %
- Victoire à domicile par tranche Elo : 16,8 % · 26,9 % · 39,6 % · 45,4 % · 59,6 % · 71,8 %

