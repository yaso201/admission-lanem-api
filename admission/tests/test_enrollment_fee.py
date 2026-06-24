"""Tests ADM-UF-1 -- Enrollment fee (frais 2): creation, gate ACC->INS.

Part A: _ensure_enrollment_fee (3 tests)
1. Creates enrollment fee with catalog amount
2. Idempotent -- returns existing
3. Catalog miss -- returns None

Part B: _check_enrollment_fee_paid (3 tests)
4. Throws when no enrollment fee
5. Throws when fee exists but not paid
6. Passes when fee paid (Confirmed)

Part C: _get_fee_and_payment (2 tests)
7. Returns fee+payment for matching types
8. Returns None,None when no match

Part D: Enrollment endpoints status guard (4 tests)
9. submit_enrollment_payment_online rejects non-ACC
10. submit_enrollment_payment_online accepts ACC
11. declare_enrollment_payment_offline rejects non-ACC
12. declare_enrollment_payment_offline accepts ACC, no status change

Ref: ADM-UF-1, gate A3 corrige.
"""

from __future__ import annotations

from unittest import TestCase
from unittest.mock import MagicMock, patch

import frappe as _real_frappe


def setUpModule():
    try:
        _real_frappe.local.flags
    except Exception:
        _real_frappe.local.flags = _real_frappe._dict(in_test=True)


PUBLIC = "admission.api.public"


# ---------------------------------------------------------------------------
# Part A: _ensure_enrollment_fee
# ---------------------------------------------------------------------------

class TestEnsureEnrollmentFee(TestCase):

    @patch(f"{PUBLIC}._resolve_fee_from_catalog", return_value=50000.0)
    @patch(f"{PUBLIC}.frappe")
    def test_creates_enrollment_fee_from_catalog(self, mock_frappe, mock_resolve):
        from admission.api.public import _ensure_enrollment_fee

        mock_frappe.get_all.return_value = []
        mock_fee = MagicMock()
        mock_frappe.get_doc.return_value = mock_fee

        applicant = MagicMock()
        applicant.name = "CAN-2026-00001"
        applicant.programme_code = "LIS"
        applicant.level_code = "LIS-L1"
        applicant.session = "SES-2026-LIC"
        applicant.person_id = "PERS-00001"

        result = _ensure_enrollment_fee(applicant)

        self.assertEqual(result, mock_fee)
        doc_arg = mock_frappe.get_doc.call_args[0][0]
        self.assertEqual(doc_arg["fee_type"], "enrollment")
        self.assertEqual(doc_arg["amount_xof"], 50000.0)
        self.assertEqual(doc_arg["applicant"], "CAN-2026-00001")
        mock_fee.insert.assert_called_once_with(ignore_permissions=True)
        mock_resolve.assert_called_once_with("LIS", "enrollment", "LIS-L1")

    @patch(f"{PUBLIC}.frappe")
    def test_idempotent_returns_existing(self, mock_frappe):
        from admission.api.public import _ensure_enrollment_fee

        mock_frappe.get_all.return_value = ["AFF-2026-00010"]
        existing = MagicMock()
        existing.name = "AFF-2026-00010"
        mock_frappe.get_doc.return_value = existing

        applicant = MagicMock()
        applicant.name = "CAN-2026-00001"

        result = _ensure_enrollment_fee(applicant)

        self.assertEqual(result, existing)
        mock_frappe.get_doc.assert_called_once_with("Applicant Fee", "AFF-2026-00010")

    @patch(f"{PUBLIC}._resolve_fee_from_catalog", return_value=None)
    @patch(f"{PUBLIC}.frappe")
    def test_catalog_miss_returns_none(self, mock_frappe, mock_resolve):
        from admission.api.public import _ensure_enrollment_fee

        mock_frappe.get_all.return_value = []

        applicant = MagicMock()
        applicant.name = "CAN-2026-00001"
        applicant.programme_code = "UNKNOWN"
        applicant.level_code = None

        result = _ensure_enrollment_fee(applicant)

        self.assertIsNone(result)
        mock_resolve.assert_called_once_with("UNKNOWN", "enrollment", None)


# ---------------------------------------------------------------------------
# Part B: _check_enrollment_fee_paid
# ---------------------------------------------------------------------------

class TestCheckEnrollmentFeePaid(TestCase):

    @patch(f"{PUBLIC}.frappe")
    def test_throws_no_enrollment_fee(self, mock_frappe):
        from admission.api.public import _check_enrollment_fee_paid

        mock_frappe.db.get_value.return_value = None
        mock_frappe.throw.side_effect = Exception("gate")

        with self.assertRaises(Exception):
            _check_enrollment_fee_paid("CAN-2026-00001")

        mock_frappe.throw.assert_called_once()
        self.assertIn("Aucun frais", mock_frappe.throw.call_args[0][0])

    @patch(f"{PUBLIC}.frappe")
    def test_throws_fee_not_confirmed(self, mock_frappe):
        from admission.api.public import _check_enrollment_fee_paid

        mock_frappe.db.get_value.return_value = "AFF-2026-00010"
        mock_frappe.db.exists.return_value = False
        mock_frappe.throw.side_effect = Exception("gate")

        with self.assertRaises(Exception):
            _check_enrollment_fee_paid("CAN-2026-00001")

        mock_frappe.throw.assert_called_once()
        self.assertIn("pas encore confirm", mock_frappe.throw.call_args[0][0])

    @patch(f"{PUBLIC}.frappe")
    def test_passes_when_confirmed(self, mock_frappe):
        from admission.api.public import _check_enrollment_fee_paid

        mock_frappe.db.get_value.return_value = "AFF-2026-00010"
        mock_frappe.db.exists.return_value = True

        _check_enrollment_fee_paid("CAN-2026-00001")

        mock_frappe.throw.assert_not_called()


# ---------------------------------------------------------------------------
# Part C: _get_fee_and_payment
# ---------------------------------------------------------------------------

class TestGetFeeAndPayment(TestCase):

    @patch(f"{PUBLIC}.frappe")
    def test_returns_fee_and_payment(self, mock_frappe):
        from admission.api.public import _get_fee_and_payment

        mock_fee = MagicMock()
        mock_fee.name = "AFF-2026-00001"
        mock_payment = MagicMock()

        mock_frappe.get_all.side_effect = [
            ["AFF-2026-00001"],
            ["REC-2026-00001"],
        ]
        mock_frappe.get_doc.side_effect = [mock_fee, mock_payment]

        fee, payment = _get_fee_and_payment("CAN-2026-00001", ["application"])

        self.assertEqual(fee, mock_fee)
        self.assertEqual(payment, mock_payment)

    @patch(f"{PUBLIC}.frappe")
    def test_returns_none_when_no_fee(self, mock_frappe):
        from admission.api.public import _get_fee_and_payment

        mock_frappe.get_all.return_value = []

        fee, payment = _get_fee_and_payment("CAN-2026-00001", ["enrollment"])

        self.assertIsNone(fee)
        self.assertIsNone(payment)


# ---------------------------------------------------------------------------
# Part D: Enrollment endpoints
# ---------------------------------------------------------------------------

class TestSubmitEnrollmentPaymentOnline(TestCase):

    @patch(f"{PUBLIC}._get_applicant")
    @patch(f"{PUBLIC}.frappe")
    def test_rejects_non_acc(self, mock_frappe, mock_get_applicant):
        from admission.api.public import submit_enrollment_payment_online

        applicant = MagicMock()
        applicant.status = "SOU"
        mock_get_applicant.return_value = applicant
        mock_frappe.form_dict = {}
        mock_frappe.request = None
        mock_frappe.local.response = {}

        result = submit_enrollment_payment_online(
            dossier_id="CAN-2026-00001", token="tok"
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "INVALID_STATE")

    @patch("admission.api.legal._record_consent", return_value="CONS-001")
    @patch("admission.api.legal._get_active_legal_document")
    @patch(f"{PUBLIC}.secrets")
    @patch(f"{PUBLIC}._ensure_enrollment_fee")
    @patch(f"{PUBLIC}._get_applicant")
    @patch(f"{PUBLIC}.frappe")
    def test_accepts_acc(self, mock_frappe, mock_get_applicant, mock_ensure, mock_secrets,
                         mock_legal, mock_record):
        from admission.api.public import submit_enrollment_payment_online

        applicant = MagicMock()
        applicant.name = "CAN-2026-00001"
        applicant.status = "ACC"
        applicant.acompte_xof = 0
        mock_get_applicant.return_value = applicant
        mock_fee = MagicMock()
        mock_fee.amount_xof = 50000
        mock_ensure.return_value = mock_fee
        mock_secrets.token_hex.return_value = "abc123"
        mock_frappe.form_dict = {}
        mock_frappe.request = None
        mock_legal.side_effect = lambda dt: MagicMock(name=f"LEGAL-{dt}")
        mock_frappe.db.exists.return_value = False   # garde amont B1 : aucun paiement Confirmed sur ce fee

        result = submit_enrollment_payment_online(
            dossier_id="CAN-2026-00001", token="tok",
            consent_refund=True, consent_data_transfer=True,
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["data"]["amount_xof"], 50000)
        self.assertEqual(result["data"]["provider"], "kkiapay")


class TestDeclareEnrollmentPaymentOffline(TestCase):

    @patch(f"{PUBLIC}._get_applicant")
    @patch(f"{PUBLIC}.frappe")
    def test_rejects_non_acc(self, mock_frappe, mock_get_applicant):
        from admission.api.public import declare_enrollment_payment_offline

        applicant = MagicMock()
        applicant.status = "ETU"
        mock_get_applicant.return_value = applicant
        mock_frappe.form_dict = {}
        mock_frappe.request = None
        mock_frappe.local.response = {}

        result = declare_enrollment_payment_offline(
            dossier_id="CAN-2026-00001", token="tok"
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "INVALID_STATE")

    @patch("admission.api.legal._record_consent", return_value="CONS-001")
    @patch("admission.api.legal._get_active_legal_document")
    @patch(f"{PUBLIC}.now_datetime", return_value="2026-06-09 12:00:00")
    @patch(f"{PUBLIC}._notify_uf_safe")
    @patch(f"{PUBLIC}._ensure_enrollment_fee")
    @patch(f"{PUBLIC}._get_applicant")
    @patch(f"{PUBLIC}.frappe")
    def test_creates_payment_no_status_change(
        self, mock_frappe, mock_get_applicant, mock_ensure, mock_notify, mock_now,
        mock_legal, mock_record,
    ):
        from admission.api.public import declare_enrollment_payment_offline

        applicant = MagicMock()
        applicant.name = "CAN-2026-00001"
        applicant.status = "ACC"
        mock_get_applicant.return_value = applicant
        mock_fee = MagicMock()
        mock_fee.name = "AFF-2026-00010"
        mock_fee.amount_xof = 50000
        mock_ensure.return_value = mock_fee
        mock_payment = MagicMock()
        mock_payment.name = "REC-2026-00012"
        mock_frappe.get_doc.return_value = mock_payment
        mock_frappe.form_dict = {}
        mock_frappe.request = None
        mock_legal.side_effect = lambda dt: MagicMock(name=f"LEGAL-{dt}")

        result = declare_enrollment_payment_offline(
            dossier_id="CAN-2026-00001", token="tok",
            mode="Bank", reference="REF-123",
            consent_refund=True, consent_data_transfer=True,
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["data"]["statut"], "ACC")
        self.assertEqual(result["data"]["payment_id"], "REC-2026-00012")
        mock_payment.insert.assert_called_once_with(ignore_permissions=True)
        mock_frappe.db.commit.assert_called_once()
        # #1 (AUDIT-UF / PAY-CONFIRM-AGENT phase b) : PLUS de notif UF au Pending.
        # UF est notifié à la CONFIRMATION (hook on_payment_update), pas à la déclaration offline.
        mock_notify.assert_not_called()
        applicant.save.assert_not_called()
