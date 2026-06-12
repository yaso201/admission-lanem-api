"""Tests for notify_uf abandon signal — ADM-UF-2.

1. REF transition → notify_uf_applicant_abandon called
2. DES transition → notify_uf_applicant_abandon called
3. Non-abandon transition (SOP→SOU) → no notification
4. Same status → no notification
5. No person_id → notification skipped
6. UF config missing → notification skipped

Ref: ADM-UF-2, DEC-221, DEC-223.
"""

from __future__ import annotations

from unittest import TestCase
from unittest.mock import MagicMock, patch

NOTIFY = "admission.api.notify_uf"


def _mock_applicant(status="REF", old_status="ETU", person_id="PERS-00001"):
    doc = MagicMock()
    doc.name = "CAN-2026-00001"
    doc.status = status
    doc.person_id = person_id
    doc.first_name = "Koffi"
    doc.last_name = "Mensah"
    doc.email = "koffi@example.com"

    old_doc = MagicMock()
    old_doc.status = old_status
    doc.get_doc_before_save.return_value = old_doc
    return doc


class TestOnApplicantAbandon(TestCase):
    """Hook detection tests."""

    @patch(f"{NOTIFY}.notify_uf_applicant_abandon")
    @patch(f"{NOTIFY}.frappe")
    def test_ref_triggers_notification(self, mock_frappe, mock_notify):
        from admission.api.notify_uf import on_applicant_abandon

        mock_frappe.flags = MagicMock(_notifying_uf_abandon=False)
        doc = _mock_applicant(status="REF", old_status="ETU")

        on_applicant_abandon(doc, "on_update")

        mock_notify.assert_called_once_with(doc)

    @patch(f"{NOTIFY}.notify_uf_applicant_abandon")
    @patch(f"{NOTIFY}.frappe")
    def test_des_triggers_notification(self, mock_frappe, mock_notify):
        from admission.api.notify_uf import on_applicant_abandon

        mock_frappe.flags = MagicMock(_notifying_uf_abandon=False)
        doc = _mock_applicant(status="DES", old_status="SOU")

        on_applicant_abandon(doc, "on_update")

        mock_notify.assert_called_once_with(doc)

    @patch(f"{NOTIFY}.notify_uf_applicant_abandon")
    @patch(f"{NOTIFY}.frappe")
    def test_non_abandon_no_notification(self, mock_frappe, mock_notify):
        from admission.api.notify_uf import on_applicant_abandon

        mock_frappe.flags = MagicMock(_notifying_uf_abandon=False)
        doc = _mock_applicant(status="SOU", old_status="SOP")

        on_applicant_abandon(doc, "on_update")

        mock_notify.assert_not_called()

    @patch(f"{NOTIFY}.notify_uf_applicant_abandon")
    @patch(f"{NOTIFY}.frappe")
    def test_same_status_no_notification(self, mock_frappe, mock_notify):
        from admission.api.notify_uf import on_applicant_abandon

        mock_frappe.flags = MagicMock(_notifying_uf_abandon=False)
        doc = _mock_applicant(status="REF", old_status="REF")

        on_applicant_abandon(doc, "on_update")

        mock_notify.assert_not_called()

    @patch(f"{NOTIFY}.notify_uf_applicant_abandon")
    @patch(f"{NOTIFY}.frappe")
    def test_no_person_id_skips(self, mock_frappe, mock_notify):
        from admission.api.notify_uf import on_applicant_abandon

        mock_frappe.flags = MagicMock(_notifying_uf_abandon=False)
        doc = _mock_applicant(status="REF", old_status="ETU", person_id="")

        on_applicant_abandon(doc, "on_update")

        mock_notify.assert_not_called()


class TestNotifyUfApplicantAbandon(TestCase):
    """HTTP call tests."""

    @patch(f"{NOTIFY}.requests.post")
    @patch(f"{NOTIFY}._get_uf_config", return_value={"url": "https://backoffice:8000", "api_key": "k", "api_secret": "s"})
    @patch(f"{NOTIFY}.frappe")
    def test_posts_correct_payload(self, mock_frappe, mock_config, mock_post):
        from admission.api.notify_uf import notify_uf_applicant_abandon

        resp = MagicMock()
        resp.json.return_value = {"ok": True}
        resp.raise_for_status.return_value = None
        mock_post.return_value = resp

        applicant = _mock_applicant(status="REF")

        result = notify_uf_applicant_abandon(applicant)

        self.assertIsNotNone(result)
        call_kwargs = mock_post.call_args
        payload = call_kwargs.kwargs["json"]
        self.assertEqual(payload["person_id"], "PERS-00001")
        self.assertEqual(payload["applicant_id"], "CAN-2026-00001")
        self.assertEqual(payload["status"], "REF")
        self.assertEqual(payload["applicant_first_name"], "Koffi")
        self.assertEqual(payload["applicant_last_name"], "Mensah")

    @patch(f"{NOTIFY}._get_uf_config", return_value=None)
    @patch(f"{NOTIFY}.frappe")
    def test_no_config_returns_none(self, mock_frappe, mock_config):
        from admission.api.notify_uf import notify_uf_applicant_abandon

        applicant = _mock_applicant(status="DES")

        result = notify_uf_applicant_abandon(applicant)

        self.assertIsNone(result)
