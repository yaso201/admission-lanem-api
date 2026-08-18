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

from admission.api.calendar_rules import field_policies, deletion_verdict
from admission.api.permissions import roles_at_or_above
from admission.api.public import _ok, _error
from admission.api.sessions import _state, session_display_status

READ_UP = roles_at_or_above("Admission Administratif")   # lecture : Administratif, Resp, Direction

_LIST_FIELDS = [
    "name", "session_code", "label", "programme_code", "programme_label", "academic_year",
    "lifecycle_state", "is_open", "is_prepa_session", "application_fee_xof", "capacity",
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
    # CAL-DEL (D6) — même verdict GK9 que le garde serveur : le bouton n'apparaît jamais pour une
    # suppression que le serveur rejetterait (état + dossiers + frais + transferts + proposition).
    row["can_delete"] = deletion_verdict(doc)["deletable"]
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
        "a_traiter": _a_traiter(rows, frappe.utils.today()),   # DEC-N : calculé SERVEUR (GK9)
        "groups": [{"academic_year": ay, "sessions": groups[ay]} for ay in order],
    })


def _a_traiter(rows, today):
    """CAL-AMEL (DEC-N) — les 3 familles du bandeau « À traiter », seuils SERVEUR (GK9 : le
    front ne calcule aucune règle) : clôture ≤ 7 j (Open) · épreuve ≤ 14 j sans salle/heures ·
    proposition en attente > 7 j (le front ne la montre qu'à la Direction). Item = lien cible."""
    from frappe.utils import date_diff, getdate
    t = getdate(today)
    items = []
    for r in rows:
        if r["lifecycle_state"] == "Open" and r["closes_on"]:
            d = date_diff(r["closes_on"], t)
            if 0 <= d <= 7:
                items.append({"famille": "cloture", "session": r["name"], "code": r["session_code"],
                              "label": r["label"], "jours": d,
                              "detail": ("clôture AUJOURD'HUI" if d == 0 else f"clôture dans {d} j"),
                              "lien": f"/calendrier-session?s={r['name']}"})
        if r["exam_date"]:
            d = date_diff(r["exam_date"], t)
            manque = ([] if r["exam_room"] else ["salle"]) + \
                     ([] if (r["exam_call_time"] and r["exam_start_time"]) else ["heures"])
            if manque and 0 <= d <= 14:
                items.append({"famille": "epreuve", "session": r["name"], "code": r["session_code"],
                              "label": r["label"], "jours": d,
                              "detail": f"épreuve dans {d} j — sans " + " ni ".join(manque),
                              "lien": f"/calendrier-session?s={r['name']}"})
        for p in r["pending"]:
            age = date_diff(t, str(p["requested_on"])[:10]) if p["requested_on"] else 0
            if age > 7:
                items.append({"famille": "pending", "session": r["name"], "code": r["session_code"],
                              "label": r["label"], "jours": age,
                              "detail": f"proposition en attente depuis {age} j",
                              "lien": "/calendrier-validations"})
                break   # une entrée par session suffit
    items.sort(key=lambda x: x["jours"])
    return items


def _next_session(doc):
    """AMEL-06-b — chaînage DÉRIVÉ (aucun champ nouveau) : même programme, même année
    académique, la première session dont l'ouverture suit celle-ci."""
    for r in frappe.get_all("Admission Session",
                            filters={"programme_code": doc.programme_code,
                                     "academic_year": doc.academic_year,
                                     "name": ["!=", doc.name]},
                            fields=["name", "session_code", "label", "opens_on"],
                            order_by="opens_on asc"):
        if r.opens_on and doc.opens_on and r.opens_on > doc.opens_on:
            return {"session": r.name, "code": r.session_code, "label": r.label,
                    "opens_on": str(r.opens_on)}
    return None


@frappe.whitelist(methods=["GET"])
def session_detail(session=None):
    """Une session + sa politique par champ + son pending — pour l'écran d'édition des dates."""
    frappe.only_for(READ_UP)
    if not session or not frappe.db.exists("Admission Session", session):
        return _error("INVALID_SESSION", "Session inconnue.", 404)
    doc = frappe.get_doc("Admission Session", session)
    row = _serialize(doc)
    row["next_session"] = _next_session(doc)   # AMEL-06-b : chaînage dérivé
    return _ok(row)


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
