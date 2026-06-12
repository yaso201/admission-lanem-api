"""SOCLE-0-CONSULT — cloisonnement de consultation générique, débranché par défaut.

Mécanisme row-level branché via les hooks `permission_query_conditions` + `has_permission`
sur Admission Applicant. Lit un flag de config (Admission Settings, single) :
  - OFF (défaut) → aucune restriction : tout staff voit tous les dossiers (MVP, petite équipe).
  - ON  → cloisonne selon un AXE paramétrable (un fieldname d'Admission Applicant : status,
          session, programme_code, …) + un mapping rôle→[valeurs autorisées].

Sécurité : System Manager / Administrator bypass ; fail-closed si ON sans scope ou axe invalide ;
axe validé contre le meta + valeurs échappées (anti-injection à deux niveaux).

Field-level financier — NON appliqué au MVP. Pour l'activer plus tard, par champ
(acompte_xof, validated_scholarships, promo_code/rate/captured_date) :
  1) poser "permlevel": 1 sur le champ dans admission_applicant.json ;
  2) AJOUTER des DocPerm "permlevel": 1 (read) pour les rôles autorisés à voir le financier.
  ⚠️ Sans l'étape 2, un champ permlevel>0 est masqué pour TOUS (y compris System Manager).
"""

import json

import frappe

ADMISSION_APPLICANT = "Admission Applicant"
SETTINGS = "Admission Settings"
TABLE = "`tabAdmission Applicant`"
BYPASS_ROLES = {"System Manager", "Administrator"}

# conditions SQL "fail-closed" : ne matchent aucun dossier
_NO_SCOPE = f"{TABLE}.`name` = '__no_scope__'"
_MISCONFIGURED = f"{TABLE}.`name` = '__cloisonnement_misconfigured__'"


def _is_bypass(user):
    if user == "Administrator":
        return True
    return bool(BYPASS_ROLES & set(frappe.get_roles(user)))


def _get_settings():
    """Renvoie {axis, scopes} si le cloisonnement est ACTIF, sinon None (OFF)."""
    if not frappe.db.get_single_value(SETTINGS, "consultation_cloisonnee"):
        return None
    axis = frappe.db.get_single_value(SETTINGS, "consultation_axis") or "status"
    raw = frappe.db.get_single_value(SETTINGS, "consultation_role_scopes")
    try:
        scopes = json.loads(raw) if isinstance(raw, str) and raw else (raw or {})
    except (ValueError, TypeError):
        scopes = {}
    if not isinstance(scopes, dict):
        scopes = {}
    return {"axis": axis, "scopes": scopes}


def _valid_axis(axis):
    """Anti-injection : l'axe doit être un fieldname réel d'Admission Applicant."""
    if not axis:
        return None
    if axis == "name" or frappe.get_meta(ADMISSION_APPLICANT).has_field(axis):
        return axis
    return None


def _allowed_values(user, scopes):
    """(matched, allowed) — matched=True si au moins un rôle de l'utilisateur est mappé."""
    user_roles = set(frappe.get_roles(user))
    allowed = set()
    matched = False
    for role, values in scopes.items():
        if role in user_roles and isinstance(values, (list, tuple)):
            matched = True
            allowed.update(str(v) for v in values)
    return matched, allowed


def get_permission_query_conditions(user=None, doctype=None):
    """Row-level : restreint la liste des Admission Applicant visibles. "" = aucune restriction."""
    user = user or frappe.session.user
    if _is_bypass(user):
        return ""
    settings = _get_settings()
    if not settings:
        return ""  # OFF → tout visible (MVP)
    axis = _valid_axis(settings["axis"])
    if not axis:
        frappe.log_error(f"Cloisonnement: axe invalide {settings['axis']!r}", "SOCLE-0-CONSULT")
        return _MISCONFIGURED  # fail-closed
    matched, allowed = _allowed_values(user, settings["scopes"])
    if not matched or not allowed:
        return _NO_SCOPE  # fail-closed
    values = ", ".join(frappe.db.escape(v) for v in sorted(allowed))
    return f"{TABLE}.`{axis}` in ({values})"


def has_permission(doc=None, ptype=None, user=None, debug=False):
    """Garde par document, tous ptypes. None = défère aux perms normales ; False = refuse."""
    user = user or frappe.session.user
    if _is_bypass(user):
        return None
    settings = _get_settings()
    if not settings:
        return None  # OFF → défère
    axis = _valid_axis(settings["axis"])
    if not axis:
        return False  # misconfig → fail-closed
    matched, allowed = _allowed_values(user, settings["scopes"])
    if not matched or not allowed:
        return False
    if doc is None:
        return None
    return None if str(doc.get(axis)) in allowed else False
