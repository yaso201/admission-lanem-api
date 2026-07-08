"""FIX-PROGRESSION — SOURCE UNIQUE de la disponibilité d'action staff (Option A élargi).

Le registre `_ACTION_RULES` déclare, par action, le rôle de BASE requis à l'état courant
(statut + condition métier). `available_actions` applique la hiérarchie ASCENDANTE
(`roles_at_or_above`, FIX-ROLES-HIERARCHIE) → un supérieur reçoit l'union de ce qu'il peut.
Les endpoints gardent leur `frappe.only_for(...)` (ENFORCEMENT intouché) ; ici = UX pure
(montrer/masquer). Un bug de disponibilité = bug UX, jamais un trou de sécurité.

GP6 : une action n'est proposée QUE si elle passerait aussi les gardes MÉTIER de l'endpoint
(pièces vérifiées pour start_review, pièces traitées pour notify, bac vérifié pour lift_condition,
frais 2 payé + consentement pour enroll) → aucun bouton montré n'est rejeté sur motif métier.
Le contexte métier est calculé par `action_context` (source unique, réutilisée par get_dossier
ET la matrice de cohérence).

Anti-dérive : la matrice de cohérence (tests, AS chaque vrai rôle) verrouille registre ↔ gardes.
`Admission SM` est ORTHOGONAL (hors hiérarchie workflow) → actions workflow vides pour un SM pur.
"""

import json

from admission.api.permissions import roles_at_or_above

# États où le désistement (withdraw) est offert (miroir WITHDRAW_STATES / renderActions front).
_WITHDRAW_STATES = {"BRO", "SOP", "SOU", "ETU", "ATT", "ADM", "ACO", "ACC"}
# Dossiers clos : aucune gestion de paiement (miroir PAYMENT_FORBIDDEN_STATES back).
_PAYMENT_FORBIDDEN = {"REF", "REJ", "DES", "INS"}


def _has_requested(applicant):
    """Le candidat a-t-il demandé au moins une bourse ? (condition de propose_scholarships)."""
    try:
        return bool(json.loads(applicant.requested_scholarships or "[]"))
    except (ValueError, TypeError):
        return False


# FIX-ROLES-HYBRIDE-WORKFLOW — chaque règle : (applicant, is_prepa, ctx) -> (rôle_base, MODE) ou None.
#   _ASC (opérationnel) : étendu vers le HAUT (roles_at_or_above) → continuité (un supérieur peut).
#   _EXA (décision/validation) : rôle EXACT + SysMgr → maker-checker (SoD ; la Direction ne DÉCIDE pas).
# Ce MÊME modèle est reflété par only_for (couche 1a) ET le Workflow (couche 1b) → concordance :
# aucun bouton montré n'échoue au clic. Clés = noms d'endpoint. `ctx` = préconditions MÉTIER
# (action_context : pieces_verified, notify_ready, enrollment_ready ; défaut True si absent).
def _c(ctx, key):
    return (ctx or {}).get(key, True)


_ASC, _EXA = "ascending", "exact"

_ACTION_RULES = {
    # ── opérationnel : ASCENDANT (continuité — un supérieur peut) ──
    "start_review":          lambda a, p, c: ("Admission Administratif", _ASC) if a.status == "SOU" and _c(c, "pieces_verified") else None,
    "notify_pieces_recap":   lambda a, p, c: ("Admission Administratif", _ASC) if a.status == "SOU" and _c(c, "notify_ready") else None,
    "reject_dossier":        lambda a, p, c: ("Admission Administratif", _ASC) if a.status == "SOU" else None,
    "reopen_dossier":        lambda a, p, c: ("Admission Administratif", _ASC) if a.status == "REJ" else None,
    "request_complement":    lambda a, p, c: ("Admission Administratif", _ASC) if a.status == "SOU"
                                             else (("Admission Responsable", _ASC) if a.status == "ETU" else None),
    "verify_bac_diploma":    lambda a, p, c: ("Admission Administratif", _ASC) if a.status == "ACO" and not a.bac_verified else None,
    "saisir_note_concours":  lambda a, p, c: ("Admission Administratif", _ASC) if a.status == "ETU" and p and not a.notes_validated else None,
    "withdraw":              lambda a, p, c: ("Admission Administratif", _ASC) if a.status in _WITHDRAW_STATES else None,
    # ── décision « maker » : EXACT Responsable (Direction EXCLUE — SoD) ──
    "valider_notes_concours":lambda a, p, c: ("Admission Responsable", _EXA) if a.status == "ETU" and p and a.notes_concours and not a.notes_validated else None,
    "propose_scholarships":  lambda a, p, c: ("Admission Responsable", _EXA) if a.status in ("ETU", "ATT") and _has_requested(a) else None,
    "set_waitlist_rank":     lambda a, p, c: ("Admission Responsable", _EXA) if a.status == "ATT" else None,
    "mark_admissible":       lambda a, p, c: ("Admission Responsable", _EXA) if a.status in ("ETU", "ATT") else None,
    "waitlist":              lambda a, p, c: ("Admission Responsable", _EXA) if a.status == "ETU" else None,
    "conditional_admission": lambda a, p, c: ("Admission Responsable", _EXA) if a.status == "ETU" and a.conditionnel else None,
    "refuse":                lambda a, p, c: ("Admission Responsable", _EXA) if a.status == "ETU"
                                             else (("Admission Direction", _EXA) if a.status == "ADM" else None),
    # ── validation « checker » : EXACT Direction ──
    "accept_admission":      lambda a, p, c: ("Admission Direction", _EXA) if a.status == "ADM" else None,
    "lift_condition":        lambda a, p, c: ("Admission Direction", _EXA) if a.status == "ACO" and a.bac_verified else None,
    "refuse_condition":      lambda a, p, c: ("Admission Direction", _EXA) if a.status == "ACO" else None,
    "enroll":                lambda a, p, c: ("Admission Direction", _EXA) if a.status == "ACC" and _c(c, "enrollment_ready") else None,
}


def _authorized(rule_out, roles):
    """Applique le MODE : ascendant (roles_at_or_above) OU exact ({base, SysMgr})."""
    if not rule_out:
        return False
    base, mode = rule_out
    allowed = roles_at_or_above(base) if mode == _ASC else (base, "System Manager")
    return bool(set(roles) & set(allowed))


def _enrollment_ready(applicant):
    """Gate MÉTIER d'enroll (miroir EXACT de l'endpoint) : frais 2 payé + consentement DATA_TRANSFER.
    Seulement pertinent à ACC ; renvoie False sinon (évite toute requête inutile)."""
    if applicant.status != "ACC":
        return False
    from admission.api.public import _check_enrollment_fee_paid
    from admission.api.legal import _require_consent_record
    try:
        _check_enrollment_fee_paid(applicant.name)
        _require_consent_record(applicant.name, "DATA_TRANSFER")
        return True
    except Exception:
        return False


def action_context(applicant):
    """Contexte MÉTIER (source unique) consommé par available_actions — reflète les gardes des
    endpoints pour que la disponibilité UX == ce que le back accepterait (GP6). Import paresseux
    (acyclique : _actions ne doit pas être importé au chargement de public/legal)."""
    from admission.api.public import pieces_requises_non_verifiees, notify_pieces_blocked
    return {
        "pieces_verified": not pieces_requises_non_verifiees(applicant),   # start_review
        "notify_ready": not notify_pieces_blocked(applicant),              # notify_pieces_recap
        "enrollment_ready": _enrollment_ready(applicant),                  # enroll
    }


def available_actions(applicant, roles, *, is_prepa, ctx=None):
    """Liste des clés d'action que `roles` peut exécuter sur `applicant` à son état courant.
    Dérivé du registre — même déclaration d'autorisation (rôle+statut+métier) que les gardes."""
    roles = roles or []
    return [key for key, rule in _ACTION_RULES.items()
            if _authorized(rule(applicant, is_prepa, ctx), roles)]


def can_control_pieces(applicant, roles):
    """Contrôle documentaire (verify/reject/require/waive/reset) : garde back `_resolve_piece_sou`
    = CONFIRM_ROLES (Administratif ⊆ ascendant) + statut SOU. Le front garde ses sous-conditions
    per-pièce (uploaded → vérifiable, verified → verrouillée)."""
    return applicant.status == "SOU" and _authorized(("Admission Administratif", _ASC), roles or [])


def can_manage_payments(applicant, roles):
    """Confirmation/initiation de paiement : garde back CONFIRM_ROLES + dossier non clos
    (PAYMENT_FORBIDDEN_STATES). Le front garde sa logique per-paiement (pending)."""
    return applicant.status not in _PAYMENT_FORBIDDEN and _authorized(("Admission Administratif", _ASC), roles or [])
