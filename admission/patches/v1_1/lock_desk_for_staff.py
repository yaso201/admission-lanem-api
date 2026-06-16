"""Verrouille le Desk Frappe (/app) pour le staff : desk_access=0 sur les 4 rôles staff.

Seuls Administrator / System Manager (OPS) gardent l'accès à /app ; le staff ET l'Admission SM
(déjà desk_access=0) passent uniquement par le front management. VÉRIFIÉ : un user en
desk_access=0 (→ Website User) garde l'accès API du front (list_dossiers whitelisté + get_list
REST selon DocPerms). Recalcule aussi user_type des users staff qui n'ont plus aucun rôle desk.
Config-as-code idempotente."""

import frappe

STAFF_ROLES = ("Admission Administratif", "Admission Responsable",
               "Admission Direction", "Admission Finance")


def execute():
    for role in STAFF_ROLES:
        if frappe.db.exists("Role", role):
            frappe.db.set_value("Role", role, "desk_access", 0)
    # Recalcule user_type : les users sans AUCUN rôle desk deviennent Website User
    # (cohérence avec user.py:313 ; bloque /app même sans re-save complet).
    users = set(frappe.get_all("Has Role", filters={"role": ["in", STAFF_ROLES]}, pluck="parent"))
    for email in users:
        if email == "Administrator":
            continue
        u = frappe.get_doc("User", email)
        if u.user_type == "System User" and not u.has_desk_access():
            frappe.db.set_value("User", email, "user_type", "Website User")
    frappe.db.commit()
