"""OBS-2 — alerte opérationnelle : émetteur Telegram durci + cooldown + dispatch log_event + digest.

Style mocké (comme test_health) : on patche `alerting.frappe`/`alerting.requests`.
Les preuves recette (bot réel, mail reçu, non-bloquant live) sont jouées en Phase 4.

Invariants testés :
- fire-and-forget : AUCUNE fonction publique ne lève (Telegram down ≠ paiement cassé) ;
- le jeton n'apparaît JAMAIS dans les traces ;
- cooldown par type : rafale → 1 envoi (la journalisation, elle, continue) ;
- 0 PII : identifiants internes seulement (CAN-/REC-) — pas de texte libre transmis du dispatch ;
- digest : métriques _ops_counters + seuils, mail + copie Telegram, silence défensif.
"""

import json
from unittest import TestCase
from unittest.mock import MagicMock, patch

import frappe

A = "admission.api.alerting"
L = "admission.api._log"
O = "admission.api.admin_ops"

TOKEN = "123456:AAH-secret-bot-token"


def _mock_frappe(mf, *, conf=None, cooldown_free=True):
    """Prépare le mock frappe pour alerting : conf, cache, logger, site."""
    if conf is None:
        conf = {"admission_telegram_bot_token": TOKEN, "admission_telegram_chat_id": "-10042"}
    mf.conf.get.side_effect = lambda k, d=None: conf.get(k, d)
    mf.cache.make_key.side_effect = lambda k: f"test|{k}"
    mf.cache.get.return_value = None if cooldown_free else 1
    mf.local.site = "rec"
    logger = MagicMock()
    mf.logger.return_value = logger
    mf.get_traceback.return_value = "tb"
    return logger


class TestSendTelegram(TestCase):
    def test_posts_to_bot_api_with_conf(self):
        with patch(f"{A}.frappe") as mf, patch(f"{A}.requests") as mr:
            _mock_frappe(mf)
            mr.post.return_value = MagicMock(ok=True)
            from admission.api.alerting import _send_telegram
            self.assertTrue(_send_telegram("hello"))
            url = mr.post.call_args[0][0]
            self.assertIn(TOKEN, url)                       # jeton dans l'URL d'appel…
            self.assertIn("sendMessage", url)
            payload = mr.post.call_args[1]["json"]
            self.assertEqual(payload["chat_id"], "-10042")
            self.assertEqual(payload["text"], "hello")
            self.assertLessEqual(mr.post.call_args[1]["timeout"], 3)   # vigilance latence : ≤3s inline

    def test_missing_config_is_silent_noop(self):
        with patch(f"{A}.frappe") as mf, patch(f"{A}.requests") as mr:
            _mock_frappe(mf, conf={})                        # pas de jeton posé
            from admission.api.alerting import _send_telegram
            self.assertFalse(_send_telegram("hello"))
            mr.post.assert_not_called()

    def test_network_failure_silent_and_token_never_logged(self):
        with patch(f"{A}.frappe") as mf, patch(f"{A}.requests") as mr:
            logger = _mock_frappe(mf)
            mr.post.side_effect = Exception("boom réseau")
            from admission.api.alerting import _send_telegram
            self.assertFalse(_send_telegram("hello"))        # pas de levée
            logged = " ".join(str(c) for c in logger.warning.call_args_list)
            self.assertNotIn(TOKEN, logged)                  # …mais JAMAIS dans les traces


class TestCooldown(TestCase):
    def test_first_acquire_allows_and_sets_window(self):
        with patch(f"{A}.frappe") as mf:
            _mock_frappe(mf)
            from admission.api.alerting import _cooldown_acquire
            self.assertTrue(_cooldown_acquire("uf_payment"))
            args = mf.cache.setex.call_args[0]
            self.assertEqual(args[1], 10 * 60)               # défaut 10 min

    def test_second_within_window_suppressed(self):
        with patch(f"{A}.frappe") as mf:
            _mock_frappe(mf, cooldown_free=False)
            from admission.api.alerting import _cooldown_acquire
            self.assertFalse(_cooldown_acquire("uf_payment"))
            mf.cache.setex.assert_not_called()


class TestSendHighAlert(TestCase):
    def test_sends_message_with_internal_ids_only(self):
        with patch(f"{A}.frappe") as mf, patch(f"{A}.requests") as mr:
            _mock_frappe(mf)
            mr.post.return_value = MagicMock(ok=True)
            from admission.api.alerting import send_high_alert
            self.assertTrue(send_high_alert("uf_payment", dossier_id="CAN-2026-00001", ref="REC-2026-00001"))
            text = mr.post.call_args[1]["json"]["text"]
            self.assertIn("CAN-2026-00001", text)
            self.assertIn("REC-2026-00001", text)
            self.assertIn("UF", text)                        # libellé neutre du type

    def test_cooldown_suppresses_send_but_still_journalises(self):
        with patch(f"{A}.frappe") as mf, patch(f"{A}.requests") as mr:
            logger = _mock_frappe(mf, cooldown_free=False)
            from admission.api.alerting import send_high_alert
            self.assertFalse(send_high_alert("uf_payment", dossier_id="CAN-2026-00001"))
            mr.post.assert_not_called()                      # rafale → 0 envoi…
            logged = " ".join(str(c) for c in logger.info.call_args_list)
            self.assertIn("cooldown_suppressed", logged)     # …mais trace conservée

    def test_never_raises_even_if_cache_breaks(self):
        with patch(f"{A}.frappe") as mf:
            _mock_frappe(mf)
            mf.cache.get.side_effect = Exception("redis down")
            from admission.api.alerting import send_high_alert
            self.assertFalse(send_high_alert("uf_payment"))  # silence défensif total


class TestLogEventDispatch(TestCase):
    def _log_event(self, mocked_alert, **kwargs):
        with patch(f"{L}.frappe") as mf:
            logger = MagicMock()
            mf.logger.return_value = logger
            with patch(f"{A}.send_high_alert", mocked_alert):
                from admission.api._log import log_event
                log_event("webhook_payment", "failed", **kwargs)
            return logger

    def test_alert_type_dispatches_ids_only(self):
        alert = MagicMock()
        self._log_event(alert, dossier_id="CAN-2026-00001", ref="R-1",
                        error="stack trace avec potentielle PII", alert_type="uf_payment")
        alert.assert_called_once_with("uf_payment", dossier_id="CAN-2026-00001", ref="R-1")
        # allowlist stricte : le texte libre (error/fields) n'est PAS transmis à l'alerte

    def test_alert_failure_never_breaks_logging(self):
        alert = MagicMock(side_effect=Exception("alerting HS"))
        logger = self._log_event(alert, dossier_id="X", level="error", alert_type="uf_payment")
        alert.assert_called_once()                            # le dispatch a bien tenté l'alerte…
        logger.error.assert_called()                          # …et la journalisation a eu lieu quand même

    def test_no_alert_type_no_dispatch(self):
        alert = MagicMock()
        self._log_event(alert, dossier_id="X")
        alert.assert_not_called()


class TestClientErrorSpike(TestCase):
    def _note(self, count, threshold=20):
        with patch(f"{A}.frappe") as mf, patch(f"{A}.send_high_alert") as alert:
            _mock_frappe(mf, conf={"admission_client_error_spike_threshold": threshold})
            mf.cache.incrby.return_value = count
            from admission.api.alerting import note_client_error
            note_client_error()
            return alert, mf

    def test_threshold_crossing_triggers_alert_once(self):
        self._note(19)[0].assert_not_called()                 # sous le seuil
        self._note(20)[0].assert_called_once()                # franchissement exact → 1 alerte
        self._note(21)[0].assert_not_called()                 # au-delà → plus rien (== strict)

    def test_ttl_rearmed_on_window_start(self):
        # L1 : count==1 (clé fraîche/recréée) → on (RE)pose le TTL (anti-fenêtre-permanente)
        _, mf = self._note(1)
        mf.cache.expire.assert_called_once()
        self.assertEqual(mf.cache.expire.call_args[0][1], 600)
        # count>1 : pas de re-pose (fenêtre en cours)
        _, mf2 = self._note(5)
        mf2.cache.expire.assert_not_called()

    def test_malformed_threshold_falls_back_to_default_not_crash(self):
        # L3 : un seuil texte ne doit pas éteindre la détection (retombe sur le défaut 20)
        with patch(f"{A}.frappe") as mf, patch(f"{A}.send_high_alert") as alert:
            _mock_frappe(mf, conf={"admission_client_error_spike_threshold": "pas-un-entier"})
            mf.cache.incrby.return_value = 20                 # == défaut
            from admission.api.alerting import note_client_error
            note_client_error()
            alert.assert_called_once()

    def test_never_raises_when_cache_down(self):
        with patch(f"{A}.frappe") as mf:
            _mock_frappe(mf)
            mf.cache.incrby.side_effect = Exception("redis down")
            from admission.api.alerting import note_client_error
            note_client_error()                               # aucune levée


class TestOpsCounters(TestCase):
    def test_counters_include_invisible_metrics(self):
        with patch(f"{O}.frappe") as mf:
            counts = {}
            def fake_count(doctype, filters=None):
                counts[json.dumps([doctype, filters], sort_keys=True, default=str)] = True
                return 3
            mf.db.count.side_effect = fake_count
            from admission.api.admin_ops import _ops_counters
            c = _ops_counters()
            for key in ("uf_unreplicated", "bridge_pending", "pending_online_stale",
                        "orphan_refund_due", "underpaid_review", "refused_terminal",
                        "email_queue_error"):
                self.assertIn(key, c)
                self.assertEqual(c[key], 3)
            flat = " ".join(counts)
            self.assertIn("Orphan - refund due", flat)
            self.assertIn("Underpaid - review", flat)
            self.assertIn("Refused - terminal state (refund due)", flat)
            self.assertIn("Email Queue", flat)


class TestReconciliationOption(TestCase):
    def test_refused_terminal_is_valid_select_option(self):
        # GA8 / D-RECONCILE-OPTION : la valeur écrite par _refuse_terminal (webhook, db.set_value)
        # doit être une option Select VALIDE → un save() ORM ultérieur ne lève plus.
        doc = frappe.new_doc("Applicant Fee Payment")
        doc.reconciliation = "Refused - terminal state (refund due)"
        doc._validate_selects()                         # lèverait si l'option manquait au Select
        # sanity négatif : une valeur hors options lève bien (la garde fonctionne)
        doc.reconciliation = "Valeur inexistante xyz"
        with self.assertRaises(frappe.exceptions.ValidationError):
            doc._validate_selects()


class TestDailyDigest(TestCase):
    def _run(self, counters, *, conf=None, telegram_ok=True):
        conf = conf if conf is not None else {
            "admission_telegram_bot_token": TOKEN, "admission_telegram_chat_id": "-10042",
            "admission_ops_digest_recipients": "ops@lanem.bj, dir@lanem.bj",
            "admission_ops_thresholds": {"pending_online_stale": 5},
        }
        with patch(f"{A}.frappe") as mf, \
             patch(f"{A}._send_telegram", return_value=telegram_ok) as tg, \
             patch(f"{O}._ops_counters", return_value=counters):
            _mock_frappe(mf, conf=conf)
            from admission.api.alerting import send_daily_digest
            res = send_daily_digest()
            return res, mf, tg

    def test_digest_mails_and_telegrams_with_thresholds(self):
        res, mf, tg = self._run({"uf_unreplicated": 2, "pending_online_stale": 3, "email_queue_error": 0})
        kw = mf.sendmail.call_args[1]
        self.assertEqual(kw["recipients"], ["ops@lanem.bj", "dir@lanem.bj"])
        self.assertIn("uf_unreplicated: 2", kw["message"].replace("<br>", "\n"))
        # seuils : uf_unreplicated (seuil 0) dépassé → signalé ; pending_online_stale (3 ≤ seuil 5) → non
        self.assertIn("1 point", kw["subject"])
        tg.assert_called_once()                               # copie Telegram (angle mort SMTP couvert)
        self.assertEqual(res["alerts"], 1)
        self.assertTrue(res["mailed"] and res["telegramed"])

    def test_digest_all_zero_is_short_proof_of_life(self):
        res, mf, tg = self._run({"uf_unreplicated": 0, "email_queue_error": 0})
        self.assertIn("RAS", mf.sendmail.call_args[1]["subject"])
        self.assertEqual(res["alerts"], 0)

    def test_digest_no_recipients_skips_mail_but_telegram_still(self):
        res, mf, tg = self._run({"uf_unreplicated": 0},
                                conf={"admission_telegram_bot_token": TOKEN,
                                      "admission_telegram_chat_id": "-10042"})
        mf.sendmail.assert_not_called()
        tg.assert_called_once()
        self.assertFalse(res["mailed"])

    def test_digest_mail_failure_is_silent_telegram_still_sent(self):
        counters = {"uf_unreplicated": 1}
        with patch(f"{A}.frappe") as mf, \
             patch(f"{A}._send_telegram", return_value=True) as tg, \
             patch(f"{O}._ops_counters", return_value=counters):
            _mock_frappe(mf, conf={"admission_telegram_bot_token": TOKEN,
                                   "admission_telegram_chat_id": "-10042",
                                   "admission_ops_digest_recipients": "ops@lanem.bj"})
            mf.sendmail.side_effect = Exception("SMTP down")
            from admission.api.alerting import send_daily_digest
            res = send_daily_digest()                         # pas de levée
            self.assertFalse(res["mailed"])
            tg.assert_called_once()                           # le canal Telegram couvre la panne mail

    def test_digest_never_raises(self):
        with patch(f"{A}.frappe") as mf, \
             patch(f"{O}._ops_counters", side_effect=Exception("db down")):
            _mock_frappe(mf)
            from admission.api.alerting import send_daily_digest
            self.assertIsNone(send_daily_digest())            # silence défensif total

    def test_malformed_thresholds_do_not_kill_digest(self):
        # L2 : seuils = string (conf mal saisie) OU valeur null → le digest part quand même
        for bad in ("pas-un-objet", {"uf_unreplicated": None}):
            res, mf, tg = self._run({"uf_unreplicated": 3},
                                    conf={"admission_telegram_bot_token": TOKEN,
                                          "admission_telegram_chat_id": "-10042",
                                          "admission_ops_digest_recipients": "ops@lanem.bj",
                                          "admission_ops_thresholds": bad})
            mf.sendmail.assert_called_once()                  # mail bien parti (pas de crash silencieux)
            self.assertEqual(res["alerts"], 1)                # seuil retombé à 0 → 3>0 signalé
