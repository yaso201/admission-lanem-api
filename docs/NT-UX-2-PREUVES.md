# NT-UX-2 — « Confirmer le paiement » réservé à l'Administratif : preuves

> Mandat DEC-L (recon courte → pause unique → exécution → rapport). **Arrêt au push · fusion/déploiement
> à l'architecte.** SHA constatés après CAL-DEL + DS-CLEAN : **back `28df720`** · **management `e89aa7d`**.
> Branches `mandat/nt-ux-2`. **DEC-N option A** : la séparation des tâches est conservée (la Direction ne
> confirme pas les paiements, CADRAGE §3) — **le correctif est UX seul**.

## La cause (recon 2)

Le workflow (`patches/v1_0/create_admission_workflow.py:47`) déclare **`SOP · Confirm Payment · SOU ·
Admission Administratif`** — Administratif SEUL. Le chemin staff « confirmer offline » fait
`applicant.save()` (`apply_confirmed_payment_cascade`, transitionne **BRO/SOP → SOU**) → `validate_workflow`
→ un rôle non-Administratif est **rejeté APRÈS saisie** (`Workflow State transition not allowed from SOP
to SOU`). La Direction en est absente **par conception**, pas par oubli.

Mais le bouton s'affichait actif : `dossier.astro` le rendait dès `D.can_manage_payments`, et
`can_manage_payments` (`_actions.py:226`) est en mode **ASCENDANT** (`_authorized(("Administratif",
_ASC))`) → Direction/Responsable le passent. **Actif côté app-gate, rejeté côté workflow** = le patron
« actif puis rejeté » qu'OBS/NT-UX traquent.

## Le balayage des 22 transitions (recon 3 — le vrai livrable, DEC-C)

| Famille workflow | Rôle workflow | Reflet front (`_ACTION_RULES`) | Aligné ? |
|---|---|---|---|
| Opérationnel (Start Review, Request Complement, Reject, Reopen, Withdraw) | ASCENDANT | `_ASC` | ✅ |
| Maker (Mark Absent/Admissible, Waitlist, Conditional, Refuse@ETU, Transfer) | EXACT Responsable | `_EXA` Responsable | ✅ |
| Checker (Accept, Refuse@ADM, Lift/Refuse Condition, Enroll) | EXACT Direction | `_EXA` Direction | ✅ |
| **Paiement (Confirm Payment SOP→SOU, Confirm Online BRO→SOU)** | **EXACT Administratif** | **hors `_ACTION_RULES` — `can_manage_payments` (ASC)** | ❌ |

**La classe a UN seul membre : la confirmation de paiement.** Toutes les décisions d'état sont déjà
alignées (NT-UX / `hybrid_workflow_roles`). Les helpers hors registre : `can_control_pieces` (SOU→SOU)
ne déclenche **aucune** transition de statut → aucun rejet workflow. **Absence d'autre cas prouvée.**

## Le correctif (DEC-N, UX seul) + respect strict des frontières

**`staff.py` est INTERDIT** → impossible d'ajouter un champ à `get_dossier`. La condition de rôle-workflow
**voyage donc sur le `blocked_actions` EXISTANT** (que `get_dossier` sert déjà) — forme inchangée, une
entrée en plus. C'est exactement ce que le mandat suggérait (« blocked_actions porte les conditions de
rôle-workflow au même titre que les conditions d'état »).

- **Back `_actions.py`** (voie A — miroir, retenue) : `_CONFIRM_TRANSITION_STATES = {BRO, SOP}` (miroir
  du cascade + du workflow) ; `blocked_actions` émet, pour un rôle **non EXACT-Administratif** à ces
  états, `{action:"confirm_payment", actor:"Administratif", code:"RESERVED_TO_ADMINISTRATIF",
  reason:"Réservé à l'Administratif"}`. **Aucune garde, aucun workflow, aucun endpoint touchés.**
- **Front `dossier.astro`** : le bouton « Confirmer » de la ligne de paiement devient à **trois états** —
  bloc `confirm_payment` présent → **grisé + « Réservé à l'Administratif — Administratif »** (aucun
  `data-confirm` → pas de modale, pas de justificatif téléversé pour rien) · sinon `can_manage_payments`
  → actif (inchangé) · sinon « En attente ». Le bloc `confirm_payment` est **exclu du panneau d'actions**
  (rendu à la ligne de paiement, pas en doublon).

### Signalement (exigence architecte) : `can_manage_payments` a d'autres consommateurs
Il est lu par **`test_nt_s.py:148`**, **`test_available_actions.py`** (qui épinglent son comportement
ASC+états) **et** par le bouton **« Initier en ligne »** (`dossier.astro:511`). Le muter aurait cassé
ces trois-là — et « Initier en ligne » ne subit **aucun** rejet workflow (l'initiation ne transitionne
pas). **Donc je ne l'ai PAS muté** : j'ai **ajouté** la condition de rôle sur `blocked_actions`. Le
dédoublement conceptuel (pertinence large `can_manage_payments` vs autorisation EXACT du confirm) est
ainsi obtenu **sans régression**.

### Forme de la nouvelle entrée `blocked_actions` (pour le sous-lot b de CONTRAT-1)
`staff.get_dossier` n'a pas de schéma CONTRAT-1 (différé). La forme de `blocked_actions[]` est
**inchangée** — `{action, actor, code, reason}` — seule une **nouvelle valeur d'`action`
(`"confirm_payment"`)** et un **nouveau `code` (`"RESERVED_TO_ADMINISTRATIF"`)** apparaissent. **Ajout
strictement additif** : aucun champ existant renommé ou reformé. À inscrire au futur schéma.

## Preuves (check-list de sortie)

1. **Direction : grisé avec « Réservé à l'Administratif »** — `test_nt_ux_2` (back, 7/7) + test front
   (3/3) : le rendu Direction contient `pay-reserved` + `disabled` + la raison, et **PAS de `data-confirm`**
   (plus de modale, plus de justificatif perdu).
2. **Administratif strictement inchangé** — `test_administratif_not_blocked_strictly_unchanged` (aucun
   bloc) ; front : bouton actif `data-confirm` identique. `test_sysmgr_not_blocked` (super-admin confirme).
3. **Balayage 22 transitions** : ci-dessus, une classe à un membre, absence d'autre cas prouvée.
   `test_no_block_outside_confirm_transition_states` (ACC/ETU/SOU/ATT/INS → aucun bloc).
4. **Aucune garde/workflow modifiés** — `git diff` : back = `_actions.py` + `test_nt_ux_2.py` + docs ;
   management = `dossier.astro` + docs. `staff.py`, le workflow, les gardes NT-S, `calendar.py` **intacts**.
5. **Non-régression** : suite back **`Ran 1167 tests — OK`** (baseline **1160** + 7 nouveaux) ;
   **CONTRAT-1 vert** (inclus) ; **build management propre** ; test front (node) 3/3.

## Extension DS-CLEAN (accordée) — texte de `/accessibilite`
La NC-1 étant corrigée par DS-CLEAN, la puce de `src/pages/accessibilite.astro` passe en **« Corrigé le
18 août 2026 »** (worktree applicant séparé `mandat/ds-clean-a11y`, write-set étendu **strictement** à
cette page pour ce texte).
