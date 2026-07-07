"""OBS-2 — alerte opérationnelle : Telegram temps réel (HIGH curé) + digest quotidien.

Deux canaux, deux rôles :
- `send_high_alert(type, ...)` : un message Telegram IMMÉDIAT quand une erreur grave
  survient (ensemble curé ~8 types, marqués via `log_event(alert_type=...)`). HIGH-only
  strict + cooldown par type = anti-volume sans reconstruire le dédup d'un SaaS.
- `send_daily_digest()` : job quotidien — bilan des compteurs d'exploitation
  (`admin_ops._ops_counters`, zéro recalcul) → mail aux responsables + copie Telegram
  (couvre l'angle mort auto-référentiel : si SMTP est en panne, le mail ne part pas).

INVARIANTS :
- N'importe JAMAIS `admission.api._log` et n'appelle JAMAIS `log_event` (anti-cycle ET
  anti-récursion alerte→log→alerte). Ses propres traces : `frappe.logger("admission")`.
- Fire-and-forget : AUCUNE fonction publique ne lève — un paiement ne doit jamais
  échouer parce que Telegram est down (même discipline que le handler front OBS-1).
- 0 PII : identifiants internes seulement (CAN-/REC-…) ; le jeton bot (site_config)
  n'apparaît JAMAIS dans les traces (messages d'échec génériques).

Config (site_config, jamais committée) : admission_telegram_bot_token ·
admission_telegram_chat_id · admission_ops_digest_recipients ·
admission_alert_cooldown_minutes · admission_ops_thresholds ·
admission_client_error_spike_threshold.
"""

import json

import frappe
import requests

_TG_TIMEOUT = 3           # vigilance latence : borne le pire cas inline (sites webhook/hooks)
_TG_DIGEST_TIMEOUT = 10   # digest = job scheduler, latence indifférente
_COOLDOWN_MINUTES_DEFAULT = 10
_SPIKE_THRESHOLD_DEFAULT = 20
_SPIKE_WINDOW_SECONDS = 600

# Libellés neutres par type (vocabulaire descriptif — V-LEARN-CAMPUS-08/09).
_ALERT_LABELS = {
    "uf_payment": "Échec de notification UF (paiement)",
    "uf_abandon": "Échec de notification UF (abandon)",
    "kkiapay_verify": "Vérification KkiaPay impossible",
    "payment_orphan": "Paiement orphelin (remboursement dû)",
    "payment_refused_terminal": "Paiement sur dossier terminal (remboursement dû)",
    "payment_underpaid": "Paiement insuffisant (revue requise)",
    "bridge_inscription": "Échec du pont d'inscription campus",
    "uf_double_check": "Échec de réconciliation UF",
    "client_error_spike": "Pic d'erreurs front",
}


def _logger():
    return frappe.logger("admission")


def _conf_int(key, default):
    """Lecture robuste d'un entier site_config : une valeur malformée (typo, null, texte)
    RETOMBE sur le défaut au lieu de lever — sinon un seul typo éteindrait silencieusement
    le canal d'alerte ou le digest (revue OBS-2 L2/L3)."""
    try:
        v = frappe.conf.get(key)
        return int(v) if v is not None else default
    except (TypeError, ValueError):
        return default


def _trace(payload, *, level="info"):
    """Trace JSON même format que log_event — SANS l'appeler (anti-cycle)."""
    try:
        getattr(_logger(), level)(json.dumps(payload, ensure_ascii=False, default=str))
    except Exception:
        pass


def _send_telegram(text, *, timeout=_TG_TIMEOUT):
    """POST unique vers l'API Bot Telegram (pas de SDK). Jeton/chat_id en site_config ;
    absents → no-op silencieux. Ne lève JAMAIS ; le jeton n'apparaît jamais dans les traces."""
    token = frappe.conf.get("admission_telegram_bot_token")
    chat_id = frappe.conf.get("admission_telegram_chat_id")
    if not token or not chat_id:
        return False
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text},
            timeout=timeout,
        )
        return bool(r.ok)
    except Exception:
        # Générique VOLONTAIREMENT : jamais l'URL (elle contient le jeton), jamais la stack requests.
        # level=error : le logger frappe filtre info/warning par défaut (niveau 40) — un échec du
        # canal d'alerte doit rester VISIBLE dans le log.
        _logger().error("telegram send failed (non-blocking)")
        return False


def _cooldown_acquire(alert_type):
    """True si l'envoi est autorisé (et pose la fenêtre), False si en refroidissement.
    État en cache redis (patron rate_limiter) — au plus 1 message par type par fenêtre."""
    minutes = _conf_int("admission_alert_cooldown_minutes", _COOLDOWN_MINUTES_DEFAULT)
    key = frappe.cache.make_key(f"alertcd:{alert_type}")
    if frappe.cache.get(key):
        return False
    frappe.cache.setex(key, minutes * 60, 1)
    return True


def send_high_alert(alert_type, *, dossier_id=None, ref=None, detail=None):
    """Alerte temps réel (ensemble HIGH curé). Cooldown par type : une rafale (UF tombe →
    chaque paiement échoue) produit 1 message, pas N — la JOURNALISATION, elle, continue
    (chaque événement garde sa trace log_event au site d'appel). Ne lève jamais."""
    try:
        if not _cooldown_acquire(alert_type):
            _trace({"step": "high_alert", "status": "cooldown_suppressed", "alert_type": alert_type,
                    "dossier_id": dossier_id, "ref": ref})
            return False
        label = _ALERT_LABELS.get(alert_type, alert_type)
        site = getattr(frappe.local, "site", "") or ""
        lines = [f"⚠️ admission [{site}] — {label}", f"type: {alert_type}"]
        if dossier_id:
            lines.append(f"dossier: {dossier_id}")
        if ref:
            lines.append(f"réf: {ref}")
        if detail:
            lines.append(f"détail: {str(detail)[:200]}")
        sent = _send_telegram("\n".join(lines))
        # send_failed en ERROR (visible au niveau logger par défaut) ; sent en info (best-effort).
        _trace({"step": "high_alert", "status": "sent" if sent else "send_failed",
                "alert_type": alert_type, "dossier_id": dossier_id, "ref": ref},
               level="info" if sent else "error")
        return sent
    except Exception:
        _trace({"step": "high_alert", "status": "internal_error", "alert_type": alert_type},
               level="error")
        return False


def note_client_error():
    """Compteur fenêtré des erreurs front (OBS-1) : le seuil franchi déclenche UNE alerte
    (== strict + cooldown), pas une par erreur. Appelé par health.log_client_error ;
    ne lève jamais (endpoint public)."""
    try:
        threshold = _conf_int("admission_client_error_spike_threshold", _SPIKE_THRESHOLD_DEFAULT)
        key = frappe.cache.make_key("alertspike:client_error")
        # incrby-d'abord (atomique) : crée la clé à 1 si absente. On (RE)pose le TTL quand
        # count==1 → élimine la course get/incr qui pouvait laisser une clé SANS expiration
        # (fenêtre permanente = spike mort à jamais, revue OBS-2 L1 ; patron rate_limiter).
        count = frappe.cache.incrby(key, 1)
        if count == 1:
            frappe.cache.expire(key, _SPIKE_WINDOW_SECONDS)
        if count == threshold:
            send_high_alert("client_error_spike",
                            detail=f"{count} erreurs front en {_SPIKE_WINDOW_SECONDS // 60} min")
    except Exception:
        _trace({"step": "client_error_spike", "status": "internal_error"}, level="error")


def send_daily_digest():
    """Job quotidien (hooks.scheduler_events.daily) : bilan opérationnel — compteurs
    `_ops_counters` (réutilisés, zéro recalcul) mis en regard des seuils site_config →
    mail aux responsables + copie Telegram. Un bilan « tout à zéro » reste envoyé
    (court : preuve de vie). Ne lève jamais."""
    try:
        from admission.api.admin_ops import _ops_counters
        counters = _ops_counters()
        thresholds = frappe.conf.get("admission_ops_thresholds") or {}
        if not isinstance(thresholds, dict):   # conf mal saisie (string/liste) → seuils neutres, digest survit (L2)
            thresholds = {}
        lines, alerts = [], 0
        for key, value in counters.items():
            try:                               # un seuil malformé n'éteint pas TOUT le digest (L2/L3)
                seuil = int(thresholds.get(key, 0))
            except (TypeError, ValueError):
                seuil = 0
            over = value > seuil
            if over:
                alerts += 1
            lines.append(f"- {key}: {value}" + (f" ⚠️ (seuil {seuil})" if over else ""))
        # OBS-3 item 6 : ligne santé — rejoue les 4 sondes health (0 recalcul, import lazy
        # acyclique) → borne la dérive config à 24h MÊME sans uptime souscrit.
        try:
            from admission.api.health import _run_checks
            hz_ok, hz_checks = _run_checks()
            ko = [k for k, v in hz_checks.items() if not v.get("ok")]
            # précédence explicite (revue L1) : le suffixe « sondes ko » est indépendant du
            # verdict — robuste même si un jour healthy≠(ko vide).
            suffix = f" (sondes ko: {','.join(ko)})" if ko else ""
            lines.append("Santé: " + ("healthy" if hz_ok else "degraded") + suffix)
        except Exception:
            lines.append("Santé: indisponible")

        site = getattr(frappe.local, "site", "") or ""
        subject = (f"[admission {site}] Bilan opérationnel quotidien — "
                   + (f"{alerts} point(s) d'attention" if alerts else "RAS"))
        body = "\n".join(lines)

        recipients = frappe.conf.get("admission_ops_digest_recipients") or []
        if isinstance(recipients, str):
            recipients = [r.strip() for r in recipients.split(",") if r.strip()]
        mailed = False
        if recipients:
            try:
                frappe.sendmail(recipients=recipients, subject=subject,
                                message=body.replace("\n", "<br>"))
                mailed = True
            except Exception:
                _logger().error("ops digest mail failed (non-blocking)")
        telegramed = _send_telegram(subject + "\n" + body, timeout=_TG_DIGEST_TIMEOUT)

        _trace({"step": "ops_digest", "status": "sent", "mailed": mailed,
                "telegramed": telegramed, "alerts": alerts, **counters})
        return {"mailed": mailed, "telegramed": telegramed, "alerts": alerts, "counters": counters}
    except Exception:
        _trace({"step": "ops_digest", "status": "internal_error"}, level="error")
        return None
