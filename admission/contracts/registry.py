"""CONTRAT-1 — Chargement des schémas + composition de l'enveloppe.

Chaque fichier `schemas/<endpoint>.json` décrit la forme du **`data`** (ce qu'il y a DANS l'enveloppe).
L'enveloppe `{ok, data, error}` est ajoutée ici, en un seul endroit — c'est elle-même une partie du
contrat machine, définie une fois. `required` d'un data-schéma = les champs CONSOMMÉS par le front
(source : AUDIT-360-A3 §2.1). Métadonnées annotées dans le fichier : `$id`, `x-source`, `x-level`,
`x-consumers` (ignorées par le validateur, qui ne lit que type/required/properties/...).
"""
import json
import os

SCHEMAS_DIR = os.path.join(os.path.dirname(__file__), "schemas")


def load_data_schema(schema_id):
    with open(os.path.join(SCHEMAS_DIR, schema_id + ".json"), encoding="utf-8") as f:
        return json.load(f)


def list_schema_ids():
    return sorted(f[:-5] for f in os.listdir(SCHEMAS_DIR) if f.endswith(".json"))


def envelope_schema(data_schema):
    """Enveloppe de SUCCÈS : ok=true, data conforme au schéma, error=null."""
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["ok", "data", "error"],
        "properties": {
            "ok": {"type": "boolean", "enum": [True]},
            "error": {"type": "null"},
            "data": data_schema,
        },
    }
