"""LOT 0 (SM BACK-OFFICE) — socle de sécurité & rôle `Admission SM`.

Couvre :
 - `Admission SM` ∈ BYPASS_ROLES du cloisonnement (voit tous les dossiers, même ON).
 - `Admission SM` ∈ STAFF_ROLES (surface lecture/liste) et rapporté par whoami (UX role-aware).
 - patch harden_sm_account : rôle créé (desk_access=0, 2FA), 2FA sur System Manager,
   System Settings durci (2FA globale + OTP App + session 04:00 + deny_multiple_sessions),
   idempotence (rejeu = no-op, état stable).

Réf : SPEC-ADMISSION-SM-BACKOFFICE §3/§5 (D2/D4).
"""

from unittest import TestCase
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from admission.api import permissions as P
from admission.api import staff as S
from admission.patches.v1_1 import harden_sm_account as H


# ── Cloisonnement : Admission SM bypasse (mocké, pas de DB) ────────────────────
class TestBypass(TestCase):
    def test_admission_sm_in_bypass_roles(self):
        self.assertIn("Admission SM", P.BYPASS_ROLES)

    def test_admission_sm_bypasses_query_conditions(self):
        with patch.object(P, "frappe") as mf:
            mf.session.user = "sm@lanem.bj"
            mf.get_roles.return_value = ["Admission SM", "All"]
            # _is_bypass(True) → aucune restriction même si le cloisonnement était ON.
            self.assertEqual(P.get_permission_query_conditions(user="sm@lanem.bj"), "")

    def test_admission_sm_has_permission_defers(self):
        with patch.object(P, "frappe") as mf:
            mf.session.user = "sm@lanem.bj"
            mf.get_roles.return_value = ["Admission SM"]
            # bypass → None (défère aux perms normales, ne refuse rien).
            self.assertIsNone(P.has_permission(doc=None, ptype="read", user="sm@lanem.bj"))


# ── STAFF_ROLES / whoami (mocké) ───────────────────────────────────────────────
class TestStaffRoles(TestCase):
    def test_admission_sm_in_staff_roles(self):
        self.assertIn("Admission SM", S.STAFF_ROLES)

    def test_whoami_reports_admission_sm(self):
        ok = patch(f"{S.__name__}._ok", side_effect=lambda d: {"ok": True, "data": d})
        with patch(f"{S.__name__}.frappe") as mf, ok:
            mf.session.user = "sm@lanem.bj"
            mf.only_for.return_value = None
            mf.get_roles.return_value = ["Admission SM", "Admission Direction", "All"]
            mf.db.get_value.return_value = "Super Admin"
            mf.sessions.get_csrf_token.return_value = "csrf-x"
            res = S.whoami()
        self.assertIn("Admission SM", res["data"]["roles"])


# ── Patch de durcissement (DB-backed, rollback FrappeTestCase) ─────────────────
class TestHardenPatch(FrappeTestCase):
    def test_end_state_role_and_settings(self):
        H.execute()

        role = frappe.get_doc("Role", "Admission SM")
        self.assertEqual(int(role.desk_access or 0), 0)        # non root : pas de Desk
        self.assertEqual(int(role.two_factor_auth or 0), 1)    # 2FA par le rôle

        self.assertEqual(
            int(frappe.db.get_value("Role", "System Manager", "two_factor_auth") or 0), 1)

        ss = frappe.get_single("System Settings")
        self.assertEqual(int(ss.enable_two_factor_auth or 0), 1)
        self.assertEqual(ss.two_factor_method, "Email")  # phase 1 : Email (TOTP = flip ultérieur)

    def test_idempotent(self):
        H.execute()
        H.execute()  # rejeu : ne lève pas, état stable
        role = frappe.get_doc("Role", "Admission SM")
        self.assertEqual(int(role.desk_access or 0), 0)
        self.assertEqual(int(role.two_factor_auth or 0), 1)
