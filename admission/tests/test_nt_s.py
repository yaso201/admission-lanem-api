"""NT-S — invariants serveur non contournables par le CRUD generique.

Ces tests ciblent le double verrou : DocPerms pour fermer REST/Desk aux roles metier,
controleurs pour les invariants absolus, et gardes partagees pour l'UX paiement.
"""

import json
import types
from pathlib import Path
from unittest import TestCase
from unittest.mock import MagicMock, patch


ROOT = Path(__file__).resolve().parents[1]
APPLICANT_MOD = "admission.admission.doctype.admission_applicant.admission_applicant"
PAYMENT_MOD = "admission.admission.doctype.applicant_fee_payment.applicant_fee_payment"
FEE_MOD = "admission.admission.doctype.applicant_fee.applicant_fee"
STAFF_MOD = "admission.api.staff"


def _doctype_json(*parts):
    return json.loads((ROOT.joinpath("admission", "doctype", *parts)).read_text())


class TestProtectedDocPerms(TestCase):
    def test_staff_has_no_generic_write_on_applicant(self):
        doc = _doctype_json("admission_applicant", "admission_applicant.json")
        by_role = {p["role"]: p for p in doc["permissions"]}
        for role in ("Admission Administratif", "Admission Responsable", "Admission Direction"):
            self.assertTrue(by_role[role].get("read"), role)
            self.assertFalse(by_role[role].get("write"), role)
        self.assertTrue(by_role["System Manager"].get("write"))

    def test_staff_has_no_generic_write_on_payment_or_fee(self):
        for folder, filename in (
            ("applicant_fee_payment", "applicant_fee_payment.json"),
            ("applicant_fee", "applicant_fee.json"),
        ):
            doc = _doctype_json(folder, filename)
            by_role = {p["role"]: p for p in doc["permissions"]}
            self.assertFalse(by_role["Admission Administratif"].get("write"), folder)
            self.assertTrue(by_role["System Manager"].get("write"), folder)

    def test_confirmed_by_is_internal_and_read_only(self):
        doc = _doctype_json("applicant_fee_payment", "applicant_fee_payment.json")
        field = next((f for f in doc["fields"] if f.get("fieldname") == "confirmed_by"), None)
        self.assertIsNotNone(field)
        self.assertEqual(field.get("fieldtype"), "Link")
        self.assertEqual(field.get("options"), "User")
        self.assertTrue(field.get("read_only"))
        self.assertTrue(field.get("hidden"))


def _applicant_stub(
    old_status, new_status, *, notes_validated=0, old_notes_validated=None,
    session="SES-PREPA",
):
    if old_notes_validated is None:
        old_notes_validated = notes_validated
    old = types.SimpleNamespace(status=old_status, notes_validated=old_notes_validated)
    return types.SimpleNamespace(
        name="CAN-NT-S-1",
        status=new_status,
        session=session,
        notes_validated=notes_validated,
        get_doc_before_save=lambda: old,
    )


class TestApplicantControllerInvariant(TestCase):
    def _run_gate(self, stub, is_prepa):
        from admission.admission.doctype.admission_applicant.admission_applicant import AdmissionApplicant

        with patch(f"{APPLICANT_MOD}.frappe") as mf:
            mf.db.get_value.return_value = 1 if is_prepa else 0
            mf.throw.side_effect = ValueError
            AdmissionApplicant._enforce_prepa_decision_gate(stub)

    def test_prepa_etu_to_adm_without_validated_notes_is_rejected(self):
        with self.assertRaises(ValueError):
            self._run_gate(_applicant_stub("ETU", "ADM"), is_prepa=True)

    def test_prepa_adm_to_acc_and_ref_are_rechecked(self):
        for target in ("ACC", "REF"):
            with self.subTest(target=target), self.assertRaises(ValueError):
                self._run_gate(_applicant_stub("ADM", target), is_prepa=True)

    def test_prepa_decision_with_validated_notes_is_allowed(self):
        self._run_gate(_applicant_stub("ETU", "ADM", notes_validated=1), is_prepa=True)

    def test_prepa_cannot_forge_notes_validation_with_decision(self):
        with self.assertRaises(ValueError):
            self._run_gate(
                _applicant_stub(
                    "ETU", "ADM", notes_validated=1, old_notes_validated=0,
                ),
                is_prepa=True,
            )

    def test_licence_decision_without_notes_is_allowed(self):
        self._run_gate(_applicant_stub("ETU", "ADM"), is_prepa=False)

    def test_sm_break_glass_warning_names_fields_not_values(self):
        old = types.SimpleNamespace(status="ETU", notes_concours='{"maths": 11}')
        stub = types.SimpleNamespace(
            name="CAN-NT-S-1", status="ETU", notes_concours='{"maths": 12}',
            flags=types.SimpleNamespace(ignore_permissions=False),
            get_doc_before_save=lambda: old,
            get=lambda field: getattr(stub, field, None),
        )
        with patch(f"{APPLICANT_MOD}.frappe") as mf, \
             patch(f"{APPLICANT_MOD}.log_event") as log:
            mf.session.user = "Administrator"
            mf.get_roles.return_value = ["System Manager"]
            from admission.admission.doctype.admission_applicant.admission_applicant import AdmissionApplicant
            AdmissionApplicant._warn_sm_sensitive_write(stub)
        log.assert_called_once()
        payload = log.call_args.kwargs
        self.assertEqual(payload["fields"], "notes_concours")
        self.assertNotIn("12", str(payload))


class TestPaymentControllerInvariant(TestCase):
    def test_confirmed_cannot_return_to_pending(self):
        old = types.SimpleNamespace(payment_status="Confirmed", justificatif="/private/a.pdf")
        payment = types.SimpleNamespace(
            payment_status="Pending", justificatif="/private/a.pdf",
            get_doc_before_save=lambda: old,
        )
        with patch(f"{PAYMENT_MOD}.frappe") as mf:
            mf.throw.side_effect = ValueError
            from admission.admission.doctype.applicant_fee_payment.applicant_fee_payment import ApplicantFeePayment
            with self.assertRaises(ValueError):
                ApplicantFeePayment._guard_confirmed_irreversible(payment)

    def test_pending_can_be_confirmed(self):
        old = types.SimpleNamespace(payment_status="Pending", justificatif=None)
        payment = types.SimpleNamespace(
            payment_status="Confirmed", justificatif=None,
            get_doc_before_save=lambda: old,
        )
        with patch(f"{PAYMENT_MOD}.frappe"):
            from admission.admission.doctype.applicant_fee_payment.applicant_fee_payment import ApplicantFeePayment
            ApplicantFeePayment._guard_confirmed_irreversible(payment)


class TestPaymentStateBounds(TestCase):
    def test_can_manage_payments_matches_endpoint_states(self):
        from admission.api._actions import can_manage_payments
        roles = ["Admission Administratif"]
        for state in ("BRO", "SOP", "SOU", "ACC"):
            self.assertTrue(can_manage_payments(types.SimpleNamespace(status=state), roles), state)
        for state in ("INC", "ETU", "ATT", "ADM", "ACO", "ABS", "REF", "REJ", "DES", "INS"):
            self.assertFalse(can_manage_payments(types.SimpleNamespace(status=state), roles), state)

    def test_fee_type_state_matrix(self):
        from admission.api._actions import payment_state_allowed
        for fee_type in ("application", "competition"):
            for state in ("BRO", "SOP", "SOU"):
                self.assertTrue(payment_state_allowed(state, fee_type))
            self.assertFalse(payment_state_allowed("ACC", fee_type))
        self.assertTrue(payment_state_allowed("ACC", "enrollment"))
        self.assertFalse(payment_state_allowed("SOU", "enrollment"))

    def test_offline_confirmation_rechecks_fee_type_boundaries(self):
        payment = MagicMock()
        payment.name = "PAY-NT-S"
        payment.payment_status = "Pending"
        payment.applicant_fee = "FEE-NT-S"
        applicant = MagicMock()
        fee = MagicMock()
        with patch(f"{STAFF_MOD}.frappe") as mf, \
             patch(f"{STAFF_MOD}._guard_write_scope", return_value=None), \
             patch(f"{STAFF_MOD}._resolve_pending_payment", return_value=payment), \
             patch(f"{STAFF_MOD}._error", side_effect=lambda code, message, status=400: {
                 "ok": False, "error": {"code": code},
             }):
            mf.db.exists.return_value = True
            mf.get_doc.side_effect = (
                lambda doctype, name=None: applicant
                if doctype == "Admission Applicant" else fee
            )
            from admission.api.staff import confirm_offline_payment
            for status, fee_type in (("ETU", "application"), ("SOU", "enrollment")):
                with self.subTest(status=status, fee_type=fee_type):
                    applicant.status = status
                    fee.fee_type = fee_type
                    result = confirm_offline_payment("CAN-NT-S")
                    self.assertEqual(result["error"]["code"], "INVALID_STATE")
        payment.save.assert_not_called()


class TestCoefficientPrepaGuard(TestCase):
    def test_direct_call_on_licence_returns_not_prepa(self):
        session = types.SimpleNamespace(name="SES-LIC", is_prepa_session=0)
        with patch(f"{STAFF_MOD}.frappe") as mf, \
             patch(f"{STAFF_MOD}._error", side_effect=lambda c, m, s=400: {"code": c}), \
             patch(f"{STAFF_MOD}._ok", side_effect=lambda d: {"ok": True, **d}):
            mf.db.exists.return_value = True
            mf.get_doc.return_value = session
            from admission.api.staff import set_exam_coefficients
            result = set_exam_coefficients(
                session_id="SES-LIC",
                coefficients={"maths": 1, "physique": 1, "culture": 1},
            )
        self.assertEqual(result["code"], "NOT_PREPA")
        session.save.assert_not_called() if isinstance(session, MagicMock) else None


class TestCloseSessionPrepaGuard(TestCase):
    def _call(self, dry_run):
        session = types.SimpleNamespace(
            name="SES-PREPA", label="Prépa", is_open=1, is_prepa_session=1,
        )
        rows = [
            types.SimpleNamespace(name="CAN-X", status="ETU", notes_validated=0),
            types.SimpleNamespace(name="CAN-Y", status="SOU", notes_validated=0),
            types.SimpleNamespace(name="CAN-Z", status="ATT", notes_validated=1),
        ]
        with patch(f"{STAFF_MOD}.frappe") as mf, \
             patch("admission.api.permissions.value_in_scope", return_value=True), \
             patch(f"{STAFF_MOD}._ok", side_effect=lambda d: {"ok": True, "data": d}), \
             patch(f"{STAFF_MOD}._error", side_effect=lambda c, m, s=400: {
                 "ok": False, "error": {"code": c, "message": m},
             }):
            mf.db.exists.return_value = True
            mf.get_doc.return_value = session
            mf.get_all.return_value = rows
            from admission.api.staff import close_session
            result = close_session(session="SES-PREPA", dry_run=dry_run)
            return result, mf

    def test_dry_run_names_all_blocking_dossiers(self):
        result, _ = self._call(dry_run=1)
        self.assertTrue(result["ok"])
        self.assertFalse(result["data"]["can_execute"])
        self.assertEqual(result["data"]["blocking_dossiers"], ["CAN-X", "CAN-Y"])

    def test_execution_refuses_before_any_write(self):
        result, mf = self._call(dry_run=0)
        self.assertEqual(result["error"]["code"], "PREPA_NOTES_NOT_VALIDATED")
        self.assertIn("CAN-X, CAN-Y", result["error"]["message"])
        self.assertNotIn("CAN-Z", result["error"]["message"])
        mf.db.set_value.assert_not_called()
        mf.db.commit.assert_not_called()


class TestActorSeparationWarning(TestCase):
    def test_same_actor_is_logged_but_never_blocked(self):
        stub = types.SimpleNamespace(
            name="CAN-NT-S-1",
            flags=types.SimpleNamespace(status_changed_to="ADM"),
        )
        with patch(f"{APPLICANT_MOD}.frappe") as mf, \
             patch(f"{APPLICANT_MOD}.log_event") as log:
            mf.session.user = "agent@lanem.bj"
            mf.db.exists.return_value = "REC-NT-S-1"
            from admission.admission.doctype.admission_applicant.admission_applicant import AdmissionApplicant
            AdmissionApplicant._warn_same_actor_payment_decision(stub)
        log.assert_called_once()
        self.assertEqual(log.call_args.kwargs["level"], "warning")
        self.assertEqual(log.call_args.kwargs["ref"], "REC-NT-S-1")


def run_runtime_trace():
    """Traceur DEV réel NT-S, invoqué par ``bench execute`` et intégralement rollbacké."""
    import frappe
    from frappe.utils import now_datetime

    from admission.api import staff

    savepoint = "nt_s_runtime_trace"
    frappe.db.savepoint(savepoint)
    baseline = {
        dt: frappe.db.count(dt)
        for dt in (
            "Admission Applicant", "Applicant Fee", "Applicant Fee Payment",
            "Admission Note Change Log", "Admission Applicant Transition Log", "User", "Version",
        )
    }
    original_user = frappe.session.user
    original_in_test = getattr(frappe.flags, "in_test", False)
    out = {}
    try:
        prepa_name = frappe.db.get_value("Admission Session", {"is_prepa_session": 1}, "name")
        licence_name = frappe.db.get_value("Admission Session", {"is_prepa_session": 0}, "name")
        if not prepa_name or not licence_name:
            raise AssertionError("Le traceur exige une session Prépa et une session Licence en DEV.")
        prepa_session = frappe.get_doc("Admission Session", prepa_name)
        licence_session = frappe.get_doc("Admission Session", licence_name)

        suffix = frappe.generate_hash(length=6).lower()
        resp = f"zztest-nts-resp-{suffix}@test.lanem.bj"
        admin = f"zztest-nts-admin-{suffix}@test.lanem.bj"
        direction = f"zztest-nts-dir-{suffix}@test.lanem.bj"
        for email, role in (
            (resp, "Admission Responsable"),
            (admin, "Admission Administratif"),
            (direction, "Admission Direction"),
        ):
            frappe.get_doc({
                "doctype": "User", "email": email, "first_name": "ZZTEST NT-S",
                "send_welcome_email": 0, "enabled": 1, "roles": [{"role": role}],
            }).insert(ignore_permissions=True)

        def applicant(session, marker):
            doc = frappe.get_doc({
                "doctype": "Admission Applicant", "status": "BRO",
                "first_name": "ZZTEST", "last_name": marker,
                "email": f"zztest-nts-{marker.lower()}-{suffix}@test.lanem.bj",
                "phone": "+2290100000000", "programme_code": session.programme_code,
                "programme_label": session.programme_label,
                "level_code": f"ZZTEST-{marker}", "session": session.name,
            }).insert(ignore_permissions=True, ignore_mandatory=True)
            frappe.db.set_value(
                "Admission Applicant", doc.name, "status", "ETU", update_modified=False,
            )
            doc.reload()
            return doc

        prepa = applicant(prepa_session, "PREPA")
        licence = applicant(licence_session, "LIC")

        # 1. Rejeu exact : CRUD Responsable ETU→ADM refusé par permissions, puis même tentative
        # avec ignore_permissions refusée par l'invariant contrôleur.
        frappe.set_user(resp)
        generic = frappe.get_doc("Admission Applicant", prepa.name)
        generic.status = "ADM"
        try:
            generic.save()
            raise AssertionError("Le CRUD Responsable ETU→ADM a été accepté.")
        except frappe.PermissionError:
            out["prepa_generic_crud"] = "PERMISSION_REFUSED"
        from frappe.model.workflow import apply_workflow
        try:
            apply_workflow(
                frappe.get_doc("Admission Applicant", prepa.name).as_json(),
                "Mark Admissible",
            )
            raise AssertionError("Le Workflow Desk générique a accepté ETU→ADM.")
        except frappe.PermissionError:
            out["prepa_generic_workflow"] = "PERMISSION_REFUSED"
        forced = frappe.get_doc("Admission Applicant", prepa.name)
        forced.status = "ADM"
        try:
            forced.save(ignore_permissions=True)
            raise AssertionError("La garde contrôleur Prépa a été contournée.")
        except frappe.ValidationError:
            out["prepa_controller"] = "NOTES_NOT_VALIDATED"

        # Même le break-glass SM ne peut faire passer la validation et la décision dans
        # une seule écriture : les notes doivent avoir été validées auparavant.
        frappe.set_user("Administrator")
        forged = frappe.get_doc("Admission Applicant", prepa.name)
        forged.notes_concours = json.dumps({"maths": 12, "physique": 13, "culture": 14})
        forged.notes_validated = 1
        forged.status = "ADM"
        try:
            forged.save()
            raise AssertionError("Le break-glass a forgé validation et décision ensemble.")
        except frappe.ValidationError:
            out["prepa_sm_combined_forge"] = "REFUSED"

        # 2. Notes : CRUD Administratif refusé ; voie dédiée fonctionne et journalise.
        valid_notes = json.dumps({"maths": 12, "physique": 13, "culture": 14})
        frappe.set_user("Administrator")
        frappe.db.set_value(
            "Admission Applicant", prepa.name,
            {"notes_concours": valid_notes, "notes_validated": 1,
             "notes_validated_by": resp, "notes_validated_date": now_datetime()},
            update_modified=False,
        )
        frappe.set_user(admin)
        generic_notes = frappe.get_doc("Admission Applicant", prepa.name)
        generic_notes.notes_concours = json.dumps({"maths": 15, "physique": 13, "culture": 14})
        try:
            generic_notes.save()
            raise AssertionError("Le CRUD notes Administratif a été accepté.")
        except frappe.PermissionError:
            out["notes_generic_crud"] = "PERMISSION_REFUSED"
        before_note_log = frappe.db.count("Admission Note Change Log", {"applicant": prepa.name})
        frappe.set_user(resp)
        invalidated = staff.invalider_notes_concours(prepa.name)
        after_note_log = frappe.db.count("Admission Note Change Log", {"applicant": prepa.name})
        if not invalidated.get("ok") or after_note_log != before_note_log + 1:
            raise AssertionError("La voie dédiée d'invalidation n'a pas journalisé exactement une ligne.")
        out["notes_dedicated"] = f"OK_LOG_DELTA_{after_note_log - before_note_log}"

        # 3. Confirmed irréversible, y compris sous Administrator/System Manager.
        frappe.set_user("Administrator")
        fee = frappe.get_doc({
            "doctype": "Applicant Fee", "applicant": prepa.name, "session": prepa.session,
            "fee_type": "competition", "amount_xof": 15000, "status": "Paid",
        }).insert(ignore_permissions=True, ignore_mandatory=True)
        payment = frappe.get_doc({
            "doctype": "Applicant Fee Payment", "applicant_fee": fee.name,
            "applicant": prepa.name, "payment_mode": "Online", "source": "online",
            "amount_xof": 15000, "payment_status": "Confirmed", "paid_at": now_datetime(),
            "confirmed_by": resp,
        }).insert(ignore_permissions=True, ignore_mandatory=True)
        payment = frappe.get_doc("Applicant Fee Payment", payment.name)
        payment.payment_status = "Pending"
        try:
            payment.save()
            raise AssertionError("Confirmed→Pending a été accepté sous System Manager.")
        except frappe.ValidationError:
            out["confirmed_irreversible"] = "REFUSED_FOR_SM"

        # 4. Licence : décision sans notes toujours possible.
        frappe.set_user(resp)
        with patch(f"{STAFF_MOD}.send_decision_notification"), \
             patch(f"{STAFF_MOD}.send_prepa_decision_notification"):
            licence_result = staff.mark_admissible(licence.name)
        if not licence_result.get("ok"):
            raise AssertionError(f"Régression Licence : {licence_result}")
        out["licence_without_notes"] = "ADM_OK"

        # 5. Même acteur confirmation puis décision : signal warning, jamais blocage.
        actor_app = applicant(licence_session, "ACTOR")
        frappe.set_user("Administrator")
        actor_fee = frappe.get_doc({
            "doctype": "Applicant Fee", "applicant": actor_app.name, "session": actor_app.session,
            "fee_type": "application", "amount_xof": 15000, "status": "Pending",
        }).insert(ignore_permissions=True, ignore_mandatory=True)
        actor_payment = frappe.get_doc({
            "doctype": "Applicant Fee Payment", "applicant_fee": actor_fee.name,
            "applicant": actor_app.name, "payment_mode": "Bank", "source": "banque",
            "amount_xof": 15000, "payment_status": "Pending", "justificatif": "/private/files/zztest.pdf",
        }).insert(ignore_permissions=True, ignore_mandatory=True)
        frappe.db.set_value("Admission Applicant", actor_app.name, "status", "SOU", update_modified=False)
        frappe.set_user(resp)
        with patch("admission.api.notify_uf.on_payment_update"), \
             patch(f"{STAFF_MOD}.send_payment_receipt"):
            confirmed = staff.confirm_offline_payment(
                actor_app.name, payment_mode="bank", justificatif="/private/files/zztest.pdf",
                payment_id=actor_payment.name,
            )
        if not confirmed.get("ok"):
            raise AssertionError(f"Confirmation dédiée échouée : {confirmed}")
        confirmed_by = frappe.db.get_value("Applicant Fee Payment", actor_payment.name, "confirmed_by")
        if confirmed_by != resp:
            raise AssertionError("confirmed_by ne porte pas l'acteur réel.")
        frappe.db.set_value("Admission Applicant", actor_app.name, "status", "ETU", update_modified=False)
        with patch(f"{APPLICANT_MOD}.log_event") as actor_log, \
             patch(f"{STAFF_MOD}.send_decision_notification"), \
             patch(f"{STAFF_MOD}.send_prepa_decision_notification"):
            decided = staff.mark_admissible(actor_app.name)
        if not decided.get("ok"):
            raise AssertionError(f"La séparation d'acteur a bloqué : {decided}")
        same_actor_calls = [
            call for call in actor_log.call_args_list
            if len(call.args) >= 2 and call.args[1] == "same_actor_payment_decision"
        ]
        if len(same_actor_calls) != 1:
            raise AssertionError("Le warning même acteur n'a pas été émis exactement une fois.")
        out["same_actor"] = "WARNING_NON_BLOCKING"

        # 6. Break-glass générique : Version + warning ; l'invariant paiement ci-dessus reste fermé.
        frappe.set_user("Administrator")
        frappe.flags.in_test = False
        version_before = frappe.db.count(
            "Version", {"ref_doctype": "Admission Applicant", "docname": actor_app.name},
        )
        sm_doc = frappe.get_doc("Admission Applicant", actor_app.name)
        sm_doc.motif_incompletude = "ZZTEST break-glass"
        with patch(f"{APPLICANT_MOD}.log_event") as sm_log:
            sm_doc.save()
        version_after = frappe.db.count(
            "Version", {"ref_doctype": "Admission Applicant", "docname": actor_app.name},
        )
        if version_after != version_before + 1 or not sm_log.called:
            raise AssertionError("Break-glass sans double trace Version + warning.")

        fee_version_before = frappe.db.count(
            "Version", {"ref_doctype": "Applicant Fee", "docname": fee.name},
        )
        fee_doc = frappe.get_doc("Applicant Fee", fee.name)
        fee_doc.status = "Pending"
        with patch(f"{FEE_MOD}.log_event") as fee_log:
            fee_doc.save()
        fee_version_after = frappe.db.count(
            "Version", {"ref_doctype": "Applicant Fee", "docname": fee.name},
        )
        if fee_version_after != fee_version_before + 1 or not fee_log.called:
            raise AssertionError("Break-glass frais sans double trace Version + warning.")

        payment_version_before = frappe.db.count(
            "Version", {"ref_doctype": "Applicant Fee Payment", "docname": payment.name},
        )
        payment_doc = frappe.get_doc("Applicant Fee Payment", payment.name)
        payment_doc.reconciliation = "Stale - awaiting webhook"
        with patch(f"{PAYMENT_MOD}.log_event") as payment_log:
            payment_doc.save()
        payment_version_after = frappe.db.count(
            "Version", {"ref_doctype": "Applicant Fee Payment", "docname": payment.name},
        )
        if payment_version_after != payment_version_before + 1 or not payment_log.called:
            raise AssertionError("Break-glass paiement sans double trace Version + warning.")
        out["sm_break_glass"] = "THREE_DOCTYPES_VERSION_PLUS_WARNING"

        # 7. Clôture Prépa : dry-run puis exécution nomment les bloqueurs, zéro écriture.
        frappe.set_user(direction)
        session_state_before = frappe.db.get_value(
            "Admission Session", prepa_session.name, ["lifecycle_state", "is_open"], as_dict=True,
        )
        dry = staff.close_session(prepa_session.name, dry_run=1)
        executed = staff.close_session(prepa_session.name, dry_run=0)
        session_state_after = frappe.db.get_value(
            "Admission Session", prepa_session.name, ["lifecycle_state", "is_open"], as_dict=True,
        )
        if prepa.name not in dry["data"]["blocking_dossiers"]:
            raise AssertionError("Le dry-run ne nomme pas le dossier Prépa bloquant.")
        if (executed.get("error") or {}).get("code") != "PREPA_NOTES_NOT_VALIDATED":
            raise AssertionError("La clôture Prépa bloquante n'a pas été refusée.")
        if session_state_before != session_state_after:
            raise AssertionError("La session a changé malgré le refus atomique.")
        out["prepa_close"] = "DRY_RUN_NAMED_AND_EXECUTION_REFUSED"

        # 8. Coefficients Licence : appel direct refusé.
        frappe.set_user(resp)
        coef = staff.set_exam_coefficients(
            licence_session.name, {"maths": 1, "physique": 1, "culture": 1},
        )
        if (coef.get("error") or {}).get("code") != "NOT_PREPA":
            raise AssertionError(f"Coefficients Licence acceptés : {coef}")
        out["licence_coefficients"] = "NOT_PREPA"
    finally:
        frappe.set_user(original_user)
        frappe.flags.in_test = original_in_test
        frappe.flags.nt_s_session_close = False
        frappe.db.rollback(save_point=savepoint)

    after = {dt: frappe.db.count(dt) for dt in baseline}
    if after != baseline:
        raise AssertionError(f"Purge NT-S incomplète : avant={baseline}, après={after}")
    out["purge"] = "BASELINE_RESTORED"
    print("NT_S_RUNTIME_TRACE::" + json.dumps(out, sort_keys=True, ensure_ascii=False))
    return out
