"""LOT G (SM BACK-OFFICE) — données & conformité.

Gestes de conformité initiés par le SM (et non par le tunnel candidat / le scheduler) :
 - admin_anonymize : effacement sur demande/injonction d'un dossier précis (IRRÉVERSIBLE) ;
 - run_retention_now : passe de rétention à la demande ;
 - get_audit_log : lecture du journal d'audit natif (Activity Log).

Wrappers des fonctions existantes (l'anonymiseur `retention.anonymize_applicant` n'est PAS
réécrit : carve-out consent/compta préservé). Garde SM, motif obligatoire sur l'effacement,
double confirmation portée par l'UI. Réf : SPEC-ADMISSION-SM-BACKOFFICE §4 (G).
"""

import frappe

from admission.api._log import log_event
from admission.api.public import _error, _ok

SM_ROLES = ("Admission SM", "System Manager")


@frappe.whitelist()
def admin_anonymize(dossier_id=None, motif=None):
    """Anonymisation sélective d'un dossier sur demande (RGPD, loi 2017-20). IRRÉVERSIBLE.

    Motif obligatoire ; idempotent (déjà anonymisé → no-op). Délègue à l'anonymiseur existant
    (PII scrubbée ; consentement art. 29 + justificatifs comptables OHADA CONSERVÉS dé-liés)."""
    frappe.only_for(SM_ROLES)
    if not dossier_id or not frappe.db.exists("Admission Applicant", dossier_id):
        return _error("INVALID_DOSSIER", "Dossier inconnu.", 404)
    if not motif or not str(motif).strip():
        return _error("MOTIF_REQUIRED", "Le motif est obligatoire (acte irréversible).", 400)
    if frappe.db.get_value("Admission Applicant", dossier_id, "anonymized"):
        return _ok({"dossier_id": dossier_id, "anonymized": True, "idempotent": True})
    from admission.api.retention import anonymize_applicant
    result = anonymize_applicant(dossier_id)
    log_event("admin_anonymize", "success", dossier_id=dossier_id,
              files_deleted=result.get("files_deleted", 0), motif=str(motif).strip()[:140])
    return _ok({"dossier_id": dossier_id, "anonymized": True,
                "files_deleted": result.get("files_deleted", 0)})


@frappe.whitelist()
def run_retention_now():
    """Lance une passe de rétention à la demande (purge OTP + anonymisation BRO/terminaux)."""
    frappe.only_for(SM_ROLES)
    from admission.api.retention import scheduled_retention_run
    summary = scheduled_retention_run()
    log_event("admin_run_retention", "success", **{k: summary[k] for k in summary if isinstance(summary[k], int)})
    return _ok(summary)


@frappe.whitelist(methods=["GET"])
def get_audit_log(limit=100, user=None):
    """Journal d'audit natif (Activity Log : connexions, modifications). Lecture, gardée SM."""
    frappe.only_for(SM_ROLES)
    filters = {}
    if user:
        filters["user"] = user
    rows = frappe.get_all(
        "Activity Log",
        filters=filters or None,
        fields=["creation", "user", "operation", "subject", "status",
                "reference_doctype", "reference_name"],
        order_by="creation desc",
        limit_page_length=min(int(limit or 100), 500),
    )
    entries = [{
        "at": str(r.creation),
        "user": r.user,
        "operation": r.operation or "",
        "subject": r.subject or "",
        "status": r.status or "",
        "ref": f"{r.reference_doctype}/{r.reference_name}" if r.reference_doctype else "",
    } for r in rows]
    return _ok({"entries": entries, "total": len(entries)})
