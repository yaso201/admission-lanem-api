"""LOT C (SM BACK-OFFICE) — réglages NON-SECRETS & diagnostic.

DÉCISION D1 : AUCUN secret n'est lu ni écrit ici. Les secrets (`site_config` : tokens campus/UF,
clés KkiaPay, hmac, webhook, SMTP) restent fichier/OPS. Cette interface n'expose que :
 - un DIAGNOSTIC présent/absent (booléens, JAMAIS la valeur) — réutilise la logique recette_check ;
 - des réglages NON-SECRETS (cloisonnement de consultation, politique de rétention).

Réf : SPEC-ADMISSION-SM-BACKOFFICE §4 (C).
"""

import json

import frappe

from admission.api._log import log_event
from admission.api.public import _error, _ok

SM_ROLES = ("Admission SM", "System Manager")

# Réglages NON-SECRETS éditables (liste blanche stricte — tout le reste est refusé).
SETTINGS_WHITELIST = {"consultation_cloisonnee", "consultation_axis", "consultation_role_scopes"}
RETENTION_WHITELIST = {"abandoned_bro_days", "ref_des_retention_days",
                       "post_ins_retention_days", "log_retention_days"}


def _present(key):
    return bool(frappe.conf.get(key))


@frappe.whitelist(methods=["GET"])
def get_config_health():
    """Diagnostic présent/absent des intégrations — JAMAIS de valeur de secret (D1)."""
    frappe.only_for(SM_ROLES)
    kkiapay_keys = all(_present(k) for k in
                       ("kkiapay_public_key", "kkiapay_private_key", "kkiapay_secret_key"))
    if frappe.conf.get("kkiapay_mock"):
        kkiapay_mode = "MOCK"
    elif frappe.conf.get("kkiapay_sandbox"):
        kkiapay_mode = "SANDBOX"
    else:
        kkiapay_mode = "LIVE"
    smtp_present = bool(frappe.db.count("Email Account", {"default_outgoing": 1}))
    return _ok({
        "campus": {"present": _present("campus_base_url") and _present("campus_api_token")},
        "uf": {"present": _present("uf_backoffice_url") and _present("uf_api_key")
               and _present("uf_api_secret")},
        "kkiapay": {"present": kkiapay_keys, "mode": kkiapay_mode},
        "hmac_secret": {"present": _present("token_hmac_secret")},
        "webhook_secret": {"present": _present("admission_payment_webhook_secret")},
        "smtp": {"present": smtp_present},
        # drapeaux d'environnement (booléens, non secrets) — utiles au SM avant une bascule.
        "flags": {
            "developer_mode": bool(frappe.conf.get("developer_mode")),
            "expose_dev_otp": bool(frappe.conf.get("expose_dev_otp")),
            "kkiapay_mock": bool(frappe.conf.get("kkiapay_mock")),
        },
    })


@frappe.whitelist(methods=["GET"])
def get_settings():
    """Réglages NON-SECRETS courants (cloisonnement + rétention). Lecture, gardée SM."""
    frappe.only_for(SM_ROLES)
    s = frappe.get_single("Admission Settings")
    raw = s.get("consultation_role_scopes")
    try:
        scopes = json.loads(raw) if isinstance(raw, str) and raw else (raw or {})
    except (ValueError, TypeError):
        scopes = {}
    retention = {k: int(frappe.db.get_single_value("Admission Retention Policy", k) or 0)
                 for k in RETENTION_WHITELIST}
    return _ok({
        "cloisonnement": {
            "consultation_cloisonnee": bool(s.get("consultation_cloisonnee")),
            "consultation_axis": s.get("consultation_axis") or "status",
            "consultation_role_scopes": scopes,
        },
        "retention": retention,
    })


@frappe.whitelist()
def update_settings(cloisonnement=None, retention=None):
    """Met à jour des réglages NON-SECRETS. Tout champ hors liste blanche est REFUSÉ (D1)."""
    frappe.only_for(SM_ROLES)

    def _parse(obj):
        if isinstance(obj, str):
            try:
                return json.loads(obj)
            except (ValueError, TypeError):
                return None
        return obj

    cloisonnement = _parse(cloisonnement) or {}
    retention = _parse(retention) or {}
    if not isinstance(cloisonnement, dict) or not isinstance(retention, dict):
        return _error("PAYLOAD_INVALID", "Format invalide (objets attendus).", 400)

    illegal = ([k for k in cloisonnement if k not in SETTINGS_WHITELIST]
               + [k for k in retention if k not in RETENTION_WHITELIST])
    if illegal:
        return _error("FIELD_NOT_ALLOWED",
                      f"Champs non modifiables ici (secret ou hors périmètre) : {', '.join(illegal)}.", 400)

    changed = []
    # db.set_value sur les singles : contourne les contrôleurs (notamment la rotation RIB
    # d'Admission Settings.on_update) — on ne touche QUE des champs non-secrets.
    if cloisonnement:
        values = {}
        for k, v in cloisonnement.items():
            if k == "consultation_role_scopes" and not isinstance(v, str):
                v = json.dumps(v or {})
            values[k] = v
            changed.append(k)
        frappe.db.set_value("Admission Settings", "Admission Settings", values)
    if retention:
        values = {k: int(v) for k, v in retention.items()}
        frappe.db.set_value("Admission Retention Policy", "Admission Retention Policy", values)
        changed.extend(retention.keys())

    log_event("admin_update_settings", "success", fields=",".join(sorted(changed)))
    return _ok({"updated": sorted(changed)})
