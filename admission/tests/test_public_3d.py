"""Tests ADM-SCH — 3D catalog + level in public.py.

Part A: _resolve_fee_from_catalog 3D fallback (4 tests)
1. Exact 3D match (prog-level-fee_type)
2. Fallback to prog-DEFAULT-fee_type (logged)
3. Fallback to DEFAULT-DEFAULT-fee_type (logged)
4. No match → None

Part B: create_dossier level validation (3 tests)
5. level_code required → error if missing
6. Unknown level_code → error
7. Level mismatch with programme → error

Part C: list_programmes returns niveaux (1 test)
8. Includes levels from mirror

Part D: _serialize_dossier includes level (1 test)
9. Programme block includes level

Part E: AdmissionSession academic_year validation (2 tests)
10. Valid format → OK
11. Invalid format → throw

Ref: ADM-SCH, D1 (3-level fallback), D2 (découplage), D3 (contrat front), D4 (academic_year).
"""

from __future__ import annotations

from unittest import TestCase
from unittest.mock import MagicMock, patch

import frappe as _real_frappe


def setUpModule():
    try:
        _real_frappe.local.flags
    except Exception:
        _real_frappe.local.flags = _real_frappe._dict(in_test=True)


PUB = "admission.api.public"
SESSION_MOD = "admission.admission.doctype.admission_session.admission_session"


# ── Part A: _resolve_fee_from_catalog 3D ──────────────────────────────────────


class TestResolveFee3D(TestCase):

    @patch(f"{PUB}.frappe")
    def test_exact_3d_match(self, mock_frappe):
        from admission.api.public import _resolve_fee_from_catalog

        def fake_get_value(doctype, key, field):
            if key == "PRE-A1-competition":
                return 10000
            return None

        mock_frappe.db.get_value.side_effect = fake_get_value

        result = _resolve_fee_from_catalog("PRE", "competition", "A1")
        self.assertEqual(result, 10000.0)

    @patch(f"{PUB}.frappe")
    def test_fallback_prog_default(self, mock_frappe):
        from admission.api.public import _resolve_fee_from_catalog

        def fake_get_value(doctype, key, field):
            if key == "LIS-DEFAULT-application":
                return 25000
            return None

        mock_frappe.db.get_value.side_effect = fake_get_value

        result = _resolve_fee_from_catalog("LIS", "application", "L1")
        self.assertEqual(result, 25000.0)
        mock_frappe.logger.assert_called_with("fee_catalog")

    @patch(f"{PUB}.frappe")
    def test_fallback_default_default(self, mock_frappe):
        from admission.api.public import _resolve_fee_from_catalog

        def fake_get_value(doctype, key, field):
            if key == "DEFAULT-DEFAULT-enrollment":
                return 50000
            return None

        mock_frappe.db.get_value.side_effect = fake_get_value

        result = _resolve_fee_from_catalog("UNKNOWN", "enrollment", "X1")
        self.assertEqual(result, 50000.0)
        mock_frappe.logger.assert_called_with("fee_catalog")

    @patch(f"{PUB}.frappe")
    def test_all_levels_miss_returns_none(self, mock_frappe):
        from admission.api.public import _resolve_fee_from_catalog

        mock_frappe.db.get_value.return_value = None

        result = _resolve_fee_from_catalog("UNKNOWN", "enrollment", "X1")
        self.assertIsNone(result)
        self.assertEqual(mock_frappe.db.get_value.call_count, 3)


# ── Part B: create_dossier level validation ───────────────────────────────────


class TestCreateDossierLevel(TestCase):

    @patch(f"{PUB}._session_doc")
    @patch(f"{PUB}.frappe")
    def test_level_required(self, mock_frappe, mock_session):
        from admission.api.public import create_dossier

        session = MagicMock()
        session.programme_code = "LIS"
        session.name = "SES-001"
        mock_session.return_value = session
        mock_frappe.request = None
        mock_frappe.form_dict = {
            "session": "SES-001",
            "identite": {"prenom": "Test", "nom": "User", "email": "t@t.com"},
        }

        result = create_dossier()

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "LEVEL_REQUIRED")

    @patch(f"{PUB}._session_doc")
    @patch(f"{PUB}.frappe")
    def test_unknown_level_rejected(self, mock_frappe, mock_session):
        from admission.api.public import create_dossier

        session = MagicMock()
        session.programme_code = "LIS"
        session.name = "SES-001"
        mock_session.return_value = session
        mock_frappe.db.exists.return_value = False
        mock_frappe.request = None
        mock_frappe.form_dict = {
            "session": "SES-001",
            "level_code": "FAKE-L9",
            "identite": {"prenom": "Test", "nom": "User", "email": "t@t.com"},
        }

        result = create_dossier()

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "INVALID_LEVEL")

    @patch(f"{PUB}._session_doc")
    @patch(f"{PUB}.frappe")
    def test_level_mismatch_rejected(self, mock_frappe, mock_session):
        from admission.api.public import create_dossier

        session = MagicMock()
        session.programme_code = "LIS"
        session.name = "SES-001"
        mock_session.return_value = session
        mock_frappe.db.exists.return_value = True
        mock_frappe.db.get_value.return_value = "PRE"
        mock_frappe.request = None
        mock_frappe.form_dict = {
            "session": "SES-001",
            "level_code": "PRE-A1",
            "identite": {"prenom": "Test", "nom": "User", "email": "t@t.com"},
        }

        result = create_dossier()

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "LEVEL_MISMATCH")


# ── Part C: list_programmes returns niveaux ───────────────────────────────────


class TestListProgrammesWithLevels(TestCase):

    @patch(f"{PUB}.frappe")
    def test_includes_niveaux(self, mock_frappe):
        from admission.api.public import list_programmes

        mock_frappe.get_all.side_effect = [
            [MagicMock(programme_code="LIS", programme_label="Licence Sciences")],
            [
                MagicMock(level_code="LIS-L1", level_name="Licence 1", program_code="LIS", level_order=1),
                MagicMock(level_code="LIS-L2", level_name="Licence 2", program_code="LIS", level_order=2),
            ],
            [],  # _programme_meta_map : Admission Programme (pas de métadonnée dans ce test)
        ]

        result = list_programmes()

        self.assertTrue(result["ok"])
        progs = result["data"]["programmes"]
        self.assertEqual(len(progs), 1)
        self.assertEqual(progs[0]["code"], "LIS")
        self.assertEqual(len(progs[0]["niveaux"]), 2)
        self.assertEqual(progs[0]["niveaux"][0]["level_code"], "LIS-L1")


# ── Part D: _serialize_dossier includes level ─────────────────────────────────


class TestSerializeDossierLevel(TestCase):

    @patch(f"{PUB}._build_promotion_section", return_value={"code": None, "taux": 0})
    @patch(f"{PUB}._build_bourses_section", return_value={"demandees": [], "simulation": None, "valide": False})
    @patch(f"{PUB}._get_fee_and_payment", return_value=(None, None))
    @patch(f"{PUB}._session_doc")
    @patch(f"{PUB}.frappe")
    def test_includes_level_in_programme(
        self, mock_frappe, mock_session, mock_fees, mock_bourses, mock_promo,
    ):
        from admission.api.public import _serialize_dossier

        mock_frappe.db.get_value.return_value = "Licence 1"

        applicant = MagicMock()
        applicant.name = "CAN-001"
        applicant.status = "BRO"
        applicant.programme_code = "LIS"
        applicant.programme_label = "Licence Sciences"
        applicant.level_code = "LIS-L1"
        applicant.session = "SES-001"
        applicant.bac_profile = ""
        applicant.first_name = "Test"
        applicant.last_name = "User"
        applicant.email = "t@t.com"
        applicant.phone = ""
        applicant.bac_date = None
        applicant.conditionnel = 0
        applicant.pieces = []

        session = MagicMock()
        session.label = "Session 2026"
        session.academic_year = "2026-2027"
        mock_session.return_value = session

        result = _serialize_dossier(applicant)

        self.assertIsNotNone(result["programme"]["level"])
        self.assertEqual(result["programme"]["level"]["code"], "LIS-L1")
        self.assertEqual(result["programme"]["level"]["name"], "Licence 1")


# ── Part E: AdmissionSession academic_year ────────────────────────────────────


class TestAdmissionSessionAcademicYear(TestCase):

    @patch(f"{SESSION_MOD}.frappe")
    def test_valid_academic_year(self, mock_frappe):
        from admission.admission.doctype.admission_session.admission_session import (
            AdmissionSession,
        )

        doc = MagicMock()
        doc.opens_on = None
        doc.closes_on = None
        doc.academic_year = "2026-2027"

        AdmissionSession.validate(doc)

        mock_frappe.throw.assert_not_called()

    @patch(f"{SESSION_MOD}.frappe")
    def test_invalid_academic_year_format(self, mock_frappe):
        from admission.admission.doctype.admission_session.admission_session import (
            AdmissionSession,
        )

        mock_frappe.throw.side_effect = Exception("validation")

        doc = MagicMock()
        doc.opens_on = None
        doc.closes_on = None
        doc.academic_year = "2026/2027"

        with self.assertRaises(Exception):
            AdmissionSession.validate(doc)

        mock_frappe.throw.assert_called_once()
