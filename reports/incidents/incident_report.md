# Rapport d'incident - exercice automatise

Genere le 2026-11-07T19:28:48+00:00 par `python src/incident_drill.py`. PostgreSQL utilise : oui.

| Scenario | Attendu | Statut obtenu | Lues | Acceptees | Rejetees | Detection |
|---|---|---|---|---|---|---|
| 1_valeurs_invalides | 7 lignes rejetees avec motif, publication maintenue (PASS_WITH_WARNINGS) | **PASS_WITH_WARNINGS** | 3484 | 3477 | 7 | 7 ligne(s) rejetee(s), conservees dans data/rejected/rejected_20261107T192823Z-bee060.csv |
| 2_doublons_massifs | FAIL par quality gate, curated precedent intact | **FAIL** | 3783 | 3477 | 306 | RuntimeError: Quality gate non franchi : taux de rejet 8.1% > seuil 5% (quality gate) |
| 3_changement_schema | fichier en quarantaine, repli sur la version precedente, 3 477 lignes | **PASS_WITH_WARNINGS** | 3477 | 3477 | 0 | ligue1_2025-2026_20991231T235959Z.csv mis en quarantaine (schema_invalide: colonnes absentes ['FTHG']) -> data/rejected/quarantine/ligue1_2025-2026_20991231T235959Z.csv; Saison 2025-2026 : repli sur la version valide precedente ligue1_2025-2026_20260925T071007Z.csv |
| 4_fichier_vide | fichier en quarantaine, repli sur la version precedente | **PASS_WITH_WARNINGS** | 3477 | 3477 | 0 | ligue1_2025-2026_20991231T235959Z.csv mis en quarantaine (fichier_vide) -> data/rejected/quarantine/ligue1_2025-2026_20991231T235959Z.csv; Saison 2025-2026 : repli sur la version valide precedente ligue1_2025-2026_20260925T071007Z.csv |
| 5_source_indisponible | erreurs journalisees, repli sur la zone raw existante | **PASS_WITH_WARNINGS** | 3477 | 3477 | 0 | source indisponible pour 10 saison(s) : repli sur la derniere version raw collectee |
| 6_echec_publication | FAIL sans publication, puis reprise par simple rejeu | **FAIL** | 3477 | 3477 | 0 | OperationalError: connection failed: connection to server at "127.0.0.1", port 1 failed: Connection refused Is the server running on that host and accepting TCP/IP connections? |
| 7_rejeu_idempotent | curated identique et 0 ligne inseree au 2e passage | **PASS_WITH_WARNINGS** | 3477 | 3477 | 0 |  |

Preuves par scenario (run report, quality report, log, motifs de quarantaine, rejets) : sous-dossiers de `reports/incidents/`.

- **1_valeurs_invalides** (run `20261107T192823Z-bee060`) : {"duplicates_removed": 1, "rejection_counts": {"completude_champs_obligatoires": 1, "validite_date": 1, "validite_buts": 1, "coherence_saison": 1, "coherence_resultat": 1, "coherence_equipes": 1, "unicite_match": 1, "fraicheur": 1}}
- **2_doublons_massifs** (run `20261107T192825Z-9d5777`) : {"duplicates_removed": 306, "rejection_counts": {"unicite_match": 306, "fraicheur": 1}, "curated_unchanged": true}
- **3_changement_schema** (run `20261107T192826Z-04f469`) : {"duplicates_removed": 0, "rejection_counts": {"fraicheur": 1}}
- **4_fichier_vide** (run `20261107T192827Z-299ee3`) : {"duplicates_removed": 0, "rejection_counts": {"fraicheur": 1}}
- **5_source_indisponible** (run `20261107T192828Z-a485f1`) : {"duplicates_removed": 0, "rejection_counts": {"fraicheur": 1}}
- **6_echec_publication** (run `20261107T192840Z-1e0c00`) : {"duplicates_removed": 0, "rejection_counts": {"fraicheur": 1}, "recovery_status": "PASS_WITH_WARNINGS", "recovery_database": {"inserted": 3477, "updated": 0, "unchanged": 0, "rows_in_view": 3477}}
- **7_rejeu_idempotent** (run `20261107T192846Z-219a66`) : {"duplicates_removed": 0, "rejection_counts": {"fraicheur": 1}, "curated_sha256_run1": "c80916bd370ed6adcbe14f08b0f154968731104f8b525acf8bc5e9423d0aff61", "curated_sha256_run2": "c80916bd370ed6adcbe14f08b0f154968731104f8b525acf8bc5e9423d0aff61", "identical": true, "database_run1": {"inserted": 3477, "updated": 0, "unchanged": 0, "rows_in_view": 3477}, "database_run2": {"inserted": 0, "updated": 0, "unchanged": 3477, "rows_in_view": 3477}}
