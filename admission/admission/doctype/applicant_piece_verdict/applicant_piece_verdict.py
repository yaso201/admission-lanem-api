import frappe
from frappe.model.document import Document


class ApplicantPieceVerdict(Document):
	"""Lot 3c-1 — trace append-only des verdicts documentaires par pièce (qui, quand, quoi,
	motif). Une ligne par action staff (verify/reject/require/waive) ou re-upload candidat (reset).
	Miroir du pattern Admission Applicant Transition Log."""

	pass
