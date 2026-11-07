# ADR-002 : persistance relationnelle avec PostgreSQL et chargement par UPSERT

- **Statut :** accepté. La v1 (2026-10-12) utilisait un remplacement complet ; la v2 (2026-11-07) passe à l'UPSERT.
- **Décideur :** Enzo (étudiant ESEO). Le passage de SQLite à PostgreSQL a été validé avec l'enseignant.

## Contexte

Le curated doit être servi à Power BI. Le besoin pédagogique porte sur l'architecture de base de données : il faut montrer des clés primaires et étrangères, des contraintes métier, l'unicité et un accès BI. En v1, le chargeur vidait les tables puis les rechargeait dans une transaction. C'était idempotent, mais le chargeur ne savait pas dire ce qui avait changé, et il effaçait tout historique de mise à jour.

## Options

| Option | Avantages | Inconvénients |
|---|---|---|
| CSV seuls | portable, aucun service | aucune contrainte, pas de vue partagée |
| SQLite | aucun service | moins représentatif d'un serveur, accès Power BI moins direct (pilote ODBC) |
| PostgreSQL, remplacement complet (v1) | simple, transactionnel | ne compte pas ce qui change ; tout est réécrit à chaque run |
| **PostgreSQL, UPSERT sur `match_id` (v2, retenue)** | idempotent, compte les insertions, mises à jour et lignes inchangées, migre une base v1 sans perte | logique un peu plus riche ; ne supprime pas les lignes disparues de la source |

## Décision

PostgreSQL 16 est la base de service. Le schéma est dans `sql/schema.sql` ; il est rejouable (`IF NOT EXISTS`, `ADD COLUMN IF NOT EXISTS`) et migre donc une base v1.

Le chargement (`src/publish.py`) se fait en **une seule transaction** :

1. `INSERT … ON CONFLICT DO NOTHING` pour `season` et `team` ;
2. `INSERT … ON CONFLICT (match_id) DO UPDATE … WHERE football_match.row_hash IS DISTINCT FROM EXCLUDED.row_hash` pour les matchs. `RETURNING (xmax = 0)` distingue une insertion d'une mise à jour ;
3. un UPSERT de `match_weather` pour les lignes insérées ou modifiées ;
4. un contrôle de cardinalité de `v_match_analysis`, puis une ligne dans `pipeline_run`.

Si une erreur survient (contrainte CHECK, connexion), tout est annulé (ROLLBACK) et la base reste dans son état précédent.

## Conséquences

**Positives.** Au rejeu, la base indique 0 insertion, 0 mise à jour et 3 477 lignes inchangées. Si la source corrige un match, seule cette ligne est mise à jour. Les contraintes restent appliquées par le SGBD. Les tests d'intégration (`tests/test_postgres.py`) tournent dans des schémas temporaires, en local comme en CI.

**Négatives.** Il faut démarrer un service (Docker Compose fourni). Les identifiants de `compose.yaml` sont réservés au développement local. Un match retiré de la source reste en base : à traiter par une purge explicite si le besoin apparaît.

## Preuves

`sql/schema.sql`, `src/publish.py`, `tests/test_postgres.py`, `reports/incidents/7_rejeu_idempotent/`, et la table `pipeline_run`.
