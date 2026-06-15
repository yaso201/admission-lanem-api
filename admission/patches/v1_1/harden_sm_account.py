"""LOT 0 (SM BACK-OFFICE) — rôle `Admission SM` + durcissement du compte super-admin.

Config-as-code idempotente (même esprit que set_password_policy.py). Réf :
SPEC-ADMISSION-SM-BACKOFFICE §3 et §5 (décisions D2/D4).

Rôle `Admission SM` (super-admin de DOMAINE, NON root) :
  - desk_access = 0 (pas de Desk Frappe : surface réduite aux pages `management` durcies).
  - two_factor_auth = 1 (2FA exigée par le RÔLE, pas seulement par le global).
Le System Manager natif (= root OPS / break-glass) reçoit AUSSI two_factor_auth = 1.

Durcissement (System Settings — global Frappe ; pas de per-rôle natif pour session/sessions) :
  - enable_two_factor_auth = 1 + two_factor_method = "OTP App" (TOTP).
    NB Frappe : la 2FA ne se déclenche QUE pour les users dont un rôle porte two_factor_auth=1
    (twofactor.two_factor_is_enabled_for_) → le staff ordinaire (Administratif/Responsable/
    Direction/Finance) N'EST PAS impacté ; seuls Admission SM et System Manager le sont.
    `Administrator` est exempt nativement (soupape ultime — cf. runbook break-glass).
  - session_expiry = "04:00" (idle timeout 4 h ; GLOBAL → s'applique à tout le staff. Choix
    défense-en-profondeur raisonnable pour une petite équipe manipulant de la PII ; Frappe
    n'offre pas d'expiration de session par rôle).
  - deny_multiple_sessions = 1 (une session active par user ; GLOBAL également).

Aucun secret posé ici (D1). Idempotent : rejeu = no-op.
⚠️ Pré-requis OPS AVANT activation : 2 comptes break-glass scellés (RUNBOOK-SM-BACKOFFICE).
"""

import frappe

SM_ROLE = "Admission SM"

SYSTEM_SETTINGS = {
    "enable_two_factor_auth": 1,
    # PHASE 1 (validé 15/06) : Email (pas d'app, SMTP recette OK, envoi 2FA synchrone).
    # Migration vers "OTP App" plus tard = simple flip de ce réglage.
    "two_factor_method": "Email",
    # session_expiry / deny_multiple_sessions : DIFFÉRÉS (durcissement session ultérieur).
}


def execute():
    # 1) Rôle applicatif super-admin de domaine (non root : pas de Desk).
    if not frappe.db.exists("Role", SM_ROLE):
        frappe.get_doc({
            "doctype": "Role",
            "role_name": SM_ROLE,
            "desk_access": 0,
            "two_factor_auth": 1,
        }).insert(ignore_permissions=True)
    else:
        # idempotence : garantir les attributs même si le rôle préexiste.
        role = frappe.get_doc("Role", SM_ROLE)
        if role.desk_access or not role.two_factor_auth:
            role.desk_access = 0
            role.two_factor_auth = 1
            role.save(ignore_permissions=True)

    # 2) 2FA exigée aussi pour le root OPS / break-glass (System Manager).
    if not frappe.db.get_value("Role", "System Manager", "two_factor_auth"):
        frappe.db.set_value("Role", "System Manager", "two_factor_auth", 1)

    # 3) Durcissement global (System Settings) — idempotent.
    ss = frappe.get_single("System Settings")
    changed = False
    for field, value in SYSTEM_SETTINGS.items():
        if str(ss.get(field)) != str(value):
            ss.set(field, value)
            changed = True
    if changed:
        ss.flags.ignore_mandatory = True
        ss.save(ignore_permissions=True)
