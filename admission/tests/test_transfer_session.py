"""TRANSFERT-SESSION — gardiens unitaires des décisions DEC-V à DEC-AB."""

from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import MagicMock, patch

import frappe as _real_frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_days, getdate, now_datetime, nowdate


STAFF = "admission.api.staff"


def setUpModule():
    try:
        _real_frappe.local.flags
    except Exception:
        _real_frappe.local.flags = _real_frappe._dict(in_test=True)


def _app(status="ETU", session="SRC"):
    app = MagicMock()
    app.name = "CAN-1"
    app.status = status
    app.session = session
    app.programme_code = "PRE"
    app.applicant_name = "Candidat Test"
    app.notes_concours = None
    app.notes_validated = 0
    return app


def trace_fixture_counts():
    """Commande de preuve post-traceur; ne modifie aucune donnée."""
    return {
        "applicants": _real_frappe.db.count("Admission Applicant"),
        "fees": _real_frappe.db.count("Applicant Fee"),
        "payments": _real_frappe.db.count("Applicant Fee Payment"),
        "zzxfer_applicants": _real_frappe.db.count(
            "Admission Applicant", {"applicant_name": ["like", "ZZXFER%"]}
        ),
        "transfer_logs": _real_frappe.db.count("Admission Applicant Transfer Log"),
    }


class TestCapacityWarning(TestCase):
    @patch(f"{STAFF}.frappe")
    def test_capacity_exceeded_warns_without_refusal(self, mock_frappe):
        mock_frappe.db.count.return_value = 81
        from admission.api.staff import _capacity_snapshot

        got = _capacity_snapshot(SimpleNamespace(name="DST", capacity=80), incoming=1)
        self.assertEqual(got["before"], 81)
        self.assertEqual(got["after"], 82)
        self.assertTrue(got["exceeded"])
        self.assertIn("82 / 80 places", got["warning"])


class TestStaffConvocationGuard(TestCase):
    @patch("admission.api.convocation._frais1_confirmed_payment")
    def test_available_requires_exam_open_session_and_confirmed_payment(self, payment):
        from admission.api.staff import _staff_convocation_available

        app = _app()
        payment.return_value = SimpleNamespace(name="PAY-1")
        open_exam = SimpleNamespace(exam_date="2026-09-20", lifecycle_state="Open", is_open=1)
        closed_exam = SimpleNamespace(exam_date="2026-09-20", lifecycle_state="Closed", is_open=0)
        open_without_exam = SimpleNamespace(exam_date=None, lifecycle_state="Open", is_open=1)

        self.assertTrue(_staff_convocation_available(app, open_exam))
        self.assertFalse(_staff_convocation_available(app, closed_exam))
        self.assertFalse(_staff_convocation_available(app, open_without_exam))
        payment.return_value = None
        self.assertFalse(_staff_convocation_available(app, open_exam))


class TestMoveAccountingInvariant(TestCase):
    @patch("admission.api.notify_uf.notify_uf_payment")
    @patch("admission.api.convocation.reissue_transfer_convocation", return_value=True)
    @patch(f"{STAFF}._record_transfer")
    @patch(f"{STAFF}.log_event")
    @patch(f"{STAFF}.frappe")
    def test_move_reuses_fee_and_never_notifies_uf(self, mock_frappe, _event, mock_log,
                                                   _reissue, mock_notify_uf):
        from admission.api.staff import _move_applicant_session

        app = _app()
        origin = SimpleNamespace(name="SRC", label="Origine")
        target = SimpleNamespace(name="DST", label="Cible", capacity=80)
        mock_frappe.get_doc.return_value = origin
        mock_frappe.db.count.return_value = 12
        mock_frappe.get_all.return_value = ["AFF-1"]
        mock_log.return_value = SimpleNamespace(name="LOG-1")

        got = _move_applicant_session(app, target, "voluntary")

        self.assertEqual(app.session, "DST")
        app.save.assert_called_once_with(ignore_permissions=True)
        mock_frappe.db.set_value.assert_called_once_with("Applicant Fee", "AFF-1", "session", "DST")
        self.assertEqual(got["fee_count"], 1)
        mock_notify_uf.assert_not_called()  # DEC-AB : session UF historique, jamais réécrite


class TestVoluntaryWindow(TestCase):
    def _run(self, today, voluntary_count=0):
        _real_frappe.local.response = {}
        app = _app(status="ETU")
        origin = SimpleNamespace(name="SRC", exam_date="2026-08-20")
        target = SimpleNamespace(name="DST")
        frappe_mock = MagicMock()
        frappe_mock.db.exists.return_value = True
        frappe_mock.db.count.return_value = voluntary_count
        frappe_mock.get_doc.side_effect = [app, origin]
        frappe_mock.local.response = {}
        with patch(f"{STAFF}.frappe", frappe_mock), \
             patch(f"{STAFF}.nowdate", return_value=today), \
             patch(f"{STAFF}._guard_write_scope", return_value=None), \
             patch(f"{STAFF}._is_prepa", return_value=True), \
             patch(f"{STAFF}._frais1_confirmed", return_value=True), \
             patch(f"{STAFF}._target_session_or_error", return_value=(target, None)), \
             patch(f"{STAFF}._move_applicant_session", return_value={"moved": True}) as move:
            from admission.api.staff import transfer_session
            result = transfer_session("CAN-1", "DST")
        return result, move

    def test_j_minus_2_accepted(self):
        result, move = self._run("2026-08-18")
        self.assertTrue(result["ok"])
        move.assert_called_once()

    def test_j_zero_refused(self):
        result, move = self._run("2026-08-20")
        self.assertEqual(result["error"]["code"], "VOLUNTARY_WINDOW_CLOSED")
        move.assert_not_called()

    def test_second_voluntary_transfer_refused(self):
        result, move = self._run("2026-08-18", voluntary_count=1)
        self.assertEqual(result["error"]["code"], "VOLUNTARY_QUOTA_USED")
        move.assert_not_called()


class TestTargetSelection(TestCase):
    def _run(self, exam_date):
        _real_frappe.local.response = {}
        app = _app()
        target = SimpleNamespace(
            name="DST", programme_code="PRE", exam_date=exam_date,
            lifecycle_state="Open", is_open=1, closes_on="2026-08-31",
        )
        frappe_mock = MagicMock()
        frappe_mock.db.exists.return_value = True
        frappe_mock.get_doc.return_value = target
        with patch(f"{STAFF}.frappe", frappe_mock), \
             patch(f"{STAFF}.nowdate", return_value="2026-08-16"), \
             patch("admission.api.sessions.is_session_selectable", return_value=True):
            from admission.api.staff import _target_session_or_error
            return _target_session_or_error(app, "DST")

    def test_past_exam_target_refused(self):
        target, err = self._run("2026-08-15")
        self.assertIsNone(target)
        self.assertEqual(err["error"]["code"], "TARGET_NOT_FUTURE")

    def test_future_exam_target_accepted(self):
        target, err = self._run("2026-08-20")
        self.assertEqual(target.name, "DST")
        self.assertIsNone(err)


class TestJustifiedAbsenceWindow(TestCase):
    def _run(self, today):
        _real_frappe.local.response = {}
        app = _app(status="ABS")
        origin = SimpleNamespace(name="SRC", exam_date="2026-08-10")
        target = SimpleNamespace(name="DST")
        frappe_mock = MagicMock()
        frappe_mock.db.exists.return_value = True
        frappe_mock.get_doc.side_effect = [app, origin]
        with patch(f"{STAFF}.frappe", frappe_mock), \
             patch(f"{STAFF}.nowdate", return_value=today), \
             patch(f"{STAFF}._guard_write_scope", return_value=None), \
             patch(f"{STAFF}._is_prepa", return_value=True), \
             patch(f"{STAFF}._frais1_confirmed", return_value=True), \
             patch(f"{STAFF}._validate_transfer_attachment", return_value=(None, None)), \
             patch(f"{STAFF}._target_session_or_error", return_value=(target, None)), \
             patch(f"{STAFF}._clear_absence_note_for_transfer") as clear_note, \
             patch(f"{STAFF}._move_applicant_session", return_value={"moved": True}) as move:
            from admission.api.staff import transfer_justified_absence
            result = transfer_justified_absence(
                "CAN-1", "DST", "maladie", "Empêchement médical documenté", None,
            )
        return result, clear_note, move

    def test_j_plus_3_reopens_without_mandatory_attachment(self):
        result, clear_note, move = self._run("2026-08-13")
        self.assertTrue(result["ok"])
        clear_note.assert_called_once()
        self.assertEqual(move.call_args.args[2], "justified_absence")
        self.assertEqual(move.call_args.kwargs["status_after"], "ETU")
        self.assertIsNone(move.call_args.kwargs["justificatif"])

    def test_j_plus_8_refused_and_abs_remains_definitive(self):
        result, clear_note, move = self._run("2026-08-18")
        self.assertEqual(result["error"]["code"], "ABSENCE_WINDOW_CLOSED")
        clear_note.assert_not_called()
        move.assert_not_called()


class TestInstitutionalBatch(TestCase):
    @patch(f"{STAFF}._capacity_snapshot", return_value={"before": 4, "after": 6})
    @patch(f"{STAFF}.log_event")
    @patch(f"{STAFF}._guard_write_scope", return_value=None)
    @patch(f"{STAFF}._move_applicant_session")
    @patch(f"{STAFF}._institutional_context")
    @patch(f"{STAFF}.frappe")
    def test_n_candidates_make_n_convocations_and_n_logs_without_voluntary_quota(
        self, mock_frappe, mock_context, mock_move, _scope, _event, _capacity,
    ):
        _real_frappe.local.response = {}
        source = SimpleNamespace(name="SRC")
        target = SimpleNamespace(name="DST")
        candidates = [_app(session="SRC"), _app(session="SRC")]
        candidates[0].name, candidates[1].name = "CAN-1", "CAN-2"
        mock_context.return_value = (source, target, candidates, None)
        mock_frappe.generate_hash.return_value = "BATCH-1"
        mock_move.side_effect = [
            {"convocation_reissued": True, "transfer_log": "LOG-1"},
            {"convocation_reissued": True, "transfer_log": "LOG-2"},
        ]

        from admission.api.staff import institutional_transfer
        result = institutional_transfer("SRC", "DST")

        self.assertEqual(result["data"]["transferred"], 2)
        self.assertEqual(result["data"]["convocations_reissued"], 2)
        self.assertEqual(result["data"]["logs"], 2)
        self.assertEqual(mock_move.call_count, 2)
        for call in mock_move.call_args_list:
            self.assertEqual(call.args[2], "institutional")
            self.assertEqual(call.kwargs["batch_ref"], "BATCH-1")


class TestAbsenceNoteJournal(TestCase):
    @patch(f"{STAFF}.now_datetime", return_value="2026-08-16 10:00:00")
    @patch(f"{STAFF}.frappe")
    def test_validated_absence_is_invalidated_then_cleared_with_two_logs(self, mock_frappe, _now):
        from admission.api.staff import _clear_absence_note_for_transfer

        app = _app(status="ABS")
        app.notes_concours = '{"__absent__": true}'
        app.notes_validated = 1
        docs = []

        def doc(payload):
            docs.append(payload)
            return MagicMock()

        mock_frappe.get_doc.side_effect = doc
        mock_frappe.session.user = "responsable@example.test"
        self.assertTrue(_clear_absence_note_for_transfer(app))
        self.assertEqual([d["champ"] for d in docs], ["validation", "absent"])
        self.assertTrue(all(d["origin"] == "transfert_absence" for d in docs))
        self.assertIsNone(app.notes_concours)
        self.assertEqual(app.notes_validated, 0)

    @patch(f"{STAFF}.now_datetime", return_value="2026-08-16 10:00:00")
    @patch(f"{STAFF}.frappe")
    def test_notes_absent_signal_never_changes_workflow_status(self, mock_frappe, _now):
        from admission.api.staff import _apply_notes

        app = _app(status="ETU")
        mock_frappe.session.user = "admin@example.test"
        _apply_notes(app, {"__absent__": True}, "unitaire")
        self.assertEqual(app.status, "ETU")  # INV-HUMAN : signal, jamais décision ABS


class TestSchema(TestCase):
    def test_abs_and_transfer_log_are_declared(self):
        import json
        from pathlib import Path

        root = Path(__file__).resolve().parents[1]
        applicant = json.loads((root / "admission/doctype/admission_applicant/admission_applicant.json").read_text())
        status = next(f for f in applicant["fields"] if f["fieldname"] == "status")
        self.assertIn("ABS", status["options"].splitlines())
        transfer = json.loads((root / "admission/doctype/admission_applicant_transfer_log/admission_applicant_transfer_log.json").read_text())
        self.assertEqual(transfer["name"], "Admission Applicant Transfer Log")
        self.assertEqual(transfer["in_create"], 0)


class TestTransferSessionE2E(FrappeTestCase):
    """Traceur DB réel : endpoints → dossier/frais/paiement/journaux → purge prouvée."""

    MARK = "ZZXFER"

    def _counts(self):
        return (
            _real_frappe.db.count("Admission Applicant"),
            _real_frappe.db.count("Applicant Fee"),
            _real_frappe.db.count("Applicant Fee Payment"),
        )

    def _purge(self):
        apps = _real_frappe.get_all(
            "Admission Applicant",
            filters={"applicant_name": ["like", f"{self.MARK}%"]},
            pluck="name",
        )
        if apps:
            for doctype in (
                "Admission Applicant Transfer Log",
                "Admission Note Change Log",
                "Admission Applicant Transition Log",
                "Applicant Fee Payment",
                "Applicant Fee",
            ):
                field = "applicant" if doctype != "Admission Applicant Transfer Log" else "applicant"
                _real_frappe.db.delete(doctype, {field: ["in", apps]})
            _real_frappe.db.delete("Admission Applicant", {"name": ["in", apps]})
        _real_frappe.db.delete(
            "Admission Session", {"session_code": ["like", f"{self.MARK}%"]}
        )
        _real_frappe.db.commit()

    def setUp(self):
        _real_frappe.set_user("Administrator")
        self._purge()
        self.baseline = self._counts()

    def tearDown(self):
        _real_frappe.db.rollback()
        self._purge()

    def _session(self, suffix, exam_offset, capacity=80):
        today = getdate(nowdate())
        return _real_frappe.get_doc({
            "doctype": "Admission Session",
            "session_code": f"{self.MARK}-{suffix}",
            "label": f"{self.MARK} {suffix}",
            "programme_code": "PREPA",
            "programme_label": "Cycle Préparatoire",
            "academic_year": "2026-2027",
            "opens_on": add_days(today, -30),
            "closes_on": add_days(today, exam_offset - 1),
            "bac_results_date": add_days(today, -15),
            "application_fee_xof": 10000,
            "capacity": capacity,
            "lifecycle_state": "Open",
            "is_open": 1,
            "is_prepa_session": 1,
            "exam_date": add_days(today, exam_offset),
            "exam_call_time": "07:30:00",
            "exam_start_time": "08:00:00",
            "exam_room": "Salle trace",
        }).insert(ignore_permissions=True, ignore_mandatory=True)

    def _paid_applicant(self, session, suffix):
        app = _real_frappe.get_doc({
            "doctype": "Admission Applicant",
            "applicant_name": f"{self.MARK} {suffix}",
            "first_name": self.MARK,
            "last_name": suffix,
            "email": f"{self.MARK.lower()}-{suffix.lower()}@example.test",
            "phone": "+2290154545054",
            "programme_code": "PREPA",
            "level_code": "PREPA",
            "session": session.name,
            "status": "BRO",
        }).insert(ignore_permissions=True, ignore_mandatory=True)
        _real_frappe.db.set_value(
            "Admission Applicant", app.name,
            {"status": "ETU", "convocation_number": f"TRACE-{suffix}"},
            update_modified=False,
        )
        app.reload()
        fee = _real_frappe.get_doc({
            "doctype": "Applicant Fee",
            "applicant": app.name,
            "session": session.name,
            "fee_type": "application",
            "amount_xof": 10000,
            "status": "Paid",
        }).insert(ignore_permissions=True, ignore_mandatory=True)
        payment = _real_frappe.get_doc({
            "doctype": "Applicant Fee Payment",
            "applicant_fee": fee.name,
            "applicant": app.name,
            "payment_mode": "Online",
            "source": "online",
            "amount_xof": 10000,
            "payment_status": "Confirmed",
            "paid_at": now_datetime(),
            "uf_notified": 1,
        }).insert(ignore_permissions=True, ignore_mandatory=True)
        return app, fee, payment

    def _assert_purged_to_baseline(self):
        self._purge()
        self.assertEqual(self._counts(), self.baseline)
        self.assertEqual(
            _real_frappe.db.count(
                "Admission Applicant", {"applicant_name": ["like", f"{self.MARK}%"]}
            ),
            0,
        )

    @patch("admission.api.notify_uf.notify_uf_payment")
    @patch("admission.api.convocation.reissue_transfer_convocation", return_value=True)
    def test_individual_then_second_refused_fee_and_uf_unchanged(self, _reissue, notify_uf):
        from admission.api.staff import transfer_session

        source = self._session("VOL-SRC", 2)
        target = self._session("VOL-DST", 30, capacity=1)
        self._paid_applicant(target, "VOL-OCCUPANT")  # cible déjà pleine : DEC-AA reste non bloquant
        app, fee, payment = self._paid_applicant(source, "VOL")

        first = transfer_session(app.name, target.name)
        second = transfer_session(app.name, source.name)
        _real_frappe.db.commit()

        self.assertTrue(first["ok"])
        self.assertTrue(first["data"]["capacity"]["exceeded"])
        self.assertIn("2 / 1 places", first["data"]["capacity"]["warning"])
        self.assertEqual(second["error"]["code"], "VOLUNTARY_QUOTA_USED")
        self.assertEqual(_real_frappe.db.get_value("Admission Applicant", app.name, "session"), target.name)
        self.assertEqual(_real_frappe.db.get_value("Applicant Fee", fee.name, "session"), target.name)
        self.assertEqual(_real_frappe.db.count("Applicant Fee", {"applicant": app.name}), 1)
        self.assertEqual(_real_frappe.db.get_value("Applicant Fee Payment", payment.name, "applicant_fee"), fee.name)
        self.assertEqual(_real_frappe.db.get_value("Applicant Fee Payment", payment.name, "uf_notified"), 1)
        self.assertEqual(_real_frappe.db.count("Admission Applicant Transfer Log", {"applicant": app.name}), 1)
        notify_uf.assert_not_called()
        self._assert_purged_to_baseline()

    @patch("admission.api.notify_uf.notify_uf_payment")
    @patch("admission.api.convocation.reissue_transfer_convocation", return_value=True)
    def test_institutional_batch_and_justified_absence_do_not_consume_quota(self, _reissue, notify_uf):
        from admission.api.staff import (
            institutional_transfer,
            mark_absent,
            transfer_justified_absence,
            transfer_session,
        )

        batch_source = self._session("BATCH-SRC", 2)
        batch_target = self._session("BATCH-DST", 30)
        voluntary_target = self._session("BATCH-VOL", 60)
        batch_apps = [self._paid_applicant(batch_source, f"BATCH-{i}")[0] for i in (1, 2)]

        batch = institutional_transfer(batch_source.name, batch_target.name)
        voluntary_after_batch = transfer_session(batch_apps[0].name, voluntary_target.name)

        absence_source = self._session("ABS-SRC", -3)
        absence_target = self._session("ABS-DST", 35)
        absence_voluntary_target = self._session("ABS-VOL", 65)
        absent, _, _ = self._paid_applicant(absence_source, "ABS")
        _real_frappe.db.set_value(
            "Admission Applicant", absent.name,
            {"notes_concours": '{"__absent__": true}', "notes_validated": 1,
             "notes_validated_by": "Administrator", "notes_validated_date": now_datetime()},
            update_modified=False,
        )
        marked = mark_absent(absent.name)
        justified = transfer_justified_absence(
            absent.name, absence_target.name, "maladie", "Empêchement médical documenté", None,
        )
        voluntary_after_justified = transfer_session(absent.name, absence_voluntary_target.name)
        _real_frappe.db.commit()

        self.assertEqual(batch["data"]["transferred"], 2)
        self.assertEqual(batch["data"]["convocations_reissued"], 2)
        self.assertEqual(batch["data"]["logs"], 2)
        self.assertTrue(voluntary_after_batch["ok"])
        self.assertEqual(_real_frappe.db.get_value("Admission Session", batch_source.name, "lifecycle_state"), "Open")
        self.assertEqual(marked["data"]["status"], "ABS")
        self.assertTrue(justified["ok"])
        self.assertTrue(voluntary_after_justified["ok"])
        self.assertEqual(_real_frappe.db.get_value("Admission Applicant", absent.name, "status"), "ETU")
        self.assertIsNone(_real_frappe.db.get_value("Admission Applicant", absent.name, "notes_concours"))
        self.assertEqual(_real_frappe.db.count("Admission Note Change Log", {"applicant": absent.name}), 2)
        self.assertEqual(
            set(_real_frappe.get_all(
                "Admission Applicant Transfer Log",
                filters={"applicant": absent.name}, pluck="transfer_type",
            )),
            {"justified_absence", "voluntary"},
        )
        notify_uf.assert_not_called()
        self._assert_purged_to_baseline()
