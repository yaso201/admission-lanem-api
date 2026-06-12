"""Tests C1-COMPLETUDE — instruction de complétude (SOU↔INC).

Phase a : champ motif_incompletude + endpoint staff request_complement (role-gardé par état, PO-5).
Phase b : notification candidat (incomplétude). Phase c : re-soumission candidat (INC→SOU, PO-4).
Style unitaire mocké, aligné suite existante.
"""

import json
import os
import types
from unittest import TestCase
from unittest.mock import MagicMock, patch

STAFF = "admission.api.staff"


def _app(status="SOU", motif=None):
    a = MagicMock()
    a.name = "CAN-2026-00001"
    a.status = status
    a.motif_incompletude = motif
    return a


def _patches():
    return (
        patch(f"{STAFF}._ok", side_effect=lambda d: {"ok": True, **d}),
        patch(f"{STAFF}._error", side_effect=lambda c, m, s=400: {"ok": False, "code": c}),
    )


class TestRequestComplementRoleGuard(TestCase):
    def test_sou_guards_administratif(self):
        app = _app("SOU")
        ok, err = _patches()
        with patch(f"{STAFF}.frappe") as mf, ok, err, patch(f"{STAFF}.send_incompletude_notification"):
            mf.db.exists.return_value = True
            mf.get_doc.return_value = app
            from admission.api.staff import request_complement
            request_complement(dossier_id="CAN-2026-00001", motif="Relevé de notes manquant")
            mf.only_for.assert_called_once_with(("Admission Administratif", "System Manager"))
        self.assertEqual(app.status, "INC")
        self.assertEqual(app.motif_incompletude, "Relevé de notes manquant")
        app.save.assert_called_once()

    def test_etu_guards_responsable(self):
        app = _app("ETU")
        ok, err = _patches()
        with patch(f"{STAFF}.frappe") as mf, ok, err, patch(f"{STAFF}.send_incompletude_notification"):
            mf.db.exists.return_value = True
            mf.get_doc.return_value = app
            from admission.api.staff import request_complement
            request_complement(dossier_id="CAN-2026-00001", motif="Pièce illisible")
            mf.only_for.assert_called_once_with(("Admission Responsable", "System Manager"))
        self.assertEqual(app.status, "INC")


class TestRequestComplementGuards(TestCase):
    def test_invalid_state_rejected(self):
        app = _app("ADM")
        ok, err = _patches()
        with patch(f"{STAFF}.frappe") as mf, ok, err:
            mf.db.exists.return_value = True
            mf.get_doc.return_value = app
            from admission.api.staff import request_complement
            res = request_complement(dossier_id="CAN-2026-00001", motif="x")
            mf.only_for.assert_not_called()  # état invalide → refus avant tout guard rôle
        self.assertEqual(res["code"], "INVALID_STATE")

    def test_motif_required(self):
        app = _app("SOU")
        ok, err = _patches()
        with patch(f"{STAFF}.frappe") as mf, ok, err:
            mf.db.exists.return_value = True
            mf.get_doc.return_value = app
            from admission.api.staff import request_complement
            res = request_complement(dossier_id="CAN-2026-00001", motif="   ")
            self.assertEqual(res["code"], "MOTIF_REQUIRED")
        app.save.assert_not_called()

    def test_invalid_dossier(self):
        ok, err = _patches()
        with patch(f"{STAFF}.frappe") as mf, ok, err:
            mf.db.exists.return_value = False
            from admission.api.staff import request_complement
            res = request_complement(dossier_id="CAN-UNKNOWN", motif="x")
        self.assertEqual(res["code"], "INVALID_DOSSIER")


NOTIF = "admission.api.notifications"


class TestIncompletudeNotification(TestCase):
    def test_sends_email_with_motif(self):
        with patch(f"{NOTIF}.frappe") as mf:
            from admission.api.notifications import send_incompletude_notification
            app = types.SimpleNamespace(name="CAN-1", applicant_name="Kossi", email="k@x.bj")
            send_incompletude_notification(app, "Relevé de terminale manquant")
        mf.sendmail.assert_called_once()
        kw = mf.sendmail.call_args.kwargs
        self.assertEqual(kw["recipients"], ["k@x.bj"])
        self.assertIn("Relevé de terminale manquant", kw["message"])
        self.assertIn("CAN-1", kw["message"])

    def test_non_blocking_on_error(self):
        with patch(f"{NOTIF}.frappe") as mf:
            mf.sendmail.side_effect = RuntimeError("smtp down")
            from admission.api.notifications import send_incompletude_notification
            app = types.SimpleNamespace(name="CAN-1", applicant_name="K", email="k@x.bj")
            send_incompletude_notification(app, "x")  # ne doit PAS lever

    def test_skips_without_email(self):
        with patch(f"{NOTIF}.frappe") as mf:
            from admission.api.notifications import send_incompletude_notification
            app = types.SimpleNamespace(name="CAN-1", applicant_name="K", email=None)
            send_incompletude_notification(app, "x")
            mf.sendmail.assert_not_called()

    def test_motif_html_escaped(self):
        captured = {}
        with patch(f"{NOTIF}.frappe") as mf:
            mf.sendmail.side_effect = lambda **kw: captured.update(kw)
            from admission.api.notifications import send_incompletude_notification
            app = types.SimpleNamespace(name="CAN-1", applicant_name="K", email="k@x.bj")
            send_incompletude_notification(app, "<script>alert(1)</script>")
        self.assertNotIn("<script>", captured["message"])  # motif échappé (anti-injection email)


class TestRequestComplementNotifies(TestCase):
    def test_wires_notification(self):
        app = _app("SOU")
        ok, err = _patches()
        with patch(f"{STAFF}.frappe") as mf, ok, err, \
             patch(f"{STAFF}.send_incompletude_notification") as notify:
            mf.db.exists.return_value = True
            mf.get_doc.return_value = app
            from admission.api.staff import request_complement
            request_complement(dossier_id="CAN-2026-00001", motif="m")
        notify.assert_called_once()


PUBLIC = "admission.api.public"
APPLICANT_MOD = "admission.admission.doctype.admission_applicant.admission_applicant"


class TestResubmitComplement(TestCase):
    @patch(f"{PUBLIC}._record_candidate_transition")
    @patch(f"{PUBLIC}._require_otp_verified", return_value=None)
    @patch(f"{PUBLIC}._get_applicant")
    @patch(f"{PUBLIC}.frappe")
    def test_inc_to_sou_clears_motif(self, mf, mget, _motp, mrec):
        app = MagicMock(); app.name = "CAN-1"; app.status = "INC"; app.session = "SES-2026-LIC"
        mget.return_value = app
        mf.db.get_value.return_value = 1  # session ouverte
        mf.form_dict = {}
        from admission.api.public import resubmit_complement
        res = resubmit_complement(dossier_id="CAN-1", token="tok")
        self.assertTrue(res["ok"])
        self.assertEqual(res["data"]["status"], "SOU")
        # INC→SOU + motif effacé, en un seul set_value (bypass workflow, pas d'effet contrôleur sur INC/SOU)
        mf.db.set_value.assert_called_once()
        self.assertEqual(mf.db.set_value.call_args[0][2], {"status": "SOU", "motif_incompletude": None})
        mrec.assert_called_once_with("CAN-1", "INC", "SOU")  # Transition Log manuel

    @patch(f"{PUBLIC}._require_otp_verified", return_value=None)
    @patch(f"{PUBLIC}._get_applicant")
    @patch(f"{PUBLIC}.frappe")
    def test_invalid_state_rejected(self, mf, mget, _motp):
        app = MagicMock(); app.status = "ADM"; app.session = "SES-2026-LIC"
        mget.return_value = app; mf.form_dict = {}
        from admission.api.public import resubmit_complement
        res = resubmit_complement(dossier_id="CAN-1", token="tok")
        self.assertFalse(res["ok"]); self.assertEqual(res["error"]["code"], "INVALID_STATE")
        mf.db.set_value.assert_not_called()

    @patch(f"{PUBLIC}._require_otp_verified", return_value=None)
    @patch(f"{PUBLIC}._get_applicant")
    @patch(f"{PUBLIC}.frappe")
    def test_session_closed_rejected(self, mf, mget, _motp):
        app = MagicMock(); app.status = "INC"; app.session = "SES-2026-LIC"
        mget.return_value = app
        mf.db.get_value.return_value = 0  # session fermée
        mf.form_dict = {}
        from admission.api.public import resubmit_complement
        res = resubmit_complement(dossier_id="CAN-1", token="tok")
        self.assertEqual(res["error"]["code"], "SESSION_CLOSED")
        mf.db.set_value.assert_not_called()

    @patch(f"{PUBLIC}._get_applicant", side_effect=Exception("no token"))
    @patch(f"{PUBLIC}.frappe")
    def test_requires_valid_token(self, mf, _mget):
        mf.form_dict = {}
        from admission.api.public import resubmit_complement
        res = resubmit_complement(dossier_id="CAN-1")  # pas de token
        self.assertEqual(res["error"]["code"], "INVALID_DOSSIER")

    @patch(f"{PUBLIC}._require_otp_verified")
    @patch(f"{PUBLIC}._get_applicant")
    @patch(f"{PUBLIC}.frappe")
    def test_requires_otp(self, mf, mget, motp):
        app = MagicMock(); app.status = "INC"; mget.return_value = app; mf.form_dict = {}
        motp.return_value = {"ok": False, "error": {"code": "OTP_REQUIRED"}}  # OTP non vérifié
        from admission.api.public import resubmit_complement
        res = resubmit_complement(dossier_id="CAN-1", token="tok")
        self.assertEqual(res["error"]["code"], "OTP_REQUIRED")
        mf.db.set_value.assert_not_called()


class TestRecordCandidateTransition(TestCase):
    @patch(f"{APPLICANT_MOD}.write_transition_log")
    @patch(f"{APPLICANT_MOD}._detect_transition_context", return_value=("public_api", None))
    @patch(f"{PUBLIC}.frappe")
    def test_traces_inc_to_sou_as_guest(self, mf, _mdet, mwrite):
        mf.session.user = "Guest"
        from admission.api.public import _record_candidate_transition
        _record_candidate_transition("CAN-1", "INC", "SOU")
        args, kw = mwrite.call_args
        self.assertEqual(args, ("CAN-1", "INC", "SOU"))
        self.assertEqual(kw["actor"], "Guest")        # candidat
        self.assertEqual(kw["source"], "public_api")  # source fidèle


class TestMotifField(TestCase):
    def test_motif_incompletude_field_exists(self):
        jf = os.path.join(os.path.dirname(__file__), "..", "admission", "doctype",
                          "admission_applicant", "admission_applicant.json")
        doc = json.load(open(jf))
        field = next((f for f in doc["fields"] if f["fieldname"] == "motif_incompletude"), None)
        self.assertIsNotNone(field, "champ motif_incompletude absent")
        self.assertIn(field["fieldtype"], ("Text", "Small Text", "Long Text"))
        self.assertIn("motif_incompletude", doc["field_order"])
