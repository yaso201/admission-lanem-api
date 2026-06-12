"""Helpers de configuration d'intégration (campus / UF) — version unique centralisée.

HELPERS-DEDUP : `_get_campus_config` (×3) et `_get_uf_config` (×4) étaient dupliqués À
L'IDENTIQUE dans public / bridge / notify_uf / level_sync / fee_catalog_sync / scholarship_sync.
Centralisés ici (refactoring ISO-FONCTIONNEL : comportement strictement inchangé — mêmes clés
`frappe.conf`, mêmes défauts, même gestion config absente). Les consommateurs font
`from admission.api._config import _get_campus_config, _get_uf_config`.
"""

import frappe


def _get_campus_config():
	url = frappe.conf.get("campus_base_url")
	if not url:
		return None
	token = frappe.conf.get("campus_api_token") or ""
	if not token:
		return None
	return {"url": url, "token": token}


def _get_uf_config():
	url = frappe.conf.get("uf_backoffice_url")
	if not url:
		return None
	return {
		"url": url,
		"api_key": frappe.conf.get("uf_api_key") or "",
		"api_secret": frappe.conf.get("uf_api_secret") or "",
	}


def _pii_transport_allowed(url, context=""):
	"""DAT-2 : interdit l'envoi de PII en clair (http://) hors developer_mode.

	https → OK. http/autre hors dev → log_error (Error Log natif, trace OBS-1) + False
	(l'appelant n'envoie pas). En dev → toléré (campus/UF en local). La sécurité du
	transport reste une dépendance OPS : base URLs d'intégration en https hors dev.
	"""
	if url and url.lower().startswith("https://"):
		return True
	if frappe.conf.get("developer_mode"):
		return True
	frappe.log_error(
		title="DAT-2 : envoi PII bloqué (transport non chiffré)",
		message=f"Refus d'envoyer de la PII vers une URL non-https. context={context} url={url}",
	)
	return False
