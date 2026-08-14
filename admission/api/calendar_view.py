"""GESTION-CALENDRIER — endpoint de LECTURE pour le front management.

À DÉPOSER dans : apps/admission/admission/api/calendar_view.py

Pourquoi ce fichier : le back porte déjà les mutations (`calendar.py`) et la source unique des
règles (`calendar_rules.py`), mais rien n'expose au front (a) la liste des sessions groupées par
année académique, (b) la politique par champ (`calendar_rules.field_policies`), (c) les
changements en attente. Sans (b), l'écran réinventerait les règles — ce que GK9 interdit.

Le front est un PUR RENDERER : il n'invente aucune règle, il affiche `policies[champ].reason`.
"""

from __future__ import annotations

import frappe

from admission.api.calendar_rules import field_policies
from admission.api.permissions import roles_at_or_above
from admission.api.public import _ok, _error
from admission.api.sessions import _state, session_display_status

READ_UP = roles_at_or_above("Admission Administratif")   # lecture : Administratif, Resp, Direction

_LIST_FIELDS = [
    "name", "session_code", "label", "programme_code", "programme_label", "academic_year",
    "lifecycle_state", "is_open", "is_prepa_session", "application_fee_xof",
    "opens_on", "closes_on", "bac_results_date",
    "exam_date", "exam_call_time", "exam_start_time", "exam_room",
]


def _t(v):
    """Time/timedelta → 'HH:MM' pour le front (les champs Time sortent en timedelta)."""
    if v is None:
        return None
    if hasattr(v, "total_seconds"):
        total = int(v.total_seconds())
        return f"{total // 3600:02d}:{(total % 3600) // 60:02d}"
    return str(v)[:5]


def _applicant_count(session):
    return frappe.db.count("Admission Applicant", {"session": session})


def _convocation_count(session):
    """Nombre de CONVOQUÉS — même prédicat que la réémission (frais 1 confirmé, cf.
    convocation.reissue_convocations). C'est ce nombre que la Direction doit voir avant de
    valider un report (GK6). Si le prédicat évolue, il évolue ICI ET LÀ-BAS ensemble."""
    from admission.api.convocation import _frais1_confirmed_payment
    n = 0
    for name in frappe.get_all("Admission Applicant", filters={"session": session}, pluck="name"):
        if _frais1_confirmed_payment(frappe.get_doc("Admission Applicant", name)):
            n += 1
    return n


def _pending_rows(doc):
    return [{
        "change_field": r.change_field,
        "field_label": r.field_label,
        "current_value": r.current_value,
        "proposed_value": r.proposed_value,
        "requested_by": r.requested_by,
        "requested_on": str(r.requested_on) if r.requested_on else None,
        "requires_validation": True,
        "triggers_reissue": r.change_field in ("exam_date", "exam_call_time", "exam_start_time", "exam_room"),
    } for r in (doc.pending_changes or [])]


def _serialize(doc, with_counts=True):
    row = {f: doc.get(f) for f in _LIST_FIELDS}
    row["exam_call_time"] = _t(doc.get("exam_call_time"))
    row["exam_start_time"] = _t(doc.get("exam_start_time"))
    for k in ("opens_on", "closes_on", "bac_results_date", "exam_date"):
        row[k] = str(row[k]) if row[k] else None
    row["lifecycle_state"] = _state(doc)
    row["display_status"] = session_display_status(doc)
    row["policies"] = field_policies(doc)
    row["pending"] = _pending_rows(doc)
    row["applicant_count"] = _applicant_count(doc.name) if with_counts else None
    row["convocation_count"] = _convocation_count(doc.name) if with_counts else None
    row["can_delete"] = row["lifecycle_state"] == "Draft" and not row["applicant_count"]
    return row


@frappe.whitelist(methods=["GET"])
def calendar_list(academic_year=None):
    """Le calendrier, groupé par année académique (récente d'abord). Lecture seule.

    Réponse : {today, default_shift_days, pending_total, groups:[{academic_year, sessions:[…]}]}
    Chaque session porte `policies` (source unique) et `pending` — le front ne calcule rien.
    """
    frappe.only_for(READ_UP)
    filters = {"academic_year": academic_year} if academic_year else {}
    names = frappe.get_all("Admission Session", filters=filters,
                           order_by="academic_year desc, opens_on asc", pluck="name")
    rows = [_serialize(frappe.get_doc("Admission Session", n)) for n in names]

    groups, order = {}, []
    for r in rows:
        ay = r["academic_year"] or "—"
        if ay not in groups:
            groups[ay] = []
            order.append(ay)
        groups[ay].append(r)

    from admission.api.calendar import DEFAULT_SHIFT_DAYS
    return _ok({
        "today": str(frappe.utils.today()),
        "default_shift_days": DEFAULT_SHIFT_DAYS,
        "pending_total": sum(len(r["pending"]) for r in rows),
        "groups": [{"academic_year": ay, "sessions": groups[ay]} for ay in order],
    })


@frappe.whitelist(methods=["GET"])
def session_detail(session=None):
    """Une session + sa politique par champ + son pending — pour l'écran d'édition des dates."""
    frappe.only_for(READ_UP)
    if not session or not frappe.db.exists("Admission Session", session):
        return _error("INVALID_SESSION", "Session inconnue.", 404)
    return _ok(_serialize(frappe.get_doc("Admission Session", session)))


@frappe.whitelist(methods=["GET"])
def pending_queue():
    """La file de validation : toutes les propositions en attente, tous programmes confondus.
    Alimente l'écran Direction et le compteur de l'onglet (côté Responsable : même liste, en
    lecture — « proposé, en attente »)."""
    frappe.only_for(READ_UP)
    # NB Frappe : lire une table ENFANT exige parent_doctype (sinon PermissionError).
    names = frappe.get_all("Admission Session Pending Change", fields=["parent"],
                           parent_doctype="Admission Session", pluck="parent")
    out = []
    for name in sorted(set(names)):
        if not frappe.db.exists("Admission Session", name):
            continue   # défensif : une ligne orpheline ne doit pas faire tomber toute la file
        doc = frappe.get_doc("Admission Session", name)
        row = _serialize(doc)
        for p in row["pending"]:
            out.append({"session": row, "change": p})
    return _ok({"items": out, "total": len(out)})
