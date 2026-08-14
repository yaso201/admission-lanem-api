"""Tests NOTES-CONCOURS — source unique exam_grading (logique pure, sans Frappe).

Couvre : GN1 (3 notes /20 + moyenne pondérée), GN3 (plage bloquante), GN4 (éliminatoire signalé
épreuve par épreuve), Absent ≠ 0, complétude « les 3 ou aucune » (arbitrage 2), coefficients (GN2).
"""

from unittest import TestCase

from admission.api import exam_grading as G


class TestValidateEntry(TestCase):
    def test_three_valid_notes(self):
        parsed, err = G.validate_entry({"maths": 14, "physique": 12.5, "culture": 8})
        self.assertIsNone(err)
        self.assertEqual(parsed, {"maths": 14.0, "physique": 12.5, "culture": 8.0})

    def test_json_string_accepted(self):
        parsed, err = G.validate_entry('{"maths": 14, "physique": 12, "culture": 10}')
        self.assertIsNone(err)
        self.assertEqual(parsed["maths"], 14.0)

    def test_missing_subject_refused(self):  # GN1 : 3 requises (arbitrage 2)
        parsed, err = G.validate_entry({"maths": 14, "physique": 12})
        self.assertIsNone(parsed)
        self.assertIn("trois notes", err)

    def test_unknown_subject_refused(self):
        parsed, err = G.validate_entry({"maths": 14, "physique": 12, "francais": 10})
        self.assertIsNone(parsed)
        self.assertIn("inconnue", err)

    def test_out_of_range_high_refused(self):  # GN3
        parsed, err = G.validate_entry({"maths": 21, "physique": 12, "culture": 10})
        self.assertIsNone(parsed)
        self.assertIn("hors barème", err)

    def test_out_of_range_negative_refused(self):  # GN3
        parsed, err = G.validate_entry({"maths": -1, "physique": 12, "culture": 10})
        self.assertIsNone(parsed)
        self.assertIn("hors barème", err)

    def test_bounds_inclusive(self):  # 0 et 20 acceptés
        parsed, err = G.validate_entry({"maths": 0, "physique": 20, "culture": 10})
        self.assertIsNone(err)
        self.assertEqual(parsed, {"maths": 0.0, "physique": 20.0, "culture": 10.0})

    def test_non_numeric_refused(self):
        parsed, err = G.validate_entry({"maths": "abc", "physique": 12, "culture": 10})
        self.assertIsNone(parsed)
        self.assertIn("non numérique", err)

    def test_absent_marker_accepted(self):
        parsed, err = G.validate_entry({"__absent__": True})
        self.assertIsNone(err)
        self.assertEqual(parsed, {"__absent__": True})

    def test_absent_with_extra_refused(self):  # ABS ne se mélange pas à des notes
        parsed, err = G.validate_entry({"__absent__": True, "maths": 14})
        self.assertIsNone(parsed)

    def test_empty_refused(self):
        parsed, err = G.validate_entry({})
        self.assertIsNone(parsed)


class TestMoyenne(TestCase):
    def test_weighted_average(self):  # GN1
        parsed = {"maths": 15, "physique": 10, "culture": 8}
        coefs = {"maths": 3, "physique": 2, "culture": 1}
        # (15*3 + 10*2 + 8*1) / 6 = (45+20+8)/6 = 73/6 = 12.1666..
        self.assertEqual(G.compute_moyenne(parsed, coefs), 12.17)

    def test_equal_coefficients(self):
        parsed = {"maths": 12, "physique": 12, "culture": 6}
        self.assertEqual(G.compute_moyenne(parsed, {"maths": 1, "physique": 1, "culture": 1}), 10.0)

    def test_absent_no_moyenne(self):  # Absent ≠ 0
        self.assertIsNone(G.compute_moyenne(G.make_absent(), {"maths": 1, "physique": 1, "culture": 1}))

    def test_no_coefficients_no_moyenne(self):  # arbitrage 1 : sans coef, pas de moyenne
        self.assertIsNone(G.compute_moyenne({"maths": 15, "physique": 10, "culture": 8}, {}))


class TestEliminatoire(TestCase):
    def test_below_threshold_flagged_per_subject(self):  # GN4
        # un 5 en culture est éliminatoire MÊME avec une bonne moyenne
        parsed = {"maths": 18, "physique": 17, "culture": 5}
        self.assertEqual(G.eliminatoire_signals(parsed), ["culture"])

    def test_multiple_below(self):
        parsed = {"maths": 5, "physique": 4, "culture": 12}
        self.assertEqual(set(G.eliminatoire_signals(parsed)), {"maths", "physique"})

    def test_exactly_six_not_eliminated(self):  # seuil STRICT < 6
        parsed = {"maths": 6, "physique": 12, "culture": 10}
        self.assertEqual(G.eliminatoire_signals(parsed), [])

    def test_absent_no_signal(self):  # absent n'est pas éliminé automatiquement
        self.assertEqual(G.eliminatoire_signals(G.make_absent()), [])

    def test_good_moyenne_still_eliminated(self):  # la moyenne n'annule pas le signal épreuve
        parsed = {"maths": 20, "physique": 20, "culture": 5}
        coefs = {"maths": 1, "physique": 1, "culture": 1}
        self.assertEqual(G.compute_moyenne(parsed, coefs), 15.0)  # moyenne 15
        self.assertEqual(G.eliminatoire_signals(parsed), ["culture"])  # mais éliminatoire signalé


class TestSummary(TestCase):
    def test_absent_summary(self):
        s = G.summary('{"__absent__": true}', {"maths": 1, "physique": 1, "culture": 1})
        self.assertTrue(s["absent"])
        self.assertIsNone(s["moyenne"])
        self.assertEqual(s["eliminatoire"], [])

    def test_full_summary(self):
        s = G.summary('{"maths": 15, "physique": 10, "culture": 8}', {"maths": 3, "physique": 2, "culture": 1})
        self.assertFalse(s["absent"])
        self.assertEqual(s["moyenne"], 12.17)
        self.assertEqual(s["valeurs"], {"maths": 15, "physique": 10, "culture": 8})

    def test_empty_summary(self):
        s = G.summary(None, {})
        self.assertFalse(s["renseigne"])
        self.assertFalse(s["absent"])


class TestCoefficients(TestCase):
    def test_valid(self):  # GN2
        parsed, err = G.validate_coefficients({"maths": 3, "physique": 2, "culture": 1})
        self.assertIsNone(err)
        self.assertEqual(parsed, {"maths": 3.0, "physique": 2.0, "culture": 1.0})

    def test_missing_refused(self):
        parsed, err = G.validate_coefficients({"maths": 3, "physique": 2})
        self.assertIsNone(parsed)
        self.assertIn("manquant", err)

    def test_zero_refused(self):
        parsed, err = G.validate_coefficients({"maths": 0, "physique": 2, "culture": 1})
        self.assertIsNone(parsed)
        self.assertIn("strictement positif", err)

    def test_coefficients_of_reads_json(self):
        session = {"exam_coefficients": '{"maths": 3, "physique": 2, "culture": 1}'}
        self.assertEqual(G.coefficients_of(session), {"maths": 3.0, "physique": 2.0, "culture": 1.0})

    def test_coefficients_complete(self):
        self.assertTrue(G.coefficients_complete({"maths": 1, "physique": 1, "culture": 1}))
        self.assertFalse(G.coefficients_complete({"maths": 1, "physique": 1}))
        self.assertFalse(G.coefficients_complete({"maths": 1, "physique": 1, "culture": 0}))
