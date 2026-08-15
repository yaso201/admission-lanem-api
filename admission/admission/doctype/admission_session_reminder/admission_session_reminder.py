"""CAL-AMEL (DEC-O) — idempotence des rappels calendrier : UNE notification par jalon par
session (patron flags J4/J6 des rappels pièces, sans polluer le doctype session).
Interne serveur (scheduler) — aucune lecture staff nécessaire."""

from frappe.model.document import Document


class AdmissionSessionReminder(Document):
	pass
