import json

import frappe
from frappe.model.document import Document
from frappe.utils import now_datetime


class AdmissionApplicant(Document):
	def autoname(self):
		# Numéro de dossier structuré XXXXYYYNNNN (année académique + formation + compteur).
		# Remplace l'ancien CAN-AAAA-##### pour les NOUVEAUX dossiers (existants inchangés).
		from admission.api.numbering import build_dossier_name
		self.name = build_dossier_name(self)

	def validate(self):
		if not self.applicant_name:
			self.applicant_name = " ".join(filter(None, [self.first_name, self.last_name])).strip()
		if self.bac_profile == "bac_attente":
			self.conditionnel = 1

		self._validate_scholarships()

		old = self.get_doc_before_save()
		if old:
			old_status = old.status
			new_status = self.status
			if old_status != new_status:
				self.flags.status_changed_to = new_status
				self.flags.transition_from = old_status
				self.flags.transition_recorded = False
				if new_status == "ACC":
					self._on_accepted()
				elif new_status == "INS":
					self._gate_enrollment_fee_paid()
					self._gate_data_transfer_consent()

	def on_update(self):
		if getattr(self.flags, "status_changed_to", None) == "INS":
			self._trigger_bridge()
			self._trigger_double_check()
		self._record_transition()

	def _record_transition(self):
		"""SOCLE-0-AUDIT — journalise la transition de status (append-only, non-bloquant)."""
		if getattr(self.flags, "transition_recorded", False):
			return
		to_status = getattr(self.flags, "status_changed_to", None)
		if not to_status:
			return
		self.flags.transition_recorded = True  # idempotence : 1 transition = 1 entrée
		try:
			source, action = _detect_transition_context()
			write_transition_log(
				self.name,
				getattr(self.flags, "transition_from", None),
				to_status,
				actor=frappe.session.user,
				source=source,
				action=action,
				context={
					"session": self.session,
					"programme_code": self.programme_code,
					"level_code": self.level_code,
				},
			)
		except Exception:
			# Non-bloquant : un échec de journalisation ne casse JAMAIS la transition métier.
			frappe.logger("admission_applicant").warning(
				f"Transition log failed for {self.name}: {frappe.get_traceback()}"
			)

	def _on_accepted(self):
		from admission.api.public import _ensure_enrollment_fee
		try:
			_ensure_enrollment_fee(self)
		except Exception:
			frappe.logger("admission_applicant").warning(
				f"Enrollment fee auto-creation failed for {self.name}: {frappe.get_traceback()}"
			)

	def _gate_enrollment_fee_paid(self):
		from admission.api.public import _check_enrollment_fee_paid
		_check_enrollment_fee_paid(self.name)

	def _gate_data_transfer_consent(self):
		from admission.api.legal import _require_consent_record
		_require_consent_record(self.name, "DATA_TRANSFER")

	def _validate_scholarships(self):
		validated = json.loads(self.validated_scholarships or "[]")
		if not validated:
			return
		requested = json.loads(self.requested_scholarships or "[]")
		invalid = [k for k in validated if k not in requested]
		if invalid:
			frappe.throw(
				f"Bourses validees non demandees: {', '.join(invalid)}. "
				f"validated_scholarships doit etre un sous-ensemble de requested_scholarships."
			)

	def _trigger_bridge(self):
		from admission.api.bridge import enqueue_bridge_notification
		try:
			enqueue_bridge_notification(self.name)
		except Exception:
			frappe.logger("admission_applicant").warning(
				f"Bridge notification enqueue failed for {self.name}: {frappe.get_traceback()}"
			)

	def _trigger_double_check(self):
		from admission.api.bridge import enqueue_double_check
		try:
			enqueue_double_check(self.name)
		except Exception:
			frappe.logger("admission_applicant").warning(
				f"Double-check enqueue failed for {self.name}: {frappe.get_traceback()}"
			)


def _detect_transition_context():
	"""Déduit (source, action) du contexte runtime.

	Source DÉRIVÉE (best-effort) : fiable pour les chemins connus (Workflow natif, webhook,
	public_api) ; "system" hors requête (cron/script/test). Pour une source GARANTIE, il
	faudrait poser un flag dans les call sites (public.py/webhook.py) — hors write-set de ce lot.
	"""
	req = getattr(frappe.local, "request", None)
	path = getattr(req, "path", "") or ""
	form = getattr(frappe.local, "form_dict", {}) or {}
	if "apply_workflow" in path:
		return "workflow", form.get("action")
	if "admission.api.webhook" in path:
		return "webhook", None
	if "admission.api.public" in path:
		return "public_api", None
	if "admission.api.staff" in path:
		return "staff_api", None
	return ("staff_api" if req is not None else "system"), None


def write_transition_log(applicant, from_status, to_status, *, actor, source, action=None, result="ok", context=None):
	"""Insère une entrée de journal append-only (code-only, ignore_permissions).

	Aucune PII : uniquement des codes système (dossier_id, statuts, codes session/programme/niveau)
	et le compte acteur (sujet d'audit staff, requis A03 §10.2 — distinct de la PII candidat).
	"""
	frappe.get_doc(
		{
			"doctype": "Admission Applicant Transition Log",
			"applicant": applicant,
			"from_status": from_status,
			"to_status": to_status,
			"action": action,
			"transition_at": now_datetime(),
			"actor": actor,
			"source": source,
			"result": result,
			"context_snapshot": json.dumps(context or {}, ensure_ascii=False, default=str),
		}
	).insert(ignore_permissions=True)
