"""LOT E (SM BACK-OFFICE) — recovery exploitation (`admin_ops.py`).

Wrappers idempotents : santé (compteurs) + relance UF / pont / expiration. On vérifie que
les wrappers délèguent aux fonctions existantes (mockées) et renvoient leur résultat.
Réf : SPEC-ADMISSION-SM-BACKOFFICE §4 (E).
"""

from unittest.mock import patch

from frappe.tests.utils import FrappeTestCase

from admission.api import admin_ops as O


class TestAdminOps(FrappeTestCase):

    def test_health_returns_int_counters(self):
        res = O.get_ops_health()
        self.assertTrue(res["ok"])
        for k in ("uf_unreplicated", "bridge_pending", "pending_online_stale"):
            self.assertIsInstance(res["data"][k], int)

    def test_redrive_uf_delegates(self):
        with patch("admission.api.notify_uf.redrive_uf_notifications",
                   return_value={"redriven": 2, "candidates": 2, "remaining": 0}):
            res = O.redrive_uf_now()
        self.assertEqual(res["data"]["redriven"], 2)

    def test_redrive_bridge_delegates(self):
        with patch("admission.api.bridge.redrive_bridge_notifications",
                   return_value={"redriven": 1, "candidates": 1, "remaining": 0}):
            res = O.redrive_bridge_now()
        self.assertEqual(res["data"]["redriven"], 1)

    def test_expire_pending_delegates_with_arg(self):
        with patch("admission.api.public.expire_stale_online_pending", return_value=3) as m:
            res = O.expire_pending_now(older_than_hours=24)
        self.assertEqual(res["data"]["expired"], 3)
        m.assert_called_once_with(older_than_hours=24)
