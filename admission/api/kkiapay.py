"""Client KkiaPay (LOT KKIAPAY) — re-vérification serveur des transactions.

Modèle de sécurité du provider (extrait de son plugin WooCommerce officiel et de son
SDK Python — la doc ne publie pas le REST brut) : le webhook n'est qu'un DÉCLENCHEUR ;
la source de vérité est l'API de statut, authentifiée par les 3 clés marchand :

    POST {base}/api/v1/transactions/status   body: transactionId=<id>
    Headers : X-API-KEY (publique) · X-PRIVATE-KEY · X-SECRET-KEY
    → {"status": "SUCCESS"|"FAILED"|"PENDING", "amount": ..., ...}

site_config : `kkiapay_public_key` / `kkiapay_private_key` / `kkiapay_secret_key`,
`kkiapay_sandbox` (1 = https://api-sandbox.kkiapay.me), `kkiapay_mock` (1 = DEV
uniquement, vérification simulée — la gate recette le REFUSE hors DEV).
Fail-closed : clés absentes, réseau en échec ou réponse non-2xx → None (NON vérifié).
"""

import frappe

BASE_URL = "https://api.kkiapay.me"
SANDBOX_URL = "https://api-sandbox.kkiapay.me"

MOCK_PREFIX = "MOCK-"  # transactionId de simulation DEV : MOCK-<provider_reference>


def is_mock():
	return bool(frappe.conf.get("kkiapay_mock"))


def is_sandbox():
	return bool(frappe.conf.get("kkiapay_sandbox"))


def mode():
	"""mock (DEV) > sandbox > live — consommé par le descriptor et le front."""
	if is_mock():
		return "mock"
	return "sandbox" if is_sandbox() else "live"


def public_key():
	return frappe.conf.get("kkiapay_public_key")


def verify_transaction(transaction_id):
	"""Statut RÉEL de la transaction chez KkiaPay. Renvoie le dict provider, ou None
	si la vérification est impossible (fail-closed : None = paiement NON confirmé)."""
	if not transaction_id:
		return None
	if is_mock():
		return _mock_verify(transaction_id)
	keys = (frappe.conf.get("kkiapay_public_key"),
	        frappe.conf.get("kkiapay_private_key"),
	        frappe.conf.get("kkiapay_secret_key"))
	if not all(keys):
		frappe.logger("webhook").error(
			"kkiapay: clés marchand absentes de site_config — vérification impossible (fail-closed)")
		return None
	import requests
	base = SANDBOX_URL if is_sandbox() else BASE_URL
	try:
		# Contrat du SDK Python officiel : POST form-encodé, en-têtes X-*-KEY.
		r = requests.post(
			base + "/api/v1/transactions/status",
			data={"transactionId": transaction_id},
			headers={"Accept": "application/json", "X-API-KEY": keys[0],
			         "X-PRIVATE-KEY": keys[1], "X-SECRET-KEY": keys[2]},
			timeout=10,
		)
		if not r.ok:
			frappe.logger("webhook").warning(
				f"kkiapay verify {transaction_id}: HTTP {r.status_code} {r.text[:200]}")
			return None
		return r.json()
	except Exception:
		frappe.logger("webhook").warning(
			f"kkiapay verify {transaction_id} failed: {frappe.get_traceback()}")
		return None


def _mock_verify(transaction_id):
	"""DEV : `MOCK-<reference>` est « vérifié » SUCCESS au montant du Pending lié —
	permet l'E2E webhook sans provider. Tout autre id reste non vérifié (fail-closed
	préservé même en mock)."""
	if not str(transaction_id).startswith(MOCK_PREFIX):
		return None
	reference = str(transaction_id)[len(MOCK_PREFIX):]
	amount = frappe.db.get_value(
		"Applicant Fee Payment", {"provider_reference": reference}, "amount_xof")
	if amount is None:
		return None
	return {"status": "SUCCESS", "amount": amount, "transactionId": transaction_id,
	        "source": "mock"}
