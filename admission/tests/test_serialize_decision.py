"""FIX-D-CONF-05/07/08 — la sérialisation candidat expose les décisions motivées (motif de rejet /
refus / désistement + rang d'attente), au candidat concerné, de façon ADDITIVE et CONDITIONNELLE par
statut (miroir de `motif_incompletude` pour INC). Style mocké, aligné sur test_public_3d Part D.
"""

from unittest import TestCase
from unittest.mock import MagicMock, patch

PUB = "admission.api.public"


def _mock_applicant(status, **kw):
    a = MagicMock()
    a.name = "CAN-001"
    a.status = status
    a.programme_code = "LIS"
    a.programme_label = "Licence Sciences"
    a.level_code = None
    a.session = "SES-001"
    a.bac_profile = ""
    a.first_name = "T"
    a.last_name = "U"
    a.email = "t@t.com"
    a.phone = ""
    a.bac_date = None
    a.conditionnel = 0
    a.pieces = []
    a.motif_incompletude = None
    a.motif_rejet = None
    a.motif_refus = None
    a.motif_desistement = None
    a.rang_liste_attente = None
    for k, v in kw.items():
        setattr(a, k, v)
    return a


class TestSerializeDecision(TestCase):
    def _serialize(self, applicant):
        session = MagicMock(label="S", academic_year="2026", closes_on=None, is_open=1)
        with patch(f"{PUB}._build_promotion_section", return_value={}), \
             patch(f"{PUB}._build_bourses_section", return_value={}), \
             patch(f"{PUB}._get_fee_and_payment", return_value=(None, None)), \
             patch(f"{PUB}._session_doc", return_value=session), \
             patch(f"{PUB}.frappe") as mf:
            mf.db.get_value.return_value = None
            from admission.api.public import _serialize_dossier
            return _serialize_dossier(applicant)

    def test_rej_exposes_motif_rejet(self):
        r = self._serialize(_mock_applicant("REJ", motif_rejet="Pièces non conformes"))
        self.assertEqual(r["motif_rejet"], "Pièces non conformes")
        self.assertIsNone(r["motif_refus"])
        self.assertIsNone(r["motif_desistement"])
        self.assertIsNone(r["rang_liste_attente"])

    def test_ref_exposes_motif_refus(self):
        r = self._serialize(_mock_applicant("REF", motif_refus="Niveau insuffisant"))
        self.assertEqual(r["motif_refus"], "Niveau insuffisant")
        self.assertIsNone(r["motif_rejet"])

    def test_des_exposes_motif_desistement(self):
        r = self._serialize(_mock_applicant("DES", motif_desistement="Désistement demandé"))
        self.assertEqual(r["motif_desistement"], "Désistement demandé")

    def test_att_exposes_rang(self):
        r = self._serialize(_mock_applicant("ATT", rang_liste_attente=3))
        self.assertEqual(r["rang_liste_attente"], 3)

    def test_non_relevant_status_exposes_none(self):
        # SOU : le champ est PRÉSENT mais None (conditionnel par statut) — pas de fuite d'un motif hors-état.
        r = self._serialize(_mock_applicant("SOU", motif_rejet="ne doit pas fuir", motif_refus="idem"))
        self.assertIn("motif_rejet", r)
        self.assertIsNone(r["motif_rejet"])
        self.assertIsNone(r["motif_refus"])
        self.assertIsNone(r["motif_desistement"])
        self.assertIsNone(r["rang_liste_attente"])

    def test_additive_statut_reel_intact(self):
        # Non-régression 3c-3a : le contrat pièces (statut_reel + reject_reason) reste intact.
        piece = MagicMock(piece_code="p1", label="P1", status="rejected",
                          reject_reason="Illisible", reject_comment=None)
        with patch(f"{PUB}.requise_effective", return_value=True):
            r = self._serialize(_mock_applicant("REJ", pieces=[piece], motif_rejet="x"))
        self.assertEqual(r["pieces"][0]["statut_reel"], "rejected")
        self.assertEqual(r["pieces"][0]["reject_reason"], "Illisible")
        # additif : le motif dossier coexiste avec le contrat pièces
        self.assertEqual(r["motif_rejet"], "x")
