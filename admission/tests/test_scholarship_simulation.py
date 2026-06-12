"""Tests ADM-UF-4 -- Scholarship simulation §3bis + stubs replacement.

Part A: _apply_exclusivity_local
1. Same group keeps highest rate only
2. No group -> all kept (additive)
3. Mixed groups + no group

Part B: _simulate_scholarship_reduction
4. Basic simulation (single scholarship)
5. Exclusivity intra-category (2 Excellence -> max only)
6. Cap at 0.50 (3 categories sum > 0.50)
7. Promos add on top of cap
8. Zero requested -> None

Part C: _build_bourses_section
9. With requested scholarships -> simulation returned
10. No requested -> empty section

Part D: _build_frais_data integration
11. bourses_eligibles populated from mirror
12. Empty mirror -> empty lists

Part E: _get_scholarships_for_programme
13. Groups by category
14. Empty -> empty list

Ref: ADM-UF-4, SPEC-CONTRAT-FINANCE-ADMISSION-UF §3bis.
Gate criteria: exclusivity intra-cat, cap 50%, cumul inter-cat.
"""

from __future__ import annotations

from unittest import TestCase
from unittest.mock import MagicMock, patch


PUB = "admission.api.public"


# -- Part A: _apply_exclusivity_local ------------------------------------------


class TestApplyExclusivityLocal(TestCase):

    def test_same_group_keeps_highest(self):
        from admission.api.public import _apply_exclusivity_local

        scholarships = [
            {"mirror_key": "BOURSE-TRES-BIEN", "scholarship_name": "Tres Bien",
             "category": "Excellence", "rate": 0.30, "exclusivity_group": "excellence"},
            {"mirror_key": "BOURSE-BIEN", "scholarship_name": "Bien",
             "category": "Excellence", "rate": 0.20, "exclusivity_group": "excellence"},
        ]

        result = _apply_exclusivity_local(scholarships)

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["rate"], 0.30)
        self.assertEqual(result[0]["mirror_key"], "BOURSE-TRES-BIEN")

    def test_no_group_all_kept(self):
        from admission.api.public import _apply_exclusivity_local

        scholarships = [
            {"mirror_key": "A", "rate": 0.20, "exclusivity_group": ""},
            {"mirror_key": "B", "rate": 0.15, "exclusivity_group": ""},
        ]

        result = _apply_exclusivity_local(scholarships)

        self.assertEqual(len(result), 2)

    def test_mixed_groups_and_no_group(self):
        from admission.api.public import _apply_exclusivity_local

        scholarships = [
            {"mirror_key": "BOURSE-TRES-BIEN", "rate": 0.30, "exclusivity_group": "excellence"},
            {"mirror_key": "BOURSE-BIEN", "rate": 0.20, "exclusivity_group": "excellence"},
            {"mirror_key": "BOURSE-PARTENAIRE-20", "rate": 0.20, "exclusivity_group": "partenaire"},
            {"mirror_key": "BOURSE-LIBRE", "rate": 0.10, "exclusivity_group": ""},
        ]

        result = _apply_exclusivity_local(scholarships)

        self.assertEqual(len(result), 3)
        keys = {s["mirror_key"] for s in result}
        self.assertIn("BOURSE-TRES-BIEN", keys)
        self.assertIn("BOURSE-PARTENAIRE-20", keys)
        self.assertIn("BOURSE-LIBRE", keys)
        self.assertNotIn("BOURSE-BIEN", keys)


# -- Part B: _simulate_scholarship_reduction -----------------------------------


class TestSimulateScholarshipReduction(TestCase):

    @patch(f"{PUB}._get_promotions_for_programme", return_value=[])
    @patch(f"{PUB}._get_scholarship_cap_local", return_value=0.50)
    @patch(f"{PUB}._resolve_fee_from_catalog")
    @patch(f"{PUB}.frappe")
    def test_basic_simulation(self, mock_frappe, mock_resolve, mock_cap, mock_promos):
        from admission.api.public import _simulate_scholarship_reduction

        mock_frappe.get_all.return_value = [
            MagicMock(
                mirror_key="BOURSE-BIEN", scholarship_name="Mention Bien",
                category="Excellence", rate=0.20, exclusivity_group="excellence",
            ),
        ]
        mock_resolve.side_effect = lambda prog, ft, lc=None: 600000.0 if ft == "annual" and prog == "LIS" else None

        result = _simulate_scholarship_reduction("LIS", ["BOURSE-BIEN"])

        self.assertIsNotNone(result)
        self.assertEqual(result["base_scolarite"], 600000.0)
        self.assertEqual(result["somme_bourses_brute"], 0.20)
        self.assertEqual(result["bourses_plafond"], 0.20)
        self.assertEqual(result["total_reduction"], 0.20)
        self.assertEqual(result["cout_final_estime"], 480000.0)
        self.assertFalse(result["plafond_atteint"])
        self.assertIn("INDICATIVE", result["disclaimer"])

    @patch(f"{PUB}._get_promotions_for_programme", return_value=[])
    @patch(f"{PUB}._get_scholarship_cap_local", return_value=0.50)
    @patch(f"{PUB}._resolve_fee_from_catalog")
    @patch(f"{PUB}.frappe")
    def test_exclusivity_intra_category(self, mock_frappe, mock_resolve, mock_cap, mock_promos):
        from admission.api.public import _simulate_scholarship_reduction

        mock_frappe.get_all.return_value = [
            MagicMock(
                mirror_key="BOURSE-TRES-BIEN", scholarship_name="Tres Bien",
                category="Excellence", rate=0.30, exclusivity_group="excellence",
            ),
            MagicMock(
                mirror_key="BOURSE-BIEN", scholarship_name="Bien",
                category="Excellence", rate=0.20, exclusivity_group="excellence",
            ),
        ]
        mock_resolve.side_effect = lambda prog, ft, lc=None: 600000.0 if ft == "annual" else None

        result = _simulate_scholarship_reduction("LIS", ["BOURSE-TRES-BIEN", "BOURSE-BIEN"])

        self.assertEqual(len(result["bourses_appliquees"]), 1)
        self.assertEqual(result["bourses_appliquees"][0]["rate"], 0.30)
        self.assertEqual(result["somme_bourses_brute"], 0.30)

    @patch(f"{PUB}._get_promotions_for_programme", return_value=[])
    @patch(f"{PUB}._get_scholarship_cap_local", return_value=0.50)
    @patch(f"{PUB}._resolve_fee_from_catalog")
    @patch(f"{PUB}.frappe")
    def test_cap_at_50_percent(self, mock_frappe, mock_resolve, mock_cap, mock_promos):
        """Gate criterion: Tres Bien + partenaire + sociale = 100% -> capped 50%."""
        from admission.api.public import _simulate_scholarship_reduction

        mock_frappe.get_all.return_value = [
            MagicMock(
                mirror_key="BOURSE-TRES-BIEN", scholarship_name="Tres Bien",
                category="Excellence", rate=0.30, exclusivity_group="excellence",
            ),
            MagicMock(
                mirror_key="BOURSE-PARTENAIRE-20", scholarship_name="Enfant partenaire",
                category="Partenariat", rate=0.20, exclusivity_group="partenaire",
            ),
            MagicMock(
                mirror_key="BOURSE-SOCIALE-50", scholarship_name="Sociale maximale",
                category="Sociale", rate=0.50, exclusivity_group="sociale",
            ),
        ]
        mock_resolve.side_effect = lambda prog, ft, lc=None: 600000.0 if ft == "annual" else None

        result = _simulate_scholarship_reduction(
            "LIS", ["BOURSE-TRES-BIEN", "BOURSE-PARTENAIRE-20", "BOURSE-SOCIALE-50"]
        )

        self.assertEqual(result["somme_bourses_brute"], 1.0)
        self.assertEqual(result["bourses_plafond"], 0.50)
        self.assertEqual(result["total_reduction"], 0.50)
        self.assertTrue(result["plafond_atteint"])
        self.assertEqual(result["cout_final_estime"], 300000.0)
        self.assertEqual(result["montant_reduction"], 300000.0)

    @patch(f"{PUB}._get_promotions_for_programme")
    @patch(f"{PUB}._get_scholarship_cap_local", return_value=0.50)
    @patch(f"{PUB}._resolve_fee_from_catalog")
    @patch(f"{PUB}.frappe")
    def test_promos_add_on_top_of_cap(self, mock_frappe, mock_resolve, mock_cap, mock_promos):
        from admission.api.public import _simulate_scholarship_reduction

        mock_frappe.get_all.return_value = [
            MagicMock(
                mirror_key="BOURSE-TRES-BIEN", scholarship_name="Tres Bien",
                category="Excellence", rate=0.30, exclusivity_group="excellence",
            ),
        ]
        mock_promos.return_value = [
            {"mirror_key": "PROMO-00001", "promo_name": "Early Bird", "rate": 0.10},
        ]
        mock_resolve.side_effect = lambda prog, ft, lc=None: 600000.0 if ft == "annual" else None

        result = _simulate_scholarship_reduction("LIS", ["BOURSE-TRES-BIEN"])

        self.assertEqual(result["bourses_plafond"], 0.30)
        self.assertEqual(result["somme_promo"], 0.10)
        self.assertEqual(result["total_reduction"], 0.40)
        self.assertEqual(result["cout_final_estime"], 360000.0)

    @patch(f"{PUB}._get_promotions_for_programme")
    @patch(f"{PUB}._get_scholarship_cap_local", return_value=0.50)
    @patch(f"{PUB}._resolve_fee_from_catalog")
    @patch(f"{PUB}.frappe")
    def test_multiple_promos_summed(self, mock_frappe, mock_resolve, mock_cap, mock_promos):
        """Coherence §3bis: 2+ promos actives simultaneously are SUMMED, not max-only."""
        from admission.api.public import _simulate_scholarship_reduction

        mock_frappe.get_all.return_value = [
            MagicMock(
                mirror_key="BOURSE-BIEN", scholarship_name="Mention Bien",
                category="Excellence", rate=0.20, exclusivity_group="excellence",
            ),
        ]
        mock_promos.return_value = [
            {"mirror_key": "PROMO-00001", "promo_name": "Early Bird", "rate": 0.10},
            {"mirror_key": "PROMO-00002", "promo_name": "Rentree", "rate": 0.05},
        ]
        mock_resolve.side_effect = lambda prog, ft, lc=None: 600000.0 if ft == "annual" else None

        result = _simulate_scholarship_reduction("LIS", ["BOURSE-BIEN"])

        self.assertEqual(result["somme_promo"], 0.15)
        self.assertEqual(len(result["promotions_appliquees"]), 2)
        self.assertEqual(result["total_reduction"], 0.35)
        self.assertEqual(result["cout_final_estime"], 390000.0)

    def test_zero_requested_returns_none(self):
        from admission.api.public import _simulate_scholarship_reduction

        result = _simulate_scholarship_reduction("LIS", [])
        self.assertIsNone(result)

        result2 = _simulate_scholarship_reduction("LIS", None)
        self.assertIsNone(result2)


# -- Part F: _build_promotion_section ------------------------------------------


class TestBuildPromotionSection(TestCase):

    @patch(f"{PUB}._get_promotions_for_programme")
    def test_multiple_promos_all_listed_and_summed(self, mock_promos):
        """Coherence §3bis: _build_promotion_section sums ALL active promos."""
        from admission.api.public import _build_promotion_section

        mock_promos.return_value = [
            {"mirror_key": "PROMO-00001", "promo_name": "Early Bird", "rate": 0.10},
            {"mirror_key": "PROMO-00002", "promo_name": "Rentree", "rate": 0.05},
        ]

        applicant = MagicMock()
        applicant.programme_code = "LIS"

        result = _build_promotion_section(applicant)

        self.assertEqual(len(result["actives"]), 2)
        self.assertEqual(result["somme_taux"], 0.15)

    @patch(f"{PUB}._get_promotions_for_programme", return_value=[])
    def test_no_promos_returns_default(self, mock_promos):
        from admission.api.public import _build_promotion_section

        applicant = MagicMock()
        applicant.programme_code = "LIS"

        result = _build_promotion_section(applicant)

        self.assertEqual(result["code"], None)
        self.assertEqual(result["taux"], 0)


# -- Part C: _build_bourses_section --------------------------------------------


class TestBuildBoursesSection(TestCase):

    @patch(f"{PUB}._simulate_scholarship_reduction")
    def test_with_requested_scholarships(self, mock_sim):
        from admission.api.public import _build_bourses_section

        mock_sim.return_value = {
            "base_scolarite": 600000,
            "total_reduction": 0.20,
            "cout_final_estime": 480000,
            "disclaimer": "test",
        }

        applicant = MagicMock()
        applicant.requested_scholarships = '["BOURSE-BIEN"]'
        applicant.validated_scholarships = None  # pas encore validées par la Direction
        applicant.programme_code = "LIS"

        result = _build_bourses_section(applicant)

        self.assertEqual(result["demandees"], ["BOURSE-BIEN"])
        self.assertIsNotNone(result["simulation"])
        self.assertEqual(result["validees"], [])
        self.assertFalse(result["valide"])

    @patch(f"{PUB}._simulate_scholarship_reduction")
    def test_validated_scholarships_exposed(self, mock_sim):
        # C2-BOURSES (T7) : les bourses validées par la Direction sont exposées au candidat
        from admission.api.public import _build_bourses_section

        mock_sim.return_value = {"disclaimer": "test"}
        applicant = MagicMock()
        applicant.requested_scholarships = '["BOURSE-BIEN", "BOURSE-TB"]'
        applicant.validated_scholarships = '["BOURSE-BIEN"]'
        applicant.programme_code = "LIS"

        result = _build_bourses_section(applicant)

        self.assertEqual(result["validees"], ["BOURSE-BIEN"])
        self.assertTrue(result["valide"])

    def test_no_requested_empty_section(self):
        from admission.api.public import _build_bourses_section

        applicant = MagicMock()
        applicant.requested_scholarships = "[]"
        applicant.validated_scholarships = "[]"

        result = _build_bourses_section(applicant)

        self.assertEqual(result["demandees"], [])
        self.assertEqual(result["validees"], [])
        self.assertIsNone(result["simulation"])
        self.assertFalse(result["valide"])

    def test_null_field_empty_section(self):
        from admission.api.public import _build_bourses_section

        applicant = MagicMock()
        applicant.requested_scholarships = None
        applicant.validated_scholarships = None

        result = _build_bourses_section(applicant)

        self.assertEqual(result["demandees"], [])
        self.assertIsNone(result["simulation"])


# -- Part D: _build_frais_data integration -------------------------------------


LEGAL = "admission.api.legal"


class TestBuildFraisDataBourses(TestCase):

    @patch(f"{LEGAL}._get_active_legal_texts_meta", return_value={})
    @patch(f"{LEGAL}._get_versioned_disclaimer", return_value=("INDICATIVE — simulation placeholder", "hash1"))
    @patch(f"{PUB}._get_scholarship_cap_local", return_value=0.50)
    @patch(f"{PUB}._get_promotions_for_programme", return_value=[])
    @patch(f"{PUB}._get_scholarships_for_programme")
    @patch(f"{PUB}._resolve_fee_from_catalog")
    @patch(f"{PUB}.frappe")
    def test_bourses_eligibles_populated(self, mock_frappe, mock_resolve, mock_sch, mock_promo, mock_cap, mock_disc, mock_texts):
        from admission.api.public import _build_frais_data

        session = MagicMock()
        session.programme_code = "LIS"
        session.is_prepa_session = 0
        session.application_fee_xof = 15000

        mock_resolve.side_effect = lambda prog, ft, lc=None: {
            ("LIS", "application"): 25000.0,
            ("LIS", "enrollment"): 50000.0,
            ("LIS", "annual"): 600000.0,
        }.get((prog, ft))

        mock_sch.return_value = [
            {"category": "Excellence", "scholarships": [
                {"mirror_key": "BOURSE-TRES-BIEN", "scholarship_name": "Tres Bien", "rate": 0.30},
            ]},
        ]

        result = _build_frais_data(session)

        self.assertEqual(len(result["bourses_eligibles"]), 1)
        self.assertEqual(result["bourses_eligibles"][0]["category"], "Excellence")
        self.assertEqual(result["scolarite_annuelle"], 600000.0)
        self.assertEqual(result["scholarship_cap"], 0.50)
        self.assertIn("INDICATIVE", result["simulation_disclaimer"])

    @patch(f"{LEGAL}._get_active_legal_texts_meta", return_value={})
    @patch(f"{LEGAL}._get_versioned_disclaimer", return_value=("Disclaimer", None))
    @patch(f"{PUB}._get_scholarship_cap_local", return_value=0.50)
    @patch(f"{PUB}._get_promotions_for_programme", return_value=[])
    @patch(f"{PUB}._get_scholarships_for_programme", return_value=[])
    @patch(f"{PUB}._resolve_fee_from_catalog", return_value=None)
    @patch(f"{PUB}.frappe")
    def test_empty_mirror_returns_empty(self, mock_frappe, mock_resolve, mock_sch, mock_promo, mock_cap, mock_disc, mock_texts):
        from admission.api.public import _build_frais_data

        session = MagicMock()
        session.programme_code = "LIC"
        session.is_prepa_session = 0
        session.application_fee_xof = 15000

        result = _build_frais_data(session)

        self.assertEqual(result["bourses_eligibles"], [])
        self.assertEqual(result["promotions_actives"], [])
        self.assertIsNone(result["scolarite_annuelle"])


# -- Part E: _get_scholarships_for_programme -----------------------------------


class TestGetScholarshipsForProgramme(TestCase):

    @patch(f"{PUB}.frappe")
    def test_groups_by_category(self, mock_frappe):
        from admission.api.public import _get_scholarships_for_programme

        mock_frappe.get_all.return_value = [
            MagicMock(mirror_key="B1", scholarship_name="Tres Bien",
                      category="Excellence", rate=0.30, exclusivity_group="excellence", program=""),
            MagicMock(mirror_key="B2", scholarship_name="Bien",
                      category="Excellence", rate=0.20, exclusivity_group="excellence", program=""),
            MagicMock(mirror_key="B3", scholarship_name="Partenaire",
                      category="Partenariat", rate=0.20, exclusivity_group="partenaire", program=""),
        ]

        result = _get_scholarships_for_programme("LIS")

        self.assertEqual(len(result), 2)
        categories = {g["category"] for g in result}
        self.assertEqual(categories, {"Excellence", "Partenariat"})
        excellence = [g for g in result if g["category"] == "Excellence"][0]
        self.assertEqual(len(excellence["scholarships"]), 2)

    @patch(f"{PUB}.frappe")
    def test_empty_mirror_returns_empty(self, mock_frappe):
        from admission.api.public import _get_scholarships_for_programme

        mock_frappe.get_all.return_value = []

        result = _get_scholarships_for_programme("LIS")

        self.assertEqual(result, [])
