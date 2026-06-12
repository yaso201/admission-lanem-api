"""Pull scholarship + promotion catalog from UF -> local mirrors.

Pattern: Phase A (referential_sync) -- HTTP GET, idempotent upsert, daily scheduler.
Direction: admission -> UF (read). Auth: Frappe API key.

Syncs:
  - Admission Scholarship Mirror (from UF Scholarship Rate)
  - Admission Promotion Mirror (from UF Promotion)
  - Annual tuition amounts into Admission Fee Catalog (fee_type=annual)
  - Scholarship cap stored as Admission Fee Catalog entry (SCHOLARSHIP-cap)

Ref: ADM-UF-4, SPEC-CONTRAT-FINANCE-ADMISSION-UF §3bis.
"""

from __future__ import annotations

import requests

import frappe
from frappe.utils import now_datetime

from admission.api._config import _get_uf_config  # HELPERS-DEDUP : version unique


UF_CATALOG_ENDPOINT = (
    "/api/method/university_finance.api.scholarship_catalog"
    ".get_scholarship_promotion_catalog"
)


def sync_scholarship_catalog():
    """Pull scholarship/promotion catalog from UF and upsert local mirrors.

    Called by scheduler (daily) or manually via bench execute.
    """
    config = _get_uf_config()
    if not config:
        frappe.logger("scholarship_sync").warning(
            "Scholarship sync skipped: uf_backoffice_url not configured."
        )
        return {"status": "skipped", "reason": "missing_config"}

    data = _fetch_catalog(config)
    if data is None:
        return {"status": "error", "reason": "fetch_failed"}

    now = now_datetime()
    sch_count = _upsert_scholarships(data.get("scholarships") or [], now)
    promo_count = _upsert_promotions(data.get("promotions") or [], now)
    annual_count = _upsert_annual_amounts(data.get("annual_amounts") or [], now)
    _store_scholarship_cap(data.get("scholarship_cap", 0.50), now)

    frappe.db.commit()

    result = {
        "status": "ok",
        "scholarships_synced": sch_count,
        "promotions_synced": promo_count,
        "annual_synced": annual_count,
        "synced_at": str(now),
    }
    frappe.logger("scholarship_sync").info(
        f"Scholarship catalog sync complete: {result}"
    )
    from admission.api.public import _invalidate_catalog_cache
    _invalidate_catalog_cache()  # PERF-1 : anti-périmé après sync
    return result


def _fetch_catalog(config):
    url = config["url"].rstrip("/") + UF_CATALOG_ENDPOINT
    headers = {"Content-Type": "application/json"}
    if config.get("api_key") and config.get("api_secret"):
        headers["Authorization"] = (
            f"token {config['api_key']}:{config['api_secret']}"
        )

    try:
        resp = requests.get(url, headers=headers, timeout=30)
        resp.raise_for_status()
    except requests.RequestException as exc:
        frappe.logger("scholarship_sync").error(
            f"Scholarship catalog fetch failed: {exc}"
        )
        return None

    payload = resp.json()
    message = payload.get("message") or payload
    if isinstance(message, dict):
        return message
    return {}


def _upsert_scholarships(scholarships, synced_on):
    count = 0
    for entry in scholarships:
        mirror_key = entry.get("name")
        scholarship_name = entry.get("scholarship_name")
        if not mirror_key or not scholarship_name:
            continue
        _upsert_scholarship_entry(
            mirror_key=mirror_key,
            scholarship_name=scholarship_name,
            category=entry.get("category", ""),
            rate=float(entry.get("rate", 0)),
            exclusivity_group=entry.get("exclusivity_group", ""),
            program=entry.get("program", ""),
            synced_on=synced_on,
        )
        count += 1
    return count


def _upsert_scholarship_entry(
    mirror_key, scholarship_name, category, rate,
    exclusivity_group, program, synced_on,
):
    if frappe.db.exists("Admission Scholarship Mirror", mirror_key):
        frappe.db.set_value(
            "Admission Scholarship Mirror",
            mirror_key,
            {
                "scholarship_name": scholarship_name,
                "category": category,
                "rate": rate,
                "exclusivity_group": exclusivity_group,
                "program": program,
                "last_synced_on": synced_on,
            },
            update_modified=False,
        )
    else:
        doc = frappe.get_doc({
            "doctype": "Admission Scholarship Mirror",
            "mirror_key": mirror_key,
            "scholarship_name": scholarship_name,
            "category": category,
            "rate": rate,
            "exclusivity_group": exclusivity_group,
            "program": program,
            "last_synced_on": synced_on,
        })
        doc.insert(ignore_permissions=True)


def _upsert_promotions(promotions, synced_on):
    count = 0
    for entry in promotions:
        mirror_key = entry.get("name")
        promo_name = entry.get("promo_name")
        if not mirror_key or not promo_name:
            continue
        _upsert_promotion_entry(
            mirror_key=mirror_key,
            promo_name=promo_name,
            rate=float(entry.get("rate", 0)),
            start_date=entry.get("start_date", ""),
            end_date=entry.get("end_date", ""),
            program=entry.get("program", ""),
            synced_on=synced_on,
        )
        count += 1
    return count


def _upsert_promotion_entry(
    mirror_key, promo_name, rate, start_date, end_date, program, synced_on,
):
    if frappe.db.exists("Admission Promotion Mirror", mirror_key):
        frappe.db.set_value(
            "Admission Promotion Mirror",
            mirror_key,
            {
                "promo_name": promo_name,
                "rate": rate,
                "start_date": start_date or None,
                "end_date": end_date or None,
                "program": program,
                "last_synced_on": synced_on,
            },
            update_modified=False,
        )
    else:
        doc = frappe.get_doc({
            "doctype": "Admission Promotion Mirror",
            "mirror_key": mirror_key,
            "promo_name": promo_name,
            "rate": rate,
            "start_date": start_date or None,
            "end_date": end_date or None,
            "program": program,
            "last_synced_on": synced_on,
        })
        doc.insert(ignore_permissions=True)


def _upsert_annual_amounts(annual_amounts, synced_on):
    count = 0
    for entry in annual_amounts:
        program_code = entry.get("program_code")
        level_code = entry.get("level_code", "DEFAULT")
        amount = entry.get("amount_xof")
        if not program_code or amount is None:
            continue
        catalog_key = f"{program_code}-{level_code}-annual"
        if frappe.db.exists("Admission Fee Catalog", catalog_key):
            frappe.db.set_value(
                "Admission Fee Catalog",
                catalog_key,
                {"amount_xof": float(amount), "last_synced_on": synced_on},
                update_modified=False,
            )
        else:
            doc = frappe.get_doc({
                "doctype": "Admission Fee Catalog",
                "catalog_key": catalog_key,
                "program_code": program_code,
                "level_code": level_code,
                "fee_type": "annual",
                "amount_xof": float(amount),
                "last_synced_on": synced_on,
            })
            doc.insert(ignore_permissions=True)
        count += 1
    return count


def _store_scholarship_cap(cap_value, synced_on):
    catalog_key = "SCHOLARSHIP-DEFAULT-cap"
    cap = float(cap_value) if cap_value else 0.50
    if frappe.db.exists("Admission Fee Catalog", catalog_key):
        frappe.db.set_value(
            "Admission Fee Catalog",
            catalog_key,
            {"amount_xof": cap, "last_synced_on": synced_on},
            update_modified=False,
        )
    else:
        doc = frappe.get_doc({
            "doctype": "Admission Fee Catalog",
            "catalog_key": catalog_key,
            "program_code": "SCHOLARSHIP",
            "level_code": "DEFAULT",
            "fee_type": "cap",
            "amount_xof": cap,
            "last_synced_on": synced_on,
        })
        doc.insert(ignore_permissions=True)
