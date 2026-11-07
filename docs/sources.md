# Sources de données

## Fiches comparatives

| Critère | A. Football-Data.co.uk (retenue) | B. ClubElo API (écartée) | C. Open-Meteo Archive (retenue, enrichissement) |
|---|---|---|---|
| Producteur et URL | football-data.co.uk, `mmz4281/{code}/F1.csv` | api.clubelo.com | archive-api.open-meteo.com/v1/archive |
| Mode d'accès | HTTP GET public, sans clé | HTTP GET public | API REST, sans clé |
| Format | CSV par saison | CSV | JSON |
| Fréquence de mise à jour | après chaque journée | quotidienne | historique (à la demande) |
| Clé / identifiant | (saison, date, domicile, extérieur) | (club, date) | (lat, lon, heure) |
| Licence / conditions | fichiers gratuits, disclaimer, citer la source | non vérifiée | CC BY 4.0, usage non commercial < 10 000 appels/jour |
| Risque technique | renommage de colonnes entre saisons | **HTTP 502 persistant (25/09/2026)** | quotas (HTTP 429), réanalyse sur grille et non mesure au stade |
| Données personnelles | aucune | aucune | aucune |


## Football-Data.co.uk — résultats et statistiques de matchs

- Page France et fichiers par saison : <https://www.football-data.co.uk/francem.php>
- Format des CSV Ligue 1 : fichier `F1.csv`, rangé dans l'URL saison de type `https://www.football-data.co.uk/mmz4281/2526/F1.csv` (exemple 2025-2026 ; le code de saison change selon l'exercice).
- Données utilisées : date, heure lorsqu'elle existe, équipes, scores finaux, code résultat et **cotes moyennes avant-match** (`AvgH/AvgD/AvgA`, nommées `BbAvH/BbAvD/BbAvA` jusqu'en 2018-2019). Les autres colonnes (statistiques de tirs, autres bookmakers, marchés secondaires) restent dans la zone raw mais sont hors périmètre (minimisation).
- Collecte du projet : `src/collect.py` télécharge `F1.csv` par saison (mode `url`) ou copie des fichiers déjà téléchargés (mode `local`), sans transformation, vers `data/raw/ligue1/`. Un manifeste stocke l'origine, la date, la taille et le SHA-256 ; un fichier identique au précédent n'est pas réécrit.
- Fréquence : mise à jour après chaque journée en saison ; fichiers figés une fois la saison terminée. Risque : changement de nom de colonne (déjà observé : `BbAv*` → `Avg*`), traité par alias et par contrôle de schéma avec quarantaine.
- Données personnelles : aucune (clubs et scores publics).
- La source annonce que ses CSV sont gratuits et renvoie vers `notes.txt` pour le dictionnaire/source des champs et vers sa page de disclaimer. « Gratuit » ne signifie pas nécessairement licence ouverte ou droit de redistribution : consulter les conditions et citer le fournisseur avant de republier les fichiers.
- Notes des champs : <https://www.football-data.co.uk/notes.txt>
- Disclaimer : <https://www.football-data.co.uk/disclaimer.php>

## Open-Meteo Historical Weather API — météo historique

- Documentation officielle : <https://open-meteo.com/en/docs/historical-weather-api>
- Endpoint utilisé : `https://archive-api.open-meteo.com/v1/archive`
- Variables demandées par `src/collect_weather.py` : température `temperature_2m`, précipitation `precipitation`, vent `wind_speed_10m` à l'heure ; et agrégats journaliers `temperature_2m_mean`, `precipitation_sum`, `wind_speed_10m_max`.
- Coordonnées : latitude/longitude du stade depuis `config/stadiums.csv`, fuseau demandé `Europe/Paris`. Les coordonnées sont des entrées de projet et restent marquées `verified=false` : les contrôler avant d'interpréter finement la météo d'un stade.
- Granularité : la documentation indique une réanalyse sur grille spatiale ; le point météo peut ne pas être exactement le stade. Les agrégats journaliers utilisés en l'absence d'heure ne sont pas équivalents à une mesure horaire au coup d'envoi.
- Accès : la page officielle indique un accès non commercial sous le seuil de 10 000 appels quotidiens ; vérifier les conditions en vigueur et appliquer les limites d'appel. Le collecteur découpe les requêtes par saison et traite les 429 avec temporisation.
- Citation recommandée par Open-Meteo : Zippenfenig, P. (2023), *Open-Meteo.com Weather API*, <https://doi.org/10.5281/ZENODO.7970649>.

## Elo — donnée calculée localement

L'Elo final provient de `src/compute_elo.py`, pas d'une API externe. ClubElo (api.clubelo.com) avait été envisagé en source B mais répondait HTTP 502 de façon persistante le 25/09/2026 ; il a été abandonné (voir le changelog du contrat). Il est recalculé chronologiquement à chaque exécution avec initialisation à 1500 pour chaque équipe à sa première apparition, avantage domicile de 100 points dans le score attendu et facteur K de 20. Limite : absence d'historique pré-2016. Les valeurs stockées dans le dataset sont pré-match.

## Cartographie des équipes et stades

`config/stadiums.csv` est un référentiel maintenu pour le projet ; les entrées apparaissent actuellement avec `verified=false`. La météo de Marseille, Nice et Strasbourg, d'abord manquante (quotas), a été collectée le 07/11/2026 : la couverture est de 100 %. Les noms de clubs et coordonnées doivent être vérifiés et leur source enregistrée avant de présenter la météo comme fiable par stade.

## Date de consultation

Pages officielles consultées le 12 octobre 2026 ; données Football-Data collectées le 25 septembre 2026 (voir les manifestes de `data/raw/ligue1/`). Les pages et conditions peuvent évoluer ; le rendu final doit conserver la date de consultation.
