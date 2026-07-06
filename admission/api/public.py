import hashlib
import hmac
import json
import re
import secrets
from datetime import date, timedelta

import requests

import frappe
from frappe.rate_limiter import rate_limit
from frappe.utils import add_days, add_to_date, get_datetime, getdate, now_datetime, validate_email_address

from admission.api._config import _get_campus_config, _pii_transport_allowed  # HELPERS-DEDUP + DAT-2 garde transport
from admission.api._log import log_event  # OBS-2 : log structuré + corrélation dossier_id


# Pieces UNIVERSELLES (tous profils, REQUISES). Slot identite = 1 piece, 3 types acceptes
# (CNI/passeport/CIP) — porte par le libelle ; le selecteur front est informationnel, le type
# choisi n'est PAS persiste (decision PC1 voie b, schema non touche).
_PIECES_UNIVERSELLES = [
	{"code": "identite", "label": "Piece d'identite (CNI, passeport ou CIP)", "requise": True},
	{"code": "photo", "label": "Photo d'identite", "requise": True},
	{"code": "cv", "label": "Curriculum Vitae", "requise": True},
	{"code": "motivation", "label": "Lettre de motivation", "requise": True},
]

# Pieces academiques PARTAGEES par bac_attente ET bac_annee (meme liste — pas de logique de date :
# diplome/releve bac OPTIONNELS couvrent avant/apres resultats sans parametre de date). Le diplome
# reste exige a posteriori par la gate DIPLOMA_MISSING a la verification (C1-ACO, DEC-214), pas a la soumission.
_PIECES_ATTENTE_ANNEE = [
	{"code": "releves_terminale", "label": "Releves de notes de terminale", "requise": True},
	{"code": "attestation_scolarite", "label": "Attestation de scolarite", "requise": True},
	{"code": "diplome_bac", "label": "Diplome du baccalaureat", "requise": False},
	{"code": "releve_bac", "label": "Releve de notes du Bac", "requise": False},
]

# Ordre : universelles -> academiques, requises avant optionnelles. Decompte : anterieur 7 / attente 8 / annee 8.
PIECES_BY_BAC_PROFILE = {
	"bac_anterieur": _PIECES_UNIVERSELLES + [
		{"code": "diplome_bac", "label": "Diplome du baccalaureat", "requise": True},
		{"code": "releve_bac", "label": "Releve de notes du Bac", "requise": True},
		{"code": "justificatifs_post_bac",
		 "label": "Justificatifs des annees post-bac (fusionner en un seul fichier si plusieurs annees)",
		 "requise": True},
	],
	"bac_attente": _PIECES_UNIVERSELLES + _PIECES_ATTENTE_ANNEE,
	"bac_annee": _PIECES_UNIVERSELLES + _PIECES_ATTENTE_ANNEE,
}


def _ok(data=None):
	return {"ok": True, "data": data or {}, "error": None}


def _error(code, message, status_code=400):
	frappe.local.response["http_status_code"] = status_code
	return {"ok": False, "data": None, "error": {"code": code, "message": message}}


def _body():
	if frappe.request and frappe.request.data:
		try:
			return json.loads(frappe.request.data.decode("utf-8"))
		except Exception:
			return {}
	return {}


def _value(name, default=None):
	return frappe.form_dict.get(name) or _body().get(name) or default


def _hash(value):
	"""SHA256 nu — réservé au TOKEN de dossier (32 bytes d'entropie : suffisant, acté
	ADM-DEBT-09). Pour l'OTP (6 chiffres), utiliser _hash_otp (HMAC, secret serveur)."""
	return hashlib.sha256(str(value).encode("utf-8")).hexdigest()


def _hash_otp(value):
	"""ADM-DEBT-09 — OTP hashé en HMAC-SHA256 avec secret serveur.

	Un SHA256 nu sur un espace de 10^6 codes est trivialement rainbow-table-able ; le HMAC
	lie le hash au secret du site. FAIL-LOUD si le secret manque (esprit ADM-DEBT-07 : pas
	de fallback silencieux vers un hash faible). Clé de conf `token_hmac_secret` — même nom
	que côté campus (LOT-A1) pour la cohérence OPS, valeur propre à ce site.
	Les OTP sont éphémères (10 min) : aucun hash legacy à migrer.
	"""
	secret = frappe.conf.get("token_hmac_secret")
	if not secret:
		frappe.throw(
			"token_hmac_secret absent de site_config.json — OTP indisponible "
			"(fail-loud ADM-DEBT-09, pas de repli SHA256)."
		)
	return hmac.new(str(secret).encode("utf-8"), str(value).encode("utf-8"), hashlib.sha256).hexdigest()


def _generate_token():
	return secrets.token_urlsafe(32)


def _generate_otp():
	return f"{secrets.randbelow(1000000):06d}"


TOKEN_TTL_DAYS = 7
# Glissant CONDITIONNEL : ne reposer l'échéance que s'il reste moins de ce nombre de jours
# (sinon une écriture surviendrait à CHAQUE lecture). → au plus ~1 écriture/jour. Perf 3G.
TOKEN_SLIDING_RENEW_BELOW_DAYS = 6

# SEC-OTP : durée de vie du CODE OTP (éphémère, distincte du token 7j et du statut
# otp_verified persistant).
OTP_TTL_MINUTES = 10


class DossierTokenExpired(Exception):
	"""Token de dossier expiré (TTL glissant 7j dépassé) — distinct d'un token invalide."""


def _get_applicant(dossier_id, token=None, check_expiry=True):
	if not dossier_id:
		frappe.throw("dossier_id requis.")
	# SEC-1 : token OBLIGATOIRE sur tout accès dossier. Pas de court-circuit :
	# un token absent/vide ne doit JAMAIS renvoyer un dossier (sinon IDOR/fuite PII).
	# Le token manquant est rejeté avant même de charger le doc.
	if not token:
		frappe.throw("Jeton de dossier invalide.")
	doc = frappe.get_doc("Admission Applicant", dossier_id)
	# Comparaison à temps constant du hash du token (anti timing-oracle).
	if not hmac.compare_digest(str(doc.dossier_token_hash or ""), _hash(token)):
		frappe.throw("Jeton de dossier invalide.")
	# SEC-TOKEN-EXPIRY : expiration vérifiée APRÈS le token (SEC-1 d'abord, jamais régressé).
	# Les chemins de renouvellement (request_otp/verify_otp) passent check_expiry=False :
	# le token reste obligatoire et vérifié, mais un token expiré peut encore relancer un OTP.
	if check_expiry:
		_enforce_and_slide_token_expiry(doc)
	return doc


def _enforce_and_slide_token_expiry(doc):
	"""Expiration glissante 7j : rejette si expiré, sinon prolonge CONDITIONNELLEMENT.

	Écriture seulement si l'échéance est absente (legacy) ou proche (< seuil) → pas de
	write à chaque requête (perf 3G).
	"""
	now = now_datetime()
	expires_at = get_datetime(doc.token_expires_at) if doc.token_expires_at else None
	if expires_at is not None and now > expires_at:
		raise DossierTokenExpired()
	if expires_at is None or (expires_at - now) < timedelta(days=TOKEN_SLIDING_RENEW_BELOW_DAYS):
		doc.db_set("token_expires_at", add_days(now, TOKEN_TTL_DAYS), update_modified=False)


def _require_otp_verified(applicant):
	"""SEC-4 : les actions engageantes (paiements, dépôt de pièce) exigent un OTP vérifié.

	Renvoie une réponse d'erreur 403 OTP_REQUIRED si le dossier n'est pas otp_verified, sinon
	None. NE PAS appeler sur get_dossier/classify_bac (token seul) ni create_dossier/OTP.
	"""
	if not applicant.otp_verified:
		return _error("OTP_REQUIRED", "Vérification OTP requise avant cette action.", 403)
	return None


# ── SEC-5 : validation des entrées (helpers centralisés, pré-effets-de-bord) ──

_NAME_MAX_LEN = 140
_PHONE_RE = re.compile(r"^\+?\d{8,15}$")
_PIECE_ALLOWED_EXT = {"pdf", "jpg", "jpeg", "png"}
PIECE_MAX_BYTES = 5 * 1024 * 1024  # 5 Mo (réf. U04)


def _validate_bac_date(value):
	"""Date du bac : format valide (400 propre, pas 500) + plage d'année plausible."""
	try:
		d = getdate(value)
	except Exception:
		return _error("BAC_DATE_INVALID", "Date du bac invalide.", 400)
	current_year = getdate(date.today()).year
	if d.year < current_year - 60 or d.year > current_year + 1:
		return _error("BAC_DATE_INVALID", "Date du bac hors plage plausible.", 400)
	return None


def _validate_identity(first_name, last_name, email, phone, bac_date=None):
	"""Identité candidat : noms (présence/longueur/charset), email, téléphone, date_bac.

	À appeler AVANT tout effet de bord (appel campus, insert) → 400 gracieux.
	"""
	for label, value in (("prenom", first_name), ("nom", last_name)):
		text = value.strip() if isinstance(value, str) else value
		if not text:
			return _error("IDENTITY_INVALID", f"Le champ {label} est requis.", 400)
		if len(str(text)) > _NAME_MAX_LEN or "<" in str(text) or ">" in str(text):
			return _error("IDENTITY_INVALID", f"Le champ {label} est invalide.", 400)
	if not email or not validate_email_address(str(email)):
		return _error("EMAIL_INVALID", "Adresse email invalide.", 400)
	cleaned_phone = re.sub(r"[\s.\-()]", "", str(phone or ""))
	if not _PHONE_RE.match(cleaned_phone):
		return _error("PHONE_INVALID", "Numero de telephone invalide.", 400)
	if bac_date:
		return _validate_bac_date(bac_date)
	return None


def _validate_amount(value, min_val=0, max_val=None):
	"""Montant : numérique + bornes. Renvoie (montant_float, None) ou (None, erreur)."""
	try:
		amount = float(value)
	except (TypeError, ValueError):
		return None, _error("AMOUNT_INVALID", "Montant invalide (non numerique).", 400)
	if amount < min_val:
		return None, _error("AMOUNT_INVALID", f"Montant invalide (minimum {min_val}).", 400)
	if max_val is not None and amount > max_val:
		return None, _error("AMOUNT_TOO_HIGH", f"Montant superieur au plafond autorise ({max_val}).", 400)
	return amount, None


def _validate_piece_file(file_ref, applicant):
	"""Résout le File (par file_url ou docname), vérifie propriété + type/taille.

	Modèle 'revendication' (DEC-SEC5) : un File non rattaché est rattaché à CE dossier ;
	un File déjà rattaché à un AUTRE dossier est refusé (anti-IDOR). Renvoie le docname du
	File (à stocker dans le champ Link→File) ou (None, erreur).
	"""
	if not file_ref:
		return None, _error("PIECE_FILE_INVALID", "Fichier requis.", 400)
	fields = ["name", "file_name", "file_size", "attached_to_doctype", "attached_to_name"]
	f = frappe.db.get_value("File", {"file_url": file_ref}, fields, as_dict=True) or \
		frappe.db.get_value("File", file_ref, fields, as_dict=True)
	if not f:
		return None, _error("PIECE_FILE_INVALID", "Fichier introuvable.", 400)
	attached_name = f.get("attached_to_name")
	if attached_name and (
		f.get("attached_to_doctype") != "Admission Applicant" or attached_name != applicant.name
	):
		return None, _error("PIECE_FILE_FORBIDDEN", "Ce fichier n'appartient pas a ce dossier.", 403)
	file_name = f.get("file_name") or ""
	ext = file_name.rsplit(".", 1)[-1].lower() if "." in file_name else ""
	if ext not in _PIECE_ALLOWED_EXT:
		return None, _error("PIECE_FILE_INVALID", "Type de fichier non autorise (pdf, jpg, png).", 400)
	file_size = f.get("file_size")
	if file_size and file_size > PIECE_MAX_BYTES:
		return None, _error("PIECE_FILE_TOO_LARGE", "Fichier trop volumineux (max 5 Mo).", 400)
	if not attached_name:
		frappe.db.set_value(
			"File", f.get("name"),
			{"attached_to_doctype": "Admission Applicant", "attached_to_name": applicant.name},
			update_modified=False,
		)
	return f.get("name"), None


def _session_doc(session):
	if not session:
		return None
	return frappe.get_doc("Admission Session", session)


def _classify_bac_date(bac_date, session=None):
	bac_day = getdate(bac_date)
	today = getdate(date.today())
	threshold = _session_doc(session).bac_results_date if session else None
	if not threshold:
		return "bac_annee" if bac_day.year == today.year else "bac_anterieur"
	threshold_day = getdate(threshold)
	if bac_day.year < today.year:
		return "bac_anterieur"
	if bac_day.year == today.year and today < threshold_day:
		return "bac_attente"
	return "bac_annee"


def _pieces_for_profile(profile):
	return PIECES_BY_BAC_PROFILE.get(profile, [])


def _sync_pieces(applicant, pieces):
	existing = {row.piece_code: row for row in applicant.pieces}
	for piece in pieces:
		row = existing.get(piece["code"])
		if not row:
			row = applicant.append("pieces", {})
		row.piece_code = piece["code"]
		row.label = piece["label"]
		row.required = 1 if piece.get("requise") else 0
		row.status = row.status or "missing"


# Lot 3a — porte paiement : statuts comptant comme « pièce fournie ». UN SEUL critère de vérité,
# importé par staff.py (calcul des manquantes). 'verified' ne sera écrit qu'au Lot 3c (vérif par
# pièce) ; aujourd'hui seuls 'missing'/'uploaded' existent, mais le critère est déjà juste.
PIECES_FOURNIE_STATUSES = ("uploaded", "verified")


def pieces_requises_manquantes(applicant):
	"""Pièces required=1 dont le statut ne compte PAS comme fournie (porte paiement, Lot 3a).
	Renvoie [{"code","label"}] (labels pour le message). Vide si rien à bloquer — y compris
	applicant.pieces vide (dossier non synchronisé) : on ne bloque que sur des requises connues."""
	return [
		{"code": row.piece_code, "label": row.label}
		for row in (applicant.pieces or [])
		if row.required and row.status not in PIECES_FOURNIE_STATUSES
	]


# ── Lot 3c — contrôle documentaire par pièce (helpers partagés public/staff) ──────────────


def requise_effective(piece):
	"""Exigence EFFECTIVE d'une pièce : la surcharge staff prime sur le structurel, SANS toucher
	PIECES_BY_BAC_PROFILE. waived→False ; required→True ; default→required structurel.
	UN SEUL critère, consommé par get_dossier (candidat+staff), la notif et la garde SOU→ETU."""
	sr = getattr(piece, "staff_requirement", None) or "default"
	if sr == "waived":
		return False
	if sr == "required":
		return True
	return bool(piece.required)


def pieces_requises_non_verifiees(applicant):
	"""Pièces requise_effective dont le statut n'est pas 'verified' (garde SOU→ETU, Lot 3c)."""
	return [
		{"code": row.piece_code, "label": row.label}
		for row in (applicant.pieces or [])
		if requise_effective(row) and row.status != "verified"
	]


def _piece_terminale_pour_notif(piece):
	"""Statut terminal pour la notif : verified/rejected, ou missing explicitement qualifié
	(required/waived). Un missing requise_effective resté 'default' n'est PAS terminal."""
	if piece.status in ("verified", "rejected"):
		return True
	sr = getattr(piece, "staff_requirement", None) or "default"
	return piece.status == "missing" and sr in ("required", "waived")


def notify_pieces_blocked(applicant):
	"""Notif récap bloquée tant qu'une requise_effective n'a pas de statut terminal (uploaded non
	traité, ou missing requise non qualifié). Geste séparé — jamais auto au reject."""
	return any(
		requise_effective(row) and not _piece_terminale_pour_notif(row)
		for row in (applicant.pieces or [])
	)


def pieces_recap(applicant):
	"""Contenu du mail récap : rejetées (à refaire + motif) + requise_effective missing (à fournir)."""
	rejetees = [
		{"code": r.piece_code, "label": r.label, "reason": r.reject_reason, "comment": r.reject_comment}
		for r in (applicant.pieces or []) if r.status == "rejected"
	]
	a_fournir = [
		{"code": r.piece_code, "label": r.label}
		for r in (applicant.pieces or [])
		if requise_effective(r) and r.status == "missing"
	]
	return {"rejetees": rejetees, "a_fournir": a_fournir}


def _record_piece_verdict(applicant_name, piece_code, action, reason=None, comment=None):
	"""Trace append-only d'un verdict documentaire (Lot 3c) — 1 ligne Applicant Piece Verdict."""
	frappe.get_doc({
		"doctype": "Applicant Piece Verdict",
		"applicant": applicant_name, "piece_code": piece_code, "action": action,
		"reason": reason or "", "comment": comment or "",
		"actor": frappe.session.user, "verdict_at": now_datetime(),
	}).insert(ignore_permissions=True)


def _resolve_frais1_fee_type(session):
	if session and session.is_prepa_session:
		return "competition"
	return "application"


# Les deux fee_type "frais 1" (Licence/Prépa) — périmètre de la capture promo DEC-228.
FRAIS1_FEE_TYPES = {"application", "competition"}

# D-CONF-01 : états où le dossier est CLOS/FINI — un paiement ne peut JAMAIS y devenir Confirmed
# (dossier mort → l'argent encaissé est un refund OPS). Blacklist fail-safe (jamais de whitelist
# devinée) : complément des WITHDRAW_STATES, moins INC (réversible, frais 1 déjà réglé). Garde
# PARTAGÉE : promotion webhook (garant — _promote_payment, couvre Pending ET Rejected), initiation
# (submit_payment_online), confirmation offline (staff.confirm_offline_payment).
PAYMENT_FORBIDDEN_STATES = frozenset({"DES", "REF", "REJ", "INS"})


# ── PERF-1 : cache couche catalogue (frappe.cache natif) + invalidation anti-périmé ──

CATALOG_QUERY_LIMIT = 2000
_CATALOG_TTL = 24 * 60 * 60  # backstop 24h


def _cache_get_or_set(key, ttl_seconds, compute):
	"""Sert la valeur cachée (wrappée {"v": ...}) ou la calcule + cache (TTL).

	Robuste : si le cache renvoie autre chose qu'un wrapper {"v": ...} (mock en test, Redis
	indisponible), on recalcule → jamais de valeur fantôme servie. Le wrapper permet de cacher
	correctement un résultat None (ex. catalog miss).
	"""
	cache = None
	try:
		cache = frappe.cache()
		wrapped = cache.get_value(key)
		if isinstance(wrapped, dict) and "v" in wrapped:
			return wrapped["v"]
	except Exception:
		# Cache indisponible (Redis down, hors contexte) → on dégrade vers la requête directe,
		# jamais de 500 ni de valeur fantôme.
		cache = None
	value = compute()
	if cache is not None:
		try:
			cache.set_value(key, {"v": value}, expires_in_sec=ttl_seconds)
		except Exception:
			pass
	return value


def _invalidate_catalog_cache():
	"""Vide tout le cache catalogue admission (anti-périmé). Fin de sync + on_update Desk."""
	try:
		frappe.cache().delete_keys("admission:")
	except Exception:
		pass


def invalidate_catalog_cache(doc=None, method=None):
	"""Handler doc_events (Fee Catalog / Scholarship-Promotion-Level Mirror / Legal Doc / Session)."""
	_invalidate_catalog_cache()


def _resolve_fee_from_catalog(programme_code, fee_type, level_code=None):
	cache_key = f"admission:fee:{programme_code}-{level_code or 'DEFAULT'}-{fee_type}"
	return _cache_get_or_set(
		cache_key, _CATALOG_TTL,
		lambda: _resolve_fee_from_catalog_uncached(programme_code, fee_type, level_code),
	)


def _resolve_fee_from_catalog_uncached(programme_code, fee_type, level_code=None):
	level = level_code or "DEFAULT"
	key = f"{programme_code}-{level}-{fee_type}"
	amount = frappe.db.get_value("Admission Fee Catalog", key, "amount_xof")
	if amount is not None:
		return float(amount)
	if level != "DEFAULT":
		key2 = f"{programme_code}-DEFAULT-{fee_type}"
		amount = frappe.db.get_value("Admission Fee Catalog", key2, "amount_xof")
		if amount is not None:
			frappe.logger("fee_catalog").info(f"Fallback {key} → {key2}")
			return float(amount)
	key3 = f"DEFAULT-DEFAULT-{fee_type}"
	amount = frappe.db.get_value("Admission Fee Catalog", key3, "amount_xof")
	if amount is not None:
		frappe.logger("fee_catalog").info(f"Fallback {key} → {key3}")
		return float(amount)
	return None


SIMULATION_DISCLAIMER = (
	"Estimation INDICATIVE — non garantie. "
	"Sous réserve de validation de la bourse par la Direction. "
	"Le montant réel reste le plein tarif tant qu'aucune bourse n'est validée. "
	"Aucun engagement contractuel."
)

DEFAULT_SCHOLARSHIP_CAP = 0.50


def _get_scholarship_cap_local():
	return _cache_get_or_set("admission:cap", _CATALOG_TTL, _get_scholarship_cap_local_uncached)


def _get_scholarship_cap_local_uncached():
	amount = frappe.db.get_value(
		"Admission Fee Catalog", "SCHOLARSHIP-DEFAULT-cap", "amount_xof"
	)
	if amount is not None and float(amount) > 0:
		return float(amount)
	return DEFAULT_SCHOLARSHIP_CAP


def _get_scholarships_for_programme(programme_code):
	return _cache_get_or_set(
		f"admission:scholarships:{programme_code}", _CATALOG_TTL,
		lambda: _get_scholarships_for_programme_uncached(programme_code),
	)


def _get_scholarships_for_programme_uncached(programme_code):
	filters = {"program": ["in", [programme_code, "", None]]}
	rows = frappe.get_all(
		"Admission Scholarship Mirror",
		filters=filters,
		fields=[
			"mirror_key", "scholarship_name", "category",
			"rate", "exclusivity_group", "program",
		],
		order_by="category asc, rate desc",
		limit=CATALOG_QUERY_LIMIT,
	)
	groups = {}
	for row in rows:
		cat = row.category or "Autre"
		if cat not in groups:
			groups[cat] = []
		groups[cat].append({
			"mirror_key": row.mirror_key,
			"scholarship_name": row.scholarship_name,
			"rate": float(row.rate),
			"exclusivity_group": row.exclusivity_group or "",
			"program": row.program or "",
		})
	return [
		{"category": cat, "scholarships": items}
		for cat, items in groups.items()
	]


def _get_promotions_for_programme(programme_code):
	today = getdate(date.today())
	# Clé datée : une promo expirée à minuit n'est jamais servie le lendemain (anti-périmé).
	return _cache_get_or_set(
		f"admission:promos:{programme_code}:{today}", _CATALOG_TTL,
		lambda: _get_promotions_for_programme_uncached(programme_code, today),
	)


def _get_promotions_for_programme_uncached(programme_code, today):
	filters = {
		"program": ["in", [programme_code, "", None]],
		"start_date": ["<=", today],
		"end_date": [">=", today],
	}
	rows = frappe.get_all(
		"Admission Promotion Mirror",
		filters=filters,
		fields=["mirror_key", "promo_name", "rate", "start_date", "end_date"],
		order_by="rate desc",
		limit=CATALOG_QUERY_LIMIT,
	)
	return [
		{
			"mirror_key": row.mirror_key,
			"promo_name": row.promo_name,
			"rate": float(row.rate),
		}
		for row in rows
	]


def _apply_exclusivity_local(scholarships):
	"""Within each exclusivity_group, keep only the highest rate.

	Same algorithm as UF scholarship.py _apply_exclusivity.
	Scholarships without an exclusivity_group are ALL kept (additive).
	"""
	groups_seen = {}
	no_group = []

	for s in scholarships:
		rate = float(s.get("rate", 0))
		group = s.get("exclusivity_group", "")
		if not group:
			no_group.append(s)
			continue
		existing = groups_seen.get(group)
		if not existing or rate > float(existing.get("rate", 0)):
			groups_seen[group] = s

	return list(groups_seen.values()) + no_group


def _simulate_scholarship_reduction(programme_code, requested_keys, level_code=None):
	"""Simulate §3bis reduction using replicated catalog data.

	SIMULATION indicative only. Real financial effect = UF at inscription.
	Formula IDENTICAL to UF scholarship.py calculate_combined_reduction.
	"""
	if not requested_keys:
		return None

	rows = frappe.get_all(
		"Admission Scholarship Mirror",
		filters={"mirror_key": ["in", requested_keys]},
		fields=[
			"mirror_key", "scholarship_name", "category",
			"rate", "exclusivity_group",
		],
	)
	if not rows:
		return None

	scholarships = [
		{
			"mirror_key": r.mirror_key,
			"scholarship_name": r.scholarship_name,
			"category": r.category,
			"rate": float(r.rate),
			"exclusivity_group": r.exclusivity_group or "",
		}
		for r in rows
	]

	after_exclusivity = _apply_exclusivity_local(scholarships)
	somme_bourses = sum(s["rate"] for s in after_exclusivity)

	cap = _get_scholarship_cap_local()
	bourses_plafond = min(somme_bourses, cap)

	promos = _get_promotions_for_programme(programme_code)
	somme_promo = sum(p["rate"] for p in promos)

	total_reduction = bourses_plafond + somme_promo

	base = _resolve_fee_from_catalog(programme_code, "annual", level_code)
	base = float(base) if base else 0

	montant_reduction = round(base * total_reduction, 2) if base else 0
	cout_final = round(max(base * (1 - total_reduction), 0), 2) if base else 0

	return {
		"base_scolarite": base,
		"bourses_appliquees": [
			{
				"mirror_key": s["mirror_key"],
				"scholarship_name": s["scholarship_name"],
				"category": s["category"],
				"rate": s["rate"],
			}
			for s in after_exclusivity
		],
		"somme_bourses_brute": round(somme_bourses, 4),
		"bourses_plafond": round(bourses_plafond, 4),
		"scholarship_cap": round(cap, 4),
		"promotions_appliquees": [
			{"mirror_key": p["mirror_key"], "promo_name": p["promo_name"], "rate": p["rate"]}
			for p in promos
		],
		"somme_promo": round(somme_promo, 4),
		"total_reduction": round(total_reduction, 4),
		"montant_reduction": montant_reduction,
		"cout_final_estime": cout_final,
		"plafond_atteint": somme_bourses > cap,
		"disclaimer": SIMULATION_DISCLAIMER,
	}


def _capture_promo_if_eligible(applicant):
	"""At frais 1 PAYMENT: capture and lock promo rate if active at payment date.

	Called from both online webhook and offline declaration (two channels).
	Idempotent: skips if already captured.
	The locked rate never changes even if the promo expires later.
	Ref: ADM-UF-5a §3bis-PROMO, VALIDATION-PAUSE2 Point 1 Option B.
	"""
	if applicant.promo_captured_date:
		return
	promos = _get_promotions_for_programme(applicant.programme_code)
	if not promos:
		return
	total_rate = round(sum(p["rate"] for p in promos), 4)
	codes = ",".join(p["mirror_key"] for p in promos)
	applicant.promo_rate = total_rate
	applicant.promo_code = codes
	applicant.promo_captured_date = str(date.today())
	applicant.save(ignore_permissions=True)
	frappe.logger("promo_capture").info(
		f"Promo captured for {applicant.name}: codes={codes} rate={total_rate} date={date.today()}"
	)


def _build_bourses_section(applicant):
	requested = json.loads(applicant.requested_scholarships or "[]")
	# C2-BOURSES (T7) : expose les bourses VALIDÉES par la Direction (écrites à l'ACC) ;
	# la simulation reste indicative (taux UF répliqués) — le montant réel est calculé par UF.
	validated = json.loads(applicant.validated_scholarships or "[]")
	if not requested and not validated:
		return {"demandees": [], "validees": [], "simulation": None, "valide": False}
	simulation = _simulate_scholarship_reduction(
		applicant.programme_code, requested, getattr(applicant, "level_code", None)
	) if requested else None
	return {
		"demandees": requested,
		"validees": validated,
		"simulation": simulation,
		"valide": bool(validated),
	}


def _build_promotion_section(applicant):
	promos = _get_promotions_for_programme(applicant.programme_code)
	if not promos:
		return {"code": None, "taux": 0}
	return {
		"actives": [
			{"code": p["mirror_key"], "nom": p["promo_name"], "taux": p["rate"]}
			for p in promos
		],
		"somme_taux": round(sum(p["rate"] for p in promos), 4),
	}


def _assert_fee_unpaid(fee):
	"""Garde amont B1 (anti double-débit) : refuse si un paiement Confirmed existe DÉJÀ sur ce fee.
	MÊME critère autoritaire que la branche orphelin du webhook (le paiement Confirmed, pas la
	dénormalisation fee.status). Retourne un _error() à propager, ou None si le fee est libre.
	Combiné au handler corrigé (qui promeut/orpheline un success tardif), ferme la fenêtre du
	double paiement. Factorisé pour les 2 entrées online (frais 1 + frais 2) — anti-divergence."""
	if frappe.db.exists("Applicant Fee Payment",
	                    {"applicant_fee": fee.name, "payment_status": "Confirmed"}):
		return _error("ALREADY_PAID", "Ce frais a deja ete regle.", 409)
	return None


def _ensure_fee(applicant, idempotency_key=None):
	session = _session_doc(applicant.session)
	fee_type = _resolve_frais1_fee_type(session)
	existing = frappe.get_all(
		"Applicant Fee",
		filters={"applicant": applicant.name, "fee_type": fee_type},
		pluck="name",
		limit=1,
	)
	if existing:
		return frappe.get_doc("Applicant Fee", existing[0])
	level_code = getattr(applicant, "level_code", None)
	amount = _resolve_fee_from_catalog(
		session.programme_code if session else "", fee_type, level_code
	)
	if amount is None:
		amount = session.application_fee_xof if session else 0
		frappe.logger("fee_catalog").warning(
			f"Catalog miss for {session.programme_code if session else '?'}/{fee_type}, "
			f"fallback session.application_fee_xof={amount}"
		)
	fee = frappe.get_doc(
		{
			"doctype": "Applicant Fee",
			"applicant": applicant.name,
			"session": applicant.session,
			"person_id": applicant.person_id,
			"fee_type": fee_type,
			"amount_xof": amount,
			"status": "Pending",
			"idempotency_key": idempotency_key,
		}
	)
	fee.insert(ignore_permissions=True)
	return fee


def _ensure_enrollment_fee(applicant, idempotency_key=None):
	existing = frappe.get_all(
		"Applicant Fee",
		filters={"applicant": applicant.name, "fee_type": "enrollment"},
		pluck="name",
		limit=1,
	)
	if existing:
		return frappe.get_doc("Applicant Fee", existing[0])
	level_code = getattr(applicant, "level_code", None)
	amount = _resolve_fee_from_catalog(applicant.programme_code, "enrollment", level_code)
	if amount is None:
		frappe.logger("fee_catalog").warning(
			f"Catalog miss for {applicant.programme_code}/{level_code}/enrollment, no fallback available"
		)
		return None
	fee = frappe.get_doc(
		{
			"doctype": "Applicant Fee",
			"applicant": applicant.name,
			"session": applicant.session,
			"person_id": applicant.person_id,
			"fee_type": "enrollment",
			"amount_xof": amount,
			"status": "Pending",
			"idempotency_key": idempotency_key,
		}
	)
	fee.insert(ignore_permissions=True)
	return fee


def _check_enrollment_fee_paid(applicant_name):
	fee_name = frappe.db.get_value(
		"Applicant Fee",
		{"applicant": applicant_name, "fee_type": "enrollment"},
		"name",
	)
	if not fee_name:
		frappe.throw(
			"Le paiement du frais d'inscription (frais 2) est requis avant l'inscription. "
			"Aucun frais d'inscription trouvé."
		)
	paid = frappe.db.exists(
		"Applicant Fee Payment",
		{"applicant_fee": fee_name, "payment_status": "Confirmed"},
	)
	if not paid:
		frappe.throw(
			"Le paiement du frais d'inscription (frais 2) est requis avant l'inscription. "
			"Le paiement n'est pas encore confirmé."
		)


def _get_fee_and_payment(applicant_name, fee_types):
	fee_names = frappe.get_all(
		"Applicant Fee",
		filters={"applicant": applicant_name, "fee_type": ["in", fee_types]},
		pluck="name",
		limit=1,
	)
	if not fee_names:
		return None, None
	fee = frappe.get_doc("Applicant Fee", fee_names[0])
	payment_names = frappe.get_all(
		"Applicant Fee Payment",
		filters={"applicant_fee": fee.name},
		pluck="name",
		limit=1,
	)
	payment = frappe.get_doc("Applicant Fee Payment", payment_names[0]) if payment_names else None
	return fee, payment


def _serialize_dossier(applicant):
	fee, payment = _get_fee_and_payment(applicant.name, ["application", "competition"])
	enrollment_fee, enrollment_payment = _get_fee_and_payment(applicant.name, ["enrollment"])
	session = _session_doc(applicant.session)
	level_name = None
	if getattr(applicant, "level_code", None):
		level_name = frappe.db.get_value(
			"Admission Level Mirror", applicant.level_code, "level_name"
		)
	return {
		"dossier_id": applicant.name,
		"statut": applicant.status,
		"programme": {
			"code": applicant.programme_code,
			"label": applicant.programme_label,
			"level": {"code": applicant.level_code, "name": level_name} if getattr(applicant, "level_code", None) else None,
		},
		"session": {
			"id": applicant.session,
			"label": session.label if session else None,
			"academic_year": session.academic_year if session else None,
			# LOT F (F8) : échéance + ouverture pour les deadlines affichées au front.
			"closes_on": str(session.closes_on) if session and session.closes_on else None,
			"is_open": bool(session.is_open) if session else None,
		},
		"profil_bac": applicant.bac_profile,
		"identite": {
			"prenom": applicant.first_name,
			"nom": applicant.last_name,
			"email": applicant.email,
			"tel": applicant.phone,
			"date_bac": str(applicant.bac_date) if applicant.bac_date else None,
		},
		"pieces": [
			{
				"code": row.piece_code,
				"label": row.label,
				# `statut` RÉTRO-COMPATIBLE (deposee/manquante) — le front candidat existant le lit tel quel.
				"statut": "deposee" if row.status in {"uploaded", "verified"} else "manquante",
				# Lot 3c-3a : état RÉEL 4-états + motif (re-upload informé, plus aveugle). Additif : 3c-3b les lira.
				"statut_reel": row.status,
				"reject_reason": row.reject_reason or None,
				"reject_comment": row.reject_comment or None,
				"requise": requise_effective(row),
			}
			for row in applicant.pieces
		],
		"bourses": _build_bourses_section(applicant),
		"promotion": _build_promotion_section(applicant),
		"paiement": {
			"frais1": {
				"montant_xof": fee.amount_xof if fee else None,
				"statut": payment.payment_status.lower() if payment else "en_attente",
				"recu_ref": payment.receipt_number if payment else None,
			},
			"frais2": {
				"montant_xof": enrollment_fee.amount_xof if enrollment_fee else None,
				"statut": enrollment_payment.payment_status.lower() if enrollment_payment else "en_attente",
				"recu_ref": enrollment_payment.receipt_number if enrollment_payment else None,
			} if enrollment_fee else None,
		},
		"conditionnel": bool(applicant.conditionnel),
		# LOT F (F6) : motif affiché par le front pour la reprise INC (resubmit_complement).
		"motif_incompletude": applicant.motif_incompletude if applicant.status == "INC" else None,
		# FIX-D-CONF-05/07/08 : décisions motivées re-consultables par le candidat concerné. ADDITIF +
		# CONDITIONNEL par statut (même patron que motif_incompletude) → aucune fuite d'un motif hors-état,
		# aucun accès croisé (get_dossier sert le seul dossier du token). Texte staff fidèle (pas de reformulation).
		"motif_rejet": applicant.motif_rejet if applicant.status == "REJ" else None,          # D-CONF-05 (HAUT)
		"motif_refus": applicant.motif_refus if applicant.status == "REF" else None,          # D-CONF-07
		"motif_desistement": applicant.motif_desistement if applicant.status == "DES" else None,  # D-CONF-07
		"rang_liste_attente": applicant.rang_liste_attente if applicant.status == "ATT" else None,  # D-CONF-08
	}


PROGRAMME_META_FIELDS = ["programme_code", "title", "parcours", "partner", "partner_name",
                         "location", "dd_component_1", "dd_component_2", "dd_affinity"]


def _programme_meta_map():
	"""Métadonnées catalogue par programme_code (vide si le doctype n'existe pas encore)."""
	if not frappe.db.exists("DocType", "Admission Programme"):
		return {}
	rows = frappe.get_all("Admission Programme", filters={"is_active": 1},
	                      fields=PROGRAMME_META_FIELDS, limit=CATALOG_QUERY_LIMIT)
	return {r["programme_code"]: r for r in rows}


@frappe.whitelist(allow_guest=True, methods=["GET"])
def list_programmes():
	sessions = frappe.get_all(
		"Admission Session",
		filters={"is_open": 1},
		fields=["programme_code", "programme_label"],
		order_by="programme_code asc",
	)
	seen = {}
	for session in sessions:
		seen[session.programme_code] = session.programme_label
	levels_by_prog = {}
	all_levels = frappe.get_all(
		"Admission Level Mirror",
		fields=["level_code", "level_name", "program_code", "level_order"],
		order_by="program_code asc, level_order asc",
		limit=CATALOG_QUERY_LIMIT,
	)
	for lvl in all_levels:
		levels_by_prog.setdefault(lvl.program_code, []).append({
			"level_code": lvl.level_code,
			"level_name": lvl.level_name,
			"level_order": lvl.level_order,
		})
	meta = _programme_meta_map()
	out = []
	for code, label in seen.items():
		m = meta.get(code, {})
		out.append({
			"code": code,
			"label": m.get("title") or label,
			"niveaux": levels_by_prog.get(code, []),
			"parcours": m.get("parcours"),
			"partner": m.get("partner"),
			"partner_name": m.get("partner_name"),
			"location": m.get("location"),
			"dd_affinity": m.get("dd_affinity"),
			"dd_component_1": m.get("dd_component_1"),
			"dd_component_2": m.get("dd_component_2"),
		})
	return _ok({"programmes": out})


@frappe.whitelist(allow_guest=True, methods=["GET"])
def list_sessions(programme=None):
	programme = programme or _value("programme")
	filters = {"is_open": 1}
	if programme:
		filters["programme_code"] = programme
	sessions = frappe.get_all(
		"Admission Session",
		filters=filters,
		fields=["name", "label", "academic_year", "programme_code", "programme_label", "opens_on", "closes_on"],
		order_by="opens_on asc",
	)
	return _ok(
		{
			"sessions": [
				{
					"id": row.name,
					"label": row.label,
					"academic_year": row.academic_year,
					"programme": {"code": row.programme_code, "label": row.programme_label},
					"opens_on": str(row.opens_on),
					"closes_on": str(row.closes_on),
				}
				for row in sessions
			]
		}
	)


@frappe.whitelist(allow_guest=True, methods=["GET"])
def get_frais(programme=None, session=None, level_code=None):
	level_code = level_code or _value("level_code")
	session = session or _value("session")
	if session:
		session_doc = _session_doc(session)
	else:
		programme = programme or _value("programme")
		name = frappe.get_all("Admission Session", filters={"programme_code": programme, "is_open": 1}, pluck="name", limit=1)
		session_doc = _session_doc(name[0]) if name else None
	if not session_doc:
		return _error("SESSION_NOT_FOUND", "Aucune session d'admission ouverte.", 404)
	return _ok(_build_frais_data(session_doc, level_code))


def _build_frais_data(session_doc, level_code=None):
	from admission.api.legal import _get_versioned_disclaimer, _get_active_legal_texts_meta
	fee_type = _resolve_frais1_fee_type(session_doc)
	frais1_amount = _resolve_fee_from_catalog(session_doc.programme_code, fee_type, level_code)
	if frais1_amount is None:
		frais1_amount = session_doc.application_fee_xof
		frappe.logger("fee_catalog").warning(
			f"get_frais catalog miss for {session_doc.programme_code}/{level_code}/{fee_type}, "
			f"fallback session.application_fee_xof={frais1_amount}"
		)
	frais2_amount = _resolve_fee_from_catalog(session_doc.programme_code, "enrollment", level_code)
	disclaimer_text, disclaimer_hash = _get_versioned_disclaimer()
	result = {
		"frais1": {
			"montant_xof": frais1_amount,
			"devise": "XOF",
			"fee_type": fee_type,
		},
		"bourses_eligibles": _get_scholarships_for_programme(session_doc.programme_code),
		"promotions_actives": _get_promotions_for_programme(session_doc.programme_code),
		"scolarite_annuelle": _resolve_fee_from_catalog(session_doc.programme_code, "annual", level_code),
		"scholarship_cap": _get_scholarship_cap_local(),
		"simulation_disclaimer": disclaimer_text,
		"simulation_disclaimer_version": disclaimer_hash,
		"textes_legaux": _get_active_legal_texts_meta(),
	}
	# LOT RIB-SETTINGS : coordonnées d'encaissement (source unique Admission Settings,
	# rôle Admission Finance). None → le front MASQUE le canal virement.
	from admission.api.email_template import get_bank
	bank = get_bank()
	result["rib"] = ({"banque": bank["banque"], "titulaire": bank["titulaire"],
	                  "iban": bank["iban"], "bic": bank["bic"], "version": bank["version"]}
	                 if bank else None)
	meta = _programme_meta_map().get(session_doc.programme_code, {})
	result["programme"] = {
		"code": session_doc.programme_code,
		"title": meta.get("title") or session_doc.programme_label,
		"parcours": meta.get("parcours"),
		"partner": meta.get("partner"),
		"partner_name": meta.get("partner_name"),
		"location": meta.get("location"),
		"dd_affinity": meta.get("dd_affinity"),
	}
	if frais2_amount is not None:
		result["frais2"] = {
			"montant_xof": frais2_amount,
			"devise": "XOF",
			"fee_type": "enrollment",
		}
	return result


@frappe.whitelist(allow_guest=True, methods=["GET"])
def get_legal_documents(types=None):
	types = types or _value("types")
	from admission.api.legal import _get_active_legal_texts
	all_texts = _get_active_legal_texts()
	if types:
		if isinstance(types, str):
			types = [t.strip() for t in types.split(",")]
		filtered = {k: v for k, v in all_texts.items() if v["type"] in types}
		return _ok({"documents": filtered})
	return _ok({"documents": all_texts})


@frappe.whitelist(allow_guest=True, methods=["POST"])
@rate_limit(limit=20, seconds=60 * 60)
def create_dossier():
	payload = _body() or frappe.form_dict
	idempotency_key = payload.get("idempotency_key")
	if idempotency_key:
		existing = frappe.get_all("Admission Applicant", filters={"idempotency_key": idempotency_key}, pluck="name", limit=1)
		if existing:
			return _ok({"dossier_id": existing[0], "token": None, "idempotent": True})
	session = _session_doc(payload.get("session"))
	if not session:
		return _error("SESSION_REQUIRED", "session is required.", 400)
	level_code = payload.get("level_code") or payload.get("niveau")
	if not level_code:
		return _error("LEVEL_REQUIRED", "level_code is required.", 400)
	if not frappe.db.exists("Admission Level Mirror", level_code):
		return _error("INVALID_LEVEL", f"Niveau {level_code} inconnu.", 400)
	mirror_prog = frappe.db.get_value("Admission Level Mirror", level_code, "program_code")
	if mirror_prog != session.programme_code:
		return _error(
			"LEVEL_MISMATCH",
			f"Niveau {level_code} ne correspond pas au programme {session.programme_code}.",
			400,
		)
	consent_dp = payload.get("consent_data_processing")
	consent_cgv = payload.get("consent_cgv")
	if not consent_dp or not consent_cgv:
		return _error(
			"CONSENT_REQUIRED",
			"consent_data_processing et consent_cgv sont requis pour creer un dossier.",
			400,
		)
	from admission.api.legal import _get_active_legal_document
	privacy_doc = _get_active_legal_document("PRIVACY_POLICY")
	cgv_doc = _get_active_legal_document("CGV")
	if not privacy_doc or not cgv_doc:
		return _error(
			"LEGAL_DOCUMENT_MISSING",
			"Textes legaux (PRIVACY_POLICY, CGV) non disponibles. Contactez l'administration.",
			503,
		)

	identity = payload.get("identite") or {}
	first_name = identity.get("prenom") or identity.get("first_name")
	last_name = identity.get("nom") or identity.get("last_name")
	email = identity.get("email")
	phone = identity.get("tel") or identity.get("phone")

	# SEC-5 : valider l'identité AVANT l'appel campus (400 propre, pas de 500 ni d'appel inutile).
	identity_err = _validate_identity(
		first_name, last_name, email, phone,
		identity.get("date_bac") or identity.get("bac_date"),
	)
	if identity_err:
		return identity_err

	person_id = _resolve_person_from_campus(email, first_name, last_name, phone)
	if not person_id:
		return _error("PERSON_RESOLUTION_FAILED", "Impossible de résoudre l'identité Person auprès du campus. Réessayez.", 503)

	token = _generate_token()
	applicant = frappe.get_doc(
		{
			"doctype": "Admission Applicant",
			"status": "BRO",
			"first_name": first_name,
			"last_name": last_name,
			"email": email,
			"phone": phone,
			"bac_date": identity.get("date_bac") or identity.get("bac_date"),
			"programme_code": session.programme_code,
			"programme_label": session.programme_label,
			"level_code": level_code,
			"session": session.name,
			"person_id": person_id,
			"dossier_token_hash": _hash(token),
			"token_expires_at": add_days(now_datetime(), TOKEN_TTL_DAYS),
			"idempotency_key": idempotency_key,
			"consent_data_processing": 1,
			"consent_data_processing_at": now_datetime(),
			"consent_cgv": 1,
			"consent_cgv_at": now_datetime(),
		}
	)
	bourses_demandees = payload.get("bourses_demandees") or []
	if bourses_demandees:
		valid_keys = frappe.get_all(
			"Admission Scholarship Mirror",
			filters={"mirror_key": ["in", bourses_demandees]},
			pluck="mirror_key",
		)
		applicant.requested_scholarships = json.dumps(
			[k for k in bourses_demandees if k in valid_keys]
		)
	if applicant.bac_date:
		applicant.bac_profile = _classify_bac_date(applicant.bac_date, session.name)
		_sync_pieces(applicant, _pieces_for_profile(applicant.bac_profile))
	applicant.insert(ignore_permissions=True)
	from admission.api.legal import _record_consent
	_record_consent(applicant.name, "DATA_PROCESSING", privacy_doc.name)
	_record_consent(applicant.name, "CGV", cgv_doc.name)
	_ensure_fee(applicant, idempotency_key=f"{idempotency_key}:fee" if idempotency_key else None)
	# LOT M (M6/A0.2) : mail de bienvenue avec le LIEN DE REPRISE tokenisé — seul moment
	# où le token est détenu en clair. Non-bloquant (socle notifications).
	from admission.api.notifications import send_account_created
	send_account_created(applicant, token)
	log_event("create_dossier", "success", dossier_id=applicant.name, programme=session.programme_code)
	return _ok({"dossier_id": applicant.name, "token": token, "statut": applicant.status})


@frappe.whitelist(allow_guest=True, methods=["POST"])
@rate_limit(key="dossier_id", limit=5, seconds=60 * 60)
def request_otp(dossier_id=None, token=None):
	dossier_id = dossier_id or _value("dossier_id")
	token = token or _value("token")
	try:
		# Renouvellement : on tolère un token EXPIRÉ (hash toujours vérifié → SEC-1 intact)
		# pour pouvoir relancer un OTP ; sinon un token expiré serait irrécupérable.
		applicant = _get_applicant(dossier_id, token, check_expiry=False)
	except Exception:
		return _error("INVALID_DOSSIER", "Identifiants de dossier invalides.", 403)
	email_otp = _generate_otp()
	phone_otp = _generate_otp()
	applicant.otp_email_hash = _hash_otp(email_otp)  # ADM-DEBT-09 : HMAC, plus de SHA256 nu
	applicant.otp_phone_hash = _hash_otp(phone_otp)
	# SEC-OTP : code OTP éphémère (10 min) + re-vérif forcée (toute nouvelle demande
	# repasse otp_verified à 0 ; le statut persiste sinon entre les visites).
	applicant.otp_expires_at = now_datetime() + timedelta(minutes=OTP_TTL_MINUTES)
	applicant.otp_verified = 0
	applicant.save(ignore_permissions=True)
	frappe.db.commit()
	# LOT M (M3) : LIVRAISON RÉELLE du code e-mail (le « queued » historique n'envoyait
	# RIEN — contrat mensonger, audit parcours). Seul le HASH est persisté ; le code part
	# par mail (template `otp`) et n'est JAMAIS loggé. SMS = canal OPS distinct (A0.1).
	from admission.api.notifications import send_email_otp
	send_email_otp(applicant, email_otp, minutes=OTP_TTL_MINUTES, token=token)
	data = {"delivery": {"email": "sent", "sms": "pending_ops"}}
	# SEC : ne JAMAIS divulguer l'OTP en réponse sur la seule foi de developer_mode
	# (landmine prod si developer_mode reste activé). Opt-in explicite et dédié requis.
	if frappe.conf.get("developer_mode") and frappe.conf.get("expose_dev_otp"):
		data["dev_otp"] = {"email": email_otp, "phone": phone_otp}
	log_event("request_otp", "success", dossier_id=applicant.name)
	return _ok(data)


@frappe.whitelist(allow_guest=True, methods=["POST"])
@rate_limit(key="dossier_id", limit=10, seconds=60 * 60)
def verify_otp(dossier_id=None, token=None, email_otp=None, phone_otp=None):
	dossier_id = dossier_id or _value("dossier_id")
	token = token or _value("token")
	email_otp = email_otp or _value("email_otp")
	phone_otp = phone_otp or _value("phone_otp")
	try:
		# Renouvellement : token expiré toléré (hash vérifié → SEC-1 intact) ; c'est l'OTP
		# (envoyé à l'email/tél du candidat) qui sert de second facteur pour renouveler.
		applicant = _get_applicant(dossier_id, token, check_expiry=False)
	except Exception:
		return _error("INVALID_DOSSIER", "Identifiants de dossier invalides.", 403)
	# SEC-OTP : le CODE OTP expire (10 min, distinct du token 7j). Code expiré → refus + redemander.
	otp_exp = get_datetime(applicant.otp_expires_at) if applicant.otp_expires_at else None
	if not otp_exp or now_datetime() > otp_exp:
		log_event("verify_otp", "failed", dossier_id=applicant.name, reason="otp_expired", level="warning")
		return _error("OTP_EXPIRED", "Code OTP expiré. Demandez un nouveau code.", 400)
	# Comparaison à temps constant (cohérent avec le token).
	# A0.1 (LOT M) : le canal E-MAIL est OBLIGATOIRE (c'est lui qui livre le code) ; le
	# code téléphone n'est vérifié QUE s'il est soumis — le canal SMS est un chantier OPS
	# (recette), mais le numéro reste collecté pour le contact humain (relances). Un
	# phone_otp soumis FAUX reste un échec (pas de contournement).
	email_ok = hmac.compare_digest(str(applicant.otp_email_hash or ""), _hash_otp(email_otp))
	phone_ok = (not phone_otp) or hmac.compare_digest(
		str(applicant.otp_phone_hash or ""), _hash_otp(phone_otp)
	)
	if not (email_ok and phone_ok):
		log_event("verify_otp", "failed", dossier_id=applicant.name, reason="otp_invalid", level="warning")
		return _error("OTP_INVALID", "OTP verification failed.", 400)
	applicant.otp_verified = 1
	applicant.otp_verified_at = now_datetime()
	# SEC-TOKEN-EXPIRY : le re-OTP RENOUVELLE le dossier — rotation du token (un ancien
	# token éventé devient inutile) + échéance repositionnée à +7j. Le nouveau token est
	# renvoyé : le front DOIT l'adopter pour les appels suivants.
	new_token = _generate_token()
	applicant.dossier_token_hash = _hash(new_token)
	applicant.token_expires_at = add_days(now_datetime(), TOKEN_TTL_DAYS)
	applicant.save(ignore_permissions=True)
	frappe.db.commit()
	log_event("verify_otp", "success", dossier_id=applicant.name)
	return _ok({"dossier_id": applicant.name, "otp_verified": True, "token": new_token})


@frappe.whitelist(allow_guest=True, methods=["POST"])
def classify_bac(bac_date=None, session=None, dossier_id=None, token=None):
	bac_date = bac_date or _value("bac_date")
	session = session or _value("session")
	dossier_id = dossier_id or _value("dossier_id")
	token = token or _value("token")
	if not bac_date:
		return _error("BAC_DATE_REQUIRED", "bac_date is required.", 400)
	bac_date_err = _validate_bac_date(bac_date)
	if bac_date_err:
		return bac_date_err
	profile = _classify_bac_date(bac_date, session)
	pieces = _pieces_for_profile(profile)
	if dossier_id:
		try:
			applicant = _get_applicant(dossier_id, token)
		except DossierTokenExpired:
			return _error("TOKEN_EXPIRED", "Lien de dossier expiré. Demandez un nouveau code OTP.", 403)
		except Exception:
			return _error("INVALID_DOSSIER", "Identifiants de dossier invalides.", 403)
		applicant.bac_date = bac_date
		applicant.bac_profile = profile
		applicant.conditionnel = 1 if profile == "bac_attente" else 0
		_sync_pieces(applicant, pieces)
		applicant.save(ignore_permissions=True)
		frappe.db.commit()
	return _ok({"profil_bac": profile, "pieces": pieces, "conditionnel": profile == "bac_attente"})


def _mark_piece_uploaded(applicant, piece_code, file_docname):
	"""Pose le File sur la ligne pièce attendue (dépôt binaire upload_piece_file). D-UPLOAD-LEGACY-27 : ancien endpoint upload_piece (file_url) retiré (0 consommateur)."""
	for row in applicant.pieces:
		if row.piece_code == piece_code:
			# Lot 3c — une pièce déjà VALIDÉE par l'administration ne peut pas être remplacée
			# (reste acquise). Une pièce REJETÉE repasse uploaded et le rejet est effacé (reset).
			if row.status == "verified":
				return _error("PIECE_ALREADY_VERIFIED",
					"Cette pièce a déjà été validée par l'administration et ne peut pas être remplacée.", 409)
			was_rejected = row.status == "rejected"
			row.file = file_docname
			row.status = "uploaded"
			row.uploaded_at = now_datetime()
			if was_rejected:
				row.reject_reason = None
				row.reject_comment = None
			applicant.save(ignore_permissions=True)
			frappe.db.commit()
			if was_rejected:
				_record_piece_verdict(applicant.name, piece_code, "reset")
			log_event("upload_piece", "success", dossier_id=applicant.name, piece=piece_code)
			return _ok({"piece_code": piece_code, "status": "deposee"})
	return _error("PIECE_NOT_EXPECTED", "Cette pièce n'est pas attendue pour ce dossier.", 400)


# A0.4 — signatures binaires admises (anti-contournement par simple renommage d'extension).
_PIECE_MAGIC = {
	"pdf": (b"%PDF",),
	"png": (b"\x89PNG\r\n\x1a\n",),
	"jpg": (b"\xff\xd8\xff",),
	"jpeg": (b"\xff\xd8\xff",),
}


@frappe.whitelist(allow_guest=True, methods=["POST"])
@rate_limit(key="dossier_id", limit=30, seconds=60 * 60)
def upload_piece_file(dossier_id=None, token=None, piece_code=None):
	"""A0.4 (LOT F) — dépôt BINAIRE direct d'une pièce par le candidat (multipart guest).

	`frappe.utils.file_manager.upload_file` n'a pas de chemin guest sûr en v15 → endpoint
	DÉDIÉ : auth token (SEC-1) + OTP vérifié (SEC-4), extension ET signature binaire
	contrôlées (un .pdf renommé est refusé), taille ≤ 5 Mo lue de manière bornée, fichier
	stocké PRIVÉ et attaché à CE dossier (anti-IDOR par construction), nom de fichier
	NORMALISÉ ({piece_code}-{dossier}.{ext} — aucun nom client ne touche le disque).
	La pièce doit être ATTENDUE par le dossier avant tout stockage (pas de File orphelin).
	"""
	dossier_id = dossier_id or _value("dossier_id")
	token = token or _value("token")
	piece_code = piece_code or _value("piece_code")
	try:
		applicant = _get_applicant(dossier_id, token)
	except DossierTokenExpired:
		return _error("TOKEN_EXPIRED", "Lien de dossier expiré. Demandez un nouveau code OTP.", 403)
	except Exception:
		return _error("INVALID_DOSSIER", "Identifiants de dossier invalides.", 403)
	otp_err = _require_otp_verified(applicant)
	if otp_err:
		return otp_err
	if not piece_code or piece_code not in {row.piece_code for row in applicant.pieces}:
		return _error("PIECE_NOT_EXPECTED", "Cette pièce n'est pas attendue pour ce dossier.", 400)
	files = getattr(frappe.request, "files", None) if getattr(frappe, "request", None) else None
	storage = files.get("file") if files else None
	if storage is None or not getattr(storage, "filename", None):
		return _error("PIECE_FILE_INVALID", "Fichier requis (champ multipart 'file').", 400)
	ext = storage.filename.rsplit(".", 1)[-1].lower() if "." in storage.filename else ""
	if ext not in _PIECE_ALLOWED_EXT:
		return _error("PIECE_FILE_INVALID", "Type de fichier non autorise (pdf, jpg, png).", 400)
	content = storage.stream.read(PIECE_MAX_BYTES + 1)  # lecture BORNÉE (pas de DoS mémoire)
	if len(content) > PIECE_MAX_BYTES:
		return _error("PIECE_FILE_TOO_LARGE", "Fichier trop volumineux (max 5 Mo).", 400)
	if not content or not any(content.startswith(sig) for sig in _PIECE_MAGIC[ext]):
		return _error("PIECE_FILE_INVALID", "Contenu du fichier invalide pour ce type.", 400)
	from frappe.utils.file_manager import save_file
	file_doc = save_file(
		f"{piece_code}-{applicant.name}.{ext}", content,
		"Admission Applicant", applicant.name, is_private=1,
	)
	return _mark_piece_uploaded(applicant, piece_code, file_doc.name)


@frappe.whitelist(allow_guest=True, methods=["POST"])
@rate_limit(key="dossier_id", limit=60, seconds=60 * 60)
def view_own_piece_file(dossier_id=None, token=None, piece_code=None):
	"""D-UPLOAD-REVIEW-17 — re-visualisation candidat d'une pièce déposée. Miroir de
	staff.download_piece_file, mais gardé côté candidat : token (SEC-1, anti-IDOR via hmac
	temps-constant _get_applicant → un token ne sert QUE les pièces de SON dossier) + OTP
	vérifié (SEC-4). Sert le File privé en RAW (response.type=download → guess_type pdf/jpg/png)."""
	dossier_id = dossier_id or _value("dossier_id")
	token = token or _value("token")
	piece_code = piece_code or _value("piece_code")
	try:
		applicant = _get_applicant(dossier_id, token)
	except DossierTokenExpired:
		return _error("TOKEN_EXPIRED", "Lien de dossier expiré. Demandez un nouveau code OTP.", 403)
	except Exception:
		return _error("INVALID_DOSSIER", "Identifiants de dossier invalides.", 403)
	otp_err = _require_otp_verified(applicant)
	if otp_err:
		return otp_err
	row = next((p for p in (applicant.pieces or []) if p.piece_code == piece_code), None)
	if not row or not row.file:
		return _error("PIECE_FILE_NOT_FOUND", "Aucun fichier pour cette pièce.", 404)
	file_doc = frappe.get_doc("File", row.file)
	frappe.local.response.filename = file_doc.file_name
	frappe.local.response.filecontent = file_doc.get_content()
	frappe.local.response.type = "download"  # as_raw → Content-Type réel via guess_type (pas de JSON)


@frappe.whitelist(allow_guest=True, methods=["POST"])
@rate_limit(key="dossier_id", limit=10, seconds=60 * 60)
def resubmit_complement(dossier_id=None, token=None):
	"""C1-COMPLETUDE — re-soumission candidat après correction (INC→SOU).

	PO-4 : illimitée tant que la session est ouverte. Auth candidat = token + OTP (SEC-1/SEC-4).
	Le candidat (Guest) ne passe pas validate_workflow (pas de rôle) → INC→SOU par db.set_value
	(aucun effet contrôleur sur INC/SOU) + Transition Log manuel fidèle (actor=Guest, source=public_api).
	Le motif d'incomplétude est effacé (évite un motif qui traîne sur un dossier redevenu SOU).
	"""
	dossier_id = dossier_id or _value("dossier_id")
	token = token or _value("token")
	try:
		applicant = _get_applicant(dossier_id, token)
	except DossierTokenExpired:
		return _error("TOKEN_EXPIRED", "Lien de dossier expiré. Demandez un nouveau code OTP.", 403)
	except Exception:
		return _error("INVALID_DOSSIER", "Identifiants de dossier invalides.", 403)
	otp_err = _require_otp_verified(applicant)
	if otp_err:
		return otp_err
	if applicant.status != "INC":
		return _error("INVALID_STATE", "Re-soumission possible seulement depuis Incomplet (INC).", 409)
	if not frappe.db.get_value("Admission Session", applicant.session, "is_open"):
		return _error("SESSION_CLOSED", "La session de candidature est fermee.", 409)
	frappe.db.set_value(
		"Admission Applicant", applicant.name, {"status": "SOU", "motif_incompletude": None}
	)
	_record_candidate_transition(applicant.name, "INC", "SOU")
	frappe.db.commit()
	log_event("resubmit_complement", "success", dossier_id=applicant.name)
	return _ok({"dossier_id": applicant.name, "status": "SOU"})


def _record_candidate_transition(applicant_name, from_status, to_status):
	"""Trace une transition CANDIDAT (actor=Guest, source=public_api/webhook) dans le
	Transition Log, en réutilisant les helpers du contrôleur (pas de duplication de
	format). Compagnon du pattern db.set_value : le Guest ne passe pas validate_workflow
	(aucun rôle → get_transitions exige read → PermissionError Frappe). Non-bloquant."""
	from admission.admission.doctype.admission_applicant.admission_applicant import (
		_detect_transition_context,
		write_transition_log,
	)
	try:
		source, action = _detect_transition_context()
		write_transition_log(
			applicant_name, from_status, to_status,
			actor=frappe.session.user, source=source, action=action,
		)
	except Exception:
		frappe.logger("public").warning(f"Transition log (candidate) failed: {frappe.get_traceback()}")


@frappe.whitelist(allow_guest=True, methods=["POST"])
@rate_limit(key="dossier_id", limit=10, seconds=60 * 60)
def candidate_resubmit(dossier_id=None, token=None):
	"""Lot 3c-3a — le candidat signale « j'ai fini de re-déposer » (modèle A : le dossier RESTE SOU).

	Marqueur `resoumis` ORTHOGONAL au statut (pas une transition). Gardé : actif seulement si plus
	AUCUNE pièce `rejected` (toutes re-uploadées). Effet = resoumis=True + 1 notif staff. Le staff
	re-contrôle ; `verify_piece`/`reject_piece` éteignent `resoumis`.
	"""
	dossier_id = dossier_id or _value("dossier_id")
	token = token or _value("token")
	try:
		applicant = _get_applicant(dossier_id, token)
	except DossierTokenExpired:
		return _error("TOKEN_EXPIRED", "Lien de dossier expiré. Demandez un nouveau code OTP.", 403)
	except Exception:
		return _error("INVALID_DOSSIER", "Identifiants de dossier invalides.", 403)
	otp_err = _require_otp_verified(applicant)
	if otp_err:
		return otp_err
	# Gardes FAIL-FAST (avant tout effet) : SOU d'abord, puis 0 rejected (esprit garde Lot 3a).
	if applicant.status != "SOU":
		return _error("INVALID_STATE", "Re-soumission possible seulement depuis Soumis (SOU).", 409)
	if any(p.status == "rejected" for p in (applicant.pieces or [])):
		return _error("PIECES_REJECTED_PENDING",
		              "Des pièces sont encore refusées — re-déposez-les avant de signaler la fin du dépôt.", 409)
	# Marqueur Guest-safe (le candidat ne passe pas validate_workflow ; statut inchangé).
	frappe.db.set_value("Admission Applicant", applicant.name, "resoumis", 1)
	frappe.db.commit()
	# Notif staff = COMPLÉMENT non bloquant (le badge resoumis est la garantie) : un échec mail ne
	# doit pas annuler la re-soumission déjà committée.
	try:
		from admission.api.notifications import send_resubmit_staff_notification
		send_resubmit_staff_notification(applicant)
	except Exception:
		frappe.logger("public").warning(f"Notif staff re-soumission échouée: {frappe.get_traceback()}")
	log_event("candidate_resubmit", "success", dossier_id=applicant.name)
	return _ok({"dossier_id": applicant.name, "resoumis": True})


@frappe.whitelist(allow_guest=True, methods=["GET"])
def get_dossier(dossier_id=None, token=None):
	dossier_id = dossier_id or _value("dossier_id")
	token = token or _value("token")
	try:
		applicant = _get_applicant(dossier_id, token)
	except DossierTokenExpired:
		return _error("TOKEN_EXPIRED", "Lien de dossier expiré. Demandez un nouveau code OTP.", 403)
	except Exception:
		return _error("INVALID_DOSSIER", "Identifiants de dossier invalides.", 403)
	return _ok(_serialize_dossier(applicant))


@frappe.whitelist(allow_guest=True, methods=["POST"])
@rate_limit(key="email", limit=3, seconds=60 * 60)
def recover_dossier(email=None):
	"""LOT M (M7/A0.2) — « retrouver mon dossier » : renvoie un lien de reprise par e-mail.

	ANTI-ÉNUMÉRATION : réponse UNIFORME que l'adresse corresponde ou non à un dossier
	(aucune fuite d'existence). Le token est TOURNÉ (l'ancien lien meurt — un demandeur
	illégitime ne gagne qu'un lien envoyé… à la boîte du candidat) et l'OTP est remis à
	zéro : la double barrière s'applique à l'arrivée (SEC-4). Rate-limit par adresse.
	"""
	email = (email or _value("email") or "").strip().lower()
	generic = _ok({"message": "Si un dossier actif correspond à cette adresse, un lien de reprise vient d'être envoyé."})
	if not email or "@" not in email or len(email) > 140:
		return generic
	names = frappe.get_all(
		"Admission Applicant",
		filters={"email": email, "anonymized": ("!=", 1), "status": ("not in", ["REF", "DES", "INS"])},
		pluck="name", order_by="modified desc", limit=1,
	)
	if not names:
		log_event("recover_dossier", "no_match", ref=email.split("@")[-1])  # domaine seul, pas de PII
		return generic
	applicant = frappe.get_doc("Admission Applicant", names[0])
	new_token = _generate_token()
	applicant.dossier_token_hash = _hash(new_token)
	applicant.token_expires_at = add_days(now_datetime(), TOKEN_TTL_DAYS)
	applicant.otp_verified = 0  # nouvelle session → re-vérification OTP exigée (double barrière)
	applicant.save(ignore_permissions=True)
	frappe.db.commit()
	from admission.api.notifications import send_recovery_link
	send_recovery_link(applicant, new_token)
	log_event("recover_dossier", "sent", dossier_id=applicant.name)
	return generic


@frappe.whitelist(allow_guest=True, methods=["POST"])
@rate_limit(key="dossier_id", limit=5, seconds=60 * 60)
def request_data_deletion(dossier_id=None, token=None, confirm=None):
	"""DAT-1 self-service — droit à l'effacement (loi 2017-20).

	(LOT G : décorateurs RESTITUÉS — l'insertion de recover_dossier en LOT M les avait
	captés, dé-whitelistant l'endpoint ; la preuve HTTP G2 a détecté la régression.)

	Acte IRRÉVERSIBLE → auth forte : token (SEC-1) + OTP vérifié (SEC-4) + confirmation
	explicite. Déclenche l'anonymisation sélective : la PII est effacée ; la preuve de
	consentement (art. 29) et les justificatifs comptables (OHADA) sont CONSERVÉS dé-liés
	(carve-out DAT-1) — pas d'effacement « total » trompeur.
	"""
	dossier_id = dossier_id or _value("dossier_id")
	token = token or _value("token")
	confirm = confirm or _value("confirm")
	try:
		applicant = _get_applicant(dossier_id, token)
	except DossierTokenExpired:
		return _error("TOKEN_EXPIRED", "Lien de dossier expiré. Demandez un nouveau code OTP.", 403)
	except Exception:
		return _error("INVALID_DOSSIER", "Identifiants de dossier invalides.", 403)
	otp_err = _require_otp_verified(applicant)
	if otp_err:
		return otp_err
	if str(confirm).strip().lower() not in ("true", "1", "yes", "oui"):
		return _error(
			"CONFIRMATION_REQUIRED",
			"L'effacement est irréversible — confirmation explicite requise (confirm=true).",
			400,
		)
	from admission.api.retention import anonymize_applicant
	anonymize_applicant(applicant.name)
	return _ok({
		"dossier_id": applicant.name,
		"anonymized": True,
		"message": (
			"Vos données personnelles ont été effacées (anonymisées) de façon irréversible. "
			"Conformément à la loi 2017-20, la preuve de votre consentement (art. 29) et les "
			"justificatifs comptables (OHADA) sont CONSERVÉS de façon dé-liée/pseudonymisée : "
			"ils ne contiennent plus de données personnelles identifiantes."
		),
	})


@frappe.whitelist(allow_guest=True, methods=["POST"])
@rate_limit(key="dossier_id", limit=10, seconds=60 * 60)
def submit_payment_online(dossier_id=None, token=None, idempotency_key=None, consent_refund=None):
	dossier_id = dossier_id or _value("dossier_id")
	token = token or _value("token")
	idempotency_key = idempotency_key or _value("idempotency_key")
	consent_refund = consent_refund or _value("consent_refund")
	try:
		applicant = _get_applicant(dossier_id, token)
	except DossierTokenExpired:
		return _error("TOKEN_EXPIRED", "Lien de dossier expiré. Demandez un nouveau code OTP.", 403)
	except Exception:
		return _error("INVALID_DOSSIER", "Identifiants de dossier invalides.", 403)
	otp_err = _require_otp_verified(applicant)
	if otp_err:
		return otp_err
	# D-CONF-01 (verrou 3, défense en profondeur) : pas d'INITIATION de paiement sur un dossier clos
	# (symétrique de la garde BRO de declare_payment_offline ; le garant reste _promote_payment).
	if applicant.status in PAYMENT_FORBIDDEN_STATES:
		return _error("INVALID_STATE", f"Paiement impossible : dossier clos ({applicant.status}).", 409)
	if not consent_refund:
		return _error("REFUND_CONSENT_REQUIRED", "Le consentement au caractere non remboursable est requis.", 400)
	from admission.api.legal import _get_active_legal_document, _record_consent
	refund_doc = _get_active_legal_document("REFUND_POLICY")
	if not refund_doc:
		return _error("LEGAL_DOCUMENT_MISSING", "Texte legal (REFUND_POLICY) non disponible.", 503)
	# Lot 3a — porte paiement : toutes les pièces requises doivent être fournies (uploaded/verified).
	# Garde AVANT _ensure_fee : fail-fast, aucun effet de bord si refus. Le back fait foi (le front 3b
	# n'est qu'une garde UX). Hors scope : enrollment / canal staff (prepare_online_payment partagé).
	manquantes = pieces_requises_manquantes(applicant)
	if manquantes:
		labels = ", ".join(m["label"] for m in manquantes)
		return _error("PIECES_MANQUANTES",
			f"Pièces obligatoires manquantes : {labels}. Merci de les déposer avant de payer.", 409)
	fee = _ensure_fee(applicant)
	already_paid = _assert_fee_unpaid(fee)  # garde amont B1 (anti double-débit, critère autoritaire)
	if already_paid:
		return already_paid
	_record_consent(applicant.name, "REFUND_ACKNOWLEDGMENT", refund_doc.name)
	log_event("payment_online", "initiated", dossier_id=applicant.name, fee_type="application")
	return _ok(prepare_online_payment(applicant, fee, idempotency_key=idempotency_key))


@frappe.whitelist(allow_guest=True, methods=["POST"])
@rate_limit(key="dossier_id", limit=10, seconds=60 * 60)
def declare_payment_offline(dossier_id=None, token=None, mode=None, reference=None, idempotency_key=None, consent_refund=None):
	dossier_id = dossier_id or _value("dossier_id")
	token = token or _value("token")
	mode = mode or _value("mode") or "Bank"
	reference = reference or _value("reference")
	idempotency_key = idempotency_key or _value("idempotency_key")
	consent_refund = consent_refund or _value("consent_refund")
	try:
		applicant = _get_applicant(dossier_id, token)
	except DossierTokenExpired:
		return _error("TOKEN_EXPIRED", "Lien de dossier expiré. Demandez un nouveau code OTP.", 403)
	except Exception:
		return _error("INVALID_DOSSIER", "Identifiants de dossier invalides.", 403)
	otp_err = _require_otp_verified(applicant)
	if otp_err:
		return otp_err
	# SEC-5 : mode borné {cash, bank} sans .lower() sur non-str ; référence bornée.
	mode_norm = str(mode).strip().lower()
	if mode_norm not in {"cash", "bank"}:
		return _error("MODE_INVALID", "Mode de paiement invalide (cash ou bank).", 400)
	if reference and len(str(reference)) > 140:
		return _error("REFERENCE_INVALID", "Reference de paiement trop longue.", 400)
	if not consent_refund:
		return _error("REFUND_CONSENT_REQUIRED", "Le consentement au caractere non remboursable est requis.", 400)
	from admission.api.legal import _get_active_legal_document, _record_consent as _record_consent_m2
	refund_doc = _get_active_legal_document("REFUND_POLICY")
	if not refund_doc:
		return _error("LEGAL_DOCUMENT_MISSING", "Texte legal (REFUND_POLICY) non disponible.", 503)
	if applicant.status != "BRO":
		return _error("INVALID_STATE", "Offline declaration is only allowed from BRO.", 409)
	# Lot 3a — porte paiement (offline) : mêmes requises fournies avant de déclarer un paiement.
	manquantes = pieces_requises_manquantes(applicant)
	if manquantes:
		labels = ", ".join(m["label"] for m in manquantes)
		return _error("PIECES_MANQUANTES",
			f"Pièces obligatoires manquantes : {labels}. Merci de les déposer avant de payer.", 409)
	fee = _ensure_fee(applicant)
	payment = frappe.get_doc(
		{
			"doctype": "Applicant Fee Payment",
			"applicant_fee": fee.name,
			"applicant": applicant.name,
			"payment_mode": "Cash" if str(mode).strip().lower() == "cash" else "Bank",
			"source": "espece" if str(mode).strip().lower() == "cash" else "banque",  # ADM-DEBT-25
			"amount_xof": fee.amount_xof,
			"payment_status": "Pending",
			"paid_at": now_datetime(),
			"provider_reference": reference,
			"idempotency_key": idempotency_key,
		}
	)
	payment.insert(ignore_permissions=True)
	_record_consent_m2(applicant.name, "REFUND_ACKNOWLEDGMENT", refund_doc.name)
	# DEC-228/R1 : PAS de capture promo au declare (Pending) — la promo est figée à la
	# CONFIRMATION du frais 1 par la cascade partagée ; un declare rejeté ne fige rien.
	# LOT F (E2E HTTP) : le candidat Guest ne passe PAS validate_workflow (get_transitions
	# exige read → PermissionError Frappe) — même pattern que resubmit_complement (INC→SOU) :
	# db.set_value (aucun effet contrôleur attendu sur BRO→SOP) + Transition Log manuel fidèle.
	frappe.db.set_value("Admission Applicant", applicant.name, "status", "SOP")
	applicant.status = "SOP"  # cohérence de l'objet en mémoire (mail SOP, log, réponse)
	_record_candidate_transition(applicant.name, "BRO", "SOP")
	frappe.db.commit()
	# #1 (AUDIT-UF) : PAS de notif UF au Pending. UF est notifié à la CONFIRMATION
	# (hook on_payment_update sur Pending→Confirmed), sinon UF avalerait le Confirmed.
	# LOT M (M5) : instructions de paiement par mail — espèces (Direction) ou virement
	# (RIB Coris réel + PDF joint). Non-bloquant.
	from admission.api.notifications import send_offline_submission
	send_offline_submission(applicant, fee, mode_norm)
	log_event("payment_offline", "declared", dossier_id=applicant.name, mode=mode_norm)
	return _ok({"dossier_id": applicant.name, "statut": "SOP", "payment_id": payment.name})


def _notify_uf_safe(applicant, fee, payment):
	try:
		from admission.api.notify_uf import notify_uf_payment
		notify_uf_payment(applicant=applicant, fee=fee, payment=payment)
	except Exception:
		frappe.log_error(
			title="UF notification wrapper failed (public)",
			message=frappe.get_traceback(),
		)
		frappe.logger("public").error(
			f"UF notification failed (non-blocking): {frappe.get_traceback()}"
		)


def apply_confirmed_payment_cascade(applicant, fee):
	"""Cascade commune à la confirmation d'un paiement (offline confirm ET webhook online).

	Pose fee.status="Paid" (#3, admission-local, symétrie online) et transitionne BRO/SOP→SOU.
	NE notifie PAS UF : le hook on_payment_update le fait au passage Pending→Confirmed (#1/DEC-221).
	Idempotent : n'écrit que si un changement est nécessaire.

	Capture promo (DEC-228, ruling R1 C2-BOURSES) : le taux promo est figé ICI, à la
	CONFIRMATION du frais 1 (fee_type application/competition), pour les 3 canaux — confirm
	offline (staff) et webhook online (candidat/agent). JAMAIS au frais 2 (enrollment),
	JAMAIS au declare (un declare rejeté ne fige rien).
	"""
	if fee and getattr(fee, "fee_type", None) in FRAIS1_FEE_TYPES:
		_capture_promo_if_eligible(applicant)
	if fee and fee.status != "Paid":
		fee.status = "Paid"
		fee.save(ignore_permissions=True)
	if applicant.status in {"BRO", "SOP"}:
		from_status = applicant.status
		if frappe.session.user == "Guest":
			# LOT F (E2E HTTP) : webhook/candidat Guest — validate_workflow exige read
			# (PermissionError). Pattern resubmit_complement : db.set_value + log manuel.
			# Le chemin STAFF (confirm offline) garde applicant.save (gates re-validées).
			frappe.db.set_value("Admission Applicant", applicant.name, "status", "SOU")
			applicant.status = "SOU"
			_record_candidate_transition(applicant.name, from_status, "SOU")
		else:
			applicant.status = "SOU"
			applicant.save(ignore_permissions=True)


def _online_payment_exists(reference):
	return bool(
		frappe.get_all("Applicant Fee Payment", filters={"provider_reference": reference}, pluck="name", limit=1)
	)


def prepare_online_payment(applicant, fee, *, idempotency_key=None, descriptor_amount=None, ventilation=None):
	"""Cœur commun candidat + agent : pré-crée un Pending Online LIÉ (applicant + provider_reference
	serveur, sur le `fee` PASSÉ — application OU enrollment) et renvoie le descriptor (dispositif
	simulation dev — DEC-216/217). Le webhook promeut ce Pending par provider_reference (phase d).
	Idempotent : réutilise le Pending d'une même référence (pas de doublon).
	`descriptor_amount`/`ventilation` (frais 2) : override du montant affiché + ventilation acompte ;
	omis (frais 1) → descriptor IDENTIQUE à l'ancien (non-régression candidat)."""
	reference = idempotency_key or secrets.token_hex(12)
	if not _online_payment_exists(reference):
		frappe.get_doc(
			{
				"doctype": "Applicant Fee Payment",
				"applicant_fee": fee.name,
				"applicant": applicant.name,
				"payment_mode": "Online",
				"source": "online",  # ADM-DEBT-25 : traçabilité canal
				"amount_xof": fee.amount_xof,
				"payment_status": "Pending",
				"provider": "kkiapay",
				"provider_reference": reference,
				"idempotency_key": idempotency_key,
			}
		).insert(ignore_permissions=True)
	# LOT KKIAPAY : tout ce dont le widget a besoin (clé PUBLIQUE seulement — jamais
	# private/secret côté front) ; `data` fait l'aller-retour widget→webhook (stateData).
	from admission.api import kkiapay as kkiapay_client
	descriptor = {
		"provider": "kkiapay",
		"mode": kkiapay_client.mode(),  # mock (DEV) | sandbox | live
		"public_key": kkiapay_client.public_key(),
		"sandbox": kkiapay_client.is_sandbox(),
		"amount_xof": fee.amount_xof if descriptor_amount is None else descriptor_amount,
		"reference": reference,
		"data": json.dumps({"reference": reference, "sdk": "lanem-admission"}),
		"webhook_required": True,
	}
	if ventilation is not None:
		descriptor["ventilation"] = ventilation
	return descriptor


def prepare_enrollment_online_payment(applicant, fee, *, acompte_xof=0, idempotency_key=None):
	"""Variant frais 2 (enrollment) : ventile l'acompte (D11), pré-crée le Pending sur le frais
	ENROLLMENT (le bon fee_type — corrige le bug recette : plus de rattachement au frais 1), descriptor
	amount=total (frais2+acompte) + ventilation + hint `fee_type`. Réutilise prepare_online_payment."""
	acompte_xof = int(acompte_xof or 0)
	total = (fee.amount_xof or 0) + acompte_xof
	if acompte_xof > 0:
		applicant.acompte_xof = acompte_xof
		applicant.save(ignore_permissions=True)
	descriptor = prepare_online_payment(
		applicant, fee, idempotency_key=idempotency_key,
		descriptor_amount=total, ventilation={"frais2": fee.amount_xof, "acompte": acompte_xof},
	)
	descriptor["fee_type"] = "enrollment"  # hint consommé par le durcissement du fallback insert webhook
	return descriptor


def expire_stale_online_pending(older_than_hours=48):
	"""PC1-D1 (VAGUE-PAY-FIX) : NON TERMINAL. Ne rejette plus les Pending Online périmés (>48h) —
	il les MARQUE 'Stale - awaiting webhook' (file de réconciliation OPS) SANS toucher payment_status,
	pour qu'un success VÉRIFIÉ tardif puisse encore les promouvoir via le handler webhook corrigé.
	NB : la file mêle vrais abandons ET succès perdus (webhook jamais reçu) — Q1=NON (pas de lookup
	KkiaPay par référence) → distinction = réconciliation OPS manuelle (dashboard)."""
	cutoff = add_to_date(now_datetime(), hours=-older_than_hours)
	names = frappe.get_all(
		"Applicant Fee Payment",
		filters={"payment_status": "Pending", "payment_mode": "Online", "creation": ["<", cutoff]},
		pluck="name",
	)
	for name in names:
		frappe.db.set_value("Applicant Fee Payment", name, "reconciliation",
		                    "Stale - awaiting webhook", update_modified=False)
	return len(names)


@frappe.whitelist(allow_guest=True, methods=["POST"])
@rate_limit(key="dossier_id", limit=10, seconds=60 * 60)
def submit_enrollment_payment_online(dossier_id=None, token=None, idempotency_key=None, acompte_xof=None,
                                     consent_refund=None, consent_data_transfer=None):
	dossier_id = dossier_id or _value("dossier_id")
	token = token or _value("token")
	idempotency_key = idempotency_key or _value("idempotency_key")
	acompte_xof = acompte_xof or _value("acompte_xof") or 0  # validé plus bas (SEC-5)
	consent_refund = consent_refund or _value("consent_refund")
	consent_data_transfer = consent_data_transfer or _value("consent_data_transfer")
	try:
		applicant = _get_applicant(dossier_id, token)
	except DossierTokenExpired:
		return _error("TOKEN_EXPIRED", "Lien de dossier expiré. Demandez un nouveau code OTP.", 403)
	except Exception:
		return _error("INVALID_DOSSIER", "Identifiants de dossier invalides.", 403)
	otp_err = _require_otp_verified(applicant)
	if otp_err:
		return otp_err
	if applicant.status != "ACC":
		return _error("INVALID_STATE", "Enrollment payment is only allowed from ACC.", 409)
	# SEC-5 : acompte numérique + 0 ≤ x ≤ scolarité annuelle → ferme le sous-paiement (négatif).
	max_acompte = _resolve_fee_from_catalog(applicant.programme_code, "annual", getattr(applicant, "level_code", None))
	acompte_xof, acompte_err = _validate_amount(acompte_xof, 0, max_acompte)
	if acompte_err:
		return acompte_err
	if not consent_refund:
		return _error("REFUND_CONSENT_REQUIRED", "Le consentement au caractere non remboursable est requis.", 400)
	if not consent_data_transfer:
		return _error("DATA_TRANSFER_CONSENT_REQUIRED", "Le consentement au transfert de donnees est requis.", 400)
	from admission.api.legal import _get_active_legal_document, _record_consent
	refund_doc = _get_active_legal_document("REFUND_POLICY")
	transfer_doc = _get_active_legal_document("DATA_TRANSFER_CONSENT")
	if not refund_doc or not transfer_doc:
		return _error("LEGAL_DOCUMENT_MISSING", "Textes legaux (REFUND_POLICY, DATA_TRANSFER_CONSENT) non disponibles.", 503)
	fee = _ensure_enrollment_fee(applicant)
	if not fee:
		return _error("FEE_NOT_AVAILABLE", "Enrollment fee amount not available in catalog.", 500)
	already_paid = _assert_fee_unpaid(fee)  # garde amont B1 symétrique frais 2 (même critère autoritaire)
	if already_paid:
		return already_paid
	_record_consent(applicant.name, "REFUND_ACKNOWLEDGMENT", refund_doc.name)
	_record_consent(applicant.name, "DATA_TRANSFER", transfer_doc.name)
	log_event("enrollment_payment_online", "initiated", dossier_id=applicant.name)
	# Adopte le cœur (comme le frais 1) : pré-crée le Pending sur l'ENROLLMENT fee → le webhook le promeut.
	return _ok(prepare_enrollment_online_payment(applicant, fee, acompte_xof=acompte_xof, idempotency_key=idempotency_key))


@frappe.whitelist(allow_guest=True, methods=["POST"])
@rate_limit(key="dossier_id", limit=10, seconds=60 * 60)
def declare_enrollment_payment_offline(
	dossier_id=None, token=None, mode=None, reference=None, idempotency_key=None,
	acompte_xof=None, consent_refund=None, consent_data_transfer=None,
):
	dossier_id = dossier_id or _value("dossier_id")
	token = token or _value("token")
	mode = mode or _value("mode") or "Bank"
	reference = reference or _value("reference")
	idempotency_key = idempotency_key or _value("idempotency_key")
	acompte_xof = acompte_xof or _value("acompte_xof") or 0  # validé plus bas (SEC-5)
	consent_refund = consent_refund or _value("consent_refund")
	consent_data_transfer = consent_data_transfer or _value("consent_data_transfer")
	try:
		applicant = _get_applicant(dossier_id, token)
	except DossierTokenExpired:
		return _error("TOKEN_EXPIRED", "Lien de dossier expiré. Demandez un nouveau code OTP.", 403)
	except Exception:
		return _error("INVALID_DOSSIER", "Identifiants de dossier invalides.", 403)
	otp_err = _require_otp_verified(applicant)
	if otp_err:
		return otp_err
	if applicant.status != "ACC":
		return _error("INVALID_STATE", "Enrollment payment is only allowed from ACC.", 409)
	# SEC-5 : acompte numérique + 0 ≤ x ≤ scolarité annuelle → ferme le sous-paiement (négatif).
	max_acompte = _resolve_fee_from_catalog(applicant.programme_code, "annual", getattr(applicant, "level_code", None))
	acompte_xof, acompte_err = _validate_amount(acompte_xof, 0, max_acompte)
	if acompte_err:
		return acompte_err
	if not consent_refund:
		return _error("REFUND_CONSENT_REQUIRED", "Le consentement au caractere non remboursable est requis.", 400)
	if not consent_data_transfer:
		return _error("DATA_TRANSFER_CONSENT_REQUIRED", "Le consentement au transfert de donnees est requis.", 400)
	from admission.api.legal import _get_active_legal_document, _record_consent as _record_consent_m4
	refund_doc = _get_active_legal_document("REFUND_POLICY")
	transfer_doc = _get_active_legal_document("DATA_TRANSFER_CONSENT")
	if not refund_doc or not transfer_doc:
		return _error("LEGAL_DOCUMENT_MISSING", "Textes legaux (REFUND_POLICY, DATA_TRANSFER_CONSENT) non disponibles.", 503)
	fee = _ensure_enrollment_fee(applicant)
	if not fee:
		return _error("FEE_NOT_AVAILABLE", "Enrollment fee amount not available in catalog.", 500)
	payment = frappe.get_doc(
		{
			"doctype": "Applicant Fee Payment",
			"applicant_fee": fee.name,
			"applicant": applicant.name,
			"payment_mode": "Cash" if str(mode).strip().lower() == "cash" else "Bank",
			"source": "espece" if str(mode).strip().lower() == "cash" else "banque",  # ADM-DEBT-25
			"amount_xof": fee.amount_xof,
			"payment_status": "Pending",
			"paid_at": now_datetime(),
			"provider_reference": reference,
			"idempotency_key": idempotency_key,
		}
	)
	payment.insert(ignore_permissions=True)
	_record_consent_m4(applicant.name, "REFUND_ACKNOWLEDGMENT", refund_doc.name)
	_record_consent_m4(applicant.name, "DATA_TRANSFER", transfer_doc.name)
	if acompte_xof > 0:
		applicant.acompte_xof = acompte_xof
		applicant.save(ignore_permissions=True)
	frappe.db.commit()
	# #1 : pas de notif UF au Pending (cf. declare_payment_offline) — notif au Confirmed via le hook.
	# LOT M (M5) : instructions de paiement du FRAIS 2 (même gabarit, libellé adapté).
	from admission.api.notifications import send_offline_submission
	send_offline_submission(applicant, fee,
		"cash" if str(mode).strip().lower() == "cash" else "bank",
		fee_label="frais d'inscription")
	log_event("enrollment_payment_offline", "declared", dossier_id=applicant.name)
	return _ok({
		"dossier_id": applicant.name,
		"statut": applicant.status,
		"payment_id": payment.name,
		"ventilation": {"frais2": fee.amount_xof, "acompte": acompte_xof},
	})


CAMPUS_ENSURE_PERSON_PATH = "/api/method/portal_app.api.external.v1.person_api.ensure_person"


def _resolve_person_from_campus(email, first_name, last_name, phone):
	"""Call campus ensure_person endpoint to resolve or create a Person.

	Returns PERS-NNNNN on success, None on failure.
	Ref: DEC-226 option A — admission calls campus, not UF.
	"""
	config = _get_campus_config()
	if not config:
		# RECETTE/DEV uniquement (campus non encore branché) : identité Person LOCALE
		# déterministe (même email → même id), pour dérouler le tunnel candidat sans le
		# campus. GARDÉ par flag `allow_local_person_resolution` (OFF par défaut) et
		# SIGNALÉ par le gate recette (MODE-local-person) → ne peut atteindre la prod
		# silencieusement. Le pont INS exige toujours le vrai campus (id PERS-REC- distinct).
		if frappe.conf.get("allow_local_person_resolution"):
			import hashlib
			local_id = "PERS-REC-" + hashlib.sha1((email or "").lower().encode()).hexdigest()[:10].upper()
			log_event("person_resolve", "local_recette", person_id=local_id, level="warning")
			return local_id
		log_event("person_resolve", "skipped_no_config", level="error")
		return None

	if not _pii_transport_allowed(config["url"], context="ensure_person→campus"):
		return None

	payload = {
		"email": email,
		"first_name": first_name,
		"last_name": last_name or "",
		"phone": phone or "",
	}

	try:
		resp = requests.post(
			config["url"].rstrip("/") + CAMPUS_ENSURE_PERSON_PATH,
			json=payload,
			headers={"Content-Type": "application/json", "X-API-Key": config["token"]},
			timeout=15,
		)
		resp.raise_for_status()
		result = resp.json()
		data = result.get("data") or result.get("message", {}).get("data") or {}
		person_id = data.get("person_id")
		if person_id:
			log_event("person_resolve", "success", person_id=person_id)
			return person_id
		log_event("person_resolve", "no_person_id", level="error")
		return None
	except requests.RequestException as exc:
		log_event("person_resolve", "failed", error=str(exc), level="error")
		return None
