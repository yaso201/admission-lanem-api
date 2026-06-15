"""HEAD-1 — Headers de sécurité HTTP (défense en profondeur, app-wide : API + Desk admin).

Posés via le hook `after_request` (hooks.py). Toutes les valeurs sont surchargeables par
`site_config["admission_security_headers"]` (dict) → configurables PAR ENVIRONNEMENT (dev /
recette / prod diffèrent), jamais en dur côté code.

Garde-fous (PIÈGE : ne pas casser) :
- `setdefault` → ne clobbe PAS un header déjà posé en amont (reverse-proxy / Cloudflare) :
  l'application complète, l'infra peut renforcer.
- CSP par défaut volontairement SÛRE (`frame-ancestors 'self'` : anti-clickjacking, ne
  restreint PAS le chargement des ressources) → ne casse pas le Frappe Desk. À durcir en prod
  via la config, après validation contre le Desk/front réel.
- HSTS seulement en HTTPS (en dev http il serait de toute façon ignoré par le navigateur).
- Une valeur vide/None dans la config DÉSACTIVE le header concerné.

Note : le front candidat est externe (pages.dev) et gère SES propres headers/CSP ; ces
headers protègent le Desk admin + les réponses API de ce bench.

Ref: HEAD-1, AUDIT-GLOBAL (headers absents).
"""

import re

import frappe


def _to_cross_site(set_cookie_value):
    """Réécrit un en-tête Set-Cookie en SameSite=None; Secure (cookies cross-sous-domaine).

    Nécessaire quand le front staff (ex. staff-rec.lanem.bj) et l'API (api-...lanem.bj) sont
    sur des origines différentes : avec SameSite=Lax (défaut Frappe, codé en dur dans auth.py)
    le navigateur N'ENVOIE PAS le cookie `sid` sur les requêtes XHR cross-site → 403 Guest.
    SameSite=None EXIGE Secure ; le navigateur reçoit la réponse en HTTPS (terminaison
    Cloudflare) donc Secure est accepté même si l'origine derrière le tunnel est en http.
    CSRF reste couvert par le token X-Frappe-CSRF-Token (le front l'envoie)."""
    value = re.sub(r"(?i)samesite=(lax|strict|none)", "SameSite=None", set_cookie_value)
    if "samesite=" not in value.lower():
        value += "; SameSite=None"
    if "secure" not in value.lower():
        value += "; Secure"
    return value


DEFAULT_HEADERS = {
	"X-Content-Type-Options": "nosniff",
	"X-Frame-Options": "SAMEORIGIN",
	"Referrer-Policy": "strict-origin-when-cross-origin",
	# CSP sûre par défaut : anti-framing sans restreindre les ressources (ne casse pas le Desk).
	"Content-Security-Policy": "frame-ancestors 'self'",
}

DEFAULT_HSTS = "max-age=31536000; includeSubDomains"


def set_security_headers(response=None, request=None):
	"""Hook after_request (frappe.call(..., response=, request=)). Renvoie la response."""
	if response is None or not hasattr(response, "headers"):
		return response

	overrides = frappe.conf.get("admission_security_headers") or {}

	for header, default in DEFAULT_HEADERS.items():
		value = overrides.get(header, default)
		if value:  # valeur vide/None en config → header désactivé volontairement
			response.headers.setdefault(header, value)

	# HSTS : uniquement sur une requête HTTPS (force HTTPS en prod ; inerte en dev http).
	req = request if request is not None else getattr(frappe.local, "request", None)
	if req is not None and getattr(req, "scheme", "") == "https":
		hsts = overrides.get("Strict-Transport-Security", DEFAULT_HSTS)
		if hsts:
			response.headers.setdefault("Strict-Transport-Security", hsts)

	# Cookies cross-sous-domaine (front staff sur une autre origine que l'API).
	# Gated par `cross_site_session` (site_config) → DEV same-site INTACT (défaut OFF).
	if frappe.conf.get("cross_site_session") and hasattr(response.headers, "getlist"):
		cookies = response.headers.getlist("Set-Cookie")
		if cookies:
			del response.headers["Set-Cookie"]
			for c in cookies:
				response.headers.add("Set-Cookie", _to_cross_site(c))

	return response
