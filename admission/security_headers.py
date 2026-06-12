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

import frappe


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

	return response
