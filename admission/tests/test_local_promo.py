"""Tests PROMO-LOCALE -- moteur de calcul (DEC-343/344).

Bornes INCLUSES ; cumul multiplicatif ; non-cumul best-of ; code seul sur plein tarif.
Exemple de reference (DEC-344) : licence 600 000, campagne 380 000 jusqu'au 25,
code -10% jusqu'au 17 -> 15: 342 000 ; 18: 380 000 ; 26: 600 000.
"""

from __future__ import annotations

import json
from datetime import date
from unittest import TestCase
from unittest.mock import MagicMock, patch

LP = "admission.api.local_promo"

CAMPAIGN = ("LPROMO-0001", "Promo rentree", 380000.0)
CODE_CUMUL = {"name": "COMEEXPRESS2026", "rate": 0.10, "cumulable": 1,
              "start_date": date(2026, 9, 1), "end_date": date(2026, 9, 17), "active": 1}
CODE_NONCUMUL = dict(CODE_CUMUL, cumulable=0)


def _compute(on_date, code_row=None, campaign=CAMPAIGN, base=600000.0):
    with patch(f"{LP}._campaign_price_for", return_value=campaign), \
         patch(f"{LP}._resolve_code", return_value=code_row), \
         patch("admission.api.public._resolve_fee_from_catalog", return_value=base):
        from admission.api.local_promo import compute_local_price
        return compute_local_price("LIS", "DEFAULT", on_date,
                                   code=code_row["name"] if code_row else None)


class TestComputeLocalPrice(TestCase):
    def test_cumul_le_15_donne_342000(self):
        r = _compute(date(2026, 9, 15), CODE_CUMUL)
        self.assertEqual(r["final_annual_xof"], 342000.0)
        self.assertTrue(r["code_applied"])

    def test_borne_incluse_le_17_code_encore_valide(self):
        r = _compute(date(2026, 9, 17), CODE_CUMUL)
        self.assertEqual(r["final_annual_xof"], 342000.0)

    def test_code_expire_le_18_campagne_seule_380000(self):
        r = _compute(date(2026, 9, 18), CODE_CUMUL)
        self.assertEqual(r["final_annual_xof"], 380000.0)
        self.assertFalse(r["code_applied"])

    def test_campagne_borne_incluse_le_25(self):
        r = _compute(date(2026, 9, 25))
        self.assertEqual(r["final_annual_xof"], 380000.0)

    def test_tout_expire_le_26_plein_tarif(self):
        r = _compute(date(2026, 9, 26), campaign=(None, None, None))
        self.assertEqual(r["final_annual_xof"], 600000.0)
        self.assertIsNone(r["campaign"])

    def test_noncumul_pendant_campagne_best_of_garde_380000(self):
        # min(380000, 600000*0.90=540000) = 380000 (DEC-344)
        r = _compute(date(2026, 9, 15), CODE_NONCUMUL)
        self.assertEqual(r["final_annual_xof"], 380000.0)
        self.assertFalse(r["code_applied"])

    def test_noncumul_hors_campagne_code_applique_540000(self):
        r = _compute(date(2026, 9, 15), CODE_NONCUMUL, campaign=(None, None, None))
        self.assertEqual(r["final_annual_xof"], 540000.0)
        self.assertTrue(r["code_applied"])

    def test_code_seul_cumulable_hors_campagne_540000(self):
        r = _compute(date(2026, 9, 15), CODE_CUMUL, campaign=(None, None, None))
        self.assertEqual(r["final_annual_xof"], 540000.0)

    def test_base_absente_catalog_miss_final_none_sans_crash(self):
        r = _compute(date(2026, 9, 15), CODE_CUMUL, base=None)
        self.assertIsNone(r["final_annual_xof"])


class TestCodeValidOn(TestCase):
    def test_bornes_incluses(self):
        from admission.api.local_promo import _code_valid_on
        self.assertTrue(_code_valid_on(CODE_CUMUL, date(2026, 9, 1)))
        self.assertTrue(_code_valid_on(CODE_CUMUL, date(2026, 9, 17)))
        self.assertFalse(_code_valid_on(CODE_CUMUL, date(2026, 8, 31)))
        self.assertFalse(_code_valid_on(CODE_CUMUL, date(2026, 9, 18)))


class TestValidatePromoCode(TestCase):
    def _call(self, code, code_row=None, valid_window=True):
        with patch(f"{LP}._resolve_code", return_value=code_row), \
             patch(f"{LP}._code_valid_on", return_value=valid_window), \
             patch(f"{LP}.compute_local_price", return_value={
                 "base_xof": 600000.0, "campaign": "LPROMO-0001",
                 "campaign_label": "Promo rentree", "campaign_price_xof": 380000.0,
                 "code": "COMEEXPRESS2026", "code_rate": 0.10, "code_applied": True,
                 "cumulable": True, "final_annual_xof": 342000.0}):
            from admission.api.local_promo import _validate_promo_code_impl
            return _validate_promo_code_impl(code, "LIS", "DEFAULT")

    def test_code_valide_renvoie_effet(self):
        r = self._call("comeexpress2026", CODE_CUMUL, True)
        self.assertTrue(r["valid"])
        self.assertEqual(r["estimation"]["final_annual_xof"], 342000.0)
        self.assertEqual(r["end_date"], "2026-09-17")

    def test_code_inconnu_reponse_generique(self):
        r = self._call("NIMPORTEQUOI", None, False)
        self.assertEqual(r, {"valid": False, "message": "Code invalide ou expire."})

    def test_code_expire_MEME_reponse_generique(self):
        # Anti-enumeration DEC-243/344 : expire indistinguable d'inconnu.
        r = self._call("comeexpress2026", CODE_CUMUL, False)
        self.assertEqual(r, {"valid": False, "message": "Code invalide ou expire."})


class TestSetPromoCode(TestCase):
    def _call(self, code, snapshot=None):
        applicant = type("A", (), {"name": "CAN-2026-00001",
                                   "local_promo_snapshot": snapshot,
                                   "programme_code": "LIS", "level_code": "DEFAULT"})()
        with patch("admission.api.public._get_applicant", return_value=applicant), \
             patch(f"{LP}._validate_promo_code_impl", return_value={"valid": True}), \
             patch(f"{LP}.frappe") as mf:
            from admission.api.local_promo import _set_promo_code_impl
            return _set_promo_code_impl("CAN-2026-00001", "tok", code), mf, applicant

    def test_pose_le_code_normalise(self):
        res, mf, a = self._call("  comeexpress2026 ")
        mf.db.set_value.assert_called_once_with(
            "Admission Applicant", "CAN-2026-00001",
            "entered_promo_code", "COMEEXPRESS2026", update_modified=False)
        self.assertTrue(res["validation"]["valid"])

    def test_code_vide_efface(self):
        res, mf, a = self._call("")
        mf.db.set_value.assert_called_once_with(
            "Admission Applicant", "CAN-2026-00001",
            "entered_promo_code", "", update_modified=False)

    def test_refuse_apres_gel(self):
        res, mf, a = self._call("X", snapshot='{"final_annual_xof": 342000}')
        self.assertFalse(res["ok"])
        self.assertEqual(res["error"]["code"], "PROMO_LOCKED")
        mf.db.set_value.assert_not_called()


class TestCaptureLocalPromo(TestCase):
    def _applicant(self, snapshot=None, entered="COMEEXPRESS2026"):
        a = MagicMock()
        a.name = "CAN-2026-00001"; a.local_promo_snapshot = snapshot
        a.entered_promo_code = entered; a.programme_code = "LIS"; a.level_code = "DEFAULT"
        return a

    def test_gel_ecrit_snapshot_et_denormalise(self):
        a = self._applicant()
        computed = {"base_xof": 600000.0, "campaign": "LPROMO-0001",
                    "campaign_label": "Promo rentree", "campaign_price_xof": 380000.0,
                    "code": "COMEEXPRESS2026", "code_rate": 0.10, "code_applied": True,
                    "cumulable": True, "final_annual_xof": 342000.0}
        with patch(f"{LP}.compute_local_price", return_value=computed), \
             patch(f"{LP}.frappe"):
            from admission.api.local_promo import capture_local_promo_if_eligible
            capture_local_promo_if_eligible(a)
        self.assertEqual(a.final_annual_xof, 342000.0)
        snap = json.loads(a.local_promo_snapshot)
        self.assertEqual(snap["final_annual_xof"], 342000.0)
        self.assertEqual(snap["code"], "COMEEXPRESS2026")
        a.save.assert_called_once_with(ignore_permissions=True)

    def test_idempotent_snapshot_deja_pose(self):
        a = self._applicant(snapshot='{"final_annual_xof": 342000}')
        with patch(f"{LP}.compute_local_price") as mcalc:
            from admission.api.local_promo import capture_local_promo_if_eligible
            capture_local_promo_if_eligible(a)
        mcalc.assert_not_called(); a.save.assert_not_called()

    def test_rien_d_actif_aucune_ecriture(self):
        a = self._applicant(entered="")
        computed = {"base_xof": 600000.0, "campaign": None, "campaign_label": None,
                    "campaign_price_xof": None, "code": None, "code_rate": 0.0,
                    "code_applied": False, "cumulable": False, "final_annual_xof": 600000.0}
        with patch(f"{LP}.compute_local_price", return_value=computed), \
             patch(f"{LP}.frappe"):
            from admission.api.local_promo import capture_local_promo_if_eligible
            capture_local_promo_if_eligible(a)
        a.save.assert_not_called()

    def test_cascade_appelle_la_capture_pour_frais1(self):
        # Le point unique DEC-272 appelle la capture LOCALE a cote de la capture miroir.
        import inspect
        from admission.api import public
        src = inspect.getsource(public.apply_confirmed_payment_cascade)
        self.assertIn("capture_local_promo_if_eligible", src)
        self.assertIn("_capture_promo_if_eligible", src)


class TestExposition(TestCase):
    def test_build_frais_data_expose_promotion_locale(self):
        import inspect
        from admission.api import public
        src = inspect.getsource(public._build_frais_data)
        self.assertIn('"promotion_locale"', src)

    def test_frais_promotion_locale_shape(self):
        from admission.api.local_promo import build_promotion_locale_section
        with patch(f"{LP}._campaign_price_for", return_value=CAMPAIGN), \
             patch(f"{LP}.frappe") as mf:
            mf.get_all.return_value = [
                {"name": "LPROMO-0001", "label": "Promo rentree", "end_date": date(2026, 9, 25)}]
            out = build_promotion_locale_section("LIS", "DEFAULT")
        self.assertEqual(out["promo_annual_xof"], 380000.0)
        self.assertEqual(out["end_date"], "2026-09-25")

    def test_frais_sans_campagne_renvoie_none(self):
        from admission.api.local_promo import build_promotion_locale_section
        with patch(f"{LP}._campaign_price_for", return_value=(None, None, None)):
            self.assertIsNone(build_promotion_locale_section("LIS", "DEFAULT"))

    def test_get_dossier_expose_promo_locale(self):
        # Le payload candidat vit dans _serialize_dossier (get_dossier delegue).
        import inspect
        from admission.api import public
        src = inspect.getsource(public._serialize_dossier)
        self.assertIn('"promo_locale"', src)

    def test_staff_get_dossier_expose_locale(self):
        import inspect
        from admission.api import staff
        src = inspect.getsource(staff.get_dossier)
        self.assertIn('"locale"', src)
