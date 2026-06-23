"""Tests LOT M — mails candidat (design email-handoff) + OTP réel + recovery + relances.

Couverture :
 A. send_email_otp — code dans le CORPS, jamais dans le sujet (sécurité) ; skip sans email
 B. Liens tokenisés (A0.2) — compte créé + recovery : lien /reprise?dossier=&token=
 C. send_offline_submission — virement (IBAN Coris réel + RIB PDF joint) / espèces ;
    frais 1 vs frais 2 (libellés) ; _rib_attachment lit le VRAI PDF de l'app
 D. send_enrolled (campus URL) + send_purge_notice (préavis J-7)
 E. verify_otp mono-canal (A0.1) — email OBLIGATOIRE ; tel vérifié seulement si soumis
 F. recover_dossier (M7) — réponse UNIFORME (anti-énumération), rotation token + reset OTP
 G. Relances scheduler (M9) — SOP J+7 (flag anti-double) + préavis purge BRO
 H. receipt._email_body — template paiement, montant/reçu, nom ÉCHAPPÉ (fix audit)

Style unitaire mocké (cohérent test_notifs/test_sec_critique).
"""

import types
from unittest import TestCase
from unittest.mock import MagicMock, patch

import frappe as _real_frappe
from frappe.utils import get_datetime


def setUpModule():
    try:
        _real_frappe.local.flags
    except Exception:
        _real_frappe.local.flags = _real_frappe._dict(in_test=True)


NOTIF = "admission.api.notifications"
PUB = "admission.api.public"
RET = "admission.api.retention"

NOW = "2026-06-09 12:00:00"


def _app(email="a@x.bj", **kw):
    base = dict(name="CAN-2026-00001", applicant_name="Ama", email=email,
                programme_label="Licence Informatique", notes_concours="{}")
    base.update(kw)
    return types.SimpleNamespace(**base)


def _sendmail_kwargs(mock_frappe):
    return mock_frappe.sendmail.call_args.kwargs


# ── A. OTP par e-mail ──────────────────────────────────────────────────────────


class TestSendEmailOtp(TestCase):
    def test_code_in_body_never_in_subject(self):
        with patch(f"{NOTIF}.frappe") as mf:
            from admission.api.notifications import send_email_otp
            send_email_otp(_app(), "987654", minutes=10)
        kw = _sendmail_kwargs(mf)
        self.assertIn("987654", kw["message"])        # le mail LIVRE le code (A0.1)
        self.assertNotIn("987654", kw["subject"])     # jamais dans le sujet (sécurité)
        self.assertEqual(kw["recipients"], ["a@x.bj"])

    def test_skip_without_email(self):
        with patch(f"{NOTIF}.frappe") as mf:
            from admission.api.notifications import send_email_otp
            send_email_otp(_app(email=None), "987654")
        mf.sendmail.assert_not_called()

    def test_one_tap_link_carries_otp_and_token(self):
        with patch(f"{NOTIF}.frappe") as mf:
            from admission.api.notifications import send_email_otp
            send_email_otp(_app(), "987654", minutes=10, token="TOK-XYZ")
        kw = _sendmail_kwargs(mf)
        msg = kw["message"]
        self.assertIn("reprise?dossier=CAN-2026-00001", msg)  # lien de reprise
        self.assertIn("TOK-XYZ", msg)                          # token dans le lien
        self.assertIn("otp=987654", msg)                       # OTP pré-saisi dans le lien
        self.assertNotIn("987654", kw["subject"])              # invariant : jamais dans le sujet

    def test_no_link_when_no_token_backward_compat(self):
        with patch(f"{NOTIF}.frappe") as mf:
            from admission.api.notifications import send_email_otp
            send_email_otp(_app(), "987654", minutes=10)
        msg = _sendmail_kwargs(mf)["message"]
        self.assertNotIn("reprise?dossier=", msg)              # pas de lien sans token


# ── B. Liens tokenisés (A0.2) ──────────────────────────────────────────────────


class TestTokenizedLinks(TestCase):
    def test_account_created_has_resume_link(self):
        with patch(f"{NOTIF}.frappe") as mf:
            from admission.api.notifications import send_account_created
            send_account_created(_app(), "TOK-SECRET-123")
        msg = _sendmail_kwargs(mf)["message"]
        self.assertIn("reprise?dossier=CAN-2026-00001", msg)
        self.assertIn("TOK-SECRET-123", msg)

    def test_recovery_link_has_rotated_token(self):
        with patch(f"{NOTIF}.frappe") as mf:
            from admission.api.notifications import send_recovery_link
            send_recovery_link(_app(), "NEW-TOK-456")
        msg = _sendmail_kwargs(mf)["message"]
        self.assertIn("reprise?dossier=CAN-2026-00001", msg)
        self.assertIn("NEW-TOK-456", msg)


# ── C. Instructions SOP (virement / espèces, frais 1 / frais 2) ────────────────


def _fee(amount=15000):
    return types.SimpleNamespace(name="FEE-001", amount_xof=amount)


class TestOfflineSubmission(TestCase):
    def test_bank_has_real_iban_and_rib_attachment(self):
        rib = {"fname": "RIB-LaNEM-CorisBank.pdf", "fcontent": b"%PDF"}
        with patch(f"{NOTIF}.frappe") as mf, \
             patch(f"{NOTIF}._rib_attachment", return_value=rib):
            from admission.api.notifications import send_offline_submission
            send_offline_submission(_app(), _fee(), "bank")
        kw = _sendmail_kwargs(mf)
        self.assertIn("BJ66 BJ21 2010 1400 6158 0241 0173", kw["message"])  # IBAN réel (A0.3)
        self.assertIn("CORIBJBJ", kw["message"])
        self.assertIn("CAN-2026-00001", kw["message"])  # référence du virement = dossier
        self.assertIn("15 000", kw["message"])
        self.assertEqual(kw["attachments"], [rib])

    def test_cash_points_to_direction_no_attachment(self):
        with patch(f"{NOTIF}.frappe") as mf:
            from admission.api.notifications import send_offline_submission
            send_offline_submission(_app(), _fee(), "cash")
        kw = _sendmail_kwargs(mf)
        self.assertIn("Direction", kw["message"])
        self.assertIn("espèces", kw["message"])
        self.assertNotIn("attachments", kw)

    def test_frais2_label_changes_subject(self):
        with patch(f"{NOTIF}.frappe") as mf:
            from admission.api.notifications import send_offline_submission
            send_offline_submission(_app(), _fee(), "cash", fee_label="frais d'inscription")
        kw = _sendmail_kwargs(mf)
        self.assertIn("frais d'inscription", kw["subject"])
        self.assertNotIn("soumission", kw["subject"])

    def test_reminder_prefix(self):
        with patch(f"{NOTIF}.frappe") as mf:
            from admission.api.notifications import send_offline_submission
            send_offline_submission(_app(), _fee(), "cash", reminder=True)
        self.assertTrue(_sendmail_kwargs(mf)["subject"].startswith("Rappel — "))

    def test_rib_attachment_reads_real_pdf(self):
        # SANS mock (LOT RIB-SETTINGS) : la PJ vient d'Admission Settings, nom VERSIONNÉ
        # — corps et pièce jointe sortent de la même génération (anti-périmé).
        import frappe as real_frappe
        from admission.api.notifications import _rib_attachment
        att = _rib_attachment()
        self.assertIsNotNone(att)
        version = real_frappe.db.get_value("Admission Settings", "Admission Settings", "rib_version")
        self.assertEqual(att["fname"], f"RIB-LaNEM-v{version}.pdf")
        self.assertTrue(att["fcontent"].startswith(b"%PDF"))


# ── D. Inscription confirmée + préavis de purge ────────────────────────────────


class TestEnrolledAndPurgeNotice(TestCase):
    def test_enrolled_links_to_campus(self):
        with patch(f"{NOTIF}.frappe") as mf:
            mf.conf.get.return_value = "https://campus.lanem.bj"
            from admission.api.notifications import send_enrolled
            send_enrolled(_app(), student_id="STU-0042")
        kw = _sendmail_kwargs(mf)
        self.assertIn("https://campus.lanem.bj", kw["message"])
        self.assertIn("STU-0042", kw["message"])
        self.assertIn("confirmée", kw["subject"])

    def test_purge_notice_days_left(self):
        with patch(f"{NOTIF}.frappe") as mf:
            from admission.api.notifications import send_purge_notice
            send_purge_notice(_app(), days_left=7)
        kw = _sendmail_kwargs(mf)
        self.assertIn("7 jours", kw["message"])
        self.assertIn("expire", kw["subject"])


# ── E. verify_otp mono-canal (A0.1) ────────────────────────────────────────────


class TestVerifyOtpSingleChannel(TestCase):
    def _applicant(self, email_hash, phone_hash):
        a = MagicMock()
        a.name = "CAN-001"
        a.otp_email_hash = email_hash
        a.otp_phone_hash = phone_hash
        a.otp_expires_at = "2026-06-09 12:05:00"  # NOW + 5 min, non expiré
        return a

    @patch(f"{PUB}.now_datetime", return_value=get_datetime(NOW))
    @patch(f"{PUB}._generate_token", return_value="NEWTOKEN")
    @patch(f"{PUB}._get_applicant")
    @patch(f"{PUB}.frappe")
    def test_email_only_succeeds_without_phone(self, mock_frappe, mock_get, _gen, _now):
        mock_frappe.conf.get.return_value = "test-otp-secret"
        mock_frappe.form_dict = {}   # pas de phone_otp soumis (ni kwarg ni body)
        mock_frappe.request = None
        from admission.api.public import _hash_otp, verify_otp
        # Canal SMS jamais livré : aucun phone_otp soumis → email seul suffit (A0.1).
        mock_get.return_value = self._applicant(_hash_otp("111111"), _hash_otp("222222"))
        result = verify_otp(dossier_id="CAN-001", token="tok", email_otp="111111")
        self.assertTrue(result["ok"])
        self.assertEqual(result["data"]["token"], "NEWTOKEN")

    @patch(f"{PUB}.now_datetime", return_value=get_datetime(NOW))
    @patch(f"{PUB}._get_applicant")
    @patch(f"{PUB}.frappe")
    def test_wrong_phone_still_fails(self, mock_frappe, mock_get, _now):
        mock_frappe.conf.get.return_value = "test-otp-secret"
        from admission.api.public import _hash_otp, verify_otp
        # phone_otp SOUMIS et faux → échec (pas de contournement par le canal optionnel).
        mock_get.return_value = self._applicant(_hash_otp("111111"), _hash_otp("222222"))
        result = verify_otp(dossier_id="CAN-001", token="tok",
                            email_otp="111111", phone_otp="999999")
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "OTP_INVALID")

    @patch(f"{PUB}.now_datetime", return_value=get_datetime(NOW))
    @patch(f"{PUB}._get_applicant")
    @patch(f"{PUB}.frappe")
    def test_email_mandatory(self, mock_frappe, mock_get, _now):
        mock_frappe.conf.get.return_value = "test-otp-secret"
        from admission.api.public import _hash_otp, verify_otp
        # Bon phone_otp SANS email_otp → échec : l'e-mail est le canal porteur obligatoire.
        mock_get.return_value = self._applicant(_hash_otp("111111"), _hash_otp("222222"))
        result = verify_otp(dossier_id="CAN-001", token="tok", phone_otp="222222")
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "OTP_INVALID")


# ── F. recover_dossier (M7) — anti-énumération + rotation ──────────────────────


class TestRecoverDossier(TestCase):
    @patch(f"{PUB}.frappe")
    def test_no_match_returns_generic(self, mock_frappe):
        mock_frappe.get_all.return_value = []
        with patch("admission.api.notifications.send_recovery_link") as send:
            from admission.api.public import recover_dossier
            result = recover_dossier(email="inconnu@x.bj")
        self.assertTrue(result["ok"])
        self.assertIn("Si un dossier actif correspond", result["data"]["message"])
        send.assert_not_called()

    @patch(f"{PUB}.now_datetime", return_value=get_datetime(NOW))
    @patch(f"{PUB}._generate_token", return_value="ROTATED-TOK")
    @patch(f"{PUB}.frappe")
    def test_match_rotates_token_resets_otp_same_response(self, mock_frappe, _gen, _now):
        mock_frappe.get_all.return_value = ["CAN-001"]
        applicant = MagicMock()
        applicant.name = "CAN-001"
        mock_frappe.get_doc.return_value = applicant
        with patch("admission.api.notifications.send_recovery_link") as send:
            from admission.api.public import _hash, recover_dossier
            result = recover_dossier(email="ama@x.bj")
        # Réponse IDENTIQUE au cas « aucun dossier » (aucun oracle d'existence).
        self.assertIn("Si un dossier actif correspond", result["data"]["message"])
        self.assertEqual(applicant.dossier_token_hash, _hash("ROTATED-TOK"))
        self.assertEqual(applicant.otp_verified, 0)  # double barrière ré-armée
        send.assert_called_once_with(applicant, "ROTATED-TOK")

    @patch(f"{PUB}.frappe")
    def test_invalid_email_returns_generic_without_lookup(self, mock_frappe):
        from admission.api.public import recover_dossier
        result = recover_dossier(email="pas-un-email")
        self.assertTrue(result["ok"])
        mock_frappe.get_all.assert_not_called()


# ── G. Relances scheduler (M9) ─────────────────────────────────────────────────


class TestSchedulerReminders(TestCase):
    @patch(f"{NOTIF}.send_offline_submission")
    @patch(f"{NOTIF}.frappe")
    def test_sop_reminder_resends_and_flags(self, mock_frappe, send):
        pending = types.SimpleNamespace(payment_mode="Bank", applicant_fee="FEE-001")
        mock_frappe.get_all.side_effect = [["CAN-001"], [pending]]
        applicant, fee = MagicMock(), MagicMock()
        mock_frappe.get_doc.side_effect = lambda dt, name: applicant if dt == "Admission Applicant" else fee
        from admission.api.notifications import remind_dormant_sop_dossiers
        result = remind_dormant_sop_dossiers()
        send.assert_called_once_with(applicant, fee, "bank", reminder=True)
        args = mock_frappe.db.set_value.call_args[0]
        self.assertEqual(args[2], "sop_reminder_sent_at")  # flag anti-double-envoi
        self.assertEqual(result, {"sop_reminders_sent": 1})

    @patch(f"{NOTIF}.send_offline_submission")
    @patch(f"{NOTIF}.frappe")
    def test_sop_reminder_skips_without_pending_payment(self, mock_frappe, send):
        mock_frappe.get_all.side_effect = [["CAN-001"], []]  # plus de paiement Pending
        mock_frappe.get_doc.return_value = MagicMock()
        from admission.api.notifications import remind_dormant_sop_dossiers
        result = remind_dormant_sop_dossiers()
        send.assert_not_called()
        self.assertEqual(result, {"sop_reminders_sent": 0})

    @patch("admission.api.notifications.send_purge_notice")
    @patch(f"{RET}.frappe")
    def test_purge_notice_sends_and_flags(self, mock_frappe, send):
        mock_frappe.get_all.return_value = ["CAN-009"]
        applicant = MagicMock()
        mock_frappe.get_doc.return_value = applicant
        from admission.api.retention import notify_expiring_drafts
        result = notify_expiring_drafts()
        send.assert_called_once_with(applicant, days_left=7)
        args = mock_frappe.db.set_value.call_args[0]
        self.assertEqual(args[2], "purge_notice_sent_at")
        self.assertEqual(result, {"purge_notices_sent": 1})


# ── H. Mail du reçu (template paiement, nom échappé) ───────────────────────────


class TestReceiptEmailBody(TestCase):
    def test_amount_receipt_and_escaped_name(self):
        from admission.api.receipt import _email_body
        applicant = _app(applicant_name="Ama <script>")
        payment = types.SimpleNamespace(applicant="CAN-2026-00001", amount_xof=15000,
                                        payment_mode="Bank", receipt_number="REC-2026-001",
                                        paid_at="2026-06-09 10:00:00")
        html = _email_body(applicant, payment)
        self.assertIn("15 000", html)
        self.assertIn("REC-2026-001", html)
        self.assertIn("Virement bancaire", html)
        self.assertIn("Ama &lt;script&gt;", html)   # fix audit : nom désormais ÉCHAPPÉ
        self.assertNotIn("<script>", html)
