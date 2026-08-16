"""Client FedaPay (PAIEMENT-FEDAPAY — DEC-216 révisée) — re-vérification serveur des transactions.

Remplace le client KkiaPay (abandonné). Modèle de sécurité identique : le webhook n'est qu'un
DÉCLENCHEUR ; la source de vérité est l'API de statut, authentifiée par la clé SECRÈTE marchand :

    GET {base}/v1/transactions/{id}   Header: Authorization: Bearer <secret_key>
    → {"v1/transaction": {"status": "approved"|"pending"|"declined"|"canceled", "amount": ..., ...}}
      (FedaPay enveloppe la ressource ; on tolère aussi la forme à plat)

Signature webhook FedaPay (en-tête `x-fedapay-signature`, forme Stripe-like `t=<ts>,s=<hash>`) :
    hash = HMAC-SHA256(webhook_secret, "<ts>.<raw_body>")   — comparaison constant-time.

site_config (VALEURS jamais dans le code/les logs/les tests) :
  `fedapay_public_key` (front) · `fedapay_secret_key` (API serveur) · `fedapay_webhook_secret`
  (signature) · `fedapay_sandbox` (1 = https://sandbox-api.fedapay.com) · `fedapay_mock` (1 = DEV
  uniquement, vérification simulée — la gate recette le REFUSE hors DEV).
Fail-closed : clé absente, réseau en échec ou réponse non-2xx → None (NON vérifié).
"""

import hashlib
import hmac

import frappe

from admission.api._log import log_event

BASE_URL = "https://api.fedapay.com"
SANDBOX_URL = "https://sandbox-api.fedapay.com"

MOCK_PREFIX = "MOCK-"  # transactionId de simulation DEV : MOCK-<provider_reference>

# Statuts FedaPay considérés comme un encaissement RÉUSSI (collection). Les autres
# (pending/declined/canceled/refunded) ne promeuvent jamais.
SUCCESS_STATUSES = {"approved", "transferred"}


def is_mock():
	return bool(frappe.conf.get("fedapay_mock"))


def is_sandbox():
	return bool(frappe.conf.get("fedapay_sandbox"))


def mode():
	"""mock (DEV) > sandbox > live — consommé par le descriptor et le front."""
	if is_mock():
		return "mock"
	return "sandbox" if is_sandbox() else "live"


def public_key():
	return frappe.conf.get("fedapay_public_key")


def _base():
	return SANDBOX_URL if is_sandbox() else BASE_URL


def _tx_from_response(body):
	"""FedaPay enveloppe la ressource sous `v1/transaction` ; on tolère aussi la forme à plat."""
	if isinstance(body, dict):
		inner = body.get("v1/transaction") or body.get("transaction")
		return inner if isinstance(inner, dict) else body
	return None


def _normalize(tx):
	"""Contrat interne stable (comme kkiapay) : {status: SUCCESS|..., amount, transactionId}.
	`status` est remonté en MAJUSCULES ; SUCCESS ssi le statut FedaPay est un encaissement."""
	if not isinstance(tx, dict):
		return None
	raw_status = str(tx.get("status") or "").lower()
	status = "SUCCESS" if raw_status in SUCCESS_STATUSES else raw_status.upper()
	return {
		"status": status,
		"raw_status": raw_status,
		"amount": tx.get("amount"),
		"transactionId": tx.get("id") or tx.get("transactionId"),
		"reference": tx.get("reference"),
	}


def verify_transaction(transaction_id):
	"""Statut RÉEL de la transaction chez FedaPay. Renvoie le dict normalisé, ou None si la
	vérification est impossible (fail-closed : None = paiement NON confirmé)."""
	if not transaction_id:
		return None
	if is_mock():
		return _mock_verify(transaction_id)
	secret = frappe.conf.get("fedapay_secret_key")
	if not secret:
		frappe.logger("webhook").error(
			"fedapay: clé secrète absente de site_config — vérification impossible (fail-closed)")
		return None
	import requests
	try:
		r = requests.get(
			_base() + f"/v1/transactions/{transaction_id}",
			headers={"Accept": "application/json", "Authorization": f"Bearer {secret}"},
			timeout=10,
		)
		if not r.ok:
			frappe.logger("webhook").warning(
				f"fedapay verify {transaction_id}: HTTP {r.status_code} {r.text[:200]}")
			# OBS-2 HIGH : vérification impossible sur un paiement RÉEL → fail-closed, 0 promotion.
			log_event("fedapay_verify", "failed", ref=str(transaction_id),
			          http_status=r.status_code, level="error", alert_type="fedapay_verify")
			return None
		return _normalize(_tx_from_response(r.json()))
	except Exception:
		frappe.logger("webhook").warning(
			f"fedapay verify {transaction_id} failed: {frappe.get_traceback()}")
		# OBS-2 HIGH : provider injoignable → cooldown produit 1 message, pas N (rafale de retentatives).
		log_event("fedapay_verify", "failed", ref=str(transaction_id), level="error",
		          alert_type="fedapay_verify")
		return None


def valid_webhook_signature(raw_body, header):
	"""Vérifie la signature FedaPay `x-fedapay-signature` = `t=<ts>,s=<hash>` où
	hash = HMAC-SHA256(webhook_secret, "<ts>.<raw_body>"). Comparaison constant-time.
	Fail-CLOSED : secret absent OU en-tête absent/mal formé OU hash != → False (jamais accepté)."""
	secret = frappe.conf.get("fedapay_webhook_secret")
	if not secret or not header:
		return False
	parts = {}
	for seg in str(header).split(","):
		if "=" in seg:
			k, v = seg.split("=", 1)
			parts[k.strip()] = v.strip()
	ts, sig = parts.get("t"), parts.get("s")
	if not ts or not sig:
		return False
	body = raw_body if isinstance(raw_body, (bytes, bytearray)) else str(raw_body or "").encode("utf-8")
	signed = ts.encode("utf-8") + b"." + body
	expected = hmac.new(str(secret).encode("utf-8"), signed, hashlib.sha256).hexdigest()
	return hmac.compare_digest(expected, str(sig))


def _mock_verify(transaction_id):
	"""DEV : `MOCK-<reference>` est « vérifié » SUCCESS au montant du Pending lié — permet l'E2E
	webhook sans provider. Tout autre id reste non vérifié (fail-closed préservé même en mock)."""
	if not str(transaction_id).startswith(MOCK_PREFIX):
		return None
	reference = str(transaction_id)[len(MOCK_PREFIX):]
	amount = frappe.db.get_value(
		"Applicant Fee Payment", {"provider_reference": reference}, "amount_xof")
	if amount is None:
		return None
	return {"status": "SUCCESS", "raw_status": "approved", "amount": amount,
	        "transactionId": transaction_id, "reference": reference, "source": "mock"}
