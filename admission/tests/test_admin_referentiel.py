"""LOT D (SM BACK-OFFICE) — référentiel en mode dégradé (`admin_referentiel.py`).

D3 « campus gagne, on prévient » : édition manuelle bornée (mode dégradé ON + source=Manuel) ;
ligne source=Campus VERROUILLÉE ; une sync campus écrase l'override manuel et émet une alerte.
Réf : SPEC-ADMISSION-SM-BACKOFFICE §4 (D).
"""

from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from admission.api import admin_referentiel as R
from admission.api import catalogue_sync as CS

CODE = "LD-TST-PROG"


class TestAdminReferentiel(FrappeTestCase):

    def _cleanup(self):
        if frappe.db.exists("Admission Programme", CODE):
            frappe.delete_doc("Admission Programme", CODE, force=True, ignore_permissions=True)

    def setUp(self):
        self._cleanup()
        self.addCleanup(self._cleanup)
        frappe.db.set_value("Admission Settings", "Admission Settings", "degraded_mode", 0)

    # ── mode dégradé ──
    def test_set_degraded_requires_motif_on_enable(self):
        self.assertEqual(R.set_degraded_mode(on=1)["error"]["code"], "MOTIF_REQUIRED")

    def test_set_degraded_enables(self):
        res = R.set_degraded_mode(on=1, motif="campus serveur 2 indisponible")
        self.assertTrue(res["data"]["degraded_mode"])
        self.assertEqual(int(frappe.db.get_single_value("Admission Settings", "degraded_mode")), 1)

    def test_status_shape(self):
        res = R.get_degraded_status()
        self.assertIn("manual_count", res["data"])
        self.assertIn("campus_count", res["data"])

    # ── édition manuelle ──
    def test_manual_upsert_refused_when_degraded_off(self):
        res = R.upsert_manual_programme(programme={"programme_code": CODE, "title": "X", "parcours": "Licence"})
        self.assertEqual(res["error"]["code"], "DEGRADED_OFF")

    def test_manual_upsert_creates_source_manuel(self):
        frappe.db.set_value("Admission Settings", "Admission Settings", "degraded_mode", 1)
        res = R.upsert_manual_programme(programme={"programme_code": CODE, "title": "Licence locale", "parcours": "Licence"})
        self.assertTrue(res["ok"])
        self.assertEqual(frappe.db.get_value("Admission Programme", CODE, "source"), "Manuel")

    def test_manual_upsert_rejects_bad_parcours(self):
        frappe.db.set_value("Admission Settings", "Admission Settings", "degraded_mode", 1)
        res = R.upsert_manual_programme(programme={"programme_code": CODE, "title": "X", "parcours": "Doctorat"})
        self.assertEqual(res["error"]["code"], "PARCOURS_INVALID")

    def test_manual_upsert_locked_by_campus(self):
        frappe.get_doc({"doctype": "Admission Programme", "programme_code": CODE,
                        "title": "Campus", "parcours": "Licence", "source": "Campus"}).insert(ignore_permissions=True)
        frappe.db.set_value("Admission Settings", "Admission Settings", "degraded_mode", 1)
        res = R.upsert_manual_programme(programme={"programme_code": CODE, "title": "Hack", "parcours": "Licence"})
        self.assertEqual(res["error"]["code"], "LOCKED_BY_CAMPUS")

    # ── D3 : campus gagne, on prévient ──
    def test_sync_overwrites_manual_and_alerts(self):
        frappe.get_doc({"doctype": "Admission Programme", "programme_code": CODE,
                        "title": "Local", "parcours": "Licence", "source": "Manuel"}).insert(ignore_permissions=True)
        overwritten = []
        with patch.object(CS, "_alert_manual_overwritten"):  # alerte testée ailleurs ; ici on vérifie la collecte
            CS._upsert_catalogue(
                [{"programme_code": CODE, "title": "Campus", "parcours": "Licence", "is_active": 1}],
                overwritten=overwritten)
        self.assertIn(CODE, overwritten)  # override manuel détecté
        self.assertEqual(frappe.db.get_value("Admission Programme", CODE, "source"), "Campus")  # reverrouillé
