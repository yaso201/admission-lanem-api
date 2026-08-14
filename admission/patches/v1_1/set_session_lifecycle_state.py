"""GESTION-CALENDRIER — backfill du cycle de vie 3 états sur les sessions existantes.

is_open est désormais le MIROIR dérivé de lifecycle_state (la nouvelle source de vérité).
Migration NON destructive : is_open=1 → Open ; is_open=0 → Closed. Aucun brouillon préexistant
(la notion n'existait pas). db.set_value direct (pas de save : aucune règle à déclencher ici).
"""

import frappe


def execute():
    if not frappe.db.has_column("Admission Session", "lifecycle_state"):
        return
    rows = frappe.get_all("Admission Session", fields=["name", "is_open", "lifecycle_state"])
    n = 0
    for r in rows:
        if r.lifecycle_state:
            continue  # idempotent : déjà posé
        state = "Open" if r.is_open else "Closed"
        frappe.db.set_value("Admission Session", r.name, "lifecycle_state", state,
                            update_modified=False)
        n += 1
    frappe.db.commit()
    frappe.logger("session_lifecycle").info(
        f"set_session_lifecycle_state : {n} session(s) rétro-remplie(s)."
    )
