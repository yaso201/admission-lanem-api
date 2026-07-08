"""FIX-PROGRESSION — SOURCE UNIQUE de la disponibilité d'action staff (Option A élargi).

Le registre `_ACTION_RULES` déclare, par action, le rôle de BASE requis à l'état courant
(statut + condition métier). `available_actions` applique la hiérarchie ASCENDANTE
(`roles_at_or_above`, FIX-ROLES-HIERARCHIE) → un supérieur reçoit l'union de ce qu'il peut.
Les endpoints gardent leur `frappe.only_for(...)` (ENFORCEMENT intouché) ; ici = UX pure
(montrer/masquer). Un bug de disponibilité = bug UX, jamais un trou de sécurité.

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


# Chaque règle : (applicant, is_prepa) -> rôle de BASE requis MAINTENANT, ou None si l'action
# n'est pas applicable (statut/condition métier). Le rôle de base est ensuite étendu vers le
# HAUT par roles_at_or_above (ascendant). Les clés = noms d'endpoint (vocabulaire canonique).
_ACTION_RULES = {
    "start_review":          lambda a, p: "Admission Administratif" if a.status == "SOU" else None,
    "notify_pieces_recap":   lambda a, p: "Admission Administratif" if a.status == "SOU" else None,
    "reject_dossier":        lambda a, p: "Admission Administratif" if a.status == "SOU" else None,
    "reopen_dossier":        lambda a, p: "Admission Administratif" if a.status == "REJ" else None,
    "request_complement":    lambda a, p: "Admission Administratif" if a.status == "SOU"
                                          else ("Admission Responsable" if a.status == "ETU" else None),
    "verify_bac_diploma":    lambda a, p: "Admission Administratif" if a.status == "ACO" and not a.bac_verified else None,
    "saisir_note_concours":  lambda a, p: "Admission Administratif" if a.status == "ETU" and p and not a.notes_validated else None,
    "valider_notes_concours":lambda a, p: "Admission Responsable" if a.status == "ETU" and p and a.notes_concours and not a.notes_validated else None,
    "propose_scholarships":  lambda a, p: "Admission Responsable" if a.status in ("ETU", "ATT") and _has_requested(a) else None,
    "set_waitlist_rank":     lambda a, p: "Admission Responsable" if a.status == "ATT" else None,
    "mark_admissible":       lambda a, p: "Admission Responsable" if a.status in ("ETU", "ATT") else None,
    "waitlist":              lambda a, p: "Admission Responsable" if a.status == "ETU" else None,
    "refuse":                lambda a, p: "Admission Responsable" if a.status == "ETU"
                                          else ("Admission Direction" if a.status == "ADM" else None),
    "conditional_admission": lambda a, p: "Admission Responsable" if a.status == "ETU" and a.conditionnel else None,
    "accept_admission":      lambda a, p: "Admission Direction" if a.status == "ADM" else None,
    "lift_condition":        lambda a, p: "Admission Direction" if a.status == "ACO" else None,
    "refuse_condition":      lambda a, p: "Admission Direction" if a.status == "ACO" else None,
    "enroll":                lambda a, p: "Admission Direction" if a.status == "ACC" else None,
    "withdraw":              lambda a, p: "Admission Administratif" if a.status in _WITHDRAW_STATES else None,
}


def _authorized(need, roles):
    """Le jeu de rôles détenus couvre-t-il le rôle de base requis (ascendant) ?"""
    return bool(need and set(roles) & set(roles_at_or_above(need)))


def available_actions(applicant, roles, *, is_prepa):
    """Liste des clés d'action que `roles` peut exécuter sur `applicant` à son état courant.
    Dérivé du registre — même déclaration d'autorisation que les gardes (source unique)."""
    roles = roles or []
    return [key for key, rule in _ACTION_RULES.items()
            if _authorized(rule(applicant, is_prepa), roles)]


def can_control_pieces(applicant, roles):
    """Contrôle documentaire (verify/reject/require/waive/reset) : garde back `_resolve_piece_sou`
    = CONFIRM_ROLES (Administratif ⊆ ascendant) + statut SOU. Le front garde ses sous-conditions
    per-pièce (uploaded → vérifiable, verified → verrouillée)."""
    return applicant.status == "SOU" and _authorized("Admission Administratif", roles or [])


def can_manage_payments(applicant, roles):
    """Confirmation/initiation de paiement : garde back CONFIRM_ROLES + dossier non clos
    (PAYMENT_FORBIDDEN_STATES). Le front garde sa logique per-paiement (pending)."""
    return applicant.status not in _PAYMENT_FORBIDDEN and _authorized("Admission Administratif", roles or [])
