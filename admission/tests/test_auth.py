"""Tests SOCLE-0-AUTH — auth staff locale Frappe (DEC-259) : politique mot de passe + logging.

DEC-259 : auth = locale Frappe (login natif /app), PAS de SSO. Ce lot règle la POLITIQUE
de mot de passe (patch config-as-code) et VERROUILLE la conformité du logging natif A03 §10.1/§10.2.

 - test_success_login_logging_wired / _logout_logging_function_present / _auth_log_helper_present :
     verrous de conformité (passent sur le natif actuel ; échouent si un upgrade Frappe retire
     la trace d'authentification → régression détectée). Ce sont des gardes, pas du TDD de code neuf.
 - test_patch_sets_password_policy : red→green du patch (notre code).
"""

from unittest import TestCase
from unittest.mock import MagicMock, patch


class TestAuthLoggingConformity(TestCase):
    """A03 §10.1/§10.2 : connexion / échec / logout tracés nativement (Activity Log)."""

    def test_success_login_logging_wired(self):
        import frappe
        # connexion réussie → on_session_creation → login_feed → add_authentication_log(Success)
        self.assertIn(
            "frappe.core.doctype.activity_log.feed.login_feed",
            frappe.get_hooks("on_session_creation"),
        )

    def test_logout_logging_function_present(self):
        # logout → delete_session → logout_feed → add_authentication_log(operation="Logout")
        from frappe.core.doctype.activity_log.feed import login_feed, logout_feed
        self.assertTrue(callable(login_feed))
        self.assertTrue(callable(logout_feed))

    def test_auth_log_helper_present(self):
        # point d'entrée unique de la trace (date/compte/action/objet/contexte/résultat → Activity Log)
        from frappe.core.doctype.activity_log.activity_log import add_authentication_log
        self.assertTrue(callable(add_authentication_log))


class TestPasswordPolicyPatch(TestCase):
    """Le patch pose une politique raisonnable : score 2→3, policy ON, pas de rotation forcée."""

    def test_patch_sets_password_policy(self):
        current = {
            "enable_password_policy": 0,
            "minimum_password_score": "2",
            "force_user_to_reset_password": 0,
        }
        ss = MagicMock()
        ss.get.side_effect = lambda k: current.get(k)
        ss.set.side_effect = lambda k, v: current.__setitem__(k, v)
        mf = MagicMock()
        mf.get_single.return_value = ss
        with patch("admission.patches.v1_0.set_password_policy.frappe", mf):
            from admission.patches.v1_0.set_password_policy import execute
            execute()
        self.assertEqual(current["minimum_password_score"], "3")
        self.assertEqual(current["enable_password_policy"], 1)
        self.assertEqual(current["force_user_to_reset_password"], 0)
        ss.save.assert_called_once()

    def test_patch_idempotent_no_write_when_already_set(self):
        current = {
            "enable_password_policy": 1,
            "minimum_password_score": "3",
            "force_user_to_reset_password": 0,
        }
        ss = MagicMock()
        ss.get.side_effect = lambda k: current.get(k)
        ss.set.side_effect = lambda k, v: current.__setitem__(k, v)
        mf = MagicMock()
        mf.get_single.return_value = ss
        with patch("admission.patches.v1_0.set_password_policy.frappe", mf):
            from admission.patches.v1_0.set_password_policy import execute
            execute()
        ss.save.assert_not_called()  # rien à changer → pas d'écriture
