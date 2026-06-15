"""Backfill des numbering_code (YYY) sur les Admission Programme existants.

Assigne, dans l'ordre de création, famille(parcours) + index suivant DANS la famille
(via assign_programme_numbering_code) → IS=201, RC=202, MI=203, bachelors 30x, DD 40x…
Idempotent (skip si déjà posé). Les NOUVEAUX programmes s'auto-assignent à la validation.
Réf : format dossier XXXXYYYNNNN (15/06/2026).
"""

import frappe

from admission.api.numbering import assign_programme_numbering_code


def execute():
    for name in frappe.get_all("Admission Programme", order_by="creation", pluck="name"):
        doc = frappe.get_doc("Admission Programme", name)
        if doc.get("numbering_code"):
            continue
        assign_programme_numbering_code(doc)
        if doc.get("numbering_code"):
            doc.db_set("numbering_code", doc.numbering_code, update_modified=False)
    frappe.db.commit()
