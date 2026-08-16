import frappe
from frappe.model.document import Document

from admission.api._log import log_event


_SENSITIVE_FIELDS = ("applicant", "session", "person_id", "fee_type", "amount_xof", "status")


def _value(doc, fieldname):
	return doc.get(fieldname) if hasattr(doc, "get") else getattr(doc, fieldname, None)


class ApplicantFee(Document):
	def validate(self):
		if self.amount_xof is not None and self.amount_xof < 0:
			frappe.throw("Applicant fee amount cannot be negative.")
		if self.amount_xof == 0 and self.status != "Pending":
			frappe.throw("Zero amount applicant fees must remain pending.")
		self._warn_sm_sensitive_write()

	def _warn_sm_sensitive_write(self):
		old = self.get_doc_before_save()
		if not old or getattr(getattr(self, "flags", None), "ignore_permissions", False):
			return
		user = frappe.session.user
		if "System Manager" not in frappe.get_roles(user):
			return
		changed = [field for field in _SENSITIVE_FIELDS if _value(old, field) != _value(self, field)]
		if changed:
			log_event(
				"break_glass_sensitive_write", "allowed", ref=self.name, level="warning",
				actor=user, doctype="Applicant Fee", fields=",".join(changed),
			)
