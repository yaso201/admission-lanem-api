"""Tests LOT-A2 — ADM-DEBT-09 (OTP HMAC, fail-loud, token 32B intact) + ADM-DEBT-25
(champ source/canal sur Applicant Fee Payment, posé aux 3 canaux, traçabilité seule).

Style unitaire mocké, aligné suite existante.
"""

import hashlib
import hmac as hmac_mod
import json
import os
from unittest import TestCase
from unittest.mock import MagicMock, patch

PUBLIC = "admission.api.public"
STAFF = "admission.api.staff"


# ── ADM-DEBT-09 : OTP en HMAC ────────────────────────────────────────────────


class TestOtpHmac(TestCase):
    def test_otp_hash_is_hmac_with_server_secret(self):
        with patch(f"{PUBLIC}.frappe") as mf:
            mf.conf.get.return_value = "s3cret-serveur"
            from admission.api.public import _hash_otp
            out = _hash_otp("123456")
        expected = hmac_mod.new(b"s3cret-serveur", b"123456", hashlib.sha256).hexdigest()
        self.assertEqual(out, expected)
        # et ce n'est PLUS le SHA256 nu (rainbow-table-able sur 10^6 codes)
        self.assertNotEqual(out, hashlib.sha256(b"123456").hexdigest())

    def test_fail_loud_without_secret(self):
        # Esprit ADM-DEBT-07 : secret absent → throw, JAMAIS de repli SHA256 silencieux
        with patch(f"{PUBLIC}.frappe") as mf:
            mf.conf.get.return_value = None
            mf.throw.side_effect = RuntimeError("token_hmac_secret absent")
            from admission.api.public import _hash_otp
            with self.assertRaises(RuntimeError):
                _hash_otp("123456")
            mf.throw.assert_called_once()

    def test_token_hash_stays_plain_sha256(self):
        # ACTÉ : le token de dossier (32 bytes d'entropie) reste en SHA256 nu
        from admission.api.public import _hash
        self.assertEqual(_hash("tok"), hashlib.sha256(b"tok").hexdigest())

    def test_otp_call_sites_use_hmac(self):
        # request_otp (pose) et verify_otp (compare) passent par _hash_otp ; le token par _hash
        import inspect
        from admission.api import public
        src_req = inspect.getsource(public.request_otp)
        src_ver = inspect.getsource(public.verify_otp)
        self.assertEqual(src_req.count("_hash_otp("), 2)   # email + phone
        self.assertEqual(src_ver.count("_hash_otp("), 2)   # comparaison email + phone
        self.assertIn("_hash(new_token)", src_ver)          # rotation token : SHA256 intact


# ── ADM-DEBT-25 : champ source/canal ─────────────────────────────────────────


class TestSourceField(TestCase):
    def setUp(self):
        jf = os.path.join(os.path.dirname(__file__), "..", "admission", "doctype",
                          "applicant_fee_payment", "applicant_fee_payment.json")
        self.fields = {f["fieldname"]: f for f in json.load(open(jf))["fields"]}

    def test_source_select_exists_read_only(self):
        f = self.fields.get("source")
        self.assertIsNotNone(f, "champ source absent")
        self.assertEqual(f["fieldtype"], "Select")
        self.assertEqual(f.get("read_only"), 1)
        opts = (f.get("options") or "").split("\n")
        for v in ("espece", "banque", "online"):
            self.assertIn(v, opts)


class TestSourceWriters(TestCase):
    """Les écrivains posent le canal — vérification par source (les inserts sont des dicts
    littéraux ; le comportement runtime est prouvé en preuve Phase 3)."""

    def _src(self, module, fn):
        import inspect
        return inspect.getsource(getattr(module, fn))

    def test_declare_offline_sets_espece_banque(self):
        from admission.api import public
        src = self._src(public, "declare_payment_offline")
        self.assertIn('"source": "espece" if', src)

    def test_declare_enrollment_offline_sets_espece_banque(self):
        from admission.api import public
        src = self._src(public, "declare_enrollment_payment_offline")
        self.assertIn('"source": "espece" if', src)

    def test_prepare_online_sets_online(self):
        from admission.api import public
        src = self._src(public, "prepare_online_payment")
        self.assertIn('"source": "online"', src)

    def test_webhook_has_no_insert_fallback(self):
        # LOT KKIAPAY : promotion UNIQUEMENT — un insert dans le webhook serait une
        # régression (double-paiement A2 / contournement de l'initiation W3).
        from admission.api import webhook
        src = self._src(webhook, "payment")
        self.assertNotIn(".insert(", src)
        self.assertIn("PAYMENT_NOT_INITIATED", src)

    def test_confirm_agent_aligns_source_on_final_mode(self):
        from admission.api import staff
        src = self._src(staff, "confirm_offline_payment")
        self.assertIn('payment.source = "espece" if payment.payment_mode == "Cash" else "banque"', src)
