"""Tests OBS-2 — instrumentation structurée aux points clés + fermeture des fuites PII {result}.

Sécurité-critique : les 3 fuites {result} (echo serveur, recon OBS-2) sont remplacées par
log_event (clés sûres uniquement) ; corrélation dossier_id sur les pas backend + endpoints OTP.

Ref: OBS-2, DAT-1/DAT-2 (aucune PII en clair dans les logs).
"""

from __future__ import annotations

from unittest import TestCase
from unittest.mock import MagicMock, patch

import frappe as _real_frappe
from frappe.utils import get_datetime


def setUpModule():
    try:
        _real_frappe.local.flags
    except Exception:
        _real_frappe.local.flags = _real_frappe._dict(in_test=True)


PUBLIC = "admission.api.public"
NOTIFY = "admission.api.notify_uf"
BRIDGE = "admission.api.bridge"

CAMPUS_CFG = {"url": "https://campus:8000", "token": "tok"}
UF_CFG = {"url": "https://uf:8000", "api_key": "k", "api_secret": "s"}
NOW = "2026-06-10 12:00:00"


def _assert_no_pii(testcase, mock_log, *needles):
    for c in mock_log.call_args_list:
        blob = repr(c)
        for n in needles:
            testcase.assertNotIn(n, blob, f"PII/raw-result fuit dans un log_event: {n}")


class TestPersonResolveNoPiiLeak(TestCase):
    """🔴 _resolve_person_from_campus : no-person_id ne loggue PLUS le dict campus (PII pré-insert)."""

    @patch(f"{PUBLIC}.log_event")
    @patch(f"{PUBLIC}.requests.post")
    @patch(f"{PUBLIC}._pii_transport_allowed", return_value=True)
    @patch(f"{PUBLIC}._get_campus_config", return_value=CAMPUS_CFG)
    @patch(f"{PUBLIC}.frappe")
    def test_no_person_id_logs_event_without_result(self, mock_frappe, _cfg, _guard, mock_post, mock_log):
        resp = MagicMock()
        resp.json.return_value = {"ok": True, "data": {}, "email": "leak@x.com", "first_name": "LEAK"}
        resp.raise_for_status.return_value = None
        mock_post.return_value = resp
        from admission.api.public import _resolve_person_from_campus
        result = _resolve_person_from_campus("leak@x.com", "Jean", "K", "+229")
        self.assertIsNone(result)
        steps = [c for c in mock_log.call_args_list if c.args[:2] == ("person_resolve", "no_person_id")]
        self.assertTrue(steps, "person_resolve/no_person_id doit être logué")
        _assert_no_pii(self, mock_log, "leak@x.com", "LEAK")


class TestNotifyUfPaymentStructured(TestCase):
    """🟠 notify_uf_payment : succès → log_event(dossier_id, payment), jamais le dict de réponse UF."""

    @patch(f"{NOTIFY}.log_event")
    @patch(f"{NOTIFY}.now_datetime", return_value=get_datetime(NOW))
    @patch(f"{NOTIFY}._pii_transport_allowed", return_value=True)
    @patch(f"{NOTIFY}._get_uf_config", return_value=UF_CFG)
    @patch(f"{NOTIFY}.requests.post")
    @patch(f"{NOTIFY}.frappe")
    def test_success_logs_event_with_dossier_id_no_result(self, mock_frappe, mock_post, _cfg, _guard, _now, mock_log):
        resp = MagicMock()
        resp.json.return_value = {"id": "UF-1", "applicant_email": "leak@x.com", "applicant_first_name": "LEAK"}
        resp.raise_for_status.return_value = None
        mock_post.return_value = resp
        applicant = MagicMock(); applicant.name = "CAN-2026-00007"
        applicant.first_name = "Jean"; applicant.last_name = "K"
        payment = MagicMock(); payment.name = "REC-1"
        from admission.api.notify_uf import notify_uf_payment
        result = notify_uf_payment(applicant=applicant, fee=MagicMock(), payment=payment)
        self.assertTrue(result)
        calls = [c for c in mock_log.call_args_list if c.args[:2] == ("notify_uf_payment", "success")]
        self.assertTrue(calls)
        self.assertEqual(calls[0].kwargs.get("dossier_id"), "CAN-2026-00007")
        _assert_no_pii(self, mock_log, "leak@x.com", "LEAK")


class TestNotifyUfAbandonStructured(TestCase):
    """🟠 notify_uf_applicant_abandon : succès → log_event(dossier_id), jamais le dict de réponse."""

    @patch(f"{NOTIFY}.log_event")
    @patch(f"{NOTIFY}._pii_transport_allowed", return_value=True)
    @patch(f"{NOTIFY}._get_uf_config", return_value=UF_CFG)
    @patch(f"{NOTIFY}.requests.post")
    @patch(f"{NOTIFY}.frappe")
    def test_success_logs_event_no_result(self, mock_frappe, mock_post, _cfg, _guard, mock_log):
        resp = MagicMock()
        resp.json.return_value = {"id": "AB-1", "applicant_email": "leak@x.com", "applicant_last_name": "LEAK"}
        resp.raise_for_status.return_value = None
        mock_post.return_value = resp
        applicant = MagicMock(); applicant.name = "CAN-2026-00008"; applicant.status = "REF"
        applicant.first_name = "Koffi"; applicant.last_name = "M"
        from admission.api.notify_uf import notify_uf_applicant_abandon
        result = notify_uf_applicant_abandon(applicant)
        self.assertIsNotNone(result)
        calls = [c for c in mock_log.call_args_list if c.args[:2] == ("notify_uf_abandon", "success")]
        self.assertTrue(calls)
        self.assertEqual(calls[0].kwargs.get("dossier_id"), "CAN-2026-00008")
        _assert_no_pii(self, mock_log, "leak@x.com", "LEAK")


class TestBridgeStructured(TestCase):
    @patch(f"{BRIDGE}.log_event")
    @patch(f"{BRIDGE}._build_financial_context", return_value={"person_id": "PERS-1"})
    @patch(f"{BRIDGE}._pii_transport_allowed", return_value=True)
    @patch(f"{BRIDGE}._get_campus_config", return_value=CAMPUS_CFG)
    @patch(f"{BRIDGE}.requests.post")
    @patch(f"{BRIDGE}.frappe")
    def test_bridge_success_logs_event(self, mock_frappe, mock_post, _cfg, _guard, _ctx, mock_log):
        # C3/ADM-DEBT-58 : le succès exige désormais un statut MÉTIER de succès dans message.status
        resp = MagicMock(); resp.json.return_value = {"message": {"status": "ok"}}; resp.raise_for_status.return_value = None
        mock_post.return_value = resp
        from admission.api.bridge import _send_bridge_notification
        _send_bridge_notification("CAN-2026-00009")
        calls = [c for c in mock_log.call_args_list if c.args[:1] == ("bridge_inscription",)]
        self.assertTrue(calls)
        self.assertEqual(calls[0].kwargs.get("dossier_id"), "CAN-2026-00009")

    @patch(f"{BRIDGE}.log_event")
    @patch(f"{BRIDGE}._build_financial_context", return_value={"person_id": "PERS-1"})
    @patch(f"{BRIDGE}._pii_transport_allowed", return_value=True)
    @patch(f"{BRIDGE}._get_uf_config", return_value=UF_CFG)
    @patch(f"{BRIDGE}.requests.post")
    @patch(f"{BRIDGE}.frappe")
    def test_double_check_success_logs_event(self, mock_frappe, mock_post, _cfg, _guard, _ctx, mock_log):
        # C3/ADM-DEBT-58 : reconciled/stored_pending = succès métier (error → raise)
        resp = MagicMock(); resp.json.return_value = {"message": {"status": "reconciled"}}; resp.raise_for_status.return_value = None
        mock_post.return_value = resp
        from admission.api.bridge import _send_double_check
        _send_double_check("CAN-2026-00010")
        calls = [c for c in mock_log.call_args_list if c.args[:1] == ("uf_double_check",)]
        self.assertTrue(calls)
        self.assertEqual(calls[0].kwargs.get("dossier_id"), "CAN-2026-00010")


class TestEndpointCorrelation(TestCase):
    """Endpoints front (parcours) : log_event(step, dossier_id) au succès/échec."""

    @patch(f"{PUBLIC}.log_event")
    @patch(f"{PUBLIC}.now_datetime", return_value=get_datetime(NOW))
    @patch(f"{PUBLIC}._get_applicant")
    @patch(f"{PUBLIC}.frappe")
    def test_request_otp_logs_event(self, mock_frappe, mock_get, _now, mock_log):
        mock_frappe.conf.get.return_value = False  # pas de dev_otp
        applicant = MagicMock(); applicant.name = "CAN-2026-00011"
        mock_get.return_value = applicant
        from admission.api.public import request_otp
        request_otp(dossier_id="CAN-2026-00011", token="tok")
        calls = [c for c in mock_log.call_args_list if c.args[:2] == ("request_otp", "success")]
        self.assertTrue(calls)
        self.assertEqual(calls[0].kwargs.get("dossier_id"), "CAN-2026-00011")

    @patch(f"{PUBLIC}.log_event")
    @patch(f"{PUBLIC}.add_days", return_value=get_datetime("2026-06-17 12:00:00"))
    @patch(f"{PUBLIC}.get_datetime", return_value=get_datetime("2026-06-10 13:00:00"))
    @patch(f"{PUBLIC}.now_datetime", return_value=get_datetime(NOW))
    @patch(f"{PUBLIC}._get_applicant")
    @patch(f"{PUBLIC}.frappe")
    def test_verify_otp_success_logs_event(self, mock_frappe, mock_get, _now, _gd, _add, mock_log):
        from admission.api.public import verify_otp, _hash_otp
        mock_frappe.conf.get.return_value = "test-otp-secret"  # LOT-A2 : OTP en HMAC
        applicant = MagicMock()
        applicant.name = "CAN-2026-00012"
        applicant.otp_expires_at = "2026-06-10 13:00:00"
        applicant.otp_email_hash = _hash_otp("111111")
        applicant.otp_phone_hash = _hash_otp("222222")
        mock_get.return_value = applicant
        verify_otp(dossier_id="CAN-2026-00012", token="tok", email_otp="111111", phone_otp="222222")
        calls = [c for c in mock_log.call_args_list if c.args[:2] == ("verify_otp", "success")]
        self.assertTrue(calls)
        self.assertEqual(calls[0].kwargs.get("dossier_id"), "CAN-2026-00012")

    @patch(f"{PUBLIC}.log_event")
    @patch(f"{PUBLIC}.get_datetime", return_value=get_datetime("2026-06-10 13:00:00"))
    @patch(f"{PUBLIC}.now_datetime", return_value=get_datetime(NOW))
    @patch(f"{PUBLIC}._get_applicant")
    @patch(f"{PUBLIC}.frappe")
    def test_verify_otp_failed_logs_event(self, mock_frappe, mock_get, _now, _gd, mock_log):
        from admission.api.public import verify_otp, _hash
        applicant = MagicMock()
        applicant.name = "CAN-2026-00013"
        applicant.otp_expires_at = "2026-06-10 13:00:00"
        applicant.otp_email_hash = _hash("111111")
        applicant.otp_phone_hash = _hash("222222")
        mock_get.return_value = applicant
        verify_otp(dossier_id="CAN-2026-00013", token="tok", email_otp="WRONG", phone_otp="222222")
        calls = [c for c in mock_log.call_args_list if c.args[:2] == ("verify_otp", "failed")]
        self.assertTrue(calls)
        self.assertEqual(calls[0].kwargs.get("dossier_id"), "CAN-2026-00013")
