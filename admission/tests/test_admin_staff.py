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
        # FIX-STAFF-DESK-LOCK : le staff naît Website User (rôles desk_access=0 → recompute Frappe),
        # PAS System User — il n'atteint jamais le desk /app.
        self.assertEqual(frappe.db.get_value("User", EMAIL, "user_type"), "Website User")
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
        # FIX-STAFF-DESK-LOCK (Test 5 basculé) : le staff Website User doit être VISIBLE
        # (le filtre user_type==System User l'excluait → 0). Devient le gardien du fix.
        with patch("frappe.sendmail"):
            A.create_staff(full_name="Test Staff", email=EMAIL, role="Admission Direction")
        res = A.list_staff()
        self.assertTrue(res["ok"])
        emails = {s["email"] for s in res["data"]["staff"]}
        self.assertIn(EMAIL, emails)

    # ── FIX-STAFF-DESK-LOCK : cohérence de la triade (sécurité) ──
    def test_created_staff_is_desk_locked(self):
        # GS1/GS4 : cœur sécurité — un staff créé n'atteint JAMAIS le desk /app.
        with patch("frappe.sendmail"):
            A.create_staff(full_name="Test Staff", email=EMAIL, role="Admission Administratif")
        user = frappe.get_doc("User", EMAIL)
        self.assertFalse(user.has_desk_access())                  # 0 accès /app
        self.assertEqual(user.user_type, "Website User")

    def test_staff_roles_are_desk_locked(self):
        # Garde-invariant : le desk-lock repose sur desk_access=0 des 4 rôles. Si un rôle
        # renaît desk_access=1 (dérive), le staff redeviendrait System User = /app → ce test tombe.
        for role in A.ASSIGNABLE_ROLES:
            self.assertEqual(int(frappe.db.get_value("Role", role, "desk_access") or 0), 0,
                             f"{role} doit rester desk_access=0 (invariant desk-lock)")

    def test_create_staff_alerts_on_desk_access_drift(self):
        # Défense en profondeur (post-check) : si un compte créé a malgré tout l'accès desk
        # (rôle mal configuré desk_access=1), create_staff alerte ops (OBS-2), sans bloquer.
        with patch("frappe.sendmail"), \
             patch("frappe.core.doctype.user.user.User.has_desk_access", return_value=True), \
             patch.object(A, "log_event") as mlog:
            res = A.create_staff(full_name="Test Staff", email=EMAIL, role="Admission Finance")
        self.assertTrue(res["ok"])                                # non-bloquant : compte créé
        drift = [c for c in mlog.call_args_list if c.args and c.args[0] == "staff_desk_access"]
        self.assertTrue(drift, "un drift desk_access doit émettre une alerte OBS-2")
        self.assertEqual(drift[0].kwargs.get("alert_type"), "staff_desk_access")
        self.assertEqual(drift[0].kwargs.get("level"), "error")
