"""Tests ADM-UF-5a -- Pont + acompte + promo capture + validation bourses.

Part A: _capture_promo_if_eligible (3 tests)
1. Captures active promo at candidature
2. No promo active → fields untouched
3. Multiple promos → sum of rates

Part B: Acompte ventilation (3 tests)
4. submit_enrollment_payment_online with acompte → total = frais2 + acompte
5. declare_enrollment_payment_offline with acompte → AFP = frais2 only, acompte stored
6. No acompte → backward compatible

Part C: Validated scholarships validation (2 tests)
7. validated ⊆ requested → OK
8. validated ⊄ requested → throw

Part D: Bridge financial context (3 tests)
9. _build_financial_context → correct payload
10. enqueue_bridge_notification → enqueues
11. enqueue_double_check → enqueues

Part E: Pont trigger at INS (2 tests)
12. INS transition triggers bridge + double-check
13. Non-INS transition does not trigger

Ref: ADM-UF-5a, §3bis-PROMO, §3ter.
"""

from __future__ import annotations

import hmac
import json
from datetime import date
from unittest import TestCase
from unittest.mock import MagicMock, patch, call

import frappe as _real_frappe


def setUpModule():
    try:
        _real_frappe.local.flags
    except Exception:
        _real_frappe.local.flags = _real_frappe._dict(in_test=True)


PUBLIC = "admission.api.public"
BRIDGE = "admission.api.bridge"
APPLICANT_MOD = (
    "admission.admission.doctype.admission_applicant.admission_applicant"
)


# ---------------------------------------------------------------------------
# Part A: _capture_promo_if_eligible
# ---------------------------------------------------------------------------

class TestCapturePromo(TestCase):

    @patch(f"{PUBLIC}._get_promotions_for_programme")
    @patch(f"{PUBLIC}.frappe")
    def test_captures_active_promo(self, mock_frappe, mock_promos):
        from admission.api.public import _capture_promo_if_eligible

        mock_promos.return_value = [
            {"mirror_key": "PROMO-EARLY-2026", "promo_name": "Early Bird", "rate": 0.10}
        ]
        applicant = MagicMock()
        applicant.programme_code = "LIS"
        applicant.promo_rate = None
        applicant.promo_code = None
        applicant.promo_captured_date = None

        _capture_promo_if_eligible(applicant)

        self.assertEqual(applicant.promo_rate, 0.10)
        self.assertEqual(applicant.promo_code, "PROMO-EARLY-2026")
        self.assertIsNotNone(applicant.promo_captured_date)
        applicant.save.assert_called_once_with(ignore_permissions=True)

    @patch(f"{PUBLIC}._get_promotions_for_programme")
    def test_no_promo_active(self, mock_promos):
        from admission.api.public import _capture_promo_if_eligible

        mock_promos.return_value = []
        applicant = MagicMock()
        applicant.programme_code = "LIS"
        applicant.promo_captured_date = None

        _capture_promo_if_eligible(applicant)

        applicant.save.assert_not_called()

    @patch(f"{PUBLIC}._get_promotions_for_programme")
    @patch(f"{PUBLIC}.frappe")
    def test_multiple_promos_sum_rates(self, mock_frappe, mock_promos):
        from admission.api.public import _capture_promo_if_eligible

        mock_promos.return_value = [
            {"mirror_key": "PROMO-A", "promo_name": "A", "rate": 0.05},
            {"mirror_key": "PROMO-B", "promo_name": "B", "rate": 0.03},
        ]
        applicant = MagicMock()
        applicant.programme_code = "LIS"
        applicant.promo_captured_date = None

        _capture_promo_if_eligible(applicant)

        self.assertAlmostEqual(applicant.promo_rate, 0.08, places=4)
        self.assertEqual(applicant.promo_code, "PROMO-A,PROMO-B")

    @patch(f"{PUBLIC}._get_promotions_for_programme")
    def test_idempotent_already_captured(self, mock_promos):
        """If promo already captured, skip (idempotency guard)."""
        from admission.api.public import _capture_promo_if_eligible

        applicant = MagicMock()
        applicant.programme_code = "LIS"
        applicant.promo_captured_date = "2026-08-14"

        _capture_promo_if_eligible(applicant)

        mock_promos.assert_not_called()
        applicant.save.assert_not_called()


# ---------------------------------------------------------------------------
# Part B: Acompte ventilation
# ---------------------------------------------------------------------------

class TestAcompteVentilationOnline(TestCase):

    @patch("admission.api.legal._record_consent", return_value="CONS-001")
    @patch("admission.api.legal._get_active_legal_document")
    @patch(f"{PUBLIC}._resolve_fee_from_catalog", return_value=500000)
    @patch(f"{PUBLIC}.secrets")
    @patch(f"{PUBLIC}._ensure_enrollment_fee")
    @patch(f"{PUBLIC}._get_applicant")
    @patch(f"{PUBLIC}.frappe")
    def test_online_with_acompte(self, mock_frappe, mock_get, mock_ensure, mock_secrets, mock_resolve, mock_legal, mock_record):
        from admission.api.public import submit_enrollment_payment_online

        applicant = MagicMock()
        applicant.name = "CAN-2026-00001"
        applicant.status = "ACC"
        applicant.acompte_xof = 0
        mock_get.return_value = applicant
        fee = MagicMock()
        fee.amount_xof = 50000
        mock_ensure.return_value = fee
        mock_secrets.token_hex.return_value = "abc"
        mock_frappe.form_dict = {}
        mock_frappe.request = None
        mock_legal.side_effect = lambda dt: MagicMock(name=f"LEGAL-{dt}")

        result = submit_enrollment_payment_online(
            dossier_id="CAN-2026-00001", token="tok", acompte_xof=100000,
            consent_refund=True, consent_data_transfer=True,
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["data"]["amount_xof"], 150000)
        self.assertEqual(result["data"]["ventilation"]["frais2"], 50000)
        self.assertEqual(result["data"]["ventilation"]["acompte"], 100000)
        self.assertEqual(applicant.acompte_xof, 100000)
        applicant.save.assert_called_once_with(ignore_permissions=True)


class TestAcompteVentilationOffline(TestCase):

    @patch("admission.api.legal._record_consent", return_value="CONS-001")
    @patch("admission.api.legal._get_active_legal_document")
    @patch(f"{PUBLIC}._resolve_fee_from_catalog", return_value=500000)
    @patch(f"{PUBLIC}.now_datetime", return_value="2026-06-09 12:00:00")
    @patch(f"{PUBLIC}._notify_uf_safe")
    @patch(f"{PUBLIC}._ensure_enrollment_fee")
    @patch(f"{PUBLIC}._get_applicant")
    @patch(f"{PUBLIC}.frappe")
    def test_offline_with_acompte(
        self, mock_frappe, mock_get, mock_ensure, mock_notify, mock_now,
        mock_resolve, mock_legal, mock_record,
    ):
        from admission.api.public import declare_enrollment_payment_offline

        applicant = MagicMock()
        applicant.name = "CAN-2026-00001"
        applicant.status = "ACC"
        applicant.acompte_xof = 0
        mock_get.return_value = applicant
        fee = MagicMock()
        fee.name = "AFF-001"
        fee.amount_xof = 50000
        mock_ensure.return_value = fee
        payment = MagicMock()
        payment.name = "REC-001"
        mock_frappe.get_doc.return_value = payment
        mock_frappe.form_dict = {}
        mock_frappe.request = None
        mock_legal.side_effect = lambda dt: MagicMock(name=f"LEGAL-{dt}")

        result = declare_enrollment_payment_offline(
            dossier_id="CAN-2026-00001", token="tok",
            mode="Cash", reference="REF-1", acompte_xof=100000,
            consent_refund=True, consent_data_transfer=True,
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["data"]["ventilation"]["frais2"], 50000)
        self.assertEqual(result["data"]["ventilation"]["acompte"], 100000)
        self.assertEqual(applicant.acompte_xof, 100000)

    @patch("admission.api.legal._record_consent", return_value="CONS-001")
    @patch("admission.api.legal._get_active_legal_document")
    @patch(f"{PUBLIC}.now_datetime", return_value="2026-06-09 12:00:00")
    @patch(f"{PUBLIC}._notify_uf_safe")
    @patch(f"{PUBLIC}._ensure_enrollment_fee")
    @patch(f"{PUBLIC}._get_applicant")
    @patch(f"{PUBLIC}.frappe")
    def test_offline_no_acompte_backward_compat(
        self, mock_frappe, mock_get, mock_ensure, mock_notify, mock_now,
        mock_legal, mock_record,
    ):
        from admission.api.public import declare_enrollment_payment_offline

        applicant = MagicMock()
        applicant.name = "CAN-2026-00002"
        applicant.status = "ACC"
        mock_get.return_value = applicant
        fee = MagicMock()
        fee.name = "AFF-002"
        fee.amount_xof = 50000
        mock_ensure.return_value = fee
        payment = MagicMock()
        payment.name = "REC-002"
        mock_frappe.get_doc.return_value = payment
        mock_frappe.form_dict = {}
        mock_frappe.request = None
        mock_legal.side_effect = lambda dt: MagicMock(name=f"LEGAL-{dt}")

        result = declare_enrollment_payment_offline(
            dossier_id="CAN-2026-00002", token="tok", mode="Bank", reference="REF-2",
            consent_refund=True, consent_data_transfer=True,
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["data"]["ventilation"]["acompte"], 0)


# ---------------------------------------------------------------------------
# Part C: Validated scholarships validation
# ---------------------------------------------------------------------------

class TestValidateScholarships(TestCase):

    def _make_applicant(self, requested, validated):
        doc = MagicMock()
        doc.requested_scholarships = json.dumps(requested)
        doc.validated_scholarships = json.dumps(validated)
        doc.applicant_name = "Test"
        doc.first_name = "Test"
        doc.last_name = "User"
        doc.bac_profile = ""
        doc.conditionnel = 0
        doc.get_doc_before_save.return_value = None
        return doc

    def test_valid_subset(self):
        from admission.admission.doctype.admission_applicant.admission_applicant import (
            AdmissionApplicant,
        )
        doc = self._make_applicant(
            ["BOURSE-TRES-BIEN", "BOURSE-BIEN"], ["BOURSE-TRES-BIEN"]
        )
        AdmissionApplicant._validate_scholarships(doc)

    @patch(f"{APPLICANT_MOD}.frappe")
    def test_invalid_superset(self, mock_frappe):
        from admission.admission.doctype.admission_applicant.admission_applicant import (
            AdmissionApplicant,
        )
        mock_frappe.throw.side_effect = Exception("validation")
        doc = self._make_applicant(
            ["BOURSE-BIEN"], ["BOURSE-TRES-BIEN"]
        )
        with self.assertRaises(Exception):
            AdmissionApplicant._validate_scholarships(doc)
        mock_frappe.throw.assert_called_once()


# ---------------------------------------------------------------------------
# Part D: Bridge financial context
# ---------------------------------------------------------------------------

class TestBuildFinancialContext(TestCase):

    @patch("admission.api.legal._get_consent_proof", return_value={"version_hash": "h", "accepted_at": "2026-06-09"})
    @patch(f"{BRIDGE}.frappe")
    def test_builds_correct_payload(self, mock_frappe, mock_proof):
        from admission.api.bridge import _build_financial_context

        session = MagicMock()
        session.academic_year = "2026-2027"
        mock_frappe.get_doc.return_value = session

        applicant = MagicMock()
        applicant.name = "CAN-001"
        applicant.person_id = "PERS-00001"
        applicant.session = "SES-2026-LIC"
        applicant.programme_code = "LIS"
        applicant.level_code = "LIS-L1"
        applicant.first_name = "Ama"
        applicant.last_name = "Koffi"
        applicant.email = "ama@x.bj"
        applicant.phone = "+22990000000"
        applicant.date_of_birth = "2006-04-12"
        applicant.validated_scholarships = json.dumps(["BOURSE-TRES-BIEN"])
        applicant.promo_code = "PROMO-EARLY-2026"
        applicant.promo_rate = 0.10
        applicant.promo_captured_date = "2026-08-02"
        applicant.acompte_xof = 100000

        ctx = _build_financial_context(applicant)

        self.assertEqual(ctx["person_id"], "PERS-00001")
        self.assertEqual(ctx["dossier_id"], "CAN-001")  # C3 : corrélation pont
        self.assertEqual(ctx["academic_year"], "2026-2027")
        self.assertEqual(ctx["program"], "LIS")
        self.assertEqual(ctx["academic_level"], "LIS-L1")
        # C3 (T4) : identité minimale pour la création headless du SA campus
        self.assertEqual(ctx["identite"]["prenom"], "Ama")
        self.assertEqual(ctx["identite"]["nom"], "Koffi")
        self.assertEqual(ctx["identite"]["email"], "ama@x.bj")
        self.assertEqual(ctx["identite"]["tel"], "+22990000000")
        self.assertEqual(ctx["identite"]["date_naissance"], "2006-04-12")
        self.assertEqual(ctx["bourses_validees"], ["BOURSE-TRES-BIEN"])
        self.assertAlmostEqual(ctx["promotion"]["rate"], 0.10)
        self.assertEqual(ctx["promotion"]["code"], "PROMO-EARLY-2026")
        self.assertTrue(ctx["acompte"]["present"])
        self.assertEqual(ctx["acompte"]["montant_xof"], 100000)
        self.assertTrue(ctx["acompte"]["encaisse"])
        self.assertIn("consent_data_transfer", ctx)
        self.assertEqual(ctx["consent_data_transfer"]["version_hash"], "h")

    @patch(f"{BRIDGE}.frappe")
    def test_enqueue_bridge(self, mock_frappe):
        from admission.api.bridge import enqueue_bridge_notification

        enqueue_bridge_notification("CAN-2026-00001")

        mock_frappe.enqueue.assert_called_once()
        args = mock_frappe.enqueue.call_args
        self.assertEqual(args.kwargs["applicant_name"], "CAN-2026-00001")

    @patch(f"{BRIDGE}.frappe")
    def test_enqueue_double_check(self, mock_frappe):
        from admission.api.bridge import enqueue_double_check

        enqueue_double_check("CAN-2026-00001")

        mock_frappe.enqueue.assert_called_once()
        args = mock_frappe.enqueue.call_args
        self.assertEqual(args.kwargs["applicant_name"], "CAN-2026-00001")


# ---------------------------------------------------------------------------
# Part F (C3-ENROLL): statut métier du pont — ADM-DEBT-58
# ---------------------------------------------------------------------------


class TestBridgeBusinessStatus(TestCase):
    """ADM-DEBT-58 : un HTTP 200 portant not_found/error est un ÉCHEC (raise → retry →
    Error Log), plus jamais un succès silencieux."""

    def test_bridge_success_statuses_pass(self):
        from admission.api.bridge import BRIDGE_SUCCESS_STATUSES, _check_business_status
        for s in ("ok", "already_ins", "created_and_ins"):
            status = _check_business_status(
                {"message": {"status": s}}, BRIDGE_SUCCESS_STATUSES,
                step="bridge_inscription", dossier_id="CAN-001",
            )
            self.assertEqual(status, s)

    def test_not_found_and_error_raise(self):
        from admission.api.bridge import BRIDGE_SUCCESS_STATUSES, BridgeRejected, _check_business_status
        for s in ("not_found", "error", None):
            with self.assertRaises(BridgeRejected):
                _check_business_status(
                    {"message": {"status": s}} if s else {"message": "ok-string"},
                    BRIDGE_SUCCESS_STATUSES, step="bridge_inscription", dossier_id="CAN-001",
                )

    def test_uf_reconciliation_statuses(self):
        from admission.api.bridge import (
            UF_RECONCILIATION_SUCCESS_STATUSES,
            BridgeRejected,
            _check_business_status,
        )
        for s in ("reconciled", "stored_pending"):  # stored_pending = succès (rejouera)
            self.assertEqual(
                _check_business_status({"message": {"status": s}}, UF_RECONCILIATION_SUCCESS_STATUSES,
                                       step="uf_double_check", dossier_id="CAN-001"), s)
        with self.assertRaises(BridgeRejected):
            _check_business_status({"message": {"status": "error"}}, UF_RECONCILIATION_SUCCESS_STATUSES,
                                   step="uf_double_check", dossier_id="CAN-001")

    @patch(f"{BRIDGE}._build_financial_context", return_value={})
    @patch(f"{BRIDGE}._pii_transport_allowed", return_value=True)
    @patch(f"{BRIDGE}._get_campus_config", return_value={"url": "http://campus", "token": "k"})
    @patch(f"{BRIDGE}.requests")
    @patch(f"{BRIDGE}.frappe")
    def test_send_bridge_raises_on_not_found_for_retry(self, mf, mreq, mcfg, mpii, mctx):
        # Le raise déclenche le retry de l'enqueue (retry=3) puis l'Error Log natif (OBS-1)
        from admission.api.bridge import BridgeRejected, _send_bridge_notification

        applicant = MagicMock(); applicant.bridge_notified = 0  # P5 : pas déjà acquitté
        mf.get_doc.return_value = applicant
        resp = MagicMock()
        resp.json.return_value = {"message": {"status": "not_found", "person_id": "PERS-X"}}
        mreq.post.return_value = resp
        # BridgeRejected ne doit PAS être avalé par le except RequestException
        mreq.RequestException = Exception  # jamais matché ici

        with self.assertRaises(BridgeRejected):
            _send_bridge_notification("CAN-001")

    @patch(f"{BRIDGE}._build_financial_context", return_value={})
    @patch(f"{BRIDGE}._pii_transport_allowed", return_value=True)
    @patch(f"{BRIDGE}._get_campus_config", return_value={"url": "http://campus", "token": "k"})
    @patch(f"{BRIDGE}.requests")
    @patch(f"{BRIDGE}.frappe")
    def test_send_bridge_success_on_created_and_ins(self, mf, mreq, mcfg, mpii, mctx):
        from admission.api.bridge import _send_bridge_notification

        applicant = MagicMock(); applicant.bridge_notified = 0  # P5 : pas déjà acquitté
        mf.get_doc.return_value = applicant
        resp = MagicMock()
        resp.json.return_value = {"message": {"status": "created_and_ins", "student_applicant": "SA-1"}}
        mreq.post.return_value = resp

        result = _send_bridge_notification("CAN-001")

        self.assertEqual(result["message"]["status"], "created_and_ins")


# ---------------------------------------------------------------------------
# Part E: Pont trigger at INS
# ---------------------------------------------------------------------------

class TestPontTriggerINS(TestCase):

    def test_ins_triggers_bridge_and_doublecheck(self):
        from admission.admission.doctype.admission_applicant.admission_applicant import (
            AdmissionApplicant,
        )
        doc = MagicMock()
        doc.applicant_name = "Test"
        doc.first_name = "Test"
        doc.last_name = "User"
        doc.bac_profile = ""
        doc.conditionnel = 0
        doc.validated_scholarships = "[]"
        doc.requested_scholarships = "[]"
        doc.status = "INS"
        doc._gate_enrollment_fee_paid = MagicMock()
        doc._trigger_bridge = MagicMock()
        doc._trigger_double_check = MagicMock()
        doc._validate_scholarships = MagicMock()
        doc._on_accepted = MagicMock()

        old = MagicMock()
        old.status = "ACC"
        doc.get_doc_before_save.return_value = old

        AdmissionApplicant.validate(doc)
        AdmissionApplicant.on_update(doc)

        doc._gate_enrollment_fee_paid.assert_called_once()
        doc._trigger_bridge.assert_called_once()
        doc._trigger_double_check.assert_called_once()

    def test_non_ins_does_not_trigger(self):
        from admission.admission.doctype.admission_applicant.admission_applicant import (
            AdmissionApplicant,
        )
        doc = MagicMock()
        doc.applicant_name = "Test"
        doc.first_name = "Test"
        doc.last_name = "User"
        doc.bac_profile = ""
        doc.conditionnel = 0
        doc.validated_scholarships = "[]"
        doc.requested_scholarships = "[]"
        doc.status = "ETU"
        doc._validate_scholarships = MagicMock()
        doc._trigger_bridge = MagicMock()
        doc._trigger_double_check = MagicMock()

        old = MagicMock()
        old.status = "SOU"
        doc.get_doc_before_save.return_value = old

        AdmissionApplicant.validate(doc)
        AdmissionApplicant.on_update(doc)

        doc._trigger_bridge.assert_not_called()
        doc._trigger_double_check.assert_not_called()


# ---------------------------------------------------------------------------
# Part F: Capture promo at payment, not creation (Point 1 Option B)
# ---------------------------------------------------------------------------

WEBHOOK = "admission.api.webhook"


class TestCapturePromoAtPayment(TestCase):
    """Verify promo captured at frais 1 PAYMENT, not at dossier creation."""

    @patch(f"{PUBLIC}._capture_promo_if_eligible")
    @patch(f"{PUBLIC}._ensure_fee")
    @patch(f"{PUBLIC}._sync_pieces")
    @patch(f"{PUBLIC}._pieces_for_profile")
    @patch(f"{PUBLIC}._classify_bac_date")
    @patch(f"{PUBLIC}._resolve_person_from_campus", return_value="PERS-001")
    @patch(f"{PUBLIC}._generate_token", return_value="tok")
    @patch(f"{PUBLIC}._session_doc")
    @patch(f"{PUBLIC}.frappe")
    def test_create_dossier_does_not_capture(
        self, mock_frappe, mock_session, mock_token, mock_resolve,
        mock_classify, mock_pieces_for, mock_sync_pieces,
        mock_ensure, mock_capture,
    ):
        from admission.api.public import create_dossier

        session = MagicMock()
        session.programme_code = "LIS"
        session.programme_label = "Licence"
        session.name = "SES-001"
        mock_session.return_value = session
        mock_frappe.form_dict = {
            "session": "SES-001",
            "identite": {"prenom": "Koffi", "nom": "Test", "email": "k@t.com"},
        }
        mock_frappe.request = None
        mock_frappe.get_all.return_value = []
        applicant = MagicMock()
        applicant.name = "CAN-001"
        applicant.status = "BRO"
        applicant.bac_date = None
        mock_frappe.get_doc.return_value = applicant

        create_dossier()

        mock_capture.assert_not_called()

    @patch("admission.api.public._capture_promo_if_eligible")
    @patch(f"{WEBHOOK}.send_payment_receipt")
    @patch(f"{WEBHOOK}.verify_transaction", return_value={"status": "SUCCESS", "amount": 25000})
    @patch(f"{WEBHOOK}.now_datetime", return_value="2026-06-09 12:00:00")
    @patch(f"{WEBHOOK}._find_payment_by_reference")
    @patch(f"{WEBHOOK}.frappe")
    def test_webhook_frais1_captures_promo_via_cascade(
        self, mock_frappe, mock_find, mock_now, _mock_verify, _msend, mock_capture,
    ):
        # LOT KKIAPAY : auth en-tête x-kkiapay-secret + re-vérification provider, puis
        # PROMOTION du Pending lié (plus d'insert). C2-BOURSES/R1 : la capture vit DANS
        # la cascade partagée — confirmation frais 1 (fee_type application) → capture (DEC-228).
        import json as _json
        from admission.api.webhook import payment

        secret = "whsecret"
        mock_frappe.conf = {"admission_payment_webhook_secret": secret}
        mock_frappe.request.data = _json.dumps(
            {"transactionId": "TX-1", "event": "transaction.success",
             "amount": 25000, "stateData": {"reference": "REF-1"}})
        mock_frappe.get_request_header.return_value = secret
        mock_frappe.session.user = "Administrator"

        pending = MagicMock()
        pending.payment_status = "Pending"
        pending.name = "REC-001"; pending.applicant = "CAN-001"
        pending.applicant_fee = "AFF-001"; pending.amount_xof = 25000
        mock_find.return_value = pending

        applicant = MagicMock()
        applicant.status = "BRO"
        fee = MagicMock()
        fee.name = "AFF-001"
        fee.amount_xof = 25000
        fee.fee_type = "application"  # frais 1 → la cascade capture
        mock_frappe.get_doc.side_effect = lambda dt, name=None: (
            applicant if dt == "Admission Applicant" else fee)

        payment()

        mock_capture.assert_called_once_with(applicant)

    @patch("admission.api.legal._record_consent", return_value="CONS-001")
    @patch("admission.api.legal._get_active_legal_document")
    @patch(f"{PUBLIC}.now_datetime", return_value="2026-06-09 12:00:00")
    @patch(f"{PUBLIC}._notify_uf_safe")
    @patch(f"{PUBLIC}._capture_promo_if_eligible")
    @patch(f"{PUBLIC}._ensure_fee")
    @patch(f"{PUBLIC}._get_applicant")
    @patch(f"{PUBLIC}.frappe")
    def test_offline_declare_does_not_capture(
        self, mock_frappe, mock_get, mock_ensure, mock_capture, mock_notify, mock_now,
        mock_legal, mock_record,
    ):
        # C2-BOURSES/R1 (DEC-228) : un declare offline (Pending) ne fige RIEN — la promo est
        # capturée à la CONFIRMATION (cascade partagée). Un declare rejeté ne fige plus de taux.
        from admission.api.public import declare_payment_offline

        applicant = MagicMock()
        applicant.name = "CAN-001"
        applicant.status = "BRO"
        mock_get.return_value = applicant

        fee = MagicMock()
        fee.name = "AFF-001"
        fee.amount_xof = 25000
        mock_ensure.return_value = fee

        payment = MagicMock()
        payment.name = "REC-001"
        mock_frappe.get_doc.return_value = payment
        mock_frappe.form_dict = {}
        mock_frappe.request = None
        mock_legal.return_value = MagicMock(name="LEGAL-REFUND")

        declare_payment_offline(
            dossier_id="CAN-001", token="tok", mode="Cash", reference="REF-1",
            consent_refund=True,
        )

        mock_capture.assert_not_called()
