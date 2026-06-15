"""Pull du catalogue de formations depuis campus → mirror Admission Programme.

Campus est la source de vérité du catalogue (structure + commercial). Ce sync
remplace le seed local à la bascule (cf. SPEC-CAMPUS-CATALOGUE-SOURCE-DE-VERITE §7/§10).

Pattern : Phase A (referential_sync) — HTTP GET, upsert idempotent, scheduler daily.
Direction : admission → campus (read). Auth : X-API-Key (comme level_sync).
DORMANT tant que campus_base_url/campus_api_token absents (recette = seed local).

Endpoint : portal_app.api.admission_catalogue.get_catalogue_for_admission.
"""

from __future__ import annotations

import requests

import frappe
from frappe.utils import now_datetime

from admission.api._config import _get_campus_config


CAMPUS_CATALOGUE_ENDPOINT = (
    "/api/method/portal_app.api.admission_catalogue.get_catalogue_for_admission"
)

# Champs marketing/structure portés par Admission Programme.
_META_FIELDS = ("title", "parcours", "partner", "partner_name", "location")


def sync_catalogue():
    """Pull le catalogue depuis campus et upsert le mirror Admission Programme.

    Appelé par le scheduler (daily) ou à la main via bench execute.
    """
    config = _get_campus_config()
    if not config:
        frappe.logger("catalogue_sync").warning(
            "Catalogue sync skipped: campus_base_url not configured."
        )
        return {"status": "skipped", "reason": "missing_config"}

    programmes = _fetch_catalogue(config)
    if programmes is None:
        return {"status": "error", "reason": "fetch_failed"}

    overwritten = []
    count = _upsert_catalogue(programmes, overwritten=overwritten)
    frappe.db.commit()

    if overwritten:
        # D3 « campus gagne, on prévient » : un override manuel (mode dégradé) vient d'être
        # écrasé et reverrouillé par campus. On alerte (on ne bloque jamais la sync).
        _alert_manual_overwritten(overwritten)

    from admission.api.public import _invalidate_catalog_cache
    _invalidate_catalog_cache()  # PERF-1 : anti-périmé après sync

    result = {
        "status": "ok",
        "programmes_synced": count,
        "synced_at": str(now_datetime()),
    }
    frappe.logger("catalogue_sync").info(f"Catalogue sync complete: {result}")
    return result


def _fetch_catalogue(config):
    url = config["url"].rstrip("/") + CAMPUS_CATALOGUE_ENDPOINT
    headers = {"Content-Type": "application/json", "X-API-Key": config["token"]}
    try:
        resp = requests.get(url, headers=headers, timeout=30)
        resp.raise_for_status()
    except requests.RequestException as exc:
        frappe.logger("catalogue_sync").error(f"Catalogue fetch failed: {exc}")
        return None

    payload = resp.json()
    message = payload.get("message") or payload
    if isinstance(message, dict):
        # LOT P2 (ceinture) : réponse SANS clé "programmes" = échec explicite (jamais 0 silencieux).
        if "programmes" not in message:
            frappe.logger("catalogue_sync").error(f"Catalogue fetch rejected: {message}")
            return None
        return message.get("programmes") or []
    return []


def _upsert_catalogue(programmes, overwritten=None):
    """Upsert idempotent. Deux passes : offres de base d'abord (pour que les Link
    dd_component_1/2 des Double-Diplomations résolvent), puis les DD.

    `overwritten` (liste optionnelle) : collecte les codes dont un override manuel
    (source=Manuel) a été écrasé par campus (D3 — pour alerte, voir sync_catalogue)."""
    now = now_datetime()
    base = [p for p in programmes if (p.get("parcours") != "Double-Diplomation")]
    dd = [p for p in programmes if (p.get("parcours") == "Double-Diplomation")]
    count = 0
    for entry in base:
        if _upsert_entry(entry, now, with_dd=False, overwritten=overwritten):
            count += 1
    for entry in dd:
        if _upsert_entry(entry, now, with_dd=True, overwritten=overwritten):
            count += 1
    return count


def _upsert_entry(entry, synced_on, with_dd, overwritten=None):
    code = entry.get("programme_code")
    if not code:
        return False

    values = {
        "title": entry.get("title") or code,
        "parcours": entry.get("parcours"),
        "partner": entry.get("partner"),
        "partner_name": entry.get("partner_name"),
        "location": entry.get("location"),
        "is_active": 1 if entry.get("is_active") else 0,
        "source": "Campus",
        "last_synced_on": synced_on,
    }
    if with_dd:
        values["dd_component_1"] = entry.get("dd_component_1")
        values["dd_component_2"] = entry.get("dd_component_2")
        values["dd_affinity"] = entry.get("dd_affinity")

    if frappe.db.exists("Admission Programme", code):
        doc = frappe.get_doc("Admission Programme", code)
        if overwritten is not None and doc.source == "Manuel":
            overwritten.append(code)  # D3 : campus écrase et reverrouille un override manuel
        doc.update(values)
        doc.save(ignore_permissions=True)
    else:
        doc = frappe.get_doc({
            "doctype": "Admission Programme",
            "programme_code": code,
            **values,
        })
        doc.insert(ignore_permissions=True)
    return True


def _alert_manual_overwritten(codes):
    """Alerte SM : campus a écrasé des overrides manuels (D3 « on prévient »). Non-bloquant."""
    frappe.logger("catalogue_sync").warning(
        f"Campus a écrasé {len(codes)} override(s) manuel(s) : {', '.join(codes)}"
    )
    try:
        from frappe.utils.user import get_system_managers
        recipients = get_system_managers(only_name=False)
        if recipients:
            frappe.sendmail(
                recipients=recipients,
                subject=f"[Admission] {len(codes)} programme(s) manuel(s) ecrase(s) par campus",
                message=(
                    "La synchronisation campus a ecrase et reverrouille des programmes edites "
                    f"manuellement en mode degrade : {', '.join(codes)}. "
                    "Campus = source de verite : verifiez que la donnee campus est correcte."
                ),
            )
    except Exception:
        frappe.logger("catalogue_sync").error(
            f"Alerte override manuel echouee: {frappe.get_traceback()}"
        )
