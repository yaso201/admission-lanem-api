"""Tests OBS-2 — helper log_event (log applicatif structuré + corrélation dossier_id).

Émet un JSON {step, status, dossier_id|ref, ...} via frappe.logger("admission"), non-PII,
non-bloquant (n'explose jamais). Complète l'Error Log OBS-1.

Ref: OBS-2.
"""

from __future__ import annotations

import json
from unittest import TestCase
from unittest.mock import MagicMock, patch

import frappe as _real_frappe


def setUpModule():
    try:
        _real_frappe.local.flags
    except Exception:
        _real_frappe.local.flags = _real_frappe._dict(in_test=True)


LOG = "admission.api._log"


class TestLogEvent(TestCase):
    @patch(f"{LOG}.frappe")
    def test_emits_structured_json_with_correlation(self, mock_frappe):
        logger = MagicMock()
        mock_frappe.logger.return_value = logger
        from admission.api._log import log_event
        log_event("create_dossier", "success", dossier_id="CAN-2026-00001", programme="L1")
        mock_frappe.logger.assert_called_with("admission")
        logger.info.assert_called_once()
        payload = json.loads(logger.info.call_args[0][0])
        self.assertEqual(payload["step"], "create_dossier")
        self.assertEqual(payload["status"], "success")
        self.assertEqual(payload["dossier_id"], "CAN-2026-00001")
        self.assertEqual(payload["programme"], "L1")

    @patch(f"{LOG}.frappe")
    def test_level_error_routes_to_error(self, mock_frappe):
        logger = MagicMock()
        mock_frappe.logger.return_value = logger
        from admission.api._log import log_event
        log_event("person_resolve", "no_person_id", level="error")
        logger.error.assert_called_once()
        logger.info.assert_not_called()

    @patch(f"{LOG}.frappe")
    def test_ref_used_when_no_dossier_id(self, mock_frappe):
        logger = MagicMock()
        mock_frappe.logger.return_value = logger
        from admission.api._log import log_event
        log_event("webhook_payment", "replay", ref="PROV-REF-1")
        payload = json.loads(logger.info.call_args[0][0])
        self.assertEqual(payload["ref"], "PROV-REF-1")
        self.assertNotIn("dossier_id", payload)

    @patch(f"{LOG}.frappe")
    def test_never_raises_on_logger_failure(self, mock_frappe):
        mock_frappe.logger.side_effect = RuntimeError("boom")
        from admission.api._log import log_event
        # Le logging ne doit JAMAIS interrompre le métier → pas d'exception propagée.
        log_event("create_dossier", "success", dossier_id="CAN-1")
