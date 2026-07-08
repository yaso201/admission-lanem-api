# FIX-PROGRESSION — available_actions (Option A élargi) — Plan PC1

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:executing-plans. Steps use checkbox (`- [ ]`).

**Goal:** Le back `admission` calcule et sert `available_actions` (+ `can_control_pieces`, `can_manage_payments`) dérivés des **mêmes** règles d'autorisation que les gardes ; le front `dossier.astro` devient un **pur renderer** (plus de `uxRole` collapse, plus de branches admin/resp/dir codées en dur, plus de `canAct=ROLE==='admin'`).

**Architecture:** Un registre déclaratif unique `_ACTION_RULES` (back) mappe chaque action staff → une règle `(applicant, is_prepa) -> base_role|None` encodant statut + condition métier + niveau minimal. `available_actions(applicant, roles, is_prepa)` itère ce registre et applique `roles_at_or_above` (hiérarchie live). `get_dossier` ajoute la liste au contrat. Le front rend un bouton ssi sa clé est dans `available_actions` (et garde ses libellés/modales/sous-conditions per-pièce). Anti-dérive = matrice de cohérence AS chaque vrai rôle (GP1).

**Tech Stack:** Frappe (Python) back ; Astro/JS vanilla front (management) ; tests `bench run-tests` (unittest) + e2e puppeteer recette.

## Global Constraints

- **Source unique, 0 dérive** : `available_actions` dérive du registre `_ACTION_RULES` ; ce registre EST la déclaration d'autorisation. Anti-dérive prouvé par la matrice de cohérence (GP1), pas par une seconde implémentation.
- **Enforcement intouché** : les `frappe.only_for(...)` de CHAQUE endpoint restent tels quels. `available_actions` = UX (montre/masque), jamais l'enforcement. Un bug d'`available_actions` = bug UX, pas trou de sécurité.
- **Séparation des pouvoirs** : par construction (mêmes niveaux) — Responsable à ADM ⇒ pas d'`accept_admission` (min = Direction).
- **Hiérarchie ascendante live** : `roles_at_or_above("Admission Administratif")` = {Administratif, Responsable, Direction, System Manager} ; idem Responsable/Direction. `Admission SM` est **orthogonal** (hors hiérarchie) ⇒ actions workflow vides pour un SM pur.
- **Candidat /suivi INCHANGÉ** : fix staff-side seulement.
- **Tester AS le vrai rôle** (`frappe.set_user`, `in_test=False` pour activer `only_for`) — V-LEARN-MOCK-FIDELITY-25.
- **Vérif front = navigateur réel** sur le bundle Worker (V-LEARN-WORKER-VERIFY-21).
- Repos : back `admission-lanem-api` (main `2f09b1b`), front management `emela-... (management)` (main `dde6865`).

---

## File Structure

| Fichier | Responsabilité | Action |
|---|---|---|
| `admission/api/_actions.py` (back, NEW) | Registre `_ACTION_RULES` + `available_actions()` + `can_control_pieces()` + `can_manage_payments()`. Source unique de la disponibilité. | Create |
| `admission/api/staff.py` (back) | `get_dossier` ajoute `available_actions`/`can_control_pieces`/`can_manage_payments` au retour (import depuis `_actions`). | Modify (retour get_dossier ~staff.py:530-567) |
| `admission/tests/test_available_actions.py` (back, NEW) | Matrice unitaire (statut × rôle × flags → actions attendues) + **cohérence AS chaque vrai rôle** (set_user, in_test=False). | Create |
| `src/pages/dossier.astro` (front management) | `renderActions` piloté par `D.available_actions.includes(key)` ; `canAct` = `D.can_control_pieces` ; panneau paiements = `D.can_manage_payments` ; message SM clair ; retrait `uxRole`/branches/`canAct=admin`. | Modify |
| `admission/tests/e2e/e2e_progression.mjs` (back, NEW) | Preuve recette bundle live GP1-GP6 (AS chaque vrai rôle via mint_session). | Create |

---

## Registre — clés d'action et règles (référence pour tous les tasks)

Clés = **noms d'endpoint** (vocabulaire canonique). Le front mappe clé → handler existant.

| Clé | Règle `(a, is_prepa) -> base_role | None` | Endpoint / garde actuelle |
|---|---|---|
| `start_review` | `"Admission Administratif"` si `a.status=="SOU"` | staff.py:211 ADMIN_UP, SOU |
| `notify_pieces_recap` | `"Admission Administratif"` si `SOU` | staff.py ADMIN_UP, SOU |
| `reject_dossier` | `"Admission Administratif"` si `SOU` | staff.py:963 ADMIN_UP, SOU |
| `reopen_dossier` | `"Admission Administratif"` si `REJ` | staff.py:986 ADMIN_UP, REJ |
| `request_complement` | `"Admission Administratif"` si `SOU` ; `"Admission Responsable"` si `ETU` | staff.py:182/184 (statut-dépendant) |
| `verify_bac_diploma` | `"Admission Administratif"` si `ACO and not a.bac_verified` | staff.py:823 ADMIN_UP, ACO |
| `saisir_note_concours` | `"Admission Administratif"` si `ETU and is_prepa and not a.notes_validated` | staff.py:1200 ADMIN_UP |
| `valider_notes_concours` | `"Admission Responsable"` si `ETU and is_prepa and a.notes_concours and not a.notes_validated` | staff.py:1230 RESP_UP |
| `propose_scholarships` | `"Admission Responsable"` si `status in (ETU,ATT) and a.requested_scholarships` | staff.py:763 RESP_UP |
| `set_waitlist_rank` | `"Admission Responsable"` si `ATT` | staff.py:1353 RESP_UP |
| `mark_admissible` | `"Admission Responsable"` si `status in (ETU,ATT)` (notes validées si prépa) | staff.py:243 RESP_UP |
| `waitlist` | `"Admission Responsable"` si `ETU` | staff.py:269 RESP_UP |
| `refuse` | `"Admission Responsable"` si `ETU` ; `"Admission Direction"` si `ADM` | staff.py:309/311 (statut-dépendant) |
| `conditional_admission` | `"Admission Responsable"` si `ETU and a.conditionnel` | staff.py:1067 RESP_UP |
| `accept_admission` | `"Admission Direction"` si `ADM` | staff.py:344 DIR_UP |
| `lift_condition` | `"Admission Direction"` si `ACO` | staff.py:1104 DIR_UP |
| `refuse_condition` | `"Admission Direction"` si `ACO` | staff.py:1134 DIR_UP |
| `enroll` | `"Admission Direction"` si `ACC` | staff.py:645 DIR_UP |
| `withdraw` | `"Admission Administratif"` si `status in {BRO,SOP,SOU,ETU,ATT,ADM,ACO,ACC}` | staff.py:1327 ADMIN_UP |

Familles booléennes (le front garde ses sous-conditions) :
- `can_control_pieces` = `status=="SOU" and roles ∩ roles_at_or_above("Admission Administratif")` (garde back `_resolve_piece_sou` = CONFIRM_ROLES + SOU).
- `can_manage_payments` = `status not in PAYMENT_FORBIDDEN_STATES and roles ∩ roles_at_or_above("Admission Administratif")` (confirm_offline_payment = CONFIRM_ROLES).

---

### Task 1 : Back — registre `_actions.py` + `available_actions`

**Files:**
- Create: `admission/api/_actions.py`
- Test: `admission/tests/test_available_actions.py`

**Interfaces:**
- Produces: `available_actions(applicant, roles, *, is_prepa) -> list[str]` ; `can_control_pieces(applicant, roles) -> bool` ; `can_manage_payments(applicant, roles) -> bool`. `applicant` = doc ou objet avec `.status`, `.bac_verified`, `.notes_validated`, `.notes_concours`, `.requested_scholarships`, `.conditionnel`. `roles` = list[str].

- [ ] **Step 1 — test rouge (matrice unitaire)** : créer `test_available_actions.py` avec un faux applicant (types.SimpleNamespace) et asserts par (statut, rôles, flags). Exemples clés :
```python
import types
from admission.api._actions import available_actions, can_control_pieces, can_manage_payments

def _a(**k):
    base = dict(status="SOU", bac_verified=0, notes_validated=0, notes_concours=None,
                requested_scholarships="[]", conditionnel=0)
    base.update(k); return types.SimpleNamespace(**base)

def test_admin_sou_intake():
    acts = available_actions(_a(status="SOU"), ["Admission Administratif"], is_prepa=False)
    assert "start_review" in acts and "reject_dossier" in acts and "request_complement" in acts
    assert "mark_admissible" not in acts        # resp-only, et pas ETU

def test_responsable_at_sou_gets_admin_actions_via_hierarchy():
    acts = available_actions(_a(status="SOU"), ["Admission Responsable"], is_prepa=False)
    assert "start_review" in acts               # ascendant : Resp ⊇ Admin
    assert can_control_pieces(_a(status="SOU"), ["Admission Responsable"]) is True

def test_directeur_at_etu_sees_resp_decisions():
    acts = available_actions(_a(status="ETU"), ["Admission Direction"], is_prepa=False)
    assert "mark_admissible" in acts and "waitlist" in acts   # Dir ⊇ Resp

def test_adm_accept_dir_only():
    assert "accept_admission" in available_actions(_a(status="ADM"), ["Admission Direction"], is_prepa=False)
    assert "accept_admission" not in available_actions(_a(status="ADM"), ["Admission Responsable"], is_prepa=False)
    assert "refuse" in available_actions(_a(status="ADM"), ["Admission Direction"], is_prepa=False)   # ADM→REF = Dir
    assert "refuse" not in available_actions(_a(status="ADM"), ["Admission Responsable"], is_prepa=False)

def test_sm_pur_zero_workflow():
    assert available_actions(_a(status="ADM"), ["Admission SM"], is_prepa=False) == []
    assert can_control_pieces(_a(status="SOU"), ["Admission SM"]) is False

def test_prepa_notes_conditions():
    a = _a(status="ETU")
    assert "saisir_note_concours" in available_actions(a, ["Admission Administratif"], is_prepa=True)
    assert "saisir_note_concours" not in available_actions(a, ["Admission Administratif"], is_prepa=False)

def test_pieces_control_requires_sou():
    assert can_control_pieces(_a(status="ETU"), ["Admission Administratif"]) is False
```
- [ ] **Step 2 — rouge** : `bench --site admission-dev.localhost run-tests --app admission --module admission.tests.test_available_actions` → ImportError / échecs.
- [ ] **Step 3 — impl** : écrire `_actions.py` :
```python
"""FIX-PROGRESSION — SOURCE UNIQUE de la disponibilité d'action staff.
Le registre _ACTION_RULES déclare, par action, le rôle de BASE requis à l'état
courant (statut + condition métier). available_actions applique la hiérarchie
ascendante (roles_at_or_above). Les endpoints gardent leur only_for (enforcement) ;
ici = UX. La matrice de cohérence (tests) verrouille registre ↔ gardes (0 dérive)."""
import json
from admission.api.permissions import roles_at_or_above

_WITHDRAW_STATES = {"BRO", "SOP", "SOU", "ETU", "ATT", "ADM", "ACO", "ACC"}
_PAYMENT_FORBIDDEN = {"REF", "REJ", "DES", "INS"}   # miroir PAYMENT_FORBIDDEN_STATES

def _has_requested(a):
    try: return bool(json.loads(a.requested_scholarships or "[]"))
    except (ValueError, TypeError): return False

# chaque règle : (applicant, is_prepa) -> rôle de base requis MAINTENANT, ou None
_ACTION_RULES = {
    "start_review":        lambda a, p: "Admission Administratif" if a.status == "SOU" else None,
    "notify_pieces_recap": lambda a, p: "Admission Administratif" if a.status == "SOU" else None,
    "reject_dossier":      lambda a, p: "Admission Administratif" if a.status == "SOU" else None,
    "reopen_dossier":      lambda a, p: "Admission Administratif" if a.status == "REJ" else None,
    "request_complement":  lambda a, p: "Admission Administratif" if a.status == "SOU"
                                        else ("Admission Responsable" if a.status == "ETU" else None),
    "verify_bac_diploma":  lambda a, p: "Admission Administratif" if a.status == "ACO" and not a.bac_verified else None,
    "saisir_note_concours":  lambda a, p: "Admission Administratif" if a.status == "ETU" and p and not a.notes_validated else None,
    "valider_notes_concours":lambda a, p: "Admission Responsable" if a.status == "ETU" and p and a.notes_concours and not a.notes_validated else None,
    "propose_scholarships":lambda a, p: "Admission Responsable" if a.status in ("ETU", "ATT") and _has_requested(a) else None,
    "set_waitlist_rank":   lambda a, p: "Admission Responsable" if a.status == "ATT" else None,
    "mark_admissible":     lambda a, p: "Admission Responsable" if a.status in ("ETU", "ATT") else None,
    "waitlist":            lambda a, p: "Admission Responsable" if a.status == "ETU" else None,
    "refuse":              lambda a, p: "Admission Responsable" if a.status == "ETU"
                                        else ("Admission Direction" if a.status == "ADM" else None),
    "conditional_admission": lambda a, p: "Admission Responsable" if a.status == "ETU" and a.conditionnel else None,
    "accept_admission":    lambda a, p: "Admission Direction" if a.status == "ADM" else None,
    "lift_condition":      lambda a, p: "Admission Direction" if a.status == "ACO" else None,
    "refuse_condition":    lambda a, p: "Admission Direction" if a.status == "ACO" else None,
    "enroll":              lambda a, p: "Admission Direction" if a.status == "ACC" else None,
    "withdraw":            lambda a, p: "Admission Administratif" if a.status in _WITHDRAW_STATES else None,
}

def _authorized(need, roles):
    return bool(need and set(roles) & set(roles_at_or_above(need)))

def available_actions(applicant, roles, *, is_prepa):
    roles = roles or []
    return [k for k, rule in _ACTION_RULES.items() if _authorized(rule(applicant, is_prepa), roles)]

def can_control_pieces(applicant, roles):
    return applicant.status == "SOU" and _authorized("Admission Administratif", roles or [])

def can_manage_payments(applicant, roles):
    return applicant.status not in _PAYMENT_FORBIDDEN and _authorized("Admission Administratif", roles or [])
```
- [ ] **Step 4 — vert** : relancer le module → OK.
- [ ] **Step 5 — commit** : `git add admission/api/_actions.py admission/tests/test_available_actions.py && git commit -m "feat(progression): registre _actions + available_actions (source unique)"`

### Task 2 : Back — `get_dossier` sert la disponibilité

**Files:**
- Modify: `admission/api/staff.py` (retour `get_dossier`, ~530-567)
- Test: `admission/tests/test_available_actions.py` (ajout d'un test d'intégration get_dossier)

**Interfaces:**
- Consumes: `available_actions`, `can_control_pieces`, `can_manage_payments` (Task 1).
- Produces: `get_dossier` renvoie `available_actions: list[str]`, `can_control_pieces: bool`, `can_manage_payments: bool`.

- [ ] **Step 1 — test rouge** : test qui appelle `staff.get_dossier` (AS un Responsable réel, fixture ETU) et asserte `"mark_admissible" in res["available_actions"]`.
- [ ] **Step 2 — rouge** : run → KeyError (`available_actions` absent).
- [ ] **Step 3 — impl** : dans `get_dossier`, juste avant le `return _ok({...})`, ajouter au dict :
```python
        # FIX-PROGRESSION — disponibilité (UX) dérivée des gardes (source unique _actions).
        "available_actions": available_actions(
            applicant, frappe.get_roles(frappe.session.user),
            is_prepa=bool(session_doc.is_prepa_session) if session_doc else False),
        "can_control_pieces": can_control_pieces(applicant, frappe.get_roles(frappe.session.user)),
        "can_manage_payments": can_manage_payments(applicant, frappe.get_roles(frappe.session.user)),
```
et en tête de fichier : `from admission.api._actions import available_actions, can_control_pieces, can_manage_payments`.
- [ ] **Step 4 — vert** : run OK.
- [ ] **Step 5 — commit** : `git commit -am "feat(progression): get_dossier expose available_actions"`

### Task 3 : Back — matrice de cohérence AS chaque vrai rôle (anti-dérive, GP1)

**Files:**
- Test: `admission/tests/test_available_actions.py` (classe `TestCoherenceMatrix`)

- [ ] **Step 1 — test** : pour chaque (rôle réel ∈ {Administratif, Responsable, Direction}) et chaque fixture-statut construite par CHEMIN MÉTIER (SOU, ETU, ADM, ACO, ACC, ATT, REJ), `frappe.set_user(role_user)` puis `available_actions(...)` doit **coïncider** avec l'autorisation réelle de l'endpoint : appeler l'endpoint représentatif ; s'il ne lève PAS `frappe.PermissionError` ni ne renvoie `INVALID_STATE` pour raison de rôle/statut ⇒ la clé DOIT être dans `available_actions`, et réciproquement. Style aligné sur la matrice FIX-ROLES-HIERARCHIE (32 cellules), `in_test=False` pour activer `only_for`.
- [ ] **Step 2 — run** : rouge si une cellule diverge (dérive registre↔garde).
- [ ] **Step 3 — impl** : ajuster `_ACTION_RULES` jusqu'à cohérence (la garde fait foi).
- [ ] **Step 4 — vert** : matrice 100 % cohérente.
- [ ] **Step 5 — commit** : `git commit -am "test(progression): matrice de cohérence available_actions AS vrais rôles"`

### Task 4 : Front — `dossier.astro` pur renderer

**Files:**
- Modify: `src/pages/dossier.astro` (front management)

- [ ] **Step 1 — pièces** : remplacer `const canAct = ROLE==='admin' && D.statut==='SOU';` (ligne 329) par `const canAct = !!D.can_control_pieces;`. Les sous-conditions per-pièce (uploaded→Vérifier, verified→verrouillé, lignes 342-347) INCHANGÉES.
- [ ] **Step 2 — paiements** : remplacer les `ROLE==='admin'` du panneau paiements (lignes 399/404) par `D.can_manage_payments`. Logique per-paiement (pending) inchangée.
- [ ] **Step 3 — renderActions pur renderer** : introduire `const A = D.available_actions || [];` et `const can = k => A.includes(k);`. Remplacer chaque garde `ROLE==='...' && S==='...'` par `can('<clé>')` (le statut/condition est déjà dans available_actions). Ex : « Accepter l'admission » → `if(can('accept_admission'))` ; « Mettre en étude » → `if(can('start_review'))` ; « Admettre » → `if(can('mark_admissible'))` ; « Refuser (ADM) » et « Refuser… (ETU) » → `if(can('refuse'))` ; etc. Les libellés/modales/handlers INCHANGÉS. Le titre du panneau : neutre (« Actions ») ou dérivé du 1er rôle détenu — plus de `{admin,resp,dir}[ROLE]` (fin de « undefined »).
- [ ] **Step 4 — SM / vide** : si `A.length===0 && !D.can_control_pieces && !D.can_manage_payments`, afficher un message clair : « Aucune action de workflow pour votre profil sur ce dossier (dans l'état actuel). » — jamais « undefined », aucun bouton fabriqué. (`uxRole` peut rester pour la NAV/visibilité de sections dans shell.js — hors panneau d'action.)
- [ ] **Step 5 — build + design-audit** : `npm run build` vert ; frontend-design-audit sur dossier.astro ; commit `feat(progression): dossier.astro pur renderer (available_actions)`.

### Task 5 : Preuve recette bundle live (GP1-GP6) + rapport → gate

**Files:**
- Create: `admission/tests/e2e/e2e_progression.mjs`

- [ ] **Step 1** : e2e mint_session AS chaque vrai rôle (admin/resp/dir + SM + multi) ; fixtures par CHEMIN MÉTIER (SOU/ETU/ADM/ACO/ACC) ; charger /dossier?id=… ; asserter les boutons rendus == available_actions attendus. Assertions sur **style calculé** (leçon V-LEARN-HIDDEN-16).
- [ ] **Step 2** : cas clés — Resp au retour SOU voit verify+start_review+reject+complément (GP2) ; Dir à ETU voit mark_admissible (GP3) ; Dir à ADM voit Accepter, Resp à ADM ne le voit PAS (GP4) ; SM message, 0 bouton (GP5) ; chaque bouton montré → l'appel back réussit le gate de rôle (GP6).
- [ ] **Step 3** : déployer back (branche) + front Worker recette ; run e2e live ; captures relues.
- [ ] **Step 4** : rapport standardisé (matrice GP1 + GP2-GP7) → gate. Merge back+front sur main + recette re-alignée après gate.

---

## Self-Review

1. **Spec coverage** : les 3 signaux → available_actions (décisions) + can_control_pieces + start_review@SOU (Task 1/2/4) ✓ ; union hiérarchie (roles_at_or_above dans `_authorized`) ✓ ; SM message (Task 4 step 4) ✓ ; source unique + anti-dérive (Task 3) ✓ ; séparation préservée (`accept_admission` = Direction, testé) ✓ ; candidat /suivi non touché (write-set) ✓ ; enforcement only_for intouché ✓.
2. **Placeholders** : aucun — code complet fourni pour `_actions.py`, l'edit get_dossier, les edits front.
3. **Type consistency** : clés d'action = noms d'endpoint, identiques registre↔front↔tests ; `available_actions`/`can_control_pieces`/`can_manage_payments` signatures identiques partout.

## Points soumis à ratification (PC1)

1. **Anti-dérive par matrice de cohérence** (recommandé) vs endpoints refactorés pour consommer le registre. Je recommande la matrice (write-set « only_for intouché » respecté littéralement ; dérive verrouillée par test permanent ; risque minimal). Alternative plus lourde : chaque garde appelle `_role_for_action` — vraie source unique d'appel, mais ~19 endpoints touchés (risque régression).
2. **Périmètre paiements** : j'inclus `can_manage_payments` (même désync `ROLE==='admin'` au panneau paiements) pour un front 100 % sans rôle codé. Confirmer l'inclusion (sinon je le retire, hors périmètre strict).
3. **Titre du panneau** : neutre « Actions » (je retire `{admin,resp,dir}[ROLE]`).
