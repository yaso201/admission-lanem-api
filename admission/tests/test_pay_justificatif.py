"""Tests PAY-CONFIRM-AGENT Phase a — justificatif obligatoire espèce/banque + immuabilité.

Précision 2 (COMPLÉMENT) : confirmer un paiement Cash/Bank SANS justificatif doit être REFUSÉ
(anti-fraude + trace matérielle A03 §10). Online exempté (la transaction KkiaPay/webhook fait foi).
Le justificatif est immuable une fois le paiement Confirmed.
Style unitaire mocké (validate testé sur un stub + frappe.throw mocké), aligné suite existante.
"""

import json
import os
import types
from unittest import TestCase
from unittest.mock import patch

MOD = "admission.admission.doctype.applicant_fee_payment.applicant_fee_payment"


def _afp(payment_status="Confirmed", payment_mode="Cash", justificatif=None,
         old_status=None, old_justificatif=None):
    old = None
    if old_status is not None:
        old = types.SimpleNamespace(payment_status=old_status, justificatif=old_justificatif)
    afp = types.SimpleNamespace(
        name="REC-2026-00001",
        receipt_number="REC-2026-00001",
        payment_status=payment_status,
        payment_mode=payment_mode,
        justificatif=justificatif,
    )
    afp.get_doc_before_save = lambda: old
    return afp


def _run_validate(afp):
    # On cible directement le garde-fou justificatif (l'unité sous test) ; validate() appelle
    # aussi _sync_receipt_number (sans rapport), non pertinent ici.
    from admission.admission.doctype.applicant_fee_payment.applicant_fee_payment import ApplicantFeePayment
    with patch(f"{MOD}.frappe") as mf:
        mf.throw.side_effect = ValueError  # simule frappe.throw qui lève
        ApplicantFeePayment._guard_justificatif(afp)


class TestJustificatifRequired(TestCase):
    def test_confirm_cash_without_justificatif_raises(self):
        with self.assertRaises(ValueError):
            _run_validate(_afp("Confirmed", "Cash", justificatif=None))

    def test_confirm_bank_without_justificatif_raises(self):
        with self.assertRaises(ValueError):
            _run_validate(_afp("Confirmed", "Bank", justificatif=None))

    def test_confirm_cash_with_justificatif_ok(self):
        _run_validate(_afp("Confirmed", "Cash", justificatif="/private/files/recu.pdf"))  # ne lève pas

    def test_confirm_bank_with_justificatif_ok(self):
        _run_validate(_afp("Confirmed", "Bank", justificatif="/private/files/virement.pdf"))

    def test_online_confirmed_without_justificatif_ok(self):
        _run_validate(_afp("Confirmed", "Online", justificatif=None))  # online exempté (webhook fait foi)

    def test_pending_cash_without_justificatif_ok(self):
        _run_validate(_afp("Pending", "Cash", justificatif=None))  # exigé seulement à la confirmation


class TestJustificatifImmutable(TestCase):
    def test_justificatif_changed_after_confirmed_raises(self):
        afp = _afp("Confirmed", "Cash", justificatif="/private/files/new.pdf",
                   old_status="Confirmed", old_justificatif="/private/files/orig.pdf")
        with self.assertRaises(ValueError):
            _run_validate(afp)

    def test_justificatif_unchanged_after_confirmed_ok(self):
        afp = _afp("Confirmed", "Cash", justificatif="/private/files/orig.pdf",
                   old_status="Confirmed", old_justificatif="/private/files/orig.pdf")
        _run_validate(afp)  # inchangé → ne lève pas


class TestAttachField(TestCase):
    def test_justificatif_field_is_attach_readonly_on_confirmed(self):
        jf = os.path.join(os.path.dirname(__file__), "..", "admission", "doctype",
                          "applicant_fee_payment", "applicant_fee_payment.json")
        doc = json.load(open(jf))
        field = next((f for f in doc["fields"] if f["fieldname"] == "justificatif"), None)
        self.assertIsNotNone(field, "champ justificatif (Attach) absent")
        self.assertIn(field["fieldtype"], ("Attach", "Attach Image"))
        self.assertIn("Confirmed", field.get("read_only_depends_on", ""))
        self.assertIn("justificatif", doc["field_order"])
