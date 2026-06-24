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
    @patch(f"{WEBHOOK}.notify_uf_payment")
    @patch(f"{WEBHOOK}.send_payment_receipt")
    @patch(f"{WEBHOOK}.apply_confirmed_payment_cascade")
    @patch(f"{WEBHOOK}.verify_transaction", return_value={"status": "SUCCESS", "amount": 15000})
    @patch(f"{WEBHOOK}.now_datetime", return_value="2026-06-13 10:00:00")
    @patch(f"{WEBHOOK}._find_payment_by_reference")
    @patch(f"{WEBHOOK}.frappe")
    def test_promotes_existing_pending(self, mf, mfind, _now, mver, mcasc, msend, _mnotify):
        _rq(mf, _payload())
        pending = _pending()
        mfind.return_value = pending
        mf.db.get_value.return_value = "Pending"   # re-lecture du statut SOUS verrou (C1)
        mf.db.exists.return_value = False           # aucun autre Confirmed sur le fee (cas nominal)
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
        mf.db.get_value.return_value = "Pending"   # re-lecture du statut SOUS verrou (DEC-5)
        from admission.api.webhook import payment
        res = payment()
        self.assertTrue(res["ok"])
        self.assertEqual(res["data"]["rejected"], "REC-1")
        # rejet sous verrou (re-lecture for_update) ; pas de save → pas de hook UF
        mf.db.get_value.assert_called_once_with(
            "Applicant Fee Payment", "REC-1", "payment_status", for_update=True)
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
    def test_underpaid_success_traced(self, mf, mfind, _mver, _mfpub):
        # C2 : verify=SUCCESS mais montant insuffisant → l'argent a bougé (DEC-4, jamais de drop) :
        # trace 'Underpaid - review' + txid, PAS de promotion.
        _rq(mf, _payload())
        pending = _pending(amount=15000)
        mfind.return_value = pending
        mf.db.get_value.return_value = "Pending"
        from admission.api.webhook import payment
        res = payment()
        self.assertFalse(res["ok"])
        self.assertEqual(res["error"]["code"], "AMOUNT_MISMATCH")
        mf.db.set_value.assert_called_once_with(
            "Applicant Fee Payment", "REC-1",
            {"reconciliation": "Underpaid - review", "provider_transaction_id": "TX-1"},
            update_modified=False)
        pending.save.assert_not_called()

    # ── VAGUE-PAY-FIX : réconciliation du success tardif (verify-AVANT-abandon) ──────
    @patch(f"{WEBHOOK}.notify_uf_payment")
    @patch(f"{WEBHOOK}.send_payment_receipt")
    @patch(f"{WEBHOOK}.apply_confirmed_payment_cascade")
    @patch(f"{WEBHOOK}.verify_transaction", return_value={"status": "SUCCESS", "amount": 15000})
    @patch(f"{WEBHOOK}.now_datetime", return_value="2026-06-13 10:00:00")
    @patch(f"{WEBHOOK}._find_payment_by_reference")
    @patch(f"{WEBHOOK}.frappe")
    def test_late_success_promotes_when_fee_unresolved(self, mf, mfind, _now, mver, mcasc, msend, _mnotify):
        # G1/case2 : failed->success même référence (Pending rejeté puis success vérifié), fee
        # non encore crédité → PROMOTION tardive (réconciliation) — aucun paiement perdu.
        _rq(mf, _payload())
        rejected = _pending(status="Rejected")
        mfind.return_value = rejected
        mf.db.get_value.return_value = "Rejected"     # re-lecture du statut SOUS verrou
        mf.db.exists.return_value = False             # aucun autre Confirmed sur le fee
        applicant = MagicMock(); fee = MagicMock()
        mf.get_doc.side_effect = lambda dt, name=None: applicant if dt == "Admission Applicant" else fee
        from admission.api.webhook import payment
        res = payment()
        self.assertTrue(res["ok"])
        self.assertEqual(rejected.payment_status, "Confirmed")        # promu
        self.assertEqual(rejected.reconciliation, "Promoted late")    # trace D-OBS-01
        self.assertEqual(rejected.provider_transaction_id, "TX-1")
        rejected.save.assert_called_once()
        mcasc.assert_called_once()                                    # cascade BRO/SOP->SOU
        mver.assert_called_once_with("TX-1")

    @patch(f"{WEBHOOK}.apply_confirmed_payment_cascade")
    @patch(f"{WEBHOOK}.verify_transaction", return_value={"status": "SUCCESS", "amount": 15000})
    @patch(f"{WEBHOOK}._find_payment_by_reference")
    @patch(f"{WEBHOOK}.frappe")
    def test_late_success_orphan_when_fee_already_paid(self, mf, mfind, _mver, mcasc):
        # G2 : success vérifié sur Rejected MAIS le fee a déjà un Confirmed (doublon réel) →
        # ORPHELIN tracé (refund OPS), AUCUNE double promotion.
        _rq(mf, _payload())
        rejected = _pending(status="Rejected")
        mfind.return_value = rejected
        mf.db.get_value.return_value = "Rejected"
        mf.db.exists.return_value = True              # un autre Confirmed existe sur le fee
        from admission.api.webhook import payment
        res = payment()
        self.assertTrue(res["ok"])
        self.assertFalse(res["data"]["promoted"])
        self.assertTrue(res["data"]["orphan"])
        mcasc.assert_not_called()                     # 0 double promotion
        rejected.save.assert_not_called()
        mf.db.set_value.assert_called_once_with(
            "Applicant Fee Payment", "REC-1",
            {"reconciliation": "Orphan - refund due", "provider_transaction_id": "TX-1"},
            update_modified=False)

    @patch(f"{WEBHOOK}.apply_confirmed_payment_cascade")
    @patch(f"{WEBHOOK}.verify_transaction", return_value={"status": "SUCCESS", "amount": 15000})
    @patch(f"{WEBHOOK}._find_payment_by_reference")
    @patch(f"{WEBHOOK}.frappe")
    def test_success_on_pending_orphan_when_fee_already_paid(self, mf, mfind, _mver, mcasc):
        # T9 / D-SEQ-FEE-02 : success vérifié sur un Pending MAIS le fee a déjà un AUTRE Confirmed
        # (2 tentatives même fee, la 2ᵉ a confirmé, la 1ʳᵉ Pending reçoit un success tardif) →
        # ORPHELIN tracé, AUCUNE double promotion. Le check fee_resolved gouverne aussi le Pending
        # (sinon double-crédit déterministe — pas qu'une course).
        _rq(mf, _payload())
        pending = _pending(status="Pending")
        mfind.return_value = pending
        mf.db.get_value.return_value = "Pending"     # re-lecture sous verrou : toujours Pending
        mf.db.exists.return_value = True             # un autre Confirmed existe déjà sur le fee
        from admission.api.webhook import payment
        res = payment()
        self.assertTrue(res["ok"])
        self.assertFalse(res["data"]["promoted"])
        self.assertTrue(res["data"]["orphan"])
        mcasc.assert_not_called()                     # 0 double promotion
        pending.save.assert_not_called()
        mf.db.set_value.assert_called_once_with(
            "Applicant Fee Payment", "REC-1",
            {"reconciliation": "Orphan - refund due", "provider_transaction_id": "TX-1"},
            update_modified=False)

    @patch(f"{PUBLIC}.frappe")
    @patch(f"{WEBHOOK}.verify_transaction", return_value=None)
    @patch(f"{WEBHOOK}._find_payment_by_reference")
    @patch(f"{WEBHOOK}.frappe")
    def test_desistement_unverified_on_rejected_stays(self, mf, mfind, _mver, _mfpub):
        # G3 : success non vérifiable (provider != SUCCESS) sur Rejected = vrai désistement →
        # reste Rejected, AUCUNE trace orpheline, AUCUNE mutation.
        _rq(mf, _payload())
        rejected = _pending(status="Rejected")
        mfind.return_value = rejected
        from admission.api.webhook import payment
        res = payment()
        self.assertTrue(res["ok"])
        self.assertFalse(res["data"]["promoted"])
        rejected.save.assert_not_called()
        mf.db.set_value.assert_not_called()           # pas un succès → pas de trace

    @patch(f"{WEBHOOK}.apply_confirmed_payment_cascade")
    @patch(f"{WEBHOOK}.verify_transaction", return_value={"status": "SUCCESS", "amount": 15000})
    @patch(f"{WEBHOOK}._find_payment_by_reference")
    @patch(f"{WEBHOOK}.frappe")
    def test_lock_acquired_and_idempotent_under_race(self, mf, mfind, _mver, mcasc):
        # G4 (révisé) : (a) for_update AVANT mutation ; (b) re-lecture sous verrou ; (c) si un
        # autre webhook a promu entre verify et verrou (statut re-lu = Confirmed) → no-op.
        _rq(mf, _payload())
        pending = _pending(status="Pending")          # pré-check replay passe
        mfind.return_value = pending
        mf.db.get_value.return_value = "Confirmed"     # course : déjà promu sous verrou
        from admission.api.webhook import payment
        res = payment()
        self.assertTrue(res["ok"])
        self.assertTrue(res["data"]["idempotent"])     # promotion unique
        mf.db.get_value.assert_called_once_with(
            "Applicant Fee Payment", "REC-1", "payment_status", for_update=True)
        mcasc.assert_not_called()                      # pas de seconde promotion
        pending.save.assert_not_called()

    @patch(f"{WEBHOOK}.notify_uf_payment")
    @patch(f"{WEBHOOK}.send_payment_receipt")
    @patch(f"{WEBHOOK}.apply_confirmed_payment_cascade")
    @patch(f"{WEBHOOK}.verify_transaction", return_value={"status": "SUCCESS", "amount": 15000})
    @patch(f"{WEBHOOK}.now_datetime", return_value="2026-06-13 10:00:00")
    @patch(f"{WEBHOOK}._find_payment_by_reference")
    @patch(f"{WEBHOOK}.frappe")
    def test_unique_violation_concurrent_routes_to_orphan(self, mf, mfind, _now, mver, mcasc, msend, _mnotify):
        # T12 / R3 (D-RACE-FEE-01) : CONCURRENCE. Fee vu non résolu (check séquentiel) mais un AUTRE
        # webhook a confirmé le même fee entre le check et le save → l'INDEX UNIQUE DB lève
        # UniqueValidationError au save (AVANT cascade) → orphelin (refund OPS), 0 double promotion.
        # Le garant concurrent RÉEL est l'index (prouvé par le harness 2-threads) ; T12 couvre le ROUTAGE.
        import frappe as _real_frappe
        _rq(mf, _payload())
        pending = _pending()
        mfind.return_value = pending
        mf.db.get_value.return_value = "Pending"
        mf.db.exists.return_value = False                          # vu non résolu → entre en promotion
        mf.UniqueValidationError = _real_frappe.UniqueValidationError   # except attrape la VRAIE classe
        pending.save.side_effect = _real_frappe.UniqueValidationError   # course perdue à l'index unique
        applicant = MagicMock(); fee = MagicMock()
        mf.get_doc.side_effect = lambda dt, name=None: applicant if dt == "Admission Applicant" else fee
        from admission.api.webhook import payment
        res = payment()
        self.assertTrue(res["ok"])
        self.assertTrue(res["data"]["orphan"])                    # course perdue → orphelin
        self.assertFalse(res["data"]["promoted"])                 # PAS de 2ᵉ promotion
        # ZÉRO EFFET AVAL pour le perdant : le save lève AVANT cascade (l.106) ET reçu (l.113)
        mcasc.assert_not_called()                                 # pas de double SOU (cascade dossier)
        msend.assert_not_called()                                 # pas de 2ᵉ reçu généré
        _mnotify.assert_not_called()                              # pas de 2ᵉ notif UF
        mf.db.rollback.assert_called_once()                       # save partiel annulé avant la trace
        mf.db.set_value.assert_called_with(                       # trace orphelin posée
            "Applicant Fee Payment", "REC-1",
            {"reconciliation": "Orphan - refund due", "provider_transaction_id": "TX-1"},
            update_modified=False)

    @patch(f"{WEBHOOK}.notify_uf_payment")
    @patch(f"{WEBHOOK}.send_payment_receipt")
    @patch(f"{WEBHOOK}.apply_confirmed_payment_cascade")
    @patch(f"{WEBHOOK}.verify_transaction", return_value={"status": "SUCCESS", "amount": 15000})
    @patch(f"{WEBHOOK}.now_datetime", return_value="2026-06-13 10:00:00")
    @patch(f"{WEBHOOK}._find_payment_by_reference")
    @patch(f"{WEBHOOK}.frappe")
    def test_uf_notify_after_commit_outside_lock(self, mf, mfind, _now, mver, mcasc, msend, mnotify):
        # T11 / D-LOCK-IO-04 : la notif UF fait un POST HTTP synchrone (15s). Elle est SORTIE de la
        # fenêtre verrouillée → ORDRE save (sous verrou) → commit (relâche Payment+Fee) → notify (HORS
        # verrou). Aucune I/O externe tenue sous verrou (respecte C1). Le hook on_payment_update est
        # supprimé sous verrou via le flag de ré-entrance ; la notif est ré-émise explicitement après commit.
        _rq(mf, _payload())
        pending = _pending()
        mfind.return_value = pending
        mf.db.get_value.return_value = "Pending"
        mf.db.exists.return_value = False
        applicant = MagicMock(); fee = MagicMock()
        mf.get_doc.side_effect = lambda dt, name=None: applicant if dt == "Admission Applicant" else fee
        order = []
        pending.save.side_effect = lambda *a, **k: order.append("save")
        mf.db.commit.side_effect = lambda *a, **k: order.append("commit")
        mnotify.side_effect = lambda *a, **k: order.append("notify")
        from admission.api.webhook import payment
        res = payment()
        self.assertTrue(res["ok"])
        self.assertEqual(order, ["save", "commit", "notify"])          # notif APRÈS commit (hors verrou)
        mnotify.assert_called_once()                                   # UF notifié une fois, post-commit


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
