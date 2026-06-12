"""Tests _resolve_person_from_campus — DEC-226 option A.

Tests the helper function directly (bypasses @frappe.whitelist on create_dossier).

1. Campus returns PERS-NNNNN → person_id returned
2. Same email → same PERS-NNNNN (idempotent, campus handles dedup)
3. Campus unreachable (ConnectionError) → None
4. Campus timeout → None
5. Campus returns empty response → None
6. Config missing → None
"""

from __future__ import annotations

from unittest import TestCase
from unittest.mock import MagicMock, patch

PUBLIC = "admission.api.public"


def _campus_response(person_id, created=True):
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"ok": True, "data": {"person_id": person_id, "created": created}}
    resp.raise_for_status.return_value = None
    return resp


class TestResolvePersonSuccess(TestCase):
    """Cases 1-2: campus returns person_id."""

    @patch(f"{PUBLIC}.requests.post")
    @patch(f"{PUBLIC}._get_campus_config", return_value={"url": "https://campus:8000", "token": "tok"})
    @patch(f"{PUBLIC}.frappe")
    def test_new_email_returns_person_id(self, mock_frappe, mock_config, mock_post):
        from admission.api.public import _resolve_person_from_campus

        mock_post.return_value = _campus_response("PERS-00042", created=True)

        result = _resolve_person_from_campus("koffi@example.com", "Koffi", "Mensah", "+229")

        self.assertEqual(result, "PERS-00042")
        mock_post.assert_called_once()
        call_kwargs = mock_post.call_args
        self.assertEqual(call_kwargs.kwargs["json"]["email"], "koffi@example.com")
        self.assertEqual(call_kwargs.kwargs["json"]["first_name"], "Koffi")
        self.assertEqual(call_kwargs.kwargs["json"]["last_name"], "Mensah")
        self.assertIn("X-API-Key", call_kwargs.kwargs["headers"])
        self.assertEqual(call_kwargs.kwargs["headers"]["X-API-Key"], "tok")

    @patch(f"{PUBLIC}.requests.post")
    @patch(f"{PUBLIC}._get_campus_config", return_value={"url": "https://campus:8000", "token": "tok"})
    @patch(f"{PUBLIC}.frappe")
    def test_existing_email_returns_same_id(self, mock_frappe, mock_config, mock_post):
        from admission.api.public import _resolve_person_from_campus

        mock_post.return_value = _campus_response("PERS-00010", created=False)

        result = _resolve_person_from_campus("koffi@example.com", "Koffi", "Mensah", "")

        self.assertEqual(result, "PERS-00010")


class TestResolvePersonFailure(TestCase):
    """Cases 3-6: campus unavailable or misconfigured → None (bloquant in create_dossier)."""

    @patch(f"{PUBLIC}.requests.post")
    @patch(f"{PUBLIC}._get_campus_config", return_value={"url": "https://campus:8000", "token": "tok"})
    @patch(f"{PUBLIC}.frappe")
    def test_connection_error_returns_none(self, mock_frappe, mock_config, mock_post):
        from admission.api.public import _resolve_person_from_campus
        import requests as real_requests

        mock_post.side_effect = real_requests.ConnectionError("Connection refused")

        result = _resolve_person_from_campus("koffi@example.com", "Koffi", "", "")

        self.assertIsNone(result)

    @patch(f"{PUBLIC}.requests.post")
    @patch(f"{PUBLIC}._get_campus_config", return_value={"url": "https://campus:8000", "token": "tok"})
    @patch(f"{PUBLIC}.frappe")
    def test_timeout_returns_none(self, mock_frappe, mock_config, mock_post):
        from admission.api.public import _resolve_person_from_campus
        import requests as real_requests

        mock_post.side_effect = real_requests.Timeout("timeout")

        result = _resolve_person_from_campus("koffi@example.com", "Koffi", "", "")

        self.assertIsNone(result)

    @patch(f"{PUBLIC}.requests.post")
    @patch(f"{PUBLIC}._get_campus_config", return_value={"url": "https://campus:8000", "token": "tok"})
    @patch(f"{PUBLIC}.frappe")
    def test_empty_response_returns_none(self, mock_frappe, mock_config, mock_post):
        from admission.api.public import _resolve_person_from_campus

        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"ok": True, "data": {}}
        resp.raise_for_status.return_value = None
        mock_post.return_value = resp

        result = _resolve_person_from_campus("koffi@example.com", "Koffi", "", "")

        self.assertIsNone(result)

    @patch(f"{PUBLIC}._get_campus_config", return_value=None)
    @patch(f"{PUBLIC}.frappe")
    def test_no_config_returns_none(self, mock_frappe, mock_config):
        from admission.api.public import _resolve_person_from_campus

        result = _resolve_person_from_campus("koffi@example.com", "Koffi", "", "")

        self.assertIsNone(result)


class TestResolvePersonPayload(TestCase):
    """Verify the HTTP request is correctly formatted."""

    @patch(f"{PUBLIC}.requests.post")
    @patch(f"{PUBLIC}._get_campus_config", return_value={"url": "https://campus:8000", "token": "secret-token"})
    @patch(f"{PUBLIC}.frappe")
    def test_correct_url_and_headers(self, mock_frappe, mock_config, mock_post):
        from admission.api.public import _resolve_person_from_campus, CAMPUS_ENSURE_PERSON_PATH

        mock_post.return_value = _campus_response("PERS-00001")

        _resolve_person_from_campus("a@b.com", "A", "B", "123")

        call_args = mock_post.call_args
        expected_url = "https://campus:8000" + CAMPUS_ENSURE_PERSON_PATH
        self.assertEqual(call_args.args[0], expected_url)
        self.assertEqual(call_args.kwargs["headers"]["X-API-Key"], "secret-token")
        self.assertEqual(call_args.kwargs["timeout"], 15)
