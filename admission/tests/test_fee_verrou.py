"""FIX-FEE2-VERROU — verrou du règlement des frais (modèle PaymentIntent : reuse/replace/refuse).

Invariant : UN SEUL règlement actif par (dossier, frais), quel que soit le canal.
Le point de passage unique `_prepare_fee_channel(fee, mode)` décide, sur DB RÉELLE :
  - Confirmed existe            → refus ferme ALREADY_PAID, tous canaux (GF1)
  - même mode hors-ligne actif  → RÉUTILISATION (aucune création, aucun courriel)
  - autre mode actif            → REMPLACEMENT (ancien annulé + motif, nouveau créé)
Un Online supplanté passe Rejected+reconciliation → RECONCILIABLE (le webhook promeut encore
un succès tardif Rejected→Confirmed « Promoted late » ; invariant #1).
`apply_confirmed_payment_cascade` rejette les Pending frères à la confirmation (invariant #2).
`_get_fee_and_payment` renvoie le Confirmed en priorité (défaut de lecture, T6).

Style DB réelle (test_concurrence_inter_modes) : purge par marqueur, jamais un mock de l'invariant.
"""

import frappe
from frappe.tests.utils import FrappeTestCase

from admission.api.public import (
    _prepare_fee_channel,
    _get_fee_and_payment,
    apply_confirmed_payment_cascade,
)

_MARK = "ZZFEEVERROU"
_AMOUNT = 50000


class TestFeeVerrou(FrappeTestCase):
    def _purge(self):
        apps = frappe.get_all("Admission Applicant",
                              filters={"applicant_name": ["like", f"{_MARK}%"]}, pluck="name")
        if apps:
            frappe.db.delete("Applicant Fee Payment", {"applicant": ["in", apps]})
            frappe.db.delete("Applicant Fee", {"applicant": ["in", apps]})
            frappe.db.delete("Admission Applicant", {"name": ["in", apps]})
        frappe.db.commit()

    def setUp(self):
        self._purge()
        self.applicant = frappe.get_doc({
            "doctype": "Admission Applicant",
            "applicant_name": f"{_MARK} Verrou",
        }).insert(ignore_permissions=True, ignore_mandatory=True)
        # ACC (état d'inscription réel) posé hors Workflow — l'insert part de l'état initial.
        frappe.db.set_value("Admission Applicant", self.applicant.name, "status", "ACC",
                            update_modified=False)
        self.applicant.status = "ACC"
        self.fee = frappe.get_doc({
            "doctype": "Applicant Fee",
            "applicant": self.applicant.name,
            "fee_type": "enrollment",
            "amount_xof": _AMOUNT,
            "status": "Pending",
        }).insert(ignore_permissions=True, ignore_mandatory=True)
        frappe.db.commit()

    def tearDown(self):
        frappe.db.rollback()
        self._purge()

    def _mk(self, mode, status="Pending", ref=None):
        # Insert en Pending (valide), puis bascule d'état hors validate (justificatif Cash/Bank).
        name = frappe.get_doc({
            "doctype": "Applicant Fee Payment",
            "applicant": self.applicant.name,
            "applicant_fee": self.fee.name,
            "payment_mode": mode,
            "amount_xof": _AMOUNT,
            "payment_status": "Pending",
            "provider_reference": ref,
        }).insert(ignore_permissions=True, ignore_mandatory=True).name
        if status != "Pending":
            frappe.db.set_value("Applicant Fee Payment", name, "payment_status", status,
                                update_modified=False)
        return name

    def _status(self, name):
        return frappe.db.get_value("Applicant Fee Payment", name, "payment_status")

    def _active(self):
        return frappe.get_all("Applicant Fee Payment",
                              filters={"applicant_fee": self.fee.name, "payment_status": "Pending"},
                              pluck="name")

    def _confirmed(self):
        return frappe.get_all("Applicant Fee Payment",
                              filters={"applicant_fee": self.fee.name, "payment_status": "Confirmed"},
                              pluck="name")

    # ── GF1 : Confirmed → refus ferme, TOUS canaux ────────────────────────────
    def test_confirmed_refuses_all_channels(self):
        self._mk("Cash", status="Confirmed")
        for mode in ("Online", "Cash", "Bank"):
            err, reuse = _prepare_fee_channel(self.fee, mode)
            self.assertIsNotNone(err, f"{mode} aurait dû être refusé")
            self.assertEqual(err["error"]["code"], "ALREADY_PAID")
            self.assertIsNone(reuse)

    # ── Réutilisation même mode hors-ligne (T2) ───────────────────────────────
    def test_same_offline_mode_reuses_no_create(self):
        cash = self._mk("Cash")
        err, reuse = _prepare_fee_channel(self.fee, "Cash")
        self.assertIsNone(err)
        self.assertIsNotNone(reuse, "un Pending cash existe → réutilisation")
        self.assertEqual(reuse.name, cash)
        self.assertEqual(self._active(), [cash])  # aucun nouveau, un seul actif

    # ── Remplacement autre mode (annule + motif) ──────────────────────────────
    def test_different_mode_supersedes_old(self):
        cash = self._mk("Cash")
        err, reuse = _prepare_fee_channel(self.fee, "Bank")
        self.assertIsNone(err)
        self.assertIsNone(reuse, "mode différent → création (pas de réutilisation)")
        self.assertEqual(self._status(cash), "Rejected")  # ancien annulé
        recon = frappe.db.get_value("Applicant Fee Payment", cash, "reconciliation")
        self.assertIn("Superseded", recon or "")

    # ── Online supplanté reste RECONCILIABLE (invariant #1) ────────────────────
    def test_superseded_online_is_reconcilable(self):
        online = self._mk("Online", ref=f"{_MARK}-REF")
        err, reuse = _prepare_fee_channel(self.fee, "Cash")
        self.assertIsNone(err)
        self.assertEqual(self._status(online), "Rejected")  # supplanté (pas supprimé)
        # Rejected + reconciliation ⇒ le webhook peut encore promouvoir un succès tardif.
        self.assertIn("Superseded",
                      frappe.db.get_value("Applicant Fee Payment", online, "reconciliation") or "")

    # ── Online → Online : nouvelle tentative, un seul actif ────────────────────
    def test_online_supersedes_prior_online(self):
        old = self._mk("Online", ref=f"{_MARK}-OLD")
        err, reuse = _prepare_fee_channel(self.fee, "Online")
        self.assertIsNone(err)
        self.assertIsNone(reuse, "online ne réutilise pas : nouvelle tentative")
        self.assertEqual(self._status(old), "Rejected")  # un seul actif après création aval

    # ── Aucun actif → création ────────────────────────────────────────────────
    def test_no_active_creates(self):
        err, reuse = _prepare_fee_channel(self.fee, "Bank")
        self.assertIsNone(err)
        self.assertIsNone(reuse)

    # ── Invariant #2 : la confirmation rejette les Pending frères ──────────────
    def test_cascade_rejects_sibling_pendings(self):
        bank = self._mk("Bank")            # déclaration hors-ligne en attente
        self._mk("Online", status="Confirmed")  # un paiement réel confirmé
        frappe.db.set_value("Applicant Fee", self.fee.name, "status", "Paid")  # évite le save (session reqd)
        applicant = frappe.get_doc("Admission Applicant", self.applicant.name)
        fee = frappe.get_doc("Applicant Fee", self.fee.name)
        apply_confirmed_payment_cascade(applicant, fee)
        self.assertEqual(self._status(bank), "Rejected")  # déclaration devenue caduque

    # ── T3 : Online supplanté puis confirmé tardivement → honoré + frère caduc ──
    def test_t3_superseded_online_promoted_late_honored(self):
        """Le candidat bascule vers un virement (l'Online est supplanté), puis KkiaPay confirme
        FINALEMENT la tentative en ligne : le paiement RÉEL est honoré (invariant #1) et la
        déclaration hors-ligne concurrente devient caduque (invariant #2). Un seul Confirmed."""
        from unittest.mock import patch
        from admission.api import webhook
        online = self._mk("Online", ref=f"{_MARK}-LATE")
        err, _ = _prepare_fee_channel(self.fee, "Bank")     # bascule → Online supplanté (Rejected)
        self.assertIsNone(err)
        self.assertEqual(self._status(online), "Rejected")
        bank = self._mk("Bank")                              # la nouvelle déclaration (créée par l'appelant)
        frappe.db.set_value("Applicant Fee", self.fee.name, "status", "Paid")  # évite le save (session reqd)
        with patch.object(webhook, "notify_uf_payment"), patch.object(webhook, "send_payment_receipt"):
            promoted = webhook._promote_payment(
                frappe.get_doc("Applicant Fee Payment", online),
                "TX-LATE", f"{_MARK}-LATE", reconciliation="Promoted late")
        self.assertTrue(promoted)                            # invariant #1 : succès tardif honoré
        self.assertEqual(self._status(online), "Confirmed")
        self.assertEqual(self._status(bank), "Rejected")     # invariant #2 : déclaration caduque
        self.assertEqual(len(self._confirmed()), 1)          # un SEUL Confirmed

    # ── T6 : lecture d'état → le Confirmed, jamais un arbitraire ───────────────
    def test_get_fee_and_payment_prefers_confirmed(self):
        self._mk("Cash", status="Pending")
        confirmed = self._mk("Online", status="Confirmed", ref=f"{_MARK}-C")
        _, payment = _get_fee_and_payment(self.applicant.name, ["enrollment"])
        self.assertIsNotNone(payment)
        self.assertEqual(payment.name, confirmed)
        self.assertEqual(payment.payment_status, "Confirmed")
