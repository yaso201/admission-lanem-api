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
    def test_lax_devient_none_secure(self):
        out = SH._to_cross_site("sid=abc; Expires=...; Max-Age=600; HttpOnly; Path=/; SameSite=Lax")
        self.assertIn("SameSite=None", out)
        self.assertNotIn("SameSite=Lax", out)
        self.assertIn("Secure", out)
        self.assertIn("HttpOnly", out)  # préservé

    def test_sans_samesite_ajoute(self):
        out = SH._to_cross_site("sid=abc; Path=/")
        self.assertIn("SameSite=None", out)
        self.assertIn("Secure", out)

    def test_idempotent(self):
        once = SH._to_cross_site("sid=abc; SameSite=Lax")
        twice = SH._to_cross_site(once)
        self.assertEqual(twice.count("SameSite=None"), 1)
        self.assertEqual(twice.lower().count("secure"), 1)


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
