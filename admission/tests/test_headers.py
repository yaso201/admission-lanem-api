"""Tests HEAD-1 — headers de sécurité (after_request), configurables, non-breaking.

- défauts posés (X-Content-Type-Options, X-Frame-Options, Referrer-Policy, CSP frame-ancestors).
- HSTS seulement en HTTPS (dev http → pas de HSTS).
- surcharge via site_config admission_security_headers (ex. X-Frame-Options=DENY).
- setdefault : ne clobbe pas un header déjà posé (reverse-proxy = défense en profondeur).
- valeur vide en config → header désactivé.

Ref: HEAD-1, AUDIT-GLOBAL.
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


HDR = "admission.security_headers"


def _make_response():
    resp = MagicMock()
    resp.headers = {}  # vrai dict → setdefault réel
    return resp


def _make_request(scheme="http"):
    req = MagicMock()
    req.scheme = scheme
    return req


class TestSecurityHeaders(TestCase):
    @patch(f"{HDR}.frappe")
    def test_default_headers_set(self, mock_frappe):
        mock_frappe.conf.get.return_value = None  # pas de surcharge
        from admission.security_headers import set_security_headers
        resp = _make_response()
        set_security_headers(response=resp, request=_make_request("http"))
        self.assertEqual(resp.headers.get("X-Content-Type-Options"), "nosniff")
        self.assertEqual(resp.headers.get("X-Frame-Options"), "SAMEORIGIN")
        self.assertEqual(resp.headers.get("Referrer-Policy"), "strict-origin-when-cross-origin")
        self.assertIn("frame-ancestors", resp.headers.get("Content-Security-Policy", ""))

    @patch(f"{HDR}.frappe")
    def test_hsts_only_on_https(self, mock_frappe):
        mock_frappe.conf.get.return_value = None
        from admission.security_headers import set_security_headers
        # http (dev) → pas de HSTS
        resp_http = _make_response()
        set_security_headers(response=resp_http, request=_make_request("http"))
        self.assertNotIn("Strict-Transport-Security", resp_http.headers)
        # https (prod) → HSTS
        resp_https = _make_response()
        set_security_headers(response=resp_https, request=_make_request("https"))
        self.assertIn("max-age", resp_https.headers.get("Strict-Transport-Security", ""))

    @patch(f"{HDR}.frappe")
    def test_config_override(self, mock_frappe):
        mock_frappe.conf.get.return_value = {"X-Frame-Options": "DENY"}
        from admission.security_headers import set_security_headers
        resp = _make_response()
        set_security_headers(response=resp, request=_make_request("http"))
        self.assertEqual(resp.headers.get("X-Frame-Options"), "DENY")

    @patch(f"{HDR}.frappe")
    def test_setdefault_does_not_clobber(self, mock_frappe):
        mock_frappe.conf.get.return_value = None
        from admission.security_headers import set_security_headers
        resp = _make_response()
        resp.headers["X-Frame-Options"] = "SET-BY-PROXY"  # déjà posé en amont
        set_security_headers(response=resp, request=_make_request("http"))
        self.assertEqual(resp.headers["X-Frame-Options"], "SET-BY-PROXY")  # non écrasé

    @patch(f"{HDR}.frappe")
    def test_empty_config_value_disables_header(self, mock_frappe):
        mock_frappe.conf.get.return_value = {"Content-Security-Policy": ""}
        from admission.security_headers import set_security_headers
        resp = _make_response()
        set_security_headers(response=resp, request=_make_request("http"))
        self.assertNotIn("Content-Security-Policy", resp.headers)

    @patch(f"{HDR}.frappe")
    def test_no_response_is_safe(self, mock_frappe):
        from admission.security_headers import set_security_headers
        self.assertIsNone(set_security_headers(response=None))
