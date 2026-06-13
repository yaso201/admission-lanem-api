"""Admission Programme — catalogue de l'offre (miroir d'agrégation).

Marketing ← vitrine/Sveltia ; frais ← UF (Fee Catalog, par programme_code).
Une double-diplomation est un programme autonome reliant une Licence + un Bachelor
(cf. specifications/MODELE-CATALOGUE-FORMATIONS.md).
"""
import frappe
from frappe.model.document import Document


def validate_programme(doc):
    """Invariants (fonction pure testable, appelée par le controller)."""
    is_dd = doc.parcours == "Double-Diplomation"
    if is_dd:
        if not (doc.dd_component_1 and doc.dd_component_2 and doc.dd_affinity):
            frappe.throw("Double-Diplomation : composant 1, composant 2 et affinité sont requis.")
        p1 = frappe.db.get_value("Admission Programme", doc.dd_component_1, "parcours")
        p2 = frappe.db.get_value("Admission Programme", doc.dd_component_2, "parcours")
        if p1 != "Licence":
            frappe.throw("Double-Diplomation : le composant 1 doit être une Licence.")
        if p2 != "Bachelor":
            frappe.throw("Double-Diplomation : le composant 2 doit être un Bachelor.")
    else:
        if doc.dd_component_1 or doc.dd_component_2 or doc.dd_affinity:
            frappe.throw("Les champs Double-Diplomation ne sont autorisés que pour ce parcours.")
    if doc.parcours in ("Bachelor", "Double-Diplomation") and not doc.partner:
        frappe.throw(f"Le parcours {doc.parcours} doit porter un partenaire (ex. ESIIA).")


class AdmissionProgramme(Document):
    def validate(self):
        validate_programme(self)
