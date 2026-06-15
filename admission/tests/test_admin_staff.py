"""LOT A (SM BACK-OFFICE) — comptes & identité staff (`admin_staff.py`).

Couvre : liste blanche des rôles (Admission SM jamais assignable), e-mail validé, anti-doublon,
cycle create→change role→disable (motif obligatoire), comptes protégés intouchables, reset =
lien mailé (motif obligatoire, aucun mot de passe en clair). DB-backed (rollback FrappeTestCase).

Réf : SPEC-ADMISSION-SM-BACKOFFICE §4 (A).
"""

from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from admission.api import admin_staff as A

EMAIL = "la-test-staff@lanem.test"


class TestAdminStaff(FrappeTestCase):

    def _cleanup(self):
        if frappe.db.exists("User", EMAIL):
            frappe.delete_doc("User", EMAIL, force=True, ignore_permissions=True)

    def setUp(self):
        self._cleanup()
        self.addCleanup(self._cleanup)

    # ── gardes de format / liste blanche ──
    def test_create_rejects_non_whitelisted_role(self):
        res = A.create_staff(full_name="X Y", email="x@lanem.test", role="Admission SM")
        self.assertFalse(res["ok"])
        self.assertEqual(res["error"]["code"], "ROLE_NOT_ALLOWED")

    def test_create_rejects_bad_email(self):
        res = A.create_staff(full_name="X Y", email="not-an-email", role="Admission Responsable")
        self.assertEqual(res["error"]["code"], "EMAIL_INVALID")

    # ── cycle de vie create → change role → disable ──
    def test_create_then_role_then_disable(self):
        with patch("frappe.sendmail"):
            res = A.create_staff(full_name="Test Staff", email=EMAIL, role="Admission Administratif")
        self.assertTrue(res["ok"])
        self.assertEqual(frappe.db.get_value("User", EMAIL, "user_type"), "System User")
        self.assertIn("Admission Administratif", frappe.get_roles(EMAIL))

        res = A.set_staff_role(email=EMAIL, role="Admission Responsable")
        self.assertTrue(res["ok"])
        roles = frappe.get_roles(EMAIL)
        self.assertIn("Admission Responsable", roles)
        self.assertNotIn("Admission Administratif", roles)  # remplacement, pas cumul

        self.assertEqual(A.set_staff_enabled(email=EMAIL, enabled=0)["error"]["code"], "MOTIF_REQUIRED")
        res = A.set_staff_enabled(email=EMAIL, enabled=0, motif="départ")
        self.assertTrue(res["ok"])
        self.assertEqual(int(frappe.db.get_value("User", EMAIL, "enabled")), 0)

    def test_create_duplicate_rejected(self):
        with patch("frappe.sendmail"):
            A.create_staff(full_name="Test Staff", email=EMAIL, role="Admission Administratif")
        res = A.create_staff(full_name="Test Staff", email=EMAIL, role="Admission Administratif")
        self.assertEqual(res["error"]["code"], "USER_EXISTS")

    # ── comptes protégés ──
    def test_cannot_manage_protected_account(self):
        res = A.set_staff_role(email="Administrator", role="Admission Responsable")
        self.assertEqual(res["error"]["code"], "PROTECTED_ACCOUNT")

    def test_cannot_disable_self(self):
        # la session de test est Administrator (protégé) → garde PROTECTED en premier
        res = A.set_staff_enabled(email=frappe.session.user, enabled=0, motif="x")
        self.assertFalse(res["ok"])  # PROTECTED_ACCOUNT ou SELF_DISABLE_FORBIDDEN

    # ── reset = lien mailé, motif obligatoire, aucun secret retourné ──
    def test_reset_requires_motif_then_sends_link(self):
        with patch("frappe.sendmail"):
            A.create_staff(full_name="Test Staff", email=EMAIL, role="Admission Administratif")
        self.assertEqual(A.reset_staff_password(email=EMAIL)["error"]["code"], "MOTIF_REQUIRED")
        with patch("frappe.sendmail"):
            res = A.reset_staff_password(email=EMAIL, motif="oubli mdp")
        self.assertTrue(res["ok"])
        self.assertTrue(res["data"]["sent"])
        self.assertNotIn("password", res["data"])  # jamais de mot de passe en clair

    # ── liste ──
    def test_list_staff_includes_created(self):
        with patch("frappe.sendmail"):
            A.create_staff(full_name="Test Staff", email=EMAIL, role="Admission Direction")
        res = A.list_staff()
        self.assertTrue(res["ok"])
        emails = {s["email"] for s in res["data"]["staff"]}
        self.assertIn(EMAIL, emails)
