"""Tests LOT W — cycle de vie complet (AUDIT-MANAGEMENT-BACK #2/#3/#5/#7, arbitrages B0.x).

W1 withdraw (Adm, 8 états Workflow, motif obligatoire, notif neutre, stamps) ·
W4 close_session (Direction, dry-run sans écriture, mapping REF/DES, motifs+log+notifs,
non-bloquant par dossier) · W5 set_waitlist_rank (Resp, ATT only, rang opposable) ·
W6 gate d'état valider_notes. Style unitaire mocké.
"""

from unittest import TestCase
from unittest.mock import MagicMock, patch
from admission.api.permissions import roles_at_or_above  # FIX-ROLES-HIERARCHIE : source unique de l'ordre

import frappe as _real_frappe


def setUpModule():
    try:
        _real_frappe.local.flags
    except Exception:
        _real_frappe.local.flags = _real_frappe._dict(in_test=True)


STAFF = "admission.api.staff"


def _patches():
    return (
        patch(f"{STAFF}._ok", side_effect=lambda d: {"ok": True, "data": d, "error": None}),
        patch(f"{STAFF}._error", side_effect=lambda c, m, s=400: {"ok": False, "data": None, "error": {"code": c}}),
    )


def _app(status):
    a = MagicMock()
    a.name = "CAN-2026-00001"
    a.status = status
    return a


class TestWithdraw(TestCase):
    def _run(self, status, motif="Demande du candidat (téléphone)"):
        ok, err = _patches()
        with patch(f"{STAFF}.frappe") as mf, ok, err, \
             patch(f"{STAFF}.send_withdrawal_notification") as mnotif:
            mf.db.exists.return_value = True
            app = _app(status)
            mf.get_doc.return_value = app
            from admission.api.staff import withdraw
            res = withdraw(dossier_id="CAN-2026-00001", motif=motif)
            return res, app, mf, mnotif

    def test_withdraw_from_sou_notifies_and_stamps(self):
        res, app, mf, mnotif = self._run("SOU")
        self.assertTrue(res["ok"])
        self.assertEqual(app.status, "DES")
        self.assertEqual(app.motif_desistement, "Demande du candidat (téléphone)")
        self.assertEqual(app.decided_by, mf.session.user)   # trace non falsifiable
        mnotif.assert_called_once()                          # candidat NOTIFIÉ (audit #2)
        app.save.assert_called_once_with(ignore_permissions=True)
        mf.only_for.assert_called_with(roles_at_or_above("Admission Administratif"))

    def test_withdraw_requires_motif(self):
        res, app, _, mnotif = self._run("SOU", motif="  ")
        self.assertEqual(res["error"]["code"], "MOTIF_REQUIRED")
        app.save.assert_not_called()
        mnotif.assert_not_called()

    def test_withdraw_blocked_from_ins(self):
        # INS = irréversible (étudiant créé) — hors WITHDRAW_STATES (calé Workflow)
        res, app, _, _ = self._run("INS")
        self.assertEqual(res["error"]["code"], "INVALID_STATE")
        app.save.assert_not_called()

    def test_withdraw_blocked_from_inc(self):
        # INC sort par resubmit candidat ou clôture de session (W4) — pas par withdraw (Workflow)
        res, app, _, _ = self._run("INC")
        self.assertEqual(res["error"]["code"], "INVALID_STATE")


class TestSetWaitlistRank(TestCase):
    def _run(self, status, rang):
        ok, err = _patches()
        with patch(f"{STAFF}.frappe") as mf, ok, err:
            mf.db.exists.return_value = True
            app = _app(status)
            mf.get_doc.return_value = app
            from admission.api.staff import set_waitlist_rank
            res = set_waitlist_rank(dossier_id="CAN-2026-00001", rang=rang)
            return res, app, mf

    def test_rank_set_on_att(self):
        res, app, mf = self._run("ATT", 3)
        self.assertTrue(res["ok"])
        self.assertEqual(app.rang_liste_attente, 3)
        mf.only_for.assert_called_with(roles_at_or_above("Admission Responsable"))

    def test_rank_cleared(self):
        res, app, _ = self._run("ATT", "")
        self.assertTrue(res["ok"])
        self.assertIsNone(app.rang_liste_attente)

    def test_rank_rejected_outside_att(self):
        res, app, _ = self._run("ETU", 1)
        self.assertEqual(res["error"]["code"], "INVALID_STATE")

    def test_rank_must_be_positive_int(self):
        res, app, _ = self._run("ATT", 0)
        self.assertEqual(res["error"]["code"], "RANG_INVALID")
        res, app, _ = self._run("ATT", "abc")
        self.assertEqual(res["error"]["code"], "RANG_INVALID")


class TestValiderNotesStateGate(TestCase):
    def test_w6_validation_blocked_outside_etu(self):
        ok, err = _patches()
        with patch(f"{STAFF}.frappe") as mf, ok, err, \
             patch(f"{STAFF}._is_prepa", return_value=True):
            mf.db.exists.return_value = True
            app = _app("ATT")
            app.notes_concours = '{"Maths": 12}'
            app.notes_validated = 0
            mf.get_doc.return_value = app
            from admission.api.staff import valider_notes_concours
            res = valider_notes_concours(dossier_id="CAN-2026-00001")
        self.assertEqual(res["error"]["code"], "INVALID_STATE")
        app.save.assert_not_called()


class TestCloseSession(TestCase):
    def _rows(self):
        import types
        return [types.SimpleNamespace(name=f"CAN-{i}", status=s)
                for i, s in enumerate(["BRO", "SOU", "ETU", "ATT", "ADM", "ACO", "INC", "ACC"], 1)]

    def test_dry_run_previews_without_writing(self):
        ok, err = _patches()
        with patch(f"{STAFF}.frappe") as mf, ok, err:
            mf.db.exists.return_value = True
            sess = MagicMock(); sess.is_open = 1; sess.label = "Octobre 2026"
            mf.get_doc.return_value = sess
            mf.get_all.return_value = self._rows()
            from admission.api.staff import close_session
            res = close_session(session="SES-1")  # dry_run=1 par DÉFAUT
        self.assertTrue(res["ok"])
        self.assertTrue(res["data"]["dry_run"])
        self.assertEqual(res["data"]["total"], 8)
        self.assertEqual(res["data"]["bascules"]["ETU→REF"], 1)
        self.assertEqual(res["data"]["bascules"]["INC→DES"], 1)
        mf.db.set_value.assert_not_called()      # PRÉVISUALISATION = zéro écriture

    @patch("admission.admission.doctype.admission_applicant.admission_applicant.write_transition_log")
    def test_execute_maps_notifies_and_logs(self, mlog):
        ok, err = _patches()
        with patch(f"{STAFF}.frappe") as mf, ok, err, \
             patch(f"{STAFF}.send_decision_notification") as mref, \
             patch(f"{STAFF}.send_withdrawal_notification") as mdes:
            mf.db.exists.return_value = True
            sess = MagicMock(); sess.is_open = 1; sess.label = "Octobre 2026"
            mf.get_doc.side_effect = lambda dt, n=None: sess if dt == "Admission Session" else MagicMock()
            mf.get_all.return_value = self._rows()
            from admission.api.staff import close_session
            res = close_session(session="SES-1", dry_run=0)
        self.assertTrue(res["ok"])
        # Mapping B0.3 : 5 instruits → REF ; 3 (BRO/INC/ACC) → DES
        self.assertEqual(res["data"]["refuses"], 5)
        self.assertEqual(res["data"]["desistes"], 3)
        self.assertEqual(res["data"]["echecs"], 0)
        self.assertEqual(mref.call_count, 5)     # mails décision motivée
        self.assertEqual(mdes.call_count, 3)     # mails clôture neutre
        self.assertEqual(mlog.call_count, 8)     # Transition Log manuel par dossier
        self.assertEqual(mlog.call_args.kwargs.get("action"), "Session Close")
        # Session fermée + motifs posés par dossier
        session_close = [c for c in mf.db.set_value.call_args_list
                         if c[0][0] == "Admission Session"]
        self.assertTrue(session_close)
        dossier_writes = [c for c in mf.db.set_value.call_args_list
                          if c[0][0] == "Admission Applicant"]
        self.assertEqual(len(dossier_writes), 8)
        sample = dossier_writes[0][0][2]
        self.assertIn("decided_by", sample)
        self.assertTrue(sample.get("motif_refus") or sample.get("motif_desistement"))
        mf.only_for.assert_called_with(roles_at_or_above("Admission Direction"))

    @patch("admission.admission.doctype.admission_applicant.admission_applicant.write_transition_log")
    def test_one_failure_does_not_stop_the_batch(self, mlog):
        ok, err = _patches()
        with patch(f"{STAFF}.frappe") as mf, ok, err, \
             patch(f"{STAFF}.send_decision_notification"), \
             patch(f"{STAFF}.send_withdrawal_notification"):
            mf.db.exists.return_value = True
            sess = MagicMock(); sess.is_open = 0; sess.label = "X"
            mf.get_doc.side_effect = lambda dt, n=None: sess if dt == "Admission Session" else MagicMock()
            mf.get_all.return_value = self._rows()[:3]
            calls = {"n": 0}
            def boom(*a, **k):
                calls["n"] += 1
                if calls["n"] == 2:
                    raise Exception("db down")
            mf.db.set_value.side_effect = boom
            from admission.api.staff import close_session
            res = close_session(session="SES-1", dry_run=0)
        self.assertEqual(res["data"]["echecs"], 1)
        self.assertEqual(res["data"]["refuses"] + res["data"]["desistes"], 2)
