"""Mint d'une session Frappe (bypass 2FA) pour un compte staff — E2E versionné.

Compte cible = env MINT_USER (défaut admin.admissions@lanem.bj). Lu via `bench console` (stdin).
Imprime `MINTED_SID::<sid>`. Aucun secret versionné : le compte est passé par l'appelant (env).
"""
import os

import frappe
from frappe.sessions import Session

frappe.local.form_dict = frappe._dict({"sid": "Guest"})
frappe.local.request_ip = "127.0.0.1"
user = os.environ.get("MINT_USER") or "admin.admissions@lanem.bj"
full_name = frappe.db.get_value("User", user, "full_name") or user
s = Session(user=user, resume=False, full_name=full_name, user_type="System User")
frappe.db.commit()
print("MINTED_SID::" + s.sid)
