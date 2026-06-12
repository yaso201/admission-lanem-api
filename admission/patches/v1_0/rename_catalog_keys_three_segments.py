"""ADM-DEBT-64 — renomme les rows Admission Fee Catalog au format 2 segments
(program-fee_type, héritées de l'ancien contrôleur) vers 3 segments
(program-level-fee_type), le format des écrivains sync et du lecteur
_resolve_fee_from_catalog. Le level vient du level_code de la row (DEFAULT si vide).

Idempotent : une row déjà au bon nom est ignorée ; une collision (cible existante,
maintenue fraîche par le sync via db.set_value) est SKIPPÉE avec log — aucune perte
de données, revue manuelle si le cas se présente.
"""

import frappe


def execute():
    rows = frappe.get_all(
        "Admission Fee Catalog",
        fields=["name", "program_code", "level_code", "fee_type"],
    )
    for row in rows:
        expected = f"{row.program_code}-{row.level_code or 'DEFAULT'}-{row.fee_type}"
        if row.name == expected:
            continue
        if frappe.db.exists("Admission Fee Catalog", expected):
            frappe.logger("patches").warning(
                f"ADM-DEBT-64: collision {row.name} -> {expected} "
                f"(cible existante, row laissée en place pour revue manuelle)"
            )
            continue
        frappe.rename_doc("Admission Fee Catalog", row.name, expected, force=True)
        # Le champ autoname doit refléter le nouveau name (cohérence field:catalog_key).
        frappe.db.set_value(
            "Admission Fee Catalog", expected, "catalog_key", expected,
            update_modified=False,
        )
