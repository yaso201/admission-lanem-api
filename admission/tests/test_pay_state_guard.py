"""D-CONF-01 — garde d'état sur la promotion de paiement (argent JAMAIS Confirmed sur dossier terminal).

Verrou 1 (LE GARANT) : `_promote_payment` refuse si `applicant.status ∈ PAYMENT_FORBIDDEN_STATES`
{DES,REF,REJ,INS}, aux DEUX chemins de promotion (Pending→Confirmed ET Rejected→Confirmed « Promoted
late ») → trace refund, ne confirme pas, le webhook ne 500 jamais. Verrou 2 : withdraw/close_session
rejettent AUSSI le Pending Online. Verrou 3 : `submit_payment_online` refuse d'initier sur dossier clos.
Style unitaire mocké (miroir test_webhook_promotion) ; la preuve real-DB est audit_bloc4 (D-CONF-01 inversé).
"""

import json
from unittest import TestCase
from unittest.mock import MagicMock, patch

WEBHOOK = "admission.api.webhook"
PUBLIC = "admission.api.public"
STAFF = "admission.api.staff"
SECRET = "whsecret"


def _payload(ref="REF-1", event="transaction.success", tx="TX-1", amount=15000):
    return {"transactionId": tx, "event": event,
            "isPaymentSucces": event == "transaction.success",
            "amount": amount, "method": "MOBILE_MONEY",
            "stateData": {"reference": ref, "sdk": "lanem-admission"}}


def _rq(mf, payload, header=SECRET):
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


def _wire(mf, mfind, pending, applicant_status, st):
    """Câble un webhook SUCCESS vérifié : Pending lié, statut re-lu sous verrou = `st`, dossier de
    statut `applicant_status`. Renvoie l'applicant mock."""
    _rq(mf, _payload())
    mfind.return_value = pending
    mf.db.get_value.return_value = st           # re-lecture du statut payment SOUS verrou
    mf.db.exists.return_value = False            # aucun autre Confirmed sur le fee
    applicant = MagicMock(); applicant.name = "CAN-2026-00001"; applicant.status = applicant_status
    fee = MagicMock()
    mf.get_doc.side_effect = lambda dt, name=None: applicant if dt == "Admission Applicant" else fee
    return applicant


class TestPromotionStateGuard(TestCase):
    @patch(f"{WEBHOOK}.notify_uf_payment")
    @patch(f"{WEBHOOK}.send_payment_receipt")
    @patch(f"{WEBHOOK}.apply_confirmed_payment_cascade")
    @patch(f"{WEBHOOK}.verify_transaction", return_value={"status": "SUCCESS", "amount": 15000})
    @patch(f"{WEBHOOK}.now_datetime", return_value="2026-06-13 10:00:00")
    @patch(f"{WEBHOOK}._find_payment_by_reference")
    @patch(f"{WEBHOOK}.frappe")
    def test_p1_valid_state_promotes(self, mf, mfind, _now, _mver, mcasc, _msend, _mn):
        """P1 (non-régression) : dossier NON terminal (SOP) → promotion NOMINALE (Confirmed + cascade)."""
        pending = _pending()
        _wire(mf, mfind, pending, "SOP", "Pending")
        from admission.api.webhook import payment
        res = payment()
        self.assertTrue(res["ok"])
        self.assertEqual(pending.payment_status, "Confirmed")   # la garde ne bloque PAS un état vivant
        mcasc.assert_called_once()

    @patch(f"{WEBHOOK}.apply_confirmed_payment_cascade")
    @patch(f"{WEBHOOK}.verify_transaction", return_value={"status": "SUCCESS", "amount": 15000})
    @patch(f"{WEBHOOK}._find_payment_by_reference")
    @patch(f"{WEBHOOK}.frappe")
    def test_p2_pending_on_des_refused(self, mf, mfind, _mver, mcasc):
        """P2 : Pending sur DES → promotion REFUSÉE (pas Confirmed, cascade NON appelée, refund tracé)."""
        pending = _pending()
        _wire(mf, mfind, pending, "DES", "Pending")
        from admission.api.webhook import payment
        res = payment()
        self.assertTrue(res["ok"])                        # pas de 500 (webhook headless)
        self.assertFalse(res["data"]["promoted"])          # refusé
        self.assertTrue(res["data"]["refused_terminal"])
        self.assertNotEqual(pending.payment_status, "Confirmed")
        mcasc.assert_not_called()                          # ZÉRO effet aval : pas de fee Paid
        traced = any("Refused - terminal state" in str(c) for c in mf.db.set_value.call_args_list)
        self.assertTrue(traced)                            # refund tracé (reconciliation posée)

    @patch(f"{WEBHOOK}.apply_confirmed_payment_cascade")
    @patch(f"{WEBHOOK}.verify_transaction", return_value={"status": "SUCCESS", "amount": 15000})
    @patch(f"{WEBHOOK}._find_payment_by_reference")
    @patch(f"{WEBHOOK}.frappe")
    def test_p3_each_terminal_state_refused(self, mf, mfind, _mver, mcasc):
        """P3 : REF / REJ / INS → chaque état terminal refuse la promotion."""
        from admission.api.webhook import payment
        for state in ("REF", "REJ", "INS"):
            with self.subTest(state=state):
                mcasc.reset_mock()
                pending = _pending()
                _wire(mf, mfind, pending, state, "Pending")
                res = payment()
                self.assertTrue(res["ok"])
                self.assertFalse(res["data"]["promoted"])
                self.assertNotEqual(pending.payment_status, "Confirmed")
                mcasc.assert_not_called()

    @patch(f"{WEBHOOK}.apply_confirmed_payment_cascade")
    @patch(f"{WEBHOOK}.verify_transaction", return_value={"status": "SUCCESS", "amount": 15000})
    @patch(f"{WEBHOOK}._find_payment_by_reference")
    @patch(f"{WEBHOOK}.frappe")
    def test_p6b_reconciliation_path_on_des_refused(self, mf, mfind, _mver, mcasc):
        """P6b (LE POINT CLÉ) : un Pending REJETÉ (par withdraw, verrou 2) sur DES → le chemin de
        réconciliation « Promoted late » (Rejected→Confirmed) est AUSSI gardé → refusé. Rejeter ne
        suffit pas ; seule la garde d'état (verrou 1) est le garant."""
        pending = _pending(status="Rejected")
        _wire(mf, mfind, pending, "DES", "Rejected")
        from admission.api.webhook import payment
        res = payment()
        self.assertTrue(res["ok"])
        self.assertFalse(res["data"]["promoted"])
        self.assertNotEqual(pending.payment_status, "Confirmed")
        mcasc.assert_not_called()


class TestRejectPendingIncludesOnline(TestCase):
    @patch(f"{STAFF}.log_event")
    @patch(f"{STAFF}.frappe")
    def test_p6a_reject_pending_covers_online(self, mf, _mlog):
        """Verrou 2 : _reject_pending_payments rejette AUSSI l'Online (pas seulement Cash/Bank)."""
        mf.get_all.return_value = ["REC-ON"]
        from admission.api.staff import _reject_pending_payments
        _reject_pending_payments("CAN-1")
        filters = mf.get_all.call_args.kwargs["filters"]
        self.assertIn("Online", filters["payment_mode"][1])
        mf.db.set_value.assert_any_call("Applicant Fee Payment", "REC-ON", "payment_status",
                                        "Rejected", update_modified=False)


class TestSubmitOnlineStateGuard(TestCase):
    @patch(f"{PUBLIC}._require_otp_verified", return_value=None)
    @patch(f"{PUBLIC}._get_applicant")
    @patch(f"{PUBLIC}.frappe")
    def test_p9_initiation_refused_on_terminal(self, _mf, mget, _motp):
        """Verrou 3 (défense en profondeur) : submit_payment_online refuse d'initier sur dossier clos."""
        applicant = MagicMock(); applicant.name = "CAN-1"; applicant.status = "DES"
        mget.return_value = applicant
        from admission.api.public import submit_payment_online
        res = submit_payment_online(dossier_id="CAN-1", token="tok", consent_refund=1)
        self.assertFalse(res["ok"])
        self.assertEqual(res["error"]["code"], "INVALID_STATE")
