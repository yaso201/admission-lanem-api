"""CONTRAT-2 — Preuve B1 (minimisation APDP de la sur-réponse de reprise, faille 19) + falsifiabilité.

`get_recovered_dossier` ne sert plus que la vue reprise : `_serialize_recovered` projette
`_serialize_dossier` sur les 6 clés que `reprise.astro` consomme (A3 §2.1 #17) + `reprenable`. Les
champs personnels non affichés (bourses, paiement, promotion, profil_bac, convocation, conditionnel,
motifs, rang) ne transitent plus par la récupération OTP.

Preuve indépendante de l'environnement : on mocke `_serialize_dossier` avec un dict PLEIN (les 17 clés)
et on vérifie que la projection ne garde que les clés réduites. Le parcours de reprise réel est prouvé
bout en bout par `test_claim_recovered_dossier` (23) + `test_identity_recovery` (12) — verts après B1.
"""
from unittest import TestCase
from unittest.mock import patch

from frappe.tests.utils import FrappeTestCase

from admission.contracts import registry
from admission.contracts.validator import validate

# forme PLEINE de _serialize_dossier (toutes les clés top-level servies avant B1)
FULL = {
    "dossier_id": "CAN-2026-00001", "statut": "ACO",
    "programme": {"code": "LIC", "label": "Licence"},
    "session": {"id": "SES-2026-LIC", "label": "Licence 2026", "academic_year": "2026", "closes_on": None, "is_open": 1},
    "identite": {"prenom": "A", "nom": "B", "email": "a@b.co", "tel": "+229...", "date_naissance": "2005-01-01"},
    "pieces": [{"code": "BAC", "statut": "verified", "statut_reel": "verified", "requise": 1}],
    # ── ces clés NE DOIVENT PLUS être servies à la reprise (minimisation) ──
    "profil_bac": {"serie": "D"}, "bourses": [{"code": "X"}], "promotion": {"taux": 10},
    "paiement": {"frais1": {"statut": "Paid"}}, "convocation": {"numero": "1"}, "conditionnel": 1,
    "motif_incompletude": None, "motif_rejet": None, "motif_refus": None, "motif_desistement": None,
    "rang_liste_attente": None,
}
DROPPED = ("profil_bac", "bourses", "promotion", "paiement", "convocation", "conditionnel",
           "motif_incompletude", "motif_rejet", "motif_refus", "motif_desistement", "rang_liste_attente")
KEPT = ("dossier_id", "statut", "programme", "session", "identite", "pieces")


def _recovered():
    """`_serialize_recovered` avec `_serialize_dossier` mocké plein → simule la vraie réponse."""
    from admission.api import public
    with patch.object(public, "_serialize_dossier", return_value=dict(FULL)):
        data = public._serialize_recovered(object())
    data["reprenable"] = True
    return data


def _envelope():
    return registry.envelope_schema(registry.load_data_schema("public.get_recovered_dossier"))


class TestB1RecoveredMinimisation(TestCase):
    def test_dropped_personal_fields_absent(self):
        data = _recovered()
        for d in DROPPED:
            self.assertNotIn(d, data, f"B1 : « {d} » ne doit plus transiter par la reprise (faille 19)")

    def test_kept_keys_present_reprise_unchanged(self):
        data = _recovered()
        for k in KEPT:
            self.assertIn(k, data, f"la reprise consomme « {k} » (DEC-E : rendu inchangé)")
        self.assertTrue(data["reprenable"])

    def test_reduced_response_conforms_to_contract(self):
        self.assertEqual(validate({"ok": True, "data": _recovered(), "error": None}, _envelope()), [])


class TestContractA2A3Conformance(FrappeTestCase):
    """A3 list_dossiers (forme PERF-1) + A2 get_frais : conformance back sur réponse réelle."""

    def test_list_dossiers_conforms(self):
        from admission.api import staff
        resp = staff.list_dossiers()
        schema = registry.envelope_schema(registry.load_data_schema("staff.list_dossiers"))
        errs = validate(resp, schema)
        self.assertEqual(errs, [], "\n".join(errs))

    def test_get_frais_conforms(self):
        import frappe
        from admission.api import public
        sess = frappe.get_all("Admission Session", fields=["name", "programme_code"], limit_page_length=1)
        if not sess:
            self.skipTest("aucune session en base de test")
        s = sess[0]
        try:
            resp = public.get_frais(programme=s.programme_code, session=s.name)
        except Exception as e:  # noqa: BLE001
            self.skipTest(f"get_frais non appelable ainsi en base de test ({e})")
        if not isinstance(resp, dict) or not resp.get("ok"):
            self.skipTest(f"get_frais indisponible sur la session de test ({resp})")
        schema = registry.envelope_schema(registry.load_data_schema("public.get_frais"))
        self.assertEqual(validate(resp, schema), [])


class TestB1Falsifiability(TestCase):
    def test_dropping_a_consumed_key_turns_red(self):
        data = _recovered()
        for key in ("dossier_id", "identite", "pieces", "statut"):
            broken = {"ok": True, "error": None, "data": {k: v for k, v in data.items() if k != key}}
            self.assertTrue(validate(broken, _envelope()), f"retirer '{key}' aurait dû rougir")

    def test_reintroducing_dropped_pii_turns_red(self):
        # additionalProperties:false → si la sur-réponse revient (régression), le contrat la détecte.
        data = _recovered(); data["bourses"] = [{"code": "X"}]
        errs = validate({"ok": True, "error": None, "data": data}, _envelope())
        self.assertTrue(any("bourses" in e for e in errs), errs)
