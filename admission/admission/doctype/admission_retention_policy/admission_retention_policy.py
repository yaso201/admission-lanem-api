import frappe
from frappe.model.document import Document


class AdmissionRetentionPolicy(Document):
	"""DAT-1 — Singleton des durées de conservation (loi 2017-20).

	Valeurs DEFAULT = placeholder ; le juriste/métier les règle. Le mécanisme de purge
	(admission.api.retention) fonctionne avec ces défauts tant qu'elles ne sont pas fixées.
	"""

	pass
