"""Tests C1-ETUDE — cœur décision (mise en étude + décision Licence + acceptation).

Phase a : champs de décision (motif_refus, decided_by, decision_date, rang_liste_attente).
Phases b/c/d : endpoints staff role-gardés (start_review / mark_admissible / waitlist / refuse / accept_admission).
Style unitaire mocké, aligné suite existante.
"""

import json
import os
from unittest import TestCase
from unittest.mock import MagicMock, patch
from admission.api.permissions import roles_at_or_above  # FIX-ROLES-HIERARCHIE : source unique de l'ordre

STAFF = "admission.api.staff"


def _app(status="SOU"):
    a = MagicMock()
    a.name = "CAN-2026-00001"
    a.status = status
    a.validated_scholarships = None  # C2-BOURSES : accept lit les bourses validées pour le mail
    return a


def _patches():
    return (
        patch(f"{STAFF}._ok", side_effect=lambda d: {"ok": True, "data": d, "error": None}),
        patch(f"{STAFF}._error", side_effect=lambda c, m, s=400: {"ok": False, "data": None, "error": {"code": c}}),
    )


class TestStartReview(TestCase):
    def test_administratif_sou_to_etu(self):
        app = _app("SOU")
        ok, err = _patches()
        with patch(f"{STAFF}.frappe") as mf, ok, err:
            mf.db.exists.return_value = True
            mf.get_doc.return_value = app
            from admission.api.staff import start_review
            res = start_review(dossier_id="CAN-2026-00001")
            mf.only_for.assert_called_once_with(roles_at_or_above("Admission Administratif"))
        self.assertEqual(app.status, "ETU")
        app.save.assert_called_once()
        self.assertEqual(res["data"]["status"], "ETU")

    def test_invalid_state_rejected(self):
        app = _app("ETU")  # déjà en étude
        ok, err = _patches()
        with patch(f"{STAFF}.frappe") as mf, ok, err:
            mf.db.exists.return_value = True
            mf.get_doc.return_value = app
            from admission.api.staff import start_review
            res = start_review(dossier_id="CAN-2026-00001")
        self.assertEqual(res["error"]["code"], "INVALID_STATE")
        app.save.assert_not_called()

    def test_invalid_dossier(self):
        ok, err = _patches()
        with patch(f"{STAFF}.frappe") as mf, ok, err:
            mf.db.exists.return_value = False
            from admission.api.staff import start_review
            res = start_review(dossier_id="CAN-UNKNOWN")
        self.assertEqual(res["error"]["code"], "INVALID_DOSSIER")


class TestMarkAdmissible(TestCase):
    def _run(self, status, user="resp@lanem.bj"):
        app = _app(status)
        ok, err = _patches()
        with patch(f"{STAFF}.frappe") as mf, ok, err, \
             patch(f"{STAFF}.now_datetime", return_value="2026-06-11 10:00:00"), \
             patch(f"{STAFF}._is_prepa", return_value=False), \
             patch(f"{STAFF}.send_decision_notification"):  # Licence → mail générique (mocké)
            mf.db.exists.return_value = True
            mf.get_doc.return_value = app
            mf.session.user = user
            from admission.api.staff import mark_admissible
            res = mark_admissible(dossier_id="CAN-2026-00001")
            return app, res, mf

    def test_etu_to_adm_stamps_decision(self):
        app, res, mf = self._run("ETU")
        mf.only_for.assert_called_once_with(roles_at_or_above("Admission Responsable"))
        self.assertEqual(app.status, "ADM")
        self.assertEqual(app.decided_by, "resp@lanem.bj")
        self.assertEqual(app.decision_date, "2026-06-11 10:00:00")
        app.save.assert_called_once()

    def test_att_to_adm(self):
        app, res, mf = self._run("ATT")
        self.assertEqual(app.status, "ADM")

    def test_invalid_state(self):
        app, res, mf = self._run("SOU")
        self.assertEqual(res["error"]["code"], "INVALID_STATE")
        app.save.assert_not_called()


class TestWaitlist(TestCase):
    def test_etu_to_att_with_rang(self):
        app = _app("ETU")
        ok, err = _patches()
        with patch(f"{STAFF}.frappe") as mf, ok, err, patch(f"{STAFF}.now_datetime", return_value="2026-06-11 10:00:00"), \
             patch(f"{STAFF}._is_prepa", return_value=False), patch(f"{STAFF}.send_decision_notification"):
            mf.db.exists.return_value = True
            mf.get_doc.return_value = app
            mf.session.user = "resp@lanem.bj"
            from admission.api.staff import waitlist
            res = waitlist(dossier_id="CAN-2026-00001", rang=3)
            mf.only_for.assert_called_once_with(roles_at_or_above("Admission Responsable"))
        self.assertEqual(app.status, "ATT")
        self.assertEqual(app.rang_liste_attente, 3)
        self.assertEqual(app.decided_by, "resp@lanem.bj")

    def test_invalid_state(self):
        app = _app("ADM")
        ok, err = _patches()
        with patch(f"{STAFF}.frappe") as mf, ok, err:
            mf.db.exists.return_value = True
            mf.get_doc.return_value = app
            from admission.api.staff import waitlist
            res = waitlist(dossier_id="CAN-2026-00001")
        self.assertEqual(res["error"]["code"], "INVALID_STATE")


class TestRefuse(TestCase):
    def test_etu_to_ref_with_motif(self):
        app = _app("ETU")
        ok, err = _patches()
        with patch(f"{STAFF}.frappe") as mf, ok, err, patch(f"{STAFF}.now_datetime", return_value="2026-06-11 10:00:00"), \
             patch(f"{STAFF}._is_prepa", return_value=False), patch(f"{STAFF}.send_decision_notification"):
            mf.db.exists.return_value = True
            mf.get_doc.return_value = app
            mf.session.user = "resp@lanem.bj"
            from admission.api.staff import refuse
            res = refuse(dossier_id="CAN-2026-00001", motif="Niveau insuffisant")
            mf.only_for.assert_called_once_with(roles_at_or_above("Admission Responsable"))
        self.assertEqual(app.status, "REF")
        self.assertEqual(app.motif_refus, "Niveau insuffisant")
        self.assertEqual(app.decided_by, "resp@lanem.bj")
        app.save.assert_called_once()

    def test_motif_required(self):
        app = _app("ETU")
        ok, err = _patches()
        with patch(f"{STAFF}.frappe") as mf, ok, err:
            mf.db.exists.return_value = True
            mf.get_doc.return_value = app
            from admission.api.staff import refuse
            res = refuse(dossier_id="CAN-2026-00001", motif="   ")
        self.assertEqual(res["error"]["code"], "MOTIF_REQUIRED")  # PAS de REF sans motif
        app.save.assert_not_called()

    def test_invalid_state(self):
        app = _app("ACC")  # W2 : ADM est désormais refusable (Direction) — ACC ne l'est pas
        ok, err = _patches()
        with patch(f"{STAFF}.frappe") as mf, ok, err:
            mf.db.exists.return_value = True
            mf.get_doc.return_value = app
            from admission.api.staff import refuse
            res = refuse(dossier_id="CAN-2026-00001", motif="x")
        self.assertEqual(res["error"]["code"], "INVALID_STATE")

    def test_w2_direction_refuses_adm(self):
        # W2 (B0.2) : revenir sur une admissibilité = Direction (calé Workflow ADM→Refuse)
        app = _app("ADM")
        ok, err = _patches()
        with patch(f"{STAFF}.frappe") as mf, ok, err, \
             patch(f"{STAFF}._require_validated_notes_if_prepa", return_value=None), \
             patch(f"{STAFF}.send_decision_notification"):
            mf.db.exists.return_value = True
            mf.get_doc.return_value = app
            from admission.api.staff import refuse
            res = refuse(dossier_id="CAN-2026-00001", motif="Places épuisées")
        self.assertTrue(res["ok"])
        self.assertEqual(app.status, "REF")
        mf.only_for.assert_called_with(roles_at_or_above("Admission Direction"))


class TestAcceptAdmission(TestCase):
    def test_direction_adm_to_acc_via_save(self):
        app = _app("ADM")
        ok, err = _patches()
        with patch(f"{STAFF}.frappe") as mf, ok, err, patch(f"{STAFF}.send_decision_notification"):
            mf.db.exists.return_value = True
            mf.get_doc.return_value = app
            from admission.api.staff import accept_admission
            res = accept_admission(dossier_id="CAN-2026-00001")
            mf.only_for.assert_called_once_with(roles_at_or_above("Admission Direction"))
        self.assertEqual(app.status, "ACC")
        # IMPÉRATIF : via le contrôleur (save) → déclenche _on_accepted (frais 2) ; PAS de court-circuit
        app.save.assert_called_once_with(ignore_permissions=True)
        mf.db.set_value.assert_not_called()

    def test_invalid_state(self):
        app = _app("ETU")
        ok, err = _patches()
        with patch(f"{STAFF}.frappe") as mf, ok, err:
            mf.db.exists.return_value = True
            mf.get_doc.return_value = app
            from admission.api.staff import accept_admission
            res = accept_admission(dossier_id="CAN-2026-00001")
        self.assertEqual(res["error"]["code"], "INVALID_STATE")
        app.save.assert_not_called()


class TestDecisionFields(TestCase):
    def setUp(self):
        jf = os.path.join(os.path.dirname(__file__), "..", "admission", "doctype",
                          "admission_applicant", "admission_applicant.json")
        self.doc = json.load(open(jf))
        self.fields = {f["fieldname"]: f for f in self.doc["fields"]}

    def test_motif_refus(self):
        f = self.fields.get("motif_refus")
        self.assertIsNotNone(f, "motif_refus absent")
        self.assertIn(f["fieldtype"], ("Text", "Small Text", "Long Text"))

    def test_decided_by_link_user(self):
        f = self.fields.get("decided_by")
        self.assertIsNotNone(f, "decided_by absent")
        self.assertEqual(f["fieldtype"], "Link")
        self.assertEqual(f["options"], "User")

    def test_decision_date(self):
        f = self.fields.get("decision_date")
        self.assertIsNotNone(f, "decision_date absent")
        self.assertEqual(f["fieldtype"], "Datetime")

    def test_rang_liste_attente_int(self):
        f = self.fields.get("rang_liste_attente")
        self.assertIsNotNone(f, "rang_liste_attente absent")
        self.assertEqual(f["fieldtype"], "Int")

    def test_in_field_order(self):
        for fn in ("motif_refus", "decided_by", "decision_date", "rang_liste_attente"):
            self.assertIn(fn, self.doc["field_order"])
