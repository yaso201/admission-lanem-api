"""LOT E (SM BACK-OFFICE) — recovery exploitation.

RÉEXPOSE (zéro logique nouvelle) les rattrapages déjà déclenchés par le scheduler, pour que le
SM ait un BOUTON (au lieu de SSH + bench) : re-drive UF, re-drive pont campus, expiration des
Pending online orphelins. + un état (compteurs) réutilisant les mêmes filtres.

Les fonctions sous-jacentes sont idempotentes. Garde SM. Réf : SPEC-ADMISSION-SM-BACKOFFICE §4 (E).
"""

import frappe
from frappe.utils import add_to_date, now_datetime

from admission.api._log import log_event
from admission.api.public import _ok

SM_ROLES = ("Admission SM", "System Manager")
PENDING_STALE_HOURS = 48


@frappe.whitelist(methods=["GET"])
def get_ops_health():
    """Compteurs d'exploitation (mêmes filtres que les redrive) — aide à la décision SM."""
    frappe.only_for(SM_ROLES)
    uf_unreplicated = frappe.db.count(
        "Applicant Fee Payment", {"payment_status": "Confirmed", "uf_notified": 0})
    bridge_pending = frappe.db.count(
        "Admission Applicant", {"status": "INS", "anonymized": ["!=", 1], "bridge_notified": ["!=", 1]})
    cutoff = add_to_date(now_datetime(), hours=-PENDING_STALE_HOURS)
    pending_online_stale = frappe.db.count(
        "Applicant Fee Payment",
        {"payment_status": "Pending", "payment_mode": "Online", "creation": ["<", cutoff]})
    return _ok({
        "uf_unreplicated": uf_unreplicated,
        "bridge_pending": bridge_pending,
        "pending_online_stale": pending_online_stale,
    })


@frappe.whitelist()
def redrive_uf_now():
    """Relance le re-POST des paiements Confirmed non notifiés à UF (idempotent)."""
    frappe.only_for(SM_ROLES)
    from admission.api.notify_uf import redrive_uf_notifications
    result = redrive_uf_notifications()
    log_event("admin_redrive_uf", "success", **{k: result[k] for k in result if isinstance(result[k], (int, str))})
    return _ok(result)


@frappe.whitelist()
def redrive_bridge_now():
    """Relance le pont INS vers le campus pour les dossiers non acquittés (idempotent)."""
    frappe.only_for(SM_ROLES)
    from admission.api.bridge import redrive_bridge_notifications
    result = redrive_bridge_notifications()
    log_event("admin_redrive_bridge", "success", **{k: result[k] for k in result if isinstance(result[k], (int, str))})
    return _ok(result)


@frappe.whitelist()
def expire_pending_now(older_than_hours=PENDING_STALE_HOURS):
    """Passe en Rejected les Pending online orphelins au-delà du délai (idempotent)."""
    frappe.only_for(SM_ROLES)
    from admission.api.public import expire_stale_online_pending
    count = expire_stale_online_pending(older_than_hours=int(older_than_hours or PENDING_STALE_HOURS))
    log_event("admin_expire_pending", "success", expired=count)
    return _ok({"expired": count})
