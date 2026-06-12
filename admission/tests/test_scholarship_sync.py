"""Tests ADM-UF-4 -- Scholarship/promotion catalog sync.

Part A: sync internals
1. Upsert creates new scholarship mirror entries
2. Upsert creates new promotion mirror entries
3. Upsert updates existing scholarship entry
4. Network error -> graceful failure
5. Missing config -> skip

Part B: upsert annual amounts
6. Creates annual fee catalog entries
7. Updates existing annual entry

Ref: ADM-UF-4, SPEC-CONTRAT-FINANCE-ADMISSION-UF §3bis.
"""

from __future__ import annotations

from unittest import TestCase
from unittest.mock import MagicMock, patch


SYNC = "admission.api.scholarship_sync"


class TestSyncScholarshipCatalog(TestCase):

    @patch(f"{SYNC}.now_datetime", return_value="2026-06-09 12:00:00")
    @patch(f"{SYNC}._get_uf_config", return_value={
        "url": "http://backoffice:8000",
        "api_key": "k",
        "api_secret": "s",
    })
    @patch(f"{SYNC}.requests.get")
    @patch(f"{SYNC}.frappe")
    def test_sync_creates_scholarship_entries(self, mock_frappe, mock_get, mock_config, mock_now):
        from admission.api.scholarship_sync import sync_scholarship_catalog

        resp = MagicMock()
        resp.json.return_value = {
            "message": {
                "scholarships": [
                    {
                        "name": "BOURSE-TRES-BIEN",
                        "scholarship_name": "Mention Tres Bien",
                        "category": "Excellence",
                        "rate": 0.30,
                        "exclusivity_group": "excellence",
                        "program": "",
                    },
                    {
                        "name": "BOURSE-BIEN",
                        "scholarship_name": "Mention Bien",
                        "category": "Excellence",
                        "rate": 0.20,
                        "exclusivity_group": "excellence",
                        "program": "",
                    },
                ],
                "promotions": [],
                "annual_amounts": [],
                "scholarship_cap": 0.50,
            }
        }
        resp.raise_for_status.return_value = None
        mock_get.return_value = resp
        mock_frappe.db.exists.return_value = False

        result = sync_scholarship_catalog()

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["scholarships_synced"], 2)
        mock_frappe.db.commit.assert_called_once()

    @patch(f"{SYNC}.now_datetime", return_value="2026-06-09 12:00:00")
    @patch(f"{SYNC}._get_uf_config", return_value={
        "url": "http://backoffice:8000",
        "api_key": "k",
        "api_secret": "s",
    })
    @patch(f"{SYNC}.requests.get")
    @patch(f"{SYNC}.frappe")
    def test_sync_creates_promotion_entries(self, mock_frappe, mock_get, mock_config, mock_now):
        from admission.api.scholarship_sync import sync_scholarship_catalog

        resp = MagicMock()
        resp.json.return_value = {
            "message": {
                "scholarships": [],
                "promotions": [
                    {
                        "name": "PROMO-00001",
                        "promo_name": "Early Bird 2026",
                        "rate": 0.10,
                        "start_date": "2026-06-01",
                        "end_date": "2026-09-30",
                        "program": "",
                    },
                ],
                "annual_amounts": [],
                "scholarship_cap": 0.50,
            }
        }
        resp.raise_for_status.return_value = None
        mock_get.return_value = resp
        mock_frappe.db.exists.return_value = False

        result = sync_scholarship_catalog()

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["promotions_synced"], 1)

    @patch(f"{SYNC}.now_datetime", return_value="2026-06-09 12:00:00")
    @patch(f"{SYNC}._get_uf_config", return_value={
        "url": "http://backoffice:8000",
        "api_key": "k",
        "api_secret": "s",
    })
    @patch(f"{SYNC}.requests.get")
    @patch(f"{SYNC}.frappe")
    def test_sync_updates_existing_scholarship(self, mock_frappe, mock_get, mock_config, mock_now):
        from admission.api.scholarship_sync import sync_scholarship_catalog

        resp = MagicMock()
        resp.json.return_value = {
            "message": {
                "scholarships": [
                    {
                        "name": "BOURSE-TRES-BIEN",
                        "scholarship_name": "Mention Tres Bien",
                        "category": "Excellence",
                        "rate": 0.35,
                        "exclusivity_group": "excellence",
                        "program": "",
                    },
                ],
                "promotions": [],
                "annual_amounts": [],
                "scholarship_cap": 0.50,
            }
        }
        resp.raise_for_status.return_value = None
        mock_get.return_value = resp

        def exists_side_effect(doctype, name):
            if doctype == "Admission Scholarship Mirror" and name == "BOURSE-TRES-BIEN":
                return True
            return False

        mock_frappe.db.exists.side_effect = exists_side_effect

        result = sync_scholarship_catalog()

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["scholarships_synced"], 1)
        set_call = mock_frappe.db.set_value.call_args
        self.assertEqual(set_call[0][0], "Admission Scholarship Mirror")
        self.assertEqual(set_call[0][1], "BOURSE-TRES-BIEN")
        self.assertEqual(set_call[0][2]["rate"], 0.35)

    @patch(f"{SYNC}._get_uf_config", return_value={
        "url": "http://backoffice:8000",
        "api_key": "k",
        "api_secret": "s",
    })
    @patch(f"{SYNC}.requests.get")
    @patch(f"{SYNC}.frappe")
    def test_sync_network_error_graceful(self, mock_frappe, mock_get, mock_config):
        import requests as req
        from admission.api.scholarship_sync import sync_scholarship_catalog

        mock_get.side_effect = req.ConnectionError("UF unreachable")

        result = sync_scholarship_catalog()

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["reason"], "fetch_failed")

    @patch(f"{SYNC}._get_uf_config", return_value=None)
    @patch(f"{SYNC}.frappe")
    def test_sync_missing_config_skips(self, mock_frappe, mock_config):
        from admission.api.scholarship_sync import sync_scholarship_catalog

        result = sync_scholarship_catalog()

        self.assertEqual(result["status"], "skipped")


class TestUpsertAnnualAmounts(TestCase):

    @patch(f"{SYNC}.now_datetime", return_value="2026-06-09 12:00:00")
    @patch(f"{SYNC}._get_uf_config", return_value={
        "url": "http://backoffice:8000",
        "api_key": "k",
        "api_secret": "s",
    })
    @patch(f"{SYNC}.requests.get")
    @patch(f"{SYNC}.frappe")
    def test_creates_annual_fee_catalog_entries(self, mock_frappe, mock_get, mock_config, mock_now):
        from admission.api.scholarship_sync import sync_scholarship_catalog

        resp = MagicMock()
        resp.json.return_value = {
            "message": {
                "scholarships": [],
                "promotions": [],
                "annual_amounts": [
                    {"program_code": "LIS", "amount_xof": 600000},
                    {"program_code": "PRE", "amount_xof": 800000},
                ],
                "scholarship_cap": 0.50,
            }
        }
        resp.raise_for_status.return_value = None
        mock_get.return_value = resp
        mock_frappe.db.exists.return_value = False

        result = sync_scholarship_catalog()

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["annual_synced"], 2)

    @patch(f"{SYNC}.now_datetime", return_value="2026-06-09 12:00:00")
    @patch(f"{SYNC}._get_uf_config", return_value={
        "url": "http://backoffice:8000",
        "api_key": "k",
        "api_secret": "s",
    })
    @patch(f"{SYNC}.requests.get")
    @patch(f"{SYNC}.frappe")
    def test_updates_existing_annual_entry(self, mock_frappe, mock_get, mock_config, mock_now):
        from admission.api.scholarship_sync import sync_scholarship_catalog

        resp = MagicMock()
        resp.json.return_value = {
            "message": {
                "scholarships": [],
                "promotions": [],
                "annual_amounts": [
                    {"program_code": "LIS", "amount_xof": 650000},
                ],
                "scholarship_cap": 0.50,
            }
        }
        resp.raise_for_status.return_value = None
        mock_get.return_value = resp

        def exists_side_effect(doctype, name):
            if doctype == "Admission Fee Catalog" and name == "LIS-DEFAULT-annual":
                return True
            return False

        mock_frappe.db.exists.side_effect = exists_side_effect

        result = sync_scholarship_catalog()

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["annual_synced"], 1)
        set_calls = [
            c for c in mock_frappe.db.set_value.call_args_list
            if c[0][0] == "Admission Fee Catalog"
        ]
        self.assertTrue(len(set_calls) >= 1)
        self.assertEqual(set_calls[0][0][1], "LIS-DEFAULT-annual")
        self.assertEqual(set_calls[0][0][2]["amount_xof"], 650000.0)
