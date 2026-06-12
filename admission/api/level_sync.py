"""Pull Academic Level catalog from campus → local Admission Level Mirror.

Pattern: Phase A (referential_sync) — HTTP GET, idempotent upsert, daily scheduler.
Direction: admission → campus (read). Auth: X-API-Key (same as bridge).

Ref: ADM-SCH, D2 (niveaux choisissables = Academic Level replique).
"""

from __future__ import annotations

import requests

import frappe
from frappe.utils import now_datetime

from admission.api._config import _get_campus_config  # HELPERS-DEDUP : version unique


CAMPUS_LEVELS_ENDPOINT = (
    "/api/method/portal_app.api.admission_levels.get_levels_for_admission"
)


def sync_levels():
    """Pull Academic Level catalog from campus and upsert into local mirror.

    Called by scheduler (daily) or manually via bench execute.
    """
    config = _get_campus_config()
    if not config:
        frappe.logger("level_sync").warning(
            "Level sync skipped: campus_base_url not configured."
        )
        return {"status": "skipped", "reason": "missing_config"}

    levels = _fetch_levels(config)
    if levels is None:
        return {"status": "error", "reason": "fetch_failed"}

    count = _upsert_levels(levels)
    frappe.db.commit()

    result = {
        "status": "ok",
        "levels_synced": count,
        "synced_at": str(now_datetime()),
    }
    frappe.logger("level_sync").info(f"Level sync complete: {result}")
    from admission.api.public import _invalidate_catalog_cache
    _invalidate_catalog_cache()  # PERF-1 : anti-périmé après sync
    return result


def _fetch_levels(config):
    url = config["url"].rstrip("/") + CAMPUS_LEVELS_ENDPOINT
    headers = {
        "Content-Type": "application/json",
        "X-API-Key": config["token"],
    }

    try:
        resp = requests.get(url, headers=headers, timeout=30)
        resp.raise_for_status()
    except requests.RequestException as exc:
        frappe.logger("level_sync").error(f"Level fetch failed: {exc}")
        return None

    payload = resp.json()
    message = payload.get("message") or payload
    if isinstance(message, dict):
        # LOT P2 (ceinture) : une réponse SANS clé "levels" (ex. erreur d'auth structurée)
        # est un ÉCHEC explicite — jamais un sync silencieux de 0 niveau.
        if "levels" not in message:
            frappe.logger("level_sync").error(f"Level fetch rejected: {message}")
            return None
        return message.get("levels") or []
    return []


def _upsert_levels(levels):
    count = 0
    now = now_datetime()
    for entry in levels:
        level_code = entry.get("level_code")
        level_name = entry.get("level_name")
        program_code = entry.get("program_code")
        level_order = entry.get("level_order", 0)
        if not level_code or not program_code:
            continue
        _upsert_entry(level_code, level_name, program_code, level_order, now)
        count += 1
    return count


def _upsert_entry(level_code, level_name, program_code, level_order, synced_on):
    if frappe.db.exists("Admission Level Mirror", level_code):
        frappe.db.set_value(
            "Admission Level Mirror",
            level_code,
            {
                "level_name": level_name,
                "program_code": program_code,
                "level_order": level_order,
                "last_synced_on": synced_on,
            },
            update_modified=False,
        )
    else:
        doc = frappe.get_doc({
            "doctype": "Admission Level Mirror",
            "level_code": level_code,
            "level_name": level_name,
            "program_code": program_code,
            "level_order": level_order,
            "last_synced_on": synced_on,
        })
        doc.insert(ignore_permissions=True)
