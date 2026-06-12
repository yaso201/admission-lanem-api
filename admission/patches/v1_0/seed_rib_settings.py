"""LOT RIB-SETTINGS — seed du compte d'encaissement (le RIB Coris actuel est VALIDÉ prod
par l'architecte) + rôle Admission Finance.

Seed depuis admission/seed/rib_coris_bank.pdf si présent (DEV/déploiements porteurs du
seed) ; sinon skip silencieux : la finance saisira via le Desk (procédure checklist §1).
"""

import hashlib
import os

import frappe


def execute():
    if not frappe.db.exists("Role", "Admission Finance"):
        frappe.get_doc({"doctype": "Role", "role_name": "Admission Finance",
                        "desk_access": 1}).insert(ignore_permissions=True)

    settings = frappe.get_doc("Admission Settings")
    if settings.rib_iban:  # déjà saisi (re-migration) → ne pas écraser la finance
        return

    settings.rib_banque = "CORIS BANK"
    settings.rib_titulaire = "LaNEM — La Nouvelle École des Métiers"
    settings.rib_iban = "BJ66 BJ21 2010 1400 6158 0241 0173"
    settings.rib_bic = "CORIBJBJ"

    seed_path = frappe.get_app_path("admission", "seed", "rib_coris_bank.pdf")
    if os.path.exists(seed_path):
        content = open(seed_path, "rb").read()
        digest = hashlib.sha256(content).hexdigest()
        from frappe.utils.file_manager import save_file
        f = save_file(f"RIB-LaNEM-v{digest[:8]}.pdf", content,
                      "Admission Settings", "Admission Settings", is_private=1)
        settings.rib_pdf = f.file_url
        settings.rib_pdf_hash = digest
        settings.rib_version = digest[:8]
    settings.flags.in_rib_rotation = True  # seed silencieux : pas de broadcast
    settings.save(ignore_permissions=True)
    frappe.db.commit()
