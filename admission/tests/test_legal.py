"""Tests ADM-LEG — Legal infrastructure (textes versionnés + consentement tracé).

Part A: Admission Legal Document (3 tests)
1. content_hash SHA-256 auto-calculé
2. is_active unique par type → throw si doublon
3. _get_active_legal_document retourne doc ou None

Part B: Admission Consent Record (2 tests)
4. Création complète (accepted_at, ip, ua, version_hash)
5. Immuable — throw si modification

Part C: Helpers (3 tests)
6. _record_consent crée un Consent Record correct
7. _has_consent + _require_consent_record
8. _get_versioned_disclaimer (texte versionné ou fallback)

Part D: Gate M1 — create_dossier (2 tests)
9.  Consent manquant → CONSENT_REQUIRED (bloqué)
10. Consent OK → dossier créé + 2 Consent Records

Part E: Gate M2 — frais 1 (2 tests)
11. consent_refund manquant → REFUND_CONSENT_REQUIRED (bloqué)
12. consent_refund OK → Consent Record créé

Part F: Gate M4 — frais 2 (2 tests)
13. consent manquant → bloqué
14. consent OK → 2 Consent Records (REFUND + DATA_TRANSFER)

Part G: Gate M5 — transition INS (2 tests)
15. DATA_TRANSFER absent → throw
16. DATA_TRANSFER présent → INS OK

Part H: Bridge consent proof (1 test)
17. Payload contient consent_data_transfer (version_hash + accepted_at)

Part I: Endpoints (3 tests)
18. get_legal_documents retourne textes actifs
19. get_frais expose textes_legaux + disclaimer versionné
20. get_frais simulation_disclaimer_version présent

Ref: ADM-LEG, loi 2017-20, DEC-222, DEC-230.
"""

from __future__ import annotations

import hashlib
from unittest import TestCase
from unittest.mock import MagicMock, patch, call

import frappe as _real_frappe


def setUpModule():
    try:
        _real_frappe.local.flags
    except Exception:
        _real_frappe.local.flags = _real_frappe._dict(in_test=True)


LEGAL = "admission.api.legal"
PUBLIC = "admission.api.public"
BRIDGE = "admission.api.bridge"
APPLICANT_MOD = "admission.admission.doctype.admission_applicant.admission_applicant"
LEGAL_DOC_MOD = (
    "admission.admission.doctype.admission_legal_document.admission_legal_document"
)
CONSENT_REC_MOD = (
    "admission.admission.doctype.admission_consent_record.admission_consent_record"
)


# ── Part A: Admission Legal Document ────────────────────────────────────────


class TestLegalDocumentContentHash(TestCase):

    @patch(f"{LEGAL_DOC_MOD}.frappe")
    def test_content_hash_computed_on_validate(self, mock_frappe):
        from admission.admission.doctype.admission_legal_document.admission_legal_document import (
            AdmissionLegalDocument,
        )

        doc = MagicMock()
        doc.content_text = "Test legal content"
        doc.is_active = 0
        doc.document_type = "CGV"
        doc.name = "LEGAL-2026-00001"
        doc.content_hash = None

        AdmissionLegalDocument.validate(doc)

        expected = hashlib.sha256(b"Test legal content").hexdigest()
        self.assertEqual(doc.content_hash, expected)

    @patch(f"{LEGAL_DOC_MOD}.frappe")
    def test_is_active_unique_per_type(self, mock_frappe):
        from admission.admission.doctype.admission_legal_document.admission_legal_document import (
            AdmissionLegalDocument,
        )

        doc = MagicMock()
        doc.content_text = "content"
        doc.is_active = 1
        doc.document_type = "CGV"
        doc.name = "LEGAL-2026-00002"

        mock_frappe.get_all.return_value = ["LEGAL-2026-00001"]
        mock_frappe.throw.side_effect = Exception("unique constraint")

        with self.assertRaises(Exception):
            AdmissionLegalDocument.validate(doc)

        mock_frappe.throw.assert_called_once()
        self.assertIn("existe deja", mock_frappe.throw.call_args[0][0])

    @patch(f"{LEGAL}.frappe")
    def test_get_active_legal_document(self, mock_frappe):
        from admission.api.legal import _get_active_legal_document

        mock_frappe.get_all.return_value = ["LEGAL-2026-00001"]
        mock_doc = MagicMock()
        mock_doc.content_hash = "abc123"
        mock_frappe.get_doc.return_value = mock_doc

        result = _get_active_legal_document("CGV")

        self.assertEqual(result, mock_doc)
        mock_frappe.get_all.assert_called_once_with(
            "Admission Legal Document",
            filters={"document_type": "CGV", "is_active": 1},
            pluck="name",
            limit=1,
        )

    @patch(f"{LEGAL}.frappe")
    def test_get_active_legal_document_none(self, mock_frappe):
        from admission.api.legal import _get_active_legal_document

        mock_frappe.get_all.return_value = []

        result = _get_active_legal_document("NONEXISTENT")

        self.assertIsNone(result)


# ── Part B: Admission Consent Record ────────────────────────────────────────


class TestConsentRecordCreation(TestCase):

    @patch(f"{LEGAL}.now_datetime", return_value="2026-06-09 15:00:00")
    @patch(f"{LEGAL}._get_user_agent", return_value="Mozilla/5.0")
    @patch(f"{LEGAL}._get_client_ip", return_value="192.168.1.1")
    @patch(f"{LEGAL}.frappe")
    def test_record_consent_creates_complete_record(
        self, mock_frappe, mock_ip, mock_ua, mock_now
    ):
        from admission.api.legal import _record_consent

        legal_doc = MagicMock()
        legal_doc.content_hash = "sha256hash"

        def fake_get_doc(*args):
            if len(args) == 2 and isinstance(args[0], str):
                return legal_doc
            record = MagicMock()
            record.name = "CONS-2026-00001"
            record.insert = MagicMock()
            return record

        mock_frappe.get_doc.side_effect = fake_get_doc

        result = _record_consent("CAN-2026-00001", "DATA_PROCESSING", "LEGAL-2026-00001")

        self.assertEqual(result, "CONS-2026-00001")

    @patch(f"{CONSENT_REC_MOD}.frappe")
    def test_consent_record_immutable(self, mock_frappe):
        from admission.admission.doctype.admission_consent_record.admission_consent_record import (
            AdmissionConsentRecord,
        )

        doc = MagicMock()
        doc.is_new.return_value = False
        mock_frappe.throw.side_effect = Exception("immutable")

        with self.assertRaises(Exception):
            AdmissionConsentRecord.before_save(doc)

        mock_frappe.throw.assert_called_once()
        self.assertIn("immuable", mock_frappe.throw.call_args[0][0])


# ── Part C: Helpers ─────────────────────────────────────────────────────────


class TestHasConsent(TestCase):

    @patch(f"{LEGAL}.frappe")
    def test_has_consent_true(self, mock_frappe):
        from admission.api.legal import _has_consent

        mock_frappe.db.exists.return_value = "CONS-2026-00001"

        self.assertTrue(_has_consent("CAN-2026-00001", "CGV"))

    @patch(f"{LEGAL}.frappe")
    def test_has_consent_false(self, mock_frappe):
        from admission.api.legal import _has_consent

        mock_frappe.db.exists.return_value = None

        self.assertFalse(_has_consent("CAN-2026-00001", "CGV"))

    @patch(f"{LEGAL}.frappe")
    def test_require_consent_throws_if_absent(self, mock_frappe):
        from admission.api.legal import _require_consent_record

        mock_frappe.db.exists.return_value = None
        mock_frappe.throw.side_effect = Exception("required")

        with self.assertRaises(Exception):
            _require_consent_record("CAN-2026-00001", "DATA_TRANSFER")

        mock_frappe.throw.assert_called_once()
        self.assertIn("DATA_TRANSFER", mock_frappe.throw.call_args[0][0])


class TestVersionedDisclaimer(TestCase):

    @patch(f"{LEGAL}._get_active_legal_document")
    def test_versioned_from_doc(self, mock_get):
        from admission.api.legal import _get_versioned_disclaimer

        doc = MagicMock()
        doc.content_text = "Versioned disclaimer text"
        doc.content_hash = "hash123"
        mock_get.return_value = doc

        text, hash_val = _get_versioned_disclaimer()

        self.assertEqual(text, "Versioned disclaimer text")
        self.assertEqual(hash_val, "hash123")

    @patch(f"{LEGAL}._get_active_legal_document")
    def test_fallback_when_no_doc(self, mock_get):
        from admission.api.legal import (
            _get_versioned_disclaimer,
            SIMULATION_DISCLAIMER_FALLBACK,
        )

        mock_get.return_value = None

        text, hash_val = _get_versioned_disclaimer()

        self.assertEqual(text, SIMULATION_DISCLAIMER_FALLBACK)
        self.assertIsNone(hash_val)


# ── Part D: Gate M1 — create_dossier ────────────────────────────────────────


class TestCreateDossierConsentGate(TestCase):

    @patch(f"{PUBLIC}._session_doc")
    @patch(f"{PUBLIC}.frappe")
    def test_consent_missing_blocked(self, mock_frappe, mock_session):
        from admission.api.public import create_dossier

        session = MagicMock()
        session.programme_code = "LIS"
        session.name = "SES-001"
        mock_session.return_value = session
        mock_frappe.db.exists.return_value = True
        mock_frappe.db.get_value.return_value = "LIS"
        mock_frappe.request = None
        mock_frappe.form_dict = {
            "session": "SES-001",
            "level_code": "LIS-L1",
            "identite": {"prenom": "Test", "nom": "User", "email": "t@t.com"},
        }

        result = create_dossier()

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "CONSENT_REQUIRED")

    @patch(f"{PUBLIC}._ensure_fee")
    @patch(f"{PUBLIC}._resolve_person_from_campus", return_value="PERS-00001")
    @patch(f"{PUBLIC}._generate_token", return_value="tok123")
    @patch(f"{PUBLIC}._hash", return_value="hashed")
    @patch(f"{PUBLIC}.now_datetime", return_value="2026-06-09 15:00:00")
    @patch(f"{LEGAL}._record_consent", return_value="CONS-001")
    @patch(f"{LEGAL}._get_active_legal_document")
    @patch(f"{PUBLIC}._session_doc")
    @patch(f"{PUBLIC}.frappe")
    def test_consent_ok_dossier_created(
        self, mock_frappe, mock_session, mock_get_legal, mock_record,
        mock_now, mock_hash, mock_token, mock_person, mock_ensure_fee,
    ):
        from admission.api.public import create_dossier

        session = MagicMock()
        session.programme_code = "LIS"
        session.programme_label = "Licence Sciences"
        session.name = "SES-001"
        mock_session.return_value = session

        mock_frappe.db.exists.return_value = True
        mock_frappe.db.get_value.return_value = "LIS"
        mock_frappe.request = None
        mock_frappe.form_dict = {
            "session": "SES-001",
            "level_code": "LIS-L1",
            "consent_data_processing": True,
            "consent_cgv": True,
            "identite": {"prenom": "Test", "nom": "User", "email": "t@t.com", "tel": "+22990112233"},
        }

        privacy_doc = MagicMock()
        privacy_doc.name = "LEGAL-PRIV"
        cgv_doc = MagicMock()
        cgv_doc.name = "LEGAL-CGV"
        mock_get_legal.side_effect = lambda dt: (
            privacy_doc if dt == "PRIVACY_POLICY" else cgv_doc
        )

        applicant_mock = MagicMock()
        applicant_mock.name = "CAN-2026-00001"
        applicant_mock.status = "BRO"
        applicant_mock.bac_date = None
        applicant_mock.pieces = []
        mock_frappe.get_doc.return_value = applicant_mock
        mock_frappe.get_all.return_value = []

        result = create_dossier()

        self.assertTrue(result["ok"])
        self.assertEqual(result["data"]["dossier_id"], "CAN-2026-00001")
        self.assertEqual(mock_record.call_count, 2)
        mock_record.assert_any_call("CAN-2026-00001", "DATA_PROCESSING", "LEGAL-PRIV")
        mock_record.assert_any_call("CAN-2026-00001", "CGV", "LEGAL-CGV")


# ── Part E: Gate M2 — frais 1 ──────────────────────────────────────────────


class TestPaymentOnlineConsentGate(TestCase):

    @patch(f"{PUBLIC}._get_applicant")
    @patch(f"{PUBLIC}.frappe")
    def test_refund_consent_missing_blocked(self, mock_frappe, mock_get):
        from admission.api.public import submit_payment_online

        applicant = MagicMock()
        mock_get.return_value = applicant
        mock_frappe.form_dict = {}
        mock_frappe.request = None

        result = submit_payment_online(dossier_id="CAN-001", token="tok")

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "REFUND_CONSENT_REQUIRED")

    @patch(f"{PUBLIC}.secrets")
    @patch(f"{LEGAL}._record_consent", return_value="CONS-001")
    @patch(f"{LEGAL}._get_active_legal_document")
    @patch(f"{PUBLIC}._ensure_fee")
    @patch(f"{PUBLIC}._get_applicant")
    @patch(f"{PUBLIC}.frappe")
    def test_refund_consent_ok_record_created(
        self, mock_frappe, mock_get, mock_ensure, mock_get_legal,
        mock_record, mock_secrets,
    ):
        from admission.api.public import submit_payment_online

        applicant = MagicMock()
        applicant.name = "CAN-001"
        applicant.pieces = []  # Lot 3a : garde pièces lit applicant.pieces avant _ensure_fee
        mock_get.return_value = applicant
        mock_frappe.form_dict = {}
        mock_frappe.request = None

        refund_doc = MagicMock()
        refund_doc.name = "LEGAL-REFUND"
        mock_get_legal.return_value = refund_doc

        fee = MagicMock()
        fee.amount_xof = 25000
        mock_ensure.return_value = fee
        mock_secrets.token_hex.return_value = "ref123"
        mock_frappe.db.exists.return_value = False   # garde amont B1 : aucun paiement Confirmed sur ce fee

        result = submit_payment_online(
            dossier_id="CAN-001", token="tok", consent_refund=True
        )

        self.assertTrue(result["ok"])
        mock_record.assert_called_once_with(
            "CAN-001", "REFUND_ACKNOWLEDGMENT", "LEGAL-REFUND"
        )


class TestPaymentOfflineConsentGate(TestCase):

    @patch(f"{PUBLIC}._get_applicant")
    @patch(f"{PUBLIC}.frappe")
    def test_refund_consent_missing_blocked(self, mock_frappe, mock_get):
        from admission.api.public import declare_payment_offline

        applicant = MagicMock()
        applicant.status = "BRO"
        mock_get.return_value = applicant
        mock_frappe.form_dict = {}
        mock_frappe.request = None
        mock_frappe.local.response = {}

        result = declare_payment_offline(dossier_id="CAN-001", token="tok")

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "REFUND_CONSENT_REQUIRED")


# ── Part F: Gate M4 — frais 2 ──────────────────────────────────────────────


class TestEnrollmentPaymentConsentGate(TestCase):

    @patch(f"{PUBLIC}._get_applicant")
    @patch(f"{PUBLIC}.frappe")
    def test_refund_consent_missing_blocked(self, mock_frappe, mock_get):
        from admission.api.public import submit_enrollment_payment_online

        applicant = MagicMock()
        applicant.status = "ACC"
        mock_get.return_value = applicant
        mock_frappe.form_dict = {}
        mock_frappe.request = None

        result = submit_enrollment_payment_online(
            dossier_id="CAN-001", token="tok", consent_data_transfer=True,
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "REFUND_CONSENT_REQUIRED")

    @patch(f"{PUBLIC}._get_applicant")
    @patch(f"{PUBLIC}.frappe")
    def test_data_transfer_consent_missing_blocked(self, mock_frappe, mock_get):
        from admission.api.public import submit_enrollment_payment_online

        applicant = MagicMock()
        applicant.status = "ACC"
        mock_get.return_value = applicant
        mock_frappe.form_dict = {}
        mock_frappe.request = None

        result = submit_enrollment_payment_online(
            dossier_id="CAN-001", token="tok", consent_refund=True,
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "DATA_TRANSFER_CONSENT_REQUIRED")

    @patch(f"{PUBLIC}.secrets")
    @patch(f"{LEGAL}._record_consent", return_value="CONS-001")
    @patch(f"{LEGAL}._get_active_legal_document")
    @patch(f"{PUBLIC}._ensure_enrollment_fee")
    @patch(f"{PUBLIC}._get_applicant")
    @patch(f"{PUBLIC}.frappe")
    def test_both_consents_ok_records_created(
        self, mock_frappe, mock_get, mock_ensure, mock_get_legal,
        mock_record, mock_secrets,
    ):
        from admission.api.public import submit_enrollment_payment_online

        applicant = MagicMock()
        applicant.name = "CAN-001"
        applicant.status = "ACC"
        applicant.acompte_xof = 0
        mock_get.return_value = applicant
        mock_frappe.form_dict = {}
        mock_frappe.request = None

        refund_doc = MagicMock()
        refund_doc.name = "LEGAL-REFUND"
        transfer_doc = MagicMock()
        transfer_doc.name = "LEGAL-TRANSFER"
        mock_get_legal.side_effect = lambda dt: (
            refund_doc if dt == "REFUND_POLICY" else transfer_doc
        )

        fee = MagicMock()
        fee.amount_xof = 50000
        mock_ensure.return_value = fee
        mock_secrets.token_hex.return_value = "ref123"
        mock_frappe.db.exists.return_value = False   # garde amont B1 : aucun paiement Confirmed sur ce fee

        result = submit_enrollment_payment_online(
            dossier_id="CAN-001", token="tok",
            consent_refund=True, consent_data_transfer=True,
        )

        self.assertTrue(result["ok"])
        self.assertEqual(mock_record.call_count, 2)
        mock_record.assert_any_call("CAN-001", "REFUND_ACKNOWLEDGMENT", "LEGAL-REFUND")
        mock_record.assert_any_call("CAN-001", "DATA_TRANSFER", "LEGAL-TRANSFER")


# ── Part G: Gate M5 — transition INS ────────────────────────────────────────


class TestINSTransitionConsentGate(TestCase):

    @patch(f"{LEGAL}._has_consent", return_value=False)
    @patch(f"{LEGAL}.frappe")
    def test_data_transfer_absent_throws(self, mock_frappe, mock_has):
        from admission.api.legal import _require_consent_record

        mock_frappe.throw.side_effect = Exception("blocked")

        with self.assertRaises(Exception):
            _require_consent_record("CAN-2026-00001", "DATA_TRANSFER")

    @patch(f"{LEGAL}.frappe")
    def test_data_transfer_present_passes(self, mock_frappe):
        from admission.api.legal import _require_consent_record

        mock_frappe.db.exists.return_value = "CONS-001"

        _require_consent_record("CAN-2026-00001", "DATA_TRANSFER")

        mock_frappe.throw.assert_not_called()


# ── Part H: Bridge consent proof ────────────────────────────────────────────


class TestBridgeConsentProof(TestCase):

    @patch(f"{LEGAL}._get_consent_proof")
    @patch(f"{BRIDGE}.frappe")
    def test_payload_includes_consent_proof(self, mock_frappe, mock_proof):
        from admission.api.bridge import _build_financial_context

        mock_proof.return_value = {
            "version_hash": "sha256abc",
            "accepted_at": "2026-06-09 14:00:00",
        }

        applicant = MagicMock()
        applicant.name = "CAN-001"
        applicant.person_id = "PERS-001"
        applicant.programme_code = "LIS"
        applicant.level_code = "LIS-L1"
        applicant.session = "SES-001"
        applicant.validated_scholarships = "[]"
        applicant.promo_code = ""
        applicant.promo_rate = 0
        applicant.promo_captured_date = None
        applicant.acompte_xof = 0

        session = MagicMock()
        session.academic_year = "2026-2027"
        mock_frappe.get_doc.return_value = session

        result = _build_financial_context(applicant)

        self.assertIn("consent_data_transfer", result)
        self.assertEqual(result["consent_data_transfer"]["version_hash"], "sha256abc")
        self.assertEqual(
            result["consent_data_transfer"]["accepted_at"], "2026-06-09 14:00:00"
        )


# ── Part I: Endpoints ───────────────────────────────────────────────────────


class TestGetLegalDocuments(TestCase):

    @patch(f"{LEGAL}._get_active_legal_texts")
    @patch(f"{PUBLIC}.frappe")
    def test_returns_active_texts(self, mock_frappe, mock_texts):
        from admission.api.public import get_legal_documents

        mock_texts.return_value = {
            "cgv": {
                "type": "CGV",
                "version": "PLACEHOLDER-V0",
                "content_text": "[CGV placeholder]",
                "content_hash": "abc",
            },
            "privacy_policy": {
                "type": "PRIVACY_POLICY",
                "version": "PLACEHOLDER-V0",
                "content_text": "[Privacy placeholder]",
                "content_hash": "def",
            },
        }
        mock_frappe.form_dict = {}
        mock_frappe.request = None

        result = get_legal_documents()

        self.assertTrue(result["ok"])
        self.assertIn("cgv", result["data"]["documents"])
        self.assertIn("privacy_policy", result["data"]["documents"])

    @patch(f"{LEGAL}._get_active_legal_texts")
    @patch(f"{PUBLIC}.frappe")
    def test_filters_by_type(self, mock_frappe, mock_texts):
        from admission.api.public import get_legal_documents

        mock_texts.return_value = {
            "cgv": {"type": "CGV", "version": "V0", "content_text": "c", "content_hash": "a"},
            "privacy_policy": {"type": "PRIVACY_POLICY", "version": "V0", "content_text": "p", "content_hash": "b"},
            "refund_policy": {"type": "REFUND_POLICY", "version": "V0", "content_text": "r", "content_hash": "c"},
        }
        mock_frappe.form_dict = {}
        mock_frappe.request = None

        result = get_legal_documents(types="CGV,PRIVACY_POLICY")

        self.assertTrue(result["ok"])
        docs = result["data"]["documents"]
        self.assertIn("cgv", docs)
        self.assertIn("privacy_policy", docs)
        self.assertNotIn("refund_policy", docs)


class TestGetFraisLegalTexts(TestCase):

    @patch(f"{LEGAL}._get_active_legal_texts_meta", return_value={"cgv": {"type": "CGV"}})
    @patch(f"{LEGAL}._get_versioned_disclaimer", return_value=("Versioned text", "hashV1"))
    @patch(f"{PUBLIC}._resolve_fee_from_catalog", return_value=25000.0)
    @patch(f"{PUBLIC}._get_scholarship_cap_local", return_value=0.5)
    @patch(f"{PUBLIC}._get_promotions_for_programme", return_value=[])
    @patch(f"{PUBLIC}._get_scholarships_for_programme", return_value=[])
    @patch(f"{PUBLIC}._resolve_frais1_fee_type", return_value="application")
    def test_get_frais_includes_legal_texts(
        self, mock_fee_type, mock_schol, mock_promo, mock_cap,
        mock_resolve, mock_disclaimer, mock_texts,
    ):
        from admission.api.public import _build_frais_data

        session = MagicMock()
        session.programme_code = "LIS"
        session.application_fee_xof = 25000

        result = _build_frais_data(session, "LIS-L1")

        self.assertEqual(result["simulation_disclaimer"], "Versioned text")
        self.assertEqual(result["simulation_disclaimer_version"], "hashV1")
        self.assertIn("textes_legaux", result)
        self.assertIn("cgv", result["textes_legaux"])
