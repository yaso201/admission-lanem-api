"""Webhook paiement KkiaPay (LOT KKIAPAY — ferme ADM-DEBT-74).

Contrat provider (doc + plugin officiel) : POST JSON brut
  {transactionId, isPaymentSucces, event: transaction.success|transaction.failed,
   amount, method, stateData: {reference, sdk}, failureCode?...}
+ en-tête `x-kkiapay-secret` = hash secret saisi au dashboard (comparaison constant-time).

Modèle de sécurité (celui du plugin WooCommerce officiel, durci) :
  1. en-tête secret valide (fail-closed SEC-2 : pas de secret configuré → REJET) ;
  2. le payload n'est JAMAIS cru sur parole : re-vérification serveur
     `kkiapay.verify_transaction(transactionId)` (3 clés marchand) — status SUCCESS
     ET montant >= Pending attendu ;
  3. promotion UNIQUEMENT : le Pending lié par `stateData.reference`
     (= provider_reference posé par prepare_online_payment) est promu Confirmed.
     Plus AUCUN insert fallback : un webhook sans Pending lié = 409 (tout paiement
     online passe par l'initiation — garde W3).
Idempotent : replay sur Confirmed/Paid → ok sans effet ; retentatives KkiaPay
(5 × ~500 ms) couvertes. `transaction.failed` → Pending→Rejected (silencieux, même
pattern que expire_stale_online_pending).
"""

import hmac
import json

import frappe
from frappe.utils import now_datetime

from admission.api.kkiapay import verify_transaction
from admission.api.public import (
	_error,
	_ok,
	apply_confirmed_payment_cascade,
	PAYMENT_FORBIDDEN_STATES,
)
from admission.api.receipt import send_payment_receipt
from admission.api.notify_uf import notify_uf_payment  # PC2-quater : notif UF post-commit (hors verrou)
from admission.api._log import log_event  # OBS-2 : log structuré + corrélation (provider_reference / dossier_id)


def _valid_header_secret(received):
	secret = frappe.conf.get("admission_payment_webhook_secret")
	if not secret:
		# SEC-2 : fail-CLOSED. Sans secret configuré, aucune notification de paiement
		# n'est authentifiable → on REJETTE (jamais d'acceptation par défaut).
		return False
	return hmac.compare_digest(str(secret), str(received or ""))


def _parse_payload():
	"""Corps JSON BRUT (KkiaPay poste du JSON, pas du form-encodé). None = invalide."""
	raw = getattr(getattr(frappe, "request", None), "data", None)
	if not raw:
		return None
	try:
		payload = json.loads(raw)
	except (ValueError, TypeError):
		return None
	return payload if isinstance(payload, dict) else None


def _extract_reference(payload):
	"""provider_reference posé à l'initiation — aller-retour via l'attribut `data` du
	widget, restitué par KkiaPay dans `stateData` (dict ou chaîne JSON selon le canal)."""
	state = payload.get("stateData") or {}
	if isinstance(state, str):
		try:
			state = json.loads(state)
		except (ValueError, TypeError):
			state = {}
	if isinstance(state, dict) and state.get("reference"):
		return state["reference"]
	return payload.get("reference")  # compat simulateur DEV


def _find_payment_by_reference(reference):
	"""Retourne l'Applicant Fee Payment portant ce provider_reference (liage persisté), ou None."""
	if not reference:
		return None
	names = frappe.get_all(
		"Applicant Fee Payment", filters={"provider_reference": reference}, pluck="name", limit=1
	)
	return frappe.get_doc("Applicant Fee Payment", names[0]) if names else None


def _promote_payment(existing, transaction_id, reference, reconciliation=None):
	"""Promotion partagée Pending/Rejected→Confirmed (logique nominale RÉUTILISÉE pour la
	réconciliation tardive — anti-divergence). Pose txid + cascade BRO/SOP→SOU + reçu.
	`reconciliation` non vide → trace D-OBS-01 ('Promoted late')."""
	existing.reload()  # PC2-bis : doc re-lu SOUS le verrou ligne du caller (FOR UPDATE tenu jusqu'au
	                   # commit) → referme la fenêtre TimestampMismatch / lost-update (ceinture+bretelles).
	applicant = frappe.get_doc("Admission Applicant", existing.applicant)
	# D-CONF-01 (verrou 1 — LE GARANT) : jamais de Confirmed sur un dossier TERMINAL. Placé ICI, au
	# point d'étranglement UNIQUE des DEUX chemins de promotion (Pending→Confirmed via le handler ET
	# Rejected→Confirmed via la réconciliation « Promoted late ») → un webhook tardif sur un dossier
	# désisté/refusé/rejeté/inscrit ne peut plus encaisser. L'argent ayant bougé chez le provider, on
	# TRACE le refund (miroir orphelin), on NE confirme pas, et le webhook ne 500 jamais.
	if applicant.status in PAYMENT_FORBIDDEN_STATES:
		_refuse_terminal(existing, transaction_id, reference, applicant.status)
		return False
	fee = frappe.get_doc("Applicant Fee", existing.applicant_fee)
	existing.payment_status = "Confirmed"
	existing.paid_at = now_datetime()
	existing.provider = "kkiapay"
	existing.provider_transaction_id = transaction_id  # opposabilité + revert API
	if reconciliation:
		existing.reconciliation = reconciliation
	# PC2-quater (D-LOCK-IO-04) : le hook on_payment_update notifie UF par un POST HTTP SYNCHRONE (15 s).
	# Sous les verrous Payment+Fee (tenus jusqu'au commit), ce serait une I/O externe SOUS verrou (viole
	# C1 ; dégrade la sérialisation fee que PC2-ter apporte). On SUPPRIME la notif du hook sous verrou
	# (flag de ré-entrance) et on notifie UF APRÈS le commit (hors verrou). Le chemin Desk (confirm
	# offline) reste notifié par le hook : le flag n'est posé qu'ici, le temps du save.
	frappe.flags._notifying_uf_payment = True
	try:
		existing.save(ignore_permissions=True)           # Pending→Confirmed (notif hook supprimée ici)
		apply_confirmed_payment_cascade(applicant, fee)  # capture promo + fee Paid + BRO/SOP→SOU
		frappe.db.commit()                               # ← relâche les verrous Payment + Fee
	finally:
		frappe.flags._notifying_uf_payment = False
	try:
		notify_uf_payment(applicant=applicant, fee=fee, payment=existing)  # UF post-commit, HORS verrou
	except Exception:
		frappe.log_error(title="UF payment notification (webhook post-commit) failed",
		                 message=frappe.get_traceback())  # non-bloquant : paiement déjà commité
	send_payment_receipt(existing, applicant=applicant, fee=fee)  # reçu online (non-bloquant)
	log_event("webhook_payment", "success", dossier_id=applicant.name, ref=reference)
	return True


def _refuse_terminal(existing, transaction_id, reference, status):
	"""D-CONF-01 : l'argent a bougé chez le provider mais le dossier est TERMINAL → refus de
	promotion, tracé refund (JAMAIS de Confirmed sur dossier mort). Miroir de _orphan_trace ; le
	webhook renvoie un _ok (promoted:false), jamais un 500 (retentatives KkiaPay sans effet)."""
	frappe.db.set_value("Applicant Fee Payment", existing.name,
	                    {"reconciliation": "Refused - terminal state (refund due)",
	                     "provider_transaction_id": transaction_id}, update_modified=False)
	frappe.db.commit()
	log_event("webhook_payment", "refused_terminal_state",
	          dossier_id=getattr(existing, "applicant", None), ref=reference, level="warning")


def _orphan_trace(existing, transaction_id, reference):
	"""DEC-4 : l'argent a bougé mais le fee est DÉJÀ crédité → ORPHELIN tracé (refund OPS), JAMAIS de
	double promotion. Partagé : cas SÉQUENTIEL (check fee_resolved) ET cas CONCURRENT (course perdue à
	l'index unique → UniqueValidationError au save)."""
	frappe.db.set_value("Applicant Fee Payment", existing.name,
	                    {"reconciliation": "Orphan - refund due",
	                     "provider_transaction_id": transaction_id}, update_modified=False)
	frappe.db.commit()
	log_event("webhook_payment", "orphan_refund_due",
	          dossier_id=getattr(existing, "applicant", None), ref=reference, level="warning")
	return _ok({"accepted": True, "transition": None, "promoted": False, "orphan": True})


@frappe.whitelist(allow_guest=True, methods=["POST"])
def payment():
	payload = _parse_payload()
	if payload is None:
		return _error("WEBHOOK_PAYLOAD_INVALID", "Expected a JSON body.", 400)
	received = frappe.get_request_header("x-kkiapay-secret")
	if not _valid_header_secret(received):
		log_event("webhook_payment", "rejected_signature",
		          ref=payload.get("transactionId"), level="warning")
		return _error("WEBHOOK_SIGNATURE_INVALID", "Invalid payment webhook secret.", 403)

	reference = _extract_reference(payload)
	transaction_id = payload.get("transactionId")
	event = payload.get("event") or ""
	success = event == "transaction.success" or payload.get("isPaymentSucces") is True \
		or str(payload.get("status") or "").lower() in {"confirmed", "success", "paid"}

	# Reboucle par provider_reference (liage persisté à l'initiation, candidat OU agent).
	existing = _find_payment_by_reference(reference)
	if not existing:
		# Durcissement LOT KKIAPAY : plus d'insert fallback — tout paiement online est
		# initié (Pending pré-créé). Webhook orphelin = anomalie à investiguer.
		log_event("webhook_payment", "rejected_no_pending", ref=reference or transaction_id,
		          level="warning")
		return _error("PAYMENT_NOT_INITIATED",
		              "Paiement non initialise (aucun Pending lie pour cette reference).", 409)

	# ── transaction.failed → rejet du Pending lié, SOUS VERROU (DEC-5) ──────────────
	# (rejet silencieux : pas de hook UF) ; un success VÉRIFIÉ ultérieur le récupère (chemin SUCCESS).
	if not success:
		# Verrou ligne (SELECT…FOR UPDATE) + statut re-lu, avant toute mutation (DEC-5).
		st = frappe.db.get_value("Applicant Fee Payment", existing.name,
		                         "payment_status", for_update=True)
		rejected = None
		if st == "Pending":
			frappe.db.set_value("Applicant Fee Payment", existing.name,
			                    "payment_status", "Rejected", update_modified=False)
			frappe.db.commit()
			rejected = existing.name
		log_event("webhook_payment", "failed_event",
		          dossier_id=getattr(existing, "applicant", None), ref=reference)
		return _ok({"accepted": True, "transition": None, "rejected": rejected})

	# ── SUCCESS ─────────────────────────────────────────────────────────────────────
	# (0) Pré-check replay HORS verrou : évite l'appel verify sur les 5× retentatives KkiaPay.
	if existing.payment_status in ("Confirmed", "Paid"):
		log_event("webhook_payment", "replay", dossier_id=getattr(existing, "applicant", None), ref=reference)
		return _ok({"accepted": True, "payment_id": existing.name, "idempotent": True})

	# (1) DEC-1/#3 : la VÉRITÉ = verify_transaction, jamais le payload. Appel HORS verrou
	#     (lecture pure idempotente — ne JAMAIS tenir un FOR UPDATE à travers l'I/O 10 s, C1).
	tx = verify_transaction(transaction_id)
	verified = bool(tx) and str(tx.get("status") or "").upper() == "SUCCESS"
	try:
		tx_amount = int(float(tx.get("amount") or 0)) if tx else 0
	except (ValueError, TypeError):
		tx_amount = 0

	if not verified:
		# Provider injoignable / != SUCCESS. Pending → 409 (KkiaPay retente) ;
		# Rejected → vrai désistement préservé (pas de trace, pas de mutation).
		log_event("webhook_payment", "rejected_not_verified",
		          dossier_id=getattr(existing, "applicant", None), ref=reference, level="warning")
		if existing.payment_status == "Pending":
			return _error("PAYMENT_NOT_VERIFIED", "Transaction non verifiee aupres du provider.", 409)
		return _ok({"accepted": True, "transition": None, "promoted": False})

	# (2) DEC-5 : décision + mutation atomiques SOUS VERROU. Verrou ligne + re-lecture du statut
	#     (fenêtre tenue en ms — verify est déjà fait hors verrou).
	st = frappe.db.get_value("Applicant Fee Payment", existing.name,
	                         "payment_status", for_update=True)
	if st in ("Confirmed", "Paid"):
		# Course gagnée par un autre webhook entre verify et verrou → idempotent (1 seule promo).
		log_event("webhook_payment", "replay", dossier_id=getattr(existing, "applicant", None), ref=reference)
		return _ok({"accepted": True, "payment_id": existing.name, "idempotent": True})

	# (3) C2 : verify=SUCCESS mais montant insuffisant → l'argent a bougé (DEC-4, jamais de drop) :
	#     trace 'Underpaid - review' + txid, PAS de promotion.
	if tx_amount < int(existing.amount_xof or 0):
		frappe.db.set_value("Applicant Fee Payment", existing.name,
		                    {"reconciliation": "Underpaid - review",
		                     "provider_transaction_id": transaction_id}, update_modified=False)
		frappe.db.commit()
		log_event("webhook_payment", "underpaid",
		          dossier_id=getattr(existing, "applicant", None), ref=reference, level="warning")
		return _error("AMOUNT_MISMATCH", "Montant verifie inferieur au montant attendu.", 409)

	# (4) verify=SUCCESS + montant OK.
	#     R3 (D-RACE-FEE-01) : l'unicité « ≤ 1 Confirmed par fee » est garantie par l'INDEX DB (colonne
	#     générée confirmed_fee = applicant_fee si Confirmed sinon NULL). Plus de verrou ligne-fee (PC2-ter
	#     RETIRÉ) : il ne sérialisait pas la VISIBILITÉ sous REPEATABLE READ (→ 2 Confirmed, harness 3/3),
	#     et le corriger en lecture courante (for_update) provoquait un deadlock 1213 (harness 5/5).
	#     Le check fee_resolved applicatif (db.exists) RESTE, mais SEULEMENT pour le cas SÉQUENTIEL :
	#     orphelin propre + message sans attendre l'exception DB. Le garant de la CONCURRENCE est l'index.
	fee_resolved = frappe.db.exists(
		"Applicant Fee Payment",
		{"applicant_fee": existing.applicant_fee, "payment_status": "Confirmed",
		 "name": ["!=", existing.name]})
	if fee_resolved:
		return _orphan_trace(existing, transaction_id, reference)  # séquentiel : fee déjà crédité

	# Fee vu non résolu → promotion. CONCURRENCE : si un AUTRE webhook a confirmé le même fee entre notre
	# check et notre save (course), l'index unique DB lève UniqueValidationError au save (AVANT la cascade,
	# DEC-4 : jamais d'effet aval pour le perdant) → on orpheline (refund OPS, zéro double promotion).
	# Garant CONCURRENT prouvé par le harness 2-threads — jamais un mock.
	try:
		if st == "Pending":
			if not _promote_payment(existing, transaction_id, reference):   # D-CONF-01 : dossier terminal
				return _ok({"accepted": True, "payment_id": existing.name,
				            "promoted": False, "refused_terminal": True})
			return _ok({"accepted": True, "payment_id": existing.name, "transition": "BRO/SOP->SOU"})
		if not _promote_payment(existing, transaction_id, reference, reconciliation="Promoted late"):
			return _ok({"accepted": True, "payment_id": existing.name,
			            "promoted": False, "refused_terminal": True})
		return _ok({"accepted": True, "payment_id": existing.name,
		            "transition": "BRO/SOP->SOU", "reconciled": True})
	except frappe.UniqueValidationError:
		frappe.db.rollback()  # annule le save partiel (Confirmed refusé par l'index) avant de tracer
		log_event("webhook_payment", "orphan_on_unique_violation",
		          dossier_id=getattr(existing, "applicant", None), ref=reference, level="warning")
		return _orphan_trace(existing, transaction_id, reference)  # concurrent : course perdue à l'index
