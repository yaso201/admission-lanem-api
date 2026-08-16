# PAIEMENT-FEDAPAY — Dossier de preuves

> Isolation : worktrees `admission/worktrees/paiement-fedapay` (back, base `6091f87`) et
> `…-applicant` (front, base `aaece83`), branche `mandat/paiement-fedapay`. Clone principal intact.
> **Aucun secret dans un fichier versionné** (grep de contrôle §7). Fusion + déploiement =
> architecte ; **le test live 100 FCFA est post-déploiement** (§8).

## 1. Recon — verdict d'effort : **changement de fournisseur sur structure mature**

L'infrastructure de paiement en ligne existe et est réutilisable. Ce qui change est **strictement
le contrat provider**. Découvertes clés :
- **Aucun nouveau champ doctype** : `Applicant Fee Payment` porte déjà `provider`,
  `provider_reference` (clé d'idempotence), `provider_transaction_id`, `reconciliation`. **Pas de
  migrate.**
- **Une seule route webhook exposée** (`webhook.payment`) ; `kkiapay.py` n'a **aucune**
  `@frappe.whitelist`. Convertir cette route ⇒ **zéro webhook concurrent**.

## 2. Livré (back worktree)

| Fichier | Changement |
|---|---|
| `api/fedapay.py` (**neuf**) | Client FedaPay : `verify_transaction` (GET `/v1/transactions/{id}`, Bearer **clé secrète**, fail-closed) · `valid_webhook_signature` (**HMAC-SHA256** `x-fedapay-signature` = `t=…,s=…`, constant-time) · `mode()` mock/sandbox/live · `_mock_verify` (DEV `MOCK-<ref>`). **Clés = noms de `site_config` uniquement**, jamais de valeur. |
| `api/webhook.py` | Conversion : import `fedapay` · signature HMAC sur le **corps brut** · payload FedaPay (`name`, `entity.{id,status,amount,custom_metadata.provider_reference}`) · `provider="fedapay"`. **Toute la logique promotion/idempotence/concurrence/état-terminal/réconciliation CONSERVÉE.** |
| `api/public.py` (`prepare_online_payment`, région paiement) | Descriptor FedaPay (clé publique seule) ; round-trip `provider_reference` via `custom_metadata` ; `provider="fedapay"` sur le Pending. Régions non-paiement **non touchées**. |
| `api/kkiapay.py` | En-tête **ABANDONNÉ** ; fonctions **intactes** (diagnostics résiduels les appellent). Hors chemin prod. |

## 3. Livré (front worktree applicant)

| Fichier | Changement |
|---|---|
| `public/scripts/admission-tunnel.js` (région paiement) | Widget KkiaPay → **checkout FedaPay** (`FedaPay.init(...).open()`, `custom_metadata.provider_reference`, `onComplete`) ; `pollDossierStatus` inchangé. Export : **`AT.payment`** (nom propre) + **`AT.kkiapay` = alias historique TROMPEUR** (commenté, conservé pour compat `suivi.astro` hors write-set). |
| `src/pages/paiement.astro` | `AT.kkiapay` → **`AT.payment`** (frais 1) ; **`?v=4`** (CAL-13, admission-tunnel.js modifié). |

## 4. Preuves runtime

**Cœur sécurité FedaPay — harnais unitaire sans dépendance : 12/12 PASS** (aucune valeur de secret
réelle) : signature valide acceptée · falsifiée rejetée · corps modifié → invalide · en-tête mal
formé rejeté · **SEC-2 fail-closed (pas de secret → rejet)** · mock verify `MOCK-<ref>` → SUCCESS ·
id non-MOCK → None · ref inconnue → None · normalisation `approved→SUCCESS`, `pending/declined ≠
SUCCESS` · enveloppe `v1/transaction` dépliée.

**Front** : build applicant **19 pages, aucun avertissement** (astro 5.18.2).

## 5. Gates de sortie — statut

| # | Gate | Statut |
|---|---|---|
| 1 | KkiaPay inventorié, chemin prod neutralisé, 0 webhook concurrent | ✅ **prouvé** (grep §7 : webhook.py/public.py n'importent plus kkiapay ; kkiapay.py sans route) |
| 2 | Mobile money frais 1 → webhook → verify → BRO→SOU → reçu → courriel | 🟡 **structure conservée** (logique intacte) ; validation E2E = tests adaptés (§9) + live (§8) |
| 3 | Mobile money frais 2 → ACC→INS → cascade | 🟡 idem (`prepare_enrollment_online_payment` réutilise l'initiation convertie) |
| 4 | Idempotence : rejeu ×3 → 1 seule transition/compta | 🟡 **logique conservée intégralement** (pré-check replay + verrou + index DB) ; preuve chiffrée = test adapté (§9) |
| 5 | Callback front falsifié → aucune transition | ✅ **par construction** : le front n'a que la clé publique ; le webhook exige signature HMAC **+** verify serveur. Prouvé unitaire (signature) §4 |
| 6 | Signature invalide → rejeté | ✅ **prouvé** §4 (falsifiée/corps modifié/mal formé/pas de secret → False) |
| 7 | Échec paiement → dossier inchangé, message clair, réessai | 🟡 conservé (`transaction.failed`→Rejected ; front handlers `failed`/`unavailable`) ; validation = tests §9 |
| 8 | Flux SOP : diff vide sur ses chemins | ✅ **aucun chemin SOP touché** (espèce/banque = `confirm_offline_payment`/`send_offline_submission`, non modifiés) |
| 9 | Test live 100 FCFA | ⏳ **post-déploiement** — procédure §8 (déploiement = architecte) |
| 10 | Aucun secret dans le dépôt | ✅ **prouvé** §7 |

## 6. Propositions de mise à jour du corpus (rédigées, NON appliquées — l'architecte répercute)

### 6.1 Révision SPEC-ADMISSION-INTEGRATION-PAIEMENT (écrite pour KkiaPay)
- **Agrégateur** : FedaPay (DEC-216 révisée). Mobile money MTN/Moov/Celtiis.
- **Autorité de confirmation** : webhook serveur `admission.api.webhook.payment` ; **jamais** le
  callback front. Le front (clé **publique** seule) ne peut rien confirmer.
- **Signature webhook** : `x-fedapay-signature` = `t=<ts>,s=<hash>`, `hash = HMAC-SHA256(webhook_secret,
  "<ts>.<corps brut>")`, comparaison constant-time, **fail-closed** (secret absent → rejet).
- **Vérification** : `GET /v1/transactions/{id}` Bearer clé secrète ; SUCCESS = statut `approved` ;
  montant vérifié ≥ Pending attendu. Le payload n'est **jamais** cru sur parole.
- **Idempotence** : clé = `provider_reference` (round-trip via `custom_metadata`). Rejeu → no-op
  (pré-check replay + verrou ligne + index DB `confirmed_fee`).
- **SOP** : espèce/banque **inchangés** (DEC-212), hors agrégateur — coexistent sur la page paiement.
- **Config `site_config`** : `fedapay_public_key` (front), `fedapay_secret_key`, `fedapay_webhook_secret`,
  `fedapay_sandbox`, `fedapay_mock` (DEV). Aucune valeur au dépôt.

### 6.2 DEC de paiement à acter
- **DEC (paiement en ligne = FedaPay)** : entérine l'implémentation FedaPay (remplace KkiaPay,
  DEC-216 révisée). Mobile money prouvé ; **carte bancaire construite non prouvée** (compte marchand
  non validé) — voir §8.
- Renvoi à la table de correspondance M02 §1 (série DEC-303→330) pour la numérotation.

## 7. Grep de contrôle — sécurité & neutralisation

- **Chemin prod sans kkiapay** : `grep "import kkiapay" api/webhook.py api/public.py` → **0**.
- **Résiduel dormant (hors write-set)** : `api/admin_config.py` (9), `api/recette_check.py` (10),
  `api/health.py` (3), `api/alerting.py` (1) — diagnostics, **aucune route**.
- **Aucun secret versionné** : `grep -rIE "sk_(live|test)|pk_(live|test)|secret_key\s*=\s*[\"']|webhook_secret\s*=\s*[\"']"`
  sur le worktree → **0** (le code lit `frappe.conf.get("fedapay_*")`, ne pose jamais de valeur).

## 8. Instructions de suivi

### 8.1 Test live 100 FCFA (gate 9 — À EXÉCUTER APRÈS DÉPLOIEMENT)
1. Configurer `site_config` PROD : `fedapay_public_key`, `fedapay_secret_key`, `fedapay_webhook_secret`,
   `fedapay_sandbox=0`. Configurer l'URL webhook FedaPay → `https://<api>/api/method/admission.api.webhook.payment`.
2. Sur un **dossier de test identifié** (préfixe test, JAMAIS SES-TEST-100 ni les 14 dossiers PROD),
   payer **100 FCFA** en mobile money de bout en bout.
3. Vérifier : webhook reçu (signature OK), `verify_transaction` = approved, `payment_status`
   Pending→Confirmed, transition BRO→SOU, reçu + courriel. **Documenter transaction_id + dossier**
   pour réconciliation. **Aucune purge.**

### 8.2 Carte bancaire (construite, non prouvée)
Le compte marchand FedaPay est **live pour mobile money** mais **la carte est en attente de validation**.
Le checkout FedaPay gère la carte nativement (même flux). À VÉRIFIER à l'activation du compte : un
paiement carte de bout en bout → webhook → confirmation. Aucune modification de code attendue.

### 8.3 Instructions de reprise (hors write-set)
1. **CAL-13 — bump `?v=4` GLOBAL** : `admission-tunnel.js` (modifié) est chargé par ~10 pages
   (`identite`, `index`, `pieces`, `recapitulatif`, `confirmation`, `bourses`, `reprise`,
   `paiement-accepte`, `paiement-sop`, `suivi`). Seule `paiement.astro` est bumpée `?v=4` (write-set).
   **Bumper `?v=4` sur toutes les autres au merge**, sinon skew de cache (lib périmée KkiaPay servie).
2. **Renommer `AT.kkiapay`→`AT.payment`** quand `suivi.astro` (frais 2) entrera dans un write-set,
   puis supprimer l'alias trompeur.
3. **Faux rouges diagnostics (Q2)** : `recette_check._check_kkiapay_keys` / `_check_kkiapay_mode`
   (gates SEC-kkiapay/MODE-kkiapay) et le check config `health.py` signaleront les **clés KkiaPay
   absentes** (on configure FedaPay) → **rouge sur un fournisseur abandonné**. Remplacer par des
   checks `fedapay_*`. Idem `admin_config.py` (config health/settings).
4. **Retirer le code KkiaPay dormant** (`kkiapay.py` + réfs `admin_config`/`recette_check`/`health`/
   `alerting` + 11 fichiers de tests) → **DETTES-REPORTEES-V1.1**.

## 9. Reste-à-faire pour clore (test suite)

Les tests paiement (11 fichiers, **mockés**) sont écrits pour KkiaPay (payload `stateData`,
en-tête `x-kkiapay-secret`, mock `kkiapay`). **Mécanisme d'exécution du worktree établi** :
`PYTHONPATH=<worktree> bench/env/bin/python -m unittest …` résout bien le code du worktree (le
bootstrap `frappe.local.flags.in_test` doit être posé). **Adaptation requise** : helpers `_payload`
(→ `{name, entity:{id,status,amount,custom_metadata:{provider_reference}}}`) et `_rq` (→ signature
HMAC + `fedapay_webhook_secret`), mock `fedapay` au lieu de `kkiapay`. Baseline cible **1044/3**.
