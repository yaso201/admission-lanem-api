# NETTOYAGE-PREPROD — Purge des données de test en production — Rapport

> **Lot de données** (aucun code modifié). Vide la PROD `api-admissions.lanem.bj` de ses données de
> test avant les premiers vrais candidats. **L'opération la plus irréversible de la série.**
> Exécuté le 2026-08-19 ~01:08–01:15 WAT. Back PROD `e1c7aed` (inchangé). `online_payment_enabled=0`.

## Décisions validées (pause obligatoire)
- **Consentements immuables** (DEC art. 29) : supprimés **délibérément**, motivation écrite ci-dessous.
- **Transition Logs** : supprimés (DEC-E amendée — un journal ne survit que s'il référence par **valeur/Data**, jamais par **lien**).
- **Sessions** : colonne rouge validée nominativement = **`SES-TEST-100` seule**. Les 21 sessions de campagne conservées.

### Motivation — suppression des consentements (exigée au rapport)
> **Consentements de test, aucune personne physique concernée** (candidats fictifs créés et confirmés
> par l'utilisateur), **garde DAT-1 contournée délibérément** dans le cadre du reset pré-production —
> via `frappe.db.delete` (SQL direct). **La garde `on_trash` elle-même n'a PAS été modifiée** : elle
> protégera les vrais consentements demain.

## Filet de sécurité (avant toute suppression)
- **Export** JSON hors base + hors dépôt git : `nettoyage-preprod-export.json`, 303 064 o, **sha256
  `e9ce2148ca975933277e707da5a884d8fa129f9805ff75f100ff2b55d9b49236`** (identique PROD↔local, relu,
  complet — 17 dossiers avec pièces + frais + paiements + verdicts + consentements + journaux + 22 sessions).
- **Sauvegarde fraîche pré-purge** : `20260819_010848` (DB 2,2 Mio + config + fichiers). Set hors-site
  du jour précédent présent sur Drive (`gcrypt:daily/2026-08-18/…database.sql.gz`) ; la fraîche se
  synchronise par le cron 3 h (RESILIENCE-1A).

## Comptes avant / après (preuve)
| Type | Avant | Après |
|---|---|---|
| Admission Applicant | 17 (SOU 3, BRO 5, ACC 4, ADM 5) | **0** |
| Applicant Fee | 21 | **0** |
| Applicant Fee Payment | 20 | **0** |
| Applicant Piece (enfant) | 134 | **0** |
| Applicant Piece Verdict | 93 | **0** |
| Admission Consent Record | 64 | **0** |
| Admission Applicant Transition Log | 51 | **0** |
| Note Change Log / Transfer Log | 0 / 0 | 0 / 0 |
| Admission Session Reminder | 1 | **0** |
| **Admission Session Change Log** | 6 | **6 (conservé)** |
| **Admission Session** | 22 | **21 (SES-TEST-100 supprimée)** |

**Ordre / méthode** : `frappe.db.delete` brut (SQL direct) sur les tables de dossiers — contourne la
garde `on_trash` des consentements ET l'intégrité des liens (`LinkExistsError`), l'ordre devient sans
objet, aucun orphelin. `SES-TEST-100` : `delete_doc` (plus aucun Link ne pointait dessus). Une seule
transaction, un seul commit.

## Vérifications de sortie
| # | Preuve | Résultat |
|---|---|---|
| 3 | Zéro dossier / frais / paiement / verdict / consentement / transition log / pièce | ✅ tous 0 |
| 4 | **21 sessions de campagne intactes** (libellés + 4 dates + frais identiques avant/après) | ✅ 21/21 |
| 5 | Sessions de test supprimées selon la liste validée, pas une de plus | ✅ `SES-TEST-100` seule |
| 6 | Journaux conservés — Session Change Log | ✅ 6 (inchangé) |
| 7 | Séries de numérotation non touchées (DEC-F) | ✅ `AFF-2026-`=28, `CONS-2026-`=64 (non remises à 0) |
| 8 | Redis purgé des résidus OTP + jetons de consultation | ✅ `delete_keys("admission:")` (identity-recovery/OTP + catalogue) |
| 9 | **Les 100 F consignés** | voir ci-dessous |
| 10 | **Catalogue candidat** (`list_sessions`) sert la campagne, 0 erreur, 0 fantôme | ✅ `SES-TEST-100` absente, aucune erreur |
| 11 | Aucun code modifié · config intacte | ✅ back `e1c7aed`, `online_payment_enabled=0` |

## Trace comptable des 100 F (DEC-C)
Un paiement réel encaissé lors de PAY-TEST, **purgé avec le reste** et consigné ici :
- **Montant** : 100 F · **Transaction FedaPay** : `261100007` (réf `pay-3d984be9-a645-4296-a9e6-8de4f8196028`)
- **Dossier** : `26272010003` (SES-TEST-100) · **Frais** : `AFF-2026-00025`
- **Confirmé** : 2026-08-19 00:08:55 WAT (webhook). **Détruit** par la purge. À rapprocher/rembourser selon la politique interne.

## État final
Production **vide de tout dossier**, catalogue candidat servant les **21 sessions de campagne 2026-2027**
(Prépa S1-S5, Licences, Bachelors, Doubles Diplômes), prêtes pour les premiers vrais candidats. Journaux
de session conservés (historique des chantiers). Séries, configuration et code inchangés.

## Dette signalée
Les coordonnées de l'environnement de **recette** (IP volatile, non documentée) doivent être inscrites
au registre « Infrastructure et dépôts » du corpus, aux côtés de Contabo et des trois dépôts.
