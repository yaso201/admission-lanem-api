"""Pull admission fee catalog from UF → local Admission Fee Catalog mirror.

Pattern: Phase A (referential_sync) — HTTP GET, idempotent upsert, daily scheduler.
Direction: admission → UF (read). Auth: Frappe API key (same as notify_uf).
3D keys: {program_code}-{level_code}-{fee_type}. Ref: ADM-UF-3, ADM-SCH, SPEC §4.4.
"""

from __future__ import annotations

import requests

import frappe
from frappe.utils import now_datetime

from admission.api._config import _get_uf_config  # HELPERS-DEDUP : version unique


UF_CATALOG_ENDPOINT = (
    "/api/method/university_finance.api.fee_catalog.get_admission_fee_catalog"
)


def sync_fee_catalog():
    """Pull fee catalog from UF and upsert into local mirror.

    Called by scheduler (daily) or manually via bench execute.
    """
    config = _get_uf_config()
    if not config:
        frappe.logger("fee_catalog_sync").warning(
            "Fee catalog sync skipped: uf_backoffice_url not configured."
        )
        return {"status": "skipped", "reason": "missing_config"}

    catalog = _fetch_catalog(config)
    if catalog is None:
        return {"status": "error", "reason": "fetch_failed"}

    count = _upsert_catalog(catalog)
    frappe.db.commit()

    result = {
        "status": "ok",
        "entries_synced": count,
        "synced_at": str(now_datetime()),
    }
    frappe.logger("fee_catalog_sync").info(f"Fee catalog sync complete: {result}")
    from admission.api.public import _invalidate_catalog_cache
    _invalidate_catalog_cache()  # PERF-1 : anti-périmé après sync
    return result


def _fetch_catalog(config):
    url = config["url"].rstrip("/") + UF_CATALOG_ENDPOINT
    headers = {"Content-Type": "application/json"}
    if config.get("api_key") and config.get("api_secret"):
        headers["Authorization"] = f"token {config['api_key']}:{config['api_secret']}"

    try:
        resp = requests.get(url, headers=headers, timeout=30)
        resp.raise_for_status()
    except requests.RequestException as exc:
        frappe.logger("fee_catalog_sync").error(
            f"Fee catalog fetch failed: {exc}"
        )
        return None

    payload = resp.json()
    message = payload.get("message") or payload
    if isinstance(message, dict):
        return message.get("catalog") or message.get("data") or []
    return []


def _upsert_catalog(catalog):
    count = 0
    now = now_datetime()
    for entry in catalog:
        program_code = entry.get("program_code")
        level_code = entry.get("level_code", "DEFAULT")
        fee_type = entry.get("fee_type")
        amount = entry.get("amount_xof")
        if not program_code or not fee_type or amount is None:
            continue
        _upsert_entry(program_code, level_code, fee_type, float(amount), now)
        count += 1
    return count


def _upsert_entry(program_code, level_code, fee_type, amount_xof, synced_on):
    catalog_key = f"{program_code}-{level_code}-{fee_type}"
    if frappe.db.exists("Admission Fee Catalog", catalog_key):
        frappe.db.set_value(
            "Admission Fee Catalog",
            catalog_key,
            {
                "amount_xof": amount_xof,
                "last_synced_on": synced_on,
            },
            update_modified=False,
        )
    else:
        doc = frappe.get_doc({
            "doctype": "Admission Fee Catalog",
            "catalog_key": catalog_key,
            "program_code": program_code,
            "level_code": level_code,
            "fee_type": fee_type,
            "amount_xof": amount_xof,
            "last_synced_on": synced_on,
        })
        doc.insert(ignore_permissions=True)
