"""Seed des sessions d'admission recette (calendrier réel 13/06/2026).

Prépa : 5 sessions concours (fenêtres séquentielles, date concours dans le label).
Standard (Licence/Bachelor/Double-Diplomation) : 1 session par programme, fenêtre jusqu'au
03/10 (deadline la plus tardive). NB modèle : Admission Session n'a pas de champ « terme »
→ une seule fenêtre par session ; la précision par terme (année 1 vs 2) nécessiterait un
champ dédié (à arbitrer). Idempotent (skip si session_code existe).
"""
import frappe
from admission.seed.catalogue import PROGRAMMES, DOUBLE_DIPLOMES, DD_FEES

# (session_code, label, opens_on, closes_on, concours_date)
PREPA_SESSIONS = [
    ("SES-PREPA-S1", "Prépa — Session 1 (concours 25/07/2026)", "2026-06-12", "2026-07-24", "2026-07-25"),
    ("SES-PREPA-S2", "Prépa — Session 2 (concours 05/08/2026)", "2026-07-25", "2026-08-04", "2026-08-05"),
    ("SES-PREPA-S3", "Prépa — Session 3 (concours 26/08/2026)", "2026-08-05", "2026-08-25", "2026-08-26"),
    ("SES-PREPA-S4", "Prépa — Session 4 (concours 07/09/2026)", "2026-08-26", "2026-09-06", "2026-09-07"),
    ("SES-PREPA-S5", "Prépa — Session 5 (concours 26/09/2026)", "2026-09-07", "2026-09-25", "2026-09-26"),
]

STD_OPENS = "2026-06-12"
STD_CLOSES = "2026-10-03"
STD_BAC = "2026-07-31"


def _frais1(code):
    for p in PROGRAMMES:
        if p["code"] == code:
            return p["fees"].get("application") or p["fees"].get("competition")
    return DD_FEES["application"]  # double-diplomation


def _upsert_session(session_code, label, programme_code, programme_label,
                    opens_on, closes_on, bac, fee, prepa):
    if frappe.db.exists("Admission Session", {"session_code": session_code}):
        return
    frappe.get_doc({
        "doctype": "Admission Session", "session_code": session_code, "label": label,
        "programme_code": programme_code, "programme_label": programme_label,
        "academic_year": "2026-2027", "opens_on": opens_on, "closes_on": closes_on,
        "bac_results_date": bac, "application_fee_xof": fee, "is_open": 1,
        "is_prepa_session": prepa,
    }).insert(ignore_permissions=True)


def run():
    title_by_code = {p["code"]: p["title"] for p in PROGRAMMES}
    # 1) prépa — 5 sessions concours
    for (sc, label, o, c, concours) in PREPA_SESSIONS:
        _upsert_session(sc, label, "PREPA", "Cycle Préparatoire", o, c, concours, 10000, 1)
    # 2) standard (Licence + Bachelor) — 1 session par programme
    for p in PROGRAMMES:
        if p["parcours"] in ("Licence", "Bachelor"):
            _upsert_session(f"SES-{p['code']}-2026", f"{p['title']} — rentrée 2026",
                            p["code"], p["title"], STD_OPENS, STD_CLOSES, STD_BAC,
                            _frais1(p["code"]), 0)
    # 3) double-diplomations — 1 session par combo
    for d in DOUBLE_DIPLOMES:
        title = f"{title_by_code[d['licence']]} + {title_by_code[d['bachelor']]}"
        _upsert_session(f"SES-{d['code']}-2026", f"{title} — rentrée 2026",
                        d["code"], title, STD_OPENS, STD_CLOSES, STD_BAC,
                        DD_FEES["application"], 0)
    frappe.db.commit()
    return {"sessions_open": frappe.db.count("Admission Session", {"is_open": 1})}
