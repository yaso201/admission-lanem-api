"""Tests ADM-SCH — Academic Level sync (campus → admission mirror).

Part A: sync_levels internals
1. Upsert creates new entries
2. Upsert updates existing entries
3. Network error → graceful failure
4. Missing config → skip
5. Skips entries with missing fields

Ref: ADM-SCH, D2 (niveaux choisissables = Academic Level répliqué).
"""

from __future__ import annotations

from unittest import TestCase
from unittest.mock import MagicMock, patch


SYNC = "admission.api.level_sync"


class TestSyncLevels(TestCase):

    @patch(f"{SYNC}.now_datetime", return_value="2026-06-09 12:00:00")
    @patch(f"{SYNC}._get_campus_config", return_value={
        "url": "http://campus:8000",
        "token": "tok",
    })
    @patch(f"{SYNC}.requests.get")
    @patch(f"{SYNC}.frappe")
    def test_sync_creates_new_entries(self, mock_frappe, mock_get, mock_config, mock_now):
        from admission.api.level_sync import sync_levels

        resp = MagicMock()
        resp.json.return_value = {
            "message": {
                "levels": [
                    {"level_code": "LIS-L1", "level_name": "Licence 1", "program_code": "LIS", "level_order": 1},
                    {"level_code": "LIS-L2", "level_name": "Licence 2", "program_code": "LIS", "level_order": 2},
                ],
            }
        }
        resp.raise_for_status.return_value = None
        mock_get.return_value = resp

        mock_frappe.db.exists.return_value = False

        result = sync_levels()

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["levels_synced"], 2)
        self.assertEqual(mock_frappe.get_doc.call_count, 2)
        mock_frappe.db.commit.assert_called_once()

    @patch(f"{SYNC}.now_datetime", return_value="2026-06-09 12:00:00")
    @patch(f"{SYNC}._get_campus_config", return_value={
        "url": "http://campus:8000",
        "token": "tok",
    })
    @patch(f"{SYNC}.requests.get")
    @patch(f"{SYNC}.frappe")
    def test_sync_updates_existing_entry(self, mock_frappe, mock_get, mock_config, mock_now):
        from admission.api.level_sync import sync_levels

        resp = MagicMock()
        resp.json.return_value = {
            "message": {
                "levels": [
                    {"level_code": "LIS-L1", "level_name": "Licence 1 updated", "program_code": "LIS", "level_order": 1},
                ],
            }
        }
        resp.raise_for_status.return_value = None
        mock_get.return_value = resp

        mock_frappe.db.exists.return_value = True

        result = sync_levels()

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["levels_synced"], 1)
        mock_frappe.db.set_value.assert_called_once()
        set_call = mock_frappe.db.set_value.call_args
        self.assertEqual(set_call[0][0], "Admission Level Mirror")
        self.assertEqual(set_call[0][1], "LIS-L1")
        self.assertEqual(set_call[0][2]["level_name"], "Licence 1 updated")
        self.assertEqual(set_call[0][2]["program_code"], "LIS")

    @patch(f"{SYNC}._get_campus_config", return_value={
        "url": "http://campus:8000",
        "token": "tok",
    })
    @patch(f"{SYNC}.requests.get")
    @patch(f"{SYNC}.frappe")
    def test_sync_network_error_graceful(self, mock_frappe, mock_get, mock_config):
        import requests as req
        from admission.api.level_sync import sync_levels

        mock_get.side_effect = req.ConnectionError("Campus unreachable")

        result = sync_levels()

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["reason"], "fetch_failed")

    @patch(f"{SYNC}._get_campus_config", return_value=None)
    @patch(f"{SYNC}.frappe")
    def test_sync_missing_config_skips(self, mock_frappe, mock_config):
        from admission.api.level_sync import sync_levels

        result = sync_levels()

        self.assertEqual(result["status"], "skipped")

    @patch(f"{SYNC}.now_datetime", return_value="2026-06-09 12:00:00")
    @patch(f"{SYNC}._get_campus_config", return_value={
        "url": "http://campus:8000",
        "token": "tok",
    })
    @patch(f"{SYNC}.requests.get")
    @patch(f"{SYNC}.frappe")
    def test_sync_skips_incomplete_entries(self, mock_frappe, mock_get, mock_config, mock_now):
        from admission.api.level_sync import sync_levels

        resp = MagicMock()
        resp.json.return_value = {
            "message": {
                "levels": [
                    {"level_code": "LIS-L1", "level_name": "Licence 1", "program_code": "LIS", "level_order": 1},
                    {"level_code": "", "level_name": "Bad", "program_code": "LIS", "level_order": 0},
                    {"level_code": "LIS-L3", "level_name": "Licence 3", "program_code": "", "level_order": 3},
                ],
            }
        }
        resp.raise_for_status.return_value = None
        mock_get.return_value = resp

        mock_frappe.db.exists.return_value = False

        result = sync_levels()

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["levels_synced"], 1)
