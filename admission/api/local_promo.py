"""PROMO-LOCALE (DEC-343/344/345) — campagnes locales a prix cibles + code promo.

Autonomie sans UF : ces tables sont locales a admission, JAMAIS touchees par
scholarship_sync. Bornes de fenetre INCLUSES. La promo ne touche QUE la scolarite
annuelle (DEC-230). Deux pans : cumulable (multiplicatif sur le prix campagne) et
non-cumulable (best-of : min(prix_campagne, plein_tarif x (1 - taux))).
"""

from __future__ import annotations

import json
from datetime import date

import frappe
from frappe.rate_limiter import rate_limit
from frappe.utils import getdate


def _normalize_code(code):
	return (code or "").strip().upper()


def _campaign_price_for(programme_code, level_code, on_date):
	"""Prix cible de la campagne active a on_date pour (programme, niveau).

	Fallback niveau DEFAULT (meme convention que le catalogue 3D, DEC-234).
	Retourne (name, label, price) ou (None, None, None).
	"""
	campaigns = frappe.get_all(
		"Admission Local Promotion",
		filters={"active": 1, "start_date": ["<=", on_date], "end_date": [">=", on_date]},
		fields=["name", "label"], limit=10,
	)
	level = level_code or "DEFAULT"
	for c in campaigns:
		rows = frappe.get_all(
			"Admission Local Promotion Price",
			filters={"parent": c.name, "program_code": programme_code},
			fields=["level_code", "promo_annual_xof"],
		)
		exact = next((r for r in rows if (r.level_code or "DEFAULT") == level), None)
		fallback = next((r for r in rows if (r.level_code or "DEFAULT") == "DEFAULT"), None)
		hit = exact or fallback
		if hit:
			return c.name, c.label, float(hit.promo_annual_xof)
	return None, None, None


def _resolve_code(code):
	"""Ligne du code promo ACTIF (fenetre non verifiee ici — voir _code_valid_on)."""
	norm = _normalize_code(code)
	if not norm:
		return None
	rows = frappe.get_all(
		"Admission Promo Code",
		filters={"name": norm, "active": 1},
		fields=["name", "rate", "cumulable", "start_date", "end_date", "active"], limit=1,
	)
	return rows[0] if rows else None


def _code_valid_on(row, on_date):
	"""Bornes INCLUSES (DEC-344)."""
	return bool(row) and getdate(row["start_date"]) <= getdate(on_date) <= getdate(row["end_date"])


def compute_local_price(programme_code, level_code, on_date, code=None):
	"""Moteur DEC-344. base_effective = prix campagne si active, sinon plein tarif.

	cumulable     -> final = base_effective x (1 - taux)
	non-cumulable -> final = min(base_effective, plein_tarif x (1 - taux))  [best-of]
	sans code     -> final = base_effective
	"""
	from admission.api.public import _resolve_fee_from_catalog
	base = _resolve_fee_from_catalog(programme_code, "annual", level_code)
	base = float(base) if base else None
	camp_name, camp_label, camp_price = _campaign_price_for(programme_code, level_code, on_date)
	row = _resolve_code(code)
	code_ok = _code_valid_on(row, on_date)

	result = {
		"base_xof": base,
		"campaign": camp_name, "campaign_label": camp_label, "campaign_price_xof": camp_price,
		"code": row["name"] if row else None,
		"code_rate": float(row["rate"]) if row else 0.0,
		"code_applied": False, "cumulable": bool(row and row["cumulable"]),
		"final_annual_xof": None,
	}
	if base is None:
		return result  # catalog miss : jamais de crash, final indeterminable

	effective = camp_price if camp_price is not None else base
	final = effective
	if code_ok:
		with_code = base * (1.0 - float(row["rate"]))
		if row["cumulable"]:
			final = effective * (1.0 - float(row["rate"]))
			result["code_applied"] = True
		elif with_code < effective:  # best-of : le code ne s'applique que s'il fait mieux
			final = with_code
			result["code_applied"] = True
	result["final_annual_xof"] = round(max(final, 0.0), 2)
	return result
