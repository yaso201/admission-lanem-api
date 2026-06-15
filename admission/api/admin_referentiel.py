"""LOT D (SM BACK-OFFICE) — référentiel en mode dégradé (campus indisponible).

DÉCISION D3 : « campus gagne, on prévient ». L'édition manuelle du catalogue est tolérée
UNIQUEMENT en mode dégradé (campus absent), sur des lignes `source=Manuel`. Une ligne déjà
`source=Campus` est VERROUILLÉE (l'invariant « campus = source de vérité » prime). Quand une
sync campus réussie écrase un override manuel, `catalogue_sync` émet une ALERTE (on prévient) ;
le reverrouillage est automatique (la ligne repasse `source=Campus`).

Garde SM, motif obligatoire pour activer le mode dégradé. Réf : SPEC-ADMISSION-SM-BACKOFFICE §4 (D).
"""

import frappe
from frappe.utils import cint

from admission.api._log import log_event
from admission.api.public import _error, _ok

SM_ROLES = ("Admission SM", "System Manager")
SETTINGS = "Admission Settings"
PARCOURS_OPTIONS = ("Prépa", "Licence", "Bachelor", "Double-Diplomation")
_EDITABLE = ("title", "parcours", "partner", "partner_name", "location", "is_active",
             "dd_component_1", "dd_component_2", "dd_affinity")


def _degraded_on():
    return bool(frappe.db.get_single_value(SETTINGS, "degraded_mode"))


@frappe.whitelist(methods=["GET"])
def get_degraded_status():
    """État du mode dégradé + répartition des lignes par source. Lecture, gardée SM."""
    frappe.only_for(SM_ROLES)
    return _ok({
        "degraded_mode": _degraded_on(),
        "manual_count": frappe.db.count("Admission Programme", {"source": "Manuel"}),
        "campus_count": frappe.db.count("Admission Programme", {"source": "Campus"}),
        "total": frappe.db.count("Admission Programme"),
    })


@frappe.whitelist()
def set_degraded_mode(on=None, motif=None):
    """Active/désactive le mode dégradé. Activation = acte sensible → motif obligatoire."""
    frappe.only_for(SM_ROLES)
    target = cint(on)
    if target and (not motif or not str(motif).strip()):
        return _error("MOTIF_REQUIRED", "Le motif est obligatoire pour activer le mode dégradé.", 400)
    frappe.db.set_value(SETTINGS, SETTINGS, "degraded_mode", target)
    log_event("admin_degraded_mode", "success", enabled=target,
              motif=(str(motif).strip()[:140] if motif else ""))
    return _ok({"degraded_mode": bool(target)})


@frappe.whitelist()
def upsert_manual_programme(programme=None):
    """Crée/édite un Admission Programme en mode dégradé (source=Manuel).

    Refusé hors mode dégradé (DEGRADED_OFF) et sur une ligne `source=Campus` (LOCKED_BY_CAMPUS :
    campus = source de vérité — on ne surcharge pas manuellement une ligne synchronisée)."""
    frappe.only_for(SM_ROLES)
    if not _degraded_on():
        return _error("DEGRADED_OFF",
                      "Édition manuelle indisponible : activez d'abord le mode dégradé.", 409)
    if isinstance(programme, str):
        import json
        try:
            programme = json.loads(programme)
        except (ValueError, TypeError):
            return _error("PAYLOAD_INVALID", "Format invalide (objet attendu).", 400)
    if not isinstance(programme, dict):
        return _error("PAYLOAD_INVALID", "Programme attendu (objet).", 400)
    code = (programme.get("programme_code") or "").strip()
    if not code:
        return _error("CODE_REQUIRED", "Le code programme est obligatoire.", 400)
    if programme.get("parcours") not in PARCOURS_OPTIONS:
        return _error("PARCOURS_INVALID",
                      f"Parcours invalide. Autorisés : {', '.join(PARCOURS_OPTIONS)}.", 400)

    exists = frappe.db.exists("Admission Programme", code)
    if exists and frappe.db.get_value("Admission Programme", code, "source") == "Campus":
        return _error("LOCKED_BY_CAMPUS",
                      "Ligne verrouillée : synchronisée depuis campus (source de vérité).", 409)

    values = {f: programme.get(f) for f in _EDITABLE if f in programme}
    values["title"] = values.get("title") or code
    values["is_active"] = 1 if programme.get("is_active", 1) else 0
    values["source"] = "Manuel"

    if exists:
        doc = frappe.get_doc("Admission Programme", code)
        doc.update(values)
        doc.save(ignore_permissions=True)
    else:
        doc = frappe.get_doc({"doctype": "Admission Programme", "programme_code": code, **values})
        doc.insert(ignore_permissions=True)

    from admission.api.public import _invalidate_catalog_cache
    _invalidate_catalog_cache()
    log_event("admin_manual_programme", "success", ref=code)
    return _ok({"programme_code": code, "source": "Manuel"})
