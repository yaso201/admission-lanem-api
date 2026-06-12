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
)
from admission.api.receipt import send_payment_receipt
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

	if not success:
		# transaction.failed → le Pending lié est rejeté (silencieux : pas de hook UF,
		# même pattern que expire_stale_online_pending). Sinon idempotent.
		rejected = None
		if existing.payment_status == "Pending":
			frappe.db.set_value("Applicant Fee Payment", existing.name,
			                    "payment_status", "Rejected", update_modified=False)
			frappe.db.commit()
			rejected = existing.name
		log_event("webhook_payment", "failed_event",
		          dossier_id=getattr(existing, "applicant", None), ref=reference)
		return _ok({"accepted": True, "transition": None, "rejected": rejected})

	if existing.payment_status in ("Confirmed", "Paid"):
		# Vrai replay (retentatives KkiaPay 5×500 ms incluses) : idempotent, rien à recréer.
		log_event("webhook_payment", "replay", dossier_id=getattr(existing, "applicant", None), ref=reference)
		return _ok({"accepted": True, "payment_id": existing.name, "idempotent": True})

	if existing.payment_status != "Pending":
		# Encaissement provider sur un Pending déjà rejeté (désistement/clôture W) :
		# l'argent a bougé chez KkiaPay → traitement manuel OPS (refund), pas de promotion.
		frappe.log_error(
			title="Webhook paiement sur Pending non promouvable",
			message=f"reference={reference} payment={existing.name} "
			        f"status={existing.payment_status} transactionId={transaction_id}",
		)
		log_event("webhook_payment", "confirmed_on_non_pending",
		          dossier_id=getattr(existing, "applicant", None), ref=reference, level="warning")
		return _ok({"accepted": True, "transition": None, "promoted": False})

	# ── Source de vérité : re-vérification serveur chez KkiaPay (jamais le payload seul)
	tx = verify_transaction(transaction_id)
	if not tx or str(tx.get("status") or "").upper() != "SUCCESS":
		log_event("webhook_payment", "rejected_not_verified",
		          dossier_id=getattr(existing, "applicant", None), ref=reference, level="warning")
		return _error("PAYMENT_NOT_VERIFIED",
		              "Transaction non verifiee aupres du provider.", 409)
	try:
		tx_amount = int(float(tx.get("amount") or 0))
	except (ValueError, TypeError):
		tx_amount = 0
	if tx_amount < int(existing.amount_xof or 0):
		frappe.log_error(
			title="Webhook paiement : montant provider insuffisant",
			message=f"reference={reference} attendu>={existing.amount_xof} recu={tx_amount} "
			        f"transactionId={transaction_id}",
		)
		log_event("webhook_payment", "rejected_amount_mismatch",
		          dossier_id=getattr(existing, "applicant", None), ref=reference, level="warning")
		return _error("AMOUNT_MISMATCH", "Montant verifie inferieur au montant attendu.", 409)

	# A2 corrigé : PROMOTION du Pending lié (pas d'insert). Le bon dossier vient du paiement pré-créé.
	applicant = frappe.get_doc("Admission Applicant", existing.applicant)
	fee = frappe.get_doc("Applicant Fee", existing.applicant_fee)
	existing.payment_status = "Confirmed"
	existing.paid_at = now_datetime()
	existing.provider = "kkiapay"
	existing.provider_transaction_id = transaction_id  # opposabilité + revert API
	existing.save(ignore_permissions=True)  # Pending→Confirmed → hook on_payment_update notifie UF
	payment_doc = existing

	# DEC-228/R1 : la capture promo est DANS la cascade (frais 1 only) — plus d'appel direct ici,
	# sinon une promo serait figée à la confirmation d'un frais 2 (ANOMALIE-1 C2-BOURSES).
	apply_confirmed_payment_cascade(applicant, fee)  # capture promo frais 1 + fee Paid (#3) + BRO/SOP→SOU (helper partagé confirm/webhook)
	frappe.db.commit()
	send_payment_receipt(payment_doc, applicant=applicant, fee=fee)  # reçu online (non-bloquant)
	log_event("webhook_payment", "success", dossier_id=applicant.name, ref=reference)
	return _ok({"accepted": True, "payment_id": payment_doc.name, "transition": "BRO/SOP->SOU"})
