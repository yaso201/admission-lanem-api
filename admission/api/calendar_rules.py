"""GESTION-CALENDRIER — SOURCE UNIQUE des règles de modification des sessions.

Principe (§4 du mandat) : « ce qui élargit est permis, ce qui restreint est interdit ».
Ces règles vivent à UN seul endroit et sont consommées par :
  (a) les endpoints calendrier (enforcement à la mutation),
  (b) le validate() du contrôleur (filet défense-en-profondeur sur tout save, GK5 appel direct),
  (c) le front (rendu des champs éditables/verrouillés + le POURQUOI — un champ grisé sans
      explication est un défaut).
Deux implémentations divergeraient — c'est la leçon du résolveur d'étape et des actions dispo.

Verdict d'un changement de champ :
  {allowed, reason, requires_validation, triggers_reissue}
  - allowed=False        → refusé par le serveur (dur), y compris sur appel direct
  - requires_validation  → maker-checker : le Responsable propose, la Direction valide (pending)
  - triggers_reissue     → la validation réémet les convocations (report/horaire/salle)
"""

from __future__ import annotations

import frappe
from frappe.utils import getdate

# Champs figés dès la publication (« publié engage ») : structure + ouverture.
STRUCTURE_FIELDS = {
    "programme_code", "programme_label", "academic_year",
    "application_fee_xof", "label", "is_prepa_session", "opens_on",
}
# Champs d'épreuve : toute modification réémet les convocations (elle affecte des convoqués).
EXAM_SCHEDULE_FIELDS = {"exam_date", "exam_call_time", "exam_start_time", "exam_room"}
# Champs date soumis à la règle monotone « avancer interdit ».
_MONOTONE_DATE_FIELDS = {"closes_on", "exam_date"}

# Champs qu'un save doit contrôler (défense-en-profondeur). bac_results_date exclu (libre).
_GUARDED_FIELDS = STRUCTURE_FIELDS | EXAM_SCHEDULE_FIELDS | {"closes_on"}


def _verdict(allowed, reason, requires_validation=False, triggers_reissue=False):
    return {
        "allowed": bool(allowed),
        "reason": reason,
        "requires_validation": bool(allowed and requires_validation),
        "triggers_reissue": bool(allowed and triggers_reissue),
    }


def _norm_date(v):
    try:
        return getdate(v) if v not in (None, "") else None
    except Exception:
        return None


def _same(field, old, new):
    """Égalité tolérante au type (dates normalisées, sinon comparaison de chaînes)."""
    if field in _MONOTONE_DATE_FIELDS or field in ("bac_results_date", "opens_on"):
        return _norm_date(old) == _norm_date(new)
    a = "" if old is None else str(old)
    b = "" if new is None else str(new)
    return a == b


def _is_advance(old, new):
    """new antérieure à old (« avancer ») — pour une paire de dates valides."""
    o, n = _norm_date(old), _norm_date(new)
    if o is None or n is None:
        return False
    return n < o


def evaluate_change(state, field, old_value, new_value):
    """Verdict pur pour un changement d'un champ, selon l'état lifecycle (Draft/Open/Closed)."""
    if _same(field, old_value, new_value):
        return _verdict(True, "Aucun changement.")

    if state == "Draft":
        return _verdict(True, "Brouillon : modification libre (rien n'est publié).")

    # ── Session publiée (Open) ou clôturée (Closed) ─────────────────────────────
    if field == "bac_results_date":
        return _verdict(True, "Résultats du bac : date subie, modifiable sans validation.")

    if field == "closes_on":
        if state != "Open":
            return _verdict(False, "Session fermée : la date de clôture des dépôts n'est plus modifiable.")
        if _is_advance(old_value, new_value):
            return _verdict(False, "Avancer la clôture priverait des candidats de candidater — refusé.")
        return _verdict(True, "Prolongation de la clôture — soumise à la validation de la Direction.",
                        requires_validation=True)

    if field == "exam_date":
        if _is_advance(old_value, new_value):
            return _verdict(False, "Avancer l'épreuve priverait du délai d'organisation — refusé.")
        return _verdict(True, "Report de l'épreuve — soumis à la Direction ; réémet les convocations.",
                        requires_validation=True, triggers_reissue=True)

    if field in ("exam_call_time", "exam_start_time", "exam_room"):
        return _verdict(True, "Correction d'horaire ou de salle — soumise à la Direction ; réémet les convocations.",
                        requires_validation=True, triggers_reissue=True)

    if field in STRUCTURE_FIELDS:
        return _verdict(False, "Session publiée : la structure et la date d'ouverture sont figées.")

    return _verdict(False, f"Champ non modifiable par le calendrier : {field}.")


def _state_of(doc):
    st = doc.get("lifecycle_state")
    if st:
        return st
    return "Open" if doc.get("is_open") else "Closed"


def enforce_document_change(before, after):
    """Filet défense-en-profondeur appelé par AdmissionSession.validate() sur un doc existant.
    Lève frappe.ValidationError sur un changement DUR-interdit (avancer clôture/épreuve, toucher
    la structure d'une session publiée). Ne bloque PAS une transition d'état (ouverture/fermeture) :
    l'endpoint la porte. Ne juge PAS le maker-checker (non décidable ici) — seulement le refus dur."""
    before_state, after_state = _state_of(before), _state_of(after)
    if before_state != after_state:
        return  # transition d'état : portée par l'endpoint, pas jugée sur les dates
    if after_state == "Draft":
        return  # tout libre
    for field in _GUARDED_FIELDS:
        old, new = before.get(field), after.get(field)
        if _same(field, old, new):
            continue
        v = evaluate_change(after_state, field, old, new)
        if not v["allowed"]:
            frappe.throw(v["reason"], title="Modification refusée")


# Couple de sonde RÉALISTE par champ : une paire qui EXERCE la direction « élargir » (nouvelle date
# postérieure). Une sonde bidon (« a »/« b ») normaliserait les champs date en None==None → faux
# « aucun changement » → tout champ date faussement éditable. Les valeurs importent peu (l'année 2000
# est neutre) : seul compte que new > old pour révéler les effets (validation, réémission).
_POLICY_FIELDS = [
    ("opens_on", "Ouverture", "2000-01-01", "2000-06-01"),
    ("closes_on", "Clôture des dépôts", "2000-01-01", "2000-12-31"),
    ("exam_date", "Date de l'épreuve", "2000-01-01", "2000-12-31"),
    ("exam_call_time", "Heure d'appel", "07:00:00", "08:00:00"),
    ("exam_start_time", "Début des épreuves", "08:00:00", "09:00:00"),
    ("exam_room", "Salle", "Salle A", "Salle B"),
    ("bac_results_date", "Résultats du bac", "2000-01-01", "2000-12-31"),
]


def field_policies(session):
    """Politique par champ pour le FRONT (rendu éditable/verrouillé + le pourquoi). Le front est un
    pur renderer : il n'invente aucune règle. `session` = doc ou dict portant lifecycle_state.
    Chaque champ délègue à evaluate_change (SOURCE UNIQUE) via un couple de sonde réaliste."""
    state = _state_of(session)
    out = {}
    for field, label, old, new in _POLICY_FIELDS:
        if state == "Draft":
            out[field] = {"label": label, "editable": True, "requires_validation": False,
                          "triggers_reissue": False, "constraint": None,
                          "reason": "Brouillon : modification libre (rien n'est publié)."}
            continue
        probe = evaluate_change(state, field, old, new)
        constraint = None
        if probe["allowed"] and field == "closes_on":
            constraint = "extend_only"      # prolonger seulement
        elif probe["allowed"] and field == "exam_date":
            constraint = "postpone_only"    # reporter seulement
        out[field] = {
            "label": label,
            "editable": probe["allowed"],
            "requires_validation": probe["requires_validation"],
            "triggers_reissue": probe["triggers_reissue"],
            "constraint": constraint,
            "reason": probe["reason"],
        }
    return out
