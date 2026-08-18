"""CONTRAT-1 — Validateur JSON Schema minimal et AUTONOME (zéro dépendance).

Pourquoi écrit à la main plutôt que `jsonschema` ? `jsonschema` n'est pas dans l'env du bench
(vérifié : ModuleNotFoundError). Le vendorer ajouterait une dépendance à maintenir pour une fraction
de sa surface. Ce validateur couvre EXACTEMENT le sous-ensemble utilisé par les schémas de contrat —
et rien de plus (garde de l'architecte : « volontairement minimal »).

Sous-ensemble supporté (Draft-07 partiel) :
  - type : object | array | string | number | integer | boolean | null (+ liste de types → union)
  - required : liste de clés obligatoires sur un objet
  - properties : schéma par clé
  - additionalProperties : bool (false = aucune clé hors `properties` tolérée)
  - items : schéma des éléments d'un array
  - enum : valeurs autorisées
  - nullable : true → la valeur peut être None en plus de `type`

NON supporté volontairement : $ref, allOf/anyOf/oneOf, if/then, pattern, format, dependencies.
Un schéma qui en aurait besoin doit être signalé, pas contourné en silence.

Le validateur est lui-même TESTÉ (test_contract_validator.py) : un validateur faux qui valide tout
serait pire que pas de validateur.
"""

_JSON_TYPES = {
    "object": dict,
    "array": list,
    "string": str,
    "boolean": bool,
    "null": type(None),
    # number/integer traités à part (bool est un int en Python → à exclure)
}


def _type_ok(value, t):
    if t == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if t == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    py = _JSON_TYPES.get(t)
    if py is None:
        return False
    if t == "object":
        return isinstance(value, dict)
    return isinstance(value, py)


def validate(instance, schema, path="$"):
    """Renvoie la liste des erreurs (chemin + message). Liste vide = conforme."""
    errors = []
    _validate(instance, schema, path, errors)
    return errors


def _validate(instance, schema, path, errors):
    if not isinstance(schema, dict):
        errors.append(f"{path}: schéma invalide (doit être un objet)")
        return

    # Détection d'un mot-clé non supporté → on refuse de valider en silence.
    unsupported = {"$ref", "allOf", "anyOf", "oneOf", "if", "then", "else",
                   "pattern", "dependencies", "patternProperties"} & set(schema)
    if unsupported:
        errors.append(f"{path}: mots-clés non supportés par le validateur minimal : {sorted(unsupported)}")
        return

    types = schema.get("type")
    if types is not None:
        types = [types] if isinstance(types, str) else list(types)
        allow_null = schema.get("nullable") is True or "null" in types
        if instance is None:
            if not allow_null:
                errors.append(f"{path}: null interdit (type attendu {types})")
            return
        if not any(_type_ok(instance, t) for t in types if t != "null"):
            errors.append(f"{path}: type {type(instance).__name__} ≠ {types}")
            return

    if "enum" in schema and instance not in schema["enum"]:
        errors.append(f"{path}: valeur {instance!r} hors enum {schema['enum']}")

    if isinstance(instance, dict):
        props = schema.get("properties", {})
        for key in schema.get("required", []):
            if key not in instance:
                errors.append(f"{path}.{key}: champ requis absent")
        if schema.get("additionalProperties") is False:
            extra = set(instance) - set(props)
            if extra:
                errors.append(f"{path}: champs non documentés au contrat : {sorted(extra)}")
        for key, val in instance.items():
            if key in props:
                _validate(val, props[key], f"{path}.{key}", errors)

    if isinstance(instance, list) and "items" in schema:
        for i, el in enumerate(instance):
            _validate(el, schema["items"], f"{path}[{i}]", errors)
