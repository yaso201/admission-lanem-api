import frappe
from frappe.model.document import Document


class AdmissionPromoCode(Document):
	def before_insert(self):
		# DEC-344 : le code vit NORMALISE en base (autoname field:code) — jamais deux casses.
		self.code = (self.code or "").strip().upper()
