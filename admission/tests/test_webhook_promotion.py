"""Tests LOT KKIAPAY — webhook provider réel (ferme ADM-DEBT-74).

Contrat : POST JSON brut KkiaPay + en-tête `x-kkiapay-secret` (constant-time) ; le payload
n'est jamais cru sur parole — re-vérification serveur `verify_transaction` (status SUCCESS +
montant >= attendu) AVANT promotion du Pending lié par stateData.reference. Plus d'insert
fallback : webhook sans Pending = 409. transaction.failed → Pending→Rejected. Style unitaire mocké.
"""

import json
from unittest import TestCase
from unittest.mock import MagicMock, patch

WEBHOOK = "admission.api.webhook"
PUBLIC = "admission.api.public"
SECRET = "whsecret"


def _payload(ref="REF-1", event="transaction.success", tx="TX-1", amount=15000):
    return {"transactionId": tx, "event": event,
            "isPaymentSucces": event == "transaction.success",
            "amount": amount, "method": "MOBILE_MONEY",
            "stateData": {"reference": ref, "sdk": "lanem-admission"}}


def _rq(mf, payload, header=SECRET):
    """Requête KkiaPay simulée : corps JSON BRUT + en-tête secret."""
    mf.conf = {"admission_payment_webhook_secret": SECRET}
    mf.request.data = json.dumps(payload)
    mf.get_request_header.return_value = header


def _pending(status="Pending", amount=15000):
    p = MagicMock()
    p.payment_status = status
    p.name = "REC-1"
    p.applicant = "CAN-2026-00001"
    p.applicant_fee = "AFF-1"
    p.amount_xof = amount
    return p


class TestWebhookPromotion(TestCase):
    @patch(f"{WEBHOOK}.send_payment_receipt")
    @patch(f"{WEBHOOK}.apply_confirmed_payment_cascade")
    @patch(f"{WEBHOOK}.verify_transaction", return_value={"status": "SUCCESS", "amount": 15000})
    @patch(f"{WEBHOOK}.now_datetime", return_value="2026-06-13 10:00:00")
    @patch(f"{WEBHOOK}._find_payment_by_reference")
    @patch(f"{WEBHOOK}.frappe")
    def test_promotes_existing_pending(self, mf, mfind, _now, mver, mcasc, msend):
        _rq(mf, _payload())
        pending = _pending()
        mfind.return_value = pending
        applicant = MagicMock(); fee = MagicMock()
        mf.get_doc.side_effect = lambda dt, name=None: applicant if dt == "Admission Applicant" else fee
        from admission.api.webhook import payment
        res = payment()
        self.assertTrue(res["ok"])
        self.assertEqual(pending.payment_status, "Confirmed")            # PROMOTION
        self.assertEqual(pending.provider_transaction_id, "TX-1")        # opposabilité + revert
        pending.save.assert_called_once()                                # hook on_payment_update → UF
        mver.assert_called_once_with("TX-1")                             # source de vérité provider
        mcasc.assert_called_once()                                       # cascade partagée
        msend.assert_called_once()                                       # reçu online

    @patch(f"{PUBLIC}.frappe")
    @patch(f"{WEBHOOK}._find_payment_by_reference", return_value=None)
    @patch(f"{WEBHOOK}.frappe")
    def test_no_pending_rejected_409(self, mf, _find, _mfpub):
        # Fin de l'insert fallback : tout paiement online est INITIÉ → webhook orphelin = 409.
        _rq(mf, _payload())
        from admission.api.webhook import payment
        res = payment()
        self.assertFalse(res["ok"])
        self.assertEqual(res["error"]["code"], "PAYMENT_NOT_INITIATED")

    @patch(f"{WEBHOOK}.verify_transaction")
    @patch(f"{WEBHOOK}._find_payment_by_reference")
    @patch(f"{WEBHOOK}.frappe")
    def test_replay_when_already_confirmed(self, mf, mfind, mver):
        _rq(mf, _payload())
        confirmed = _pending(status="Confirmed")
        mfind.return_value = confirmed
        from admission.api.webhook import payment
        res = payment()
        self.assertTrue(res["ok"])
        self.assertTrue(res["data"]["idempotent"])   # vrai replay (retentatives 5×500 ms)
        confirmed.save.assert_not_called()
        mver.assert_not_called()                     # pas d'appel provider inutile au replay

    @patch(f"{WEBHOOK}._find_payment_by_reference")
    @patch(f"{WEBHOOK}.frappe")
    def test_failed_event_rejects_pending(self, mf, mfind):
        _rq(mf, _payload(event="transaction.failed"))
        pending = _pending()
        mfind.return_value = pending
        from admission.api.webhook import payment
        res = payment()
        self.assertTrue(res["ok"])
        self.assertEqual(res["data"]["rejected"], "REC-1")
        # rejet silencieux (pattern expire_stale) : pas de save → pas de hook UF
        mf.db.set_value.assert_called_once_with(
            "Applicant Fee Payment", "REC-1", "payment_status", "Rejected", update_modified=False)
        pending.save.assert_not_called()

    @patch(f"{PUBLIC}.frappe")
    @patch(f"{WEBHOOK}.verify_transaction", return_value=None)
    @patch(f"{WEBHOOK}._find_payment_by_reference")
    @patch(f"{WEBHOOK}.frappe")
    def test_unverified_transaction_rejected(self, mf, mfind, _mver, _mfpub):
        # Fail-closed : payload "success" mais provider injoignable/non-SUCCESS → AUCUNE promotion.
        _rq(mf, _payload())
        pending = _pending()
        mfind.return_value = pending
        from admission.api.webhook import payment
        res = payment()
        self.assertFalse(res["ok"])
        self.assertEqual(res["error"]["code"], "PAYMENT_NOT_VERIFIED")
        pending.save.assert_not_called()

    @patch(f"{PUBLIC}.frappe")
    @patch(f"{WEBHOOK}.verify_transaction", return_value={"status": "SUCCESS", "amount": 500})
    @patch(f"{WEBHOOK}._find_payment_by_reference")
    @patch(f"{WEBHOOK}.frappe")
    def test_amount_mismatch_rejected(self, mf, mfind, _mver, _mfpub):
        # Montant VÉRIFIÉ chez le provider < attendu (modèle plugin officiel : amount >= total).
        _rq(mf, _payload())
        pending = _pending(amount=15000)
        mfind.return_value = pending
        from admission.api.webhook import payment
        res = payment()
        self.assertFalse(res["ok"])
        self.assertEqual(res["error"]["code"], "AMOUNT_MISMATCH")
        pending.save.assert_not_called()

    @patch(f"{WEBHOOK}.verify_transaction", return_value={"status": "SUCCESS", "amount": 15000})
    @patch(f"{WEBHOOK}._find_payment_by_reference")
    @patch(f"{WEBHOOK}.frappe")
    def test_success_on_rejected_pending_not_promoted(self, mf, mfind, _mver):
        # Argent encaissé provider sur Pending déjà rejeté (désistement/clôture W) :
        # pas de promotion — alerte OPS (refund manuel), réponse 2xx (pas de retry inutile).
        _rq(mf, _payload())
        rejected = _pending(status="Rejected")
        mfind.return_value = rejected
        from admission.api.webhook import payment
        res = payment()
        self.assertTrue(res["ok"])
        self.assertFalse(res["data"]["promoted"])
        rejected.save.assert_not_called()
        mf.log_error.assert_called_once()


class TestWebhookTransport(TestCase):
    @patch(f"{PUBLIC}.frappe")
    @patch(f"{WEBHOOK}.frappe")
    def test_missing_body_rejected_400(self, mf, _mfpub):
        mf.request = None  # pas de corps JSON
        from admission.api.webhook import payment
        res = payment()
        self.assertFalse(res["ok"])
        self.assertEqual(res["error"]["code"], "WEBHOOK_PAYLOAD_INVALID")

    def test_extract_reference_handles_statedata_string(self):
        # KkiaPay peut restituer stateData en CHAÎNE JSON selon le canal.
        from admission.api.webhook import _extract_reference
        self.assertEqual(
            _extract_reference({"stateData": json.dumps({"reference": "REF-9"})}), "REF-9")
        self.assertEqual(_extract_reference({"stateData": {"reference": "REF-8"}}), "REF-8")
        self.assertEqual(_extract_reference({"reference": "REF-7"}), "REF-7")  # compat simulateur
        self.assertIsNone(_extract_reference({}))


class TestFindPaymentByReference(TestCase):
    @patch(f"{WEBHOOK}.frappe")
    def test_returns_none_without_reference(self, mf):
        from admission.api.webhook import _find_payment_by_reference
        self.assertIsNone(_find_payment_by_reference(None))
        mf.get_all.assert_not_called()

    @patch(f"{WEBHOOK}.frappe")
    def test_returns_doc_when_found(self, mf):
        mf.get_all.return_value = ["REC-1"]
        doc = MagicMock()
        mf.get_doc.return_value = doc
        from admission.api.webhook import _find_payment_by_reference
        self.assertIs(_find_payment_by_reference("REF-1"), doc)
