"""CAL-AMEL — journal sessions (DEC-P), bandeau À traiter (DEC-N), rappels idempotents (DEC-O),
compteur DEC-Q, chaînage AMEL-06-b. DB réelle. _MARK = ZZAMEL."""

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_days, nowdate

from admission.api import calendar, calendar_reminders, calendar_view, sessions

_MARK = "ZZAMEL"


def _purge():
    frappe.set_user("Administrator")
    for dt in ("Admission Session Change Log", "Admission Session Reminder"):
        frappe.db.delete(dt, {"session": ["like", _MARK + "%"]})   # standalone → purge explicite
    frappe.db.delete("Admission Session Pending Change", {"parent": ["like", _MARK + "%"]})
    for a in frappe.get_all("Admission Applicant", filters={"session": ["like", _MARK + "%"]}, pluck="name"):
        frappe.db.delete("Applicant Fee Payment", {"applicant": a})
        frappe.db.delete("Applicant Fee", {"applicant": a})
        frappe.db.delete("Admission Applicant Transition Log", {"applicant": a})
        frappe.db.delete("Admission Note Change Log", {"applicant": a})
        frappe.db.delete("Admission Applicant", {"name": a})
    frappe.db.delete("Admission Session", {"session_code": ["like", _MARK + "%"]})
    frappe.db.commit()


def _mk(code, state="Open", opens=None, closes=None, exam=None, room=None, call=None, start=None,
        prog="ZAMELP", ay="2026-2027"):
    d = {"doctype": "Admission Session", "session_code": f"{_MARK}-{code}",
         "label": f"Amel {code}", "programme_code": prog, "programme_label": "Prog Amel",
         "academic_year": ay, "opens_on": opens or "2026-06-01",
         "closes_on": closes or add_days(nowdate(), 60), "bac_results_date": "2026-07-15",
         "application_fee_xof": 10000, "lifecycle_state": state, "exam_date": exam,
         "exam_room": room}
    if call:
        d["exam_call_time"] = call
    if start:
        d["exam_start_time"] = start
    return frappe.get_doc(d).insert(ignore_permissions=True, ignore_mandatory=True)


def _log(session, **flt):
    return frappe.get_all("Admission Session Change Log",
                          filters={"session": session, **flt},
                          fields=["champ", "old_value", "new_value", "action_type"],
                          order_by="creation asc")


class TestCalAmel(FrappeTestCase):
    def setUp(self):
        _purge()

    def tearDown(self):
        frappe.db.rollback()
        _purge()

    # ── DEC-P : les 6 actes laissent une trace ──
    def test_journal_ouverture_et_maker_checker(self):
        s = _mk("MC", state="Draft", closes=add_days(nowdate(), 30), exam=add_days(nowdate(), 60),
                call="07:30:00", start="08:00:00")
        calendar._open_session(s.name)
        self.assertEqual([(r.champ, r.old_value, r.new_value) for r in _log(s.name, action_type="ouverture")],
                         [("lifecycle_state", "Draft", "Open")])
        newc = str(add_days(nowdate(), 37))
        calendar._propose_changes(s.name, {"closes_on": newc})
        prop = _log(s.name, action_type="proposition")
        self.assertEqual((prop[0].champ, prop[0].new_value), ("closes_on", newc))
        calendar._validate_changes(s.name)
        val = _log(s.name, action_type="validation")
        self.assertEqual((val[0].champ, val[0].new_value), ("closes_on", newc))
        # rejet : nouvelle proposition puis écartée — la trace survit à la purge du pending
        newc2 = str(add_days(nowdate(), 44))
        calendar._propose_changes(s.name, {"closes_on": newc2})
        calendar._reject_changes(s.name)
        rej = _log(s.name, action_type="rejet")
        self.assertEqual((rej[0].champ, rej[0].new_value), ("closes_on", newc2))
        self.assertEqual(len(frappe.get_doc("Admission Session", s.name).pending_changes), 0)

    def test_journal_duplication_et_autofermeture_avec_decq(self):
        src = _mk("SRC", state="Open", closes=add_days(nowdate(), -1))   # échue → auto-fermeture
        # DEC-Q : 1 dossier bloqué-paiement-en-attente (BRO sans frais payé)
        frappe.get_doc({"doctype": "Admission Applicant", "status": "BRO", "first_name": "Blo",
                        "last_name": "Que", "email": f"blo.{_MARK}@t.bj", "phone": "+22990000000",
                        "programme_code": "ZAMELP", "programme_label": "Prog Amel",
                        "level_code": "PRE-A1", "session": src.name,
                        }).insert(ignore_permissions=True, ignore_mandatory=True)
        created = calendar._create_duplicates([src.name], 364, None)["created"]
        dup = _log(created[0], action_type="duplication")
        self.assertEqual((dup[0].old_value, dup[0].new_value), (src.name, created[0]))
        res = sessions.close_expired_sessions()
        mine = [c for c in res["closed"] if c["session"] == src.name]
        self.assertEqual(len(mine), 1)
        self.assertEqual(mine[0]["bloques_paiement"], 1)                     # DEC-Q compté ET loggé
        auto = _log(src.name, action_type="auto-fermeture")
        self.assertEqual([(auto[0].old_value, auto[0].new_value)], [("Open", "Closed")])

    # ── DEC-N : les 3 familles du bandeau, et l'absence quand rien ne presse ──
    def test_a_traiter_families(self):
        _mk("F1", state="Open", closes=add_days(nowdate(), 6))
        _mk("F2", state="Open", closes=add_days(nowdate(), 2), exam=add_days(nowdate(), 10))  # sans salle/heures (A07 : closes < exam)
        f3 = _mk("F3", state="Open", closes=add_days(nowdate(), 50), exam=add_days(nowdate(), 80),
                 call="07:30:00", start="08:00:00", room="A")
        calendar._propose_changes(f3.name, {"closes_on": str(add_days(nowdate(), 55))})
        frappe.db.sql("""UPDATE `tabAdmission Session Pending Change`
                         SET requested_on = %s WHERE parent = %s""",
                      (add_days(nowdate(), -8), f3.name))
        frappe.db.commit()
        data = calendar_view.calendar_list()["data"]
        mine = [i for i in data["a_traiter"] if i["session"].startswith(_MARK)]
        fams = {i["famille"] for i in mine}
        self.assertEqual(fams, {"cloture", "epreuve", "pending"})
        self.assertTrue([i for i in mine if i["famille"] == "cloture" and i["jours"] == 6])
        ep = [i for i in mine if i["famille"] == "epreuve"][0]
        self.assertIn("salle", ep["detail"])
        self.assertTrue(all("lien" in i for i in mine))
        # rien ne presse → aucune entrée (le front masque le bandeau si vide)
        _purge()
        _mk("CALM", state="Open", closes=add_days(nowdate(), 60))
        data = calendar_view.calendar_list()["data"]
        self.assertEqual([i for i in data["a_traiter"] if i["session"].startswith(_MARK)], [])

    # ── AMEL-06-b : chaînage dérivé ──
    def test_next_session_derivation(self):
        a = _mk("N1", opens="2026-06-01", closes="2026-08-01")
        b = _mk("N2", opens="2026-09-01", closes="2026-11-01")
        det = calendar_view.session_detail(session=a.name)["data"]
        self.assertEqual(det["next_session"]["session"], b.name)
        det = calendar_view.session_detail(session=b.name)["data"]
        self.assertIsNone(det["next_session"])

    # ── DEC-O : idempotence + collapse anti-rafale ──
    def test_reminders_idempotents(self):
        _mk("R1", state="Open", closes=add_days(nowdate(), 6))                       # → cloture_j7
        _mk("R2", state="Open", closes=nowdate(),
            exam=add_days(nowdate(), 1))                                             # → epreuve_j7 (+j14 collapse)
        r1 = calendar_reminders.send_calendar_reminders()
        self.assertGreaterEqual(r1["cloture"], 1)
        self.assertGreaterEqual(r1["epreuve"], 1)
        marks = frappe.get_all("Admission Session Reminder",
                               filters={"session": ["like", _MARK + "%"]}, pluck="jalon")
        self.assertIn("cloture_j7", marks)
        self.assertIn("epreuve_j7", marks)
        self.assertIn("epreuve_j14", marks)   # collapse : posé SANS second envoi
        n_before = len(marks)
        r2 = calendar_reminders.send_calendar_reminders()   # REJEU → no-op
        self.assertEqual([r2["cloture"], r2["epreuve"]], [0, 0])
        self.assertEqual(len(frappe.get_all("Admission Session Reminder",
                                            filters={"session": ["like", _MARK + "%"]})), n_before)
