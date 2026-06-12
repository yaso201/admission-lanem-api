"""Notify UF (backoffice) of payment events — choreography DEC-221.

After admission confirms a payment (online or offline), this module
POSTs a notification to UF so it can record the payment for accounting.

Both online (webhook) and offline (agent) paths converge here.
UF is reactive: it records, never pilots.

Config (site_config.json):
    uf_backoffice_url: base URL of the backoffice site (e.g. http://backoffice:8000)
    uf_api_key: API key for authenticating to UF
    uf_api_secret: API secret for authenticating to UF

Ref: DEC-221, PAY-3.
"""

from __future__ import annotations

import requests

import frappe
from frappe.utils import now_datetime

from admission.api._config import _get_uf_config, _pii_transport_allowed  # HELPERS-DEDUP + DAT-2 garde transport
from admission.api._log import log_event  # OBS-2 : log structuré + corrélation dossier_id


UF_ENDPOINT = "/api/method/university_finance.api.admission_payment_receiver.receive_payment_notification"


def notify_uf_payment(
    applicant,
    fee,
    payment,
    session=None,
):
    """Build payload and POST to UF backoffice.

    Args:
        applicant: Admission Applicant document (or dict-like)
        fee: Applicant Fee document (admission side)
        payment: Applicant Fee Payment document (admission side)
        session: Admission Session document (optional, for session_type)
    """
    config = _get_uf_config()
    if not config:
        log_event("notify_uf_payment", "skipped_no_config", level="warning")
        return None

    if not _pii_transport_allowed(config["url"], context="notify_uf payment→UF"):
        return None

    payload = _build_payload(applicant, fee, payment, session)

    try:
        resp = requests.post(
            config["url"].rstrip("/") + UF_ENDPOINT,
            json=payload,
            headers=_auth_headers(config),
            timeout=15,
        )
        resp.raise_for_status()
        result = resp.json()
        _mark_uf_notified(payment)
        log_event("notify_uf_payment", "success", dossier_id=_get(applicant, "name"), ref=_get(payment, "name"))
        return result
    except requests.RequestException as exc:
        # OBS-1 : trace exploitable (Error Log natif) au lieu d'un return None muet.
        frappe.log_error(
            title="UF payment notification failed",
            message=f"payment={_get(payment, 'name')} error={exc}\n{frappe.get_traceback()}",
        )
        log_event("notify_uf_payment", "failed", dossier_id=_get(applicant, "name"), ref=_get(payment, "name"), error=str(exc), level="error")
        return None


def _mark_uf_notified(payment):
    """OBS-1 : marque le paiement comme répercuté à UF (base du re-drive idempotent)."""
    name = _get(payment, "name")
    if name:
        frappe.db.set_value(
            "Applicant Fee Payment", name,
            {"uf_notified": 1, "uf_notified_at": now_datetime()},
            update_modified=False,
        )


def _build_payload(applicant, fee, payment, session):
    return {
        "applicant_id": _get(applicant, "name"),
        "person_id": _get(applicant, "person_id") or "",
        "session_id": _get(applicant, "session") or "",
        "fee_type": _get(fee, "fee_type") or "application",
        "session_type": _get(session, "programme_label") if session else "",
        "applicant_name": " ".join(
            filter(None, [_get(applicant, "first_name"), _get(applicant, "last_name")])
        ),
        "applicant_first_name": _get(applicant, "first_name") or "",
        "applicant_last_name": _get(applicant, "last_name") or "",
        "applicant_email": _get(applicant, "email") or "",
        "amount_paid": _get(payment, "amount_xof") or _get(payment, "amount_paid") or 0,
        "payment_mode": _get(payment, "payment_mode") or "Bank",
        "provider": _get(payment, "provider") or "",
        "provider_reference": _get(payment, "provider_reference") or "",
        "idempotency_key": _get(payment, "idempotency_key") or "",
        "source": _resolve_source(payment),
        "payment_status": _map_status(_get(payment, "payment_status")),
        "paid_at": str(_get(payment, "paid_at") or ""),
    }


def _resolve_source(payment):
    mode = _get(payment, "payment_mode") or ""
    if mode == "Online":
        return "online_provider"
    return "manual"


def _map_status(status):
    """Map admission payment status to UF status vocabulary."""
    if not status:
        return "Confirmé"
    s = status.lower()
    if s in {"confirmed", "paid"}:
        return "Confirmé"
    if s in {"pending"}:
        return "En attente"
    return status


def _get(obj, attr):
    if isinstance(obj, dict):
        return obj.get(attr)
    return getattr(obj, attr, None)


def on_payment_update(doc, method):
    """doc_events hook: when AFP status changes to Confirmed, notify UF.

    This catches the Desk workflow "Confirm Payment" (SOP→SOU) path
    where an admin manually confirms an offline payment.
    """
    if getattr(frappe.flags, "_notifying_uf_payment", False):
        return

    old_status = doc.get_doc_before_save()
    if not old_status:
        return
    old_payment_status = getattr(old_status, "payment_status", None)
    new_payment_status = doc.payment_status

    if old_payment_status == new_payment_status:
        return
    if new_payment_status not in {"Confirmed", "Paid"}:
        return

    try:
        frappe.flags._notifying_uf_payment = True
        fee = frappe.get_doc("Applicant Fee", doc.applicant_fee) if doc.applicant_fee else None
        applicant_name = _get(doc, "applicant")
        applicant = frappe.get_doc("Admission Applicant", applicant_name) if applicant_name else None
        if applicant and fee:
            notify_uf_payment(applicant=applicant, fee=fee, payment=doc)
    except Exception:
        frappe.log_error(
            title="UF payment notification (on_update) failed",
            message=frappe.get_traceback(),
        )
        frappe.logger("notify_uf").error(
            f"UF notification on_update failed (non-blocking): {frappe.get_traceback()}"
        )
    finally:
        frappe.flags._notifying_uf_payment = False


ABANDON_STATUSES = {"REF", "DES"}
UF_ABANDON_ENDPOINT = "/api/method/university_finance.api.admission_abandon_receiver.receive_admission_abandon"


def on_applicant_abandon(doc, method):
    """doc_events hook: when Admission Applicant transitions to REF or DES, notify UF.

    Distinct from DEM (education abandon, F2-F4). REF = refus, DES = désistement.
    Non-blocking: logs on failure.

    Ref: ADM-UF-2, DEC-221 (UF reactive), DEC-223 (DEM = education only).
    """
    if getattr(frappe.flags, "_notifying_uf_abandon", False):
        return

    old_doc = doc.get_doc_before_save()
    if not old_doc:
        return

    old_status = getattr(old_doc, "status", None)
    new_status = doc.status

    if old_status == new_status:
        return
    if new_status not in ABANDON_STATUSES:
        return

    person_id = _get(doc, "person_id")
    if not person_id:
        frappe.logger("notify_uf").warning(
            f"Abandon notification skipped: no person_id on {doc.name}"
        )
        return

    try:
        frappe.flags._notifying_uf_abandon = True
        notify_uf_applicant_abandon(doc)
    except Exception:
        frappe.log_error(
            title="UF abandon notification (on_update) failed",
            message=frappe.get_traceback(),
        )
        frappe.logger("notify_uf").error(
            f"UF abandon notification failed (non-blocking): {frappe.get_traceback()}"
        )
    finally:
        frappe.flags._notifying_uf_abandon = False


def notify_uf_applicant_abandon(applicant):
    """POST abandon signal to UF backoffice."""
    config = _get_uf_config()
    if not config:
        log_event("notify_uf_abandon", "skipped_no_config", level="warning")
        return None

    if not _pii_transport_allowed(config["url"], context="notify_uf abandon→UF"):
        return None

    payload = {
        "person_id": _get(applicant, "person_id") or "",
        "applicant_id": _get(applicant, "name"),
        "status": _get(applicant, "status"),
        "applicant_first_name": _get(applicant, "first_name") or "",
        "applicant_last_name": _get(applicant, "last_name") or "",
        "applicant_name": " ".join(
            filter(None, [_get(applicant, "first_name"), _get(applicant, "last_name")])
        ),
        "applicant_email": _get(applicant, "email") or "",
    }

    try:
        resp = requests.post(
            config["url"].rstrip("/") + UF_ABANDON_ENDPOINT,
            json=payload,
            headers=_auth_headers(config),
            timeout=15,
        )
        resp.raise_for_status()
        result = resp.json()
        log_event("notify_uf_abandon", "success", dossier_id=_get(applicant, "name"), applicant_status=_get(applicant, "status"))
        return result
    except requests.RequestException as exc:
        frappe.log_error(
            title="UF abandon notification failed",
            message=f"applicant={applicant.name} error={exc}\n{frappe.get_traceback()}",
        )
        log_event("notify_uf_abandon", "failed", dossier_id=_get(applicant, "name"), error=str(exc), level="error")
        return None


def redrive_uf_notifications():
    """OBS-1 — la vraie « nightly sync » : re-POST les paiements Confirmed non notifiés à UF.

    Idempotent (UF dédoublonne par provider_reference). Si la config UF est ABSENTE, on ne
    re-drive PAS (ce n'est pas un échec, juste non configuré) → pas de boucle dans le vide.
    Point d'entrée scheduler (hooks.py scheduler_events.daily).
    """
    if not _get_uf_config():
        frappe.logger("notify_uf").info("UF re-drive skipped: uf_backoffice_url not configured.")
        return {"status": "skipped_no_config"}
    names = frappe.get_all(
        "Applicant Fee Payment",
        filters={"payment_status": "Confirmed", "uf_notified": 0},
        pluck="name",
    )
    redriven = 0
    for name in names:
        payment = frappe.get_doc("Applicant Fee Payment", name)
        applicant = (
            frappe.get_doc("Admission Applicant", payment.applicant) if payment.applicant else None
        )
        fee = frappe.get_doc("Applicant Fee", payment.applicant_fee) if payment.applicant_fee else None
        if applicant and fee and notify_uf_payment(applicant=applicant, fee=fee, payment=payment):
            redriven += 1
    frappe.db.commit()
    remaining = len(names) - redriven
    if remaining > 0:
        _alert_unreplicated_payments(remaining)
    frappe.logger("notify_uf").info(
        f"UF re-drive: {redriven}/{len(names)} re-notified, {remaining} still pending."
    )
    return {"redriven": redriven, "candidates": len(names), "remaining": remaining}


def _alert_unreplicated_payments(count):
    """Alerte native (email System Manager) si des paiements restent non répercutés à UF."""
    try:
        from frappe.utils.user import get_system_managers
        recipients = get_system_managers(only_name=False)
        if recipients:
            frappe.sendmail(
                recipients=recipients,
                subject=f"[Admission] {count} paiement(s) non repercute(s) a UF",
                message=(
                    f"{count} paiement(s) confirme(s) n'ont pas pu etre notifie(s) a UF apres "
                    f"re-drive quotidien. Voir Error Log (UF payment notification failed)."
                ),
            )
    except Exception:
        frappe.logger("notify_uf").error(
            f"UF re-drive alert email failed: {frappe.get_traceback()}"
        )


def _auth_headers(config):
    headers = {"Content-Type": "application/json"}
    if config.get("api_key") and config.get("api_secret"):
        headers["Authorization"] = f"token {config['api_key']}:{config['api_secret']}"
    return headers
