"""Preuve concurrence RÉELLE — D-RACE-FEE-01 (verrou fee-level, PC2-ter).

⚠️ HARNESS DE DÉPLOIEMENT — NE TOURNE PAS sur le runner unitaire mické.
Un mock ne peut PAS sérialiser deux threads : la preuve d'une protection concurrente sur un
flux argent se fait sur DEUX VRAIS THREADS contre la VRAIE DB (cf. mémoire feedback
« concurrence : preuve réelle »). T10 prouve seulement que le `for_update` est ÉMIS ; CE test
prouve l'EFFET (sérialisation observée). C'est l'élément BLOQUANT de gate CLOSE pour D-RACE-FEE-01.

Pré-requis d'exécution (au déploiement, serveur) :
  - MariaDB up, champ `reconciliation` MIGRÉ (`bench migrate` fait) ;
  - secret webhook configuré (`admission_payment_webhook_secret`) ;
  - lancer : `bench --site <site> run-tests --module admission.tests.test_concurrence_fee_lock`.

Scénario (le cas exact de la vague) : 2 Payment DISTINCTS du MÊME fee, références distinctes,
tous deux verify=SUCCESS. 2 webhooks concurrents (2 threads, 2 connexions). Le verrou fee-level
(`SELECT … FOR UPDATE` sur Applicant Fee, posé AVANT le check fee_resolved) doit sérialiser :
→ EXACTEMENT 1 `Confirmed` + 1 `Orphan - refund due` sur le fee. Jamais 2 Confirmed.

Périmètre de la preuve = la zone de course : verrou Payment + verrou Fee + check fee_resolved +
écriture du statut. La cascade métier / la notif UF / le reçu sont POSTÉRIEURS à la décision de
verrou (hors course) → stubés dans les threads pour isoler la sérialisation (et éviter d'exiger
un cycle de vie Applicant complet dans la fixture).
"""

import json
import threading
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

WEBHOOK = "admission.api.webhook"
_MARK = "ZZCONC"  # marqueur de fixtures pour purge ciblée


def _stub(*args, **kwargs):
    return None


class TestConcurrenceFeeLockReal(FrappeTestCase):
    """Sérialisation réelle 2-threads du verrou fee-level (D-RACE-FEE-01)."""

    def setUp(self):
        # Fixtures réelles minimales : un Applicant + un Fee + 2 Pending même fee (refs distinctes).
        # ignore_mandatory : on ne veut PAS un cycle de vie complet (la cascade est stubée), juste
        # des lignes existantes que _promote_payment peut get_doc + verrouiller.
        self.applicant = frappe.get_doc({
            "doctype": "Admission Applicant",
            "applicant_name": f"{_MARK} Concurrence",
            "status": "BRO",
        }).insert(ignore_permissions=True, ignore_mandatory=True)

        self.fee = frappe.get_doc({
            "doctype": "Applicant Fee",
            "applicant": self.applicant.name,
            "amount_xof": 15000,
            "status": "Pending",
        }).insert(ignore_permissions=True, ignore_mandatory=True)

        self.ref_a, self.ref_b = f"{_MARK}-REF-A", f"{_MARK}-REF-B"
        self.pay_a = self._make_pending(self.ref_a)
        self.pay_b = self._make_pending(self.ref_b)
        frappe.db.commit()  # rendre les fixtures visibles aux connexions des threads

    def _make_pending(self, ref):
        return frappe.get_doc({
            "doctype": "Applicant Fee Payment",
            "applicant": self.applicant.name,
            "applicant_fee": self.fee.name,
            "payment_mode": "Online",
            "amount_xof": 15000,
            "payment_status": "Pending",
            "provider": "kkiapay",
            "provider_reference": ref,
        }).insert(ignore_permissions=True, ignore_mandatory=True).name

    def tearDown(self):
        # Purge EXPLICITE : les threads commitent sur des connexions séparées → hors du rollback
        # du FrappeTestCase. On supprime tout ce qui porte le marqueur (cf. feedback purge test data).
        frappe.db.rollback()
        for name in (self.pay_a, self.pay_b):
            frappe.db.delete("Applicant Fee Payment", {"name": name})
        frappe.db.delete("Applicant Fee", {"name": self.fee.name})
        frappe.db.delete("Admission Applicant", {"name": self.applicant.name})
        frappe.db.commit()

    def _worker(self, ref, site, secret, errors):
        """Un webhook success complet dans un thread, sur sa PROPRE connexion DB.
        Les stubs (verify/cascade/notif/reçu) sont posés UNE FOIS par le thread principal AVANT le
        démarrage : `unittest.mock.patch` n'est PAS thread-safe sur un attribut global de module
        (un patch/unpatch concurrent restaurerait la vraie cascade sous l'autre thread)."""
        frappe.init(site=site)
        frappe.connect()
        try:
            frappe.local.request = frappe._dict(
                data=json.dumps({"transactionId": f"TX-{ref}",
                                 "event": "transaction.success",
                                 "stateData": {"reference": ref}}),
                headers={"x-kkiapay-secret": secret},
            )
            from admission.api.webhook import payment
            payment()
        except Exception as exc:  # noqa: BLE001 — on remonte au thread principal
            errors.append((ref, repr(exc)))
        finally:
            frappe.destroy()

    def test_two_concurrent_success_one_confirmed_one_orphan(self):
        site = frappe.local.site
        secret = frappe.conf.get("admission_payment_webhook_secret")
        self.assertTrue(secret, "secret webhook requis (admission_payment_webhook_secret)")
        errors = []
        threads = [
            threading.Thread(target=self._worker, args=(self.ref_a, site, secret, errors)),
            threading.Thread(target=self._worker, args=(self.ref_b, site, secret, errors)),
        ]
        # Stubs posés UNE FOIS (thread-safe), AVANT le démarrage : I/O KkiaPay (verify) + effets aval
        # (cascade/notif/reçu). Le VRAI chemin verrou (for_update Payment+Fee, fee_resolved, save
        # statut, commit) tourne RÉEL dans chaque thread → c'est lui qu'on prouve.
        with patch(f"{WEBHOOK}.verify_transaction", return_value={"status": "SUCCESS", "amount": 15000}), \
             patch(f"{WEBHOOK}.apply_confirmed_payment_cascade", _stub), \
             patch(f"{WEBHOOK}.notify_uf_payment", _stub), \
             patch(f"{WEBHOOK}.send_payment_receipt", _stub):
            for t in threads:
                t.start()
            for t in threads:
                t.join()
        self.assertEqual(errors, [], f"erreur(s) thread : {errors}")

        # VÉRITÉ = état DB sérialisé (pas un mock). On relit depuis une connexion fraîche.
        frappe.db.rollback()
        confirmed = frappe.get_all("Applicant Fee Payment", filters={
            "applicant_fee": self.fee.name, "payment_status": "Confirmed"}, pluck="name")
        orphans = frappe.get_all("Applicant Fee Payment", filters={
            "applicant_fee": self.fee.name, "reconciliation": "Orphan - refund due"}, pluck="name")

        self.assertEqual(len(confirmed), 1,
                         f"EXACTEMENT 1 Confirmed attendu (sérialisation fee), obtenu {confirmed}")
        self.assertEqual(len(orphans), 1,
                         f"EXACTEMENT 1 orphelin attendu, obtenu {orphans}")
        # le Confirmed et l'orphelin sont deux Payment DISTINCTS du même fee
        self.assertNotEqual(confirmed[0], orphans[0])
