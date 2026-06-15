"""LOT G (SM BACK-OFFICE) — données & conformité (`admin_data.py`).

admin_anonymize (motif obligatoire, idempotent, délègue à l'anonymiseur existant),
run_retention_now (délègue), get_audit_log (lecture Activity Log). DB-backed.
Réf : SPEC-ADMISSION-SM-BACKOFFICE §4 (G).
"""

from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from admission.api import admin_data as G

EMAIL = "lg-cand@lanem.test"


class TestAdminData(FrappeTestCase):

    def setUp(self):
        sessions = frappe.get_all("Admission Session", limit=1, pluck="name")
        if not sessions:
            self.skipTest("Aucune Admission Session seedée (décor requis).")
        doc = frappe.get_doc({
            "doctype": "Admission Applicant", "status": "BRO",
            "first_name": "Test", "last_name": "Conformite",
            "email": EMAIL, "phone": "+22900000003",
            "programme_code": "PREPA", "level_code": "PREPA-S1", "session": sessions[0],
        })
        doc.insert(ignore_permissions=True)
        self.name = doc.name

    def test_anonymize_requires_motif(self):
        self.assertEqual(
            G.admin_anonymize(dossier_id=self.name)["error"]["code"], "MOTIF_REQUIRED")

    def test_anonymize_delegates_and_flags(self):
        res = G.admin_anonymize(dossier_id=self.name, motif="injonction RGPD")
        self.assertTrue(res["ok"])
        self.assertEqual(int(frappe.db.get_value("Admission Applicant", self.name, "anonymized") or 0), 1)

    def test_anonymize_idempotent(self):
        frappe.db.set_value("Admission Applicant", self.name, "anonymized", 1)
        res = G.admin_anonymize(dossier_id=self.name, motif="x")
        self.assertTrue(res["data"]["idempotent"])

    def test_anonymize_unknown_dossier(self):
        self.assertEqual(
            G.admin_anonymize(dossier_id="CAN-0000-00000", motif="x")["error"]["code"], "INVALID_DOSSIER")

    def test_run_retention_delegates(self):
        with patch("admission.api.retention.scheduled_retention_run",
                   return_value={"otp_cleared": 0, "abandoned_anonymized": 0}) as m:
            res = G.run_retention_now()
        self.assertTrue(res["ok"])
        self.assertTrue(m.called)
        self.assertIn("otp_cleared", res["data"])

    def test_audit_log_reads_activity_log(self):
        res = G.get_audit_log(limit=10)
        self.assertTrue(res["ok"])
        self.assertIsInstance(res["data"]["entries"], list)
