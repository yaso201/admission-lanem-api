"""Preuve idempotence RÉELLE — rejeu webhook FedaPay ×3 sur la MÊME référence → 1 SEULE transition.

Gate 4 (PAIEMENT-FEDAPAY), preuve « chiffrée ». Un agrégateur réémet le webhook (retentatives
réseau : FedaPay peut livrer plusieurs fois le MÊME évènement). Rejouer le même
`provider_reference` 3× ne doit produire :
  - qu'UNE promotion Pending→Confirmed  (1 seul Confirmed sur le fee) ;
  - qu'UN seul effet compta             (cascade appelée EXACTEMENT 1 fois) ;
  - qu'UNE re-vérification serveur       (verify appelé 1 fois : les rejeux court-circuitent au
    pré-check replay AVANT tout appel provider) ;
  - 2 réponses `idempotent=True`         (appels 2 et 3).

Preuve sur DB RÉELLE (cf. feedback « concurrence/idempotence : preuve réelle ») : l'état
Confirmed doit PERSISTER entre les appels pour que le pré-check replay le voie — un mock ne le
prouverait pas (il ne persiste pas la transition). Style aligné sur test_concurrence_fee_lock.py.
"""

import hashlib
import hmac
import json
from unittest.mock import MagicMock, patch

import frappe
from frappe.tests.utils import FrappeTestCase

WEBHOOK = "admission.api.webhook"
_MARK = "ZZIDEMP"
_AMOUNT = 15000


def _fedapay_sig(secret, body, ts="1700000000"):
    """Signature FedaPay VALIDE : en-tête `t=<ts>,s=<hash>`, hash = HMAC-SHA256(secret, `<ts>.<corps brut>`)."""
    payload = ts.encode("utf-8") + b"." + body.encode("utf-8")
    return f"t={ts},s=" + hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()


class TestIdempotenceReplayReal(FrappeTestCase):
    """Rejeu réel ×3 du webhook sur une même référence : exactement 1 transition/compta."""

    def _purge(self):
        apps = frappe.get_all("Admission Applicant",
                              filters={"applicant_name": ["like", f"{_MARK}%"]}, pluck="name")
        if apps:
            frappe.db.delete("Applicant Fee Payment", {"applicant": ["in", apps]})
            frappe.db.delete("Applicant Fee", {"applicant": ["in", apps]})
            frappe.db.delete("Admission Applicant", {"name": ["in", apps]})
        frappe.db.delete("Applicant Fee Payment", {"provider_reference": ["like", f"{_MARK}%"]})
        frappe.db.commit()

    def setUp(self):
        self._purge()
        self.applicant = frappe.get_doc({
            "doctype": "Admission Applicant",
            "applicant_name": f"{_MARK} Idempotence",
            "status": "BRO",
        }).insert(ignore_permissions=True, ignore_mandatory=True)
        self.fee = frappe.get_doc({
            "doctype": "Applicant Fee",
            "applicant": self.applicant.name,
            "amount_xof": _AMOUNT,
            "status": "Pending",
        }).insert(ignore_permissions=True, ignore_mandatory=True)
        self.ref = f"{_MARK}-REF"
        self.pay = frappe.get_doc({
            "doctype": "Applicant Fee Payment",
            "applicant": self.applicant.name,
            "applicant_fee": self.fee.name,
            "payment_mode": "Online",
            "amount_xof": _AMOUNT,
            "payment_status": "Pending",
            "provider": "fedapay",
            "provider_reference": self.ref,
        }).insert(ignore_permissions=True, ignore_mandatory=True).name
        frappe.db.commit()

    def tearDown(self):
        frappe.db.rollback()
        self._purge()

    def _fire(self, secret, out):
        """Un webhook FedaPay SUCCESS signé sur la référence self.ref (connexion courante)."""
        body = json.dumps({"name": "transaction.approved",
                           "entity": {"id": f"TX-{self.ref}", "status": "approved",
                                      "custom_metadata": {"provider_reference": self.ref}}})
        frappe.local.request = frappe._dict(
            data=body, headers={"x-fedapay-signature": _fedapay_sig(secret, body)})
        from admission.api.webhook import payment
        out.append(payment())

    def test_replay_x3_single_transition(self):
        secret = frappe.conf.get("fedapay_webhook_secret")
        self.assertTrue(secret, "secret webhook requis (fedapay_webhook_secret)")

        verify = MagicMock(return_value={"status": "SUCCESS", "amount": _AMOUNT})
        cascade = MagicMock()
        results = []
        # verify + effets aval stubés/comptés ; le VRAI cœur (pré-check replay, promotion, save,
        # index, commit) tourne réel → c'est lui qu'on prouve idempotent.
        with patch(f"{WEBHOOK}.verify_transaction", verify), \
             patch(f"{WEBHOOK}.apply_confirmed_payment_cascade", cascade), \
             patch(f"{WEBHOOK}.notify_uf_payment", MagicMock()), \
             patch(f"{WEBHOOK}.send_payment_receipt", MagicMock()):
            for _ in range(3):
                self._fire(secret, results)
                frappe.db.commit()  # rendre la transition visible au rejeu suivant (persistance réelle)

        # ── Preuve chiffrée ──────────────────────────────────────────────────────
        self.assertEqual(len(results), 3, "3 rejeux émis")
        self.assertTrue(all(r.get("ok") for r in results), f"3 réponses ok attendues : {results}")

        frappe.db.rollback()  # relire l'état DB depuis une vue fraîche (vérité = base)
        confirmed = frappe.get_all("Applicant Fee Payment", filters={
            "applicant_fee": self.fee.name, "payment_status": "Confirmed"}, pluck="name")
        self.assertEqual(len(confirmed), 1, f"EXACTEMENT 1 Confirmed après 3 rejeux, obtenu {confirmed}")

        self.assertEqual(cascade.call_count, 1,
                         f"cascade compta appelée EXACTEMENT 1 fois, obtenu {cascade.call_count}")
        self.assertEqual(verify.call_count, 1,
                         f"re-vérification serveur 1 fois (rejeux court-circuités au pré-check), "
                         f"obtenu {verify.call_count}")

        idem = [r for r in results if (r.get("data") or {}).get("idempotent")]
        self.assertEqual(len(idem), 2, f"appels 2 et 3 idempotents attendus, obtenu {len(idem)}")

        txid = frappe.db.get_value("Applicant Fee Payment", confirmed[0], "provider_transaction_id")
        self.assertEqual(txid, f"TX-{self.ref}", "txid opposable posé une fois")

        print(f"\n[IDEMP ×3] MÊME ref rejouée 3× → Confirmed={len(confirmed)} | cascade(compta)="
              f"{cascade.call_count} | verify={verify.call_count} | idempotent={len(idem)}/3")
