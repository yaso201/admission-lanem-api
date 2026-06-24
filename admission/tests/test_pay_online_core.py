"""Tests PAY-CONFIRM-AGENT Phase e — factorisation cœur online + initiation staff (scénario 3).

Cœur commun `prepare_online_payment` (public.py) : pré-crée un Pending Online LIÉ (applicant +
provider_reference serveur) et renvoie le descriptor. Réutilisé par le candidat (submit_payment_online,
token+OTP) ET par l'agent (initiate_online_payment, Administratif + dossier_id). Le webhook (phase d)
promeut ce Pending par provider_reference.
Impératifs vérifiés : descriptor candidat IDENTIQUE (non-régression) ; Pending orphelins gérés
(filtre offline du resolve + cleanup expire_stale_online_pending). Style unitaire mocké.
"""

from unittest import TestCase
from unittest.mock import MagicMock, patch

PUBLIC = "admission.api.public"
STAFF = "admission.api.staff"
LEGAL = "admission.api.legal"


class TestPrepareOnlinePayment(TestCase):
    def test_creates_pending_linked(self):
        with patch(f"{PUBLIC}.frappe") as mf, patch(f"{PUBLIC}.secrets") as msec, \
             patch("admission.api.kkiapay.frappe") as mkk, \
             patch(f"{PUBLIC}._online_payment_exists", return_value=False):
            mkk.conf = {"kkiapay_sandbox": 1, "kkiapay_public_key": "pk_test"}
            msec.token_hex.return_value = "abc123"
            inserted = MagicMock(); mf.get_doc.return_value = inserted
            from admission.api.public import prepare_online_payment
            applicant = MagicMock(); applicant.name = "CAN-1"
            fee = MagicMock(); fee.name = "AFF-1"; fee.amount_xof = 15000
            desc = prepare_online_payment(applicant, fee, idempotency_key=None)
        doc = mf.get_doc.call_args[0][0]
        self.assertEqual(doc["payment_status"], "Pending")
        self.assertEqual(doc["payment_mode"], "Online")
        self.assertEqual(doc["applicant"], "CAN-1")
        self.assertEqual(doc["provider_reference"], "abc123")  # liage persisté serveur
        inserted.insert.assert_called_once_with(ignore_permissions=True)
        self.assertEqual(desc["provider"], "kkiapay")
        self.assertEqual(desc["mode"], "sandbox")       # piloté par site_config (kkiapay.mode)
        self.assertEqual(desc["public_key"], "pk_test")  # clé PUBLIQUE seule côté front
        self.assertTrue(desc["sandbox"])
        self.assertIn("abc123", desc["data"])            # aller-retour widget→webhook (stateData)
        self.assertTrue(desc["webhook_required"])
        self.assertEqual(desc["reference"], "abc123")

    def test_idempotent_reuses_existing(self):
        with patch(f"{PUBLIC}.frappe") as mf, patch(f"{PUBLIC}.secrets"), \
             patch(f"{PUBLIC}._online_payment_exists", return_value=True):
            from admission.api.public import prepare_online_payment
            desc = prepare_online_payment(MagicMock(), MagicMock(), idempotency_key="KEY-1")
            mf.get_doc.assert_not_called()  # réutilise le Pending existant (pas de doublon)
        self.assertEqual(desc["reference"], "KEY-1")


class TestSubmitOnlineDescriptorUnchanged(TestCase):
    """Non-régression : le descriptor candidat reste IDENTIQUE après factorisation."""

    @patch(f"{PUBLIC}._online_payment_exists", return_value=True)  # skip insert (pas de DB en test)
    @patch(f"{PUBLIC}.secrets")
    @patch(f"{LEGAL}._record_consent", return_value="CONS-1")
    @patch(f"{LEGAL}._get_active_legal_document")
    @patch(f"{PUBLIC}._ensure_fee")
    @patch(f"{PUBLIC}._get_applicant")
    @patch("admission.api.kkiapay.frappe")
    @patch(f"{PUBLIC}.frappe")
    def test_descriptor_keys_values(self, mf, mkk, mget, mens, mlegal, mrec, msec, _exists):
        mkk.conf = {"kkiapay_sandbox": 1, "kkiapay_public_key": "pk_test"}
        mf.form_dict = {}; mf.request = None
        applicant = MagicMock(); applicant.name = "CAN-001"; mget.return_value = applicant
        refund = MagicMock(); refund.name = "LEGAL-REFUND"; mlegal.return_value = refund
        fee = MagicMock(); fee.amount_xof = 25000; mens.return_value = fee
        msec.token_hex.return_value = "ref123"
        mf.db.exists.return_value = False            # garde amont B1 : aucun paiement Confirmed sur ce fee
        from admission.api.public import submit_payment_online
        result = submit_payment_online(dossier_id="CAN-001", token="tok", consent_refund=True)
        self.assertTrue(result["ok"])
        data = result["data"]
        self.assertEqual(data["provider"], "kkiapay")
        self.assertEqual(data["mode"], "sandbox")
        self.assertTrue(data["webhook_required"])
        self.assertEqual(data["amount_xof"], 25000)
        self.assertIn("reference", data)


class TestInitiateOnlinePaymentStaff(TestCase):
    def test_role_guarded(self):
        with patch(f"{STAFF}.frappe") as mf:
            mf.only_for.side_effect = PermissionError("403")
            from admission.api.staff import initiate_online_payment
            with self.assertRaises(PermissionError):
                initiate_online_payment(dossier_id="CAN-1")
            mf.only_for.assert_called_once()

    def test_initiates_linked_pending(self):
        with patch(f"{STAFF}.frappe") as mf, \
             patch(f"{STAFF}._ensure_fee") as mens, \
             patch(f"{STAFF}.prepare_online_payment", return_value={"provider": "kkiapay", "reference": "R-AGENT"}) as mprep, \
             patch(f"{STAFF}._ok", side_effect=lambda d: {"ok": True, **d}), \
             patch(f"{STAFF}._error", side_effect=lambda c, m, s=400: {"ok": False, "code": c}):
            mf.db.exists.return_value = True
            applicant = MagicMock(); applicant.status = "SOP"  # W3/B0.5 : frais 1 depuis BRO/SOP/SOU
            fee = MagicMock()
            mf.get_doc.return_value = applicant; mens.return_value = fee
            from admission.api.staff import initiate_online_payment
            res = initiate_online_payment(dossier_id="CAN-1")
        mprep.assert_called_once()                  # cœur commun réutilisé
        self.assertEqual(mprep.call_args[0][0], applicant)  # lié au bon dossier
        self.assertEqual(res["reference"], "R-AGENT")


class TestOrphanPendingHandling(TestCase):
    def test_resolve_ignores_online_pending(self):
        with patch(f"{STAFF}.frappe") as mf:
            mf.get_all.return_value = []  # filtre offline → un Pending Online n'est jamais retourné
            from admission.api.staff import _resolve_pending_payment
            self.assertIsNone(_resolve_pending_payment("CAN-1"))
            filters = mf.get_all.call_args.kwargs["filters"]
            self.assertIn("payment_mode", filters)  # restreint aux modes offline (Cash/Bank)

    def test_expire_stale_marks_non_terminal(self):
        # T8 / PC1-D1 : expire_stale ne REJETTE plus (non terminal) → marque 'Stale - awaiting
        # webhook' SANS toucher payment_status (le Pending reste promouvable par un success tardif).
        with patch(f"{PUBLIC}.frappe") as mf, \
             patch(f"{PUBLIC}.now_datetime", return_value="2026-06-10 10:00:00"), \
             patch(f"{PUBLIC}.add_to_date", return_value="2026-06-08 10:00:00"):
            mf.get_all.return_value = ["REC-1", "REC-2"]
            from admission.api.public import expire_stale_online_pending
            n = expire_stale_online_pending(older_than_hours=48)
        self.assertEqual(n, 2)
        self.assertEqual(mf.db.set_value.call_count, 2)
        for call in mf.db.set_value.call_args_list:
            self.assertEqual(call.args[2], "reconciliation")          # marque, jamais payment_status
            self.assertEqual(call.args[3], "Stale - awaiting webhook")
        filters = mf.get_all.call_args.kwargs["filters"]
        self.assertEqual(filters["payment_mode"], "Online")
        self.assertEqual(filters["payment_status"], "Pending")


class TestUpstreamGuard(TestCase):
    @patch(f"{PUBLIC}.prepare_online_payment")
    @patch(f"{PUBLIC}._ensure_fee")
    @patch(f"{LEGAL}._record_consent")
    @patch(f"{LEGAL}._get_active_legal_document")
    @patch(f"{PUBLIC}._require_otp_verified", return_value=None)
    @patch(f"{PUBLIC}._get_applicant")
    @patch(f"{PUBLIC}.frappe")
    def test_submit_blocked_when_fee_already_confirmed(self, mf, mget, _otp, mleg, _rec, mfee, mprep):
        # Garde amont B1 : un fee déjà Confirmed → ALREADY_PAID, AUCUN nouveau Pending créé.
        mget.return_value = MagicMock()
        mleg.return_value = MagicMock()              # REFUND_POLICY disponible
        fee = MagicMock(); fee.name = "AFF-1"; mfee.return_value = fee
        mf.db.exists.return_value = True             # un paiement Confirmed existe déjà sur ce fee
        from admission.api.public import submit_payment_online
        res = submit_payment_online(dossier_id="CAN-1", token="tok", consent_refund=1)
        self.assertFalse(res["ok"])
        self.assertEqual(res["error"]["code"], "ALREADY_PAID")
        mprep.assert_not_called()                    # pas de nouveau Pending
