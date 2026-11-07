# Hypothèses d'analyse

## Question de recherche

**Dans quelle mesure la force relative des équipes et le contexte du match sont-ils associés au résultat et au nombre de buts en Ligue 1 ?**

Le projet analyse des associations observées sur les matchs. Il ne permet pas, à lui seul, d'affirmer qu'un facteur cause un résultat : les variables disponibles ne contrôlent notamment pas les blessures, compositions, fatigue, qualité des joueurs ou stratégie.

## Hypothèses testables

### H1 — Avantage Elo et résultat

- **Hypothèse nulle H0** : l'écart Elo pré-match entre domicile et extérieur n'est pas associé à la probabilité de victoire à domicile.
- **Hypothèse H1** : plus `elo_diff` est élevé, plus la fréquence de victoire à domicile augmente.
- **Mesures** : `elo_diff`, `result`; taux H/D/A par classes d'écart Elo et, si possible, modèle multinomial ou logistique.
- **Précaution** : l'Elo est calculé uniquement avec les résultats depuis 2016 et initialise chaque équipe à 1500 ; il n'intègre pas la force antérieure à la période.

### H2 — Avantage du terrain

- **H0** : la répartition H/D/A ne diffère pas de manière substantielle selon l'équipe qui reçoit.
- **H1** : les victoires à domicile sont plus fréquentes que les victoires à l'extérieur.
- **Mesures** : `result`, buts domicile/extérieur, saison ; calculer les fréquences et leur évolution par saison.
- **Précaution** : ici, chaque match a une équipe à domicile ; la comparaison porte sur le résultat domicile/extérieur, pas sur des observations appariées d'une même équipe dans les deux rôles au même moment.

### H3 — Météo et buts

- **H0** : température, précipitations et vent ne sont pas associés au total de buts (`home_goals + away_goals`).
- **H1** : au moins une variable météo est associée au total de buts.
- **Mesures** : variables météo, buts totaux, `result` ; analyser d'abord uniquement les valeurs météo observées.
- **Précaution** : 518 matchs sur 3 477 (14,9 %) n'avaient pas de météo dans l'instantané audité en octobre ; couverture portée à 100 % le 07/11/2026. L'absence peut varier selon équipe/saison et ne doit pas être supposée aléatoire. Ne pas confondre corrélation et causalité.

### H4 — Évolution des performances par saison

- **H0** : les indicateurs de buts et de résultats ne varient pas entre saisons.
- **H1** : les indicateurs diffèrent selon la saison.
- **Mesures** : matchs par saison, buts moyens, taux H/D/A et différence de buts ; calculer les taux avec un dénominateur explicite.
- **Précaution** : les saisons n'ont pas toutes le même nombre de matchs (380, 306, et 279 en 2019-2020 dans le fichier). Comparer des moyennes/taux plutôt que des totaux bruts, et expliquer les formats/calendriers à partir de sources officielles.

### H5 — Stabilité Elo et résultats

- **H0** : l'évolution du Elo pré-match d'un club n'est pas associée à ses résultats ultérieurs.
- **H1** : les périodes de hausse du Elo sont associées à de meilleurs résultats ultérieurs.
- **Mesures** : séries temporelles par équipe, différences de Elo et résultats futurs dans l'ordre chronologique.
- **Précaution** : éviter la fuite de cible : une mesure calculée après le match ne peut pas expliquer le résultat du même match. Les colonnes `home_elo` et `away_elo` représentent le Elo avant le match.

## Hypothèses à ne pas prétendre tester avec le dataset actuel

- **Cotes et valeur de pari** : aucune cote de marché ni probabilité implicite n'est présente ; on ne peut pas conclure qu'un pari est rentable.
- **Forme glissante fiable** : une forme des cinq derniers matchs nécessite un calcul chronologique par équipe incluant domicile et extérieur ; elle ne doit pas être créée par une agrégation non ordonnée dans Power BI.
- **Qualité tactique ou individuelle** : compositions, joueurs, tirs, xG, blessures et entraîneurs ne sont pas dans le dataset curé actuel.

## Protocole minimal

1. Définir la population et la période analysées.
2. Vérifier le taux de couverture et la qualité des champs utilisés.
3. Formuler H0/H1 avant de regarder les résultats.
4. Contrôler les dénominateurs et les effectifs par groupe.
5. Présenter tailles d'effet et incertitude, pas seulement un graphique ou une corrélation.
6. Séparer résultats descriptifs, associations statistiques et interprétation causale.
