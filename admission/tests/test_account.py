"""STAFF-LOGIN-RECOVERY — pont de demande de réinitialisation (admission.api.account).

Invariants sécurité : réponse UNIFORME (anti-énumération, comparée en profondeur),
mail ENQUEUED (jamais now=True → la réponse ne bloque pas sur SMTP, timing uniforme),
lien vers le front staff (jamais /app), clé JAMAIS loggée, rate-limit structurel,
conf absente = fail-safe silencieux. Style mocké (test_health) ; parcours réel en Phase 4.
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


class TestRequestPasswordReset(TestCase):
    def _call(self, email, *, exists=True, user=None, roles=("Admission Administratif",),
              conf=FRONT):
        with patch(f"{A}.frappe") as mf, \
             patch(f"{A}._ok", side_effect=lambda d=None: {"ok": True, "data": d, "error": None}), \
             patch(f"{A}.log_event") as mlog:
            mf.db.exists.return_value = exists
            mf.get_doc.return_value = user or _mk_user()
            mf.get_roles.return_value = list(roles)
            mf.conf.get.side_effect = lambda k, d=None: {"staff_portal_url": conf}.get(k, d)
            from admission.api.account import request_password_reset
            res = request_password_reset(email=email)
            return res, mf, mlog

    # ── anti-énumération : réponse STRICTEMENT identique dans tous les cas ──
    def test_uniform_response_all_cases_deep_equal(self):
        cases = [
            dict(exists=True),                                        # staff valide (mail part)
            dict(exists=False),                                       # inconnu
            dict(exists=True, user=_mk_user(enabled=0)),              # désactivé
            dict(exists=True, roles=()),                              # User sans rôle staff
            dict(exists=True, user=_mk_user(name="Administrator")),   # protégé
            dict(exists=True, conf=""),                               # conf absente
        ]
        responses = [self._call("x@lanem.bj", **c)[0] for c in cases]
        for r in responses[1:]:
            self.assertEqual(r, responses[0])                         # profondeur : dicts identiques

    def test_valid_staff_mail_enqueued_with_front_link(self):
        res, mf, _ = self._call("staff@lanem.bj")
        mf.sendmail.assert_called_once()
        kw = mf.sendmail.call_args.kwargs
        self.assertEqual(kw["recipients"], ["staff@lanem.bj"])
        self.assertIn(f"{FRONT}/reinitialisation?key={KEY}", kw["message"])   # lien FRONT staff
        self.assertNotIn("/update-password", kw["message"])                   # jamais le desk
        self.assertNotIn("now", kw)                                           # ENQUEUED (timing uniforme)

    def test_unknown_email_no_mail_same_response(self):
        res, mf, _ = self._call("ghost@lanem.bj", exists=False)
        mf.sendmail.assert_not_called()
        self.assertTrue(res["data"]["sent"])                          # dit « envoyé » quand même

    def test_disabled_and_non_staff_and_admin_no_mail(self):
        for c in (dict(user=_mk_user(enabled=0)), dict(roles=()),
                  dict(user=_mk_user(name="Administrator"))):
            _, mf, _ = self._call("x@lanem.bj", **c)
            mf.sendmail.assert_not_called()

    def test_missing_conf_fail_safe_logged(self):
        _, mf, mlog = self._call("staff@lanem.bj", conf="")
        mf.sendmail.assert_not_called()
        steps = [(c.args[0], c.args[1]) for c in mlog.call_args_list]
        self.assertIn(("password_reset", "misconfigured"), steps)     # visible ops (error)

    def test_key_never_logged(self):
        _, mf, mlog = self._call("staff@lanem.bj")
        self.assertNotIn(KEY, str(mlog.call_args_list))               # la clé ne fuit pas en trace
        self.assertNotIn(KEY, str(mf.logger.mock_calls))

    def test_email_never_logged_in_clear(self):
        _, _, mlog = self._call("staff@lanem.bj")
        self.assertNotIn("staff@lanem.bj", str(mlog.call_args_list))  # ref = hash, 0 PII

    def test_fallback_reset_method_15_111(self):
        # recette 15.111 : la méthode s'appelle _reset_password (V-LEARN, patron getattr)
        u = _mk_user()
        del u.reset_password                                          # absent → fallback
        u._reset_password = MagicMock(return_value=f"http://back/update-password?key={KEY}")
        _, mf, _ = self._call("staff@lanem.bj", user=u)
        u._reset_password.assert_called_once_with(send_email=False)
        mf.sendmail.assert_called_once()

    def test_internal_error_still_uniform(self):
        boom = _mk_user()
        boom.reset_password = MagicMock(side_effect=Exception("db boom"))
        res, _, _ = self._call("staff@lanem.bj", user=boom)
        self.assertTrue(res["data"]["sent"])                          # jamais de 500 révélateur


class TestRateLimit(TestCase):
    def test_request_is_rate_limited_structurally(self):
        from admission.api import account
        self.assertTrue(hasattr(account.request_password_reset, "__wrapped__"))
        self.assertEqual(account.request_password_reset.__wrapped__.__name__,
                         "request_password_reset")
