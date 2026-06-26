"""Lot 3a — enforcement des pièces à la PORTE PAIEMENT (back, candidat).

Garde : on ne peut pas initier le paiement (online OU offline) tant que toutes les pièces
required=1 ne sont pas fournies (status uploaded OU verified). Le BACK fait foi ; le front (3b)
n'est qu'une garde UX. Hors scope : enrollment / canal staff (prepare_online_payment partagé,
NON gaté) ; vérification par pièce (Lot 3c).

Style unitaire mocké (cohérent test_pay_online_core / test_bridge) : aucun accès DB.
"""

import inspect
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import MagicMock, patch

PUBLIC = "admission.api.public"
LEGAL = "admission.api.legal"
NOTIF = "admission.api.notifications"


def _piece(code, label, required, status):
    """Ligne Applicant Piece mockée (mêmes attributs que la child table)."""
    return SimpleNamespace(piece_code=code, label=label, required=required, status=status)


def _pieces_anterieur(diplome_status="uploaded"):
    """Profil bac_anterieur (7 requises, labels = PIECES_BY_BAC_PROFILE / Lot 1+2)."""
    return [
        _piece("identite", "Piece d'identite (CNI, passeport ou CIP)", 1, "uploaded"),
        _piece("photo", "Photo d'identite", 1, "uploaded"),
        _piece("cv", "Curriculum Vitae", 1, "uploaded"),
        _piece("motivation", "Lettre de motivation", 1, "uploaded"),
        _piece("diplome_bac", "Diplome du baccalaureat", 1, diplome_status),
        _piece("releve_bac", "Releve de notes du Bac", 1, "uploaded"),
        _piece("justificatifs_post_bac", "Justificatifs des annees post-bac", 1, "uploaded"),
    ]


def _pieces_attente_optionnelles_manquantes():
    """Profil attente : 6 requises uploaded + 2 OPTIONNELLES (required=0) manquantes."""
    return [
        _piece("identite", "Piece d'identite (CNI, passeport ou CIP)", 1, "uploaded"),
        _piece("photo", "Photo d'identite", 1, "uploaded"),
        _piece("cv", "Curriculum Vitae", 1, "uploaded"),
        _piece("motivation", "Lettre de motivation", 1, "uploaded"),
        _piece("releves_terminale", "Releves de notes de terminale", 1, "uploaded"),
        _piece("attestation_scolarite", "Attestation de scolarite", 1, "uploaded"),
        _piece("diplome_bac", "Diplome du baccalaureat", 0, "missing"),
        _piece("releve_bac", "Releve de notes du Bac", 0, "missing"),
    ]


class TestHelperManquantes(TestCase):
    """Le helper de vérité partagé pieces_requises_manquantes."""

    def test_vide_ne_bloque_pas(self):
        # T7 : applicant.pieces vide → aucune requise connue → ne bloque pas.
        from admission.api.public import pieces_requises_manquantes
        self.assertEqual(pieces_requises_manquantes(SimpleNamespace(pieces=[])), [])

    def test_verified_compte_comme_fournie(self):
        # T9 : une pièce 'verified' compte comme fournie (pas manquante).
        from admission.api.public import pieces_requises_manquantes
        pieces = [_piece("identite", "Piece d'identite", 1, "verified")]
        self.assertEqual(pieces_requises_manquantes(SimpleNamespace(pieces=pieces)), [])

    def test_optionnelle_manquante_non_comptee(self):
        # base T5 : une optionnelle (required=0) manquante n'est jamais retournée.
        from admission.api.public import pieces_requises_manquantes
        pieces = [_piece("diplome_bac", "Diplome", 0, "missing")]
        self.assertEqual(pieces_requises_manquantes(SimpleNamespace(pieces=pieces)), [])

    def test_requise_missing_retournee_avec_label(self):
        from admission.api.public import pieces_requises_manquantes
        pieces = [_piece("identite", "Piece d'identite (CNI, passeport ou CIP)", 1, "missing")]
        out = pieces_requises_manquantes(SimpleNamespace(pieces=pieces))
        self.assertEqual(out, [{"code": "identite", "label": "Piece d'identite (CNI, passeport ou CIP)"}])


class TestSubmitPaymentOnlineGuard(TestCase):
    @patch(f"{LEGAL}._record_consent", return_value="CONS-1")
    @patch(f"{LEGAL}._get_active_legal_document")
    @patch(f"{PUBLIC}.prepare_online_payment", return_value={"provider": "kkiapay", "reference": "R-1"})
    @patch(f"{PUBLIC}._ensure_fee")
    @patch(f"{PUBLIC}._get_applicant")
    @patch(f"{PUBLIC}.frappe")
    def test_refuse_si_requise_manquante(self, mf, mget, mens, mprep, mleg, mrec):
        # T1 + G3a1 : une requise non uploaded → 409 PIECES_MANQUANTES, AUCUN effet de bord.
        mf.db.exists.return_value = False
        applicant = MagicMock(); applicant.name = "CAN-1"
        applicant.pieces = _pieces_anterieur(diplome_status="missing")
        mget.return_value = applicant
        mleg.return_value = MagicMock()
        from admission.api.public import submit_payment_online
        res = submit_payment_online(dossier_id="CAN-1", token="tok", consent_refund=True)
        self.assertFalse(res["ok"])
        self.assertEqual(res["error"]["code"], "PIECES_MANQUANTES")
        mf.local.response.__setitem__.assert_any_call("http_status_code", 409)
        mens.assert_not_called()   # fail-fast AVANT _ensure_fee
        mprep.assert_not_called()  # cœur partagé jamais atteint

    @patch(f"{LEGAL}._record_consent", return_value="CONS-1")
    @patch(f"{LEGAL}._get_active_legal_document")
    @patch(f"{PUBLIC}.prepare_online_payment", return_value={"provider": "kkiapay", "reference": "R-1"})
    @patch(f"{PUBLIC}._ensure_fee")
    @patch(f"{PUBLIC}._get_applicant")
    @patch(f"{PUBLIC}.frappe")
    def test_autorise_si_toutes_uploaded(self, mf, mget, mens, mprep, mleg, mrec):
        # T2 + G3a1 : toutes requises uploaded → passe (cœur partagé appelé).
        mf.db.exists.return_value = False
        applicant = MagicMock(); applicant.name = "CAN-1"
        applicant.pieces = _pieces_anterieur()
        mget.return_value = applicant
        mleg.return_value = MagicMock()
        fee = MagicMock(); fee.name = "AFF-1"; mens.return_value = fee
        from admission.api.public import submit_payment_online
        res = submit_payment_online(dossier_id="CAN-1", token="tok", consent_refund=True)
        self.assertTrue(res["ok"])
        mprep.assert_called_once()

    @patch(f"{LEGAL}._record_consent", return_value="CONS-1")
    @patch(f"{LEGAL}._get_active_legal_document")
    @patch(f"{PUBLIC}.prepare_online_payment", return_value={"provider": "kkiapay", "reference": "R-1"})
    @patch(f"{PUBLIC}._ensure_fee")
    @patch(f"{PUBLIC}._get_applicant")
    @patch(f"{PUBLIC}.frappe")
    def test_optionnelle_manquante_autorise(self, mf, mget, mens, mprep, mleg, mrec):
        # T5 + G3a3 : optionnelles (required=0) manquantes, requises OK → AUTORISÉ.
        mf.db.exists.return_value = False
        applicant = MagicMock(); applicant.name = "CAN-1"
        applicant.pieces = _pieces_attente_optionnelles_manquantes()
        mget.return_value = applicant
        mleg.return_value = MagicMock()
        mens.return_value = MagicMock(name="AFF-1")
        from admission.api.public import submit_payment_online
        res = submit_payment_online(dossier_id="CAN-1", token="tok", consent_refund=True)
        self.assertTrue(res["ok"])
        mprep.assert_called_once()

    @patch(f"{LEGAL}._record_consent", return_value="CONS-1")
    @patch(f"{LEGAL}._get_active_legal_document")
    @patch(f"{PUBLIC}.prepare_online_payment", return_value={"provider": "kkiapay", "reference": "R-1"})
    @patch(f"{PUBLIC}._ensure_fee")
    @patch(f"{PUBLIC}._get_applicant")
    @patch(f"{PUBLIC}.frappe")
    def test_pieces_vide_ne_bloque_pas(self, mf, mget, mens, mprep, mleg, mrec):
        # T7 (flux) : applicant.pieces vide → la garde ne bloque pas (passe).
        mf.db.exists.return_value = False
        applicant = MagicMock(); applicant.name = "CAN-1"; applicant.pieces = []
        mget.return_value = applicant
        mleg.return_value = MagicMock()
        mens.return_value = MagicMock(name="AFF-1")
        from admission.api.public import submit_payment_online
        res = submit_payment_online(dossier_id="CAN-1", token="tok", consent_refund=True)
        self.assertTrue(res["ok"])
        mprep.assert_called_once()

    @patch(f"{LEGAL}._record_consent", return_value="CONS-1")
    @patch(f"{LEGAL}._get_active_legal_document")
    @patch(f"{PUBLIC}.prepare_online_payment", return_value={"provider": "kkiapay", "reference": "R-1"})
    @patch(f"{PUBLIC}._ensure_fee")
    @patch(f"{PUBLIC}._get_applicant")
    @patch(f"{PUBLIC}.frappe")
    def test_message_utilise_label_pas_code(self, mf, mget, mens, mprep, mleg, mrec):
        # T8 + G3a5 : le message liste le LABEL (pas le code) de la pièce manquante.
        mf.db.exists.return_value = False
        applicant = MagicMock(); applicant.name = "CAN-1"
        applicant.pieces = _pieces_anterieur(diplome_status="missing")
        mget.return_value = applicant
        mleg.return_value = MagicMock()
        from admission.api.public import submit_payment_online
        res = submit_payment_online(dossier_id="CAN-1", token="tok", consent_refund=True)
        self.assertIn("Diplome du baccalaureat", res["error"]["message"])  # label
        self.assertNotIn("diplome_bac", res["error"]["message"])           # pas le code


class TestDeclarePaymentOfflineGuard(TestCase):
    @patch(f"{LEGAL}._get_active_legal_document")
    @patch(f"{PUBLIC}._ensure_fee")
    @patch(f"{PUBLIC}._get_applicant")
    @patch(f"{PUBLIC}.frappe")
    def test_refuse_si_requise_manquante(self, mf, mget, mens, mleg):
        # T3 + G3a2 : offline refusé si une requise manque ; pas d'effet de bord.
        mf.form_dict = {}; mf.request = None
        applicant = MagicMock(); applicant.name = "CAN-1"; applicant.status = "BRO"
        applicant.pieces = _pieces_anterieur(diplome_status="missing")
        mget.return_value = applicant
        mleg.return_value = MagicMock()
        from admission.api.public import declare_payment_offline
        res = declare_payment_offline(dossier_id="CAN-1", token="tok", mode="Bank", consent_refund=True)
        self.assertFalse(res["ok"])
        self.assertEqual(res["error"]["code"], "PIECES_MANQUANTES")
        mf.local.response.__setitem__.assert_any_call("http_status_code", 409)
        mens.assert_not_called()

    @patch(f"{NOTIF}.send_offline_submission")
    @patch(f"{LEGAL}._record_consent", return_value="CONS-1")
    @patch(f"{LEGAL}._get_active_legal_document")
    @patch(f"{PUBLIC}.now_datetime", return_value="2026-06-09 12:00:00")
    @patch(f"{PUBLIC}._ensure_fee")
    @patch(f"{PUBLIC}._get_applicant")
    @patch(f"{PUBLIC}.frappe")
    def test_autorise_si_toutes_uploaded(self, mf, mget, mens, mnow, mleg, mrec, mnotif):
        # T4 + G3a2 : offline passe si toutes requises uploaded → SOP.
        mf.form_dict = {}; mf.request = None
        applicant = MagicMock(); applicant.name = "CAN-1"; applicant.status = "BRO"
        applicant.pieces = _pieces_anterieur()
        mget.return_value = applicant
        fee = MagicMock(); fee.name = "AFF-1"; fee.amount_xof = 25000; mens.return_value = fee
        payment = MagicMock(); payment.name = "REC-1"; mf.get_doc.return_value = payment
        mleg.return_value = MagicMock()
        from admission.api.public import declare_payment_offline
        res = declare_payment_offline(
            dossier_id="CAN-1", token="tok", mode="Cash", reference="REF-1", consent_refund=True,
        )
        self.assertTrue(res["ok"])
        self.assertEqual(res["data"]["statut"], "SOP")
        mens.assert_called_once()  # garde passée → flux normal


class TestPrepareCoreNotGated(TestCase):
    def test_prepare_online_payment_non_gate(self):
        # T6 + G3a4 : le cœur partagé prepare_online_payment ne porte PAS la garde
        # (enrollment / canal staff non gatés) → renvoie le descriptor MALGRÉ pièces manquantes.
        with patch(f"{PUBLIC}.frappe") as mf, patch(f"{PUBLIC}.secrets") as msec, \
             patch("admission.api.kkiapay.frappe") as mkk, \
             patch(f"{PUBLIC}._online_payment_exists", return_value=False):
            mkk.conf = {"kkiapay_sandbox": 1, "kkiapay_public_key": "pk_test"}
            msec.token_hex.return_value = "ref"
            mf.get_doc.return_value = MagicMock()
            from admission.api.public import prepare_online_payment
            applicant = MagicMock(); applicant.name = "CAN-1"
            applicant.pieces = [_piece("identite", "Piece d'identite", 1, "missing")]
            fee = MagicMock(); fee.amount_xof = 15000; fee.name = "AFF-1"
            desc = prepare_online_payment(applicant, fee)
        self.assertEqual(desc["provider"], "kkiapay")
        self.assertEqual(desc["reference"], "ref")


class TestStaffFactorisation(TestCase):
    def test_critere_partage_une_requete_bulk(self):
        # T10 + G3c0 : staff.list_dossiers utilise le critère PARTAGÉ et reste UNE requête bulk
        # (parent in names), pas N requêtes per-doc. Prouvé par introspection source.
        from admission.api.public import PIECES_FOURNIE_STATUSES
        from admission.api import staff
        self.assertEqual(PIECES_FOURNIE_STATUSES, ("uploaded", "verified"))
        src = inspect.getsource(staff.list_dossiers)
        self.assertIn("PIECES_FOURNIE_STATUSES", src)            # critère partagé (pas de littéral dupliqué)
        self.assertNotIn('"status": "missing"', src)             # ancien critère retiré
        self.assertEqual(src.count('get_all("Applicant Piece"'), 1)  # UNE requête bulk
        self.assertIn('"parent": ["in", names]', src)            # bulk, pas per-doc
