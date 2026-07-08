"""STAFF-LOGIN-RECOVERY — pont de demande de réinitialisation (admission.api.account).

Invariants sécurité : l'ENDPOINT est constant (corps uniforme + travail ENFILÉ dans un job
→ timing constant = anti-énumération temporelle) ; le JOB `_maybe_send` n'envoie qu'à un
compte staff actif, lien vers le FRONT staff (jamais /app), mail ENQUEUED (pas now=True),
clé/e-mail JAMAIS loggés, conf absente = fail-safe. Parcours réel en Phase 4.
"""

from unittest import TestCase
from unittest.mock import MagicMock, patch

A = "admission.api.account"
FRONT = "https://staff-rec.lanem.bj"
KEY = "k3y-s3cr3t-native"


def _mk_user(enabled=1, name="staff@lanem.bj"):
    u = MagicMock()
    u.name = name
    u.enabled = enabled
    u.reset_password = MagicMock(return_value=f"http://back/update-password?key={KEY}")
    return u


class TestRequestEndpoint(TestCase):
    """L'endpoint fait un travail CONSTANT : log + enqueue + réponse uniforme. Rien qui
    varie selon le compte → ni le corps ni le timing ne fuient l'existence."""

    def _call(self, email):
        with patch(f"{A}.frappe") as mf, \
             patch(f"{A}._ok", side_effect=lambda d=None: {"ok": True, "data": d, "error": None}):
            mf.form_dict = {}
            from admission.api.account import request_password_reset
            res = request_password_reset(email=email)
            return res, mf

    def test_response_is_uniform_and_enqueues_regardless(self):
        r1, m1 = self._call("staff@lanem.bj")
        r2, m2 = self._call("ghost@lanem.bj")
        self.assertEqual(r1, r2)                                      # corps identique
        for m, email in ((m1, "staff@lanem.bj"), (m2, "ghost@lanem.bj")):
            m.enqueue.assert_called_once()
            args, kwargs = m.enqueue.call_args
            self.assertEqual(args[0], "admission.api.account._maybe_send")
            self.assertEqual(kwargs["email"], email)                 # même chemin pour tous

    def test_enqueue_failure_does_not_break_response(self):
        with patch(f"{A}.frappe") as mf, \
             patch(f"{A}._ok", side_effect=lambda d=None: {"ok": True, "data": d, "error": None}):
            mf.form_dict = {}
            mf.enqueue.side_effect = Exception("redis down")
            from admission.api.account import request_password_reset
            res = request_password_reset(email="staff@lanem.bj")
            self.assertTrue(res["data"]["sent"])                     # jamais de 500 révélateur

    def test_request_is_rate_limited_structurally(self):
        from admission.api import account
        self.assertTrue(hasattr(account.request_password_reset, "__wrapped__"))
        self.assertEqual(account.request_password_reset.__wrapped__.__name__,
                         "request_password_reset")


class TestMaybeSend(TestCase):
    """Le JOB : n'envoie qu'à un staff actif ; silencieux (0 effet) partout ailleurs."""

    def _run(self, *, exists=True, user=None, roles=("Admission Administratif",), conf=FRONT):
        with patch(f"{A}.frappe") as mf, patch(f"{A}.log_event") as mlog:
            mf.db.exists.return_value = exists
            mf.get_doc.return_value = user or _mk_user()
            mf.get_roles.return_value = list(roles)
            mf.conf.get.side_effect = lambda k, d=None: {"staff_portal_url": conf}.get(k, d)
            from admission.api.account import _maybe_send
            _maybe_send("staff@lanem.bj")
            return mf, mlog

    def test_valid_staff_mail_enqueued_with_front_link(self):
        mf, _ = self._run()
        mf.sendmail.assert_called_once()
        kw = mf.sendmail.call_args.kwargs
        self.assertEqual(kw["recipients"], ["staff@lanem.bj"])
        self.assertIn(f"{FRONT}/reinitialisation?key={KEY}", kw["message"])   # lien FRONT staff
        self.assertNotIn("/update-password", kw["message"])                   # jamais le desk
        self.assertNotIn("now", kw)                                           # ENQUEUED

    def test_unknown_disabled_nonstaff_admin_no_mail(self):
        for c in (dict(exists=False), dict(user=_mk_user(enabled=0)), dict(roles=()),
                  dict(user=_mk_user(name="Administrator"))):
            mf, _ = self._run(**c)
            mf.sendmail.assert_not_called()

    def test_missing_conf_fail_safe_logged(self):
        mf, mlog = self._run(conf="")
        mf.sendmail.assert_not_called()
        steps = [(c.args[0], c.args[1]) for c in mlog.call_args_list]
        self.assertIn(("password_reset", "misconfigured"), steps)

    def test_key_never_logged(self):
        mf, mlog = self._run()
        self.assertNotIn(KEY, str(mlog.call_args_list))
        self.assertNotIn(KEY, str(mf.logger.mock_calls))

    def test_email_never_logged_in_clear(self):
        _, mlog = self._run()
        self.assertNotIn("staff@lanem.bj", str(mlog.call_args_list))          # ref = hash, 0 PII

    def test_fallback_reset_method_15_111(self):
        u = _mk_user()
        del u.reset_password                                                  # 15.111 : _reset_password
        u._reset_password = MagicMock(return_value=f"http://back/update-password?key={KEY}")
        mf, _ = self._run(user=u)
        u._reset_password.assert_called_once_with(send_email=False)
        mf.sendmail.assert_called_once()

    def test_internal_error_never_raises(self):
        boom = _mk_user()
        boom.reset_password = MagicMock(side_effect=Exception("db boom"))
        mf, mlog = self._run(user=boom)                                       # ne lève pas
        steps = [(c.args[0], c.args[1]) for c in mlog.call_args_list]
        self.assertIn(("password_reset", "internal_error"), steps)
