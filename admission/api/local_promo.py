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


def _validate_promo_code_impl(code, programme_code, level_code):
	"""Coeur testable (hors decorateurs). Reponse invalide UNIQUE (anti-enumeration)."""
	invalid = {"valid": False, "message": "Code invalide ou expire."}
	row = _resolve_code(code)
	today = date.today()
	if not row or not _code_valid_on(row, today):
		return invalid
	est = compute_local_price(programme_code, level_code, today, code=row["name"])
	return {
		"valid": True, "code": row["name"], "rate": float(row["rate"]),
		"cumulable": bool(row["cumulable"]), "end_date": str(row["end_date"]),
		"estimation": est,
	}


@frappe.whitelist(allow_guest=True, methods=["GET"])
@rate_limit(limit=30, seconds=60 * 60)
def validate_promo_code(code=None, session=None, programme=None, level_code=None):
	"""DEC-344 : validation publique plafonnee. Resolution programme via session (comme get_frais)."""
	from admission.api.public import _ok, _session_doc
	programme_code = programme
	if session and not programme_code:
		sdoc = _session_doc(session)
		programme_code = sdoc.programme_code if sdoc else None
	return _ok(_validate_promo_code_impl(code, programme_code, level_code))


def _set_promo_code_impl(dossier_id, token, code):
	"""Coeur testable. Un code se pose/efface librement AVANT le gel, jamais apres (DEC-345)."""
	from admission.api.public import _error, _get_applicant
	applicant = _get_applicant(dossier_id, token)
	if getattr(applicant, "local_promo_snapshot", None):
		return _error("PROMO_LOCKED",
		              "Le droit promo est deja fige (frais 1 confirme).", 409)
	norm = _normalize_code(code)
	frappe.db.set_value("Admission Applicant", applicant.name,
	                    "entered_promo_code", norm, update_modified=False)
	validation = _validate_promo_code_impl(
		norm, applicant.programme_code, getattr(applicant, "level_code", None)) if norm else None
	return {"ok": True, "entered_promo_code": norm, "validation": validation}


@frappe.whitelist(allow_guest=True, methods=["POST"])
@rate_limit(key="dossier_id", limit=10, seconds=60 * 60)
def set_promo_code(dossier_id=None, token=None, code=None):
	"""DEC-344/345 : persiste le code saisi (auth token dossier, pattern DEC-241)."""
	from admission.api.public import _ok, _value
	dossier_id = dossier_id or _value("dossier_id")
	token = token or _value("token")
	code = code if code is not None else _value("code")
	res = _set_promo_code_impl(dossier_id, token, code)
	if isinstance(res, dict) and res.get("ok"):
		return _ok({"entered_promo_code": res["entered_promo_code"],
		            "validation": res["validation"]})
	return res


def capture_local_promo_if_eligible(applicant):
	"""DEC-345 : gel du droit promo LOCAL au frais 1 confirme. Idempotent.

	Validite reevaluee A LA DATE DE CONFIRMATION (un code saisi mais expire est perdu).
	N'ecrit que si une campagne ou un code s'applique — sinon plein tarif, pas de snapshot.
	Champs SEPARES du miroir UF (promo_code/promo_rate restent DEC-228).
	"""
	if getattr(applicant, "local_promo_snapshot", None):
		return
	computed = compute_local_price(
		applicant.programme_code, getattr(applicant, "level_code", None),
		date.today(), code=getattr(applicant, "entered_promo_code", None))
	if not computed["campaign"] and not computed["code_applied"]:
		return
	snapshot = dict(computed, captured_date=str(date.today()))
	applicant.local_promo_snapshot = json.dumps(snapshot)
	applicant.final_annual_xof = computed["final_annual_xof"]
	applicant.save(ignore_permissions=True)
	frappe.logger("promo_capture").info(
		f"Local promo captured for {applicant.name}: final={computed['final_annual_xof']}")


def build_promotion_locale_section(programme_code, level_code):
	"""Section get_frais (DEC-343) : campagne active pour (programme, niveau), ou None."""
	today = date.today()
	name, label, price = _campaign_price_for(programme_code, level_code, today)
	if not name:
		return None
	rows = frappe.get_all("Admission Local Promotion",
	                      filters={"name": name}, fields=["name", "label", "end_date"], limit=1)
	end = str(rows[0]["end_date"]) if rows else None
	return {"campaign": name, "label": label, "end_date": end, "promo_annual_xof": price}


# Ordre d'affichage FIXE des familles au bandeau (DEC-343).
_BANNER_PARCOURS_ORDER = ("Licence", "Bachelor", "Double-Diplomation")


def _banner_data_uncached(on_date):
	"""Bandeau accueil : agrege la campagne active PAR FAMILLE (parcours du programme).

	Plein tarif = premiere ligne `annual` du catalogue pour le programme (identique au
	sein d'une famille ; ⚠️ PROD n'a PAS de lignes DEFAULT — ne jamais supposer un niveau).
	Une ligne sans base catalogue est ECARTEE (jamais de colonne sans plein tarif).
	"""
	campaigns = frappe.get_all(
		"Admission Local Promotion",
		filters={"active": 1, "start_date": ["<=", on_date], "end_date": [">=", on_date]},
		fields=["name", "label", "end_date"], limit=1,
	)
	if not campaigns:
		return {"active": False}
	camp = campaigns[0]
	lines = frappe.get_all(
		"Admission Local Promotion Price",
		filters={"parent": camp["name"]}, fields=["program_code", "promo_annual_xof"],
	)
	parcours_map = {p["name"]: p["parcours"] for p in frappe.get_all(
		"Admission Programme", fields=["name", "parcours"])}
	familles = {}
	for row in lines:
		parcours = parcours_map.get(row["program_code"])
		if not parcours or parcours in familles:
			continue
		base_rows = frappe.get_all(
			"Admission Fee Catalog",
			filters={"program_code": row["program_code"], "fee_type": "annual"},
			fields=["amount_xof"], limit=1,
		)
		if not base_rows:
			continue  # pas de plein tarif -> pas de colonne (jamais de barre vide)
		familles[parcours] = {
			"parcours": parcours,
			"plein_xof": float(base_rows[0]["amount_xof"]),
			"promo_xof": float(row["promo_annual_xof"]),
		}
	ordered = [familles[p] for p in _BANNER_PARCOURS_ORDER if p in familles]
	ordered += [v for k, v in familles.items() if k not in _BANNER_PARCOURS_ORDER]
	return {"active": True, "label": camp["label"], "end_date": str(camp["end_date"]),
	        "familles": ordered}


@frappe.whitelist(allow_guest=True, methods=["GET"])
@rate_limit(limit=60, seconds=60 * 60)
def get_active_local_promotion():
	"""DEC-343 : resume public de la campagne active pour le bandeau d'accueil.

	Cache date (anti-perime a minuit) + invalidation on_update des doctypes campagne.
	"""
	from admission.api.public import _cache_get_or_set, _ok
	today = date.today()
	data = _cache_get_or_set(
		f"admission:local_promo_banner:{today}", 24 * 60 * 60,
		lambda: _banner_data_uncached(today),
	)
	return _ok(data)
