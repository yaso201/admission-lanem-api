# FIX-ROLES-HYBRIDE-WORKFLOW — Plan PC1

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:executing-plans. Steps use checkbox (`- [ ]`).

**Goal:** Aligner les DEUX couches d'autorisation (`only_for` endpoint + Frappe Workflow) sur un **modèle hybride** — opérationnel ASCENDANT, décisions/validations EXACTES (maker-checker/SoD) — pour que `available_actions` soit correct et FIX-PROGRESSION mergeable.

**Architecture:** Le registre `_ACTION_RULES` porte un **mode** par action (`ascending`|`exact`) ; `available_actions` en dérive (roles_at_or_above si ascending, `{base, SysMgr}` si exact). `only_for` (couche 1a) et le Workflow (couche 1b, multi-lignes — validé sur v15.103.2) reflètent le MÊME modèle → concordance. La matrice teste les 2 couches **end-to-end** en exécutant les vraies transitions (WorkflowPermissionError = rejet).

**Tech Stack:** Frappe 15.103.2 (Python) ; Astro/JS (front management) ; `bench run-tests` + e2e puppeteer recette.

## Global Constraints

- **Invariant de concordance** : `only_for` ET Workflow reflètent le même modèle hybride. 0 bouton montré rejeté par une couche ; 0 action autorisée par une couche et bloquée par l'autre.
- **Maker-checker (SoD, ISO 27001 A.5.3)** : la Direction NE PEUT PAS `mark_admissible` (maker) ; elle ne fait que `accept_admission` (checker). Prouver Directeur **REJETÉ** sur mark_admissible (les 2 couches).
- **Opérationnel ascendant** : un supérieur PEUT les transitions opérationnelles (start_review…). Prouver Resp/Dir font start_review (2 couches).
- **SysMgr** = break-glass, dans TOUS les sets, sur les 2 couches.
- **Argent** : `enroll` reste Direction-exact ; garde argent (409) **intouchée**.
- **Candidat /suivi inchangé** ; **actions non-transition pièces (verify/reject/require/waive/reset)** déjà ascendantes, inchangées.
- **Tester AS le vrai rôle** (`frappe.set_user`, `in_test=False`), jamais Administrator ; matrice = **exécuter les transitions**, jamais `db.set_value` du statut CIBLE.
- Base : branches `fix/progression` (back `f294a71`, front `0b39748`). Frappe multi-ligne Workflow **fonctionne** (v15.103.2 : `get_transitions` itère toutes les lignes `allowed in roles` — #14862 ne s'applique pas au chemin `save()`→`validate_workflow`).
- Transitions candidat/paiement (BRO→SOP, BRO→SOU, SOP→SOU, INC→SOU) = **db.set_value** → **bypassent** le Workflow → inchangées.

---

## Carte transition × mode (À VALIDER EN PC1)

Colonne « save-path » = la transition passe par `applicant.save()` (staff) → Workflow. Les autres (db.set_value) bypassent.

| Transition Workflow | Endpoint | save-path ? | Mode | Rôles (2 couches) |
|---|---|---|---|---|
| SOU→ETU Start Review | start_review | ✅ | **ascending** (Admin) | Admin, Resp, Dir, SysMgr |
| SOU→INC Request Complement | request_complement@SOU | ✅ | **ascending** (Admin) | Admin, Resp, Dir, SysMgr |
| ETU→INC Request Complement | request_complement@ETU | ✅ | **ascending** (Resp) | Resp, Dir, SysMgr |
| SOU→REJ Reject Documentary | reject_dossier | ✅ | **ascending** (Admin) | Admin, Resp, Dir, SysMgr |
| REJ→SOU Reopen | reopen_dossier | ✅ | **ascending** (Admin) | Admin, Resp, Dir, SysMgr |
| {BRO,SOP,SOU,ETU,ATT,ADM,ACO,ACC}→DES Withdraw | withdraw | ✅ | **ascending** (Admin) | Admin, Resp, Dir, SysMgr (×8 états) |
| ETU→ATT Waitlist | waitlist | ✅ | **exact** (Resp) | Resp, SysMgr |
| ETU→ADM Mark Admissible | mark_admissible | ✅ | **exact** (Resp) | Resp, SysMgr |
| ATT→ADM Mark Admissible | mark_admissible | ✅ | **exact** (Resp) | Resp, SysMgr |
| ETU→ACO Conditional Admission | conditional_admission | ✅ | **exact** (Resp) | Resp, SysMgr |
| ETU→REF Refuse | refuse@ETU | ✅ | **exact** (Resp) | Resp, SysMgr |
| ADM→ACC Accept Admission | accept_admission | ✅ | **exact** (Dir) | Dir, SysMgr |
| ADM→REF Refuse | refuse@ADM | ✅ | **exact** (Dir) | Dir, SysMgr |
| ACO→ACC Lift Condition | lift_condition | ✅ | **exact** (Dir) | Dir, SysMgr |
| ACO→REF Refuse | refuse_condition | ✅ | **exact** (Dir) | Dir, SysMgr |
| ACC→INS Enroll | enroll | ✅ | **exact** (Dir) | Dir, SysMgr |
| BRO→SOP / BRO→SOU / SOP→SOU / INC→SOU | declare/webhook/confirm/resubmit | ❌ db.set_value | — | inchangé (bypass) |

**Actions NON-transition** (pas de changement de statut → only_for seul, pas de Workflow) :
| Action | Mode | only_for cible |
|---|---|---|
| verify_bac_diploma, saisir_note_concours, notify_pieces_recap | ascending (Admin) | ADMIN_UP (inchangé) |
| valider_notes_concours, propose_scholarships, set_waitlist_rank | **exact** (Resp) | **RESP_EXACT** (changement) |
| verify/reject/require/waive/reset_piece, confirm_offline_payment | ascending (Admin) | CONFIRM_ROLES=ADMIN_UP (inchangé) |

---

## File Structure

| Fichier | Responsabilité | Action |
|---|---|---|
| `admission/api/staff.py` | `RESP_EXACT` tier ; only_for des **makers** RESP_UP→RESP_EXACT (mark_admissible, waitlist, refuse@ETU, conditional, propose_scholarships, valider_notes, set_waitlist_rank) | Modify |
| `admission/api/_actions.py` | mode `ascending`/`exact` par action ; `_authorized` applique le mode ; available_actions dérive | Modify |
| `admission/patches/v1_0/create_admission_workflow.py` | `TRANSITIONS` hybride (multi-lignes ascendantes + SysMgr) — fresh installs | Modify |
| `admission/patches/v1_1/hybrid_workflow_roles.py` (NEW) | patch idempotent : appelle `_setup_workflow()` (reconfig sites existants) | Create |
| `admission/patches.txt` | enregistre le nouveau patch | Modify |
| `admission/tests/test_available_actions.py` | matrice **2 couches end-to-end** (exécute les transitions ; WorkflowPermissionError=REJECT) + tests mode | Modify |
| `admission/tests/test_roles_hierarchy.py` | RÉVISION : maker-checker (Dir REJETÉ sur mark_admissible) | Modify |
| front `dossier.astro` (fix/progression) | pur renderer (déjà fait) — available_actions désormais concordant | (inchangé) |
| `admission/tests/e2e/e2e_progression.mjs` | preuve recette 2-couches end-to-end AS vrais rôles (GH1-GH6) | Modify |
| garde argent (409), candidat /suivi, actions pièces | ❌ intouchés |

---

### Task 1 : only_for hybride (couche 1a) — makers en EXACT

**Files:** Modify `admission/api/staff.py` · Test `admission/tests/test_available_actions.py`

- [ ] **Step 1 — définir RESP_EXACT** (après les tiers existants, ~staff.py:52) :
```python
RESP_EXACT = ("Admission Responsable", "System Manager")   # maker EXACT (SoD : sans Direction)
```
- [ ] **Step 2 — basculer les makers RESP_UP→RESP_EXACT** (lignes exactes) : `mark_admissible` (244), `waitlist` (270), `refuse` branche ETU (310), `conditional_admission` (1079), `propose_scholarships` (775), `valider_notes_concours` (1242), `set_waitlist_rank` (1365). Remplacer `frappe.only_for(RESP_UP)` par `frappe.only_for(RESP_EXACT)` à CES lignes. **NE PAS** toucher `request_complement`@ETU (185, reste RESP_UP = opérationnel ascendant), ni les DIR_UP/ADMIN_UP.
- [ ] **Step 3 — test rouge→vert** : un test asserte qu'un Directeur (rôle exact Direction) est REJETÉ par only_for sur mark_admissible. (Détail : couvert par la matrice T4.)
- [ ] **Step 4 — commit** : `git commit -am "feat(hybride): only_for makers en Responsable-EXACT (SoD maker-checker)"`

### Task 2 : registre mode ascending/exact (source unique available_actions)

**Files:** Modify `admission/api/_actions.py` · Test `test_available_actions.py`

**Interfaces produced:** `available_actions(applicant, roles, *, is_prepa, ctx=None)` inchangé en signature ; comportement respecte le mode.

- [ ] **Step 1 — test rouge** : ajouter à `TestAvailableActions` :
```python
def test_mode_exact_maker_no_ascending(self):
    # Dir NE voit PAS les décisions maker (exact Responsable)
    self.assertNotIn("mark_admissible", available_actions(_a(status="ETU"), DIR, is_prepa=False))
    self.assertIn("mark_admissible", available_actions(_a(status="ETU"), RESP, is_prepa=False))

def test_mode_ascending_operational(self):
    # Dir/Resp voient start_review (opérationnel ascendant)
    self.assertIn("start_review", available_actions(_a(status="SOU"), DIR, is_prepa=False, ctx={"pieces_verified": True}))
    self.assertIn("start_review", available_actions(_a(status="SOU"), RESP, is_prepa=False, ctx={"pieces_verified": True}))

def test_refuse_exact_by_state(self):
    self.assertIn("refuse", available_actions(_a(status="ETU"), RESP, is_prepa=False))    # maker Resp exact
    self.assertNotIn("refuse", available_actions(_a(status="ETU"), DIR, is_prepa=False))  # Dir PAS de refuse ETU (exact Resp)
    self.assertIn("refuse", available_actions(_a(status="ADM"), DIR, is_prepa=False))     # validation Dir exact
    self.assertNotIn("refuse", available_actions(_a(status="ADM"), RESP, is_prepa=False))
```
- [ ] **Step 2 — run rouge** (Dir voit encore mark_admissible via l'ancien ascending).
- [ ] **Step 3 — impl** : les règles renvoient `(base_role, mode)`. Remplacer le cœur de `_actions.py` :
```python
_ASC, _EXA = "ascending", "exact"
# règle -> (base_role, mode) ou None
_ACTION_RULES = {
    "start_review":          lambda a,p,c: ("Admission Administratif", _ASC) if a.status=="SOU" and _c(c,"pieces_verified") else None,
    "notify_pieces_recap":   lambda a,p,c: ("Admission Administratif", _ASC) if a.status=="SOU" and _c(c,"notify_ready") else None,
    "reject_dossier":        lambda a,p,c: ("Admission Administratif", _ASC) if a.status=="SOU" else None,
    "reopen_dossier":        lambda a,p,c: ("Admission Administratif", _ASC) if a.status=="REJ" else None,
    "request_complement":    lambda a,p,c: ("Admission Administratif", _ASC) if a.status=="SOU"
                                           else (("Admission Responsable", _ASC) if a.status=="ETU" else None),
    "verify_bac_diploma":    lambda a,p,c: ("Admission Administratif", _ASC) if a.status=="ACO" and not a.bac_verified else None,
    "saisir_note_concours":  lambda a,p,c: ("Admission Administratif", _ASC) if a.status=="ETU" and p and not a.notes_validated else None,
    "valider_notes_concours":lambda a,p,c: ("Admission Responsable", _EXA) if a.status=="ETU" and p and a.notes_concours and not a.notes_validated else None,
    "propose_scholarships":  lambda a,p,c: ("Admission Responsable", _EXA) if a.status in ("ETU","ATT") and _has_requested(a) else None,
    "set_waitlist_rank":     lambda a,p,c: ("Admission Responsable", _EXA) if a.status=="ATT" else None,
    "mark_admissible":       lambda a,p,c: ("Admission Responsable", _EXA) if a.status in ("ETU","ATT") else None,
    "waitlist":              lambda a,p,c: ("Admission Responsable", _EXA) if a.status=="ETU" else None,
    "refuse":                lambda a,p,c: ("Admission Responsable", _EXA) if a.status=="ETU"
                                           else (("Admission Direction", _EXA) if a.status=="ADM" else None),
    "conditional_admission": lambda a,p,c: ("Admission Responsable", _EXA) if a.status=="ETU" and a.conditionnel else None,
    "accept_admission":      lambda a,p,c: ("Admission Direction", _EXA) if a.status=="ADM" else None,
    "lift_condition":        lambda a,p,c: ("Admission Direction", _EXA) if a.status=="ACO" and a.bac_verified else None,
    "refuse_condition":      lambda a,p,c: ("Admission Direction", _EXA) if a.status=="ACO" else None,
    "enroll":                lambda a,p,c: ("Admission Direction", _EXA) if a.status=="ACC" and _c(c,"enrollment_ready") else None,
    "withdraw":              lambda a,p,c: ("Admission Administratif", _ASC) if a.status in _WITHDRAW_STATES else None,
}

def _authorized(rule_out, roles):
    if not rule_out: return False
    base, mode = rule_out
    allowed = roles_at_or_above(base) if mode == _ASC else (base, "System Manager")
    return bool(set(roles) & set(allowed))

def available_actions(applicant, roles, *, is_prepa, ctx=None):
    roles = roles or []
    return [k for k, rule in _ACTION_RULES.items() if _authorized(rule(applicant, is_prepa, ctx), roles)]
```
(`can_control_pieces`/`can_manage_payments` inchangés — ascendants.)
- [ ] **Step 4 — run vert.**
- [ ] **Step 5 — commit** : `feat(hybride): registre _actions mode ascending/exact (available_actions concordant)`

### Task 3 : Workflow hybride (couche 1b) — multi-lignes + SysMgr

**Files:** Modify `patches/v1_0/create_admission_workflow.py` · Create `patches/v1_1/hybrid_workflow_roles.py` · Modify `patches.txt`

- [ ] **Step 1 — réécrire `TRANSITIONS`** (create_admission_workflow.py:26-54) en HYBRIDE. Générer les lignes par compréhension pour la lisibilité :
```python
_ASCENDING = ["Admission Administratif", "Admission Responsable", "Admission Direction", "System Manager"]
_ASC_FROM_RESP = ["Admission Responsable", "Admission Direction", "System Manager"]
_MAKER = ["Admission Responsable", "System Manager"]
_CHECKER = ["Admission Direction", "System Manager"]
_WITHDRAW_STATES = ["BRO", "SOP", "SOU", "ETU", "ATT", "ADM", "ACO", "ACC"]

def _rows():
    r = []
    # candidat/paiement (db.set_value → bypass ; conservés pour cohérence d'affichage desk, 1 ligne)
    r += [("BRO","Declare Offline Payment","SOP","Admission Administratif"),
          ("BRO","Confirm Online Payment","SOU","Admission Administratif"),
          ("SOP","Confirm Payment","SOU","Admission Administratif"),
          ("INC","Resubmit Complement","SOU","Admission Administratif")]
    def fan(state, action, nxt, roles): r.extend((state, action, nxt, role) for role in roles)
    # opérationnel ASCENDANT
    fan("SOU","Start Review","ETU", _ASCENDING)
    fan("SOU","Request Complement","INC", _ASCENDING)
    fan("ETU","Request Complement","INC", _ASC_FROM_RESP)
    fan("SOU","Reject Documentary","REJ", _ASCENDING)
    fan("REJ","Reopen","SOU", _ASCENDING)
    for s in _WITHDRAW_STATES: fan(s,"Withdraw","DES", _ASCENDING)
    # décision maker EXACT (Responsable)
    fan("ETU","Waitlist","ATT", _MAKER)
    fan("ETU","Mark Admissible","ADM", _MAKER)
    fan("ATT","Mark Admissible","ADM", _MAKER)
    fan("ETU","Conditional Admission","ACO", _MAKER)
    fan("ETU","Refuse","REF", _MAKER)
    # validation checker EXACT (Direction)
    fan("ADM","Accept Admission","ACC", _CHECKER)
    fan("ADM","Refuse","REF", _CHECKER)
    fan("ACO","Lift Condition","ACC", _CHECKER)
    fan("ACO","Refuse","REF", _CHECKER)
    fan("ACC","Enroll","INS", _CHECKER)
    return r

TRANSITIONS = _rows()
```
Le reste de `_setup_workflow` (append transitions avec `allowed=role`) est inchangé — il crée une ligne Workflow Transition par tuple. `System Manager` existe déjà (rôle core) ; le loop de création de rôles ignore les rôles existants.
- [ ] **Step 2 — nouveau patch** `patches/v1_1/hybrid_workflow_roles.py` :
```python
from admission.patches.v1_0.create_admission_workflow import _setup_workflow

def execute():
    """FIX-ROLES-HYBRIDE-WORKFLOW — reconfigure les transitions du Workflow existant vers le
    modèle hybride (opérationnel ascendant multi-lignes + décisions/validations exactes + SysMgr).
    Idempotent : _setup_workflow reconstruit states/transitions sur le Workflow en place."""
    _setup_workflow()
```
- [ ] **Step 3 — enregistrer** dans `admission/patches.txt` : ajouter `admission.patches.v1_1.hybrid_workflow_roles`.
- [ ] **Step 4 — migrer dev + vérifier** : `bench --site admission-dev.localhost migrate` ; puis vérifier via `bench execute` que la transition SOU→Start Review→ETU a bien 4 lignes (Admin/Resp/Dir/SysMgr) et ETU→Mark Admissible→ADM 2 lignes (Resp/SysMgr).
- [ ] **Step 5 — commit** : `feat(hybride): Workflow transitions hybrides (opérationnel ascendant + maker/checker exact + SysMgr)`

### Task 4 : matrice 2 couches END-TO-END (LE correctif de la leçon)

**Files:** Modify `admission/tests/test_available_actions.py` (TestCoherenceMatrix) · Modify `admission/tests/test_roles_hierarchy.py`

- [ ] **Step 1 — `_gate` attrape WorkflowPermissionError** : importer `from frappe.model.workflow import WorkflowPermissionError` et l'ajouter comme REJET AVANT le `except Exception`. Le décor doit **exécuter la vraie transition** : positionner l'état SOURCE via `db.set_value` (OK, ne masque pas — la validation Workflow se déclenche au `save()` de l'endpoint qui pose l'état CIBLE), puis invoquer l'endpoint. Nouveau `_gate` :
```python
from frappe.model.workflow import WorkflowPermissionError   # en tête
...
    def _gate(self, user, fn):
        frappe.set_user(user); frappe.flags.in_test = False
        try:
            try:
                r = fn(dossier_id=self.dossier)
                if isinstance(r, dict) and not r.get("ok") \
                        and (r.get("error") or {}).get("code") in self._REJECT_CODES:
                    return "REJECT"
                return "PASSED"
            except frappe.PermissionError:
                return "DENIED"          # couche 1a (only_for)
            except WorkflowPermissionError:
                return "REJECT"          # couche 1b (Workflow) — LA leçon : plus jamais PASSED
            except Exception:
                return "PASSED"          # échoue plus loin (args/motif), pas une garde
        finally:
            frappe.flags.in_test = True; frappe.set_user("Administrator"); frappe.db.rollback()
```
- [ ] **Step 2 — décor : Workflow actif + rôles réels** : le décor applicant existe déjà (status positionné par set_value) ; s'assurer que le Workflow est actif sur le site de test (le patch T3 l'a migré). La matrice invoque les vraies transitions (start_review, mark_admissible, refuse, accept_admission, enroll…). FORWARD (montré⇒PASSED) attrape désormais un blocage Workflow (REJECT) d'un bouton montré. REVERSE inchangé.
- [ ] **Step 3 — run** : rouge si une transition montrée est bloquée par le Workflow (concordance non atteinte) → ajuster jusqu'à concordance 2-couches.
- [ ] **Step 4 — réviser test_roles_hierarchy.py** : les assertions ascendantes des DÉCISIONS deviennent maker-checker. Remplacer GH1/GH2/GH3 pour mark_admissible : **Directeur REJETÉ** sur mark_admissible (DENIED via only_for RESP_EXACT) ; Responsable PASSED ; opérationnel (start_review) reste ascendant (Admin/Resp/Dir PASSED). Ajouter un cas Workflow end-to-end.
- [ ] **Step 5 — run vert + commit** : `test(hybride): matrice 2 couches end-to-end (WorkflowPermissionError=rejet) + maker-checker`

### Task 5 : front — vérifier (déjà pur renderer)

**Files:** (aucun changement attendu) `dossier.astro`

- [ ] **Step 1** : confirmer que `renderActions` lit `available_actions` (fait). available_actions étant désormais concordant, les boutons montrés fonctionnent. `npm run build` vert.
- [ ] **Step 2** : frontend-design-audit sur dossier.astro (déjà pur renderer, pas de nouveau markup).

### Task 6 : preuve recette 2-couches end-to-end (GH1-GH7) + rapport

**Files:** Modify `admission/tests/e2e/e2e_progression.mjs`

- [ ] **Step 1** — déployer back+front `fix/progression` (avec ces commits) sur recette (ssh migrate pour le patch Workflow + Worker). `mute_emails=1` temporaire (dette SMTP synchrone) pour bâtir les fixtures, **rétabli+vérifié+purgé** en fin (discipline éprouvée).
- [ ] **Step 2** — e2e AS chaque vrai rôle, **transitions exécutées** :
  - **GH1** Admin+Resp+Dir cliquent « Mettre en étude » (après vérif pièces) → dossier passe **ETU** (serveur) — opérationnel ascendant, 2 couches.
  - **GH2** Responsable clique « Admettre » → **ADM** ; **Directeur** : « Admettre » ABSENT du panneau (available_actions) ET appel direct `markAdmissible` → **rejeté** (only_for) — maker-checker/SoD.
  - **GH3** Direction clique « Accepter » → **ACC**, « Inscrire » → **INS** ; Responsable : absent/rejeté.
  - **GH4** chaque bouton montré **s'exécute** (aucun fantôme) — la transition se produit côté serveur.
  - **GH5** (unit) matrice 2-couches verte (T4).
  - **GH6** SM orthogonal (message clair) ; candidat /suivi inchangé ; argent 409 intact.
- [ ] **Step 3** — non-régression (test_completude, etc.) ; code-review ; captures relues (style calculé, V-LEARN-HIDDEN-16).
- [ ] **Step 4** — rapport GH1-GH7 → gate. Merge back+front sur main + re-déploie + confirme bundle. Dette SMTP (request_otp synchrone → enqueue) notée.

---

## Self-Review

1. **Spec coverage** : hybride 2 couches (T1 only_for + T3 Workflow) ✓ ; registre mode (T2) ✓ ; matrice end-to-end WorkflowPermissionError=rejet (T4) ✓ ; maker-checker Dir rejeté mark_admissible (T1+T3+T4+GH2) ✓ ; opérationnel ascendant (T2/T3/GH1) ✓ ; SysMgr 2 couches (T1 tiers + T3 rows) ✓ ; argent/candidat/pièces intouchés ✓ ; #14862 écarté (v15.103.2 multi-ligne OK, vérifié get_transitions) ✓.
2. **Placeholders** : code complet fourni (RESP_EXACT, registre mode, _authorized, _rows Workflow, patch, _gate). Aucun TODO.
3. **Type consistency** : clés d'action = noms d'endpoint, identiques registre/Workflow(action labels distincts mais mappés)/front/tests. `RESP_EXACT`/`_ASC`/`_EXA` cohérents.

## Points soumis à ratification (PC1)

1. **Carte transition × mode** (tableau ci-dessus) — surtout : `request_complement`@ETU = **ascendant** (Resp+Dir), `refuse` = maker@ETU / checker@ADM, `propose_scholarships`/`valider_notes`/`set_waitlist_rank` = **maker exact** (Responsable seul). Confirmer.
2. **Mécanisme Workflow** = **multi-lignes** (validé sur v15.103.2 : `get_transitions` itère toutes les lignes `allowed in roles` ; #14862 = chemin dropdown/`apply_workflow`, non le chemin `save()`→`validate_workflow` utilisé ici). Fallback condition/permissif NON retenu (multi-ligne reflète le modèle proprement). Confirmer.
3. **SysMgr sur couche 1b** : ajouté comme ligne Workflow sur CHAQUE transition save-path (concordance avec only_for qui l'inclut). Confirmer (sinon SysMgr resterait bloqué par le Workflow — discordance).
4. **Transitions candidat/paiement** (db.set_value) laissées à 1 ligne (bypass) — pas de SysMgr/ascendant car non gatées par le Workflow. Confirmer.
