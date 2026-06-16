"""Verrouillage Desk staff (patch lock_desk_for_staff). commit() neutralisé → rollback DEV."""

from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from admission.patches.v1_1 import lock_desk_for_staff as P


class TestLockDesk(FrappeTestCase):
    def test_staff_roles_desk_access_zero(self):
        with patch.object(frappe.db, "commit"):  # ne pas persister sur DEV
            P.execute()
        for role in P.STAFF_ROLES:
            self.assertEqual(int(frappe.db.get_value("Role", role, "desk_access") or 0), 0,
                             f"{role} doit être desk_access=0")
