"""Tests catalogue formations — invariants Admission Programme + enrichissement API."""
from unittest import TestCase
from unittest.mock import MagicMock, patch

MOD = "admission.admission.doctype.admission_programme.admission_programme"
PUBLIC = "admission.api.public"


def _doc(**kw):
    d = MagicMock()
    d.parcours = kw.get("parcours", "Licence")
    d.partner = kw.get("partner")
    d.dd_component_1 = kw.get("dd_component_1")
    d.dd_component_2 = kw.get("dd_component_2")
    d.dd_affinity = kw.get("dd_affinity")
    return d


class TestProgrammeInvariants(TestCase):
    @patch(f"{MOD}.frappe")
    def test_double_diplo_requires_components(self, mf):
        from admission.admission.doctype.admission_programme.admission_programme import validate_programme
        mf.throw.side_effect = Exception
        doc = _doc(parcours="Double-Diplomation", partner="ESIIA")
        with self.assertRaises(Exception):
            validate_programme(doc)

    @patch(f"{MOD}.frappe")
    def test_double_diplo_component_types_enforced(self, mf):
        from admission.admission.doctype.admission_programme.admission_programme import validate_programme
        mf.throw.side_effect = Exception
        mf.db.get_value.side_effect = lambda dt, n, f: {"L": "Bachelor", "B": "Bachelor"}[n]
        doc = _doc(parcours="Double-Diplomation", partner="ESIIA",
                   dd_component_1="L", dd_component_2="B", dd_affinity="Recommandé")
        with self.assertRaises(Exception):
            validate_programme(doc)

    @patch(f"{MOD}.frappe")
    def test_double_diplo_valid_passes(self, mf):
        from admission.admission.doctype.admission_programme.admission_programme import validate_programme
        mf.throw.side_effect = Exception
        mf.db.get_value.side_effect = lambda dt, n, f: {"L": "Licence", "B": "Bachelor"}[n]
        doc = _doc(parcours="Double-Diplomation", partner="ESIIA",
                   dd_component_1="L", dd_component_2="B", dd_affinity="Recommandé")
        validate_programme(doc)  # ne lève pas

    @patch(f"{MOD}.frappe")
    def test_non_dd_forbids_dd_fields(self, mf):
        from admission.admission.doctype.admission_programme.admission_programme import validate_programme
        mf.throw.side_effect = Exception
        doc = _doc(parcours="Licence", dd_component_1="X")
        with self.assertRaises(Exception):
            validate_programme(doc)

    @patch(f"{MOD}.frappe")
    def test_bachelor_requires_partner(self, mf):
        from admission.admission.doctype.admission_programme.admission_programme import validate_programme
        mf.throw.side_effect = Exception
        doc = _doc(parcours="Bachelor", partner=None)
        with self.assertRaises(Exception):
            validate_programme(doc)
