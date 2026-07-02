"""Robustesse de l'invariant argent INTER-MODES (RECO-BANCAIRE / DEC-PAY-RECON).

Invariant : pour un `Applicant Fee` donné, il existe AU PLUS UN `Applicant Fee Payment`
en statut `Confirmed`, tous modes confondus (KkiaPay webhook ET validation manuelle staff).
R3 (D-RACE-FEE-01) le garantit par l'index UNIQUE sur la colonne générée
`confirmed_fee = if(payment_status='Confirmed', applicant_fee, NULL)` — path-agnostique.

Ce harnais PROUVE deux dimensions DISTINCTES, sur DB réelle, threads réels :
  (1) INVARIANT (argent)      : count(Confirmed where fee=X) == 1 dans tous les cas.
  (2) ROBUSTESSE (ergonomie)  : caractérisation du perdant — orphelin gracieux (KkiaPay,
      check fee_resolved + try/except) vs exception non capturée (manuel, D-MANUAL-ROBUST-25).

Style aligné sur test_concurrence_fee_lock.py (R3) : chaque thread ouvre sa PROPRE connexion,
commite hors du rollback du FrappeTestCase ; purge explicite par nom en tearDown. Aucun
exec/tmp/réécriture dynamique (V-LEARN-CAMPUS-08/09) — fichier versionné, relisible.
"""

import json
import threading
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

WEBHOOK = "admission.api.webhook"
STAFF = "admission.api.staff"
NOTIFY = "admission.api.notify_uf"
_MARK = "ZZINTERMODES"
_AMOUNT = 15000


def _stub(*a, **k):
    return None


class TestConcurrenceInterModes(FrappeTestCase):
    def _purge_marker(self):
        """Auto-nettoyage par marqueur : absorbe tout résidu (un run interrompu commite hors
        du rollback FrappeTestCase). provider_reference porte un index unique → indispensable."""
        apps = frappe.get_all("Admission Applicant",
                              filters={"applicant_name": ["like", f"{_MARK}%"]}, pluck="name")
        if apps:
            frappe.db.delete("Applicant Fee Payment", {"applicant": ["in", apps]})
            frappe.db.delete("Applicant Fee", {"applicant": ["in", apps]})
            frappe.db.delete("Admission Applicant", {"name": ["in", apps]})
        frappe.db.delete("Applicant Fee Payment", {"provider_reference": ["like", f"{_MARK}%"]})
        frappe.db.commit()

    def setUp(self):
        self._purge_marker()
        self.applicant = frappe.get_doc({
            "doctype": "Admission Applicant",
            "applicant_name": f"{_MARK} InterModes",
            "status": "BRO",
        }).insert(ignore_permissions=True, ignore_mandatory=True)

        self.fee = frappe.get_doc({
            "doctype": "Applicant Fee",
            "applicant": self.applicant.name,
            "amount_xof": _AMOUNT,
            "status": "Pending",
        }).insert(ignore_permissions=True, ignore_mandatory=True)

        # DEUX Pending sur le MÊME fee, un par mode (précondition inter-modes réelle :
        # declare_payment_offline banque + initiate_online_payment KkiaPay tant que le fee est Pending).
        self.ref_online = f"{_MARK}-ONLINE"
        self.pay_online = self._make_pending("Online", self.ref_online, provider="kkiapay")
        self.pay_bank = self._make_pending("Bank", f"{_MARK}-BANK",
                                           justificatif="/private/files/test-justif.pdf")
        frappe.db.commit()  # visible aux connexions des threads

    def _make_pending(self, mode, ref, provider=None, justificatif=None):
        d = {
            "doctype": "Applicant Fee Payment",
            "applicant": self.applicant.name,
            "applicant_fee": self.fee.name,
            "payment_mode": mode,
            "amount_xof": _AMOUNT,
            "payment_status": "Pending",
            "provider_reference": ref,
        }
        if provider:
            d["provider"] = provider
        if justificatif:
            d["justificatif"] = justificatif
        return frappe.get_doc(d).insert(ignore_permissions=True, ignore_mandatory=True).name

    def tearDown(self):
        frappe.db.rollback()
        self._purge_marker()

    # ---- helpers état DB (vérité = base sérialisée, jamais un mock) ----
    def _confirmed(self):
        frappe.db.rollback()
        return frappe.get_all("Applicant Fee Payment",
                              filters={"applicant_fee": self.fee.name, "payment_status": "Confirmed"},
                              pluck="name")

    def _orphans(self):
        frappe.db.rollback()
        return frappe.get_all("Applicant Fee Payment",
                              filters={"applicant_fee": self.fee.name, "reconciliation": "Orphan - refund due"},
                              pluck="name")

    def _reset_pending(self):
        for name in (self.pay_online, self.pay_bank):
            frappe.db.set_value("Applicant Fee Payment", name,
                                {"payment_status": "Pending", "reconciliation": None,
                                 "provider_transaction_id": None, "paid_at": None},
                                update_modified=False)
        frappe.db.commit()

    # ---- appels « inline » (connexion COURANTE — usage séquentiel) ----
    def _webhook_call(self, ref, secret, out):
        try:
            frappe.local.request = frappe._dict(
                data=json.dumps({"transactionId": f"TX-{ref}", "event": "transaction.success",
                                 "stateData": {"reference": ref}}),
                headers={"x-kkiapay-secret": secret})
            from admission.api.webhook import payment
            payment()
            out.append(("kkiapay", "ok"))
        except Exception as exc:  # noqa: BLE001
            out.append(("kkiapay", "exc", repr(exc)))

    def _manual_call(self, out):
        try:
            frappe.set_user("Administrator")  # System Manager ∈ CONFIRM_ROLES → garde only_for réelle
            from admission.api.staff import confirm_offline_payment
            confirm_offline_payment(dossier_id=self.applicant.name, payment_id=self.pay_bank)
            frappe.db.commit()
            out.append(("manuel", "ok"))
        except Exception as exc:  # noqa: BLE001
            try:
                frappe.db.rollback()
            except Exception:
                pass
            out.append(("manuel", "exc", repr(exc)))

    # ---- workers threadés (chacun sa PROPRE connexion — usage concurrent) ----
    def _webhook_worker(self, ref, site, secret, out):
        frappe.init(site=site)
        frappe.connect()
        try:
            self._webhook_call(ref, secret, out)
        finally:
            frappe.destroy()

    def _manual_worker(self, site, out):
        frappe.init(site=site)
        frappe.connect()
        try:
            self._manual_call(out)
        finally:
            frappe.destroy()

    def _patches(self):
        # Stubs posés UNE FOIS avant les threads (mock.patch non thread-safe) : I/O KkiaPay (verify)
        # + effets aval (cascade/notif/reçu) des DEUX chemins + hook UF (HTTP). Le VRAI cœur
        # (statut→Confirmed, save, index unique, commit) tourne réel.
        return [
            patch(f"{WEBHOOK}.verify_transaction", return_value={"status": "SUCCESS", "amount": _AMOUNT}),
            patch(f"{WEBHOOK}.apply_confirmed_payment_cascade", _stub),
            patch(f"{WEBHOOK}.notify_uf_payment", _stub),
            patch(f"{WEBHOOK}.send_payment_receipt", _stub),
            patch(f"{STAFF}.apply_confirmed_payment_cascade", _stub),
            patch(f"{STAFF}.send_payment_receipt", _stub),
            patch(f"{NOTIFY}.on_payment_update", _stub),  # suppr. hook UF sur save() (les 2 chemins)
        ]

    # ===================== Cas A — concurrent (≥5 runs) =====================
    def test_a_concurrent_inter_modes(self):
        site = frappe.local.site
        secret = frappe.conf.get("admission_payment_webhook_secret")
        self.assertTrue(secret, "secret webhook requis (admission_payment_webhook_secret)")
        losers = []
        RUNS = 5
        for i in range(RUNS):
            self._reset_pending()
            out = []
            threads = [
                threading.Thread(target=self._webhook_worker, args=(self.ref_online, site, secret, out)),
                threading.Thread(target=self._manual_worker, args=(site, out)),
            ]
            patches = self._patches()
            for p in patches:
                p.start()
            try:
                for t in threads:
                    t.start()
                for t in threads:
                    t.join()
            finally:
                for p in patches:
                    p.stop()

            confirmed = self._confirmed()
            self.assertEqual(len(confirmed), 1,
                             f"[run {i}] INVARIANT VIOLÉ : {len(confirmed)} Confirmed attendu 1 — {confirmed} ; out={out}")
            # caractérisation du perdant (dimension robustesse)
            manual = next((o for o in out if o[0] == "manuel"), None)
            kkia = next((o for o in out if o[0] == "kkiapay"), None)
            if manual and manual[1] == "exc":
                losers.append("manuel:exception")          # D-MANUAL-ROBUST-25 se manifeste
            elif len(self._orphans()) == 1:
                losers.append("kkiapay:orphelin")          # perdant gracieux
            else:
                losers.append(f"indéterminé(out={out})")
        print(f"\n[Cas A] {RUNS} runs — invariant count==1 : OK partout | perdants = {losers}")

    # ===================== Cas B1 — séquentiel manuel → webhook =====================
    def test_b1_manuel_puis_webhook_orphelin_gracieux(self):
        site = frappe.local.site
        secret = frappe.conf.get("admission_payment_webhook_secret")
        self._reset_pending()
        with self._patched():
            manual_out = []
            self._manual_call(manual_out)                      # manuel (1er) → Confirmed
            self.assertEqual(("manuel", "ok"), tuple(manual_out[0][:2]), manual_out)
            out = []
            self._webhook_call(self.ref_online, secret, out)   # webhook (2e), connexion courante
        confirmed = self._confirmed()
        self.assertEqual(len(confirmed), 1, f"1 Confirmed attendu, obtenu {confirmed}")
        self.assertEqual(confirmed[0], self.pay_bank, "le manuel (1er) doit être le Confirmed")
        self.assertEqual(len(self._orphans()), 1, "le webhook perdant doit être orphelin gracieux")
        self.assertEqual(("kkiapay", "ok"), tuple(out[0][:2]))
        print(f"\n[Cas B1] manuel→webhook : Confirmed={confirmed}, webhook perdant → orphelin gracieux OK")

    # ===================== Cas B2 — séquentiel webhook → manuel (D-MANUAL-ROBUST-25) =====================
    def test_b2_webhook_puis_manuel_gracieux(self):
        # D-MANUAL-ROBUST-25 : le manuel sur un fee déjà Confirmed doit DÉGRADER GRACIEUSEMENT
        # (409 ALREADY_PAID, Pending intact — le staff décide), PAS lever une exception (500).
        secret = frappe.conf.get("admission_payment_webhook_secret")
        self._reset_pending()
        with self._patched():
            out = []
            self._webhook_call(self.ref_online, secret, out)  # online → Confirmed (connexion courante)
            self.assertEqual(("kkiapay", "ok"), tuple(out[0][:2]))
            from admission.api.staff import confirm_offline_payment
            frappe.set_user("Administrator")
            res = confirm_offline_payment(dossier_id=self.applicant.name, payment_id=self.pay_bank)
        self.assertFalse(res.get("ok"), f"réponse gracieuse attendue (pas d'exception), obtenu {res}")
        self.assertEqual(res.get("error", {}).get("code"), "ALREADY_PAID", res)
        confirmed = self._confirmed()
        self.assertEqual(len(confirmed), 1, f"1 Confirmed attendu (webhook), obtenu {confirmed}")
        self.assertEqual(confirmed[0], self.pay_online, "le webhook (1er) doit être le Confirmed")
        # le Pending manuel reste INTACT (pas orphelin auto — canal humain, le staff décide)
        self.assertEqual(
            frappe.db.get_value("Applicant Fee Payment", self.pay_bank, "payment_status"), "Pending",
            "le Pending manuel doit rester intact")
        print("\n[Cas B2] webhook→manuel : dégradation GRACIEUSE (409 ALREADY_PAID), Pending intact, "
              "invariant OK — D-MANUAL-ROBUST-25 remédiée")

    # ===================== Cas C — l'index reste le GARANT FINAL (défense en profondeur) =====================
    def test_c_index_garant_final_meme_sans_precheck(self):
        # Même si le pré-check était contourné (fenêtre concurrente pré-check→save), l'index unique
        # rattrape au save → le try/except D-MANUAL-ROBUST-25 → 409 gracieux, toujours 1 Confirmed.
        # On NEUTRALISE le pré-check (patch → None) pour exercer précisément la branche `except`.
        self._reset_pending()
        frappe.db.set_value("Applicant Fee Payment", self.pay_online, "payment_status", "Confirmed",
                            update_modified=False)  # fee déjà crédité (STORED recalculée)
        frappe.db.commit()
        self.assertEqual(len(self._confirmed()), 1)
        with self._patched(), patch(f"{STAFF}._assert_fee_unpaid", return_value=None):
            from admission.api.staff import confirm_offline_payment
            frappe.set_user("Administrator")
            res = confirm_offline_payment(dossier_id=self.applicant.name, payment_id=self.pay_bank)
        self.assertFalse(res.get("ok"), res)
        self.assertEqual(res.get("error", {}).get("code"), "ALREADY_PAID", res)
        self.assertEqual(len(self._confirmed()), 1,
                         "toujours 1 Confirmed — l'index (rattrapé par try/except) est le garant final")
        print("\n[Cas C] pré-check neutralisé → l'index rattrape au save (try/except) → 409 gracieux, "
              "1 Confirmed (l'index reste le garant structurel)")

    def _patched(self):
        # Contexte de patchs partagé (usage séquentiel — un seul thread).
        import contextlib
        cm = contextlib.ExitStack()
        for p in self._patches():
            cm.enter_context(p)
        return cm
