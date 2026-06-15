"""Numérotation dossier (XXXXYYYNNNN) + reçu (XXAANNNNN) — formats validés 15/06/2026."""

from unittest import TestCase
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from admission.api import numbering as N


class TestAcademicYearCode(TestCase):
    def test_tiret(self):
        with patch.object(N, "frappe") as mf:
            mf.db.get_value.return_value = "2026-2027"
            self.assertEqual(N.academic_year_code("S"), "2627")

    def test_slash(self):
        with patch.object(N, "frappe") as mf:
            mf.db.get_value.return_value = "2026/2027"
            self.assertEqual(N.academic_year_code("S"), "2627")


class TestProgrammeCodeAuto(FrappeTestCase):
    def test_auto_assign_famille_index(self):
        code = "ZZ-NUM-LIC"
        self.addCleanup(lambda: frappe.db.exists("Admission Programme", code)
                        and frappe.delete_doc("Admission Programme", code, force=True))
        doc = frappe.get_doc({"doctype": "Admission Programme", "programme_code": code,
                              "title": "Licence test", "parcours": "Licence"}).insert(ignore_permissions=True)
        self.assertTrue(doc.numbering_code.startswith("2"))   # famille Licence = 2
        self.assertEqual(len(doc.numbering_code), 3)          # Y + YY


class TestNumberingDB(FrappeTestCase):
    def setUp(self):
        sess = frappe.get_all("Admission Session", limit=1, pluck="name")
        if not sess:
            self.skipTest("aucune Admission Session (décor requis)")
        self.session = sess[0]
        code = "ZZ-NUM-PROG"
        if frappe.db.exists("Admission Programme", code):
            frappe.delete_doc("Admission Programme", code, force=True)
        self.prog = frappe.get_doc({"doctype": "Admission Programme", "programme_code": code,
                                    "title": "Prog num test", "parcours": "Licence"}).insert(ignore_permissions=True)
        self.addCleanup(lambda: frappe.db.exists("Admission Programme", code)
                        and frappe.delete_doc("Admission Programme", code, force=True))

    def _applicant(self):
        a = frappe.get_doc({"doctype": "Admission Applicant", "status": "BRO",
                            "first_name": "Num", "last_name": "Test", "email": "num-test@lanem.test",
                            "phone": "+22900000099", "programme_code": self.prog.name,
                            "level_code": "X", "session": self.session}).insert(ignore_permissions=True)
        self.addCleanup(lambda: frappe.db.exists("Admission Applicant", a.name)
                        and frappe.delete_doc("Admission Applicant", a.name, force=True))
        return a

    def test_dossier_name_format(self):
        a = self._applicant()
        self.assertTrue(a.name.isdigit(), a.name)
        self.assertEqual(len(a.name), 11)                 # XXXX(4)+YYY(3)+NNNN(4)
        self.assertEqual(a.name[4:7], self.prog.numbering_code)  # YYY = code du programme

    def test_receipt_name_format(self):
        a = self._applicant()
        fee = frappe.get_doc({"doctype": "Applicant Fee", "applicant": a.name,
                              "fee_type": "application", "amount_xof": 1000, "status": "Pending",
                              "session": self.session, "programme_code": self.prog.name,
                              "level_code": "X"}).insert(ignore_permissions=True)
        self.addCleanup(lambda: frappe.db.exists("Applicant Fee", fee.name)
                        and frappe.delete_doc("Applicant Fee", fee.name, force=True))
        p = frappe.get_doc({"doctype": "Applicant Fee Payment", "applicant": a.name,
                            "applicant_fee": fee.name, "payment_mode": "Online", "amount_xof": 1000,
                            "payment_status": "Pending"}).insert(ignore_permissions=True)
        self.addCleanup(lambda: frappe.db.exists("Applicant Fee Payment", p.name)
                        and frappe.delete_doc("Applicant Fee Payment", p.name, force=True))
        self.assertTrue(p.name.isdigit(), p.name)
        self.assertEqual(len(p.name), 9)                  # XX(2)+AA(2)+NNNNN(5)
        self.assertEqual(p.name[2:4], "11")               # AA = source 1 + canal Online 1
        self.assertEqual(p.receipt_number, p.name)
