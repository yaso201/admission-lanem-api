"""Pont admission→education + double-check admission→UF — ADM-UF-5a.

At INS transition, admission sends the FULL financial context to:
1. Campus/education (pont): triggers Student Applicant INS → cascade → Student Fee
2. UF (double-check): same context for reconciliation

Both are non-blocking (enqueued + retry=3 ; les échecs sont tracés en Error Log natif).
LOT P4 (AUDIT-MANAGEMENT-BACK #1) : le pont est désormais MARQUÉ sur le dossier
(bridge_notified / bridge_notified_at / bridge_last_error) et RE-DRIVÉ quotidiennement
(redrive_bridge_notifications, hooks daily) avec alerte System Manager sur reliquat —
un INS raté n'est plus un silence. Les notifications de PAIEMENT→UF gardent leur propre
re-drive (notify_uf.redrive_uf_notifications).

Financial context transported:
  { person_id, academic_year, program, academic_level,
    bourses_validees: [mirror_keys],
    promotion: {code, rate, captured_date},
    acompte: {present, montant_xof, encaisse: true} }

Ref: ADM-UF-5a, DEC-221 (UF calculates), §3bis-PROMO (locked rate).
"""

from __future__ import annotations

import json

import requests

import frappe

from admission.api._config import _get_campus_config, _get_uf_config, _pii_transport_allowed  # HELPERS-DEDUP + DAT-2 garde transport
from admission.api._log import log_event  # OBS-2 : log structuré + corrélation dossier_id


CAMPUS_INSCRIPTION_ENDPOINT = (
    "/api/method/portal_app.api.admission_bridge.receive_inscription"
)
UF_RECONCILIATION_ENDPOINT = (
    "/api/method/university_finance.api.admission_reconciliation"
    ".receive_financial_context"
)

# C3-ENROLL (ADM-DEBT-58, contrat DEC-260) : le statut MÉTIER est dans message.status —
# un HTTP 200 portant not_found/error est un ÉCHEC (avant : loggé succès, rien créé, silencieux).
BRIDGE_SUCCESS_STATUSES = {"ok", "already_ins", "created_and_ins"}
UF_RECONCILIATION_SUCCESS_STATUSES = {"reconciled", "stored_pending"}


class BridgeRejected(Exception):
    """Le récepteur a répondu HTTP 200 mais avec un statut métier d'échec.

    Levée pour déclencher le retry de l'enqueue (retry=3) puis l'Error Log natif (OBS-1) —
    exactement comme un échec HTTP."""


def _business_status(result):
    """Extrait message.status d'une réponse Frappe ({"message": {...}}) — None si absent."""
    message = result.get("message") if isinstance(result, dict) else None
    if isinstance(message, dict):
        return message.get("status")
    return None


def _check_business_status(result, success_statuses, *, step, dossier_id):
    """ADM-DEBT-58 : statut métier hors succès → log failed + raise (retry → Error Log)."""
    status = _business_status(result)
    if status in success_statuses:
        return status
    log_event(step, "failed", dossier_id=dossier_id, error=f"business_status={status!r}", level="error",
              alert_type=step)  # OBS-2 HIGH : step ∈ {bridge_inscription, uf_double_check} = types curés
    raise BridgeRejected(f"{step}: statut métier d'échec reçu du récepteur: {status!r}")


def _build_financial_context(applicant):
    """Build the financial context payload from an Admission Applicant."""
    session = None
    if applicant.session:
        session = frappe.get_doc("Admission Session", applicant.session)

    from admission.api.legal import _get_consent_proof

    consent_proof = _get_consent_proof(applicant.name, "DATA_TRANSFER")

    return {
        "person_id": applicant.person_id or "",
        "dossier_id": applicant.name,  # C3-ENROLL : corrélation pont/logs campus
        "academic_year": session.academic_year if session else "",
        "program": applicant.programme_code or "",
        "academic_level": getattr(applicant, "level_code", "") or "",
        # C3-ENROLL (T4) : identité minimale pour la création headless du Student Applicant
        # campus (A1/DEC-260). PII — l'envoi reste gardé par _pii_transport_allowed (DAT-2).
        "identite": {
            "prenom": applicant.first_name or "",
            "nom": applicant.last_name or "",
            "email": applicant.email or "",
            "tel": applicant.phone or "",
            "date_naissance": str(applicant.date_of_birth or "") if getattr(applicant, "date_of_birth", None) else "",
        },
        "bourses_validees": json.loads(applicant.validated_scholarships or "[]"),
        "promotion": {
            "code": applicant.promo_code or "",
            "rate": float(applicant.promo_rate or 0),
            "captured_date": str(applicant.promo_captured_date or ""),
        },
        "acompte": {
            "present": bool(applicant.acompte_xof and float(applicant.acompte_xof) > 0),
            "montant_xof": float(applicant.acompte_xof or 0),
            "encaisse": True,
        },
        "consent_data_transfer": consent_proof,
    }


def enqueue_bridge_notification(applicant_name):
    """Enqueue the pont POST to campus (non-blocking).

    Called from admission_applicant.py on INS transition.
    """
    frappe.enqueue(
        _send_bridge_notification,
        queue="default",
        applicant_name=applicant_name,
        is_async=True,
        retry=3,
    )


def _mark_bridge(applicant_name, *, ok, error=None):
    """LOT P4 — marque l'état d'émission du pont sur le dossier (update_modified=False :
    les fenêtres de rétention reposent sur modified). Trace lisible par le redrive et le staff."""
    from frappe.utils import now_datetime
    values = {"bridge_notified": 1 if ok else 0,
              "bridge_last_error": (str(error)[:500] if error else None)}
    if ok:
        values["bridge_notified_at"] = now_datetime()
    frappe.db.set_value("Admission Applicant", applicant_name, values, update_modified=False)


def _send_bridge_notification(applicant_name):
    """POST financial context to campus → Student Applicant INS.

    LOT P5 — idempotence d'émission : déjà acquitté (bridge_notified=1) → skip (un rejeu
    manuel/redrive ne double-POSTe pas ; le récepteur reste idempotent par person_id en
    défense en profondeur). Échec → marquage bridge_last_error puis raise (retry enqueue,
    puis redrive quotidien P4)."""
    applicant = frappe.get_doc("Admission Applicant", applicant_name)
    if getattr(applicant, "bridge_notified", 0):
        log_event("bridge_inscription", "skipped_already_notified", dossier_id=applicant_name)
        return {"status": "already_notified"}
    config = _get_campus_config()
    if not config:
        log_event("bridge_inscription", "skipped_no_config", dossier_id=applicant_name, level="warning")
        return None

    if not _pii_transport_allowed(config["url"], context="bridge→campus"):
        return None

    payload = _build_financial_context(applicant)

    try:
        resp = requests.post(
            config["url"].rstrip("/") + CAMPUS_INSCRIPTION_ENDPOINT,
            json=payload,
            headers={"Content-Type": "application/json", "X-API-Key": config["token"]},
            timeout=30,
        )
        resp.raise_for_status()
        result = resp.json()
        # ADM-DEBT-58 : not_found/error dans le corps = ÉCHEC (raise → retry → Error Log)
        try:
            status = _check_business_status(
                result, BRIDGE_SUCCESS_STATUSES, step="bridge_inscription", dossier_id=applicant_name
            )
        except BridgeRejected as exc:
            _mark_bridge(applicant_name, ok=False, error=exc)
            frappe.db.commit()
            raise
        log_event("bridge_inscription", "success", dossier_id=applicant_name, business_status=status)
        _mark_bridge(applicant_name, ok=True)
        frappe.db.commit()
        return result
    except requests.RequestException as exc:
        log_event("bridge_inscription", "failed", dossier_id=applicant_name, error=str(exc), level="error",
                  alert_type="bridge_inscription")  # OBS-2 HIGH : étudiant pas créé côté campus
        _mark_bridge(applicant_name, ok=False, error=exc)
        frappe.db.commit()
        raise


def enqueue_double_check(applicant_name):
    """Enqueue the double-check POST to UF (non-blocking).

    Called from admission_applicant.py on INS transition.
    """
    frappe.enqueue(
        _send_double_check,
        queue="default",
        applicant_name=applicant_name,
        is_async=True,
        retry=3,
    )


def _send_double_check(applicant_name):
    """POST financial context directly to UF for reconciliation."""
    applicant = frappe.get_doc("Admission Applicant", applicant_name)
    config = _get_uf_config()
    if not config:
        log_event("uf_double_check", "skipped_no_config", dossier_id=applicant_name, level="warning")
        return None

    if not _pii_transport_allowed(config["url"], context="double-check→UF"):
        return None

    payload = _build_financial_context(applicant)

    try:
        resp = requests.post(
            config["url"].rstrip("/") + UF_RECONCILIATION_ENDPOINT,
            json=payload,
            headers=_uf_auth_headers(config),
            timeout=15,
        )
        resp.raise_for_status()
        result = resp.json()
        # ADM-DEBT-58 (symétrie) : error dans le corps = ÉCHEC (stored_pending reste un succès :
        # la réconciliation se rejouera quand le Student Fee existera).
        status = _check_business_status(
            result, UF_RECONCILIATION_SUCCESS_STATUSES, step="uf_double_check", dossier_id=applicant_name
        )
        log_event("uf_double_check", "success", dossier_id=applicant_name, business_status=status)
        return result
    except requests.RequestException as exc:
        log_event("uf_double_check", "failed", dossier_id=applicant_name, error=str(exc), level="error",
                  alert_type="uf_double_check")  # OBS-2 HIGH : réconciliation UF cassée
        raise


def _uf_auth_headers(config):
    headers = {"Content-Type": "application/json"}
    if config.get("api_key") and config.get("api_secret"):
        headers["Authorization"] = f"token {config['api_key']}:{config['api_secret']}"
    return headers


# ── LOT P4 : redrive quotidien du pont INS (hooks.py daily) ────────────────────


def redrive_bridge_notifications():
    """Rattrape les dossiers INS jamais acquittés par le campus (bridge_notified=0).

    Miroir de notify_uf.redrive_uf_notifications : renvoi SYNCHRONE (pas de re-enqueue —
    on veut le résultat), idempotent (skip si acquitté entre-temps), alerte System
    Manager s'il reste des dossiers non répliqués après le passage. Non-bloquant par
    dossier : un échec n'arrête pas la file.
    """
    names = frappe.get_all(
        "Admission Applicant",
        filters={"status": "INS", "anonymized": ("!=", 1), "bridge_notified": ("!=", 1)},
        pluck="name",
    )
    redriven = 0
    for name in names:
        try:
            _send_bridge_notification(name)
            redriven += 1
        except Exception:
            frappe.logger("bridge").warning(
                f"Bridge redrive failed for {name}: {frappe.get_traceback()}"
            )
    remaining = len(names) - redriven
    if remaining > 0:
        _alert_unbridged_inscriptions(remaining)
    frappe.logger("bridge").info(
        f"Bridge redrive: {redriven}/{len(names)} re-sent, {remaining} still pending."
    )
    return {"redriven": redriven, "candidates": len(names), "remaining": remaining}


def _alert_unbridged_inscriptions(count):
    """Alerte native (email System Manager) si des INS restent non répliqués au campus."""
    try:
        from frappe.utils.user import get_system_managers
        recipients = get_system_managers(only_name=False)
        if recipients:
            frappe.sendmail(
                recipients=recipients,
                subject=f"[Admission] {count} inscription(s) non repliquee(s) au campus",
                message=(
                    f"{count} dossier(s) INS n'ont pas pu etre transmis au campus apres "
                    f"re-drive quotidien (etudiant NON cree). Voir bridge_last_error sur "
                    f"les dossiers et l'Error Log (bridge_inscription failed)."
                ),
            )
    except Exception:
        frappe.logger("bridge").error(
            f"Bridge redrive alert email failed: {frappe.get_traceback()}"
        )
