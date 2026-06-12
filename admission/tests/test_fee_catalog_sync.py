"""Tests ADM-UF-3 — Fee catalog sync + consumption in admission.

Part A: sync internals (_fetch_catalog, _upsert_catalog)
1. Upsert creates new entries
2. Upsert updates existing entries
3. Network error → graceful failure
4. Missing config → skip

Part B: _resolve_fee_from_catalog (consumption)
5. Exact match (programme + fee_type)
6. DEFAULT fallback
7. No match → None

Part C: _build_frais_data (get_frais core logic)
8. Uses catalog amount
9. Fallback logged when catalog empty
10. Prepa returns competition type

Part D: _ensure_fee integration
11. Uses catalog amount
12. Prepa → competition fee_type
13. Fallback logged

Part E: _resolve_frais1_fee_type
14. Prepa → competition
15. Non-prepa → application
16. None → application

Ref: ADM-UF-3, SPEC §4.4, DEC-221.
"""

from __future__ import annotations

from unittest import TestCase
from unittest.mock import MagicMock, patch


SYNC = "admission.api.fee_catalog_sync"
PUB = "admission.api.public"


# ── Part A: sync internals ──────────────────────────────────────────────────


class TestSyncFeeCatalog(TestCase):

    @patch(f"{SYNC}.now_datetime", return_value="2026-06-08 12:00:00")
    @patch(f"{SYNC}._get_uf_config", return_value={
        "url": "http://backoffice:8000",
        "api_key": "k",
        "api_secret": "s",
    })
    @patch(f"{SYNC}.requests.get")
    @patch(f"{SYNC}.frappe")
    def test_sync_upserts_entries(self, mock_frappe, mock_get, mock_config, mock_now):
        from admission.api.fee_catalog_sync import sync_fee_catalog

        resp = MagicMock()
        resp.json.return_value = {
            "message": {
                "catalog": [
                    {"program_code": "PRE", "fee_type": "competition", "amount_xof": 10000},
                    {"program_code": "LIS", "fee_type": "application", "amount_xof": 25000},
                ],
                "exported_at": "2026-06-08 12:00:00",
            }
        }
        resp.raise_for_status.return_value = None
        mock_get.return_value = resp

        mock_frappe.db.exists.return_value = False

        result = sync_fee_catalog()

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["entries_synced"], 2)
        self.assertEqual(mock_frappe.get_doc.call_count, 2)
        mock_frappe.db.commit.assert_called_once()

    @patch(f"{SYNC}.now_datetime", return_value="2026-06-08 12:00:00")
    @patch(f"{SYNC}._get_uf_config", return_value={
        "url": "http://backoffice:8000",
        "api_key": "k",
        "api_secret": "s",
    })
    @patch(f"{SYNC}.requests.get")
    @patch(f"{SYNC}.frappe")
    def test_sync_updates_existing_entry(self, mock_frappe, mock_get, mock_config, mock_now):
        from admission.api.fee_catalog_sync import sync_fee_catalog

        resp = MagicMock()
        resp.json.return_value = {
            "message": {
                "catalog": [
                    {"program_code": "PRE", "fee_type": "competition", "amount_xof": 12000},
                ],
            }
        }
        resp.raise_for_status.return_value = None
        mock_get.return_value = resp

        mock_frappe.db.exists.return_value = True

        result = sync_fee_catalog()

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["entries_synced"], 1)
        mock_frappe.db.set_value.assert_called_once()
        set_call = mock_frappe.db.set_value.call_args
        self.assertEqual(set_call[0][0], "Admission Fee Catalog")
        self.assertEqual(set_call[0][1], "PRE-DEFAULT-competition")
        self.assertEqual(set_call[0][2]["amount_xof"], 12000.0)

    @patch(f"{SYNC}._get_uf_config", return_value={
        "url": "http://backoffice:8000",
        "api_key": "k",
        "api_secret": "s",
    })
    @patch(f"{SYNC}.requests.get")
    @patch(f"{SYNC}.frappe")
    def test_sync_network_error_graceful(self, mock_frappe, mock_get, mock_config):
        import requests as req
        from admission.api.fee_catalog_sync import sync_fee_catalog

        mock_get.side_effect = req.ConnectionError("UF unreachable")

        result = sync_fee_catalog()

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["reason"], "fetch_failed")

    @patch(f"{SYNC}._get_uf_config", return_value=None)
    @patch(f"{SYNC}.frappe")
    def test_sync_missing_config_skips(self, mock_frappe, mock_config):
        from admission.api.fee_catalog_sync import sync_fee_catalog

        result = sync_fee_catalog()

        self.assertEqual(result["status"], "skipped")


# ── Part B: _resolve_fee_from_catalog ───────────────────────────────────────


class TestResolveFeeFromCatalog(TestCase):

    @patch(f"{PUB}.frappe")
    def test_exact_match(self, mock_frappe):
        from admission.api.public import _resolve_fee_from_catalog

        def fake_get_value(doctype, key, field):
            if key == "PRE-DEFAULT-competition":
                return 10000
            return None

        mock_frappe.db.get_value.side_effect = fake_get_value

        result = _resolve_fee_from_catalog("PRE", "competition")
        self.assertEqual(result, 10000.0)

    @patch(f"{PUB}.frappe")
    def test_default_fallback(self, mock_frappe):
        from admission.api.public import _resolve_fee_from_catalog

        def fake_get_value(doctype, key, field):
            if key == "LIC-DEFAULT-application":
                return None
            if key == "DEFAULT-DEFAULT-application":
                return 25000
            return None

        mock_frappe.db.get_value.side_effect = fake_get_value

        result = _resolve_fee_from_catalog("LIC", "application")
        self.assertEqual(result, 25000.0)

    @patch(f"{PUB}.frappe")
    def test_no_match_returns_none(self, mock_frappe):
        from admission.api.public import _resolve_fee_from_catalog

        mock_frappe.db.get_value.return_value = None

        result = _resolve_fee_from_catalog("UNKNOWN", "enrollment")
        self.assertIsNone(result)


# ── Part C: _build_frais_data (get_frais core) ─────────────────────────────


LEGAL = "admission.api.legal"


class TestBuildFraisData(TestCase):

    @patch(f"{LEGAL}._get_active_legal_texts_meta", return_value={})
    @patch(f"{LEGAL}._get_versioned_disclaimer", return_value=("Disclaimer", "hash1"))
    @patch(f"{PUB}._resolve_fee_from_catalog")
    @patch(f"{PUB}.frappe")
    def test_uses_catalog_amount(self, mock_frappe, mock_resolve, mock_disc, mock_texts):
        from admission.api.public import _build_frais_data

        session = MagicMock()
        session.programme_code = "LIS"
        session.is_prepa_session = 0
        session.application_fee_xof = 15000

        mock_resolve.side_effect = lambda prog, ft, lc=None: {
            ("LIS", "application"): 25000.0,
            ("LIS", "enrollment"): 50000.0,
        }.get((prog, ft))

        result = _build_frais_data(session)

        self.assertEqual(result["frais1"]["montant_xof"], 25000.0)
        self.assertEqual(result["frais1"]["fee_type"], "application")
        self.assertEqual(result["frais2"]["montant_xof"], 50000.0)

    @patch(f"{LEGAL}._get_active_legal_texts_meta", return_value={})
    @patch(f"{LEGAL}._get_versioned_disclaimer", return_value=("Disclaimer", None))
    @patch(f"{PUB}._resolve_fee_from_catalog", return_value=None)
    @patch(f"{PUB}.frappe")
    def test_fallback_logged_when_catalog_empty(self, mock_frappe, mock_resolve, mock_disc, mock_texts):
        from admission.api.public import _build_frais_data

        session = MagicMock()
        session.programme_code = "LIC"
        session.is_prepa_session = 0
        session.application_fee_xof = 15000

        result = _build_frais_data(session)

        self.assertEqual(result["frais1"]["montant_xof"], 15000)
        mock_frappe.logger.assert_called_with("fee_catalog")
        self.assertNotIn("frais2", result)

    @patch(f"{LEGAL}._get_active_legal_texts_meta", return_value={})
    @patch(f"{LEGAL}._get_versioned_disclaimer", return_value=("Disclaimer", "hash1"))
    @patch(f"{PUB}._resolve_fee_from_catalog")
    @patch(f"{PUB}.frappe")
    def test_prepa_returns_competition_type(self, mock_frappe, mock_resolve, mock_disc, mock_texts):
        from admission.api.public import _build_frais_data

        session = MagicMock()
        session.programme_code = "PRE"
        session.is_prepa_session = 1
        session.application_fee_xof = 15000

        mock_resolve.side_effect = lambda prog, ft, lc=None: {
            ("PRE", "competition"): 10000.0,
            ("PRE", "enrollment"): 75000.0,
        }.get((prog, ft))

        result = _build_frais_data(session)

        self.assertEqual(result["frais1"]["fee_type"], "competition")
        self.assertEqual(result["frais1"]["montant_xof"], 10000.0)
        self.assertEqual(result["frais2"]["montant_xof"], 75000.0)


# ── Part D: _ensure_fee integration ─────────────────────────────────────────


class TestEnsureFeeCatalog(TestCase):

    @patch(f"{PUB}._resolve_fee_from_catalog", return_value=25000.0)
    @patch(f"{PUB}._session_doc")
    @patch(f"{PUB}.frappe")
    def test_uses_catalog_amount(self, mock_frappe, mock_session, mock_resolve):
        from admission.api.public import _ensure_fee

        session = MagicMock()
        session.programme_code = "LIS"
        session.is_prepa_session = 0
        session.application_fee_xof = 15000
        mock_session.return_value = session

        mock_frappe.get_all.return_value = []

        applicant = MagicMock()
        applicant.name = "CAN-2026-001"
        applicant.session = "SES-2026-LIC"
        applicant.person_id = "PERS-00001"

        fee_mock = MagicMock()
        mock_frappe.get_doc.return_value = fee_mock

        _ensure_fee(applicant)

        doc_dict = mock_frappe.get_doc.call_args[0][0]
        self.assertEqual(doc_dict["amount_xof"], 25000.0)
        self.assertEqual(doc_dict["fee_type"], "application")

    @patch(f"{PUB}._resolve_fee_from_catalog", return_value=10000.0)
    @patch(f"{PUB}._session_doc")
    @patch(f"{PUB}.frappe")
    def test_prepa_uses_competition_type(self, mock_frappe, mock_session, mock_resolve):
        from admission.api.public import _ensure_fee

        session = MagicMock()
        session.programme_code = "PRE"
        session.is_prepa_session = 1
        session.application_fee_xof = 15000
        mock_session.return_value = session

        mock_frappe.get_all.return_value = []

        applicant = MagicMock()
        applicant.name = "CAN-2026-002"
        applicant.session = "SES-2026-10"
        applicant.person_id = "PERS-00002"

        fee_mock = MagicMock()
        mock_frappe.get_doc.return_value = fee_mock

        _ensure_fee(applicant)

        doc_dict = mock_frappe.get_doc.call_args[0][0]
        self.assertEqual(doc_dict["fee_type"], "competition")
        self.assertEqual(doc_dict["amount_xof"], 10000.0)

    @patch(f"{PUB}._resolve_fee_from_catalog", return_value=None)
    @patch(f"{PUB}._session_doc")
    @patch(f"{PUB}.frappe")
    def test_fallback_logged(self, mock_frappe, mock_session, mock_resolve):
        from admission.api.public import _ensure_fee

        session = MagicMock()
        session.programme_code = "LIC"
        session.is_prepa_session = 0
        session.application_fee_xof = 15000
        mock_session.return_value = session

        mock_frappe.get_all.return_value = []

        applicant = MagicMock()
        applicant.name = "CAN-2026-003"
        applicant.session = "SES-2026-LIC"
        applicant.person_id = "PERS-00003"

        fee_mock = MagicMock()
        mock_frappe.get_doc.return_value = fee_mock

        _ensure_fee(applicant)

        doc_dict = mock_frappe.get_doc.call_args[0][0]
        self.assertEqual(doc_dict["amount_xof"], 15000)
        mock_frappe.logger.assert_called_with("fee_catalog")


# ── Part E: _resolve_frais1_fee_type ────────────────────────────────────────


class TestResolveFrais1FeeType(TestCase):

    def test_prepa_returns_competition(self):
        from admission.api.public import _resolve_frais1_fee_type

        session = MagicMock()
        session.is_prepa_session = 1

        self.assertEqual(_resolve_frais1_fee_type(session), "competition")

    def test_non_prepa_returns_application(self):
        from admission.api.public import _resolve_frais1_fee_type

        session = MagicMock()
        session.is_prepa_session = 0

        self.assertEqual(_resolve_frais1_fee_type(session), "application")

    def test_none_session_returns_application(self):
        from admission.api.public import _resolve_frais1_fee_type

        self.assertEqual(_resolve_frais1_fee_type(None), "application")
