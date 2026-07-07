"""DAT-1 — Rétention des données (loi 2017-20).

Socle : purge des données éphémères (OTP expirés) + anonymisation SÉLECTIVE des dossiers
abandonnés/terminaux. Les durées sont lues du singleton « Admission Retention Policy »
(placeholder par défaut tant que le juriste n'a pas tranché — le mécanisme ne les attend pas).

Anonymisation sélective (carve-out) : on scrubbe la PII de l'Admission Applicant (champs
déclarés dans le hook natif `user_data_fields`) + ses pièces (File) + son historique (Version),
mais on PRÉSERVE Consent Record (preuve art. 29) et Applicant Fee Payment (compta OHADA) —
qui restent liés à un Applicant désormais sans PII (pseudonymisé).

⚠️ Ne purge PAS le hash du token sur simple expiration 7 j : un token expiré reste récupérable
par re-OTP (SEC-TOKEN-EXPIRY). Le hash token n'est effacé qu'à l'anonymisation (abandon/terminal).

Ref: AUDIT-DAT1-RETENTION, loi 2017-20, ADM-LEG, décisions (anonymisation native sélective).
"""

from __future__ import annotations

import frappe
from frappe.utils import add_days, now_datetime

from admission.api._log import log_event


ANON_PLACEHOLDER = "[REDACTED]"

# Durées par DÉFAUT (placeholder) — le juriste les règle dans le singleton Retention Policy.
RETENTION_DEFAULTS = {
	"abandoned_bro_days": 90,
	"ref_des_retention_days": 365,
	"post_ins_retention_days": 30,
}


def _retention_days(key):
	"""Durée (jours) lue du singleton Admission Retention Policy, sinon défaut placeholder."""
	value = None
	try:
		value = frappe.db.get_single_value("Admission Retention Policy", key)
	except Exception:
		value = None
	return int(value) if value else RETENTION_DEFAULTS.get(key, 0)


def _pii_fields(doctype):
	"""Champs PII à scrubber, lus du hook natif `user_data_fields` (source de vérité)."""
	for entry in (frappe.get_hooks("user_data_fields") or []):
		if isinstance(entry, dict) and entry.get("doctype") == doctype:
			return list(entry.get("redact_fields") or [])
	return []


def anonymize_applicant(applicant_name):
	"""Anonymisation sélective d'un dossier (droit à l'effacement, loi 2017-20).

	Scrubbe la PII (déclarée `user_data_fields`) + credentials + pièces (File) + Version.
	PRÉSERVE Consent Record + Paiement (carve-out) → liés à un Applicant pseudonymisé.
	"""
	# LOT G (fix prouvé HTTP) : redaction TYPÉE — "[REDACTED]" dans une colonne Date
	# (bac_date) lève une erreur SQL → l'anonymisation (self-service ET purges scheduler)
	# crashait à l'exécution. Champs texte → placeholder ; autres types → NULL.
	meta = frappe.get_meta("Admission Applicant")
	_text_types = {"Data", "Small Text", "Text", "Long Text", "Text Editor"}
	updates = {}
	for f in _pii_fields("Admission Applicant"):
		df = meta.get_field(f)
		updates[f] = ANON_PLACEHOLDER if (df and df.fieldtype in _text_types) else None
	updates.update({
		"dossier_token_hash": None,
		"otp_email_hash": None,
		"otp_phone_hash": None,
		"otp_verified": 0,
		"anonymized": 1,  # anomalie 4 : flag d'idempotence robuste (≠ email == "[REDACTED]")
	})
	frappe.db.set_value("Admission Applicant", applicant_name, updates, update_modified=False)

	# Pièces : supprimer les File PII rattachés au dossier (diplômes, relevés, etc.).
	file_names = frappe.get_all(
		"File",
		filters={"attached_to_doctype": "Admission Applicant", "attached_to_name": applicant_name},
		pluck="name",
	)
	for name in file_names:
		frappe.delete_doc("File", name, ignore_permissions=True, force=True)

	# Anomalie 2 : vider le champ file (Link→File) des lignes pièce → plus de lien pendant.
	piece_rows = frappe.get_all(
		"Applicant Piece",
		filters={"parenttype": "Admission Applicant", "parent": applicant_name},
		pluck="name",
	)
	for row in piece_rows:
		frappe.db.set_value("Applicant Piece", row, "file", None, update_modified=False)

	# Historique PII (Version, track_changes=1) : effacer pour ne pas laisser la PII survivre.
	frappe.db.delete("Version", {"ref_doctype": "Admission Applicant", "docname": applicant_name})

	# Consent Record + Applicant Fee Payment : NON touchés (carve-out preuve/compta).
	return {"applicant": applicant_name, "files_deleted": len(file_names)}


def purge_expired_otp():
	"""Scrubbe les hashes OTP expirés (sûr : ré-émissibles). NE touche PAS le token (récup SEC)."""
	names = frappe.get_all(
		"Admission Applicant",
		filters={"otp_expires_at": ["<", now_datetime()], "otp_email_hash": ["is", "set"]},
		pluck="name",
	)
	for name in names:
		# Scrubbe le CODE OTP expiré (hashes). NE touche PAS `otp_verified` : le statut de
		# vérification PERSISTE entre visites (SEC-OTP) — ne pas dé-vérifier un candidat.
		frappe.db.set_value(
			"Admission Applicant", name,
			{"otp_email_hash": None, "otp_phone_hash": None},
			update_modified=False,
		)
	return {"otp_cleared": len(names)}


def purge_abandoned_dossiers():
	"""Anonymise les dossiers BRO inactifs au-delà du délai (jamais soumis ni payés)."""
	cutoff = add_days(now_datetime(), -_retention_days("abandoned_bro_days"))
	names = frappe.get_all(
		"Admission Applicant",
		filters={"status": "BRO", "modified": ["<", cutoff], "anonymized": ["!=", 1]},
		pluck="name",
	)
	for name in names:
		anonymize_applicant(name)
	return {"abandoned_anonymized": len(names)}


def purge_terminal_dossiers():
	"""Anonymise les dossiers terminaux au-delà de leur délai de conservation.

	REF/DES (refus/désistement) après délai de recours ; INS (transféré au campus) après délai.
	Consent + Paiement préservés par l'anonymiseur (carve-out).
	"""
	now = now_datetime()
	ref_des_cutoff = add_days(now, -_retention_days("ref_des_retention_days"))
	ref_des = frappe.get_all(
		"Admission Applicant",
		filters={
			"status": ["in", ["REF", "DES"]],
			"modified": ["<", ref_des_cutoff],
			"anonymized": ["!=", 1],
		},
		pluck="name",
	)
	ins_cutoff = add_days(now, -_retention_days("post_ins_retention_days"))
	ins = frappe.get_all(
		"Admission Applicant",
		filters={"status": "INS", "modified": ["<", ins_cutoff], "anonymized": ["!=", 1]},
		pluck="name",
	)
	for name in ref_des + ins:
		anonymize_applicant(name)
	return {"terminal_anonymized": len(ref_des) + len(ins)}


PURGE_NOTICE_DAYS = 7


def notify_expiring_drafts():
	"""Préavis J-7 avant l'anonymisation des brouillons BRO inactifs (M9, RGPD transparence).

	Fenêtre : inactif depuis (abandoned_bro_days − 7) jours. Anti-double-envoi :
	flag `purge_notice_sent_at` (db.set_value update_modified=False — l'envoi du
	préavis ne doit PAS repousser la purge, seule une action du candidat le fait).
	Best-effort : un dossier déjà au-delà du délai complet est purgé sans préavis
	(cas limite de premier déploiement). Hook daily dédié (hooks.py).
	"""
	from admission.api.notifications import send_purge_notice

	cutoff = add_days(now_datetime(), -(_retention_days("abandoned_bro_days") - PURGE_NOTICE_DAYS))
	names = frappe.get_all(
		"Admission Applicant",
		filters={
			"status": "BRO",
			"modified": ["<", cutoff],
			"anonymized": ["!=", 1],
			"purge_notice_sent_at": ["is", "not set"],
		},
		pluck="name",
	)
	sent = 0
	for name in names:
		try:
			applicant = frappe.get_doc("Admission Applicant", name)
			send_purge_notice(applicant, days_left=PURGE_NOTICE_DAYS)
			frappe.db.set_value("Admission Applicant", name,
			                    "purge_notice_sent_at", now_datetime(), update_modified=False)
			sent += 1
		except Exception:
			frappe.logger("retention").warning(
				f"Purge notice failed for {name} (non-blocking): {frappe.get_traceback()}")
	frappe.db.commit()
	frappe.logger("retention").info(f"Purge notices sent: {sent}/{len(names)}")
	return {"purge_notices_sent": sent}


def scheduled_retention_run():
	"""Point d'entrée scheduler quotidien (hooks.py scheduler_events.daily). Non-bloquant."""
	summary = {}
	for step in (purge_expired_otp, purge_abandoned_dossiers, purge_terminal_dossiers):
		try:
			summary.update(step())
		except Exception as exc:
			frappe.logger("retention").error(
				f"Retention step {step.__name__} failed: {frappe.get_traceback()}"
			)
			# OBS-3 item 3 : conformité rétention — une étape de purge qui casse cesse d'être
			# avalée ; trace corrélée/structurée en error (à côté du logger texte).
			log_event("retention_run", "step_failed", step=step.__name__, error=str(exc), level="error")
	frappe.db.commit()
	frappe.logger("retention").info(f"Retention run complete: {summary}")
	return summary
