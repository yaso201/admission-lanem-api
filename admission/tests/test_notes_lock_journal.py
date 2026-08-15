"""NOTES-FIX-2 — verrou post-validation (C7, DEC-J) + journal append-only (C8) + DEC-M.

Le verrou vit au POINT DE PASSAGE UNIQUE (_apply_notes) : les 3 chemins d'écriture
(unitaire / masse écran / import CSV) le traversent — aucun n'est une porte dérobée.
Le journal s'écrit dans la MÊME transaction que la note, une ligne par champ changé.
DB réelle (patron test_notes_csv). _MARK = ZZNLJ.
"""

import json

import frappe
from frappe.tests.utils import FrappeTestCase

from admission.api import staff

_MARK = "ZZNLJ"
_SESSION = f"{_MARK}-SES"


def _purge():
    frappe.set_user("Administrator")
    for a in frappe.get_all("Admission Applicant", filters={"session": ["like", _SESSION + "%"]}, pluck="name"):
        frappe.db.delete("Applicant Fee Payment", {"applicant": a})
        frappe.db.delete("Applicant Fee", {"applicant": a})
        frappe.db.delete("Admission Applicant Transition Log", {"applicant": a})
        frappe.db.delete("Admission Note Change Log", {"applicant": a})   # standalone → purge explicite
        frappe.db.delete("Admission Applicant", {"name": a})
    frappe.db.delete("Admission Session", {"session_code": ["like", _SESSION + "%"]})
    frappe.db.commit()


class TestNotesLockJournal(FrappeTestCase):
    def setUp(self):
        _purge()
        frappe.get_doc({
            "doctype": "Admission Session", "session_code": _SESSION, "label": "Lock/Journal",
            "programme_code": "PREPA", "programme_label": "Cycle Préparatoire",
            "academic_year": "2026-2027", "opens_on": "2026-08-01", "closes_on": "2026-08-20",
            "bac_results_date": "2026-07-15", "application_fee_xof": 15000,
            "lifecycle_state": "Closed", "is_prepa_session": 1, "exam_date": "2026-08-26",
            "exam_call_time": "07:30:00", "exam_start_time": "08:00:00",
        }).insert(ignore_permissions=True)
        a = frappe.get_doc({
            "doctype": "Admission Applicant", "status": "BRO", "first_name": "Jo",
            "last_name": "Lock", "email": f"jo.{_MARK}@t.bj".lower(), "phone": "+22990000000",
            "programme_code": "PREPA", "programme_label": "Cycle Préparatoire",
            "level_code": "PRE-A1", "session": _SESSION, "convocation_number": "08260010",
        }).insert(ignore_permissions=True, ignore_mandatory=True)
        frappe.db.set_value("Admission Applicant", a.name, "status", "ETU", update_modified=False)
        fee = frappe.get_doc({"doctype": "Applicant Fee", "applicant": a.name, "session": _SESSION,
                              "fee_type": "competition", "amount_xof": 15000, "status": "Paid"}
                             ).insert(ignore_permissions=True)
        frappe.get_doc({"doctype": "Applicant Fee Payment", "applicant_fee": fee.name,
                        "applicant": a.name, "payment_mode": "Cash", "amount_xof": 15000,
                        "payment_status": "Confirmed", "justificatif": "/x.pdf"}
                       ).insert(ignore_permissions=True)
        self.d = a.name
        staff.set_exam_coefficients(session_id=_SESSION,
                                    coefficients={"maths": 3, "physique": 2, "culture": 1})
        frappe.db.commit()

    def tearDown(self):
        frappe.db.rollback()
        _purge()

    def _journal(self, **flt):
        return frappe.get_all("Admission Note Change Log",
                              filters={"applicant": self.d, **flt},
                              fields=["champ", "old_value", "new_value", "action_type", "origin"],
                              order_by="creation asc")

    def _validate(self):
        staff.valider_notes_concours(dossier_id=self.d)

    # ── journal : 1ʳᵉ saisie = 3 lignes '' → valeur, origine tracée ──
    def test_journal_premiere_saisie(self):
        staff.saisir_note_concours(dossier_id=self.d, notes={"maths": 12, "physique": 11, "culture": 10})
        rows = self._journal(action_type="saisie")
        self.assertEqual(len(rows), 3)
        self.assertEqual({r.champ for r in rows}, {"maths", "physique", "culture"})
        m = [r for r in rows if r.champ == "maths"][0]
        self.assertEqual((m.old_value, m.new_value, m.origin), ("", "12.0", "unitaire"))

    # ── journal : re-saisie = uniquement les champs CHANGÉS, ancienne→nouvelle ──
    def test_journal_diff_seulement(self):
        staff.saisir_note_concours(dossier_id=self.d, notes={"maths": 12, "physique": 11, "culture": 10})
        staff.saisir_note_concours(dossier_id=self.d, notes={"maths": 14, "physique": 11, "culture": 10})
        rows = [r for r in self._journal() if r.old_value == "12.0"]
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0].champ, rows[0].new_value), ("maths", "14.0"))

    # ── journal : passage à ABSENT = transition absent + effacements tracés ──
    def test_journal_absent_transition(self):
        staff.saisir_note_concours(dossier_id=self.d, notes={"maths": 12, "physique": 11, "culture": 10})
        staff.saisir_note_concours(dossier_id=self.d, notes={"__absent__": True})
        rows = self._journal()
        abs_rows = [r for r in rows if r.champ == "absent"]
        self.assertEqual([(abs_rows[0].old_value, abs_rows[0].new_value)], [("non", "oui")])
        eff = [r for r in rows if r.champ == "maths" and r.new_value == ""]
        self.assertEqual(len(eff), 1)

    # ── C7 : le verrou couvre les 3 CHEMINS ──
    def test_verrou_unitaire(self):
        staff.saisir_note_concours(dossier_id=self.d, notes={"maths": 12, "physique": 11, "culture": 10})
        self._validate()
        r = staff.saisir_note_concours(dossier_id=self.d, notes={"maths": 9, "physique": 9, "culture": 9})
        self.assertFalse(r.get("ok"))
        self.assertEqual(r["error"]["code"], "NOTES_LOCKED")
        self.assertEqual(json.loads(frappe.db.get_value("Admission Applicant", self.d,
                                                        "notes_concours"))["maths"], 12.0)

    def test_verrou_masse_et_import(self):
        staff.saisir_note_concours(dossier_id=self.d, notes={"maths": 12, "physique": 11, "culture": 10})
        self._validate()
        res = staff.saisir_notes_masse(session_id=_SESSION,
                                       rows=[{"dossier_id": self.d, "maths": 8, "physique": 8, "culture": 8}])["data"]
        self.assertEqual((res["ecrits"], len(res["problemes"])), (0, 1))
        self.assertIn("verrouillée", res["problemes"][0]["probleme"])
        res = staff.saisir_notes_masse(session_id=_SESSION,
                                       csv_text=f"dossier_id,maths,physique,culture,absent\n{self.d},7,7,7,\n")["data"]
        self.assertEqual((res["ecrits"], len(res["problemes"])), (0, 1))

    # ── DEC-J : Invalider rouvre, journalisé ; la ré-écriture repart en attente ──
    def test_invalider_rouvre_et_journalise(self):
        staff.saisir_note_concours(dossier_id=self.d, notes={"maths": 12, "physique": 11, "culture": 10})
        self._validate()
        r = staff.invalider_notes_concours(dossier_id=self.d)
        self.assertTrue(r.get("ok"))
        inv = self._journal(action_type="invalidation")
        self.assertEqual([(inv[0].champ, inv[0].old_value, inv[0].new_value)],
                         [("validation", "validées", "en attente")])
        r = staff.saisir_note_concours(dossier_id=self.d, notes={"maths": 9, "physique": 9, "culture": 9})
        self.assertTrue(r.get("ok"))
        self.assertEqual(frappe.db.get_value("Admission Applicant", self.d, "notes_validated"), 0)

    # ── DEC-M : décision émise → invalidation refusée ──
    def test_dec_m_decision_emise(self):
        staff.saisir_note_concours(dossier_id=self.d, notes={"maths": 12, "physique": 11, "culture": 10})
        self._validate()
        frappe.db.set_value("Admission Applicant", self.d, "status", "ADM", update_modified=False)
        r = staff.invalider_notes_concours(dossier_id=self.d)
        self.assertFalse(r.get("ok"))
        self.assertEqual(r["error"]["code"], "DECISION_EMISE")
