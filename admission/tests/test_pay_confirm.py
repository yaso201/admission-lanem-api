"""Tests PAY-CONFIRM-AGENT Phase b — confirm_offline_payment (espèce/banque, scénarios 1&2).

C1-OFFLINE (🔴) : un paiement offline reste Pending sans confirmation → SOP→SOU bloqué.
Cet endpoint staff (Administratif) confirme Pending→Confirmed → cascade (fee Paid #3, SOP→SOU)
et le hook on_payment_update notifie UF au Confirmed (#1 : plus de notify au Pending).
Idempotent (rejeu = no-op). Justificatif obligatoire (garde validate, phase a).
Style unitaire mocké (guards + cascade) ; l'end-to-end est prouvé en runtime à la PAUSE b.
"""

from unittest import TestCase
from unittest.mock import MagicMock, patch

STAFF = "admission.api.staff"


class TestCascadeHelper(TestCase):
    """Helper de cascade PARTAGÉ confirm + webhook (factorisation, ne pas diverger)."""

    def test_cascade_sets_paid_and_sou(self):
        from admission.api.public import apply_confirmed_payment_cascade
        applicant = MagicMock(); applicant.status = "SOP"
        fee = MagicMock(); fee.status = "Pending"
        apply_confirmed_payment_cascade(applicant, fee)
        self.assertEqual(fee.status, "Paid")
        fee.save.assert_called_once()
        self.assertEqual(applicant.status, "SOU")
        applicant.save.assert_called_once()

    def test_cascade_from_bro(self):
        from admission.api.public import apply_confirmed_payment_cascade
        applicant = MagicMock(); applicant.status = "BRO"
        fee = MagicMock(); fee.status = "Pending"
        apply_confirmed_payment_cascade(applicant, fee)
        self.assertEqual(applicant.status, "SOU")

    def test_cascade_idempotent_when_already_advanced(self):
        from admission.api.public import apply_confirmed_payment_cascade
        applicant = MagicMock(); applicant.status = "SOU"  # déjà transitionné
        fee = MagicMock(); fee.status = "Paid"
        apply_confirmed_payment_cascade(applicant, fee)
        fee.save.assert_not_called()
        applicant.save.assert_not_called()


class TestConfirmGuards(TestCase):
    def test_role_guarded(self):
        with patch(f"{STAFF}.frappe") as mf:
            mf.only_for.side_effect = PermissionError("403")
            from admission.api.staff import confirm_offline_payment
            with self.assertRaises(PermissionError):
                confirm_offline_payment(dossier_id="CAN-2026-00001")
            mf.only_for.assert_called_once()

    def test_idempotent_when_not_pending(self):
        pay = MagicMock()
        pay.payment_status = "Confirmed"
        pay.name = "REC-2026-00001"
        with patch(f"{STAFF}.frappe") as mf, \
             patch(f"{STAFF}._resolve_pending_payment", return_value=pay), \
             patch(f"{STAFF}._ok", side_effect=lambda d: {"ok": True, **d}), \
             patch(f"{STAFF}._error", side_effect=lambda c, m, s=400: {"ok": False, "code": c}):
            mf.db.exists.return_value = True
            from admission.api.staff import confirm_offline_payment
            res = confirm_offline_payment(dossier_id="CAN-2026-00001")
        self.assertTrue(res.get("idempotent"))
        pay.save.assert_not_called()  # rejeu = aucune écriture

    def test_no_pending_payment_returns_error(self):
        with patch(f"{STAFF}.frappe") as mf, \
             patch(f"{STAFF}._resolve_pending_payment", return_value=None), \
             patch(f"{STAFF}._ok", side_effect=lambda d: {"ok": True, **d}), \
             patch(f"{STAFF}._error", side_effect=lambda c, m, s=400: {"ok": False, "code": c}):
            mf.db.exists.return_value = True
            from admission.api.staff import confirm_offline_payment
            res = confirm_offline_payment(dossier_id="CAN-2026-00001")
        self.assertFalse(res.get("ok"))
        self.assertEqual(res.get("code"), "NO_PENDING_PAYMENT")

    def test_confirm_pending_calls_save_and_cascade(self):
        pay = MagicMock()
        pay.payment_status = "Pending"
        pay.name = "REC-2026-00001"
        pay.applicant_fee = "FEE-1"
        applicant = MagicMock(); fee = MagicMock()
        with patch(f"{STAFF}.frappe") as mf, \
             patch(f"{STAFF}._resolve_pending_payment", return_value=pay), \
             patch(f"{STAFF}.apply_confirmed_payment_cascade") as casc, \
             patch(f"{STAFF}._ok", side_effect=lambda d: {"ok": True, **d}), \
             patch(f"{STAFF}._error", side_effect=lambda c, m, s=400: {"ok": False, "code": c}), \
             patch(f"{STAFF}.now_datetime", return_value="2026-06-10 10:00:00"):
            mf.db.exists.return_value = True
            mf.get_doc.side_effect = lambda dt, name=None: applicant if dt == "Admission Applicant" else fee
            from admission.api.staff import confirm_offline_payment
            res = confirm_offline_payment(dossier_id="CAN-2026-00001", payment_mode="cash",
                                          justificatif="/private/files/recu.pdf")
        self.assertEqual(pay.payment_status, "Confirmed")
        self.assertEqual(pay.justificatif, "/private/files/recu.pdf")
        pay.save.assert_called_once()           # save → validate (justif) + hook notif UF
        casc.assert_called_once()                # cascade fee Paid + SOP→SOU
        self.assertTrue(res.get("ok"))
