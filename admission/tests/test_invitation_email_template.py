"""Templates e-mail invitation/reset LaNEM : le lien doit pointer vers le front management
(staff_portal_url) avec la clé extraite, jamais vers la page Frappe brute. commit() neutralisé."""

from unittest.mock import patch

import frappe
from frappe.email.doctype.email_template.email_template import get_email_template
from frappe.tests.utils import FrappeTestCase

from admission.patches.v1_1 import set_invitation_email_template as P


class TestInvitationEmailTemplate(FrappeTestCase):
    def setUp(self):
        with patch.object(frappe.db, "commit"):  # ne pas persister System Settings sur DEV
            P.execute()
        self.staff_url = (frappe.conf.get("staff_portal_url") or "https://staff-rec.lanem.bj").rstrip("/")

    def _render(self, name):
        # Contexte tel que fourni par send_login_mail (link = URL host API + key)
        ctx = {
            "first_name": "Awa",
            "link": "https://api-admission-rec.lanem.bj/update-password?key=ABC123XYZ",
        }
        return get_email_template(name, ctx)

    def test_invitation_points_to_management_with_key(self):
        out = self._render(P.INVITATION_NAME)
        msg = out["message"]
        self.assertIn(f"{self.staff_url}/update-password?key=ABC123XYZ", msg)
        self.assertNotIn("api-admission-rec.lanem.bj/update-password", msg)
        self.assertIn("Awa", msg)

    def test_reset_points_to_management_with_key(self):
        out = self._render(P.RESET_NAME)
        msg = out["message"]
        self.assertIn(f"{self.staff_url}/update-password?key=ABC123XYZ", msg)
        self.assertNotIn("api-admission-rec.lanem.bj/update-password", msg)

    def test_key_extraction_handles_password_expired(self):
        ctx = {"first_name": "Awa",
               "link": "https://x/update-password?key=KEY9&password_expired=true"}
        msg = get_email_template(P.INVITATION_NAME, ctx)["message"]
        self.assertIn(f"{self.staff_url}/update-password?key=KEY9", msg)
        self.assertNotIn("password_expired", msg)

    def test_system_settings_wired(self):
        # P.execute a tourné en setUp (commit mocké) → valeurs en cache de session
        self.assertEqual(frappe.db.get_single_value("System Settings", "welcome_email_template"),
                         P.INVITATION_NAME)
        self.assertEqual(frappe.db.get_single_value("System Settings", "reset_password_template"),
                         P.RESET_NAME)
