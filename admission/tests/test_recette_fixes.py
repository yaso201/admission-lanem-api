"""Corrections recette : P1 (cookies cross-site) + P2 (OTP synchrone).

P1 : security_headers._to_cross_site réécrit Set-Cookie en SameSite=None; Secure.
P2 : _send_candidate_mail relaie now=True à frappe.sendmail ; send_email_otp l'active.
Style unitaire mocké (pas de DB).
"""

from unittest import TestCase
from unittest.mock import MagicMock, patch

from admission import security_headers as SH
from admission.api import notifications as N


class TestCrossSiteCookie(TestCase):
    def test_mute_opts_session_none_secure(self):
        cm = MagicMock()
        cm.cookies = {
            "sid": {"samesite": "Lax", "secure": False, "httponly": True},
            "user_id": {"samesite": "Lax", "secure": False},
        }
        with patch.object(SH, "frappe") as mf:
            mf.local.cookie_manager = cm
            SH._apply_cross_site_cookies()
        self.assertEqual(cm.cookies["sid"]["samesite"], "None")
        self.assertTrue(cm.cookies["sid"]["secure"])
        self.assertTrue(cm.cookies["sid"]["httponly"])  # préservé
        self.assertEqual(cm.cookies["user_id"]["samesite"], "None")

    def test_sans_cookie_manager_no_op(self):
        with patch.object(SH, "frappe") as mf:
            mf.local.cookie_manager = None
            SH._apply_cross_site_cookies()  # ne lève pas


class TestOtpSync(TestCase):
    def _appl(self):
        a = MagicMock(); a.email = "a@b.test"; a.name = "CAN-2026-00001"; return a

    def test_now_relaye_a_sendmail(self):
        with patch.object(N, "frappe") as mf:
            N._send_candidate_mail(self._appl(), "s", "m", "ev", now=True)
            self.assertTrue(mf.sendmail.call_args.kwargs.get("now"))

    def test_defaut_reste_en_file(self):
        with patch.object(N, "frappe") as mf:
            N._send_candidate_mail(self._appl(), "s", "m", "ev")
            self.assertNotIn("now", mf.sendmail.call_args.kwargs)

    def test_otp_envoye_en_synchrone(self):
        with patch.object(N, "render_candidate_email", return_value="<html>"), \
             patch.object(N, "_full_name", return_value="X"), \
             patch.object(N, "_send_candidate_mail") as snd:
            N.send_email_otp(self._appl(), "123456")
            self.assertTrue(snd.call_args.kwargs.get("now"))
