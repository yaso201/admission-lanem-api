"""GESTION-CALENDRIER — endpoints du calendrier des sessions.

Le geste annuel : reprendre le calendrier de l'année écoulée et le DÉCALER (§1). La duplication
en est le cœur (§3) ; l'ouverture et les modifications de dates suivent le circuit maker-checker
(Responsable saisit / Direction valide, §5). TOUTES les mutations passent ici — le write REST du
doctype est verrouillé (§6, « le serveur fait foi, y compris sur appel direct »). Les règles de
modification viennent de la source unique `calendar_rules` (§4, GK9).
"""

from __future__ import annotations

import json
import re

import frappe
from frappe.utils import add_days, cint, getdate

from admission.api.permissions import roles_at_or_above
from admission.api.public import _ok, _error
from admission.api.sessions import set_lifecycle, _state, session_display_status

RESP_UP = roles_at_or_above("Admission Responsable")   # {Resp, Dir, SysMgr} — le Responsable saisit
DIR_UP = roles_at_or_above("Admission Direction")       # {Dir, SysMgr} — la Direction valide

DEFAULT_SHIFT_DAYS = 364   # 52 semaines : conserve le jour de la semaine (§3)


# ─────────────────────────── duplication (le cœur, §3) ───────────────────────────

def _parse_list(v):
    if v is None:
        return []
    if isinstance(v, (list, tuple)):
        return list(v)
    try:
        parsed = json.loads(v)
        return parsed if isinstance(parsed, list) else [parsed]
    except Exception:
        return [v]


def _bump_academic_year(ay, years=1):
    m = re.match(r"^(\d{4})-(\d{4})$", ay or "")
    if not m:
        return ay
    return f"{int(m.group(1)) + years}-{int(m.group(2)) + years}"


def _year_token(ay):
    m = re.match(r"^(\d{4})-(\d{4})$", ay or "")
    return (m.group(1)[2:] + m.group(2)[2:]) if m else ""


def _derive_code(source_code, source_ay, target_ay, taken):
    """Code dérivé, unique, non destructif. Retire le jeton d'année SOURCE s'il est en suffixe
    (évite l'empilement sur re-duplication), pose le jeton d'année CIBLE. Collision → suffixe
    lettre + drapeau signalé dans l'aperçu (jamais silencieux, §3 #2)."""
    src_token = _year_token(source_ay)
    base = source_code
    if src_token and base.endswith(f"-{src_token}"):
        base = base[: -(len(src_token) + 1)]
    token = _year_token(target_ay) or "copy"
    cand = f"{base}-{token}"
    adjusted = False
    if cand in taken or frappe.db.exists("Admission Session", cand):
        adjusted = True
        i = ord("b")
        while True:
            alt = f"{cand}{chr(i)}"
            if alt not in taken and not frappe.db.exists("Admission Session", alt):
                cand = alt
                break
            i += 1
    taken.add(cand)
    return cand, adjusted


def _time_str(t):
    if t is None:
        return None
    if hasattr(t, "total_seconds"):   # datetime.timedelta (champ Time)
        total = int(t.total_seconds())
        return f"{total // 3600:02d}:{(total % 3600) // 60:02d}:{total % 60:02d}"
    return str(t)


def _shift_date(d, days):
    return str(add_days(getdate(d), days)) if d else None


def _compute_duplicates(session_names, shift_days, academic_year):
    """SOURCE de vérité de la duplication : consommée par l'aperçu ET la création. Ne crée rien.
    Décale toutes les dates ; reprend structure, heures, salle, libellés ; année mise à jour ;
    état Draft ; nouveaux codes uniques. Ne lit jamais candidatures/paiements."""
    shift = cint(shift_days) if shift_days not in (None, "") else DEFAULT_SHIFT_DAYS
    taken = set()
    plans = []
    for code in session_names:
        src = frappe.get_doc("Admission Session", code)
        target_ay = academic_year or _bump_academic_year(src.academic_year, 1)
        new_code, adjusted = _derive_code(src.session_code, src.academic_year, target_ay, taken)
        plans.append({
            "source": src.name,
            "source_label": src.label,
            "new_code": new_code,
            "code_adjusted": adjusted,
            "label": src.label,
            "programme_code": src.programme_code,
            "programme_label": src.programme_label,
            "academic_year": target_ay,
            "application_fee_xof": src.application_fee_xof,
            "is_prepa_session": src.is_prepa_session,
            "opens_on": _shift_date(src.opens_on, shift),
            "closes_on": _shift_date(src.closes_on, shift),
            "bac_results_date": _shift_date(src.bac_results_date, shift),
            "exam_date": _shift_date(src.exam_date, shift) if src.exam_date else None,
            "exam_call_time": _time_str(src.exam_call_time),
            "exam_start_time": _time_str(src.exam_start_time),
            "exam_room": src.exam_room or None,
            "lifecycle_state": "Draft",
        })
    return {"shift_days": shift, "sessions": plans}


def _create_duplicates(session_names, shift_days, academic_year, code_overrides=None):
    """Crée les copies en BROUILLON à partir du même calcul que l'aperçu. Ne touche JAMAIS une
    source (§3 #4). Ne reprend NI candidatures NI paiements NI état d'ouverture."""
    plan = _compute_duplicates(session_names, shift_days, academic_year)
    overrides = code_overrides or {}
    created = []
    for p in plan["sessions"]:
        code = overrides.get(p["source"]) or p["new_code"]
        if frappe.db.exists("Admission Session", code):
            frappe.throw(f"Collision d'identifiant : « {code} » existe déjà.", title="Duplication refusée")
        doc = frappe.get_doc({
            "doctype": "Admission Session", "session_code": code, "label": p["label"],
            "programme_code": p["programme_code"], "programme_label": p["programme_label"],
            "academic_year": p["academic_year"], "application_fee_xof": p["application_fee_xof"],
            "is_prepa_session": p["is_prepa_session"], "opens_on": p["opens_on"],
            "closes_on": p["closes_on"], "bac_results_date": p["bac_results_date"],
            "exam_date": p["exam_date"], "exam_call_time": p["exam_call_time"],
            "exam_start_time": p["exam_start_time"], "exam_room": p["exam_room"],
            "lifecycle_state": "Draft",   # systématiquement (§3)
        }).insert(ignore_permissions=True)
        created.append(doc.name)
    frappe.db.commit()
    return {"created": created, "shift_days": plan["shift_days"]}


def _delete_draft(name):
    """Filet en cas d'erreur (§3 #3) : supprime une session EN BROUILLON. Refuse tout autre état
    (rien de publié ne se supprime). Ceinture : refuse s'il existe des dossiers rattachés."""
    if not frappe.db.exists("Admission Session", name):
        frappe.throw("Session inconnue.", title="Suppression impossible")
    state = frappe.db.get_value("Admission Session", name, "lifecycle_state") or (
        "Open" if frappe.db.get_value("Admission Session", name, "is_open") else "Closed")
    if state != "Draft":
        frappe.throw("Seule une session en brouillon peut être supprimée.", title="Suppression refusée")
    if frappe.db.exists("Admission Applicant", {"session": name}):
        frappe.throw("Des dossiers sont rattachés à cette session.", title="Suppression refusée")
    frappe.delete_doc("Admission Session", name, ignore_permissions=True)
    frappe.db.commit()
    return {"deleted": name}


# ─────────────────────────── endpoints whitelistés ───────────────────────────

@frappe.whitelist()
def duplicate_preview(sessions=None, shift_days=DEFAULT_SHIFT_DAYS, academic_year=None):
    """Aperçu de la duplication (aucune écriture) — le Responsable saisit."""
    frappe.only_for(RESP_UP)
    names = _parse_list(sessions)
    if not names:
        return _error("NO_SESSION", "Sélectionnez au moins une session à reprendre.", 400)
    for n in names:
        if not frappe.db.exists("Admission Session", n):
            return _error("INVALID_SESSION", f"Session inconnue : {n}.", 404)
    return _ok(_compute_duplicates(names, shift_days, academic_year))


@frappe.whitelist(methods=["POST"])
def duplicate_create(sessions=None, shift_days=DEFAULT_SHIFT_DAYS, academic_year=None, code_overrides=None):
    """Crée les brouillons (le Responsable saisit — rien n'engage tant qu'ils ne sont pas ouverts)."""
    frappe.only_for(RESP_UP)
    names = _parse_list(sessions)
    if not names:
        return _error("NO_SESSION", "Sélectionnez au moins une session à reprendre.", 400)
    for n in names:
        if not frappe.db.exists("Admission Session", n):
            return _error("INVALID_SESSION", f"Session inconnue : {n}.", 404)
    if isinstance(code_overrides, str):
        try:
            code_overrides = json.loads(code_overrides)
        except Exception:
            code_overrides = None
    return _ok(_create_duplicates(names, shift_days, academic_year, code_overrides))


@frappe.whitelist(methods=["POST"])
def delete_draft(session=None):
    """Supprime une session en brouillon (le filet, §3 #3)."""
    frappe.only_for(RESP_UP)
    if not session:
        return _error("NO_SESSION", "Session manquante.", 400)
    return _ok(_delete_draft(session))


# ─────────────────────────── maker-checker (§5) ───────────────────────────
#
# Le Responsable SAISIT, la Direction VALIDE — sans rien inventer (le patron des admissions).
# Deux points de validation seulement : OUVRIR une session, et MODIFIER une DATE d'une session
# ouverte. Le brouillon et la date des résultats du bac échappent au circuit (rien n'engage).

# Édition libre d'un brouillon : sous-ensemble sûr (jamais l'état ni le code ni le miroir).
_DRAFT_EDITABLE = {
    "label", "programme_code", "programme_label", "academic_year", "application_fee_xof",
    "is_prepa_session", "opens_on", "closes_on", "bac_results_date",
    "exam_date", "exam_call_time", "exam_start_time", "exam_room",
}


def _open_session(name):
    """Ouvre une session (Draft → Open). GK4 : acte de la Direction. La transition rend la session
    visible du candidat → doc.save (déclenche l'invalidation du catalogue via on_update)."""
    doc = frappe.get_doc("Admission Session", name)
    if _state(doc) != "Draft":
        frappe.throw("Seule une session en brouillon peut être ouverte.", title="Ouverture refusée")
    doc.lifecycle_state = "Open"
    doc.save(ignore_permissions=True)
    frappe.db.commit()
    return {"session": name, "lifecycle_state": "Open"}


def _update_draft(name, values):
    """Modification LIBRE d'un brouillon (§4 : rien n'engage). Refusé hors brouillon."""
    doc = frappe.get_doc("Admission Session", name)
    if _state(doc) != "Draft":
        frappe.throw("La modification libre est réservée aux brouillons.", title="Modification refusée")
    for k, v in (values or {}).items():
        if k in _DRAFT_EDITABLE:
            doc.set(k, v)
    doc.save(ignore_permissions=True)
    frappe.db.commit()
    return {"session": name, "updated": [k for k in (values or {}) if k in _DRAFT_EDITABLE]}


def _propose_changes(name, changes):
    """Le Responsable propose des changements de dates sur une session PUBLIÉE (Open/Closed).
    Chaque champ est jugé par la SOURCE UNIQUE (calendar_rules) :
      - refusé (avancer/structure) → toute la proposition est rejetée (atomique), rien n'est écrit ;
      - libre (résultats du bac) → appliqué immédiatement ;
      - soumis à validation (prolonger clôture, reporter épreuve, horaire, salle) → posé en PENDING,
        la valeur LIVE reste effective (GK8)."""
    from admission.api.calendar_rules import evaluate_change
    from frappe.utils import now_datetime
    doc = frappe.get_doc("Admission Session", name)
    state = _state(doc)
    if state == "Draft":
        frappe.throw("Une session en brouillon se modifie librement, sans proposition.",
                     title="Proposition inutile")

    refused, immediate, pending = [], {}, {}
    for field, new_value in (changes or {}).items():
        v = evaluate_change(state, field, doc.get(field), new_value)
        if not v["allowed"]:
            refused.append({"field": field, "reason": v["reason"]})
        elif v["requires_validation"]:
            pending[field] = (new_value, v)
        else:
            immediate[field] = new_value
    if refused:   # atomique : une restriction refuse toute la proposition
        msg = " ".join(r["reason"] for r in refused)
        frappe.throw(msg, title="Modification refusée")

    for field, val in immediate.items():
        doc.set(field, val)   # résultats du bac : subie, appliquée direct

    if pending:
        keep = [r for r in (doc.pending_changes or []) if r.change_field not in pending]
        doc.set("pending_changes", keep)   # un nouveau propose REMPLACE le pending du même champ
        policies = _field_labels()
        for field, (val, _v) in pending.items():
            doc.append("pending_changes", {
                "change_field": field, "field_label": policies.get(field, field),
                "current_value": _as_text(doc.get(field)), "proposed_value": _as_text(val),
                "requested_by": frappe.session.user, "requested_on": now_datetime(),
            })
    doc.save(ignore_permissions=True)
    frappe.db.commit()
    return {"session": name, "applied": list(immediate), "pending": list(pending)}


def _validate_changes(name):
    """La Direction valide (§5) : applique TOUTES les modifications en attente (par lot, #7),
    purge le pending, puis RÉÉMET les convocations si une date/horaire/salle d'épreuve a changé."""
    from admission.api.calendar_rules import EXAM_SCHEDULE_FIELDS
    doc = frappe.get_doc("Admission Session", name)
    rows = list(doc.pending_changes or [])
    if not rows:
        return {"session": name, "applied": [], "reissue_triggered": False}
    applied, reissue = [], False
    for r in rows:
        doc.set(r.change_field, r.proposed_value)   # Frappe coerce chaîne → Date/Time
        applied.append(r.change_field)
        if r.change_field in EXAM_SCHEDULE_FIELDS:
            reissue = True
    doc.set("pending_changes", [])
    doc.save(ignore_permissions=True)
    frappe.db.commit()
    if reissue:
        from admission.api.convocation import reissue_convocations
        reissue_convocations(doc.name)
    return {"session": name, "applied": applied, "reissue_triggered": reissue}


def _reject_changes(name):
    """La Direction écarte les propositions (§5) : purge le pending, l'ancienne valeur demeure."""
    doc = frappe.get_doc("Admission Session", name)
    n = len(doc.pending_changes or [])
    doc.set("pending_changes", [])
    doc.save(ignore_permissions=True)
    frappe.db.commit()
    return {"session": name, "rejected": n}


def _field_labels():
    return {"closes_on": "Clôture des dépôts", "exam_date": "Date de l'épreuve",
            "exam_call_time": "Heure d'appel", "exam_start_time": "Début des épreuves",
            "exam_room": "Salle", "bac_results_date": "Résultats du bac", "opens_on": "Ouverture"}


def _as_text(v):
    return "" if v in (None, "") else str(v)


@frappe.whitelist(methods=["POST"])
def open_session(session=None):
    """Ouvre une session (Draft → Open) — acte de la DIRECTION (GK4)."""
    frappe.only_for(DIR_UP)
    if not session or not frappe.db.exists("Admission Session", session):
        return _error("INVALID_SESSION", "Session inconnue.", 404)
    return _ok(_open_session(session))


@frappe.whitelist(methods=["POST"])
def update_draft(session=None, values=None):
    """Modifie librement un brouillon — le Responsable saisit."""
    frappe.only_for(RESP_UP)
    if not session or not frappe.db.exists("Admission Session", session):
        return _error("INVALID_SESSION", "Session inconnue.", 404)
    if isinstance(values, str):
        try:
            values = json.loads(values)
        except Exception:
            values = {}
    return _ok(_update_draft(session, values or {}))


@frappe.whitelist(methods=["POST"])
def propose_changes(session=None, changes=None):
    """Propose des changements de dates sur une session publiée — le Responsable saisit."""
    frappe.only_for(RESP_UP)
    if not session or not frappe.db.exists("Admission Session", session):
        return _error("INVALID_SESSION", "Session inconnue.", 404)
    if isinstance(changes, str):
        try:
            changes = json.loads(changes)
        except Exception:
            changes = {}
    if not changes:
        return _error("NO_CHANGE", "Aucun changement proposé.", 400)
    return _ok(_propose_changes(session, changes))


@frappe.whitelist(methods=["POST"])
def validate_changes(session=None):
    """Valide les modifications en attente (applique + réémet si épreuve) — la DIRECTION valide."""
    frappe.only_for(DIR_UP)
    if not session or not frappe.db.exists("Admission Session", session):
        return _error("INVALID_SESSION", "Session inconnue.", 404)
    return _ok(_validate_changes(session))


@frappe.whitelist(methods=["POST"])
def reject_changes(session=None):
    """Écarte les modifications en attente — la DIRECTION valide (ou rejette)."""
    frappe.only_for(DIR_UP)
    if not session or not frappe.db.exists("Admission Session", session):
        return _error("INVALID_SESSION", "Session inconnue.", 404)
    return _ok(_reject_changes(session))
