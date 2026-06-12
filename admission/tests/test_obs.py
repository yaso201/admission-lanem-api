"""Tests OBS-1 — re-drive notifications UF + Error Log natif sur échec.

- notify_uf_payment succès → pose uf_notified=1 (+ pas de log_error).
- notify_uf_payment échec → frappe.log_error (Error Log) + pas de uf_notified.
- notify_uf_payment config absente → skip propre (pas de log_error : ≠ échec).
- redrive : config absente → AUCUN re-drive (pas dans le vide).
- redrive : config présente → re-POST des Confirmed non notifiés (uf_notified=0), idempotent.

Ref: AUDIT-OBS1-MONITORING, DEC-221 (UF réactif).
"""

from __future__ import annotations

from unittest import TestCase
from unittest.mock import MagicMock, patch

import requests as _requests
import frappe as _real_frappe
from frappe.utils import get_datetime


def setUpModule():
    try:
        _real_frappe.local.flags
    except Exception:
        _real_frappe.local.flags = _real_frappe._dict(in_test=True)


NOTIFY = "admission.api.notify_uf"
PATCH = "admission.patches.v1_0.backfill_uf_notified"
NOW = "2026-06-09 12:00:00"
UF_CFG = {"url": "https://uf:8000", "api_key": "", "api_secret": ""}


class TestNotifyUfMarking(TestCase):
    @patch(f"{NOTIFY}.now_datetime", return_value=get_datetime(NOW))
    @patch(f"{NOTIFY}._get_uf_config", return_value=UF_CFG)
    @patch(f"{NOTIFY}.requests.post")
    @patch(f"{NOTIFY}.frappe")
    def test_success_marks_uf_notified(self, mock_frappe, mock_post, _cfg, _now):
        resp = MagicMock()
        resp.json.return_value = {"ok": True}
        resp.raise_for_status.return_value = None
        mock_post.return_value = resp
        applicant = MagicMock(); applicant.name = "CAN-1"
        applicant.first_name = "Jean"; applicant.last_name = "K"
        payment = MagicMock(); payment.name = "REC-1"
        from admission.api.notify_uf import notify_uf_payment
        result = notify_uf_payment(applicant=applicant, fee=MagicMock(), payment=payment)
        self.assertTrue(result)
        marks = [c for c in mock_frappe.db.set_value.call_args_list
                 if c[0][0] == "Applicant Fee Payment"]
        self.assertTrue(marks)
        self.assertEqual(marks[0][0][2].get("uf_notified"), 1)
        mock_frappe.log_error.assert_not_called()

    @patch(f"{NOTIFY}._get_uf_config", return_value=UF_CFG)
    @patch(f"{NOTIFY}.requests.post", side_effect=_requests.exceptions.ConnectionError("boom"))
    @patch(f"{NOTIFY}.frappe")
    def test_failure_logs_error_not_marked(self, mock_frappe, _post, _cfg):
        applicant = MagicMock(); applicant.name = "CAN-1"
        applicant.first_name = "Jean"; applicant.last_name = "K"
        payment = MagicMock(); payment.name = "REC-1"
        from admission.api.notify_uf import notify_uf_payment
        result = notify_uf_payment(applicant=applicant, fee=MagicMock(), payment=payment)
        self.assertIsNone(result)
        mock_frappe.log_error.assert_called()  # Error Log natif (plus de return None muet)
        marks = [c for c in mock_frappe.db.set_value.call_args_list
                 if c[0][0] == "Applicant Fee Payment" and c[0][2].get("uf_notified") == 1]
        self.assertFalse(marks)

    @patch(f"{NOTIFY}._get_uf_config", return_value=None)  # config UF absente
    @patch(f"{NOTIFY}.frappe")
    def test_no_config_skips_without_error(self, mock_frappe, _cfg):
        from admission.api.notify_uf import notify_uf_payment
        result = notify_uf_payment(applicant=MagicMock(), fee=MagicMock(), payment=MagicMock())
        self.assertIsNone(result)
        # config absente = skip, PAS un échec → pas d'Error Log
        mock_frappe.log_error.assert_not_called()


class TestRedrive(TestCase):
    @patch(f"{NOTIFY}._get_uf_config", return_value=None)  # config absente
    @patch(f"{NOTIFY}.frappe")
    def test_redrive_skips_when_no_config(self, mock_frappe, _cfg):
        from admission.api.notify_uf import redrive_uf_notifications
        result = redrive_uf_notifications()
        mock_frappe.get_all.assert_not_called()  # pas de re-drive dans le vide
        self.assertEqual(result.get("status"), "skipped_no_config")

    @patch(f"{NOTIFY}.notify_uf_payment", return_value={"ok": True})
    @patch(f"{NOTIFY}._get_uf_config", return_value=UF_CFG)
    @patch(f"{NOTIFY}.frappe")
    def test_redrive_renotifies_unnotified(self, mock_frappe, _cfg, mock_notify):
        mock_frappe.get_all.return_value = ["REC-OLD"]
        payment = MagicMock(); payment.applicant = "CAN-1"; payment.applicant_fee = "AFF-1"
        mock_frappe.get_doc.return_value = payment
        from admission.api.notify_uf import redrive_uf_notifications
        result = redrive_uf_notifications()
        filters = mock_frappe.get_all.call_args.kwargs.get("filters", {})
        self.assertEqual(filters.get("payment_status"), "Confirmed")
        self.assertEqual(filters.get("uf_notified"), 0)
        mock_notify.assert_called()
        self.assertEqual(result.get("redriven"), 1)


class TestBackfillUfNotified(TestCase):
    @patch(f"{PATCH}._get_uf_config", return_value=None)
    @patch(f"{PATCH}.frappe")
    def test_skips_when_uf_not_configured(self, mock_frappe, _cfg):
        # Flux UF inactif (uf_url absent) → aucun paiement notifié → on ne marque RIEN (anti-désync).
        from admission.patches.v1_0.backfill_uf_notified import execute
        execute()
        mock_frappe.get_all.assert_not_called()

    @patch(f"{PATCH}.now_datetime", return_value="2026-06-09 12:00:00")
    @patch(f"{PATCH}._get_uf_config", return_value={"url": "https://uf:8000"})
    @patch(f"{PATCH}.frappe")
    def test_marks_confirmed_unnotified_with_paid_at(self, mock_frappe, _cfg, _now):
        mock_frappe.get_all.return_value = ["REC-1"]
        mock_frappe.db.get_value.return_value = "2026-01-01 10:00:00"  # paid_at
        from admission.patches.v1_0.backfill_uf_notified import execute
        execute()
        filters = mock_frappe.get_all.call_args.kwargs.get("filters", {})
        self.assertEqual(filters.get("payment_status"), "Confirmed")
        self.assertEqual(filters.get("uf_notified"), 0)  # idempotent : seulement les non-marqués
        upd = mock_frappe.db.set_value.call_args[0][2]
        self.assertEqual(upd.get("uf_notified"), 1)
        self.assertEqual(upd.get("uf_notified_at"), "2026-01-01 10:00:00")  # COALESCE(paid_at, now)
