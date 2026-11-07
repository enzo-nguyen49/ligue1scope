# Soutenance : déroulé de 6 minutes et réponses préparées

Avant de commencer :

- ouvrir un terminal à la racine du projet, avec le venv activé et `DATABASE_URL` défini ;
- démarrer PostgreSQL (`docker compose up -d`) ;
- ouvrir Power BI sur la page « Vue d'ensemble » ;
- garder `docs/architecture/architecture_v2.png` ouvert.

## Déroulé (8 points de démonstration du guide)

| Temps | Point du guide | À montrer | À dire |
|---|---|---|---|
| 0:00 – 0:40 | 1. Question et utilisateur | README §1 | « Un analyste qui prépare un match veut savoir si le favori l'est vraiment. Question : à quel point l'écart Elo, le terrain et la météo anticipent le résultat, comparé aux bookmakers. » |
| 0:40 – 1:20 | 2. Source et raw | `data/raw/ligue1/` + un manifeste | « Football-Data, un CSV par saison, copié octet pour octet, horodaté, avec son SHA-256. Open-Meteo en enrichissement. ClubElo écarté : 502 persistant, d'où un Elo calculé en interne. » |
| 1:20 – 2:00 | Architecture | `architecture_v2.png` | « Batch hebdomadaire, raw rejouable, 11 règles et un quality gate, curated en CSV/Parquet, PostgreSQL en UPSERT, Power BI sur des vues. Pas de streaming : la source ne publie qu'après chaque journée. » |
| 2:00 – 2:50 | 3. Exécution complète | `python src/pipeline.py` | Lire les logs : run_id, 3 477 lues, 0 rejet, `PASS_WITH_WARNINGS` ; expliquer l'alerte de fraîcheur (saison 2026-2027 non collectée). |
| 2:50 – 3:40 | 4. Donnée invalide et rejet | `reports/incidents/incident_report.md` puis `data/rejected/sample_rejected.csv` | « Score 42, FTR contradictoire, 31 février, club vide, Rennes-Rennes, date hors saison, doublon : 7 rejets, chacun avec son motif. Le pipeline publie quand même, car on est sous le seuil de 5 %. Une saison livrée en double passe à 8 % : FAIL, rien n'est publié. » |
| 3:40 – 4:20 | 5. Curated | `matches_curated.csv`, `v_match_analysis` | Colonnes Elo pré-match, probabilités du marché, météo, lignage `source_file` / `raw_sha256`. |
| 4:20 – 4:50 | 6. Rapport d'exécution | `reports/run_report.json` | Statut, durées par étape, compteurs, fraîcheur, KPI, statistiques de la base. |
| 4:50 – 5:20 | 7. Rejeu sans duplication | relancer `python src/pipeline.py` | « 0 insertion, 0 mise à jour, 3 477 lignes inchangées ; même SHA-256 du curated. » Montrer `SELECT * FROM pipeline_run;`. |
| 5:20 – 6:00 | Résultat + 8. Limite | Power BI, page Elo | « Victoire à domicile : de 17 % à 72 % selon l'écart Elo. Le favori Elo gagne 50,8 % des matchs, celui du marché 52,2 % : l'Elo seul est presque aussi bon. La météo pèse peu. Limite : la météo est une réanalyse sur grille, journalière avant 2019-2020 ; elle avait d'abord manqué pour 3 clubs (quotas), un trou détecté par la couverture puis comblé. Amélioration : planification hebdomadaire et Elo avec historique antérieur à 2016. » |

Plan B, si PostgreSQL ne démarre pas : `python src/pipeline.py --no-db`. Le statut reste `PASS_WITH_WARNINGS` et les fichiers curated et le mart sont produits. Montrer ensuite le scénario 7 du rapport d'incident pour les chiffres de la base.

## Questions du guide et réponses courtes

- **Quelle décision ?** Doser le discours d'avant-match (favori réel ou piège) et choisir les facteurs de contexte à suivre : l'Elo et le terrain, oui ; la météo, marginalement.
- **Pourquoi cette source ?** C'est la seule source ouverte, sans clé, qui réunit 10 saisons de résultats, l'heure et les cotes dans un format stable. ClubElo était indisponible ; le scraping de sites officiels a été écarté (conditions d'utilisation).
- **Clé métier ?** (saison, date, domicile, extérieur), d'où `match_id`. Elle est vérifiée par R7 et par `UNIQUE` en base.
- **Règle qui rejette le plus ?** Sur les données réelles, aucune (0 rejet) : la source est propre. Sur l'exercice d'incident, c'est l'unicité (306 doublons). Ce qui déclenche le plus souvent une alerte, c'est la fraîcheur.
- **Source indisponible ?** La collecte réessaie avec backoff, journalise l'erreur et le pipeline se replie sur le dernier raw (scénario 5). Si aucune version n'existe, le run est `FAIL`.
- **Schéma modifié ?** Le contrôle SCHEMA met le fichier en quarantaine avec son motif, et le pipeline repart de la version précédente (scénario 3). Le renommage connu `BbAv*` → `Avg*` est géré par des alias.
- **Comment savoir qu'on n'a pas traité deux fois ?** Trois preuves : l'empreinte SHA-256 à la collecte, le curated identique octet pour octet, et l'UPSERT conditionné par `row_hash`, avec des compteurs dans `pipeline_run`.
- **Pour passer en production ?** Un planificateur (cron ou Actions), des secrets dans un coffre, un rôle PostgreSQL en lecture seule pour Power BI, des sauvegardes, une alerte par e-mail sur `FAIL`, et le recalcul incrémental de l'Elo si le volume grossit.
- **Owner et rétention ?** L'étudiant est owner du dataset. Raw et quarantaine sont conservés intégralement ; les rejets un an ; les rapports de run sont historisés (contrat, section `retention`).
