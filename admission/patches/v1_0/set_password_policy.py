"""SOCLE-0-AUTH — politique de mot de passe staff (config-as-code, idempotent).

DEC-259 : auth staff = locale Frappe (login natif /app, pas de SSO). Ce patch pose une
politique raisonnable pour une petite équipe manipulant de la PII candidat :
  - enable_password_policy = 1   (politique de complexité active)
  - minimum_password_score = "3" (zxcvbn 0-4 ; 3 = difficilement devinable ; 2 par défaut)
  - force_user_to_reset_password = 0 (pas de rotation périodique forcée — aligné NIST)
Lockout (allow_consecutive_login_attempts / allow_login_after_fail) et session_expiry restent
au natif (raisonnables, pas de sur-contrainte). Aucun secret : uniquement des seuils.

Le logging d'authentification (A03 §10.1/§10.2) est couvert nativement (Activity Log via
on_session_creation→login_feed, auth fail, delete_session→logout_feed) — aucun complément requis.
"""

import frappe

DESIRED = {
    "enable_password_policy": 1,
    "minimum_password_score": "3",
    "force_user_to_reset_password": 0,
}


def execute():
    ss = frappe.get_single("System Settings")
    changed = False
    for field, value in DESIRED.items():
        if str(ss.get(field)) != str(value):
            ss.set(field, value)
            changed = True
    if changed:
        ss.flags.ignore_mandatory = True
        ss.save(ignore_permissions=True)
