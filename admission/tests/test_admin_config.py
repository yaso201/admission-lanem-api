"""LOT C (SM BACK-OFFICE) — réglages non-secrets & diagnostic (`admin_config.py`).

D1 : aucun secret lu/écrit. get_config_health = booléens présent/absent (jamais de valeur) ;
get_settings/update_settings = cloisonnement + rétention ; tout champ secret/hors-liste REFUSÉ.
Réf : SPEC-ADMISSION-SM-BACKOFFICE §4 (C).
"""

import frappe
from frappe.tests.utils import FrappeTestCase

from admission.api import admin_config as CFG


class TestAdminConfig(FrappeTestCase):

    def test_health_returns_booleans_no_secret_values(self):
        res = CFG.get_config_health()
        self.assertTrue(res["ok"])
        d = res["data"]
        for grp in ("campus", "uf", "kkiapay", "hmac_secret", "webhook_secret", "smtp"):
            self.assertIn("present", d[grp])
            self.assertIsInstance(d[grp]["present"], bool)
        # aucune valeur de secret ne doit transiter (pas de clé "value"/"token"/"secret_value")
        blob = frappe.as_json(d).lower()
        self.assertNotIn("api_secret", blob)
        self.assertNotIn("private_key", blob)

    def test_get_settings_shape(self):
        res = CFG.get_settings()
        self.assertTrue(res["ok"])
        self.assertIn("consultation_cloisonnee", res["data"]["cloisonnement"])
        self.assertIn("abandoned_bro_days", res["data"]["retention"])

    def test_update_rejects_secret_or_out_of_scope_field(self):
        res = CFG.update_settings(cloisonnement={"rib_iban": "BJ.."})
        self.assertEqual(res["error"]["code"], "FIELD_NOT_ALLOWED")

    def test_update_retention_persists(self):
        res = CFG.update_settings(retention={"abandoned_bro_days": 77})
        self.assertTrue(res["ok"])
        self.assertEqual(
            int(frappe.db.get_single_value("Admission Retention Policy", "abandoned_bro_days")), 77)

    def test_update_cloisonnement_persists(self):
        res = CFG.update_settings(cloisonnement={"consultation_axis": "session"})
        self.assertTrue(res["ok"])
        self.assertEqual(
            frappe.db.get_single_value("Admission Settings", "consultation_axis"), "session")
