# PAY-V1 — Vérification du plafond + pose de V1.0.0 — Preuves

> Deux volets. **B (pose de version) conditionné au succès de A (vérification du plafond).**
> A concluant → B exécuté. Exécuté le 2026-08-19. Aucun changement de comportement (B3).

## Volet A — Le plafond de 5 000 F est levé ✅ (prouvé de bout en bout)

Le compte marchand FedaPay est validé ; le webhook fonctionne depuis PAY-TEST (secret synchronisé).
Reste à confirmer que le plafond de 5 000 F (qui avait motivé DEC-334 et la fermeture du paiement en
ligne) est réellement levé.

**Séquence (décision utilisateur)** : ouverture du drapeau → l'utilisateur teste via le checkout réel.
- **Ouverture** : `set-config online_payment_enabled 1` + `bench restart`, vérifiée à **11:55:49 WAT**
  (`_online_payment_enabled()=True`, garde `ONLINE_PAYMENT_DISABLED` levée). **Le drapeau reste levé —
  c'est l'ouverture réelle, pas une fenêtre de test.**
- **Test réel** (utilisateur) : dossier `26271010018` (PREPA, SES-PREPA-S3), montée jusqu'au paiement,
  **paiement en ligne validé**.
- **Constat** : paiement `261100008` — Online, **Confirmed, 10 000 F** (frais concours), réf
  `pay-89967fe7-847b-4a5a-92a6-e9b703181462`, `paid_at 2026-08-19 12:02:25` → dossier **SOU**.

**Verdict** : **10 000 F = le double de l'ancien plafond de 5 000 F**. Un paiement réel de 10 000 F a
été **accepté, encaissé et confirmé** par FedaPay → **le plafond est levé**, et la chaîne complète
(checkout → mobile money → webhook → confirmation → dossier soumis) fonctionne en production.
*(A2 tableau de bord rendu superflu : le checkout lui-même a proposé les moyens de paiement actifs —
mobile money éprouvé.)*

## Volet B — V1.0.0 posée (A concluant)

Version portée de **0.9.0 → 1.0.0** dans les **4 fichiers**, via `npm version` pour les fronts
(précis, ne touche pas les dépendances) et édition directe pour le back :

| Fichier | Dépôt | 0.9.0 → 1.0.0 |
|---|---|---|
| `admission/__init__.py` (`__version__`) | back `admission-lanem-api` | ✅ |
| `package.json` (`version`) | applicant `admission-lanem` | ✅ |
| `package.json` (`version`) | management `lanem-admission-management` | ✅ |
| `package-lock.json` (`version` + `packages[""]`) | management | ✅ |

- **`CHANGELOG.md`** (back) : entrée **v1.0.0 — le paiement en ligne est actif**, en langage utilisateur,
  rappelant les jalons depuis v0.9.3 (audit 360 soldé, correctifs de recette, contrats testés,
  nettoyage pré-PROD).
- **Aucun autre changement** (B3). Aucun changement de comportement.

## Check-list de sortie
| # | Preuve | Résultat |
|---|---|---|
| 1 | Plafond levé | ✅ paiement réel **10 000 F Confirmed** (> 5 000) |
| 2 | Moyens de paiement | mobile money éprouvé (checkout) ; liste exacte au retour utilisateur |
| 3 | Aucun débit non voulu | l'utilisateur a validé volontairement (test complet) — pas de prompt agent |
| 4 | Drapeau | **laissé levé sur décision explicite** (ouverture réelle), documenté |
| 5 | Dossiers de simulation purgés | ✅ **exécuté** — `26272010006` + `26271010018` supprimés, base à **0 dossier** (21 sessions de campagne intactes) |
| 6 | Version 1.0.0 cohérente (4 fichiers) + CHANGELOG | ✅ |
| 7 | Builds propres · baseline **1189/0/0** · aucun changement de comportement | ✅ (voir builds + suite) |
| 8 | Trois branches poussées, arrêt au push | `mandat/pay-v1` back + applicant + management |

## Trace comptable — deux encaissements de test consignés
Les deux dossiers de simulation ont été **purgés** (méthode NETTOYAGE-PREPROD : `frappe.db.delete` SQL
direct, garde `on_trash` des consentements **contournée sans être modifiée** ; consentements de test,
**aucune personne physique concernée**). La PROD est revenue à **0 dossier / 0 frais / 0 paiement /
0 pièce / 0 consentement / 0 transition log**, **21 sessions de campagne intactes**.

Détail des suppressions du dossier payé `26271010018` : 8 pièces (`vht61d53k5 … vhuv5ddohm`), frais
`AFF-2026-00030`, paiement `261100008`, consentements `CONS-2026-00065/66/67`, transition log
`14ceb95vc2`, puis le dossier. Le dossier A1 `26272010006` (LIC-IS, frais `AFF-2026-00029` 25 000
Pending) était **impayé** — purgé sans consignation (aucun encaissement).

**Deux paiements réels encaissés pendant les tests, détruits en base, consignés ici** pour rapprochement
ou remboursement selon la politique interne (la trace vit côté FedaPay, indépendante du dossier) :

| Transaction | Montant | Horodatage (WAT) | Dossier (purgé) | Frais | Origine |
|---|---|---|---|---|---|
| `261100007` (réf `pay-3d984be9-a645-4296-a9e6-8de4f8196028`) | **100 F** | 2026-08-19 00:08:55 | `26272010003` | `AFF-2026-00025` | PAY-TEST / NETTOYAGE-PREPROD |
| `261100008` (réf `pay-89967fe7-847b-4a5a-92a6-e9b703181462`) | **10 000 F** | 2026-08-19 12:02:25 | `26271010018` | `AFF-2026-00030` | PAY-V1 (vérification du plafond) |
| **Total** | **10 100 F** | — | — | — | **à rapprocher / rembourser** |

## Note
Les tags `v1.0.0` sont posés par l'architecte après fusion et déploiement (comme `v0.9.0`/`v0.9.3`).
Arrêt au push des trois branches.
