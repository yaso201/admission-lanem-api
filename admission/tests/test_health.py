"""OBS-1 — bilan de santé réel (503 si dégradé) + ingestion durcie des erreurs front.

Style mocké : on patche `health.frappe` + les envelopes `_error`/`_ok` + `log_event`, comme test_etude.
La preuve E2E (capture front sur bundle réel) + rate-limit réel sont jouées en Phase 4.
"""

from unittest import TestCase
from unittest.mock import MagicMock, patch

H = "admission.api.health"


def _envelopes():
    return (
        patch(f"{H}._ok", side_effect=lambda d=None: {"ok": True, "data": d, "error": None}),
        patch(f"{H}._error", side_effect=lambda c, m, s=400: {"ok": False, "data": None, "error": {"code": c, "http": s}}),
    )


class TestHealthCheck(TestCase):
    def _run(self, *, db_ok=True, catalog=(5, 3), conf=lambda k: "x", tz="Africa/Porto-Novo"):
        with patch(f"{H}.frappe") as mf:
            mf.local.response = {}
            if db_ok:
                mf.db.sql.return_value = [(1,)]
            else:
                mf.db.sql.side_effect = Exception("db down")
            mf.db.count.side_effect = lambda dt: {"Admission Fee Catalog": catalog[0], "Admission Level Mirror": catalog[1]}.get(dt, 0)
            mf.conf.get.side_effect = conf
            mf.db.get_single_value.return_value = tz
            from admission.api.health import check
            res = check()
            return res, mf.local.response

    def test_healthy_all_deps_ok(self):
        res, response = self._run()
        self.assertEqual(res["status"], "healthy")
        self.assertTrue(res["ok"])
        self.assertNotIn("http_status_code", response)          # 200 par défaut
        self.assertTrue(all(c["ok"] for c in res["checks"].values()))

    def test_degraded_config_missing_returns_503(self):
        # une clé de config critique manquante → dégradé + HTTP 503 (casse le « 200 trompeur »)
        res, response = self._run(conf=lambda k: None if k == "campus_base_url" else "x")
        self.assertEqual(res["status"], "degraded")
        self.assertFalse(res["ok"])
        self.assertEqual(response["http_status_code"], 503)
        self.assertFalse(res["checks"]["config"]["ok"])
        self.assertIn("campus_base_url", res["checks"]["config"]["detail"])

    def test_uf_absent_is_pending_not_critical(self):
        # uf_backoffice_url ABSENT = normal tant qu'UF n'est pas en recette → SAIN (200),
        # mais visible dans le détail (« en attente »). Rebascule critique à l'arrivée d'UF.
        res, response = self._run(conf=lambda k: None if k == "uf_backoffice_url" else "x")
        self.assertEqual(res["status"], "healthy")
        self.assertTrue(res["ok"])
        self.assertNotIn("http_status_code", response)
        self.assertTrue(res["checks"]["config"]["ok"])
        self.assertIn("uf_backoffice_url", res["checks"]["config"]["detail"])   # visibilité conservée

    def test_degraded_catalog_empty_returns_503(self):
        res, response = self._run(catalog=(0, 0))
        self.assertEqual(res["status"], "degraded")
        self.assertEqual(response["http_status_code"], 503)
        self.assertFalse(res["checks"]["catalog"]["ok"])

    def test_degraded_db_down_returns_503(self):
        res, response = self._run(db_ok=False)
        self.assertEqual(response["http_status_code"], 503)
        self.assertFalse(res["checks"]["db"]["ok"])

    def test_degraded_timezone_drift_returns_503(self):
        res, response = self._run(tz="Asia/Kolkata")
        self.assertEqual(res["status"], "degraded")
        self.assertFalse(res["checks"]["timezone"]["ok"])

    def test_config_detail_never_exposes_secret_value(self):
        # présence booléenne seulement : le détail ne contient jamais la valeur d'un secret
        res, _ = self._run(conf=lambda k: "super-secret-value")
        self.assertNotIn("super-secret-value", str(res["checks"]))


class TestLogClientError(TestCase):
    def _post(self, raw_bytes):
        ok_p, err_p = _envelopes()
        captured = {}
        with patch(f"{H}.frappe") as mf, ok_p, err_p, \
             patch(f"{H}.log_event", side_effect=lambda *a, **k: captured.update(step=a[0], status=a[1], fields=k)):
            mf.request.data = raw_bytes
            from admission.api.health import log_client_error
            res = log_client_error()
            return res, captured

    def test_oversized_payload_rejected_413(self):
        res, captured = self._post(b"x" * 3000)
        self.assertEqual(res["error"]["code"], "PAYLOAD_TOO_LARGE")
        self.assertEqual(res["error"]["http"], 413)
        self.assertEqual(captured, {})                          # rien journalisé sur rejet

    def test_normal_error_logged_with_correlation_step(self):
        import json as _j
        res, captured = self._post(_j.dumps({
            "front": "applicant", "page": "/suivi", "message": "x is undefined",
            "source": "tunnel.js", "line": 42, "col": 7, "ua": "Mozilla/5.0",
        }).encode())
        self.assertTrue(res["ok"])
        self.assertEqual(captured["step"], "client_error")
        self.assertEqual(captured["fields"]["front"], "applicant")
        self.assertEqual(captured["fields"]["page"], "/suivi")
        self.assertEqual(captured["fields"]["message"], "x is undefined")
        self.assertEqual(captured["fields"]["level"], "error")

    def test_fields_truncated(self):
        import json as _j
        res, captured = self._post(_j.dumps({"front": "management", "message": "M" * 1000}).encode())
        self.assertTrue(res["ok"])
        self.assertEqual(len(captured["fields"]["message"]), 500)   # tronqué au cap

    def test_no_pii_field_leaks_through_allowlist(self):
        # un token/contenu form envoyé par erreur n'est JAMAIS journalisé (allowlist stricte)
        import json as _j
        res, captured = self._post(_j.dumps({
            "front": "applicant", "message": "err", "token": "SECRET-TOKEN", "email": "a@b.c",
        }).encode())
        self.assertNotIn("token", captured["fields"])
        self.assertNotIn("email", captured["fields"])
        self.assertNotIn("SECRET-TOKEN", str(captured["fields"]))

    def test_malformed_json_does_not_crash(self):
        res, captured = self._post(b"{not json")
        self.assertTrue(res["ok"])                              # défensif : journalise des champs vides, pas de 500
        self.assertEqual(captured["fields"]["front"], "?")


class TestRateLimitDecorator(TestCase):
    def test_log_client_error_is_rate_limited(self):
        # GO4 : la protection débit est réellement posée sur l'endpoint public.
        # frappe.rate_limiter.rate_limit enrobe via @wraps → __wrapped__ pointe la fn brute ;
        # frappe.whitelist ne réenrobe pas (simple enregistrement). Donc l'absence de
        # __wrapped__ == décorateur @rate_limit retiré → ce test tombe (garde structurelle réelle).
        from admission.api import health
        self.assertTrue(hasattr(health.log_client_error, "__wrapped__"),
                        "@rate_limit doit rester posé sur log_client_error (endpoint public)")
        self.assertEqual(health.log_client_error.__wrapped__.__name__, "log_client_error")
