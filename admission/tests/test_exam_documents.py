"""DOCUMENTS-JOUR-EPREUVE — convoqués (frais 1 confirmé), tri alpha, sous réserve, à la demande.

DB réelle (style test_convocation). Le rendu PDF réel (paysage, multi-page, 2 états) est prouvé
séparément sur image. GJ1/GJ2/GJ3/GJ7.
"""

import frappe
from frappe.tests.utils import FrappeTestCase

from admission.api.exam_documents import _convoques_of_session, render_emargement_html, BLANK_LINES

_MARK = "ZZEXAMT"


class TestExamDocuments(FrappeTestCase):
    def _purge(self):
        apps = frappe.get_all("Admission Applicant", filters={"applicant_name": ["like", f"{_MARK}%"]}, pluck="name")
        for a in apps:
            frappe.db.delete("Applicant Fee Payment", {"applicant": a})
            frappe.db.delete("Applicant Fee", {"applicant": a})
        if apps:
            frappe.db.delete("Admission Applicant", {"name": ["in", apps]})
        frappe.db.delete("Admission Session", {"session_code": ["like", f"{_MARK}%"]})
        frappe.db.commit()

    def setUp(self):
        self._purge()
        self.session = frappe.get_doc({
            "doctype": "Admission Session", "session_code": f"{_MARK}-SES", "label": "Session test",
            "programme_code": "PREPA", "programme_label": "Cycle Préparatoire", "academic_year": "2026-2027",
            "opens_on": "2026-06-01", "closes_on": "2026-08-01", "bac_results_date": "2026-07-15",
            "application_fee_xof": 10000, "is_open": 0, "exam_date": "2026-09-12",
            "exam_call_time": "07:30:00", "exam_start_time": "08:00:00", "exam_room": "Salle B",
        }).insert(ignore_permissions=True, ignore_mandatory=True)

    def tearDown(self):
        frappe.db.rollback()
        self._purge()

    def _candidate(self, nom, verified=True, confirmed=True):
        app = frappe.get_doc({
            "doctype": "Admission Applicant", "applicant_name": f"{_MARK} {nom}", "programme_code": "PREPA",
            "session": self.session.name, "convocation_number": "09260001",
            "pieces": [{"piece_code": "identite", "label": "P", "required": 1,
                        "status": "verified" if verified else "uploaded"}],
        }).insert(ignore_permissions=True, ignore_mandatory=True)
        fee = frappe.get_doc({"doctype": "Applicant Fee", "applicant": app.name, "fee_type": "application",
                              "amount_xof": 10000, "status": "Paid"}).insert(ignore_permissions=True, ignore_mandatory=True)
        pay = frappe.get_doc({"doctype": "Applicant Fee Payment", "applicant": app.name, "applicant_fee": fee.name,
                              "payment_mode": "Cash", "amount_xof": 10000, "payment_status": "Pending"}
                             ).insert(ignore_permissions=True, ignore_mandatory=True)
        if confirmed:   # bascule hors validate (justificatif Cash/Bank)
            frappe.db.set_value("Applicant Fee Payment", pay.name, "payment_status", "Confirmed", update_modified=False)
        return app

    # ── GJ1 : convoqués = frais 1 CONFIRMÉ seulement ───────────────────────────
    def test_only_confirmed_are_convoques(self):
        self._candidate("KODJO Confirmé", confirmed=True)
        self._candidate("MENSAH EnAttente", confirmed=False)   # Pending → PAS convoqué
        _, convoques = _convoques_of_session(self.session.name)
        noms = [c["nom"] for c in convoques]
        self.assertEqual(len(convoques), 1)
        self.assertIn(f"{_MARK} KODJO Confirmé", noms)

    # ── GJ1 : tri alphabétique ─────────────────────────────────────────────────
    def test_alphabetical(self):
        self._candidate("ZOUNON Zoe"); self._candidate("ABALO Ana"); self._candidate("MENSAH Max")
        _, convoques = _convoques_of_session(self.session.name)
        self.assertEqual([c["nom"] for c in convoques],
                         [f"{_MARK} ABALO Ana", f"{_MARK} MENSAH Max", f"{_MARK} ZOUNON Zoe"])

    # ── GJ2 : sous réserve = pièces non vérifiées ──────────────────────────────
    def test_sous_reserve(self):
        self._candidate("KODJO OK", verified=True)
        self._candidate("ABALO KO", verified=False)
        _, convoques = _convoques_of_session(self.session.name)
        by = {c["nom"]: c["verifie"] for c in convoques}
        self.assertTrue(by[f"{_MARK} KODJO OK"])
        self.assertFalse(by[f"{_MARK} ABALO KO"])
        # la mention « sous réserve » apparaît dans le rendu pour le non-vérifié
        html = render_emargement_html(self.session, convoques)
        self.assertIn("sous réserve", html)

    # ── GJ7 : à la demande — un candidat confirmé APRÈS coup apparaît ───────────
    def test_on_demand(self):
        self._candidate("KODJO Premier")
        _, c1 = _convoques_of_session(self.session.name)
        self.assertEqual(len(c1), 1)
        self._candidate("MENSAH Guichet")           # réglé « après la 1re impression »
        _, c2 = _convoques_of_session(self.session.name)
        self.assertEqual(len(c2), 2)

    # ── GJ3 : lignes vierges + total = convoqués RÉELS (pas les vierges) ────────
    def test_blank_lines_and_real_total(self):
        for n in ("KODJO A", "MENSAH B", "ABALO C"):
            self._candidate(n)
        _, convoques = _convoques_of_session(self.session.name)
        html = render_emargement_html(self.session, convoques)
        self.assertIn("Total convoqués : 3", html)          # 3 réels
        # 3 convoqués + BLANK_LINES lignes → dernière ligne numérotée = 3 + BLANK_LINES
        self.assertIn(f'<td class="c">{3 + BLANK_LINES}</td>', html)
