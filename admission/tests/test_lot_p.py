"""Tests LOT P (pont INS : marquage + redrive + idempotence d'émission) + W3 (garde
d'état initiate_online_payment, B0.5/ASVS 11.1.5).

Style unitaire mocké, aligné suite existante.
"""

from unittest import TestCase
from unittest.mock import MagicMock, patch

import frappe as _real_frappe


def setUpModule():
    try:
        _real_frappe.local.flags
    except Exception:
        _real_frappe.local.flags = _real_frappe._dict(in_test=True)


BRIDGE = "admission.api.bridge"
STAFF = "admission.api.staff"


def _campus_config():
    return {"url": "https://campus.lanem.bj", "token": "tok"}


class TestBridgeMarking(TestCase):
    @patch(f"{BRIDGE}._build_financial_context", return_value={})
    @patch(f"{BRIDGE}._get_campus_config", return_value=_campus_config())
    @patch(f"{BRIDGE}._pii_transport_allowed", return_value=True)
    @patch(f"{BRIDGE}.requests")
    @patch(f"{BRIDGE}.frappe")
    def test_success_marks_notified(self, mf, mreq, _pii, _cfg, _ctx):
        applicant = MagicMock()
        applicant.bridge_notified = 0
        mf.get_doc.return_value = applicant
        resp = MagicMock()
        resp.json.return_value = {"message": {"status": "created_and_ins"}}
        mreq.post.return_value = resp
        mreq.RequestException = Exception
        from admission.api.bridge import _send_bridge_notification
        _send_bridge_notification("CAN-1")
        values = mf.db.set_value.call_args[0][2]
        self.assertEqual(values["bridge_notified"], 1)
        self.assertIn("bridge_notified_at", values)
        self.assertIsNone(values["bridge_last_error"])

    @patch(f"{BRIDGE}._build_financial_context", return_value={})
    @patch(f"{BRIDGE}._get_campus_config", return_value=_campus_config())
    @patch(f"{BRIDGE}._pii_transport_allowed", return_value=True)
    @patch(f"{BRIDGE}.requests")
    @patch(f"{BRIDGE}.frappe")
    def test_http_failure_marks_error_and_raises(self, mf, mreq, _pii, _cfg, _ctx):
        import requests as real_requests
        applicant = MagicMock()
        applicant.bridge_notified = 0
        mf.get_doc.return_value = applicant
        mreq.RequestException = real_requests.RequestException
        mreq.post.side_effect = real_requests.ConnectionError("campus down")
        from admission.api.bridge import _send_bridge_notification
        with self.assertRaises(real_requests.RequestException):
            _send_bridge_notification("CAN-1")
        values = mf.db.set_value.call_args[0][2]
        self.assertEqual(values["bridge_notified"], 0)
        self.assertIn("campus down", values["bridge_last_error"])

    @patch(f"{BRIDGE}._build_financial_context", return_value={})
    @patch(f"{BRIDGE}._get_campus_config", return_value=_campus_config())
    @patch(f"{BRIDGE}._pii_transport_allowed", return_value=True)
    @patch(f"{BRIDGE}.requests")
    @patch(f"{BRIDGE}.frappe")
    def test_business_error_marks_and_raises(self, mf, mreq, _pii, _cfg, _ctx):
        # Réponse 200 SANS statut métier de succès (ex. build_error_response auth) → ÉCHEC marqué.
        applicant = MagicMock()
        applicant.bridge_notified = 0
        mf.get_doc.return_value = applicant
        resp = MagicMock()
        resp.json.return_value = {"message": {"error": "Jeton API invalide.", "status_code": 401}}
        mreq.post.return_value = resp
        mreq.RequestException = Exception
        from admission.api.bridge import BridgeRejected, _send_bridge_notification
        with self.assertRaises(BridgeRejected):
            _send_bridge_notification("CAN-1")
        values = mf.db.set_value.call_args[0][2]
        self.assertEqual(values["bridge_notified"], 0)
        self.assertTrue(values["bridge_last_error"])

    @patch(f"{BRIDGE}.requests")
    @patch(f"{BRIDGE}.frappe")
    def test_already_notified_skips_post(self, mf, mreq):
        # P5 : idempotence d'émission — déjà acquitté → AUCUN POST (pas de double-envoi).
        applicant = MagicMock()
        applicant.bridge_notified = 1
        mf.get_doc.return_value = applicant
        from admission.api.bridge import _send_bridge_notification
        result = _send_bridge_notification("CAN-1")
        self.assertEqual(result["status"], "already_notified")
        mreq.post.assert_not_called()


class TestBridgeRedrive(TestCase):
    @patch(f"{BRIDGE}._alert_unbridged_inscriptions")
    @patch(f"{BRIDGE}._send_bridge_notification")
    @patch(f"{BRIDGE}.frappe")
    def test_redrive_resends_unacked_ins(self, mf, msend, malert):
        mf.get_all.return_value = ["CAN-1", "CAN-2"]
        from admission.api.bridge import redrive_bridge_notifications
        result = redrive_bridge_notifications()
        self.assertEqual(msend.call_count, 2)
        self.assertEqual(result, {"redriven": 2, "candidates": 2, "remaining": 0})
        malert.assert_not_called()
        # Cible : INS non acquittés uniquement
        filters = mf.get_all.call_args.kwargs.get("filters", {})
        self.assertEqual(filters.get("status"), "INS")
        self.assertEqual(filters.get("bridge_notified"), ("!=", 1))

    @patch(f"{BRIDGE}._alert_unbridged_inscriptions")
    @patch(f"{BRIDGE}._send_bridge_notification", side_effect=Exception("still down"))
    @patch(f"{BRIDGE}.frappe")
    def test_redrive_alerts_on_remaining(self, mf, msend, malert):
        mf.get_all.return_value = ["CAN-1"]
        from admission.api.bridge import redrive_bridge_notifications
        result = redrive_bridge_notifications()
        self.assertEqual(result["remaining"], 1)
        malert.assert_called_once_with(1)


class TestInitiateStateGuard(TestCase):
    """W3/B0.5 — frais 1 initiables depuis BRO/SOP/SOU seulement ; frais 2 depuis ACC."""

    def _call(self, mf, status, fee_type):
        mf.db.exists.return_value = True
        applicant = MagicMock()
        applicant.status = status
        mf.get_doc.return_value = applicant
        from admission.api.staff import initiate_online_payment
        return initiate_online_payment(dossier_id="CAN-1", fee_type=fee_type)

    @patch(f"{STAFF}.prepare_online_payment", return_value={"ok": 1})
    @patch(f"{STAFF}._ensure_fee", return_value=MagicMock())
    @patch(f"{STAFF}.frappe")
    def test_frais1_allowed_from_sop(self, mf, _fee, _prep):
        res = self._call(mf, "SOP", "application")
        self.assertTrue(res["ok"])

    @patch(f"{STAFF}.frappe")
    def test_frais1_refused_on_refused_dossier(self, mf):
        res = self._call(mf, "REF", "application")
        self.assertFalse(res["ok"])
        self.assertEqual(res["error"]["code"], "INVALID_STATE")

    @patch(f"{STAFF}.frappe")
    def test_frais2_refused_before_acceptance(self, mf):
        # Avant : créait le fee d'inscription dès l'ETU (incohérence argent/états).
        res = self._call(mf, "ETU", "enrollment")
        self.assertFalse(res["ok"])
        self.assertEqual(res["error"]["code"], "INVALID_STATE")

    @patch(f"{STAFF}.prepare_enrollment_online_payment", return_value={"ok": 1})
    @patch(f"{STAFF}._ensure_enrollment_fee", return_value=MagicMock())
    @patch(f"{STAFF}.frappe")
    def test_frais2_allowed_from_acc(self, mf, _fee, _prep):
        res = self._call(mf, "ACC", "enrollment")
        self.assertTrue(res["ok"])
