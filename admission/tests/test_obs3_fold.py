"""OBS-3-FOLD — finition observabilité : 6 items sur l'infra OBS-1/2 existante.

Style mocké (comme test_health/test_alerting). Preuves recette (heartbeat visible,
health dégrade sur clé absente, digest ligne santé) en Phase 4.

Invariants : error-level (V-LEARN-LOGLEVEL-23 — info/warning invisible) ; 0 recalcul
(item 1 dans _ops_counters partagé) ; item 5 recette-safe ; ligne santé au digest.
"""

import json
from unittest import TestCase
from unittest.mock import MagicMock, patch

O = "admission.api.admin_ops"
H = "admission.api.health"
A = "admission.api.alerting"


class TestItem1ScheduledJobFailed(TestCase):
    def test_ops_counters_includes_scheduled_job_failed_24h(self):
        with patch(f"{O}.frappe") as mf, patch(f"{O}.add_to_date", return_value="CUTOFF"), \
             patch(f"{O}.now_datetime", return_value="NOW"):
            seen = {}
            def fake_count(dt, filters=None):
                seen[dt] = filters
                return 4 if dt == "Scheduled Job Log" else 0
            mf.db.count.side_effect = fake_count
            from admission.api.admin_ops import _ops_counters
            c = _ops_counters()
            self.assertEqual(c["scheduled_job_failed_24h"], 4)          # couvre les 11 jobs d'un coup
            f = seen["Scheduled Job Log"]
            self.assertEqual(f["status"], "Failed")
            self.assertEqual(f["creation"], [">", "CUTOFF"])            # fenêtre 24h


class TestItem2ExpireStaleHeartbeat(TestCase):
    def test_run_summary_logged_at_error_level(self):
        P = "admission.api.public"
        with patch(f"{P}.frappe") as mf, patch(f"{P}.log_event") as mlog, \
             patch(f"{P}.add_to_date", return_value="CUT"), patch(f"{P}.now_datetime", return_value="NOW"):
            mf.get_all.return_value = ["P1", "P2", "P3"]
            from admission.api.public import expire_stale_online_pending
            n = expire_stale_online_pending()
            self.assertEqual(n, 3)
            call = next(c for c in mlog.call_args_list if c.args[0] == "expire_stale_run")
            self.assertEqual(call.kwargs["level"], "error")            # battement visible (job argent muet)
            self.assertEqual(call.kwargs["marked"], 3)


class TestItem3RetentionStepError(TestCase):
    def test_step_failure_logged_via_log_event_error(self):
        R = "admission.api.retention"
        boom = MagicMock(side_effect=Exception("purge boom"))
        boom.__name__ = "purge_expired_otp"                            # en prod = vraie fonction
        with patch(f"{R}.frappe") as mf, patch(f"{R}.log_event") as mlog, \
             patch(f"{R}.purge_expired_otp", boom), \
             patch(f"{R}.purge_abandoned_dossiers", return_value={}), \
             patch(f"{R}.purge_terminal_dossiers", return_value={}):
            from admission.api.retention import scheduled_retention_run
            scheduled_retention_run()                                  # ne lève pas (non-bloquant)
            call = next(c for c in mlog.call_args_list if c.args[0] == "retention_run")
            self.assertEqual(call.args[1], "step_failed")
            self.assertEqual(call.kwargs["level"], "error")
            self.assertEqual(call.kwargs["step"], "purge_expired_otp")


class TestItem4TransitionLogError(TestCase):
    def test_transition_log_failure_logged_at_error(self):
        P = "admission.api.public"
        D = "admission.admission.doctype.admission_applicant.admission_applicant"
        with patch(f"{P}.frappe") as mf, patch(f"{P}.log_event") as mlog, \
             patch(f"{D}._detect_transition_context", return_value=("src", "act")), \
             patch(f"{D}.write_transition_log", side_effect=Exception("audit boom")):
            from admission.api.public import _record_candidate_transition
            _record_candidate_transition("CAN-2026-00001", "INC", "SOU")  # except interne → log_event error
            call = next(c for c in mlog.call_args_list if c.args[0] == "transition_log")
            self.assertEqual(call.args[1], "failed")
            self.assertEqual(call.kwargs["level"], "error")
            self.assertEqual(call.kwargs["dossier_id"], "CAN-2026-00001")


class TestItem5KkiapayCritical(TestCase):
    def _probe(self, present_keys):
        with patch(f"{H}.frappe") as mf:
            mf.conf.get.side_effect = lambda k, d=None: "x" if k in present_keys else None
            from admission.api.health import _probe_config
            return _probe_config()

    def test_all_present_ok(self):
        allk = ("campus_base_url", "candidate_portal_url", "admission_payment_webhook_secret",
                "kkiapay_public_key", "kkiapay_private_key", "kkiapay_secret_key")
        ok, detail = self._probe(allk)
        self.assertTrue(ok)

    def test_missing_kkiapay_key_degrades_by_name_only(self):
        allk = ("campus_base_url", "candidate_portal_url", "admission_payment_webhook_secret",
                "kkiapay_public_key", "kkiapay_private_key")           # secret_key ABSENTE
        ok, detail = self._probe(allk)
        self.assertFalse(ok)                                          # angle mort A fermé : health dégrade
        self.assertIn("kkiapay_secret_key", detail)                  # nom seul, pas de valeur


class TestItem6DigestHealthLine(TestCase):
    def test_digest_embeds_health_summary(self):
        with patch(f"{A}.frappe") as mf, patch(f"{A}._send_telegram", return_value=True), \
             patch(f"{O}._ops_counters", return_value={"uf_unreplicated": 0}), \
             patch(f"{H}._run_checks", return_value=(False, {"config": {"ok": False, "detail": "manquant: kkiapay_secret_key"}})):
            mf.conf.get.side_effect = lambda k, d=None: {"admission_ops_digest_recipients": "ops@lanem.bj"}.get(k, d)
            mf.cache.make_key.side_effect = lambda k: k
            mf.local.site = "rec"
            from admission.api.alerting import send_daily_digest
            send_daily_digest()
            body = mf.sendmail.call_args[1]["message"]
            self.assertIn("Santé", body)
            self.assertIn("degraded", body)                          # la ligne santé rejoue les sondes
            self.assertIn("sondes ko: config", body)                 # sonde ko nommée (détail via health.check)

    def test_digest_health_line_healthy_branch(self):
        # L2 : la branche HEALTHY (sensible à la précédence) — "Santé: healthy", sans suffixe ko
        with patch(f"{A}.frappe") as mf, patch(f"{A}._send_telegram", return_value=True), \
             patch(f"{O}._ops_counters", return_value={"uf_unreplicated": 0}), \
             patch(f"{H}._run_checks", return_value=(True, {"db": {"ok": True, "detail": "joignable"}})):
            mf.conf.get.side_effect = lambda k, d=None: {"admission_ops_digest_recipients": "ops@lanem.bj"}.get(k, d)
            mf.cache.make_key.side_effect = lambda k: k
            mf.local.site = "rec"
            from admission.api.alerting import send_daily_digest
            send_daily_digest()
            body = mf.sendmail.call_args[1]["message"]
            self.assertIn("Santé: healthy", body)
            self.assertNotIn("sondes ko", body)                      # pas de suffixe quand tout va bien
