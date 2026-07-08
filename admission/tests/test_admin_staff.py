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


SM_EMAIL = "la-test-sm@lanem.test"
TARGET_EMAIL = "la-test-target@lanem.test"


class TestSMWebsiteUserMutations(FrappeTestCase):
    """FIX-SM-USER-MUTATIONS — les mutations User du SM DOIVENT réussir AS un vrai SM
    Website User (contexte de permission réel, pas Administrator — c'est ce qui a masqué
    la régression du desk-lock). Reproduit l'échec puis prouve le succès."""

    def _clean(self):
        frappe.set_user("Administrator")
        for e in (SM_EMAIL, TARGET_EMAIL, EMAIL):
            if frappe.db.exists("User", e):
                frappe.delete_doc("User", e, force=True, ignore_permissions=True)

    def setUp(self):
        self._clean()
        # SM RÉEL : rôle Admission SM (desk_access=0) → Website User (comme yaovi.soglo en prod).
        sm = frappe.get_doc({"doctype": "User", "email": SM_EMAIL, "first_name": "SM",
                             "send_welcome_email": 0, "enabled": 1,
                             "roles": [{"role": "Admission SM"}]})
        sm.insert(ignore_permissions=True)
        self.assertEqual(sm.user_type, "Website User")            # pré-condition du bug
        target = frappe.get_doc({"doctype": "User", "email": TARGET_EMAIL, "first_name": "Target",
                                 "send_welcome_email": 0, "enabled": 1,
                                 "roles": [{"role": "Admission Administratif"}]})
        target.insert(ignore_permissions=True)
        self.addCleanup(self._clean)

    # GM1/GM2 : AS le SM Website User, changer le rôle réussit (échouait → PermissionError)
    def test_sm_website_user_can_set_role(self):
        frappe.set_user(SM_EMAIL)
        res = A.set_staff_role(email=TARGET_EMAIL, role="Admission Responsable")
        self.assertTrue(res["ok"])

    def test_sm_website_user_can_create_staff(self):
        frappe.set_user(SM_EMAIL)
        with patch("frappe.sendmail"):
            res = A.create_staff(full_name="Nouveau Staff", email=EMAIL, role="Admission Direction")
        self.assertTrue(res["ok"])
        self.assertIn("Admission Direction", frappe.get_roles(EMAIL))

    # GM6 : remplacement propre — l'ancien rôle admission n'est PLUS présent, cible Website User
    def test_set_role_replaces_no_accumulation(self):
        frappe.set_user(SM_EMAIL)
        A.set_staff_role(email=TARGET_EMAIL, role="Admission Responsable")
        roles = [r for r in frappe.get_roles(TARGET_EMAIL) if r in A.ASSIGNABLE_ROLES]
        self.assertEqual(roles, ["Admission Responsable"])        # exactement 1, l'ancien parti
        self.assertNotIn("Admission Administratif", roles)
        frappe.set_user("Administrator")
        self.assertEqual(frappe.db.get_value("User", TARGET_EMAIL, "user_type"), "Website User")

    # GM4 : le SM reste Website User après ses actions (desk-lock intact)
    def test_sm_stays_website_user(self):
        frappe.set_user(SM_EMAIL)
        A.set_staff_role(email=TARGET_EMAIL, role="Admission Direction")
        frappe.set_user("Administrator")
        self.assertEqual(frappe.db.get_value("User", SM_EMAIL, "user_type"), "Website User")

    # GM3 : autorisation NON élargie — un non-SM ne peut pas muter (bloqué à only_for).
    # frappe.only_for est un no-op sous frappe.flags.in_test (frappe/__init__.py:947) → on force
    # l'enforcement pour prouver la garde telle qu'elle s'applique en prod (recette la reconfirme).
    def test_non_sm_cannot_mutate(self):
        frappe.set_user(TARGET_EMAIL)                             # Admission Administratif, pas SM
        frappe.flags.in_test = False
        try:
            with self.assertRaises(frappe.PermissionError):
                A.set_staff_role(email=SM_EMAIL, role="Admission Responsable")
        finally:
            frappe.flags.in_test = True

    # GM3 : Administrator jamais gérable via ces actions (même AS SM)
    def test_administrator_not_manageable_as_sm(self):
        frappe.set_user(SM_EMAIL)
        res = A.set_staff_role(email="Administrator", role="Admission Responsable")
        self.assertEqual(res["error"]["code"], "PROTECTED_ACCOUNT")
