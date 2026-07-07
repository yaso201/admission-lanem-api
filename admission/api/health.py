"""OBS-1 — santé réelle + ingestion des erreurs client (ferme l'angle mort A « santé invisible »).

- `check()` : bilan de santé RÉEL (DB joignable, catalogues présents, config critique posée, fuseau) →
  `healthy`/`degraded` + **HTTP 503 si dégradé**. Casse le « 200 trompeur » (un 200 par défaut ne prouve
  pas que l'app fonctionne). Public, sondes LÉGÈRES, **0 valeur secrète** (présence booléenne seulement).
- `log_client_error()` : ingestion des erreurs JS des fronts (`window.onerror`/`unhandledrejection`) →
  `log_event`. Endpoint public DURCI : guest + `rate_limit` 30/h/IP + payload BORNÉ + allowlist tronquée
  + **0 PII** (jamais token/contenu de formulaire ; `page` = pathname fourni par le client).

Reste dans `admission` (graph acyclique — aucune nouvelle arête).
"""

import json

import frappe
from frappe.rate_limiter import rate_limit

from admission.api._log import log_event
from admission.api.public import _error, _ok

_HEALTH_TZ = "Africa/Porto-Novo"
_CRITICAL_CONF = (
    "campus_base_url",
    "candidate_portal_url",
    "admission_payment_webhook_secret",
)
# En attente : absent = NORMAL tant que la dépendance n'est pas en recette (visible dans le
# détail, sans dégrader). ⚠️ Rebasculer dans _CRITICAL_CONF quand UF arrivera en recette.
_PENDING_CONF = ("uf_backoffice_url",)
_CLIENT_ERR_MAX_BYTES = 2048
_CAPS = {"message": 500, "source": 200, "page": 200, "ua": 200, "front": 24}


# ── sondes santé (légères) ────────────────────────────────────────────────────

def _probe_db():
    try:
        frappe.db.sql("SELECT 1")
        return True, "joignable"
    except Exception:
        return False, "injoignable"


def _probe_catalog():
    """Catalogues synchronisés = au moins un frais + un niveau miroir (count borné, léger)."""
    try:
        fees = frappe.db.count("Admission Fee Catalog")
        levels = frappe.db.count("Admission Level Mirror")
        return (fees > 0 and levels > 0), f"fees={fees} levels={levels}"
    except Exception:
        return False, "lecture catalogue impossible"


def _probe_config():
    """Config critique POSÉE — noms de clés manquantes seulement, JAMAIS les valeurs (0 secret).
    Les clés « en attente » (dépendance pas encore en recette) restent VISIBLES sans dégrader."""
    missing = [k for k in _CRITICAL_CONF if not frappe.conf.get(k)]
    pending = [k for k in _PENDING_CONF if not frappe.conf.get(k)]
    detail = "complète" if not missing else "manquant: " + ",".join(missing)
    if pending:
        detail += " (en attente: " + ",".join(pending) + ")"
    return (not missing), detail


def _probe_timezone():
    """Anti-dérive (incident Kolkata) : le fuseau doit être celui du Bénin."""
    tz = frappe.db.get_single_value("System Settings", "time_zone") or ""
    return (tz == _HEALTH_TZ), (tz or "non défini")


_PROBES = (("db", _probe_db), ("catalog", _probe_catalog),
           ("config", _probe_config), ("timezone", _probe_timezone))


@frappe.whitelist(allow_guest=True, methods=["GET"])
def check():
    """Bilan de santé RÉEL. `healthy` seulement si TOUTES les vraies dépendances répondent ; sinon
    `degraded` + **HTTP 503** (détectable par un uptime bête — le 200 par défaut ne prouve rien)."""
    checks = {}
    for name, probe in _PROBES:
        try:
            ok, detail = probe()
        except Exception:
            ok, detail = False, "sonde en erreur"
        checks[name] = {"ok": bool(ok), "detail": detail}
    healthy = all(c["ok"] for c in checks.values())
    if not healthy:
        frappe.local.response["http_status_code"] = 503
    return {"ok": healthy, "status": "healthy" if healthy else "degraded", "checks": checks}


# ── ingestion erreurs front (endpoint public durci) ───────────────────────────

def _cap_int(v):
    try:
        return int(v)
    except (ValueError, TypeError):
        return None


@frappe.whitelist(allow_guest=True, methods=["POST"])
@rate_limit(limit=30, seconds=60 * 60)
def log_client_error():
    """Ingestion d'une erreur JS front → `log_event`. DURCI : `rate_limit` 30/h/IP (frappe `request_ip` =
    1re entrée `X-Forwarded-For` = vraie IP client via Cloudflare, donc throttle PAR CLIENT, pas collectif),
    payload BORNÉ (≤ 2 Ko), allowlist tronquée, **0 PII** (jamais token/contenu form ; `page` = pathname)."""
    raw = getattr(getattr(frappe, "request", None), "data", None) or b""
    try:
        too_large = len(raw) > _CLIENT_ERR_MAX_BYTES
    except TypeError:
        too_large = False
    if too_large:
        return _error("PAYLOAD_TOO_LARGE", "Payload trop volumineux.", 413)
    try:
        data = json.loads(raw) if raw else {}
    except (ValueError, TypeError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    # allowlist STRICTE + troncature — aucun autre champ n'est journalisé (pas de token/form/PII).
    log_event(
        "client_error", "reported", level="error",
        front=str(data.get("front") or "?")[:_CAPS["front"]],
        page=str(data.get("page") or "")[:_CAPS["page"]],
        message=str(data.get("message") or "")[:_CAPS["message"]],
        source=str(data.get("source") or "")[:_CAPS["source"]],
        line=_cap_int(data.get("line")),
        col=_cap_int(data.get("col")),
        ua=str(data.get("ua") or "")[:_CAPS["ua"]],
    )
    # OBS-2 : compteur de pic — le SEUIL franchi déclenche UNE alerte (pas une par erreur).
    # Import paresseux + try/except : l'endpoint public ne peut jamais 500 à cause de l'alerte.
    try:
        from admission.api.alerting import note_client_error
        note_client_error()
    except Exception:
        pass
    return _ok({"logged": True})
