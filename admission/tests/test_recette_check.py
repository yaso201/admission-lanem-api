"""Tests RECETTE-GATE — garde la cohérence du vérificateur de recette.

Le gate (api/recette_check.py) est l'outillage de sortie de DEV : ces tests
verrouillent sa structure (ids uniques, contrôles appelables) et les règles
les plus sensibles (CORS localhost refusé, developer_mode refusé, secrets
jamais affichés). Style unitaire mocké.
"""

from unittest import TestCase
from unittest.mock import patch

import frappe as _real_frappe


def setUpModule():
    try:
        _real_frappe.local.flags
    except Exception:
        _real_frappe.local.flags = _real_frappe._dict(in_test=True)


RC = "admission.api.recette_check"


class TestChecklistStructure(TestCase):
    def test_ids_uniques_et_controles_appelables(self):
        from admission.api.recette_check import CHECKS
        ids = [c[0] for c in CHECKS]
        self.assertEqual(len(ids), len(set(ids)), "ids de contrôles dupliqués")
        for check_id, label, fn in CHECKS:
            self.assertTrue(callable(fn), f"{check_id} : contrôle non appelable")
            self.assertTrue(label, f"{check_id} : libellé vide")

    def test_jobs_scheduler_alignes_sur_hooks(self):
        # Anti-dérive : la liste du gate doit refléter hooks.scheduler_events.daily
        from admission import hooks
        from admission.api.recette_check import SCHEDULER_JOBS
        self.assertEqual(set(SCHEDULER_JOBS), set(hooks.scheduler_events["daily"]))


class TestReglesSensibles(TestCase):
    @patch(f"{RC}.frappe")
    def test_cors_localhost_refuse(self, mf):
        mf.conf.get.return_value = ["http://localhost:4321"]
        from admission.api.recette_check import _check_cors
        status, detail = _check_cors()
        self.assertEqual(status, "FAIL")
        self.assertIn("localhost", detail)

    @patch(f"{RC}.frappe")
    def test_cors_origine_https_acceptee(self, mf):
        mf.conf.get.return_value = ["https://candidature.lanem.bj"]
        from admission.api.recette_check import _check_cors
        self.assertEqual(_check_cors()[0], "PASS")

    @patch(f"{RC}.frappe")
    def test_developer_mode_refuse(self, mf):
        mf.conf.get.return_value = 1
        from admission.api.recette_check import _check_dev_mode
        self.assertEqual(_check_dev_mode()[0], "FAIL")

    @patch(f"{RC}.frappe")
    def test_url_http_refusee_https_acceptee(self, mf):
        from admission.api.recette_check import _check_url
        mf.conf.get.return_value = "http://campus.lanem.bj"
        self.assertEqual(_check_url("campus_base_url", "x")()[0], "FAIL")
        mf.conf.get.return_value = "https://campus.lanem.bj"
        self.assertEqual(_check_url("campus_base_url", "x")()[0], "PASS")

    @patch(f"{RC}.frappe")
    def test_secret_present_jamais_affiche(self, mf):
        mf.conf.get.return_value = "SUPER-SECRET-VALEUR"
        from admission.api.recette_check import _check_secret
        status, detail = _check_secret("token_hmac_secret", "x")()
        self.assertEqual(status, "PASS")
        self.assertNotIn("SUPER-SECRET-VALEUR", detail)  # présence seulement, jamais la valeur
