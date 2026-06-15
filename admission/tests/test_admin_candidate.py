"""LOT B (SM BACK-OFFICE) — support candidat (`admin_candidate.py`).

Couvre : purge throttle (`rl:`), reissue (rotation token + otp_verified=0 + lien AU CANDIDAT,
aucun token retourné, motif obligatoire), rectification PII (liste blanche, recompute
applicant_name, motif obligatoire), garde dossier anonymisé. DB-backed.

NB : reissue committe (miroir recover_dossier) → cleanup explicite du dossier (apprentissage
« commit en FrappeTestCase casse le rollback »). Réf : SPEC-ADMISSION-SM-BACKOFFICE §4 (B).
"""

from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from admission.api import admin_candidate as C

EMAIL = "lb-cand@lanem.test"


class TestAdminCandidate(FrappeTestCase):

    def setUp(self):
        sessions = frappe.get_all("Admission Session", limit=1, pluck="name")
        if not sessions:
            self.skipTest("Aucune Admission Session seedée sur le site (décor requis).")
        doc = frappe.get_doc({
            "doctype": "Admission Applicant", "status": "BRO",
            "first_name": "Test", "last_name": "Candidat",
            "email": EMAIL, "phone": "+22900000001",
            "programme_code": "PREPA", "level_code": "PREPA-S1", "session": sessions[0],
        })
        doc.insert(ignore_permissions=True)
        self.name = doc.name
        self.addCleanup(self._cleanup)

    def _cleanup(self):
        frappe.db.rollback()
        if frappe.db.exists("Admission Applicant", self.name):
            frappe.delete_doc("Admission Applicant", self.name, force=True, ignore_permissions=True)
            frappe.db.commit()

    # ── throttle ──
    def test_clear_throttle_requires_motif(self):
        self.assertEqual(
            C.clear_candidate_throttle(dossier_id=self.name)["error"]["code"], "MOTIF_REQUIRED")

    def test_clear_throttle_purges_rl_keys(self):
        raw = frappe.cache.make_key(f"rl:admission.api.public.request_otp:1.2.3.4:{self.name}")
        frappe.cache.setex(raw, 120, 1)
        self.assertTrue(frappe.cache.keys(frappe.cache.make_key(f"rl:*{self.name}")))
        res = C.clear_candidate_throttle(dossier_id=self.name, motif="candidat bloqué OTP")
        self.assertTrue(res["ok"])
        self.assertGreaterEqual(res["data"]["throttle_keys_cleared"], 1)
        self.assertFalse(frappe.cache.keys(frappe.cache.make_key(f"rl:*{self.name}")))

    # ── reissue ──
    def test_reissue_rotates_token_and_resets_otp_no_token_leak(self):
        old_hash = frappe.db.get_value("Admission Applicant", self.name, "dossier_token_hash")
        with patch("admission.api.notifications.send_recovery_link") as send:
            res = C.reissue_candidate_access(dossier_id=self.name, motif="boîte mail récupérée")
        self.assertTrue(res["ok"])
        self.assertNotIn("token", res["data"])              # aucun token à l'écran
        self.assertTrue(send.called)                         # lien envoyé AU CANDIDAT
        new_hash = frappe.db.get_value("Admission Applicant", self.name, "dossier_token_hash")
        self.assertNotEqual(new_hash, old_hash)              # token tourné
        self.assertEqual(int(frappe.db.get_value("Admission Applicant", self.name, "otp_verified") or 0), 0)

    def test_reissue_requires_motif(self):
        self.assertEqual(
            C.reissue_candidate_access(dossier_id=self.name)["error"]["code"], "MOTIF_REQUIRED")

    # ── rectification PII ──
    def test_rectify_whitelist_and_recompute_name(self):
        res = C.rectify_candidate_pii(
            dossier_id=self.name, fields={"first_name": "Téa", "last_name": "Koné"},
            motif="erreur de saisie signalée")
        self.assertTrue(res["ok"])
        row = frappe.db.get_value("Admission Applicant", self.name,
                                  ["first_name", "applicant_name"], as_dict=True)
        self.assertEqual(row.first_name, "Téa")
        self.assertEqual(row.applicant_name, "Téa Koné")

    def test_rectify_rejects_non_whitelisted_field(self):
        res = C.rectify_candidate_pii(dossier_id=self.name, fields={"status": "ACC"}, motif="x")
        self.assertEqual(res["error"]["code"], "FIELD_NOT_ALLOWED")

    def test_rectify_requires_motif(self):
        res = C.rectify_candidate_pii(dossier_id=self.name, fields={"phone": "+22900000002"})
        self.assertEqual(res["error"]["code"], "MOTIF_REQUIRED")

    # ── garde anonymisé ──
    def test_anonymized_blocks_support(self):
        frappe.db.set_value("Admission Applicant", self.name, "anonymized", 1)
        self.assertEqual(
            C.clear_candidate_throttle(dossier_id=self.name, motif="x")["error"]["code"],
            "DOSSIER_ANONYMIZED")
