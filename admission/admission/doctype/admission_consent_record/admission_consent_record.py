import frappe
from frappe.model.document import Document


class AdmissionConsentRecord(Document):

    def before_save(self):
        if not self.is_new():
            frappe.throw(
                "Les enregistrements de consentement sont immuables. "
                "Impossible de modifier un Admission Consent Record existant."
            )

    def on_trash(self):
        # DAT-1 : preuve légale immuable (loi 2017-20 art. 29) — non supprimable, y compris
        # lors de l'effacement d'un dossier (on anonymise l'Applicant, on conserve la preuve).
        frappe.throw(
            "Un Admission Consent Record est une preuve légale (loi 2017-20 art. 29) et ne "
            "peut pas être supprimé. Anonymiser le dossier lié plutôt que de supprimer la preuve."
        )
