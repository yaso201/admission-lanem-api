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
                                        notes_concours='{"maths": 14.0, "physique": 13.0, "culture": 12.0}')
            send_prepa_decision_notification(app, "admis")
        mf.sendmail.assert_called_once()
        msg = mf.sendmail.call_args.kwargs["message"]
        self.assertIn("admis", msg)
        self.assertIn("Mathématiques", msg)  # libellé d'épreuve (source unique exam_grading)
        self.assertIn("14", msg)             # notes incluses (DEC-197)

    def test_absent_renders_absent_not_sentinel(self):  # Absent ≠ sentinelle brute / 0
        with patch(f"{NOTIF}.frappe") as mf:
            from admission.api.notifications import send_prepa_decision_notification
            app = types.SimpleNamespace(name="CAN-1", applicant_name="K", email="k@x.bj",
                                        notes_concours='{"__absent__": true}')
            send_prepa_decision_notification(app, "refuse")
        msg = mf.sendmail.call_args.kwargs["message"]
        self.assertIn("Absent", msg)
        self.assertNotIn("__absent__", msg)

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


class TestSaisirNoteConcours(TestCase):
    COEF = {"maths": 3.0, "physique": 2.0, "culture": 1.0}
    NOTES = {"maths": 14, "physique": 13, "culture": 12}

    def _ctx(self, is_prepa=True, coef=None):
        """Contexte : _is_prepa + coefficients de session posés (patchés) — la source unique
        exam_grading n'est PAS mockée (logique réelle plage/complétude)."""
        return (patch(f"{STAFF}._is_prepa", return_value=is_prepa),
                patch(f"{STAFF}._session_coefficients", return_value=(self.COEF if coef is None else coef)))

    def test_administratif_prepa_etu(self):
        app = _app("ETU")
        ok, err = _patches(); isp, coef = self._ctx()
        with patch(f"{STAFF}.frappe") as mf, ok, err, isp, coef:
            mf.db.exists.return_value = True
            mf.get_doc.return_value = app
            from admission.api.staff import saisir_note_concours
            res = saisir_note_concours(dossier_id="CAN-2026-00001", notes=self.NOTES)
            mf.only_for.assert_called_once_with(roles_at_or_above("Admission Administratif"))
        self.assertTrue(res["ok"])
        self.assertEqual(json.loads(app.notes_concours), {"maths": 14.0, "physique": 13.0, "culture": 12.0})
        self.assertEqual(app.notes_validated, 0)  # NON validées
        app.save.assert_called_once()

    def test_absent_accepted(self):  # GN8 — ABS ≠ 0
        app = _app("ETU")
        ok, err = _patches(); isp, coef = self._ctx()
        with patch(f"{STAFF}.frappe") as mf, ok, err, isp, coef:
            mf.db.exists.return_value = True
            mf.get_doc.return_value = app
            from admission.api.staff import saisir_note_concours
            res = saisir_note_concours(dossier_id="CAN-2026-00001", notes={"__absent__": True})
        self.assertTrue(res["ok"]); self.assertTrue(res["data"]["absent"])
        self.assertEqual(json.loads(app.notes_concours), {"__absent__": True})

    def test_out_of_range_refused(self):  # GN3 (preuve maîtresse, niveau endpoint)
        app = _app("ETU")
        ok, err = _patches(); isp, coef = self._ctx()
        with patch(f"{STAFF}.frappe") as mf, ok, err, isp, coef:
            mf.db.exists.return_value = True
            mf.get_doc.return_value = app
            from admission.api.staff import saisir_note_concours
            res = saisir_note_concours(dossier_id="CAN-2026-00001",
                                       notes={"maths": 25, "physique": 13, "culture": 12})
        self.assertEqual(res["error"]["code"], "NOTES_INVALID")
        app.save.assert_not_called()  # rien d'enregistré

    def test_incomplete_refused(self):  # arbitrage 2 : les 3 ou aucune
        app = _app("ETU")
        ok, err = _patches(); isp, coef = self._ctx()
        with patch(f"{STAFF}.frappe") as mf, ok, err, isp, coef:
            mf.db.exists.return_value = True
            mf.get_doc.return_value = app
            from admission.api.staff import saisir_note_concours
            res = saisir_note_concours(dossier_id="CAN-2026-00001", notes={"maths": 14, "physique": 13})
        self.assertEqual(res["error"]["code"], "NOTES_INVALID")
        app.save.assert_not_called()

    def test_coefficients_required_first(self):  # arbitrage 1
        app = _app("ETU")
        ok, err = _patches(); isp, coef = self._ctx(coef={})  # coefficients absents
        with patch(f"{STAFF}.frappe") as mf, ok, err, isp, coef:
            mf.db.exists.return_value = True
            mf.get_doc.return_value = app
            from admission.api.staff import saisir_note_concours
            res = saisir_note_concours(dossier_id="CAN-2026-00001", notes=self.NOTES)
        self.assertEqual(res["error"]["code"], "COEF_REQUIRED")
        app.save.assert_not_called()

    def test_not_prepa_rejected(self):
        app = _app("ETU")
        ok, err = _patches()
        with patch(f"{STAFF}.frappe") as mf, ok, err, patch(f"{STAFF}._is_prepa", return_value=False):
            mf.db.exists.return_value = True
            mf.get_doc.return_value = app
            from admission.api.staff import saisir_note_concours
            res = saisir_note_concours(dossier_id="CAN-2026-00001", notes=self.NOTES)
        self.assertEqual(res["error"]["code"], "NOT_PREPA")
        app.save.assert_not_called()

    def test_invalid_state(self):
        app = _app("SOU")
        ok, err = _patches()
        with patch(f"{STAFF}.frappe") as mf, ok, err, patch(f"{STAFF}._is_prepa", return_value=True):
            mf.db.exists.return_value = True
            mf.get_doc.return_value = app
            from admission.api.staff import saisir_note_concours
            res = saisir_note_concours(dossier_id="CAN-2026-00001", notes=self.NOTES)
        self.assertEqual(res["error"]["code"], "INVALID_STATE")

    def test_resaisie_resets_validation(self):
        app = _app("ETU", notes_validated=1)  # déjà validées
        ok, err = _patches(); isp, coef = self._ctx()
        with patch(f"{STAFF}.frappe") as mf, ok, err, isp, coef:
            mf.db.exists.return_value = True
            mf.get_doc.return_value = app
            from admission.api.staff import saisir_note_concours
            saisir_note_concours(dossier_id="CAN-2026-00001", notes={"maths": 15, "physique": 10, "culture": 8})
        self.assertEqual(app.notes_validated, 0)            # ré-validation requise
        self.assertIsNone(app.notes_validated_by)


class TestSetExamCoefficients(TestCase):
    def test_responsable_sets(self):  # GN2 + arbitrage 3 (RESP_UP, Administratif exclu)
        session = MagicMock(); session.exam_coefficients = None
        ok, err = _patches()
        with patch(f"{STAFF}.frappe") as mf, ok, err, patch(f"{STAFF}.coefficients_locked", return_value=False):
            mf.db.exists.return_value = True
            mf.get_doc.return_value = session
            from admission.api.staff import set_exam_coefficients
            res = set_exam_coefficients(session_id="SES-2026-10",
                                        coefficients={"maths": 3, "physique": 2, "culture": 1})
            mf.only_for.assert_called_once_with(roles_at_or_above("Admission Responsable"))
        self.assertTrue(res["ok"])
        self.assertEqual(json.loads(session.exam_coefficients), {"maths": 3.0, "physique": 2.0, "culture": 1.0})
        session.save.assert_called_once()

    def test_locked_after_first_note(self):  # GN2 — verrou dès la 1ʳᵉ note
        ok, err = _patches()
        with patch(f"{STAFF}.frappe") as mf, ok, err, patch(f"{STAFF}.coefficients_locked", return_value=True):
            mf.db.exists.return_value = True
            from admission.api.staff import set_exam_coefficients
            res = set_exam_coefficients(session_id="SES-2026-10",
                                        coefficients={"maths": 3, "physique": 2, "culture": 1})
        self.assertEqual(res["error"]["code"], "COEF_LOCKED")

    def test_incomplete_coefficients_refused(self):
        ok, err = _patches()
        with patch(f"{STAFF}.frappe") as mf, ok, err, patch(f"{STAFF}.coefficients_locked", return_value=False):
            mf.db.exists.return_value = True
            from admission.api.staff import set_exam_coefficients
            res = set_exam_coefficients(session_id="SES-2026-10", coefficients={"maths": 3, "physique": 2})
        self.assertEqual(res["error"]["code"], "COEF_INVALID")


class TestNotesMasse(TestCase):
    """GN5/GN6/GN7 — saisie en masse : rapprochement, aperçu 2 paniers, écriture partielle atomique.
    Teste _process_notes_rows directement (frappe isolé : roster + write patchés ; exam_grading réel)."""

    ROSTER = [
        {"dossier_id": "D1", "numero": "26260001", "nom": "Ada"},
        {"dossier_id": "D2", "numero": "26260002", "nom": "Kofi"},
        {"dossier_id": "D3", "numero": "26260003", "nom": "Zara"},
    ]

    def _index(self):
        return (self.ROSTER, {c["dossier_id"]: c for c in self.ROSTER},
                {c["numero"]: c for c in self.ROSTER})

    def _run(self, rows, write=False):
        from admission.api import staff
        with patch(f"{STAFF}._notes_roster_index", return_value=self._index()), \
             patch(f"{STAFF}._write_one_note", return_value=True) as w:
            res = staff._process_notes_rows("SES", rows, write=write)
        return res, w

    def test_match_by_dossier_id(self):
        res, _ = self._run([{"dossier_id": "D1", "maths": 14, "physique": 13, "culture": 12}])
        self.assertEqual(res["compte_a_ecrire"], 1)
        self.assertEqual(res["problemes"], [])
        self.assertEqual(res["a_ecrire"][0]["dossier_id"], "D1")

    def test_match_by_numero_when_no_id(self):
        res, _ = self._run([{"numero_convocation": "26260002", "maths": 10, "physique": 10, "culture": 10}])
        self.assertEqual(res["a_ecrire"][0]["dossier_id"], "D2")

    def test_never_matched_by_name(self):  # GN6 : nom seul → NON rapproché (jamais deviné)
        res, _ = self._run([{"nom": "Ada", "maths": 14, "physique": 13, "culture": 12}])
        self.assertEqual(res["compte_a_ecrire"], 0)
        self.assertIn("identifiant", res["problemes"][0]["probleme"])

    def test_unknown_id_is_problem(self):
        res, _ = self._run([{"dossier_id": "D9", "maths": 14, "physique": 13, "culture": 12}])
        self.assertEqual(res["compte_a_ecrire"], 0)
        self.assertIn("hors des convoqués", res["problemes"][0]["probleme"])

    def test_out_of_range_is_problem(self):  # GN3 en masse
        res, _ = self._run([{"dossier_id": "D1", "maths": 25, "physique": 13, "culture": 12}])
        self.assertEqual(res["compte_a_ecrire"], 0)
        self.assertEqual(len(res["problemes"]), 1)

    def test_incomplete_is_problem(self):  # arbitrage 2
        res, _ = self._run([{"dossier_id": "D1", "maths": 14, "physique": 13}])
        self.assertEqual(res["compte_a_ecrire"], 0)

    def test_absent_row(self):  # GN8
        res, _ = self._run([{"dossier_id": "D1", "absent": "1"}])
        self.assertEqual(res["compte_a_ecrire"], 1)
        self.assertTrue(res["a_ecrire"][0]["absent"])

    def test_empty_row_ignored(self):
        res, _ = self._run([{"dossier_id": "D1"}])  # ni note ni ABS
        self.assertEqual(res["compte_a_ecrire"], 0)
        self.assertEqual(res["problemes"], [])

    def test_duplicate_candidate_is_problem(self):
        res, _ = self._run([{"dossier_id": "D1", "maths": 14, "physique": 13, "culture": 12},
                            {"dossier_id": "D1", "maths": 10, "physique": 10, "culture": 10}])
        self.assertEqual(res["compte_a_ecrire"], 1)
        self.assertIn("double", res["problemes"][0]["probleme"])

    def test_partial_write(self):  # arbitrage 2 : valides passent, problèmes rapportés/non écrits
        res, w = self._run([{"dossier_id": "D1", "maths": 14, "physique": 13, "culture": 12},  # valide
                            {"dossier_id": "D2", "maths": 25, "physique": 13, "culture": 12},   # hors plage
                            {"dossier_id": "D3", "absent": "1"}],                                # ABS valide
                           write=True)
        self.assertEqual(res["ecrits"], 2)
        self.assertEqual(len(res["problemes"]), 1)
        self.assertEqual(w.call_count, 2)

    def test_preview_does_not_write(self):
        res, w = self._run([{"dossier_id": "D1", "maths": 14, "physique": 13, "culture": 12}], write=False)
        self.assertEqual(res["ecrits"], 0)
        w.assert_not_called()


class TestCsvParse(TestCase):
    def test_technical_headers(self):
        from admission.api.staff import _parse_csv
        rows = _parse_csv("dossier_id,maths,physique,culture,absent\nD1,14,13,12,\n")
        self.assertEqual(rows[0]["dossier_id"], "D1")
        self.assertEqual(rows[0]["maths"], "14")

    def test_label_headers_mapped(self):  # en-têtes libellés → clés canoniques
        from admission.api.staff import _parse_csv
        rows = _parse_csv("dossier_id,Mathématiques,Sciences physiques,Culture générale\nD1,14,13,12\n")
        self.assertEqual(rows[0]["maths"], "14")
        self.assertEqual(rows[0]["physique"], "13")
        self.assertEqual(rows[0]["culture"], "12")


class TestValiderNotesMasse(TestCase):
    """D3 option B — validation par lot : Responsable EXACT (Direction exclue), ne valide que le
    saisi-non-validé, éliminatoire compté et validé (jamais écarté)."""

    def _doc(self, name, nc, status="ETU", validated=0):
        d = MagicMock(); d.name = name; d.status = status; d.notes_validated = validated; d.notes_concours = nc
        return d

    def test_role_resp_exact(self):  # requirement 1
        ok, err = _patches()
        with patch(f"{STAFF}.frappe") as mf, ok, err, \
             patch(f"{STAFF}._pending_notes_of_session", return_value=[]), \
             patch(f"{STAFF}._session_coefficients", return_value={"maths": 1, "physique": 1, "culture": 1}):
            mf.db.exists.return_value = True
            from admission.api.staff import valider_notes_masse
            res = valider_notes_masse(session_id="SES")
            mf.only_for.assert_called_once_with(("Admission Responsable", "System Manager"))
        self.assertEqual(res["data"]["valides"], 0)

    def test_validates_pending_and_counts_signal(self):  # éliminatoire compté ET validé
        p1 = MagicMock(name="p1"); p1.name = "D1"; p1.notes_concours = '{"maths":18,"physique":17,"culture":5}'
        p2 = MagicMock(name="p2"); p2.name = "D2"; p2.notes_concours = '{"maths":12,"physique":12,"culture":12}'
        docs = {"D1": self._doc("D1", p1.notes_concours), "D2": self._doc("D2", p2.notes_concours)}
        ok, err = _patches()
        with patch(f"{STAFF}.frappe") as mf, ok, err, \
             patch(f"{STAFF}._pending_notes_of_session", return_value=[p1, p2]), \
             patch(f"{STAFF}._session_coefficients", return_value={"maths": 1, "physique": 1, "culture": 1}), \
             patch(f"{STAFF}.now_datetime", return_value="2026-08-14 10:00:00"):
            mf.db.exists.return_value = True
            mf.get_doc.side_effect = lambda dt, nm: docs[nm]
            mf.session.user = "resp@lanem.bj"
            from admission.api.staff import valider_notes_masse
            res = valider_notes_masse(session_id="SES")
        self.assertEqual(res["data"]["valides"], 2)
        self.assertEqual(res["data"]["avec_signal"], 1)          # D1 (culture=5) compté
        self.assertEqual(docs["D1"].notes_validated, 1)          # ET validé (jamais écarté)
        self.assertEqual(docs["D2"].notes_validated, 1)

    def test_skips_already_validated(self):  # requirement 3 (re-garde course)
        p1 = MagicMock(name="p1"); p1.name = "D1"; p1.notes_concours = '{"maths":12,"physique":12,"culture":12}'
        d = self._doc("D1", p1.notes_concours, validated=1)      # déjà validé entre-temps
        ok, err = _patches()
        with patch(f"{STAFF}.frappe") as mf, ok, err, \
             patch(f"{STAFF}._pending_notes_of_session", return_value=[p1]), \
             patch(f"{STAFF}._session_coefficients", return_value={"maths": 1, "physique": 1, "culture": 1}):
            mf.db.exists.return_value = True
            mf.get_doc.side_effect = lambda dt, nm: d
            from admission.api.staff import valider_notes_masse
            res = valider_notes_masse(session_id="SES")
        self.assertEqual(res["data"]["valides"], 0)
        d.save.assert_not_called()

    def test_preview_announces_counts(self):  # requirement 2
        p1 = MagicMock(name="p1"); p1.name = "D1"; p1.applicant_name = "A"; p1.notes_concours = '{"maths":18,"physique":17,"culture":5}'
        p2 = MagicMock(name="p2"); p2.name = "D2"; p2.applicant_name = "B"; p2.notes_concours = '{"maths":12,"physique":12,"culture":12}'
        ok, err = _patches()
        with patch(f"{STAFF}.frappe") as mf, ok, err, \
             patch(f"{STAFF}._pending_notes_of_session", return_value=[p1, p2]), \
             patch(f"{STAFF}._session_coefficients", return_value={"maths": 1, "physique": 1, "culture": 1}):
            mf.db.exists.return_value = True
            from admission.api.staff import valider_notes_masse_preview
            res = valider_notes_masse_preview(session_id="SES")
        self.assertEqual(res["data"]["a_valider"], 2)
        self.assertEqual(res["data"]["avec_signal"], 1)
