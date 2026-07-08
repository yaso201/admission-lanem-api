"""STAFF-LOGIN-RECOVERY — self-service « mot de passe oublié » du front staff (pont middle).

Pourquoi un pont ? Le natif Frappe `user.reset_password` (module, guest) ÉNUMÈRE (« not
found »/404, « disabled », « not allowed ») — inutilisable tel quel. Ce pont réutilise les
briques sûres du natif (génération de clé expirante/usage-unique via `User.reset_password`
— `_reset_password` en 15.111, patron getattr — ; la DÉFINITION passe, elle, directement
par le natif `update_password` : politique de force + expiry + usage unique) et neutralise
l'énumération :

- réponse STRICTEMENT UNIFORME (même corps, même statut) que le compte existe, soit
  inconnu, désactivé, non-staff, protégé ou la conf absente ;
- mail ENQUEUED (jamais now=True) → la réponse ne bloque pas sur SMTP (timing uniforme) ;
- rate-limit 5/h/IP (clé = request_ip = vrai client derrière Cloudflare via XFF[0]) ;
- lien vers le FRONT staff (`staff_portal_url`/reinitialisation?key=…) — jamais /app
  (cohérent desk-lock) ; la clé n'est JAMAIS loggée ; traces ref = hash e-mail (0 PII).
"""

import hashlib

import frappe
from frappe.rate_limiter import rate_limit

from admission.api._log import log_event
from admission.api.public import _ok

# Rôles ouvrant droit au self-service (staff du portail management). Administrator jamais.
_STAFF_ROLES = frozenset({
    "Admission Administratif", "Admission Responsable",
    "Admission Direction", "Admission Finance", "Admission SM",
})

_SUBJECT = "Réinitialisation de votre mot de passe — LaNEM Management"


def _ref(email):
    """Ref non-PII corrélable (hash court) — jamais l'e-mail en clair dans les traces."""
    return hashlib.sha256((email or "").strip().lower().encode()).hexdigest()[:12]


def _uniform_response():
    """LA réponse — unique, quelle que soit l'issue interne (anti-énumération)."""
    return _ok({"sent": True,
                "message": "Si un compte staff existe pour cette adresse, "
                           "un lien de réinitialisation a été envoyé."})


@frappe.whitelist(allow_guest=True, methods=["POST"])
@rate_limit(limit=5, seconds=60 * 60)
def request_password_reset(email=None):
    """Demande de réinitialisation (guest, 5/h/IP). Toujours la même réponse."""
    email = (email or frappe.form_dict.get("email") or "").strip().lower()
    log_event("password_reset", "requested", ref=_ref(email))
    try:
        _maybe_send(email)
    except Exception:
        # Jamais de 500 révélateur : l'échec interne est tracé (sans clé, sans e-mail),
        # la réponse reste uniforme.
        log_event("password_reset", "internal_error", ref=_ref(email), level="error")
    return _uniform_response()


def _maybe_send(email):
    """Envoie le lien SEULEMENT à un compte staff actif — silencieux dans tous les autres cas."""
    if not email or not frappe.db.exists("User", email):
        return
    user = frappe.get_doc("User", email)
    if user.name == "Administrator" or not int(user.enabled or 0):
        return
    if not (_STAFF_ROLES & set(frappe.get_roles(email))):
        return
    base = (frappe.conf.get("staff_portal_url") or "").rstrip("/")
    if not base:
        # Fail-safe : sans URL front on n'envoie rien (jamais de lien /app) — visible ops.
        log_event("password_reset", "misconfigured", ref=_ref(email),
                  error="staff_portal_url absent", level="error")
        return
    # Brique native : génère + stocke la clé (hashée, horodatée, expirante, usage unique).
    # 15.103 = reset_password / 15.111 = _reset_password (V-LEARN, patron admin_staff).
    generate = getattr(user, "reset_password", None) or getattr(user, "_reset_password", None)
    native_link = generate(send_email=False)
    key = native_link.split("key=", 1)[-1].split("&", 1)[0]
    link = f"{base}/reinitialisation?key={key}"
    # ENQUEUED (pas now=True) : la réponse HTTP ne dépend pas du SMTP (timing uniforme).
    frappe.sendmail(
        recipients=[email],
        subject=_SUBJECT,
        message=(
            "<p>Bonjour,</p>"
            "<p>Une réinitialisation du mot de passe de votre compte staff LaNEM a été "
            "demandée. Si vous n'êtes pas à l'origine de cette demande, ignorez ce message "
            "— votre mot de passe actuel reste valable.</p>"
            f"<p><a href=\"{link}\" style=\"display:inline-block;background:#5B3FA8;"
            "color:#fff;padding:10px 18px;border-radius:8px;text-decoration:none;"
            "font-weight:600\">Définir un nouveau mot de passe</a></p>"
            "<p style=\"font-size:12px;color:#666\">Ce lien est à usage unique et expire "
            "automatiquement. Ne le transférez à personne — l'équipe LaNEM ne vous "
            "demandera jamais votre mot de passe.</p>"
        ),
    )
    log_event("password_reset", "sent", ref=_ref(email))
