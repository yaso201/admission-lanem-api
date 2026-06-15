import frappe
from frappe.model.document import Document

# Modes exigeant un justificatif (scan) pour être confirmés — Précision 2 (anti-fraude, A03 §10).
PROOF_REQUIRED_MODES = ("Cash", "Bank")


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
		self._guard_justificatif()

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
