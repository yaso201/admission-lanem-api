"""CONTRAT-1 — Le validateur de contrat est LUI-MÊME testé.

Garde de l'architecte : « un validateur faux qui valide tout est pire que pas de validateur ».
Ces tests prouvent que le validateur ROUGIT sur chaque famille de violation (pas seulement qu'il
accepte le valide). unittest.TestCase pur — aucune DB, aucun fixture.
"""
import unittest

from admission.contracts.validator import validate

ENVELOPE = {
    "type": "object",
    "additionalProperties": False,
    "required": ["ok", "data", "error"],
    "properties": {
        "ok": {"type": "boolean"},
        "data": {"type": "object", "additionalProperties": True},
        "error": {"type": ["object", "null"]},
    },
}


class TestValidatorAcceptsValid(unittest.TestCase):
    def test_valid_envelope_zero_error(self):
        self.assertEqual(validate({"ok": True, "data": {}, "error": None}, ENVELOPE), [])

    def test_nullable_allows_null(self):
        self.assertEqual(validate(None, {"type": ["string", "null"]}), [])

    def test_number_and_integer(self):
        self.assertEqual(validate(3, {"type": "integer"}), [])
        self.assertEqual(validate(3.5, {"type": "number"}), [])


class TestValidatorCatchesViolations(unittest.TestCase):
    """Chaque cas DOIT produire au moins une erreur — sinon le validateur est faux."""

    def test_missing_required_field(self):
        errs = validate({"ok": True, "data": {}}, ENVELOPE)  # error absent
        self.assertTrue(any("error" in e and "requis" in e for e in errs), errs)

    def test_wrong_type(self):
        errs = validate({"ok": "oui", "data": {}, "error": None}, ENVELOPE)
        self.assertTrue(any(".ok" in e and "type" in e for e in errs), errs)

    def test_additional_property_rejected(self):
        errs = validate({"ok": True, "data": {}, "error": None, "extra": 1}, ENVELOPE)
        self.assertTrue(any("non documentés" in e for e in errs), errs)

    def test_null_where_not_nullable(self):
        errs = validate(None, {"type": "string"})
        self.assertTrue(any("null interdit" in e for e in errs), errs)

    def test_enum_violation(self):
        errs = validate("MAYBE", {"type": "string", "enum": ["YES", "NO"]})
        self.assertTrue(any("hors enum" in e for e in errs), errs)

    def test_bool_is_not_integer(self):
        # piège Python : True == 1 ; le validateur doit refuser bool là où integer est attendu.
        errs = validate(True, {"type": "integer"})
        self.assertTrue(errs, "bool accepté comme integer — validateur faux")

    def test_array_items_validated(self):
        schema = {"type": "array", "items": {"type": "object", "required": ["id"],
                                             "properties": {"id": {"type": "string"}}}}
        errs = validate([{"id": "A"}, {"nope": 1}], schema)
        self.assertTrue(any("[1].id" in e for e in errs), errs)

    def test_nested_required(self):
        schema = {"type": "object", "properties": {"data": {"type": "object",
                  "required": ["x"], "properties": {"x": {"type": "string"}}}}}
        errs = validate({"data": {}}, schema)
        self.assertTrue(any("data.x" in e for e in errs), errs)


class TestValidatorRefusesUnsupported(unittest.TestCase):
    """Le validateur ne doit JAMAIS valider en silence un schéma qu'il ne comprend pas."""

    def test_ref_refused(self):
        errs = validate({"any": 1}, {"$ref": "#/defs/x"})
        self.assertTrue(any("non supportés" in e for e in errs), errs)

    def test_anyof_refused(self):
        errs = validate(1, {"anyOf": [{"type": "integer"}]})
        self.assertTrue(any("non supportés" in e for e in errs), errs)
