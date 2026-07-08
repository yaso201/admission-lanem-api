"""NOTIF soumission — dédup at-call-site : la cascade de confirmation notifie « soumission »
UNIQUEMENT depuis BRO (paiement en ligne direct). Une provisoire (SOP) a déjà été notifiée
au declare → SOP→SOU ne re-notifie pas (pas de double compte). Style mocké."""

from unittest import TestCase
from unittest.mock import MagicMock, patch

P = "admission.api.public"


def _applicant(status):
    a = MagicMock()
    a.name = "26274090002"
    a.status = status
    a.programme_code = "DD-MI-CPI"
    a.level_code = "DD-MI-CPI-L1"
    return a


def _fee():
    f = MagicMock()
    f.fee_type = "application"
    f.status = "Paid"                      # déjà Paid → pas de save ; promo capturée à part
    return f


class TestSubmissionNotifDedup(TestCase):
    def _run(self, status):
        with patch(f"{P}.frappe") as mf, \
             patch(f"{P}._capture_promo_if_eligible"), \
             patch(f"{P}._record_candidate_transition"):
            mf.session.user = "Guest"                     # chemin webhook (en ligne)
            from admission.api.public import apply_confirmed_payment_cascade
            apply_confirmed_payment_cascade(_applicant(status), _fee())
            return mf

    def test_bro_to_sou_enqueues_submission_notif(self):
        mf = self._run("BRO")
        calls = [c for c in mf.enqueue.call_args_list
                 if c.args and c.args[0] == "admission.api.alerting.notify_new_submission"]
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].kwargs["dossier_id"], "26274090002")
        self.assertEqual(calls[0].kwargs["mode"], "payée en ligne")
        self.assertEqual(calls[0].kwargs["programme"], "DD-MI-CPI")

    def test_sop_to_sou_does_not_re_notify(self):
        mf = self._run("SOP")                             # déjà notifié au declare
        calls = [c for c in mf.enqueue.call_args_list
                 if c.args and c.args[0] == "admission.api.alerting.notify_new_submission"]
        self.assertEqual(calls, [])                       # 0 double compte
