"""Tests C1-CONCOURS — branche Prépa (DEC-197) : saisie note (Adm) → validation (Resp) → décision → mail notes.

Phase a : champs note + saisir_note_concours (Administratif, Prépa-only, ETU, garde de format JSON).
Style unitaire mocké, aligné suite existante.
"""

import json
import os
import types
from unittest import TestCase
from unittest.mock import MagicMock, patch
from admission.api.permissions import roles_at_or_above  # FIX-ROLES-HIERARCHIE : source unique de l'ordre

STAFF = "admission.api.staff"
NOTIF = "admission.api.notifications"


def _app(status="ETU", notes_validated=0):
    a = MagicMock()
    a.name = "CAN-2026-00001"
    a.status = status
    a.session = "SES-2026-10"
    a.notes_validated = notes_validated
    return a


def _patches():
    return (
        patch(f"{STAFF}._ok", side_effect=lambda d: {"ok": True, "data": d, "error": None}),
        patch(f"{STAFF}._error", side_effect=lambda c, m, s=400: {"ok": False, "data": None, "error": {"code": c}}),
    )


class TestPrepaDecisionMail(TestCase):
    def test_includes_notes_and_decision(self):
        with patch(f"{NOTIF}.frappe") as mf:
            from admission.api.notifications import send_prepa_decision_notification
            app = types.SimpleNamespace(name="CAN-1", applicant_name="Kossi", email="k@x.bj",
                                        notes_concours='{"maths": 14.0, "francais": 12.0}')
            send_prepa_decision_notification(app, "admis")
        mf.sendmail.assert_called_once()
        msg = mf.sendmail.call_args.kwargs["message"]
        self.assertIn("admis", msg)
        self.assertIn("maths", msg)
        self.assertIn("14", msg)  # notes incluses (DEC-197)

    def test_non_blocking(self):
        with patch(f"{NOTIF}.frappe") as mf:
            mf.sendmail.side_effect = RuntimeError("smtp down")
            from admission.api.notifications import send_prepa_decision_notification
            app = types.SimpleNamespace(name="CAN-1", applicant_name="K", email="k@x.bj", notes_concours="{}")
            send_prepa_decision_notification(app, "admis")  # ne doit PAS lever

    def test_skips_without_email(self):
        with patch(f"{NOTIF}.frappe") as mf:
            from admission.api.notifications import send_prepa_decision_notification
            app = types.SimpleNamespace(name="CAN-1", applicant_name="K", email=None, notes_concours="{}")
            send_prepa_decision_notification(app, "admis")
            mf.sendmail.assert_not_called()


class TestDecisionWiresPrepaMail(TestCase):
    def test_prepa_mark_admissible_sends_mail(self):
        app = _app("ETU", notes_validated=1)
        ok, err = _patches()
        with patch(f"{STAFF}.frappe") as mf, ok, err, patch(f"{STAFF}._is_prepa", return_value=True), \
             patch(f"{STAFF}.now_datetime", return_value="2026-06-11 10:00:00"), \
             patch(f"{STAFF}.send_prepa_decision_notification") as send:
            mf.db.exists.return_value = True
            mf.get_doc.return_value = app
            mf.session.user = "resp@lanem.bj"
            from admission.api.staff import mark_admissible
            mark_admissible(dossier_id="CAN-2026-00001")
        send.assert_called_once()
        self.assertEqual(send.call_args[0][1], "admis")  # libellé décision

    def test_licence_decision_no_prepa_mail(self):
        app = _app("ETU")
        ok, err = _patches()
        with patch(f"{STAFF}.frappe") as mf, ok, err, patch(f"{STAFF}._is_prepa", return_value=False), \
             patch(f"{STAFF}.now_datetime", return_value="2026-06-11 10:00:00"), \
             patch(f"{STAFF}.send_prepa_decision_notification") as send, \
             patch(f"{STAFF}.send_decision_notification") as gen:
            mf.db.exists.return_value = True
            mf.get_doc.return_value = app
            mf.session.user = "resp@lanem.bj"
            from admission.api.staff import mark_admissible
            mark_admissible(dossier_id="CAN-2026-00001")
        send.assert_not_called()  # Licence : pas de mail Prépa
        gen.assert_called_once()  # Licence : mail générique (C1-NOTIFS)


class TestDecisionNotesGarde(TestCase):
    """Garde « notes validées » sur les décisions — Prépa uniquement ; Licence JAMAIS bloqué."""

    def test_prepa_mark_admissible_blocked_without_validation(self):
        app = _app("ETU", notes_validated=0)  # Prépa, notes NON validées
        ok, err = _patches()
        with patch(f"{STAFF}.frappe") as mf, ok, err, patch(f"{STAFF}._is_prepa", return_value=True):
            mf.db.exists.return_value = True
            mf.get_doc.return_value = app
            from admission.api.staff import mark_admissible
            res = mark_admissible(dossier_id="CAN-2026-00001")
        self.assertEqual(res["error"]["code"], "NOTES_NOT_VALIDATED")
        app.save.assert_not_called()

    def test_prepa_mark_admissible_allowed_with_validation(self):
        app = _app("ETU", notes_validated=1)  # Prépa, notes validées
        ok, err = _patches()
        with patch(f"{STAFF}.frappe") as mf, ok, err, patch(f"{STAFF}._is_prepa", return_value=True), \
             patch(f"{STAFF}.now_datetime", return_value="2026-06-11 10:00:00"), \
             patch(f"{STAFF}.send_prepa_decision_notification"):
            mf.db.exists.return_value = True
            mf.get_doc.return_value = app
            mf.session.user = "resp@lanem.bj"
            from admission.api.staff import mark_admissible
            res = mark_admissible(dossier_id="CAN-2026-00001")
        self.assertTrue(res["ok"])
        self.assertEqual(app.status, "ADM")

    def test_prepa_refuse_blocked_without_validation(self):
        app = _app("ETU", notes_validated=0)
        ok, err = _patches()
        with patch(f"{STAFF}.frappe") as mf, ok, err, patch(f"{STAFF}._is_prepa", return_value=True):
            mf.db.exists.return_value = True
            mf.get_doc.return_value = app
            from admission.api.staff import refuse
            res = refuse(dossier_id="CAN-2026-00001", motif="Niveau insuffisant")
        self.assertEqual(res["error"]["code"], "NOTES_NOT_VALIDATED")

    def test_licence_decision_not_blocked(self):
        app = _app("ETU", notes_validated=0)  # Licence : pas de notes, NE DOIT PAS être bloqué
        ok, err = _patches()
        with patch(f"{STAFF}.frappe") as mf, ok, err, patch(f"{STAFF}._is_prepa", return_value=False), \
             patch(f"{STAFF}.now_datetime", return_value="2026-06-11 10:00:00"), \
             patch(f"{STAFF}.send_decision_notification"):
            mf.db.exists.return_value = True
            mf.get_doc.return_value = app
            mf.session.user = "resp@lanem.bj"
            from admission.api.staff import mark_admissible
            res = mark_admissible(dossier_id="CAN-2026-00001")
        self.assertTrue(res["ok"])
        self.assertEqual(app.status, "ADM")  # Licence inchangé (aucune garde note)


class TestValiderNotesConcours(TestCase):
    def test_responsable_validates(self):
        app = _app("ETU", notes_validated=0); app.notes_concours = '{"maths": 14.0}'
        ok, err = _patches()
        with patch(f"{STAFF}.frappe") as mf, ok, err, patch(f"{STAFF}._is_prepa", return_value=True), \
             patch(f"{STAFF}.now_datetime", return_value="2026-06-11 10:00:00"):
            mf.db.exists.return_value = True
            mf.get_doc.return_value = app
            mf.session.user = "resp@lanem.bj"
            from admission.api.staff import valider_notes_concours
            res = valider_notes_concours(dossier_id="CAN-2026-00001")
            mf.only_for.assert_called_once_with(("Admission Responsable", "System Manager"))
        self.assertEqual(app.notes_validated, 1)
        self.assertEqual(app.notes_validated_by, "resp@lanem.bj")  # séparation : validateur tracé
        self.assertEqual(app.notes_validated_date, "2026-06-11 10:00:00")
        app.save.assert_called_once()

    def test_not_prepa(self):
        app = _app("ETU"); app.notes_concours = '{"maths": 14.0}'
        ok, err = _patches()
        with patch(f"{STAFF}.frappe") as mf, ok, err, patch(f"{STAFF}._is_prepa", return_value=False):
            mf.db.exists.return_value = True
            mf.get_doc.return_value = app
            from admission.api.staff import valider_notes_concours
            res = valider_notes_concours(dossier_id="CAN-2026-00001")
        self.assertEqual(res["error"]["code"], "NOT_PREPA")

    def test_notes_missing(self):
        app = _app("ETU"); app.notes_concours = None  # rien saisi
        ok, err = _patches()
        with patch(f"{STAFF}.frappe") as mf, ok, err, patch(f"{STAFF}._is_prepa", return_value=True):
            mf.db.exists.return_value = True
            mf.get_doc.return_value = app
            from admission.api.staff import valider_notes_concours
            res = valider_notes_concours(dossier_id="CAN-2026-00001")
        self.assertEqual(res["error"]["code"], "NOTES_MISSING")
        app.save.assert_not_called()

    def test_idempotent_when_already_validated(self):
        app = _app("ETU", notes_validated=1); app.notes_concours = '{"maths": 14.0}'
        ok, err = _patches()
        with patch(f"{STAFF}.frappe") as mf, ok, err, patch(f"{STAFF}._is_prepa", return_value=True):
            mf.db.exists.return_value = True
            mf.get_doc.return_value = app
            from admission.api.staff import valider_notes_concours
            res = valider_notes_concours(dossier_id="CAN-2026-00001")
        self.assertTrue(res["data"]["idempotent"])
        app.save.assert_not_called()


class TestNotesFields(TestCase):
    def setUp(self):
        jf = os.path.join(os.path.dirname(__file__), "..", "admission", "doctype",
                          "admission_applicant", "admission_applicant.json")
        self.doc = json.load(open(jf))
        self.fields = {f["fieldname"]: f for f in self.doc["fields"]}

    def test_notes_concours_json(self):
        f = self.fields.get("notes_concours")
        self.assertIsNotNone(f); self.assertEqual(f["fieldtype"], "JSON")

    def test_notes_validated_check(self):
        f = self.fields.get("notes_validated")
        self.assertIsNotNone(f); self.assertEqual(f["fieldtype"], "Check")

    def test_notes_validated_by_link_user_readonly(self):
        f = self.fields.get("notes_validated_by")
        self.assertIsNotNone(f); self.assertEqual(f["fieldtype"], "Link")
        self.assertEqual(f["options"], "User"); self.assertEqual(f.get("read_only"), 1)

    def test_notes_validated_date_readonly(self):
        f = self.fields.get("notes_validated_date")
        self.assertIsNotNone(f); self.assertEqual(f["fieldtype"], "Datetime")
        self.assertEqual(f.get("read_only"), 1)

    def test_in_field_order(self):
        for fn in ("notes_concours", "notes_validated", "notes_validated_by", "notes_validated_date"):
            self.assertIn(fn, self.doc["field_order"])


class TestValidateNotesFormat(TestCase):
    def test_valid_dict(self):
        from admission.api.staff import _validate_notes_format
        parsed, err = _validate_notes_format({"maths": 14, "francais": 12.5})
        self.assertIsNone(err)
        self.assertEqual(parsed, {"maths": 14.0, "francais": 12.5})

    def test_json_string(self):
        from admission.api.staff import _validate_notes_format
        parsed, err = _validate_notes_format('{"maths": 14}')
        self.assertIsNone(err); self.assertEqual(parsed, {"maths": 14.0})

    def test_non_dict_rejected(self):
        from admission.api.staff import _validate_notes_format
        _, err = _validate_notes_format([14, 12])
        self.assertIsNotNone(err)

    def test_non_numeric_rejected(self):
        from admission.api.staff import _validate_notes_format
        _, err = _validate_notes_format({"maths": "abc"})
        self.assertIsNotNone(err)

    def test_empty_rejected(self):
        from admission.api.staff import _validate_notes_format
        _, err = _validate_notes_format({})
        self.assertIsNotNone(err)


class TestSaisirNoteConcours(TestCase):
    def test_administratif_prepa_etu(self):
        app = _app("ETU")
        ok, err = _patches()
        with patch(f"{STAFF}.frappe") as mf, ok, err, patch(f"{STAFF}._is_prepa", return_value=True):
            mf.db.exists.return_value = True
            mf.get_doc.return_value = app
            from admission.api.staff import saisir_note_concours
            res = saisir_note_concours(dossier_id="CAN-2026-00001", notes={"maths": 14, "francais": 12})
            mf.only_for.assert_called_once_with(roles_at_or_above("Admission Administratif"))
        self.assertTrue(res["ok"])
        self.assertEqual(json.loads(app.notes_concours), {"maths": 14.0, "francais": 12.0})
        self.assertEqual(app.notes_validated, 0)  # NON validées
        app.save.assert_called_once()

    def test_not_prepa_rejected(self):
        app = _app("ETU")
        ok, err = _patches()
        with patch(f"{STAFF}.frappe") as mf, ok, err, patch(f"{STAFF}._is_prepa", return_value=False):
            mf.db.exists.return_value = True
            mf.get_doc.return_value = app
            from admission.api.staff import saisir_note_concours
            res = saisir_note_concours(dossier_id="CAN-2026-00001", notes={"maths": 14})
        self.assertEqual(res["error"]["code"], "NOT_PREPA")
        app.save.assert_not_called()

    def test_invalid_state(self):
        app = _app("SOU")
        ok, err = _patches()
        with patch(f"{STAFF}.frappe") as mf, ok, err, patch(f"{STAFF}._is_prepa", return_value=True):
            mf.db.exists.return_value = True
            mf.get_doc.return_value = app
            from admission.api.staff import saisir_note_concours
            res = saisir_note_concours(dossier_id="CAN-2026-00001", notes={"maths": 14})
        self.assertEqual(res["error"]["code"], "INVALID_STATE")

    def test_format_guard(self):
        app = _app("ETU")
        ok, err = _patches()
        with patch(f"{STAFF}.frappe") as mf, ok, err, patch(f"{STAFF}._is_prepa", return_value=True):
            mf.db.exists.return_value = True
            mf.get_doc.return_value = app
            from admission.api.staff import saisir_note_concours
            res = saisir_note_concours(dossier_id="CAN-2026-00001", notes={"maths": "abc"})
        self.assertEqual(res["error"]["code"], "NOTES_FORMAT_INVALID")
        app.save.assert_not_called()

    def test_resaisie_resets_validation(self):
        app = _app("ETU", notes_validated=1)  # déjà validées
        ok, err = _patches()
        with patch(f"{STAFF}.frappe") as mf, ok, err, patch(f"{STAFF}._is_prepa", return_value=True):
            mf.db.exists.return_value = True
            mf.get_doc.return_value = app
            from admission.api.staff import saisir_note_concours
            saisir_note_concours(dossier_id="CAN-2026-00001", notes={"maths": 15})
        self.assertEqual(app.notes_validated, 0)            # ré-validation requise
        self.assertIsNone(app.notes_validated_by)
