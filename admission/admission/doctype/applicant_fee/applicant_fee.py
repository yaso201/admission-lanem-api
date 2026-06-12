import frappe
from frappe.model.document import Document


class ApplicantFee(Document):
	def validate(self):
		if self.amount_xof is not None and self.amount_xof < 0:
			frappe.throw("Applicant fee amount cannot be negative.")
		if self.amount_xof == 0 and self.status != "Pending":
			frappe.throw("Zero amount applicant fees must remain pending.")

