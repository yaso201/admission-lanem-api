"""Tests DAT-2 — client_ip fiable (source canonique Frappe, anti-XFF-spoofing)
+ garde PII-en-transit (_pii_transport_allowed : refuse http:// hors developer_mode).

(A) _get_client_ip → frappe.local.request_ip (source unique canonique) ; un
    X-Forwarded-For forgé par le client n'altère plus l'IP enregistrée.
(P1) _pii_transport_allowed refuse/loggue l'envoi de PII vers une URL http://
    hors developer_mode, câblé sur les 5 appels porteurs de PII (pas les catalogues).

Ref: DAT-2, AUDIT-GLOBAL (client_ip XFF spoofable ; PII en transit), loi 2017-20 art.29.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import MagicMock, patch

import frappe as _real_frappe


def setUpModule():
    try:
        _real_frappe.local.flags
    except Exception:
        _real_frappe.local.flags = _real_frappe._dict(in_test=True)


LEGAL = "admission.api.legal"
CONFIG = "admission.api._config"
NOTIFY = "admission.api.notify_uf"
BRIDGE = "admission.api.bridge"
PUBLIC = "admission.api.public"

CAMPUS_CFG = {"url": "https://campus:8000", "token": "tok"}
UF_CFG = {"url": "https://uf:8000", "api_key": "k", "api_secret": "s"}


# ---- Volet 1 : _get_client_ip = source canonique (request_ip), XFF ignoré ----

class TestClientIpCanonicalSource(TestCase):
    @patch(f"{LEGAL}.frappe")
    def test_uses_request_ip_ignores_spoofed_xff(self, mock_frappe):
        # XFF forgé par le client + request_ip canonique → on retient request_ip.
        mock_frappe.local = SimpleNamespace(request_ip="10.0.0.1")
        mock_frappe.request = SimpleNamespace(
            headers={"X-Forwarded-For": "1.2.3.4"}, remote_addr="9.9.9.9"
        )
        from admission.api.legal import _get_client_ip
        self.assertEqual(_get_client_ip(), "10.0.0.1")

    @patch(f"{LEGAL}.frappe")
    def test_empty_when_request_ip_unset(self, mock_frappe):
        # Pas de request_ip (offline/scheduler) → "" (ne retombe PAS sur remote_addr/XFF).
        mock_frappe.local = SimpleNamespace()
        mock_frappe.request = SimpleNamespace(headers={}, remote_addr="9.9.9.9")
        from admission.api.legal import _get_client_ip
        self.assertEqual(_get_client_ip(), "")


# ---- Volet 2 : _pii_transport_allowed (garde transport) ----

class TestPiiTransportGuard(TestCase):
    @patch(f"{CONFIG}.frappe")
    def test_https_allowed_no_log(self, mock_frappe):
        from admission.api._config import _pii_transport_allowed
        self.assertTrue(_pii_transport_allowed("https://uf:8000", context="x"))
        mock_frappe.log_error.assert_not_called()

    @patch(f"{CONFIG}.frappe")
    def test_http_blocked_in_prod_and_logged(self, mock_frappe):
        mock_frappe.conf.get.return_value = False  # developer_mode OFF
        from admission.api._config import _pii_transport_allowed
        self.assertFalse(_pii_transport_allowed("http://uf:8000", context="x"))
        mock_frappe.log_error.assert_called()  # trace OBS-1 (Error Log natif)

    @patch(f"{CONFIG}.frappe")
    def test_http_allowed_in_dev(self, mock_frappe):
        mock_frappe.conf.get.return_value = True  # developer_mode ON
        from admission.api._config import _pii_transport_allowed
        self.assertTrue(_pii_transport_allowed("http://uf:8000", context="x"))
        mock_frappe.log_error.assert_not_called()

    @patch(f"{CONFIG}.frappe")
    def test_empty_url_blocked(self, mock_frappe):
        mock_frappe.conf.get.return_value = False
        from admission.api._config import _pii_transport_allowed
        self.assertFalse(_pii_transport_allowed("", context="x"))


# ---- Volet 2 : câblage — la garde bloque l'envoi PII (return None, pas de POST) ----

class TestGuardWiringBlocks(TestCase):
    @patch(f"{NOTIFY}._pii_transport_allowed", return_value=False)
    @patch(f"{NOTIFY}._get_uf_config", return_value=UF_CFG)
    @patch(f"{NOTIFY}.requests.post")
    @patch(f"{NOTIFY}.frappe")
    def test_notify_uf_payment_blocked(self, mock_frappe, mock_post, _cfg, _guard):
        applicant = MagicMock(); applicant.name = "CAN-1"
        payment = MagicMock(); payment.name = "REC-1"
        from admission.api.notify_uf import notify_uf_payment
        result = notify_uf_payment(applicant=applicant, fee=MagicMock(), payment=payment)
        self.assertIsNone(result)
        mock_post.assert_not_called()
        # pas de uf_notified=1 → re-drive OBS-1 réessaiera une fois l'URL en https
        marks = [c for c in mock_frappe.db.set_value.call_args_list
                 if c[0][0] == "Applicant Fee Payment" and c[0][2].get("uf_notified") == 1]
        self.assertFalse(marks)

    @patch(f"{NOTIFY}._pii_transport_allowed", return_value=False)
    @patch(f"{NOTIFY}._get_uf_config", return_value=UF_CFG)
    @patch(f"{NOTIFY}.requests.post")
    @patch(f"{NOTIFY}.frappe")
    def test_notify_uf_abandon_blocked(self, mock_frappe, mock_post, _cfg, _guard):
        applicant = MagicMock(); applicant.name = "CAN-1"; applicant.status = "REF"
        from admission.api.notify_uf import notify_uf_applicant_abandon
        result = notify_uf_applicant_abandon(applicant)
        self.assertIsNone(result)
        mock_post.assert_not_called()

    @patch(f"{BRIDGE}._pii_transport_allowed", return_value=False)
    @patch(f"{BRIDGE}._get_campus_config", return_value=CAMPUS_CFG)
    @patch(f"{BRIDGE}.requests.post")
    @patch(f"{BRIDGE}.frappe")
    def test_bridge_send_blocked(self, mock_frappe, mock_post, _cfg, _guard):
        from admission.api.bridge import _send_bridge_notification
        applicant = MagicMock(); applicant.bridge_notified = 0  # P5 : pas déjà acquitté
        mock_frappe.get_doc.return_value = applicant
        result = _send_bridge_notification("CAN-1")
        self.assertIsNone(result)
        mock_post.assert_not_called()

    @patch(f"{BRIDGE}._pii_transport_allowed", return_value=False)
    @patch(f"{BRIDGE}._get_uf_config", return_value=UF_CFG)
    @patch(f"{BRIDGE}.requests.post")
    @patch(f"{BRIDGE}.frappe")
    def test_double_check_blocked(self, mock_frappe, mock_post, _cfg, _guard):
        from admission.api.bridge import _send_double_check
        result = _send_double_check("CAN-1")
        self.assertIsNone(result)
        mock_post.assert_not_called()

    @patch(f"{PUBLIC}._pii_transport_allowed", return_value=False)
    @patch(f"{PUBLIC}._get_campus_config", return_value=CAMPUS_CFG)
    @patch(f"{PUBLIC}.requests.post")
    @patch(f"{PUBLIC}.frappe")
    def test_resolve_person_blocked(self, mock_frappe, mock_post, _cfg, _guard):
        from admission.api.public import _resolve_person_from_campus
        result = _resolve_person_from_campus("a@b.com", "Jean", "K", "+22500000000")
        self.assertIsNone(result)
        mock_post.assert_not_called()
