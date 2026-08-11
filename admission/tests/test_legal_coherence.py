"""LEGAL-SOURCE-UNIQUE — sonde de cohérence front↔back (OBS-2).

`_compare_legal_hashes` est PURE (dict-in/dict-out) : compare le manifeste légal
COMPILÉ du front  {TYPE: {version, content_hash}}  aux hash back ACTIFS
{type_lower: {type, version, content_hash}}  (= _get_active_legal_texts_meta).

Ne considère QUE les types que le front prétend dériver (les 4 signés) : le
SIMULATION_DISCLAIMER (back, non-page) ne doit JAMAIS être un faux positif.
Style mocké (comme test_obs*/test_health) — aucune I/O, aucune DB.
"""

from unittest import TestCase

from admission.api.legal import _compare_legal_hashes

BACK_META = {
    "cgv": {"type": "CGV", "version": "V1", "content_hash": "h_cgv"},
    "privacy_policy": {"type": "PRIVACY_POLICY", "version": "V2", "content_hash": "h_priv"},
    "refund_policy": {"type": "REFUND_POLICY", "version": "V3", "content_hash": "h_ref"},
    "data_transfer_consent": {"type": "DATA_TRANSFER_CONSENT", "version": "V4", "content_hash": "h_tra"},
    # back-only, jamais une page front → ne doit pas provoquer d'alerte
    "simulation_disclaimer": {"type": "SIMULATION_DISCLAIMER", "version": "V9", "content_hash": "h_sim"},
}


def _front(cgv_hash="h_cgv"):
    return {
        "CGV": {"version": "V1", "content_hash": cgv_hash},
        "PRIVACY_POLICY": {"version": "V2", "content_hash": "h_priv"},
        "REFUND_POLICY": {"version": "V3", "content_hash": "h_ref"},
        "DATA_TRANSFER_CONSENT": {"version": "V4", "content_hash": "h_tra"},
    }


class TestCompareLegalHashes(TestCase):
    def test_aligned_front_back_is_ok(self):
        r = _compare_legal_hashes(_front(), BACK_META)
        self.assertTrue(r["ok"])
        self.assertEqual(r["divergences"], [])

    def test_divergent_hash_flags_rebuild_due(self):
        r = _compare_legal_hashes(_front(cgv_hash="STALE"), BACK_META)
        self.assertFalse(r["ok"])
        self.assertEqual([d["type"] for d in r["divergences"]], ["CGV"])

    def test_simulation_disclaimer_is_not_a_false_positive(self):
        # le disclaimer existe côté back mais n'est pas une page dérivée → OK
        r = _compare_legal_hashes(_front(), BACK_META)
        self.assertTrue(r["ok"])

    def test_type_absent_from_back_flags_divergence(self):
        back = dict(BACK_META)
        del back["refund_policy"]
        r = _compare_legal_hashes(_front(), back)
        self.assertFalse(r["ok"])
        self.assertIn("REFUND_POLICY", [d["type"] for d in r["divergences"]])
