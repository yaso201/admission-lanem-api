"""Tests DAT-1 — rétention / purge / anonymisation sélective.

- _retention_days : durée lue du singleton, défaut sinon (placeholder fonctionne).
- purge_expired_otp : scrubbe les hashes OTP expirés MAIS pas le token (récup SEC-EXPIRY préservée).
- anonymize_applicant : scrubbe PII + credentials + pièces (File) + Version ; PRÉSERVE Consent + Paiement (carve-out).
- purge_abandoned_dossiers : anonymise les BRO inactifs au-delà du délai.
- Consent Record : on_trash bloque la suppression (preuve immuable).

Ref: AUDIT-DAT1-RETENTION, loi 2017-20, ADM-LEG.
"""

from __future__ import annotations

from unittest import TestCase
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import frappe as _real_frappe
from frappe.utils import get_datetime


def setUpModule():
    try:
        _real_frappe.local.flags
    except Exception:
        _real_frappe.local.flags = _real_frappe._dict(in_test=True)


RET = "admission.api.retention"
PUB = "admission.api.public"
CONSENT = "admission.admission.doctype.admission_consent_record.admission_consent_record"
NOW = "2026-06-09 12:00:00"


class TestRetentionDays(TestCase):
    @patch(f"{RET}.frappe")
    def test_default_when_singleton_unset(self, mock_frappe):
        mock_frappe.db.get_single_value.return_value = None
        from admission.api.retention import _retention_days
        self.assertEqual(_retention_days("abandoned_bro_days"), 90)  # placeholder défaut

    @patch(f"{RET}.frappe")
    def test_value_from_singleton(self, mock_frappe):
        mock_frappe.db.get_single_value.return_value = 30
        from admission.api.retention import _retention_days
        self.assertEqual(_retention_days("abandoned_bro_days"), 30)


class TestPurgeExpiredOtp(TestCase):
    @patch(f"{RET}.now_datetime", return_value=get_datetime(NOW))
    @patch(f"{RET}.frappe")
    def test_clears_otp_hashes_not_token(self, mock_frappe, _now):
        mock_frappe.get_all.return_value = ["CAN-001"]
        from admission.api.retention import purge_expired_otp
        purge_expired_otp()
        updates = mock_frappe.db.set_value.call_args[0][2]
        self.assertIsNone(updates.get("otp_email_hash"))
        self.assertIsNone(updates.get("otp_phone_hash"))
        # SEC-EXPIRY : le token reste récupérable par re-OTP → ne PAS purger son hash ici.
        self.assertNotIn("dossier_token_hash", updates)
        # SEC-OTP : otp_verified PERSISTE entre visites → ne pas le toucher (pas de dé-vérif).
        self.assertNotIn("otp_verified", updates)


class TestAnonymizeApplicant(TestCase):
    @patch(f"{RET}.frappe")
    def test_scrubs_pii_and_credentials_preserves_consent_payment(self, mock_frappe):
        mock_frappe.get_hooks.return_value = [
            {"doctype": "Admission Applicant",
             "redact_fields": ["first_name", "last_name", "applicant_name", "email", "phone", "bac_date"]},
        ]
        # LOT G : redaction TYPÉE — le meta fournit les fieldtypes (Date → NULL, texte → placeholder)
        fieldtypes = {"first_name": "Data", "last_name": "Data", "applicant_name": "Data",
                      "email": "Data", "phone": "Data", "bac_date": "Date"}
        meta = MagicMock()
        meta.get_field.side_effect = lambda f: (
            SimpleNamespace(fieldtype=fieldtypes[f]) if f in fieldtypes else None
        )
        mock_frappe.get_meta.return_value = meta
        mock_frappe.get_all.return_value = ["FILE-1"]  # une pièce rattachée
        from admission.api.retention import anonymize_applicant
        anonymize_applicant("CAN-001")

        # PII + credentials scrubés sur l'Applicant (1re écriture ; une 2e vide le file pièce)
        appl_calls = [c for c in mock_frappe.db.set_value.call_args_list if c[0][0] == "Admission Applicant"]
        self.assertTrue(appl_calls)
        call = appl_calls[0]
        self.assertEqual(call[0][1], "CAN-001")
        updates = call[0][2]
        self.assertEqual(updates["first_name"], "[REDACTED]")
        self.assertEqual(updates["applicant_name"], "[REDACTED]")  # nom complet scrubé (LOT G)
        self.assertEqual(updates["email"], "[REDACTED]")
        self.assertIsNone(updates["bac_date"])  # colonne Date → NULL (sinon erreur SQL — LOT G)
        self.assertIsNone(updates["dossier_token_hash"])
        self.assertIsNone(updates["otp_email_hash"])

        # Pièce (File PII) supprimée + historique Version effacé
        mock_frappe.delete_doc.assert_called_with(
            "File", "FILE-1", ignore_permissions=True, force=True
        )
        mock_frappe.db.delete.assert_any_call(
            "Version", {"ref_doctype": "Admission Applicant", "docname": "CAN-001"}
        )

        # CARVE-OUT : aucune écriture/suppression sur Consent Record ni Paiement
        for c in mock_frappe.db.set_value.call_args_list:
            self.assertNotIn(c[0][0], ("Admission Consent Record", "Applicant Fee Payment"))
        for c in mock_frappe.delete_doc.call_args_list:
            self.assertNotIn(c[0][0], ("Admission Consent Record", "Applicant Fee Payment"))


class TestPurgeAbandoned(TestCase):
    @patch(f"{RET}.anonymize_applicant")
    @patch(f"{RET}.now_datetime", return_value=get_datetime(NOW))
    @patch(f"{RET}.frappe")
    def test_anonymizes_stale_bro(self, mock_frappe, _now, mock_anon):
        mock_frappe.db.get_single_value.return_value = None  # défaut 90j
        mock_frappe.get_all.return_value = ["CAN-OLD"]
        from admission.api.retention import purge_abandoned_dossiers
        purge_abandoned_dossiers()
        mock_anon.assert_called_with("CAN-OLD")
        # le filtre cible bien les BRO
        filters = mock_frappe.get_all.call_args.kwargs.get("filters", {})
        self.assertEqual(filters.get("status"), "BRO")


class TestConsentImmutableDelete(TestCase):
    @patch(f"{CONSENT}.frappe")
    def test_on_trash_blocks_deletion(self, mock_frappe):
        mock_frappe.throw.side_effect = Exception
        from admission.admission.doctype.admission_consent_record.admission_consent_record import (
            AdmissionConsentRecord,
        )
        doc = AdmissionConsentRecord.__new__(AdmissionConsentRecord)
        with self.assertRaises(Exception):
            doc.on_trash()


# ── DAT-1 finitions : flag anonymized (anomalie 4) + lien pièce vidé (anomalie 2) ─────


class TestAnonymizeFinitions(TestCase):
    @patch(f"{RET}.frappe")
    def test_sets_anonymized_flag(self, mock_frappe):
        mock_frappe.get_hooks.return_value = [
            {"doctype": "Admission Applicant", "redact_fields": ["email"]}
        ]
        mock_frappe.get_all.return_value = []
        from admission.api.retention import anonymize_applicant
        anonymize_applicant("CAN-1")
        appl = [c for c in mock_frappe.db.set_value.call_args_list if c[0][0] == "Admission Applicant"]
        self.assertEqual(appl[0][0][2].get("anonymized"), 1)

    @patch(f"{RET}.frappe")
    def test_clears_piece_file_link(self, mock_frappe):
        mock_frappe.get_hooks.return_value = []
        # get_all : 1) File rattachés (vide) 2) lignes Applicant Piece
        mock_frappe.get_all.side_effect = [[], ["PIECE-1"]]
        from admission.api.retention import anonymize_applicant
        anonymize_applicant("CAN-1")
        piece = [c for c in mock_frappe.db.set_value.call_args_list if c[0][0] == "Applicant Piece"]
        self.assertTrue(piece)
        self.assertEqual(piece[0][0][1], "PIECE-1")
        self.assertEqual(piece[0][0][2], "file")
        self.assertIsNone(piece[0][0][3])  # file vidé → plus de lien pendant

    @patch(f"{RET}.anonymize_applicant")
    @patch(f"{RET}.now_datetime", return_value=get_datetime(NOW))
    @patch(f"{RET}.frappe")
    def test_purge_filters_on_anonymized_not_email(self, mock_frappe, _now, _anon):
        mock_frappe.db.get_single_value.return_value = None
        mock_frappe.get_all.return_value = []
        from admission.api.retention import purge_abandoned_dossiers
        purge_abandoned_dossiers()
        filters = mock_frappe.get_all.call_args.kwargs.get("filters", {})
        self.assertIn("anonymized", filters)  # idempotence via le flag, pas via email


# ── DAT-1 self-service : effacement candidat (token + OTP + confirm) ───────────────────


class TestSelfServiceDeletion(TestCase):
    @patch(f"{PUB}._get_applicant", side_effect=Exception("no token"))
    @patch(f"{PUB}.frappe")
    def test_no_token_403(self, mock_frappe, _get):
        mock_frappe.local.response = {}; mock_frappe.form_dict = {}; mock_frappe.request = None
        from admission.api.public import request_data_deletion
        result = request_data_deletion(dossier_id="CAN-1")
        self.assertEqual(result["error"]["code"], "INVALID_DOSSIER")

    @patch(f"{PUB}._get_applicant")
    @patch(f"{PUB}.frappe")
    def test_no_otp_403(self, mock_frappe, mock_get):
        mock_frappe.local.response = {}; mock_frappe.form_dict = {}; mock_frappe.request = None
        a = MagicMock(); a.otp_verified = 0; mock_get.return_value = a
        from admission.api.public import request_data_deletion
        result = request_data_deletion(dossier_id="CAN-1", token="tok", confirm="true")
        self.assertEqual(result["error"]["code"], "OTP_REQUIRED")

    @patch(f"{PUB}._get_applicant")
    @patch(f"{PUB}.frappe")
    def test_no_confirm_refused(self, mock_frappe, mock_get):
        mock_frappe.local.response = {}; mock_frappe.form_dict = {}; mock_frappe.request = None
        a = MagicMock(); a.otp_verified = 1; mock_get.return_value = a
        from admission.api.public import request_data_deletion
        result = request_data_deletion(dossier_id="CAN-1", token="tok")  # pas de confirm
        self.assertEqual(result["error"]["code"], "CONFIRMATION_REQUIRED")

    @patch("admission.api.retention.anonymize_applicant")
    @patch(f"{PUB}._get_applicant")
    @patch(f"{PUB}.frappe")
    def test_full_anonymizes_with_carveout_message(self, mock_frappe, mock_get, mock_anon):
        mock_frappe.local.response = {}; mock_frappe.form_dict = {}; mock_frappe.request = None
        a = MagicMock(); a.name = "CAN-1"; a.otp_verified = 1; mock_get.return_value = a
        from admission.api.public import request_data_deletion
        result = request_data_deletion(dossier_id="CAN-1", token="tok", confirm="true")
        self.assertTrue(result["ok"])
        mock_anon.assert_called_once_with("CAN-1")
        msg = result["data"]["message"].lower()
        self.assertIn("consentement", msg)   # carve-out : preuve conservée
        self.assertIn("conserv", msg)
