"""LOT A (SM BACK-OFFICE) — comptes & identité du personnel.

Capacité PRIVILÉGIÉE encadrée (SPEC §3.3) : créer un User et lui assigner un rôle sont des
actes privilégiés, rendus possibles UNIQUEMENT via ces endpoints (garde SM + `ignore_permissions`
interne), avec **liste blanche** des rôles assignables = les 4 rôles admission seulement. Le rôle
`Admission SM` n'est JAMAIS assignable via l'UI ; les comptes protégés (SM / System Manager /
Administrator) ne sont jamais gérés ici.

Jamais de mot de passe en clair : le reset envoie le **lien natif** Frappe par e-mail.
Trace : `log_event` (ref non-PII = hash de l'e-mail) + Activity Log natif (trace identifiante).

Réf : SPEC-ADMISSION-SM-BACKOFFICE §4 (A).
"""

import hashlib

import frappe
from frappe.utils import cint, validate_email_address

from admission.api._log import log_event
from admission.api.public import _error, _ok

SM_ROLES = ("Admission SM", "System Manager")
ASSIGNABLE_ROLES = (
    "Admission Administratif",
    "Admission Responsable",
    "Admission Direction",
    "Admission Finance",
)
PROTECTED_ROLES = {"System Manager", "Admission SM", "Administrator"}


def _norm(email):
    return (email or "").strip().lower()


def _ref(email):
    """Ref non-PII corrélable (hash court) — pas d'e-mail en clair dans le log applicatif."""
    return hashlib.sha256(_norm(email).encode()).hexdigest()[:12]


def _is_protected(email):
    """True si le compte porte un rôle protégé (jamais géré via cette UI)."""
    if email == "Administrator":
        return True
    return bool(PROTECTED_ROLES & set(frappe.get_roles(email)))


def _guard_manageable(email):
    """Retourne un _error si le compte est inconnu ou protégé, sinon None."""
    if not frappe.db.exists("User", email):
        return _error("USER_NOT_FOUND", "Compte inconnu.", 404)
    if _is_protected(email):
        return _error("PROTECTED_ACCOUNT",
                      "Ce compte privilégié ne peut pas être géré via cette interface.", 403)
    return None


@frappe.whitelist(methods=["GET"])
def list_staff():
    """Liste des comptes staff (porteurs d'un rôle admission). Lecture, gardée SM."""
    frappe.only_for(SM_ROLES)
    by_user = {}
    for r in frappe.get_all("Has Role", filters={"role": ["in", ASSIGNABLE_ROLES]},
                            fields=["parent", "role"]):
        by_user.setdefault(r.parent, []).append(r.role)
    staff = []
    for user, roles in by_user.items():
        u = frappe.db.get_value(
            "User", user, ["enabled", "full_name", "last_login", "user_type"], as_dict=True)
        if not u or u.user_type != "System User":
            continue
        staff.append({
            "email": user,
            "full_name": u.full_name or user,
            "roles": sorted(roles),
            "enabled": bool(u.enabled),
            "last_login": str(u.last_login or ""),
        })
    staff.sort(key=lambda x: x["full_name"].lower())
    return _ok({"staff": staff, "total": len(staff)})


@frappe.whitelist()
def create_staff(full_name=None, email=None, role=None):
    """Crée un compte staff (System User) + 1 rôle ∈ liste blanche. Invitation native par e-mail."""
    frappe.only_for(SM_ROLES)
    email = _norm(email)
    if not email or not validate_email_address(email):
        return _error("EMAIL_INVALID", "Adresse e-mail invalide.", 400)
    if not full_name or not str(full_name).strip():
        return _error("NAME_REQUIRED", "Le nom complet est obligatoire.", 400)
    if role not in ASSIGNABLE_ROLES:
        return _error("ROLE_NOT_ALLOWED",
                      f"Rôle non assignable. Autorisés : {', '.join(ASSIGNABLE_ROLES)}.", 400)
    if frappe.db.exists("User", email):
        return _error("USER_EXISTS", "Un compte existe déjà pour cette adresse.", 409)
    parts = str(full_name).strip().split(" ", 1)
    user = frappe.get_doc({
        "doctype": "User",
        "email": email,
        "first_name": parts[0],
        "last_name": parts[1] if len(parts) > 1 else "",
        "user_type": "System User",
        "send_welcome_email": 1,
        "enabled": 1,
    })
    user.insert(ignore_permissions=True)
    user.add_roles(role)
    log_event("admin_create_staff", "success", ref=_ref(email), role=role)
    return _ok({"email": email, "role": role, "enabled": True})


@frappe.whitelist()
def set_staff_role(email=None, role=None):
    """Change le rôle d'un compte staff (remplace ses rôles admission par `role`). Liste blanche."""
    frappe.only_for(SM_ROLES)
    email = _norm(email)
    guard = _guard_manageable(email)
    if guard:
        return guard
    if role not in ASSIGNABLE_ROLES:
        return _error("ROLE_NOT_ALLOWED",
                      f"Rôle non assignable. Autorisés : {', '.join(ASSIGNABLE_ROLES)}.", 400)
    user = frappe.get_doc("User", email)
    current = [r for r in frappe.get_roles(email) if r in ASSIGNABLE_ROLES]
    if current:
        user.remove_roles(*current)
    user.add_roles(role)
    log_event("admin_set_staff_role", "success", ref=_ref(email), role=role)
    return _ok({"email": email, "role": role})


@frappe.whitelist()
def reset_staff_password(email=None, motif=None):
    """Déclenche le lien de réinitialisation NATIF (mailé). Acte sensible → motif obligatoire."""
    frappe.only_for(SM_ROLES)
    email = _norm(email)
    guard = _guard_manageable(email)
    if guard:
        return guard
    if not motif or not str(motif).strip():
        return _error("MOTIF_REQUIRED", "Le motif est obligatoire.", 400)
    user = frappe.get_doc("User", email)
    # Lien mailé ; AUCUN mot de passe en clair. Compat versions Frappe : la méthode s'appelle
    # `reset_password` (frappe récents) OU `_reset_password` (15.111.x). On prend ce qui existe.
    send_reset = getattr(user, "reset_password", None) or getattr(user, "_reset_password", None)
    send_reset(send_email=True)
    log_event("admin_reset_password", "success", ref=_ref(email), motif=str(motif).strip()[:140])
    return _ok({"email": email, "sent": True})


@frappe.whitelist()
def set_staff_enabled(email=None, enabled=None, motif=None):
    """Suspend (0) / réactive (1) un compte. Désactivation = acte sensible → motif obligatoire.
    Interdit de se désactiver soi-même. Désactivation via save() → purge des sessions actives."""
    frappe.only_for(SM_ROLES)
    email = _norm(email)
    guard = _guard_manageable(email)
    if guard:
        return guard
    if email == _norm(frappe.session.user):
        return _error("SELF_DISABLE_FORBIDDEN", "Vous ne pouvez pas modifier votre propre statut.", 403)
    target = cint(enabled)
    if target == 0 and (not motif or not str(motif).strip()):
        return _error("MOTIF_REQUIRED", "Le motif de désactivation est obligatoire.", 400)
    user = frappe.get_doc("User", email)
    user.enabled = target
    user.save(ignore_permissions=True)  # on_update → purge des sessions si désactivé
    log_event("admin_set_staff_enabled", "success", ref=_ref(email), enabled=target)
    return _ok({"email": email, "enabled": bool(target)})
