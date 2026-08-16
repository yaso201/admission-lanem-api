"""DEC-322/323 — naissance obligatoire et récupération multi-dossiers fail-closed."""

import json
import uuid
from unittest.mock import MagicMock, patch

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import get_datetime
from redis.exceptions import RedisError


PUB = "admission.api.public"


class TestDateOfBirthGate(FrappeTestCase):
    def setUp(self):
        frappe.local.response = {}

    @patch(f"{PUB}.now_datetime", return_value=get_datetime("2026-08-16 12:00:00"))
    def test_inclusive_age_bounds(self, _now):
        from admission.api.public import _validate_date_of_birth

        for value in ("2012-08-16", "1965-08-17"):
            born, error = _validate_date_of_birth(value)
            self.assertIsNotNone(born)
            self.assertIsNone(error)

    @patch(f"{PUB}.now_datetime", return_value=get_datetime("2026-08-16 12:00:00"))
    def test_missing_too_young_and_too_old_are_explicit(self, _now):
        from admission.api.public import _validate_date_of_birth

        _, missing = _validate_date_of_birth(None)
        _, young = _validate_date_of_birth("2012-08-17")
        _, old = _validate_date_of_birth("1965-08-16")
        self.assertEqual(missing["error"]["code"], "DOB_REQUIRED")
        self.assertEqual(young["error"]["code"], "DOB_INVALID")
        self.assertEqual(old["error"]["code"], "DOB_INVALID")

    @patch(f"{PUB}._resolve_person_from_campus")
    @patch("admission.api.legal._get_active_legal_document", return_value=MagicMock(name="LEGAL"))
    @patch(f"{PUB}._session_doc")
    @patch(f"{PUB}.frappe")
    def test_create_endpoint_blocks_missing_dob_before_campus(
        self, mock_frappe, mock_session, _legal, resolve_person,
    ):
        from admission.api.public import create_dossier

        session = MagicMock(programme_code="LIS", name="SES-001", is_open=1, closes_on=None)
        mock_session.return_value = session
        mock_frappe.local.response = {}
        mock_frappe.request = None
        mock_frappe.db.exists.return_value = True
        mock_frappe.db.get_value.return_value = "LIS"
        mock_frappe.form_dict = {
            "session": "SES-001", "level_code": "LIS-L1",
            "consent_data_processing": True, "consent_cgv": True,
            "identite": {"prenom": "Ama", "nom": "K", "email": "ama@x.bj",
                         "tel": "+22990112233"},
        }
        result = create_dossier()
        self.assertEqual(result["error"]["code"], "DOB_REQUIRED")
        resolve_person.assert_not_called()


class TestIdentityRecoveryContract(FrappeTestCase):
    def setUp(self):
        frappe.local.response = {}
        frappe.local.request_ip = "198.51.100.8"

    def test_redis_unavailable_fails_closed_on_all_access_points(self):
        from admission.api.public import get_recovered_dossier, recover_dossier, verify_recovery_otp

        with patch(f"{PUB}._identity_recovery_cache", side_effect=RedisError("down")):
            request = recover_dossier(email="ama@x.bj")
            verify = verify_recovery_otp(email="ama@x.bj", otp="123456")
            detail = get_recovered_dossier(recovery_token="opaque", dossier_id="CAN-1")
        for result in (request, verify, detail):
            self.assertEqual(result["error"]["code"], "IDENTITY_RECOVERY_UNAVAILABLE")
            self.assertEqual(result["error"]["message"], "Service temporairement indisponible, réessayez")

    @patch(f"{PUB}._check_identity_rate_limits", return_value=None)
    @patch(f"{PUB}._identity_recovery_cache", return_value=MagicMock())
    def test_known_and_unknown_requests_share_exact_response_and_job(self, _cache, _rate):
        from admission.api.public import recover_dossier

        with patch(f"{PUB}.frappe.enqueue") as enqueue:
            known = recover_dossier(email="ama@x.bj")
            unknown = recover_dossier(email="personne@x.bj")
        self.assertEqual(known, unknown)
        self.assertEqual(enqueue.call_count, 2)
        for call in enqueue.call_args_list:
            self.assertEqual(call.args[0], "admission.api.public.send_identity_recovery_otp")
            self.assertEqual(call.kwargs["queue"], "short")
            self.assertTrue(call.kwargs["enqueue_after_commit"])

    @patch("admission.api.notifications.send_email_otp")
    @patch(f"{PUB}._hash_otp", return_value="otp-hmac")
    @patch(f"{PUB}._generate_otp", return_value="123456")
    @patch(f"{PUB}._identity_recovery_key", side_effect=lambda cache, kind, value: kind)
    @patch(f"{PUB}._identity_recovery_cache")
    @patch(f"{PUB}._identity_recovery_names", return_value=["CAN-1", "CAN-2"])
    def test_worker_uses_existing_email_sender_without_dossier_token(
        self, _names, get_cache, _key, _otp, _hash, send_email,
    ):
        from admission.api.public import send_identity_recovery_otp

        cache = get_cache.return_value
        applicant = MagicMock(name="applicant")
        applicant.name = "CAN-1"
        with patch(f"{PUB}.frappe.get_doc", return_value=applicant):
            send_identity_recovery_otp("ama@x.bj")
        cache.setex.assert_called_once_with("otp", 600, "otp-hmac")
        cache.delete.assert_called_once_with("attempts")
        send_email.assert_called_once_with(applicant, "123456", minutes=10)

    @patch(f"{PUB}._generate_token", return_value="opaque-30m")
    @patch(f"{PUB}._identity_recovery_summaries")
    @patch(f"{PUB}._identity_recovery_names", return_value=["CAN-1", "CAN-2", "CAN-3"])
    @patch(f"{PUB}._hash_otp", return_value="submitted-hmac")
    @patch(f"{PUB}._identity_recovery_key", side_effect=lambda cache, kind, value: kind)
    @patch(f"{PUB}._check_identity_rate_limits", return_value=None)
    @patch(f"{PUB}._identity_recovery_cache")
    def test_valid_otp_returns_all_active_and_closed_and_stores_only_docnames(
        self, get_cache, _rate, _key, _hash, _names, summaries, _token,
    ):
        from admission.api.public import verify_recovery_otp

        cache = get_cache.return_value
        cache.eval.return_value = 1
        summaries.return_value = [
            {"dossier_id": "CAN-1", "statut": "SOU"},
            {"dossier_id": "CAN-2", "statut": "REF"},
            {"dossier_id": "CAN-3", "statut": "INS"},
        ]
        result = verify_recovery_otp(email="ama@x.bj", otp="123456")
        self.assertTrue(result["ok"])
        self.assertEqual(len(result["data"]["dossiers"]), 3)
        cache.setex.assert_called_once_with(
            "session", 1800, json.dumps(["CAN-1", "CAN-2", "CAN-3"]),
        )

    @patch(f"{PUB}._serialize_dossier", return_value={"dossier_id": "CAN-2"})
    @patch(f"{PUB}._identity_recovery_key", return_value="session")
    @patch(f"{PUB}._identity_recovery_cache")
    def test_consultation_is_allowlisted_read_only_and_never_rotates_tokens(
        self, get_cache, _key, _serialize,
    ):
        from admission.api.public import get_recovered_dossier

        cache = get_cache.return_value
        cache.get.return_value = b'["CAN-1", "CAN-2"]'
        applicant = MagicMock()
        with patch(f"{PUB}.frappe.db.exists", return_value=True), \
             patch(f"{PUB}.frappe.get_doc", return_value=applicant):
            result = get_recovered_dossier(recovery_token="opaque", dossier_id="CAN-2")
            refused = get_recovered_dossier(recovery_token="opaque", dossier_id="CAN-9")
        self.assertTrue(result["ok"])
        self.assertEqual(refused["error"]["code"], "RECOVERY_DOSSIER_FORBIDDEN")
        applicant.save.assert_not_called()
        applicant.db_set.assert_not_called()

    @patch(f"{PUB}._increment_identity_window", side_effect=[1, 21])
    @patch(f"{PUB}._request_ip", return_value="203.0.113.42")
    @patch(f"{PUB}.frappe.logger")
    def test_ip_limit_journal_contains_ip_timestamp_and_counter(self, logger, _ip, _increment):
        from admission.api.public import _check_identity_rate_limits

        result = _check_identity_rate_limits(MagicMock(), "ama@x.bj")
        self.assertEqual(result["error"]["code"], "RECOVERY_RATE_LIMITED")
        payload = json.loads(logger.return_value.warning.call_args.args[0])
        self.assertEqual(payload["ip"], "203.0.113.42")
        self.assertEqual(payload["counter"], 21)
        self.assertIn("at", payload)


class TestRecoveryOtpRedisAtomicity(FrappeTestCase):
    """Exécute réellement le script Lua sur le Redis du bench de test."""

    def setUp(self):
        suffix = uuid.uuid4().hex
        self.otp_key = frappe.cache.make_key(f"test:identity-recovery:otp:{suffix}")
        self.attempt_key = frappe.cache.make_key(f"test:identity-recovery:attempt:{suffix}")

    def tearDown(self):
        frappe.cache.delete(self.otp_key, self.attempt_key)

    def _run(self, submitted):
        from admission.api.public import _VERIFY_RECOVERY_OTP_LUA

        return int(frappe.cache.eval(
            _VERIFY_RECOVERY_OTP_LUA, 2, self.otp_key, self.attempt_key,
            submitted, 600, 5,
        ))

    def test_expired_success_consumed_and_replay_are_distinguished(self):
        frappe.cache.setex(self.otp_key, 600, "expired")
        frappe.cache.expire(self.otp_key, 0)
        self.assertEqual(self._run("expired"), -1)
        frappe.cache.setex(self.otp_key, 600, "correct")
        self.assertEqual(self._run("correct"), 1)
        self.assertEqual(self._run("correct"), -1)

    def test_fifth_wrong_attempt_invalidates_otp(self):
        frappe.cache.setex(self.otp_key, 600, "correct")
        self.assertEqual([self._run("wrong") for _ in range(5)], [0, 0, 0, 0, -2])
        self.assertEqual(self._run("correct"), -1)

    def test_email_rate_limit_triggers_on_fourth_request(self):
        from admission.api.public import (
            _check_identity_rate_limits,
            _identity_recovery_key,
        )

        email = f"rate-{uuid.uuid4().hex}@x.bj"
        ip = f"test-ip-{uuid.uuid4().hex}"
        frappe.local.request_ip = ip
        email_key = _identity_recovery_key(frappe.cache, "request-email", email)
        ip_key = _identity_recovery_key(frappe.cache, "request-ip", ip)
        try:
            results = [_check_identity_rate_limits(frappe.cache, email) for _ in range(4)]
            self.assertEqual(results[:3], [None, None, None])
            self.assertEqual(results[3]["error"]["code"], "RECOVERY_RATE_LIMITED")
        finally:
            frappe.cache.delete(email_key, ip_key)
