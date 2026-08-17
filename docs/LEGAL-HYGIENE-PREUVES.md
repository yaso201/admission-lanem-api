# LEGAL-HYGIENE — Dossier de preuves

> Mandat DEC-L (recon → pause unique → exécution continue → rapport). Deux worktrees, une branche
> par dépôt : back `mandat/legal-hygiene` (base `9c2e039`, post-NT-S) · front applicant
> `mandat/legal-hygiene` (base `ac8c3be`, post-LEGAL-FEDAPAY). **Arrêt au push** ; fusion,
> `migrate` et déploiement appartiennent à l'architecte.
>
> Deux volets : **B** = LEGAL-BACK (les 3 `Admission Legal Document` back-générés que
> [[LEGAL-FEDAPAY]] avait laissés en cas d'arrêt) · **C** = hygiène KkiaPay (C1 diagnostics,
> C2 module mort, C3 alias front mort).

---

## 0. Recon — le point le moins clair, résolu

Le contenu légal des 3 pages back-générées (CGV, politique de confidentialité, consentement
transfert) **est une DONNÉE de base** (doctype `Admission Legal Document`), pas un fichier :
aucune fixture, aucun seed, aucun `legal.py` d'écriture (seulement lecture :
`_get_active_legal_document`, `_record_consent`). Le doctype porte un **versionnement natif** :

- `autoname = LEGAL-.YYYY.-.#####`, une seule version active par `document_type` (`is_active=1`) ;
- `validate()` recalcule `content_hash = sha256(content_text)` **et REFUSE** (throw) une 2ᵉ version
  active du même type — *il ne désactive PAS automatiquement l'ancienne* (contrat vérifié en base,
  §B.1) ;
- les consentements candidats pointent le `content_hash` (`version_hash`) → **écraser un texte
  détruirait la preuve de consentement**.

→ **Voie d'écriture correcte = patch de migration** qui, pour chaque type, lit la version active,
substitue le texte (fail-closed), **désactive l'ancienne** puis **insère** une nouvelle version
active. L'ancienne reste en base (preuve de consentement intacte). C'est la voie retenue (ruling 1).

Write-set NT-S (9c2e039) confronté : `admission_applicant/fee/payment` + `_actions.py` + `staff.py`
+ leurs tests — **zéro recouvrement** avec le write-set LEGAL-HYGIENE. Les gardes NT-S ne bloquent ni
le patch (joué au `migrate`, contexte Administrator) ni les diagnostics (lecture seule).

---

## B. LEGAL-BACK — patch de migration `Admission Legal Document`

### B.1 Contrat réel du doctype (corrige l'hypothèse de recon)

L'hypothèse « `validate()` auto-désactive l'ancienne version » était **fausse**. Preuve — le premier
jet du patch (insert direct) a levé :

```
frappe.exceptions.ValidationError: Un document actif de type CGV existe deja: LEGAL-2026-00001.
Desactivez-le avant d'en activer un nouveau.
```

Le fail-closed a donc **protégé** (aucune écriture partielle). Correctif : le patch **désactive
explicitement** l'ancienne active (`frappe.db.set_value(..., "is_active", 0)`, écriture directe qui
ne touche PAS son `content_hash`) **avant** d'insérer la nouvelle. L'ancienne survit en base → les
consentements déjà recueillis conservent leur preuve.

### B.2 Le patch — `admission/patches/v1_2/update_legal_fedapay.py`

Enregistré dans `patches.txt` après `add_transfer_session_workflow` (post_model_sync). Propriétés :

- **fail-closed** : chaque chaîne attendue DOIT être présente, sinon `frappe.throw` (on ne devine
  jamais un contenu légal) ;
- **idempotent** : si la version active ne contient plus « KkiaPay », le type est déjà migré → skip
  (pas d'empilement de versions identiques) ;
- **versionnant** : n'écrase jamais ; crée une v1.1 active, désactive la v1.0 (conservée).

Substitutions exactes (relues sur `LEGAL-2026-00001/2/4` en base) :

| Type | Ancien | Nouveau |
|---|---|---|
| CGV | `moyens de paiement acceptés` | `moyens de paiement proposés` *(B2, drapeau à 0)* |
| CGV | `du prestataire KkiaPay` | `du prestataire FedaPay` *(B1)* |
| PRIVACY_POLICY | `\| KkiaPay \| Paiement \| Bénin \|` | `\| FedaPay \| Paiement \| Bénin \|` |
| DATA_TRANSFER_CONSENT | `\| KkiaPay \| Cotonou \|` | `\| FedaPay (FEDAPAY SA) \| Ste Rita, Quartier Tonato, 8ᵉ arr., Cotonou \|` |

Identité (fournie par la Direction, canal marchand) : **FEDAPAY SA**, capital 100 000 000 XOF, siège
Ste Rita C/1398 P/V, Quartier Tonato, 8ᵉ arr., Cotonou, Bénin, RCCM RB/COT/19B24720, IFU
3201910819942, DG Boris KOUMONDJI. Entité **béninoise** → la qualification « Bénin — hors champ des
transferts vers un État tiers » de la table de transferts **reste exacte**.

### B.3 Preuve runtime (dev `admission-dev.localhost`)

Patch joué (via `bench execute …update_legal_fedapay.execute`), puis **rejoué** (idempotence) :

```
CGV                   : 2 versions | active=v1.1 (LEGAL-2026-00006) | KkiaPay=False FedaPay=True
PRIVACY_POLICY        : 2 versions | active=v1.1 (LEGAL-2026-00007) | KkiaPay=False FedaPay=True
DATA_TRANSFER_CONSENT : 2 versions | active=v1.1 (LEGAL-2026-00008) | KkiaPay=False FedaPay=True
--- hash distinct ancienne/nouvelle (preuve versionnement, consentements préservés) ---
   CGV v1.0 active=0 hash=e5de2f7ebb27   |   CGV v1.1 active=1 hash=f2fe4b7cd52c
   PRIVACY v1.0 active=0 hash=1b4731b0bce6 | PRIVACY v1.1 active=1 hash=b430751d6573
   DATA_TRANSFER v1.0 active=0 hash=e3b5b92ea45f | v1.1 active=1 hash=a1c47501215c
```

- **1 seule active par type** (assertion) · ancienne v1.0 conservée `is_active=0`, hash inchangé.
- **Rejeu = aucune 3ᵉ version** (idempotence prouvée : 2 versions, pas 3).
- `recette_check DATA-legal` : « 4 types actifs (PRIVACY/CGV/REFUND/DATA_TRANSFER) » — intact.

> ⚠️ Les v1.1 de dev sont le **résultat légitime** de la migration (état cible de dev), pas une
> donnée de test à purger ; la garde d'idempotence évite tout doublon si un `migrate` officiel
> repasse dessus.

---

## C1. Diagnostics — KkiaPay → FedaPay + health flag-aware (ruling 3)

Cause : `fedapay.py` lit `fedapay_public_key` / `fedapay_secret_key` / `fedapay_webhook_secret` /
`fedapay_mock` / `fedapay_sandbox`, mais les diagnostics interrogeaient encore les clés `kkiapay_*`
**mortes** → faux négatifs silencieux. Corrigé dans 4 fichiers :

| Fichier | Changement |
|---|---|
| `api/recette_check.py` | `_check_kkiapay_keys`→`_check_fedapay_keys` (2 clés, plus 3) ; `_check_kkiapay_mode`→`_check_fedapay_mode` ; **nouveau** `_check_fedapay_webhook` ; entrées `SEC-fedapay` / `MODE-fedapay` / `SEC-webhook` recâblées. **Flag-aware** : clés/secret absents + `online_payment_enabled=0` → **WARN** (pas FAIL, pas PASS) ; drapeau à 1 → FAIL si absents. |
| `api/health.py` | Clés paiement sorties de `_CRITICAL_CONF` vers `_PAYMENT_CONF` (fedapay_*) ; `_probe_config` **flag-aware** : critiques seulement si paiement actif, sinon visibles sous « paiement désactivé » sans dégrader (évite le faux 503 payment-off *et* le faux healthy payment-on). |
| `api/admin_config.py` | Bloc diagnostic + docstring : lit fedapay_* (2 clés) ; réponse `kkiapay`→**`fedapay`**, `flags.kkiapay_mock`→`fedapay_mock`, `webhook_secret` lit `fedapay_webhook_secret`. |
| `api/alerting.py` | `_ALERT_LABELS` : `kkiapay_verify`→**`fedapay_verify`** (« Vérification FedaPay impossible ») — alignement sur `alert_type` réellement émis par `fedapay.py` (l'alerte tirait déjà, mais avec le libellé brut faute de clé). |

Tests suivant le changement de contrat : `test_admin_config.py` (groupe `kkiapay`→`fedapay`) ;
`test_obs3_fold.py` (`TestItem5FedapayCritical` : clés fedapay + **nouveau cas flag-off** « visible,
non dégradé »).

### Preuve runtime (dev)

`recette_check.run` :
```
[✓ PASS] SEC-webhook   Secret webhook paiement — secret posé          (lit fedapay_webhook_secret)
[✓ PASS] SEC-fedapay   Clés marchand FedaPay (2) — 2 clés posées (environnement LIVE)
[✗ FAIL] MODE-fedapay  Mode FedaPay — fedapay_mock actif — vérification SIMULÉE (DEV)
```
→ plus aucune entrée `kkiapay` ; les 3 contrôles paiement résolvent sur fedapay_* sans exception.

`health._probe_config` (dev, `online_payment_enabled=True`, clés fedapay posées) :
```
online_payment_enabled = True
probe_config ok = False | detail = manquant: candidate_portal_url (en attente: uf_backoffice_url)
```
→ les clés fedapay **ne figurent pas** dans « manquant » (dev les a) ; la dégradation vient d'une
clé non-paiement préexistante. La branche flag-off (WARN, non dégradant) est prouvée par le test
unitaire vert `test_missing_fedapay_key_visible_not_degraded_when_payment_off`.

`admin_config.get_config_health` :
```
fedapay bloc = {'present': True, 'mode': 'MOCK'}   |   kkiapay bloc = None
```
→ contrat renommé proprement (`.fedapay` présent, `.kkiapay` disparu).

---

## C2. Module mort `api/kkiapay.py` — supprimé

Avant suppression, vérifié **aucun** : import (`import kkiapay` / `from …api.kkiapay` / `api.kkiapay`),
wiring `hooks.py`, endpoint `@frappe.whitelist` (appelable par chemin), appel front
`admission.api.kkiapay.*`. Ses fonctions (`is_mock/is_sandbox/mode/public_key/verify_transaction/
_mock_verify`) sont toutes couvertes par `fedapay.py`. **`git rm` (D)** confirmé.

---

## C3. Alias front mort `AT.kkiapay` — retiré + bump cache CAL-13

`public/scripts/admission-tunnel.js` exportait `kkiapay: { launch, pollDossierStatus }` (alias
« historique TROMPEUR », le nom lançait déjà du FedaPay). Vérifié **aucun consommateur** de
`AT.kkiapay` dans `src/` + `public/` ; `suivi.astro` utilise déjà `AT.payment` → alias vraiment mort,
retiré (le namespace public ne garde que `payment`).

`admission-tunnel.js` est un asset `public/` **non hashé** chargé par 11 pages → **bump `?v=` global
obligatoire dans le même commit** (leçon CAL-13, sinon un navigateur sert l'ancien script en cache).
**`?v=5 → ?v=6`** sur les 11 pages :

```
bourses · confirmation · identite · index · paiement · paiement-accepte ·
paiement-sop · pieces · recapitulatif · reprise · suivi   (.astro)
```

`dist/` reconstruit : `11 × admission-tunnel.js?v=6`, **0 résidu `?v=5`** · `politique-cookies` =
FedaPay/0 KkiaPay (front-natif [[LEGAL-FEDAPAY]] préservé).

---

## Baselines

| Baseline | Attendu | Obtenu |
|---|---|---|
| Back `run-tests --app admission` | 1100/3 | **1101/3** (1100 + 1 nouveau test flag-off ; 3 erreurs `setUpClass` **connues** : `test_calendar` / `test_roles_hierarchy` / `test_sm_l0` — échec RQ/Redis `enqueue` en `commit`, environnemental, **hors write-set**) |
| Front applicant `npm test` | vert | **61/61, 0 fail** (déterministe sur 2 runs ; les 4 « faux-rouges » du 1er run = `jsdom` non installé, résolu par `npm install`) |
| Front build (fail-closed pull-legal + astro) | propre | **Complete!** sans avertissement, 19 pages |
| `dist` cache-buster | v=6 | **11/11 en v=6, 0 en v=5** |

Les erreurs `setUpClass` sont prouvées environnementales (traceback :
`setUpClass → frappe.db.commit → after_commit → enqueue_call → rq/job.save`), identiques au baseline
documenté « 1100/3 » ; `test_admin_config` (5/5) et `test_obs3_fold` (9/9) — mes deux modules
modifiés — passent en run ciblé.

---

## Résidus hors périmètre (signalés, non touchés — DEC strict)

1. **Commentaires historiques** nommant KkiaPay dans du code hors write-set (`staff.py`, `public.py`,
   `webhook.py`, `fedapay.py`, `applicant_fee_payment.py`) : **lignée exacte** (« Remplace le client
   KkiaPay », « ex-LOT KKIAPAY »), pas des références actives erronées. `staff.py` est de plus
   adjacent à NT-S → non touché.
2. **`tests/e2e/audit_bloc4.py:34`** filtre `provider: "kkiapay"` alors que le flux réel écrit
   désormais `provider: "fedapay"` (public.py:2412/2421, webhook.py:104). Module d'audit
   **adversarial dormant**, lancé manuellement (`bench execute`), findings CONFORMITÉ-E2E déjà
   **soldés** — hors baseline `run-tests`, concurrence-sensible → non modifié pour éviter la reprise
   d'un module E2E. **À rafraîchir si l'audit est rejoué.**
3. `tests/e2e/lib_session.mjs:112` : commentaire « bloque … KkiaPay » (liste d'hôtes headless). Cosmétique.

## Dépendance cross-repo (front management — SÉPARÉ, pas dans le write-set)

`admin_config.get_config_health` renvoie désormais `data.fedapay` (au lieu de `data.kkiapay`) et
`data.flags.fedapay_mock`. Le front **management** (`lanem-admission-management`, dépôt séparé, non
présent localement) qui lit ce contrat **doit être mis à jour en lockstep** (`.kkiapay`→`.fedapay`,
`flags.kkiapay_mock`→`fedapay_mock`) sinon l'écran SM « santé config » affichera l'intégration
paiement comme absente (dégradation d'affichage ; pas de crash si accès optionnel). Alias de compat
volontairement **non** ajouté (contredirait l'hygiène du lot). — routage architecte.

---

## Ordre de déploiement (architecte — après fusion des deux branches)

1. **Back** : fusion `mandat/legal-hygiene` → `main` (ff-only après rebase), puis **`bench migrate`
   obligatoire** (le patch crée les v1.1 FedaPay + désactive les v1.0) + `bench clear-cache` +
   restart. Vérifier `bench --site … execute admission.api.public.get_legal_documents` = 0 KkiaPay.
2. **C1 déployé AVANT** tout retrait des clés `kkiapay_*` de la config PROD — sinon les diagnostics
   nouvellement migrés liraient des clés absentes. (Poser `fedapay_public_key` / `fedapay_secret_key`
   / `fedapay_webhook_secret` en PROD au préalable ; retirer les `kkiapay_*` ensuite.)
3. **Front management** : mettre à jour le consommateur de `get_config_health` en lockstep avec C1
   (cf. dépendance cross-repo).
4. **Front applicant** : fusion → Pages reconstruit ; comme le back sert désormais FedaPay, les **3
   pages back-générées** (`cgv`, `politique-de-confidentialite`, `consentement-transfert-donnees`)
   basculent en FedaPay au build, et le `?v=6` casse le cache d'`admission-tunnel.js`. Cible finale :
   **0 KkiaPay dans tout `dist/legal/`**. (Dans le build de recette actuel, ces 3 pages sont encore
   KkiaPay car tirées du back recette non encore migré — attendu, résolu par l'étape 1 + rebuild.)
