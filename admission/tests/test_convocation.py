"""CONVOCATION-PREPA — déclenchement, envoi unique, gate date d'épreuve, sous réserve, guichet.

DB réelle (style test_fee_verrou). get_pdf + sendmail mockés dans les tests d'envoi (rapidité +
isolement) ; le rendu PDF réel est prouvé séparément (2 états, navigateur). GC1/GC3/GC5/GC6.
"""

from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from admission.api.numbering import build_convocation_number
from admission.api.public import apply_confirmed_payment_cascade

PUB = "admission.api.public"
CONV = "admission.api.convocation"
_MARK = "ZZCONVOC"


class TestConvocation(FrappeTestCase):
    def _purge(self):
        apps = frappe.get_all("Admission Applicant",
                              filters={"applicant_name": ["like", f"{_MARK}%"]}, pluck="name")
        for a in apps:
            frappe.db.delete("Applicant Fee Payment", {"applicant": a})
            frappe.db.delete("Applicant Fee", {"applicant": a})
        if apps:
            frappe.db.delete("Admission Applicant", {"name": ["in", apps]})
        frappe.db.delete("Admission Session", {"session_code": ["like", f"{_MARK}%"]})
        frappe.db.commit()

    def _session(self, exam_date=None, is_open=1):
        code = f"{_MARK}-SES"
        doc = frappe.get_doc({
            "doctype": "Admission Session", "session_code": code, "label": "Session test",
            "programme_code": "PREPA", "programme_label": "Cycle Préparatoire",
            "academic_year": "2026-2027", "opens_on": "2026-06-01", "closes_on": "2026-08-01",
            "bac_results_date": "2026-07-15", "application_fee_xof": 10000,
            "is_open": is_open, "exam_date": exam_date, "exam_call_time": "07:30:00",
            "exam_start_time": "08:00:00", "exam_room": "Salle B",
        }).insert(ignore_permissions=True, ignore_mandatory=True)
        return doc

    def _applicant(self, session, verified_pieces=True):
        app = frappe.get_doc({
            "doctype": "Admission Applicant", "applicant_name": f"{_MARK} Test",
            "programme_code": "PREPA", "session": session.name,
            "pieces": [{"piece_code": "identite", "label": "Pièce", "required": 1,
                        "status": "verified" if verified_pieces else "uploaded"}],
        }).insert(ignore_permissions=True, ignore_mandatory=True)
        frappe.db.set_value("Admission Applicant", app.name,
                            {"email": "c@test.local", "status": "SOU"}, update_modified=False)
        app.reload()
        return app

    def _fee(self, applicant, fee_type="application"):
        return frappe.get_doc({
            "doctype": "Applicant Fee", "applicant": applicant.name, "fee_type": fee_type,
            "amount_xof": 10000, "status": "Paid",
        }).insert(ignore_permissions=True, ignore_mandatory=True)

    def setUp(self):
        self._purge()

    def tearDown(self):
        frappe.db.rollback()
        self._purge()

    # ── GC6 : la date d'épreuve DÉCIDE — aucun cas particulier de parcours ──────
    def test_trigger_fires_with_exam_date(self):
        session = self._session(exam_date="2026-09-12")
        app = self._applicant(session)
        fee = self._fee(app)
        with patch(f"{CONV}.send_convocation") as mock_send:
            apply_confirmed_payment_cascade(app, fee)
        mock_send.assert_called_once()

    def test_trigger_skipped_without_exam_date(self):
        session = self._session(exam_date=None)   # licence/bachelor → pas d'épreuve
        app = self._applicant(session)
        fee = self._fee(app)
        with patch(f"{CONV}.send_convocation") as mock_send:
            apply_confirmed_payment_cascade(app, fee)
        mock_send.assert_not_called()

    def test_trigger_skipped_for_frais2(self):
        session = self._session(exam_date="2026-09-12")
        app = self._applicant(session)
        fee = self._fee(app, fee_type="enrollment")   # frais 2 → jamais de convocation
        with patch(f"{CONV}.send_convocation") as mock_send:
            apply_confirmed_payment_cascade(app, fee)
        mock_send.assert_not_called()

    # ── GC1 : envoi UNIQUE + numéro DÉFINITIF ──────────────────────────────────
    def test_send_once_assigns_number_and_flag(self):
        from admission.api.convocation import send_convocation
        session = self._session(exam_date="2026-09-12")
        app = self._applicant(session)
        self._fee(app)
        with patch(f"{CONV}.get_pdf", return_value=b"%PDF"), patch("frappe.sendmail") as ms:
            send_convocation(app, session)
            num1 = frappe.db.get_value("Admission Applicant", app.name, "convocation_number")
            sent1 = frappe.db.get_value("Admission Applicant", app.name, "convocation_sent_at")
            self.assertTrue(num1 and sent1)
            self.assertEqual(ms.call_count, 1)
            # 2e appel → envoi unique (drapeau) : aucun nouvel e-mail, numéro conservé
            app.reload()
            send_convocation(app, session)
            self.assertEqual(ms.call_count, 1)
            self.assertEqual(frappe.db.get_value("Admission Applicant", app.name, "convocation_number"), num1)

    # ── GC5 (essence) : session FERMÉE → la convocation part quand même ─────────
    def test_closed_session_still_triggers(self):
        session = self._session(exam_date="2026-09-12", is_open=0)   # session fermée (guichet)
        app = self._applicant(session)
        fee = self._fee(app)
        with patch(f"{CONV}.send_convocation") as mock_send:
            apply_confirmed_payment_cascade(app, fee)
        mock_send.assert_called_once()   # aucune garde de session sur la confirmation

    # ── GC3 : sous réserve = pièces non vérifiées ──────────────────────────────
    def test_dossier_verifie_flag(self):
        from admission.api.convocation import _pieces_non_verifiees
        session = self._session(exam_date="2026-09-12")
        app_ok = self._applicant(session, verified_pieces=True)
        self.assertEqual(_pieces_non_verifiees(app_ok), [])            # vérifié → mention retirée
        self._purge_app(app_ok)
        app_ko = self._applicant(session, verified_pieces=False)
        self.assertTrue(_pieces_non_verifiees(app_ko))                 # non vérifié → mention affichée

    def _purge_app(self, app):
        frappe.delete_doc("Admission Applicant", app.name, force=True, ignore_permissions=True)

    # ── numéro : format MMAAXXXX + compteur ANNUEL continu ─────────────────────
    def test_number_format_annual(self):
        a = build_convocation_number("2026-09-12")
        b = build_convocation_number("2026-08-05")   # mois différent, même année
        self.assertEqual(len(a), 8)
        self.assertTrue(a.startswith("0926") and b.startswith("0826"))
        self.assertNotEqual(a, b)
        # compteur continu (annuel) : le 2e numéro suit le 1er malgré le mois différent
        self.assertEqual(int(b[4:]), int(a[4:]) + 1)
