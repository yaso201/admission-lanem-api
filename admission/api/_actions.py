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
# NT-S/DEC-C — bornes uniques des actes de paiement, consommées par les endpoints et l'UX.
_PAYMENT_STATES_BY_FEE_TYPE = {
    "application": ("BRO", "SOP", "SOU"),
    "competition": ("BRO", "SOP", "SOU"),
    "enrollment": ("ACC",),
}
_PAYMENT_MANAGE_STATES = frozenset(
    state for states in _PAYMENT_STATES_BY_FEE_TYPE.values() for state in states
)


def _has_requested(applicant):
    """Le candidat a-t-il demandé au moins une bourse ? (condition de propose_scholarships)."""
    try:
        return bool(json.loads(applicant.requested_scholarships or "[]"))
    except (ValueError, TypeError):
        return False


def payment_allowed_states(fee_type):
    """États autorisés pour le type de frais ; tuple vide pour un type inconnu."""
    return _PAYMENT_STATES_BY_FEE_TYPE.get(str(fee_type or "").strip().lower(), ())


def payment_state_allowed(status, fee_type):
    return status in payment_allowed_states(fee_type)


# FIX-ROLES-HYBRIDE-WORKFLOW — chaque règle : (applicant, is_prepa, ctx) -> (rôle_base, MODE) ou None.
#   _ASC (opérationnel) : étendu vers le HAUT (roles_at_or_above) → continuité (un supérieur peut).
#   _EXA (décision/validation) : rôle EXACT + SysMgr → maker-checker (SoD ; la Direction ne DÉCIDE pas).
# Ce MÊME modèle est reflété par only_for (couche 1a) ET le Workflow (couche 1b) → concordance :
# aucun bouton montré n'échoue au clic. Clés = noms d'endpoint. `ctx` = préconditions MÉTIER
# (action_context : pieces_verified, notify_ready, enrollment_ready ; défaut True si absent).
def _c(ctx, key):
    return (ctx or {}).get(key, True)


def _notes_ok(applicant, is_prepa):
    """NT-UX — miroir de `_require_validated_notes_if_prepa` (staff.py) : un dossier Prépa exige des
    notes VALIDÉES avant toute décision ; un dossier Licence n'est jamais bloqué. Dérivé de (a, p),
    pas du contexte (les deux valeurs sont déjà passées aux règles)."""
    return (not is_prepa) or bool(applicant.notes_validated)


def _diploma_uploaded(applicant):
    """NT-UX — miroir de `_has_uploaded_diploma` (staff.py:1450), inline pour éviter le cycle
    _actions↔staff : la pièce diplome_bac est-elle déposée (status uploaded) ?"""
    return any(getattr(r, "piece_code", None) == "diplome_bac" and getattr(r, "status", None) == "uploaded"
               for r in (getattr(applicant, "pieces", None) or []))


_ASC, _EXA = "ascending", "exact"

_ACTION_RULES = {
    # ── opérationnel : ASCENDANT (continuité — un supérieur peut) ──
    "start_review":          lambda a, p, c: ("Admission Administratif", _ASC) if a.status == "SOU" and _c(c, "pieces_verified") else None,
    "notify_pieces_recap":   lambda a, p, c: ("Admission Administratif", _ASC) if a.status == "SOU" and _c(c, "notify_ready") else None,
    "reject_dossier":        lambda a, p, c: ("Admission Administratif", _ASC) if a.status == "SOU" else None,
    "reopen_dossier":        lambda a, p, c: ("Admission Administratif", _ASC) if a.status == "REJ" else None,
    "request_complement":    lambda a, p, c: ("Admission Administratif", _ASC) if a.status == "SOU"
                                             else (("Admission Responsable", _ASC) if a.status == "ETU" else None),
    "verify_bac_diploma":    lambda a, p, c: ("Admission Administratif", _ASC) if a.status == "ACO" and not a.bac_verified and _c(c, "diploma_uploaded") else None,
    "saisir_note_concours":  lambda a, p, c: ("Admission Administratif", _ASC) if a.status == "ETU" and p and not a.notes_validated and _c(c, "coef_complete") else None,
    "withdraw":              lambda a, p, c: ("Admission Administratif", _ASC) if a.status in _WITHDRAW_STATES else None,
    # ── décision « maker » : EXACT Responsable (Direction EXCLUE — SoD) ──
    "transfer_session":      lambda a, p, c: ("Admission Responsable", _EXA) if p and a.status in ("SOU", "INC", "ETU") and (c or {}).get("transfer_ready", False) else None,
    "mark_absent":           lambda a, p, c: ("Admission Responsable", _EXA) if p and a.status == "ETU" and (c or {}).get("absence_mark_ready", False) else None,
    "transfer_justified_absence": lambda a, p, c: ("Admission Responsable", _EXA) if p and a.status == "ABS" and (c or {}).get("absence_transfer_ready", False) else None,
    "valider_notes_concours":lambda a, p, c: ("Admission Responsable", _EXA) if a.status == "ETU" and p and a.notes_concours and not a.notes_validated else None,
    "propose_scholarships":  lambda a, p, c: ("Admission Responsable", _EXA) if a.status in ("ETU", "ATT") and _has_requested(a) else None,
    "set_waitlist_rank":     lambda a, p, c: ("Admission Responsable", _EXA) if a.status == "ATT" else None,
    "mark_admissible":       lambda a, p, c: ("Admission Responsable", _EXA) if a.status in ("ETU", "ATT") and _notes_ok(a, p) else None,
    "waitlist":              lambda a, p, c: ("Admission Responsable", _EXA) if a.status == "ETU" and _notes_ok(a, p) else None,
    "conditional_admission": lambda a, p, c: ("Admission Responsable", _EXA) if a.status == "ETU" and a.conditionnel and _notes_ok(a, p) else None,
    "refuse":                lambda a, p, c: ("Admission Responsable", _EXA) if a.status == "ETU" and _notes_ok(a, p)
                                             else (("Admission Direction", _EXA) if a.status == "ADM" and _notes_ok(a, p) else None),
    # ── validation « checker » : EXACT Direction ──
    "accept_admission":      lambda a, p, c: ("Admission Direction", _EXA) if a.status == "ADM" and _notes_ok(a, p) else None,
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
        "diploma_uploaded": _diploma_uploaded(applicant),                  # verify_bac_diploma (NT-UX)
        # `coef_complete` (saisir_note_concours) est dépendant de la SESSION → fusionné dans
        # get_dossier (où le doc session est disponible), comme _transfer_action_context.
    }


def available_actions(applicant, roles, *, is_prepa, ctx=None):
    """Liste des clés d'action que `roles` peut exécuter sur `applicant` à son état courant.
    Dérivé du registre — même déclaration d'autorisation (rôle+statut+métier) que les gardes."""
    roles = roles or []
    return [key for key, rule in _ACTION_RULES.items()
            if _authorized(rule(applicant, is_prepa, ctx), roles)]


# NT-UX (DEC-A/B/C) — EXPOSITION des indisponibilités « franchissables ». Déclaration UX PURE :
# libellé + acteur qui débloque + code + prédicat d'ÉTAT (la seule clause d'état de la règle, SANS
# la condition franchissable). La VALEUR de la condition vient de `action_context` (qui miroite la
# garde endpoint) → aucune règle réécrite, aucune garde dupliquée (GK9). `state` vrai + `ok` faux
# + action non-disponible → grisée avec raison. `state` faux → l'action reste ABSENTE (pas de mur
# de 29 boutons). `actor` = qui pose la condition manquante (le « prochain geste réel »).
_BLOCKED = {
    # ── 7 familles du mandat ──
    "mark_admissible":       {"state": lambda a, p: a.status in ("ETU", "ATT"),
                              "ok": lambda a, p, c: _notes_ok(a, p),
                              "code": "NOTES_NOT_VALIDATED", "reason": "Notes du concours à valider", "actor": "Responsable"},
    "waitlist":              {"state": lambda a, p: a.status == "ETU",
                              "ok": lambda a, p, c: _notes_ok(a, p),
                              "code": "NOTES_NOT_VALIDATED", "reason": "Notes du concours à valider", "actor": "Responsable"},
    "conditional_admission": {"state": lambda a, p: a.status == "ETU" and bool(a.conditionnel),
                              "ok": lambda a, p, c: _notes_ok(a, p),
                              "code": "NOTES_NOT_VALIDATED", "reason": "Notes du concours à valider", "actor": "Responsable"},
    "refuse":                {"state": lambda a, p: a.status == "ETU",
                              "ok": lambda a, p, c: _notes_ok(a, p),
                              "code": "NOTES_NOT_VALIDATED", "reason": "Notes du concours à valider", "actor": "Responsable"},
    "saisir_note_concours":  {"state": lambda a, p: a.status == "ETU" and p and not a.notes_validated,
                              "ok": lambda a, p, c: _c(c, "coef_complete"),
                              "code": "COEF_REQUIRED", "reason": "Coefficients des épreuves à poser", "actor": "Responsable"},
    "verify_bac_diploma":    {"state": lambda a, p: a.status == "ACO" and not a.bac_verified,
                              "ok": lambda a, p, c: _c(c, "diploma_uploaded"),
                              "code": "DIPLOMA_MISSING", "reason": "Diplôme du bac à déposer", "actor": "Candidat"},
    # ── DEC-C : jalons non-décisionnels bloqués par une précondition candidat/pièces ──
    "start_review":          {"state": lambda a, p: a.status == "SOU",
                              "ok": lambda a, p, c: _c(c, "pieces_verified"),
                              "code": "PIECES_NOT_VERIFIED", "reason": "Pièces requises à vérifier", "actor": "Administratif"},
    "enroll":                {"state": lambda a, p: a.status == "ACC",
                              "ok": lambda a, p, c: _c(c, "enrollment_ready"),
                              "code": "ENROLLMENT_NOT_READY", "reason": "Frais 2 et consentement requis", "actor": "Candidat"},
}


def blocked_actions(applicant, roles, *, is_prepa, ctx=None):
    """NT-UX — actions PERTINENTES à l'état courant mais indisponibles par une condition
    FRANCHISSABLE, à rendre grisées + raison (DEC-B). Ne réécrit aucune garde : la valeur de la
    condition vient d'`action_context`. Une action déjà disponible n'est jamais « bloquée » ; une
    action hors état reste absente. `roles` n'est PAS filtré — on nomme l'acteur attendu même si
    le lecteur n'est pas ce rôle (ordre visible, DEC-A/C)."""
    ctx = ctx or {}
    avail = set(available_actions(applicant, roles, is_prepa=is_prepa, ctx=ctx))
    out = []
    for key, meta in _BLOCKED.items():
        if key in avail:
            continue                                   # disponible → pas bloquée
        if not meta["state"](applicant, is_prepa):
            continue                                   # hors état → absente
        if meta["ok"](applicant, is_prepa, ctx):
            continue                                   # condition satisfaite → bloquée pour un autre motif (rôle) → pas ici
        out.append({"action": key, "actor": meta["actor"],
                    "code": meta["code"], "reason": meta["reason"]})
    return out


def can_control_pieces(applicant, roles):
    """Contrôle documentaire (verify/reject/require/waive/reset) : garde back `_resolve_piece_sou`
    = CONFIRM_ROLES (Administratif ⊆ ascendant) + statut SOU. Le front garde ses sous-conditions
    per-pièce (uploaded → vérifiable, verified → verrouillée)."""
    return applicant.status == "SOU" and _authorized(("Admission Administratif", _ASC), roles or [])


def can_manage_payments(applicant, roles):
    """UX paiement : union exacte des états où frais 1 ou frais 2 est actionnable.

    Le détail par type reste re-gardé par ``payment_state_allowed`` dans chaque endpoint.
    """
    return applicant.status in _PAYMENT_MANAGE_STATES and _authorized(("Admission Administratif", _ASC), roles or [])
