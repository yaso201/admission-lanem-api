"""Tests SOCLE-0-AUDIT — Admission Applicant Transition Log (audit A03 §10.1/§10.2 + SLA, DEC-261).

Journal append-only des transitions de status, alimenté depuis le contrôleur (capture Workflow
natif ET changements directs). Couvre : déduction de source (4 cas), champs A03 §10.2,
idempotence (1 transition = 1 entrée), absence de PII, append-only (perms read-only).
Style unitaire mocké, aligné sur la suite existante.
"""

import json
import os
import types
from unittest import TestCase
from unittest.mock import MagicMock, patch

MOD = "admission.admission.doctype.admission_applicant.admission_applicant"


def _frappe(path="", form=None, user="agent@lanem.bj", request=True):
    mf = MagicMock()
    if request:
        mf.local.request.path = path
    else:
        mf.local.request = None
    mf.local.form_dict = form or {}
    mf.session.user = user
    return mf


class TestDetectSource(TestCase):
    """Source dérivée du contexte runtime — les 4 cas (best-effort, documenté)."""

    def test_source_workflow(self):
        mf = _frappe("/api/method/frappe.model.workflow.apply_workflow", {"action": "Accept Admission"})
        with patch(f"{MOD}.frappe", mf):
            from admission.admission.doctype.admission_applicant.admission_applicant import _detect_transition_context
            self.assertEqual(_detect_transition_context(), ("workflow", "Accept Admission"))

    def test_source_webhook(self):
        mf = _frappe("/api/method/admission.api.webhook.payment")
        with patch(f"{MOD}.frappe", mf):
            from admission.admission.doctype.admission_applicant.admission_applicant import _detect_transition_context
            self.assertEqual(_detect_transition_context(), ("webhook", None))

    def test_source_public_api(self):
        mf = _frappe("/api/method/admission.api.public.declare_payment_offline")
        with patch(f"{MOD}.frappe", mf):
            from admission.admission.doctype.admission_applicant.admission_applicant import _detect_transition_context
            self.assertEqual(_detect_transition_context(), ("public_api", None))

    def test_source_system_no_request(self):
        mf = _frappe(request=False)
        with patch(f"{MOD}.frappe", mf):
            from admission.admission.doctype.admission_applicant.admission_applicant import _detect_transition_context
            self.assertEqual(_detect_transition_context(), ("system", None))


class TestWriteTransitionLog(TestCase):
    """Champs A03 §10.2 + insertion code-only (ignore_permissions)."""

    def test_write_transition_log_fields(self):
        captured = {}
        inserted = MagicMock()
        mf = MagicMock()
        mf.get_doc.side_effect = lambda d: (captured.update(d), inserted)[1]
        with patch(f"{MOD}.frappe", mf), patch(f"{MOD}.now_datetime", return_value="2026-06-10 10:00:00"):
            from admission.admission.doctype.admission_applicant.admission_applicant import write_transition_log
            write_transition_log(
                "CAN-2026-00001", "BRO", "SOU",
                actor="agent@lanem.bj", source="webhook", action=None,
                context={"session": "SES-2026-LIC", "programme_code": "LIC", "level_code": "L1"},
            )
        self.assertEqual(captured["doctype"], "Admission Applicant Transition Log")
        self.assertEqual(captured["applicant"], "CAN-2026-00001")
        self.assertEqual(captured["from_status"], "BRO")     # objet/action
        self.assertEqual(captured["to_status"], "SOU")
        self.assertEqual(captured["actor"], "agent@lanem.bj")  # compte
        self.assertEqual(captured["source"], "webhook")        # contexte
        self.assertEqual(captured["result"], "ok")             # résultat
        self.assertEqual(captured["transition_at"], "2026-06-10 10:00:00")  # date
        ctx = json.loads(captured["context_snapshot"])
        self.assertEqual(set(ctx), {"session", "programme_code", "level_code"})
        inserted.insert.assert_called_once_with(ignore_permissions=True)


class TestRecordTransition(TestCase):
    """Alimentation depuis le contrôleur : 1 transition = 1 entrée (idempotence) + non-PII."""

    def _stub(self):
        return types.SimpleNamespace(
            name="CAN-2026-00009",
            session="SES-2026-LIC", programme_code="LIC", level_code="L1",
            flags=types.SimpleNamespace(status_changed_to="SOU", transition_from="BRO"),
        )

    def test_record_writes_once_and_idempotent(self):
        stub = self._stub()
        calls = []
        mf = MagicMock()
        mf.session.user = "agent@lanem.bj"
        with patch(f"{MOD}.frappe", mf), \
             patch(f"{MOD}._detect_transition_context", return_value=("workflow", "Start Review")), \
             patch(f"{MOD}.write_transition_log", side_effect=lambda *a, **k: calls.append((a, k))):
            from admission.admission.doctype.admission_applicant.admission_applicant import AdmissionApplicant
            AdmissionApplicant._record_transition(stub)
            AdmissionApplicant._record_transition(stub)  # 2e appel dans le même save → no-op
        self.assertEqual(len(calls), 1)
        args, kw = calls[0]
        self.assertEqual(args, ("CAN-2026-00009", "BRO", "SOU"))
        self.assertEqual(kw["actor"], "agent@lanem.bj")
        self.assertEqual(kw["source"], "workflow")
        self.assertEqual(kw["action"], "Start Review")
        self.assertEqual(set(kw["context"]), {"session", "programme_code", "level_code"})
        for forbidden in ("name", "email", "phone", "first_name", "last_name"):
            self.assertNotIn(forbidden, kw["context"])  # zéro PII
        self.assertTrue(stub.flags.transition_recorded)

    def test_record_noop_without_status_change(self):
        stub = types.SimpleNamespace(name="CAN-2026-00009", flags=types.SimpleNamespace())
        calls = []
        mf = MagicMock()
        with patch(f"{MOD}.frappe", mf), \
             patch(f"{MOD}.write_transition_log", side_effect=lambda *a, **k: calls.append(1)):
            from admission.admission.doctype.admission_applicant.admission_applicant import AdmissionApplicant
            AdmissionApplicant._record_transition(stub)
        self.assertEqual(calls, [])  # pas de status_changed_to → rien


class TestAppendOnlyPerms(TestCase):
    """Append-only : DocPerm read-only pour tous les rôles, aucun write/create/delete."""

    def test_perms_read_only(self):
        jf = os.path.join(
            os.path.dirname(__file__), "..", "admission", "doctype",
            "admission_applicant_transition_log", "admission_applicant_transition_log.json",
        )
        doc = json.load(open(jf))
        self.assertEqual(doc.get("track_changes", 0), 0)  # le journal n'est pas versionné
        allowed_roles = {"System Manager", "Admission Administratif", "Admission Responsable", "Admission Direction"}
        self.assertTrue(doc["permissions"])
        for p in doc["permissions"]:
            self.assertIn(p["role"], allowed_roles)
            self.assertTrue(p.get("read"))
            for right in ("write", "create", "delete", "submit", "cancel"):
                self.assertFalse(p.get(right), f"{p['role']} ne doit pas avoir {right} (append-only)")
