# ADR-001 : architecture cible de Ligue1Scope

- **Statut :** accepté
- **Date :** 2026-11-07
- **Décideur :** Enzo (étudiant ESEO)
- **Remplace :** l'architecture V1 des séances 1-2 (scripts lancés à la main, trois exports curated)

## Contexte

Un analyste prépare un match de Ligue 1 et doit savoir à quel point le rapport de force (Elo), le terrain et la météo permettent d'anticiper le résultat, comparé au marché. Les données arrivent en lots : Football-Data met à jour un CSV par saison après chaque journée, et la météo historique Open-Meteo se demande à la carte. Le volume est faible (≈ 3 500 matchs sur 10 saisons, ≈ 380 par an). La consommation se fait dans Power BI Desktop.

## Exigences

1. **Rejouabilité :** pouvoir reconstruire tout le produit à partir des fichiers bruts, sans réseau.
2. **Qualité :** au moins 5 règles automatisées, les rejets sont conservés, et aucun lot dégradé n'est publié.
3. **Idempotence :** rejouer le même lot ne crée ni doublon ni dérive.
4. **Traçabilité :** chaque ligne publiée remonte à son fichier source et à son empreinte.
5. **Service BI :** des tables et vues stables, avec des contraintes d'intégrité, accessibles par le connecteur natif de Power BI.
6. **Proportionnalité :** un étudiant doit pouvoir exploiter la chaîne seul, sur un poste Windows.

## Options comparées

| Critère | A. Fichiers seuls (CSV/Parquet) | **B. Raw en fichiers + PostgreSQL + mart (retenue)** | C. Lake objet + warehouse cloud + orchestrateur (S3, BigQuery, Airflow) |
|---|---|---|---|
| Rejouabilité | oui (raw) | oui (raw horodaté + manifestes) | oui |
| Intégrité (PK, FK, CHECK) | non | oui, appliquée par le SGBD | partielle (warehouse) |
| Idempotence | réécriture complète du fichier | réécriture atomique + UPSERT sur `match_id` | MERGE |
| Accès Power BI | import de fichier | connecteur PostgreSQL, vues SQL | connecteur cloud |
| Coût et complexité | très faibles | faibles (un service Docker) | élevés : comptes, IAM, coût, maintenance |
| Adapté à ≈ 400 lignes/an | oui | oui | surdimensionné |

Pour le mode de traitement, nous avons aussi comparé **batch hebdomadaire** et **streaming**. La source ne publie qu'un fichier par saison, mis à jour après chaque journée : aucune donnée n'arrive en continu, donc le streaming n'apporterait aucune fraîcheur supplémentaire.

## Décision

Nous retenons l'**option B** en **batch**, orchestré par `src/pipeline.py`. Le pipeline est relancé chaque semaine en saison (planificateur Windows, cron, ou GitHub Actions planifié comme évolution), et à la demande hors saison.

- **Zone raw** (« lake » léger en fichiers) : copies octet pour octet, horodatées, avec manifeste SHA-256 ; quarantaine séparée pour les fichiers invalides.
- **Validation** : 11 règles, plus un contrôle de schéma, et un quality gate à 5 % de rejets.
- **Curated** : un seul jeu (`matches_curated`) en CSV pour la portabilité et en Parquet pour le typage et la compacité (≈ 239 Ko contre 1,1 Mo en CSV).
- **Warehouse léger** : PostgreSQL 16 en schéma normalisé (`season`, `team`, `football_match`, `match_weather`), plus un journal `pipeline_run`.
- **Mart** : vues `v_kpi_*` et tables `mart/kpi_*.csv`, une par question métier.

## Arguments

- Chaque composant répond à une exigence. Le raw répond à E1, les règles et le gate à E2, l'empreinte des fichiers et l'UPSERT à `row_hash` à E3, les colonnes de lignage à E4, PostgreSQL et ses vues à E5. Un seul service et un seul script couvrent E6.
- Garder le raw en fichiers plutôt qu'en base conserve la preuve exacte de ce que la source a livré, y compris quand ce n'est pas parsable.
- Un warehouse cloud ou un orchestrateur de DAG n'apporterait rien de mesurable à ce volume. Le DAG est linéaire (5 étapes) et tient dans `pipeline.py`, avec un statut et un rapport par étape.

## Conséquences positives

- On peut rejouer et prouver l'idempotence : au 2e passage, le curated est identique octet pour octet, et la base indique 0 insertion et 3 477 lignes inchangées (`reports/incidents/`).
- Une livraison invalide ne casse rien : elle part en quarantaine et le pipeline se replie sur la version précédente. Au-delà de 5 % de rejets, rien n'est publié.
- Power BI lit des vues stables, et la qualité des runs se consulte en SQL (`pipeline_run`).

## Conséquences négatives et limites

- PostgreSQL ajoute un service à démarrer. Sans `DATABASE_URL`, le pipeline publie quand même les fichiers et signale l'alerte.
- L'Elo est recalculé entièrement à chaque run. C'est négligeable à ce volume (< 1 s), mais ce ne serait plus vrai pour plusieurs ligues sur plusieurs décennies.
- L'UPSERT ne supprime jamais un match disparu de la source. C'est voulu (historique), mais une correction de clé (club renommé) laisserait l'ancienne ligne en base.
- La planification n'est pas incluse : le run hebdomadaire reste à configurer sur le poste.

## Conditions de réexamen

- Passage à plusieurs championnats ou plusieurs décennies (au-delà de ~1 M de lignes) : recalcul incrémental de l'Elo, partitionnement, orchestrateur (Airflow ou Dagster).
- Besoin de données en direct (cotes en direct, événements de match) : réévaluer une ingestion en streaming.
- Exploitation partagée ou en production : secrets dans un coffre, rôles PostgreSQL en lecture seule pour Power BI, sauvegardes.
