"""NOTES-FIX-0 — contrat fichier CSV (C3/C4/C6, DEC-I/DEC-K).

TRAÇABILITÉ AUDIT → TESTS : l'audit NOTES-AUDIT prouvait « 3 états » d'aller-retour —
(a) export→réimport pur, (b) mangle Excel-Mac complet, (c) fichier de preuve réel. DEC-I
redéfinit l'attendu : le mojibake (données altérées irréversiblement) est REJETÉ avec message,
jamais réparé. Les 3 états deviennent donc 4 CAS :
  (a)  aller-retour pur                     → 100 % écrit (contrat minimal)
  (b') mangle MÉCANIQUE seul (BOM + « ; » + zéros strippés, accents intacts) → 100 %
       ← LE cas décisif du lot : la mécanique est tolérée (C3)
  (b)  mangle complet (mécanique + mojibake) → rejet CSV_MOJIBAKE (DEC-I)
  (c)  représentant du fichier de preuve réel → rejet CSV_MOJIBAKE
       (le fichier RÉEL est rejoué en PROD à chaque déploiement du lot, aperçu write=False)

DB réelle (patron test_calendar / _recette_notes). _MARK = ZZNCSV.
"""

import json

import frappe
from frappe.tests.utils import FrappeTestCase

from admission.api import staff

_MARK = "ZZNCSV"
_SESSION = f"{_MARK}-SES"


def _purge():
    frappe.set_user("Administrator")
    for a in frappe.get_all("Admission Applicant", filters={"session": ["like", _SESSION + "%"]}, pluck="name"):
        frappe.db.delete("Applicant Fee Payment", {"applicant": a})
        frappe.db.delete("Applicant Fee", {"applicant": a})
        frappe.db.delete("Admission Applicant Transition Log", {"applicant": a})
        frappe.db.delete("Admission Applicant", {"name": a})
    frappe.db.delete("Admission Session", {"session_code": ["like", _SESSION + "%"]})
    frappe.db.commit()


class TestNotesCsvContract(FrappeTestCase):
    def setUp(self):
        _purge()
        frappe.get_doc({
            "doctype": "Admission Session", "session_code": _SESSION, "label": "CSV contrat",
            "programme_code": "PREPA", "programme_label": "Cycle Préparatoire",
            "academic_year": "2026-2027", "opens_on": "2026-08-01", "closes_on": "2026-08-20",
            "bac_results_date": "2026-07-15", "application_fee_xof": 15000,
            "lifecycle_state": "Closed", "is_prepa_session": 1, "exam_date": "2026-08-26",
            "exam_call_time": "07:30:00", "exam_start_time": "08:00:00",
        }).insert(ignore_permissions=True)
        # 3 convoqués : zéro de tête au numéro / SANS numéro (« — » à l'écran) / accents au nom
        self.a1 = self._candidat("Aime", "Sero", "08260001")
        self.a2 = self._candidat("Bela", "Toko", None)
        self.a3 = self._candidat("Cesaire", "Essogbé", "08260003")
        staff.set_exam_coefficients(session_id=_SESSION,
                                    coefficients={"maths": 3, "physique": 2, "culture": 1})
        frappe.db.commit()

    def tearDown(self):
        frappe.db.rollback()
        _purge()

    def _candidat(self, prenom, nom, num):
        a = frappe.get_doc({
            "doctype": "Admission Applicant", "status": "BRO", "first_name": prenom,
            "last_name": nom, "email": f"{prenom}.{_MARK}@t.bj".lower(), "phone": "+22990000000",
            "programme_code": "PREPA", "programme_label": "Cycle Préparatoire",
            "level_code": "PRE-A1", "session": _SESSION,
            **({"convocation_number": num} if num else {}),
        }).insert(ignore_permissions=True, ignore_mandatory=True)
        frappe.db.set_value("Admission Applicant", a.name, "status", "ETU", update_modified=False)
        fee = frappe.get_doc({"doctype": "Applicant Fee", "applicant": a.name, "session": _SESSION,
                              "fee_type": "competition", "amount_xof": 15000, "status": "Paid"}
                             ).insert(ignore_permissions=True)
        frappe.get_doc({"doctype": "Applicant Fee Payment", "applicant_fee": fee.name,
                        "applicant": a.name, "payment_mode": "Cash", "amount_xof": 15000,
                        "payment_status": "Confirmed", "justificatif": "/x.pdf"}
                       ).insert(ignore_permissions=True)
        return a.name

    def _export(self):
        return staff.export_notes_template(session_id=_SESSION)["data"]["csv"]

    def _fill(self, csv_text):
        """Remplit les 3 notes de chaque ligne en ÉDITION TEXTE PURE (sans tableur)."""
        lines = csv_text.lstrip("﻿").strip().split("\r\n")
        out = [lines[0]]
        for ln in lines[1:]:
            self.assertTrue(ln.endswith(",,,,"), f"gabarit inattendu : {ln!r}")
            out.append(ln[:-4] + ",14,13,12,")
        return "\r\n".join(out)

    # ── C6 : forensique export — n'exporter que ce qu'on sait réimporter ──
    def test_export_forensics(self):
        exp = self._export()
        self.assertTrue(exp.startswith("﻿"), "BOM UTF-8 absent (NT-02)")
        self.assertNotIn("—", exp, "placeholder d'affichage exporté comme donnée (NT-04)")
        # csv.writer encode `="08260001"` en `"=""08260001"""` (quoting standard) — c'est la
        # forme BRUTE dans le fichier ; Excel la lit comme formule texte, notre parse la déballe.
        self.assertIn('"=""08260001"""', exp, "numéro non protégé contre la coercition Excel (DEC-K)")

    # ── (a) : aller-retour pur = contrat minimal, 100 % ──
    def test_a_roundtrip_pure(self):
        filled = self._fill(self._export())
        prev = staff.import_notes_preview(session_id=_SESSION, csv_text=filled)["data"]
        self.assertEqual(prev["compte_a_ecrire"], 3, prev["problemes"])
        self.assertEqual(prev["problemes"], [])
        res = staff.saisir_notes_masse(session_id=_SESSION, csv_text=filled)["data"]
        self.assertEqual(res["ecrits"], 3)
        stored = json.loads(frappe.db.get_value("Admission Applicant", self.a1, "notes_concours"))
        self.assertEqual(stored["maths"], 14.0)

    # ── (b') : mangle MÉCANIQUE seul → 100 % (le cas décisif du lot) ──
    def test_b_prime_mechanical_mangle_passes(self):
        filled = self._fill(self._export())
        # simulation Excel FR : protection évaluée (plain), zéros strippés, « ; », BOM conservé
        mech = filled.replace('"=""08260001"""', "8260001").replace('"=""08260003"""', "8260003")
        mech = "﻿" + mech.replace(",", ";")
        prev = staff.import_notes_preview(session_id=_SESSION, csv_text=mech)["data"]
        self.assertEqual(prev["compte_a_ecrire"], 3, prev["problemes"])
        self.assertEqual(prev["problemes"], [])

    # ── DEC-K : le numéro seul (sans dossier_id), zéros strippés, matche quand même ──
    def test_zeros_tolerance_numero_only(self):
        csv_text = "numero_convocation,maths,physique,culture,absent\r\n8260001,10,11,12,\r\n"
        prev = staff.import_notes_preview(session_id=_SESSION, csv_text=csv_text)["data"]
        self.assertEqual(prev["compte_a_ecrire"], 1, prev["problemes"])

    # ── Exigence A (pause) : le déballage `="…"` vaut pour TOUTE colonne identifiante ──
    def test_excel_guard_unwrapped_on_dossier_id(self):
        csv_text = f'dossier_id,maths,physique,culture,absent\r\n"=""{self.a1}""",10,11,12,\r\n'
        prev = staff.import_notes_preview(session_id=_SESSION, csv_text=csv_text)["data"]
        self.assertEqual(prev["compte_a_ecrire"], 1, prev["problemes"])

    # ── (b) : mangle complet (mojibake) → rejet DEC-I, aperçu ET écriture ──
    def test_b_full_mangle_rejected(self):
        filled = self._fill(self._export())
        moji = "﻿" + filled.replace(",", ";").replace("é", "√©")   # Essogbé → Essogb√©
        r = staff.import_notes_preview(session_id=_SESSION, csv_text=moji)
        self.assertFalse(r.get("ok"))
        self.assertEqual(r["error"]["code"], "CSV_MOJIBAKE")
        r = staff.saisir_notes_masse(session_id=_SESSION, csv_text=moji)
        self.assertFalse(r.get("ok"), "l'écriture doit porter la MÊME garde que l'aperçu")
        self.assertEqual(r["error"]["code"], "CSV_MOJIBAKE")

    # ── (c) : représentant du fichier de preuve réel (BOM + ; + √© + ‚Äî) → rejet DEC-I ──
    def test_c_proof_file_representative_rejected(self):
        csv_text = ("﻿dossier_id;numero_convocation;nom;maths;physique;culture;absent\r\n"
                    f"{self.a3};8260003;Essogb√© C√©saire;12;15;11;\r\n"
                    f"{self.a2};‚Äî;Bela Toko;10;18;6;\r\n")
        r = staff.import_notes_preview(session_id=_SESSION, csv_text=csv_text)
        self.assertFalse(r.get("ok"))
        self.assertEqual(r["error"]["code"], "CSV_MOJIBAKE")
        self.assertIn("réexportez le gabarit", r["error"]["message"].lower())
