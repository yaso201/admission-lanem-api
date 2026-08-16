import frappe
from frappe.model.document import Document

from admission.api._log import log_event

# Modes exigeant un justificatif (scan) pour être confirmés — Précision 2 (anti-fraude, A03 §10).
PROOF_REQUIRED_MODES = ("Cash", "Bank")
_SENSITIVE_FIELDS = (
	"applicant_fee", "applicant", "payment_mode", "source", "amount_xof",
	"payment_status", "paid_at", "justificatif", "provider", "provider_reference",
	"provider_transaction_id", "idempotency_key", "reconciliation",
	"uf_notified", "uf_notified_at", "confirmed_by",
)


def _value(doc, fieldname):
	return doc.get(fieldname) if hasattr(doc, "get") else getattr(doc, fieldname, None)


class ApplicantFeePayment(Document):
	def autoname(self):
		# Numéro de reçu structuré XXAANNNNN (année + source/canal + compteur).
		# Remplace l'ancien REC-AAAA-##### pour les NOUVEAUX reçus (existants inchangés).
		from admission.api.numbering import build_receipt_name
		self.name = build_receipt_name(self)
		self.receipt_number = self.name

	def before_insert(self):
		self._sync_receipt_number()

	def validate(self):
		self._sync_receipt_number()
		self._guard_confirmed_irreversible()
		self._guard_justificatif()
		self._warn_sm_sensitive_write()

	def _sync_receipt_number(self):
		if not self.receipt_number and self.name and not self.name.startswith("new-"):
			self.receipt_number = self.name

	def _guard_justificatif(self):
		"""Justificatif obligatoire pour confirmer un paiement espèce/banque ; immuable une fois Confirmed.

		Online exempté : la transaction KkiaPay (webhook) fait foi.
		"""
		old = self.get_doc_before_save()
		# Immuabilité : une fois Confirmed, le justificatif ne peut plus changer.
		if old and getattr(old, "payment_status", None) == "Confirmed":
			if self.justificatif != getattr(old, "justificatif", None):
				frappe.throw("Le justificatif d'un paiement confirmé est immuable.")
		# Obligation : confirmer un paiement Cash/Bank exige le justificatif (scan du reçu).
		if self.payment_status == "Confirmed" and self.payment_mode in PROOF_REQUIRED_MODES and not self.justificatif:
			frappe.throw(
				"Justificatif obligatoire pour confirmer un paiement espèce/banque (Cash/Bank)."
			)

	def _guard_confirmed_irreversible(self):
		"""NT-S/DEC-D — Confirmed ne revient jamais à un état antérieur, même en break-glass."""
		old = self.get_doc_before_save()
		if old and _value(old, "payment_status") == "Confirmed" and self.payment_status != "Confirmed":
			frappe.throw(
				"Un paiement confirmé est irréversible. Toute annulation exige un acte comptable dédié."
			)

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
				actor=user, doctype="Applicant Fee Payment", fields=",".join(changed),
			)
