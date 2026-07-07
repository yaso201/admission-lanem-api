"""Tests PAY-CONFIRM-AGENT Phase c — reçu PDF mailé (DEC-198).

À la confirmation d'un paiement, génère un reçu PDF (HTML→PDF) et l'envoie au candidat
(mentions légales REFUND_POLICY + visuels école). NON-BLOQUANT : un échec (wkhtmltopdf/SMTP
absent) ne doit jamais interrompre la confirmation. Style unitaire mocké.
"""

from unittest import TestCase
from unittest.mock import MagicMock, patch

RECEIPT = "admission.api.receipt"
STAFF = "admission.api.staff"


def _payment(mode="Cash"):
    p = MagicMock()
    p.receipt_number = "REC-2026-00042"
    p.amount_xof = 15000
    p.payment_mode = mode
    p.paid_at = "2026-06-10 10:00:00"
    p.applicant = "CAN-2026-00001"
    p.applicant_fee = "AFF-1"
    return p


def _applicant(email="candidat@example.test"):
    a = MagicMock()
    a.name = "CAN-2026-00001"
    a.applicant_name = "Kossi Adjavon"
    a.email = email
    return a


class TestRenderReceiptHtml(TestCase):
    def test_html_contains_payment_and_identity_and_legal(self):
        from admission.api.receipt import render_receipt_html
        html = render_receipt_html(_payment("Cash"), _applicant(), MagicMock(),
                                   legal_text="Les frais ne sont pas remboursables.")
        self.assertIn("REC-2026-00042", html)        # receipt_number
        self.assertIn("15", html)                      # montant (15 000)
        self.assertIn("Cash", html)                    # mode
        self.assertIn("CAN-2026-00001", html)          # dossier
        self.assertIn("LaNEM", html)                   # identité école
        self.assertIn("remboursables", html)           # mention légale injectée


class TestSendReceipt(TestCase):
    def test_sends_email_with_pdf_attachment(self):
        with patch(f"{RECEIPT}.frappe") as mf, \
             patch(f"{RECEIPT}.get_pdf", return_value=b"%PDF-1.4 fake") as gp, \
             patch(f"{RECEIPT}._get_legal_text", return_value="mention"):
            from admission.api.receipt import send_payment_receipt
            send_payment_receipt(_payment("Bank"), applicant=_applicant("c@x.bj"), fee=MagicMock())
            gp.assert_called_once()
            mf.sendmail.assert_called_once()
            kwargs = mf.sendmail.call_args.kwargs
            self.assertEqual(kwargs["recipients"], ["c@x.bj"])
            att = kwargs["attachments"][0]
            self.assertTrue(att["fname"].endswith(".pdf"))
            self.assertEqual(att["fcontent"], b"%PDF-1.4 fake")

    def test_non_blocking_on_pdf_error(self):
        with patch(f"{RECEIPT}.frappe") as mf, \
             patch(f"{RECEIPT}.get_pdf", side_effect=RuntimeError("wkhtmltopdf missing")), \
             patch(f"{RECEIPT}._get_legal_text", return_value="m"):
            from admission.api.receipt import send_payment_receipt
            # ne doit PAS lever (non-bloquant)
            send_payment_receipt(_payment(), applicant=_applicant(), fee=MagicMock())
            mf.sendmail.assert_not_called()

    def test_skips_without_email(self):
        with patch(f"{RECEIPT}.frappe") as mf, \
             patch(f"{RECEIPT}.get_pdf", return_value=b"x"), \
             patch(f"{RECEIPT}._get_legal_text", return_value="m"):
            from admission.api.receipt import send_payment_receipt
            send_payment_receipt(_payment(), applicant=_applicant(email=None), fee=MagicMock())
            mf.sendmail.assert_not_called()


class TestConfirmWiresReceipt(TestCase):
    def test_confirm_offline_payment_sends_receipt(self):
        pay = MagicMock(); pay.payment_status = "Pending"; pay.name = "REC-1"; pay.applicant_fee = "AFF-1"
        applicant = MagicMock(); fee = MagicMock()
        # TEST-HYGIENE : mock aligné sur la garde amont B1 (_assert_fee_unpaid, 02/07 — le mock
        # antérieur laissait courir la VRAIE garde avec un fee.name MagicMock → erreur SQL).
        # autospec=True (V-LEARN-MOCK-FIDELITY-25) ; None = fee libre → chemin nominal préservé.
        with patch(f"{STAFF}.frappe") as mf, \
             patch(f"{STAFF}._resolve_pending_payment", return_value=pay), \
             patch(f"{STAFF}._assert_fee_unpaid", autospec=True, return_value=None), \
             patch(f"{STAFF}.apply_confirmed_payment_cascade"), \
             patch(f"{STAFF}.send_payment_receipt") as send, \
             patch(f"{STAFF}._ok", side_effect=lambda d: {"ok": True, **d}), \
             patch(f"{STAFF}._error", side_effect=lambda c, m, s=400: {"ok": False, "code": c}), \
             patch(f"{STAFF}.now_datetime", return_value="2026-06-10 10:00:00"):
            mf.db.exists.return_value = True
            mf.get_doc.side_effect = lambda dt, name=None: applicant if dt == "Admission Applicant" else fee
            from admission.api.staff import confirm_offline_payment
            confirm_offline_payment(dossier_id="CAN-2026-00001", payment_mode="cash",
                                    justificatif="/private/files/recu.pdf")
        send.assert_called_once()  # reçu envoyé à la confirmation
