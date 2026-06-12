import re

import frappe
from frappe.model.document import Document


_ACADEMIC_YEAR_FORMAT = re.compile(r"^\d{4}-\d{4}$")


class AdmissionSession(Document):
	def validate(self):
		if self.opens_on and self.closes_on and self.opens_on > self.closes_on:
			frappe.throw("Admission session opening date cannot be after closing date.")
		if self.academic_year and not _ACADEMIC_YEAR_FORMAT.match(self.academic_year):
			frappe.throw(
				"academic_year doit être au format YYYY-YYYY (ex. 2026-2027). "
				f"Valeur reçue: {self.academic_year}"
			)

