"""Expiration du lien d'invitation / réinitialisation de mot de passe = 24 h.

Le mail d'invitation (welcome staff) et de reset utilisent le même
`reset_password_link_expiry_duration` (System Settings), dont le défaut Frappe est
1200 s (20 min) — trop court pour une invitation. On le porte à 86400 s (24 h).
Config-as-code idempotente (même esprit que set_password_policy)."""

import frappe

EXPIRY_SECONDS = 86400  # 24 h


def execute():
    ss = frappe.get_single("System Settings")
    if str(ss.get("reset_password_link_expiry_duration") or "") != str(EXPIRY_SECONDS):
        ss.reset_password_link_expiry_duration = EXPIRY_SECONDS
        ss.flags.ignore_mandatory = True
        ss.save(ignore_permissions=True)
