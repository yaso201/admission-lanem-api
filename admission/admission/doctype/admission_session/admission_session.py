import re

import frappe
from frappe.model.document import Document
from frappe.utils import getdate


_ACADEMIC_YEAR_FORMAT = re.compile(r"^\d{4}-\d{4}$")
_LIFECYCLE_STATES = ("Draft", "Open", "Closed")


class AdmissionSession(Document):
	def validate(self):
		# GESTION-CALENDRIER : lifecycle_state est la SOURCE de vérité ; is_open en est le miroir
		# dérivé (Open ⟺ 1). Reconciliation à chaque save. Les inserts historiques (is_open posé,
		# lifecycle_state absent) dérivent l'état ; les nouveaux (duplication) posent l'état.
		self._sync_lifecycle_mirror()
		if self.academic_year and not _ACADEMIC_YEAR_FORMAT.match(self.academic_year):
			frappe.throw(
				"academic_year doit être au format YYYY-YYYY (ex. 2026-2027). "
				f"Valeur reçue: {self.academic_year}"
			)
		# Défense-en-profondeur : refuser sur tout save (y.c. Desk SysMgr) ce que la règle interdit
		# (avancer clôture/épreuve, toucher la structure d'une session publiée). Le verrou d'appel
		# direct principal reste le retrait du write REST + le passage obligé par les endpoints.
		self._enforce_change_rules()
		# A07 : cohérence INTER-CHAMPS (source unique) sur TOUT save — création et édition. Remplace
		# l'ancien contrôle opens/closes (élargi : closes>opens, exam>closes, début>appel).
		from admission.api.calendar_rules import enforce_coherence
		enforce_coherence({f: self.get(f) for f in (
			"opens_on", "closes_on", "exam_date", "exam_call_time", "exam_start_time", "bac_results_date")})
		# NOTES-CONCOURS (GN2) : les coefficients d'épreuve sont VERROUILLÉS dès la 1ʳᵉ note saisie
		# dans la session. 2ᵉ couche (défense en profondeur) — attrape aussi une édition Desk directe ;
		# la 1ʳᵉ couche est l'endpoint set_exam_coefficients. Hors calendar_rules (verrou propre).
		self._enforce_coefficient_lock()

	def _sync_lifecycle_mirror(self):
		if not self.lifecycle_state:
			self.lifecycle_state = "Open" if self.is_open else "Closed"
		elif self.lifecycle_state not in _LIFECYCLE_STATES:
			frappe.throw(f"lifecycle_state invalide : {self.lifecycle_state}")
		self.is_open = 1 if self.lifecycle_state == "Open" else 0

	def _enforce_change_rules(self):
		before = self.get_doc_before_save()
		if not before:
			return  # création : rien à contraindre (la duplication crée des brouillons)
		from admission.api.calendar_rules import enforce_document_change
		enforce_document_change(before, self)

	def _enforce_coefficient_lock(self):
		before = self.get_doc_before_save()
		if not before:
			return  # création : coefficients libres
		if (before.get("exam_coefficients") or "") == (self.get("exam_coefficients") or ""):
			return  # coefficients inchangés
		from admission.api.staff import coefficients_locked
		if coefficients_locked(self.name):
			frappe.throw(
				"Coefficients verrouillés : une note de concours a déjà été saisie dans cette "
				"session. La pondération ne se change pas une fois la correction commencée.",
				title="Coefficients verrouillés",
			)

