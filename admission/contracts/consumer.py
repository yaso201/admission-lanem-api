"""CONTRAT-1 — Côté CONSOMMATEUR : le front dépend-il de ce que le contrat garantit ?

Deux vérifications, indépendantes du dépôt front (utilisables aussi par les tests JS via le même
JSON) :
  - `path_in_data_schema` : le chemin `a.b.c` que le front LIT existe-t-il dans le data-schéma ?
    Un déballage en trop (E-02 : `data.data.total`) échoue ici.
  - `stringified_null_keys` : un payload de REQUÊTE contient-il une valeur « null »/« undefined »
    sérialisée en chaîne ? (CAL-10 : paramètre envoyé `"null"` au lieu d'absent/null.)
"""


def path_in_data_schema(data_schema, dotted_path):
    node = data_schema
    for part in dotted_path.split("."):
        if not isinstance(node, dict):
            return False
        props = node.get("properties", {})
        if part not in props:
            return False
        node = props[part]
    return True


def stringified_null_keys(params):
    """Clés dont la valeur est un null/undefined SÉRIALISÉ en chaîne (bug de contrat requête)."""
    bad = {"null", "undefined", "none", "nan"}
    return [k for k, v in params.items() if isinstance(v, str) and v.strip().lower() in bad]
