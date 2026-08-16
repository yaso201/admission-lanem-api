"""Journal append-only des transferts de session d'un dossier d'admission."""

import frappe
from frappe import _
from frappe.model.document import Document


class AdmissionApplicantTransferLog(Document):
	def on_update(self):
		if self.get_doc_before_save() is not None:
			frappe.throw(_("Admission Applicant Transfer Log est append-only (immuable)."))
