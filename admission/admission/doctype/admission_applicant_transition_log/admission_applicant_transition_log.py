"""Admission Applicant Transition Log — journal append-only des transitions de status.

SOCLE-0-AUDIT (DEC-261) : audit A03 §10.1/§10.2 ET aging SLA (une seule structure).
Alimenté depuis le contrôleur AdmissionApplicant (capture Workflow natif + status directs).
Append-only : aucune modification d'une entrée existante (double verrou : DocPerm read-only
+ cette garde). Insertion par code uniquement (ignore_permissions). Aucune PII.
"""

import frappe
from frappe import _
from frappe.model.document import Document


class AdmissionApplicantTransitionLog(Document):
    def on_update(self):
        # Append-only : une entrée déjà persistée ne peut jamais être modifiée.
        if self.get_doc_before_save() is not None:
            frappe.throw(_("Admission Applicant Transition Log est append-only (immuable)."))
