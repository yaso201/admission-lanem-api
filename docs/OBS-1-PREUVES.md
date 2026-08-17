# OBS-1 — Observabilité & erreurs : preuves

**Lot :** rendre VISIBLE ce qui existe déjà (compteurs calculés puis jetés, erreurs métier
masquées, échecs silencieux) et assainir la baseline de tests à la RACINE. **Aucun changement
de comportement métier.**

- Back : branche `mandat/obs-1` sur `7f356c2` — 3 fichiers de test modifiés.
- Front management : branche `mandat/obs-1` sur `a603644` — 6 fichiers.
- Arrêt au push (merge = architecte).

## Décisions appliquées

- **DEC-A** — aucun échec silencieux : tout `catch` qui masque affiche un état actionnable et
  journalise en **error** (`console.error` côté front ; `default_log_level=ERROR` rendrait un
  `warning` muet hors dev-server).
- **DEC-B** — les 5 compteurs sont **rendus** (un tableau de bord partiel donne une fausse assurance).
- **DEC-C** — baseline **propre** (0 erreur, pas seulement 0 échec) à la **racine**, pas contournée.
- **DEC-D** — **aucun changement de comportement métier** ; ce lot rend visible l'existant.

---

## 1. Les 5 compteurs d'exploitation — RENDUS (DEC-B)

`admin_ops._ops_counters()` calcule 8 compteurs ; `get_ops_health()` les renvoie tous. Le front
n'en affichait que 3 (`uf_unreplicated`, `bridge_pending`, `pending_online_stale`) et **jetait**
les 5 autres. Désormais rendus dans `src/pages/exploitation.astro` (tuiles + `loadHealth`) :

| id | compteur back | libellé |
|----|---------------|---------|
| `n-orphan` | `orphan_refund_due` | Paiements orphelins (remboursement dû) |
| `n-underpaid` | `underpaid_review` | Paiements insuffisants (revue requise) |
| `n-refused` | `refused_terminal` | Paiements sur dossier terminal (remboursement dû) |
| `n-email-err` | `email_queue_error` | E-mails en échec (file d'envoi) |
| `n-jobs-failed` | `scheduled_job_failed_24h` | Tâches planifiées échouées (24 h) |

Indicateurs de **surveillance** (pas d'action de redrive) : une valeur > 0 vire en `warn`
(rouge) via le `set()` existant. **Preuve build** : les 5 ids sont présents dans
`dist/exploitation/index.html`.

## 2. E-05 — erreurs métier perdues en mode `raw` (DEC-A) — PROUVÉ FALSIFIABLE

**Défaut** : en mode `raw`, tout non-2xx levait `HTTP_n / « Téléchargement impossible »` AVANT de
lire l'enveloppe métier `{ok,error}` (`public/scripts/api.js`). Les branches `NOT_CONFIRMED`,
`NO_CONVOCATION`, `SESSION_CLOSED`… étaient **inatteignables** sur les 5 familles de
téléchargement (reçu, convocation, émargement, état concours, pièce).

**Correctif** : avant l'erreur générique, si `content-type: application/json`, parser
l'enveloppe (`message.{ok,error}`) et lever `{code, message: err.message, httpStatus}`. Le
fallback « Téléchargement impossible » est **conservé** pour les erreurs non-JSON (HTML/500).

**Preuve TDD RED/GREEN** (test transitoire `e05-api.test.mjs`, `api.js` chargé par import
dynamique, `fetch` mutable — falsifiable via `API_JS_PATH`) :

| test | original (RED) | corrigé (GREEN) |
|------|:--:|:--:|
| refus métier JSON `NOT_CONFIRMED` 409 → message serveur | ✖ `HTTP_409` | ✔ |
| `NO_CONVOCATION` 404 sur reçu → message serveur | ✖ `HTTP_404` | ✔ |
| émargement `SESSION_CLOSED` → message serveur | ✖ `HTTP_409` | ✔ |
| erreur NON-JSON (500 HTML) → « Téléchargement impossible » **préservé** | ✔ | ✔ |
| succès 2xx → renvoie un blob (inchangé) | ✔ | ✔ |

Les 3 tests de refus métier **rougissent** sur l'original et **verdissent** sur le correctif ;
les 2 contrôles (fallback non-JSON, succès) restent verts dans les deux cas → **aucun changement
de comportement** pour ces chemins. `?v=3 → ?v=4` bumpé (`api.js` = asset `public/` non hashé).

## 3. E-06 — échec silencieux (transfert institutionnel) (DEC-A)

`calendrier-session.astro › loadTransferTargets` : le `catch` faisait `card.hidden=true` — la
fonction de transfert disparaissait **sans message**. Désormais : `AUTH` → return (redirection
gérée par `request()`) ; sinon la carte **reste visible**, un `UI.emToast(...,'error')` signale
l'échec, `console.error` journalise, et l'aperçu est désactivé faute de cibles chargées.

## 4. E-07 — échec silencieux (détection de rôle sur /notes) (DEC-A)

`notes.astro › init` : `try{ whoami }catch(e){ return }` laissait l'écran **inerte** sans
explication. Désormais : `AUTH` → return ; sinon un message actionnable est rendu dans `#roster`
(« Rechargez la page ou reconnectez-vous. ») et `console.error` journalise.

`ui.js` **intact** (E-06/E-07 réutilisent `UI.emToast` existant) → aucune coordination A11Y-1.

---

## 5. Baseline back — 3 faux-rouges assainis à la RACINE (DEC-C)

### Symptôme
`FAILED (errors=3)` : `setUpClass` de `TestCal09DecE`, `TestRolesHierarchyHelper`,
`TestHardenPatch` → `_pickle.PicklingError: Can't pickle MagicMock`.

### Cause racine (investigation systématique)
`frappe.db.commit()` (dans `setUpClass`) exécute `after_commit.run()` → une callback
`enqueue(..., enqueue_after_commit=True)` tente de **sérialiser** un job RQ dont un argument est
un `MagicMock` → PicklingError. Les 3 classes en erreur sont **innocentes** : simplement les
prochaines à committer.

Mécanisme d'isolation confirmé dans le framework :
- `db.rollback()` **complet** fait `after_commit.reset()` → `FrappeTestCase` (rollback complet
  par test) **ne peut pas** fuir entre classes.
- Le rollback **par savepoint** ne réinitialise PAS `after_commit`.
- ⇒ Le fuyard est un **`unittest.TestCase` simple** (aucun rollback Frappe) qui déclenche un vrai
  `enqueue_after_commit` avec des arguments Mock : la callback survit sur `frappe.db.after_commit`
  jusqu'au `commit()` du `setUpClass` suivant, où elle **détone**.

### Isolation décisive (sonde transitoire `obs1_probe.py`, supprimée)
Balayage des **93 modules** (reset `after_commit` entre chaque, attribution par module puis par
test) → **exactement 3 modules fuyards**, 1 callback chacun = les 3 erreurs :

| test fuyard | chemin |
|-------------|--------|
| `test_bridge.TestCapturePromoAtPayment.test_webhook_frais1_captures_promo_via_cascade` | webhook FedaPay → cascade |
| `test_pay_confirm.TestCascadeHelper.test_cascade_from_bro` | cascade directe |
| `test_sec_critique.TestSec2Webhook.test_webhook_valid_signature_accepted` | webhook signé → cascade |

Point commun : `apply_confirmed_payment_cascade` transitionne **BRO→SOU** et, ligne
`public.py:2377` (garde `from_status == "BRO"`), appelle `_enqueue_submission_notif(applicant, …)`
→ **seul** `frappe.enqueue(enqueue_after_commit=True)` de la cascade. Les 3 tests passent un
`applicant = MagicMock()` avec un `frappe.enqueue` **réel** (ils ne mockent pas `PUBLIC.frappe`
ni l'enqueue) → la callback capture le Mock.

### Correctif racine (pas un contournement)
Le **code de production est correct** : il DOIT enfiler la notif en prod. Le défaut est purement
d'**isolation de test** : un test unitaire laisse un effet de bord `enqueue` non mocké s'échapper.
Correctif idiomatique (mêmes que les 4 fichiers sœurs identity/bridge) : mocker la frontière
d'effet de bord dans les **3 méthodes** exactes —

```python
@patch("admission.api.public._enqueue_submission_notif")
```

Toutes les assertions sont préservées (transition BRO→SOU, capture promo, acceptation de
signature se produisent **avant/indépendamment** de l'enqueue). Sonde après correctif : **0
callback pendante** sur les 3 modules ; les 3 modules passent (`test_bridge` 22 OK, `test_pay_confirm`
7 OK, `test_sec_critique` 42 OK).

### Baseline finale
```
Ran 1123 tests in 44.510s
OK
```
0 erreur, 0 échec, 0 PicklingError.

**Pourquoi 1123 et pas 1111 ?** La cible « 1111 » était elle-même un **artefact du bug** : un
`setUpClass` en erreur fait **sauter** les méthodes de la classe (unittest ne compte que
l'erreur). Les 3 classes victimes totalisent **8 + 2 + 2 = 12** méthodes jusqu'ici non exécutées.
Le correctif débloque ces 12 tests → 1111 + 12 = **1123**, tous verts. C'est la baseline saine.

### Falsifiabilité (mandat : « réintroduire le cas → le test doit rougir »)
Mock retiré de `test_cascade_from_bro` → la sonde re-détecte la fuite (`total 1`, méthode exacte) ;
mock restauré → `total 0`. L'instrument est prouvé sensible (93→3→0 sur les mêmes edits).

---

## Frontière & garanties

- **Aucun** fichier applicant touché ; **aucun** fichier interdit (NT-S, `_actions.py`,
  `exam_grading.py`, `calendar_rules.py`, `dossier.astro`, construction des labels dans `ui.js`).
- `ui.js` **intact** ; `dossier.astro` intact.
- **Aucun changement de comportement métier** : back = tests seuls ; front = affichage/erreurs.
- Build front management **propre** (`Complete!`) ; `?v=4` propagé dans `dist`.
- Sonde `obs1_probe.py` **transitoire supprimée** (hors commit).
