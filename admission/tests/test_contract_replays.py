"""CONTRAT-1 — La preuve que le lot sert à quelque chose.

1) FALSIFIABILITÉ (≥3 endpoints) : sur une réponse RÉELLE, retirer chaque champ consommé (required)
   fait ROUGIR la validation. C'est la garantie de DEC-B, prouvée par mesure.
2) REJEU des 3 incidents historiques : le contrat aurait-il attrapé E-01, E-02, CAL-10 ? On valide
   la forme D'ORIGINE (buggée, reconstruite depuis l'audit) → ROUGE, et la forme corrigée → VERT.

Chaque test reste vert en ASSERTANT que la violation est bien détectée (le rouge est encapsulé).
La démonstration « test back réellement rouge » sur schéma cassé est faite en direct au rapport.
"""
from frappe.tests.utils import FrappeTestCase

from admission.api import admin_config, admin_ops, admin_referentiel, staff
from admission.contracts import registry
from admission.contracts.consumer import path_in_data_schema, stringified_null_keys
from admission.contracts.validator import validate


def _envelope(schema_id):
    return registry.envelope_schema(registry.load_data_schema(schema_id))


class TestFalsifiability(FrappeTestCase):
    """Retirer un champ consommé → le contrat rougit. Sur 3 endpoints réels (≥3 exigé)."""

    CASES = [
        ("admin_config.get_config_health", lambda: admin_config.get_config_health()),
        ("admin_ops.get_ops_health", lambda: admin_ops.get_ops_health()),
        ("staff.whoami", lambda: staff.whoami()),
        ("admin_referentiel.get_degraded_status", lambda: admin_referentiel.get_degraded_status()),
    ]

    def test_dropping_each_consumed_field_turns_red(self):
        for schema_id, call in self.CASES:
            data_schema = registry.load_data_schema(schema_id)
            envelope = registry.envelope_schema(data_schema)
            real = call()
            # baseline : la vraie réponse est VERTE
            self.assertEqual(validate(real, envelope), [], f"{schema_id} devrait être conforme")
            # falsifiabilité : retirer CHAQUE champ requis → au moins une erreur
            for field in data_schema.get("required", []):
                broken = {**real, "data": {k: v for k, v in real["data"].items() if k != field}}
                errs = validate(broken, envelope)
                self.assertTrue(errs, f"{schema_id}: retirer '{field}' aurait dû rougir, or vert")
                self.assertTrue(any(field in e for e in errs), f"{schema_id}: erreur ne cite pas '{field}': {errs}")


class TestReplayE01PhantomField(FrappeTestCase):
    """E-01 — get_config_health servait `kkiapay` ; le front lit `fedapay`. Le contrat rougit."""

    def test_old_kkiapay_shape_fails_current_contract(self):
        envelope = _envelope("admin_config.get_config_health")
        # Forme D'ORIGINE (avant LEGAL-HYGIENE) reconstruite depuis A3 §2.1 #1 : `kkiapay` au lieu de `fedapay`.
        old = {"ok": True, "error": None, "data": {
            "campus": {"present": True}, "uf": {"present": True},
            "kkiapay": {"present": True, "mode": "LIVE"},  # ← le fantôme
            "hmac_secret": {"present": True}, "webhook_secret": {"present": True},
            "smtp": {"present": True},
            "flags": {"developer_mode": False, "expose_dev_otp": False, "kkiapay_mock": False}}}
        errs = validate(old, envelope)
        self.assertTrue(errs, "E-01 : la forme kkiapay aurait dû rougir contre le contrat fedapay")
        self.assertTrue(any("fedapay" in e for e in errs), errs)  # fedapay requis, absent
        self.assertTrue(any("kkiapay" in e for e in errs), errs)  # kkiapay non documenté

    def test_current_fedapay_shape_is_green(self):
        # La vraie réponse courante (fedapay) est VERTE — le rejeu discrimine.
        self.assertEqual(validate(admin_config.get_config_health(), _envelope("admin_config.get_config_health")), [])


class TestReplayE02DoubleUnwrap(FrappeTestCase):
    """E-02 — le front déballait DEUX fois close_session (`data.data.total` → 0). Le contrat rougit."""

    def test_double_unwrap_path_is_not_in_contract(self):
        data_schema = registry.load_data_schema("staff.close_session")
        # Consommateur CORRIGÉ : lit data.total, data.bascules, data.can_execute
        for good in ("total", "bascules", "can_execute", "blocking_message"):
            self.assertTrue(path_in_data_schema(data_schema, good), f"{good} devrait exister")
        # Consommateur D'ORIGINE (double déballage) : lit un niveau `data.*` EN TROP
        for bad in ("data.total", "data.bascules"):
            self.assertFalse(path_in_data_schema(data_schema, bad),
                             f"E-02 : le chemin double-déballé '{bad}' ne doit PAS exister au contrat")


class TestReplayCAL10StringifiedNull(FrappeTestCase):
    """CAL-10 — un paramètre envoyé littéralement `"null"` (chaîne) au lieu d'absent/null."""

    def test_stringified_null_param_detected(self):
        buggy = {"academic_year": "null", "programme": "LIS"}      # forme d'origine
        clean = {"academic_year": "2025-2026", "programme": "LIS"}  # corrigée
        self.assertEqual(stringified_null_keys(buggy), ["academic_year"], "CAL-10 aurait dû être détecté")
        self.assertEqual(stringified_null_keys(clean), [], "un payload propre ne doit rien signaler")
