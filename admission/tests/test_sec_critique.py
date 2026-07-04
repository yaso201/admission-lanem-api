"""Tests SEC-CRITIQUE + SEC-TOKEN-EXPIRY — failles 🔴 corrigées + expiration glissante.

SEC-1 — Auth dossier (IDOR/bypass de token) :
 1. _get_applicant SANS token → lève (court-circuit supprimé)
 2. _get_applicant MAUVAIS token → lève
 3. _get_applicant BON token → renvoie le doc (flux légitime intact)
 4. get_dossier SANS token → 403 INVALID_DOSSIER (plus de PII)
 5. get_dossier BON token → 200 (flux légitime intact)
 6. Énumération : id deviné sans token → 403 uniforme (pas d'oracle)

SEC-2 — Webhook paiement (fail-open) :
 7. webhook secret ABSENT → REJET (plus de fail-open)
 8. webhook MAUVAISE signature → REJET
 9. webhook signature VALIDE → accepté (flux légitime)

developer_mode — divulgation OTP :
10. request_otp developer_mode SEUL → PAS de dev_otp (landmine neutralisée)
11. request_otp developer_mode + expose_dev_otp → dev_otp présent (opt-in explicite)

SEC-TOKEN-EXPIRY — expiration glissante 7 jours :
12. Token valide non expiré → renvoie le doc
13. Token expiré → DossierTokenExpired
14. get_dossier token expiré → 403 TOKEN_EXPIRED (distinct d'INVALID_DOSSIER)
15. check_expiry=False (renouvellement) → tolère un token expiré (hash vérifié, SEC-1 intact)
16. Glissant CONDITIONNEL : échéance lointaine → AUCUNE écriture (perf 3G)
17. Glissant CONDITIONNEL : échéance proche → prolongation (db_set token_expires_at)
18. verify_otp → régénère le token + repose l'échéance (renouvellement, check_expiry=False)
19. create_dossier → pose token_expires_at à l'émission

Ref: AUDIT-GLOBAL-SECU-SEO-META, SEC-CRITIQUE, SEC-TOKEN-EXPIRY.
"""

from __future__ import annotations

import hashlib
import hmac
from unittest import TestCase
from unittest.mock import MagicMock, patch

import frappe as _real_frappe
from frappe.utils import get_datetime


def setUpModule():
    try:
        _real_frappe.local.flags
    except Exception:
        _real_frappe.local.flags = _real_frappe._dict(in_test=True)


PUB = "admission.api.public"
WEBHOOK = "admission.api.webhook"

# now_datetime() réel nécessite une DB (timezone) → indisponible hors site : on le patche.
NOW = "2026-06-09 12:00:00"
FUTURE = "2099-01-01 00:00:00"   # non expiré, très loin → pas de glissement
PAST = "2020-01-01 00:00:00"     # expiré
FRESH = "2026-06-20 12:00:00"    # NOW + 11 j (> seuil 6 j) → pas d'écriture
APPROACHING = "2026-06-11 12:00:00"  # NOW + 2 j (< seuil 6 j) → écriture


def _sha(value):
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()


# ── SEC-1 : auth dossier ──────────────────────────────────────────────────────


class TestSec1DossierAuth(TestCase):
    @patch(f"{PUB}.frappe")
    def test_get_applicant_without_token_raises(self, mock_frappe):
        mock_frappe.throw.side_effect = Exception
        applicant = MagicMock()
        applicant.dossier_token_hash = _sha("goodtok")
        mock_frappe.get_doc.return_value = applicant
        from admission.api.public import _get_applicant
        with self.assertRaises(Exception):
            _get_applicant("CAN-2026-00001")  # NO token → doit lever

    @patch(f"{PUB}.frappe")
    def test_get_applicant_wrong_token_raises(self, mock_frappe):
        mock_frappe.throw.side_effect = Exception
        applicant = MagicMock()
        applicant.dossier_token_hash = _sha("goodtok")
        mock_frappe.get_doc.return_value = applicant
        from admission.api.public import _get_applicant
        with self.assertRaises(Exception):
            _get_applicant("CAN-2026-00001", "WRONGTOKEN")

    @patch(f"{PUB}.now_datetime", return_value=get_datetime(NOW))
    @patch(f"{PUB}.frappe")
    def test_get_applicant_correct_token_returns_doc(self, mock_frappe, _now):
        mock_frappe.throw.side_effect = Exception
        applicant = MagicMock()
        applicant.dossier_token_hash = _sha("goodtok")
        applicant.token_expires_at = FUTURE  # non expiré
        mock_frappe.get_doc.return_value = applicant
        from admission.api.public import _get_applicant
        result = _get_applicant("CAN-2026-00001", "goodtok")
        self.assertIs(result, applicant)

    @patch(f"{PUB}.frappe")
    def test_get_dossier_without_token_returns_403(self, mock_frappe):
        mock_frappe.throw.side_effect = Exception
        mock_frappe.local.response = {}
        mock_frappe.form_dict = {}
        mock_frappe.request = None
        applicant = MagicMock()
        applicant.dossier_token_hash = _sha("goodtok")
        mock_frappe.get_doc.return_value = applicant
        from admission.api.public import get_dossier
        result = get_dossier(dossier_id="CAN-2026-00001")  # NO token
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "INVALID_DOSSIER")
        self.assertEqual(mock_frappe.local.response["http_status_code"], 403)

    @patch(f"{PUB}.now_datetime", return_value=get_datetime(NOW))
    @patch(f"{PUB}._serialize_dossier", return_value={"dossier_id": "CAN-2026-00001"})
    @patch(f"{PUB}.frappe")
    def test_get_dossier_correct_token_returns_200(self, mock_frappe, _mock_serialize, _now):
        mock_frappe.throw.side_effect = Exception
        mock_frappe.local.response = {}
        mock_frappe.form_dict = {}
        mock_frappe.request = None
        applicant = MagicMock()
        applicant.dossier_token_hash = _sha("goodtok")
        applicant.token_expires_at = FUTURE  # non expiré
        mock_frappe.get_doc.return_value = applicant
        from admission.api.public import get_dossier
        result = get_dossier(dossier_id="CAN-2026-00001", token="goodtok")
        self.assertTrue(result["ok"])
        self.assertEqual(result["data"]["dossier_id"], "CAN-2026-00001")

    @patch(f"{PUB}.frappe")
    def test_enumeration_guessed_id_without_token_403(self, mock_frappe):
        # Même si l'id existe, sans token → 403 uniforme → aucun signal d'énumération.
        mock_frappe.throw.side_effect = Exception
        mock_frappe.local.response = {}
        mock_frappe.form_dict = {}
        mock_frappe.request = None
        applicant = MagicMock()
        applicant.dossier_token_hash = _sha("goodtok")
        mock_frappe.get_doc.return_value = applicant
        from admission.api.public import get_dossier
        for guessed in ("CAN-2026-00001", "CAN-2026-00002", "CAN-2026-09999"):
            result = get_dossier(dossier_id=guessed)
            self.assertFalse(result["ok"])
            self.assertEqual(result["error"]["code"], "INVALID_DOSSIER")


# ── developer_mode : divulgation OTP ──────────────────────────────────────────


class TestDevOtpLeak(TestCase):
    @patch(f"{PUB}.now_datetime", return_value=get_datetime(NOW))
    @patch(f"{PUB}._get_applicant")
    @patch(f"{PUB}.frappe")
    def test_request_otp_no_leak_with_developer_mode_only(self, mock_frappe, mock_get, _now):
        mock_frappe.conf = {"developer_mode": 1}  # PAS de expose_dev_otp
        mock_get.return_value = MagicMock()
        from admission.api.public import request_otp
        result = request_otp(dossier_id="CAN-2026-00001", token="tok")
        self.assertNotIn("dev_otp", result["data"])

    @patch(f"{PUB}.now_datetime", return_value=get_datetime(NOW))
    @patch(f"{PUB}._get_applicant")
    @patch(f"{PUB}.frappe")
    def test_request_otp_leak_requires_explicit_flag(self, mock_frappe, mock_get, _now):
        mock_frappe.conf = {"developer_mode": 1, "expose_dev_otp": 1}
        mock_get.return_value = MagicMock()
        from admission.api.public import request_otp
        result = request_otp(dossier_id="CAN-2026-00001", token="tok")
        self.assertIn("dev_otp", result["data"])


# ── SEC-2 : webhook paiement ──────────────────────────────────────────────────


class TestSec2Webhook(TestCase):
    """LOT KKIAPAY : auth = en-tête `x-kkiapay-secret` (constant-time, fail-closed) +
    re-vérification serveur — la signature HMAC maison (ADM-DEBT-74) est SUPPRIMÉE."""

    @staticmethod
    def _rq(mf, header, secret_conf="s3cr3t"):
        import json as _json
        mf.conf = {"admission_payment_webhook_secret": secret_conf} if secret_conf else {}
        mf.request.data = _json.dumps({"transactionId": "TX-1", "event": "transaction.success",
                                       "isPaymentSucces": True, "amount": 25000,
                                       "stateData": {"reference": "REF-1"}})
        mf.get_request_header.return_value = header

    @patch(f"{PUB}.frappe")
    @patch(f"{WEBHOOK}.frappe")
    def test_webhook_no_secret_rejects(self, mock_wh_frappe, mock_pub_frappe):
        self._rq(mock_wh_frappe, "anything", secret_conf=None)  # secret absent → fail-closed
        mock_pub_frappe.local.response = {}
        from admission.api.webhook import payment
        result = payment()
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "WEBHOOK_SIGNATURE_INVALID")

    @patch(f"{PUB}.frappe")
    @patch(f"{WEBHOOK}.frappe")
    def test_webhook_bad_signature_rejects(self, mock_wh_frappe, mock_pub_frappe):
        self._rq(mock_wh_frappe, "WRONG-SECRET")
        mock_pub_frappe.local.response = {}
        from admission.api.webhook import payment
        result = payment()
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "WEBHOOK_SIGNATURE_INVALID")

    @patch(f"{WEBHOOK}.notify_uf_payment")
    @patch(f"{WEBHOOK}.send_payment_receipt")
    @patch("admission.api.public._capture_promo_if_eligible")  # C2-BOURSES/R1 : capture dans la cascade
    @patch(f"{WEBHOOK}.verify_transaction", return_value={"status": "SUCCESS", "amount": 25000})
    @patch(f"{WEBHOOK}.now_datetime", return_value="2026-06-09 12:00:00")
    @patch(f"{WEBHOOK}._find_payment_by_reference")
    @patch(f"{WEBHOOK}.frappe")
    def test_webhook_valid_signature_accepted(
        self, mock_frappe, mock_find, _mock_now, _mock_verify, mock_capture, _msend, _mnotify,
    ):
        self._rq(mock_frappe, "s3cr3t")  # en-tête == secret dashboard → accepté
        pending = MagicMock()
        pending.payment_status = "Pending"
        pending.name = "REC-001"; pending.applicant = "CAN-001"
        pending.applicant_fee = "AFF-001"; pending.amount_xof = 25000
        mock_find.return_value = pending

        applicant = MagicMock(); applicant.status = "BRO"
        fee = MagicMock(); fee.name = "AFF-001"; fee.amount_xof = 25000
        fee.fee_type = "application"  # frais 1 → la cascade capture la promo (R1/DEC-228)
        mock_frappe.get_doc.side_effect = lambda dt, name=None: (
            applicant if dt == "Admission Applicant" else fee)
        mock_frappe.session.user = "Administrator"
        mock_frappe.db.get_value.return_value = "Pending"  # re-lecture du statut SOUS verrou (B2)
        mock_frappe.db.exists.return_value = False          # aucun autre Confirmed sur le fee (D-SEQ-FEE-02)

        from admission.api.webhook import payment
        result = payment()
        self.assertTrue(result["ok"])
        self.assertEqual(pending.payment_status, "Confirmed")
        mock_capture.assert_called_once_with(applicant)


# ── SEC-TOKEN-EXPIRY : expiration glissante 7 jours ───────────────────────────


class TestSec3TokenExpiry(TestCase):
    @patch(f"{PUB}.now_datetime", return_value=get_datetime(NOW))
    @patch(f"{PUB}.frappe")
    def test_valid_not_expired_returns_doc(self, mock_frappe, _now):
        mock_frappe.throw.side_effect = Exception
        applicant = MagicMock()
        applicant.dossier_token_hash = _sha("goodtok")
        applicant.token_expires_at = FUTURE
        mock_frappe.get_doc.return_value = applicant
        from admission.api.public import _get_applicant
        self.assertIs(_get_applicant("CAN-001", "goodtok"), applicant)

    @patch(f"{PUB}.now_datetime", return_value=get_datetime(NOW))
    @patch(f"{PUB}.frappe")
    def test_expired_raises_token_expired(self, mock_frappe, _now):
        mock_frappe.throw.side_effect = Exception
        applicant = MagicMock()
        applicant.dossier_token_hash = _sha("goodtok")
        applicant.token_expires_at = PAST
        mock_frappe.get_doc.return_value = applicant
        from admission.api.public import _get_applicant, DossierTokenExpired
        with self.assertRaises(DossierTokenExpired):
            _get_applicant("CAN-001", "goodtok")

    @patch(f"{PUB}.now_datetime", return_value=get_datetime(NOW))
    @patch(f"{PUB}.frappe")
    def test_get_dossier_expired_returns_403_token_expired(self, mock_frappe, _now):
        mock_frappe.throw.side_effect = Exception
        mock_frappe.local.response = {}
        mock_frappe.form_dict = {}
        mock_frappe.request = None
        applicant = MagicMock()
        applicant.dossier_token_hash = _sha("goodtok")
        applicant.token_expires_at = PAST
        mock_frappe.get_doc.return_value = applicant
        from admission.api.public import get_dossier
        result = get_dossier(dossier_id="CAN-001", token="goodtok")
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "TOKEN_EXPIRED")
        self.assertEqual(mock_frappe.local.response["http_status_code"], 403)

    @patch(f"{PUB}.frappe")
    def test_check_expiry_false_ignores_expiry(self, mock_frappe):
        # Renouvellement : token EXPIRÉ mais hash valide → autorisé (SEC-1 intact, expiry ignorée).
        # check_expiry=False → _enforce non appelé → now_datetime non requis.
        mock_frappe.throw.side_effect = Exception
        applicant = MagicMock()
        applicant.dossier_token_hash = _sha("goodtok")
        applicant.token_expires_at = PAST
        mock_frappe.get_doc.return_value = applicant
        from admission.api.public import _get_applicant
        self.assertIs(
            _get_applicant("CAN-001", "goodtok", check_expiry=False), applicant
        )

    @patch(f"{PUB}.now_datetime", return_value=get_datetime(NOW))
    @patch(f"{PUB}.frappe")
    def test_sliding_no_write_when_fresh(self, mock_frappe, _now):
        mock_frappe.throw.side_effect = Exception
        applicant = MagicMock()
        applicant.dossier_token_hash = _sha("goodtok")
        applicant.token_expires_at = FRESH  # échéance lointaine (> seuil)
        mock_frappe.get_doc.return_value = applicant
        from admission.api.public import _get_applicant
        _get_applicant("CAN-001", "goodtok")
        applicant.db_set.assert_not_called()  # pas d'écriture — perf 3G

    @patch(f"{PUB}.now_datetime", return_value=get_datetime(NOW))
    @patch(f"{PUB}.frappe")
    def test_sliding_writes_when_approaching(self, mock_frappe, _now):
        mock_frappe.throw.side_effect = Exception
        applicant = MagicMock()
        applicant.dossier_token_hash = _sha("goodtok")
        applicant.token_expires_at = APPROACHING  # échéance proche (< seuil)
        mock_frappe.get_doc.return_value = applicant
        from admission.api.public import _get_applicant
        _get_applicant("CAN-001", "goodtok")
        applicant.db_set.assert_called_once()  # prolongation glissante conditionnelle
        self.assertEqual(applicant.db_set.call_args[0][0], "token_expires_at")

    @patch(f"{PUB}.now_datetime", return_value=get_datetime(NOW))
    @patch(f"{PUB}._generate_token", return_value="NEWTOKEN")
    @patch(f"{PUB}._get_applicant")
    @patch(f"{PUB}.frappe")
    def test_verify_otp_regenerates_token_and_resets_expiry(
        self, mock_frappe, mock_get, _mock_gen, _now,
    ):
        mock_frappe.conf.get.return_value = "test-otp-secret"  # LOT-A2 : OTP en HMAC
        from admission.api.public import _hash_otp
        applicant = MagicMock()
        applicant.name = "CAN-001"
        applicant.otp_email_hash = _hash_otp("111111")
        applicant.otp_phone_hash = _hash_otp("222222")
        applicant.otp_expires_at = "2026-06-09 12:05:00"  # code OTP non expiré (NOW+5min)
        mock_get.return_value = applicant
        from admission.api.public import verify_otp
        result = verify_otp(
            dossier_id="CAN-001", token="oldtok", email_otp="111111", phone_otp="222222",
        )
        # Renouvellement : nouveau token renvoyé + hash + échéance repositionnés.
        self.assertEqual(result["data"]["token"], "NEWTOKEN")
        self.assertEqual(applicant.dossier_token_hash, _sha("NEWTOKEN"))
        self.assertIsNotNone(applicant.token_expires_at)
        # Le renouvellement doit tolérer un token expiré → check_expiry=False.
        mock_get.assert_called_once_with("CAN-001", "oldtok", check_expiry=False)

    @patch(f"{PUB}._ensure_fee")
    @patch(f"{PUB}._resolve_person_from_campus", return_value="PERS-00001")
    @patch(f"{PUB}._generate_token", return_value="tok123")
    @patch(f"{PUB}.now_datetime", return_value="2026-06-09 15:00:00")
    @patch("admission.api.legal._record_consent", return_value="CONS-001")
    @patch("admission.api.legal._get_active_legal_document")
    @patch(f"{PUB}._session_doc")
    @patch(f"{PUB}.frappe")
    def test_create_dossier_sets_token_expiry(
        self, mock_frappe, mock_session, mock_get_legal, mock_record,
        mock_now, mock_token, mock_person, mock_ensure_fee,
    ):
        from admission.api.public import create_dossier

        session = MagicMock()
        session.programme_code = "LIS"
        session.programme_label = "Licence"
        session.name = "SES-001"
        mock_session.return_value = session
        mock_frappe.db.exists.return_value = True
        mock_frappe.db.get_value.return_value = "LIS"
        mock_frappe.request = None
        mock_frappe.form_dict = {
            "session": "SES-001", "level_code": "LIS-L1",
            "consent_data_processing": True, "consent_cgv": True,
            "identite": {"prenom": "T", "nom": "U", "email": "t@t.com", "tel": "+22990112233"},
        }
        privacy = MagicMock(); privacy.name = "LEGAL-PRIV"
        cgv = MagicMock(); cgv.name = "LEGAL-CGV"
        mock_get_legal.side_effect = lambda dt: privacy if dt == "PRIVACY_POLICY" else cgv
        applicant_mock = MagicMock()
        applicant_mock.name = "CAN-2026-00001"
        applicant_mock.status = "BRO"
        applicant_mock.bac_date = None
        applicant_mock.pieces = []
        mock_frappe.get_doc.return_value = applicant_mock
        mock_frappe.get_all.return_value = []

        create_dossier()
        doc_dict = mock_frappe.get_doc.call_args[0][0]
        self.assertIn("token_expires_at", doc_dict)  # échéance posée à l'émission


# ── SEC-OTP : SEC-4 (enforce otp_verified) + cycle OTP (TTL, reset) ────────────


class TestSec4OtpEnforce(TestCase):
    @patch(f"{PUB}._get_applicant")
    @patch(f"{PUB}.frappe")
    def test_submit_payment_online_without_otp_403(self, mock_frappe, mock_get):
        mock_frappe.local.response = {}
        mock_frappe.form_dict = {}
        mock_frappe.request = None
        applicant = MagicMock()
        applicant.otp_verified = 0
        mock_get.return_value = applicant
        from admission.api.public import submit_payment_online
        result = submit_payment_online(dossier_id="CAN-001", token="tok", consent_refund=True)
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "OTP_REQUIRED")

    @patch(f"{PUB}._get_applicant")
    @patch(f"{PUB}.frappe")
    def test_declare_payment_offline_without_otp_403(self, mock_frappe, mock_get):
        mock_frappe.local.response = {}
        mock_frappe.form_dict = {}
        mock_frappe.request = None
        applicant = MagicMock()
        applicant.otp_verified = 0
        mock_get.return_value = applicant
        from admission.api.public import declare_payment_offline
        result = declare_payment_offline(dossier_id="CAN-001", token="tok", consent_refund=True)
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "OTP_REQUIRED")

    @patch(f"{PUB}._serialize_dossier", return_value={"dossier_id": "CAN-001"})
    @patch(f"{PUB}._get_applicant")
    @patch(f"{PUB}.frappe")
    def test_get_dossier_not_gated_on_otp(self, mock_frappe, mock_get, _ser):
        # Décision SEC-4 : get_dossier reste protégé par le TOKEN seul, PAS par l'OTP.
        mock_frappe.form_dict = {}
        mock_frappe.request = None
        applicant = MagicMock()
        applicant.otp_verified = 0  # non vérifié → doit quand même passer
        mock_get.return_value = applicant
        from admission.api.public import get_dossier
        result = get_dossier(dossier_id="CAN-001", token="tok")
        self.assertTrue(result["ok"])

    @patch(f"{PUB}._get_applicant")
    @patch(f"{PUB}.frappe")
    def test_classify_bac_not_gated_on_otp(self, mock_frappe, mock_get):
        # Décision SEC-4 : classify_bac n'est PAS gaté OTP.
        mock_frappe.local.response = {}
        mock_frappe.form_dict = {}
        mock_frappe.request = None
        applicant = MagicMock()
        applicant.otp_verified = 0
        mock_get.return_value = applicant
        from admission.api.public import classify_bac
        result = classify_bac(bac_date="2025-07-01", session=None, dossier_id="CAN-001", token="tok")
        self.assertTrue(result["ok"])
        self.assertIn("profil_bac", result["data"])

    @patch(f"{PUB}.now_datetime", return_value=get_datetime(NOW))
    @patch(f"{PUB}._get_applicant")
    @patch(f"{PUB}.frappe")
    def test_verify_otp_expired_code_refused(self, mock_frappe, mock_get, _now):
        mock_frappe.local.response = {}
        mock_frappe.form_dict = {}
        mock_frappe.request = None
        applicant = MagicMock()
        applicant.otp_email_hash = _sha("111111")
        applicant.otp_phone_hash = _sha("222222")
        applicant.otp_expires_at = PAST  # code OTP expiré (>10 min)
        mock_get.return_value = applicant
        from admission.api.public import verify_otp
        result = verify_otp(dossier_id="CAN-001", token="tok", email_otp="111111", phone_otp="222222")
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "OTP_EXPIRED")

    @patch(f"{PUB}.now_datetime", return_value=get_datetime(NOW))
    @patch(f"{PUB}._get_applicant")
    @patch(f"{PUB}.frappe")
    def test_request_otp_sets_expiry_and_resets_verified(self, mock_frappe, mock_get, _now):
        mock_frappe.conf = {}
        applicant = MagicMock()
        mock_get.return_value = applicant
        from admission.api.public import request_otp
        request_otp(dossier_id="CAN-001", token="tok")
        self.assertEqual(applicant.otp_verified, 0)  # re-vérif forcée
        self.assertIsNotNone(applicant.otp_expires_at)  # TTL OTP posé


# ── SEC-5 : validation des entrées (helpers centralisés) ──────────────────────


class TestSec5Helpers(TestCase):
    # _validate_amount
    @patch(f"{PUB}.frappe")
    def test_amount_valid(self, mock_frappe):
        from admission.api.public import _validate_amount
        x, err = _validate_amount(100000, 0, 500000)
        self.assertEqual(x, 100000.0)
        self.assertIsNone(err)

    @patch(f"{PUB}.frappe")
    def test_amount_negative_rejected(self, mock_frappe):
        mock_frappe.local.response = {}
        from admission.api.public import _validate_amount
        x, err = _validate_amount(-5000, 0, 500000)
        self.assertIsNone(x)
        self.assertEqual(err["error"]["code"], "AMOUNT_INVALID")

    @patch(f"{PUB}.frappe")
    def test_amount_above_max_rejected(self, mock_frappe):
        mock_frappe.local.response = {}
        from admission.api.public import _validate_amount
        x, err = _validate_amount(600000, 0, 500000)
        self.assertIsNone(x)
        self.assertEqual(err["error"]["code"], "AMOUNT_TOO_HIGH")

    @patch(f"{PUB}.frappe")
    def test_amount_non_numeric_rejected(self, mock_frappe):
        mock_frappe.local.response = {}
        from admission.api.public import _validate_amount
        x, err = _validate_amount("abc", 0, 500000)
        self.assertIsNone(x)
        self.assertEqual(err["error"]["code"], "AMOUNT_INVALID")

    # _validate_identity
    @patch(f"{PUB}.frappe")
    def test_identity_valid(self, mock_frappe):
        from admission.api.public import _validate_identity
        self.assertIsNone(
            _validate_identity("Jean", "Koudjo", "jean@example.com", "+22990112233", None)
        )

    @patch(f"{PUB}.frappe")
    def test_identity_bad_email(self, mock_frappe):
        mock_frappe.local.response = {}
        from admission.api.public import _validate_identity
        err = _validate_identity("Jean", "Koudjo", "pas-un-email", "+22990112233", None)
        self.assertEqual(err["error"]["code"], "EMAIL_INVALID")

    @patch(f"{PUB}.frappe")
    def test_identity_bad_phone(self, mock_frappe):
        mock_frappe.local.response = {}
        from admission.api.public import _validate_identity
        err = _validate_identity("Jean", "Koudjo", "jean@example.com", "abc", None)
        self.assertEqual(err["error"]["code"], "PHONE_INVALID")

    @patch(f"{PUB}.frappe")
    def test_identity_name_with_html_rejected(self, mock_frappe):
        mock_frappe.local.response = {}
        from admission.api.public import _validate_identity
        err = _validate_identity("<script>", "Koudjo", "jean@example.com", "+22990112233", None)
        self.assertEqual(err["error"]["code"], "IDENTITY_INVALID")

    # _validate_bac_date
    @patch(f"{PUB}.frappe")
    def test_bac_date_valid(self, mock_frappe):
        from admission.api.public import _validate_bac_date
        self.assertIsNone(_validate_bac_date("2024-07-01"))

    @patch(f"{PUB}.frappe")
    def test_bac_date_malformed(self, mock_frappe):
        mock_frappe.local.response = {}
        from admission.api.public import _validate_bac_date
        err = _validate_bac_date("pas-une-date")
        self.assertEqual(err["error"]["code"], "BAC_DATE_INVALID")

    @patch(f"{PUB}.frappe")
    def test_bac_date_out_of_range(self, mock_frappe):
        mock_frappe.local.response = {}
        from admission.api.public import _validate_bac_date
        err = _validate_bac_date("1850-01-01")
        self.assertEqual(err["error"]["code"], "BAC_DATE_INVALID")

    # _validate_piece_file
    @patch(f"{PUB}.frappe")
    def test_piece_file_not_found(self, mock_frappe):
        mock_frappe.local.response = {}
        mock_frappe.db.get_value.return_value = None
        applicant = MagicMock(); applicant.name = "CAN-001"
        from admission.api.public import _validate_piece_file
        docname, err = _validate_piece_file("https://evil.example/x", applicant)
        self.assertIsNone(docname)
        self.assertEqual(err["error"]["code"], "PIECE_FILE_INVALID")

    @patch(f"{PUB}.frappe")
    def test_piece_file_foreign_dossier_rejected(self, mock_frappe):
        mock_frappe.local.response = {}
        mock_frappe.db.get_value.return_value = {
            "name": "FILE-1", "file_name": "x.pdf", "file_size": 1000,
            "attached_to_doctype": "Admission Applicant", "attached_to_name": "CAN-999",
        }
        applicant = MagicMock(); applicant.name = "CAN-001"
        from admission.api.public import _validate_piece_file
        docname, err = _validate_piece_file("/private/files/x.pdf", applicant)
        self.assertIsNone(docname)
        self.assertEqual(err["error"]["code"], "PIECE_FILE_FORBIDDEN")

    @patch(f"{PUB}.frappe")
    def test_piece_file_bad_type_rejected(self, mock_frappe):
        mock_frappe.local.response = {}
        mock_frappe.db.get_value.return_value = {
            "name": "FILE-1", "file_name": "x.exe", "file_size": 1000,
            "attached_to_doctype": None, "attached_to_name": None,
        }
        applicant = MagicMock(); applicant.name = "CAN-001"
        from admission.api.public import _validate_piece_file
        docname, err = _validate_piece_file("/private/files/x.exe", applicant)
        self.assertIsNone(docname)
        self.assertEqual(err["error"]["code"], "PIECE_FILE_INVALID")

    @patch(f"{PUB}.frappe")
    def test_piece_file_legit_unattached_claims(self, mock_frappe):
        mock_frappe.local.response = {}
        mock_frappe.db.get_value.return_value = {
            "name": "FILE-1", "file_name": "diplome.pdf", "file_size": 1000,
            "attached_to_doctype": None, "attached_to_name": None,
        }
        applicant = MagicMock(); applicant.name = "CAN-001"
        from admission.api.public import _validate_piece_file
        docname, err = _validate_piece_file("/private/files/diplome.pdf", applicant)
        self.assertEqual(docname, "FILE-1")
        self.assertIsNone(err)
        mock_frappe.db.set_value.assert_called_once()  # revendication (attached_to)


class TestSec5Integration(TestCase):
    @patch(f"{PUB}._resolve_fee_from_catalog", return_value=500000)
    @patch(f"{PUB}._get_applicant")
    @patch(f"{PUB}.frappe")
    def test_submit_enrollment_negative_acompte_400(self, mock_frappe, mock_get, _fee):
        # 🔴 ferme le sous-paiement : acompte négatif → 400 AVANT total_provider.
        mock_frappe.local.response = {}
        mock_frappe.form_dict = {}
        mock_frappe.request = None
        applicant = MagicMock()
        applicant.otp_verified = 1
        applicant.status = "ACC"
        applicant.programme_code = "LIS"
        applicant.level_code = "LIS-L1"
        mock_get.return_value = applicant
        from admission.api.public import submit_enrollment_payment_online
        result = submit_enrollment_payment_online(
            dossier_id="CAN-001", token="tok", acompte_xof=-5000,
            consent_refund=True, consent_data_transfer=True,
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "AMOUNT_INVALID")

    @patch(f"{PUB}._resolve_person_from_campus", return_value="PERS-1")
    @patch(f"{PUB}._session_doc")
    @patch(f"{PUB}.frappe")
    def test_create_dossier_bad_email_no_campus_call(self, mock_frappe, mock_session, mock_campus):
        # email malformé → 400 AVANT l'appel campus (pas de 500, pas d'appel inutile).
        session = MagicMock()
        session.programme_code = "LIS"
        session.name = "SES-001"
        mock_session.return_value = session
        mock_frappe.local.response = {}
        mock_frappe.request = None
        mock_frappe.db.exists.return_value = True
        mock_frappe.db.get_value.return_value = "LIS"
        mock_frappe.form_dict = {
            "session": "SES-001", "level_code": "LIS-L1",
            "consent_data_processing": True, "consent_cgv": True,
            "identite": {"prenom": "Jean", "nom": "K", "email": "pas-un-email", "tel": "+22990112233"},
        }
        with patch("admission.api.legal._get_active_legal_document", return_value=MagicMock(name="LEGAL")):
            from admission.api.public import create_dossier
            result = create_dossier()
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "EMAIL_INVALID")
        mock_campus.assert_not_called()  # pré-validation AVANT campus
