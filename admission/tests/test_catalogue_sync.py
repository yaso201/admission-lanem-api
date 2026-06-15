"""Lot 5 — catalogue_sync (admission ← campus).

Vérifie la dormance (config absente → skip) et l'upsert idempotent du mirror
Admission Programme à partir du payload campus (forme get_catalogue_for_admission),
y compris la résolution des Link dd_component_1/2 (upsert base puis DD).

Ref: SPEC-CAMPUS-CATALOGUE-SOURCE-DE-VERITE §7.
"""

import frappe
from unittest.mock import patch
from frappe.tests.utils import FrappeTestCase

from admission.api import catalogue_sync as cs

PAYLOAD = [
    {"programme_code": "TST-LIC", "title": "Licence Test", "parcours": "Licence",
     "partner": None, "partner_name": None, "location": "Cotonou", "is_active": 1,
     "dd_component_1": None, "dd_component_2": None, "dd_affinity": None},
    {"programme_code": "TST-BACH", "title": "Bachelor Test", "parcours": "Bachelor",
     "partner": "ESIIA", "partner_name": "ESIIA SA", "location": "Cotonou", "is_active": 1,
     "dd_component_1": None, "dd_component_2": None, "dd_affinity": None},
    {"programme_code": "TST-DD", "title": "Licence Test + Bachelor Test",
     "parcours": "Double-Diplomation", "partner": "ESIIA", "partner_name": "ESIIA SA",
     "location": "Cotonou", "is_active": 1,
     "dd_component_1": "TST-LIC", "dd_component_2": "TST-BACH", "dd_affinity": "Recommandé"},
]


class TestCatalogueSync(FrappeTestCase):

    def _cleanup(self, code):
        if frappe.db.exists("Admission Programme", code):
            frappe.delete_doc("Admission Programme", code, force=True)

    def test_dormant_when_campus_not_configured(self):
        with patch.object(cs, "_get_campus_config", return_value=None):
            res = cs.sync_catalogue()
        self.assertEqual(res["status"], "skipped")
        self.assertEqual(res["reason"], "missing_config")

    def test_fetch_rejected_without_programmes_key_is_error(self):
        # Une réponse structurée sans "programmes" = échec explicite (jamais 0 silencieux).
        class _Resp:
            def raise_for_status(self): pass
            def json(self): return {"message": {"error": "scope_denied"}}
        with patch.object(cs, "_get_campus_config", return_value={"url": "https://x", "token": "k"}), \
             patch.object(cs.requests, "get", return_value=_Resp()):
            res = cs.sync_catalogue()
        self.assertEqual(res["status"], "error")

    def test_upsert_creates_mirror_with_resolved_dd_links(self):
        for c in ("TST-LIC", "TST-BACH", "TST-DD"):
            self.addCleanup(self._cleanup, c)

        count = cs._upsert_catalogue(PAYLOAD)
        self.assertEqual(count, 3)

        dd = frappe.get_doc("Admission Programme", "TST-DD")
        self.assertEqual(dd.source, "Campus")
        self.assertEqual(dd.parcours, "Double-Diplomation")
        self.assertEqual(dd.dd_component_1, "TST-LIC")  # Link résolu (base upsert avant DD)
        self.assertEqual(dd.dd_component_2, "TST-BACH")
        self.assertEqual(dd.dd_affinity, "Recommandé")

        bach = frappe.get_doc("Admission Programme", "TST-BACH")
        self.assertEqual(bach.partner, "ESIIA")
        self.assertEqual(bach.is_active, 1)

        # idempotence : re-run ne duplique pas, met à jour
        count2 = cs._upsert_catalogue(PAYLOAD)
        self.assertEqual(count2, 3)
        self.assertEqual(frappe.db.count("Admission Programme", {"programme_code": "TST-DD"}), 1)
