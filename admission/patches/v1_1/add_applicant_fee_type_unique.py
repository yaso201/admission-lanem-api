import frappe


def execute():
	"""FIX-D-CONF-02 — invariant « au plus un Applicant Fee par (applicant, fee_type) » garanti EN BASE
	(même famille que R3 add_confirmed_fee_unique). Index UNIQUE (applicant, fee_type) : le
	check-then-insert de `_ensure_fee`/`_ensure_enrollment_fee` ne peut plus créer un doublon sous
	concurrence — la 2ᵉ création du même couple lève 1062 → `frappe.UniqueValidationError` → handler
	applicatif (rollback + retombe sur le fee du gagnant).

	Index à PLAT (applicant + fee_type toujours renseignés → pas de NULL, pas de colonne générée
	nécessaire, contrairement à R3 qui filtrait sur Confirmed).

	Idempotent. SUPPOSE 0 doublon (applicant, fee_type) — vérifié en reconnaissance (recette : 0). Si un
	doublon résiduel existait, l'ADD UNIQUE échouerait : garde-fou VOULU (nettoyer la data AVANT)."""
	table = "tabApplicant Fee"
	if not frappe.db.sql(f"SHOW INDEX FROM `{table}` WHERE Key_name='unique_applicant_fee_type'"):
		frappe.db.sql(
			f"ALTER TABLE `{table}` ADD UNIQUE INDEX `unique_applicant_fee_type` (`applicant`, `fee_type`)"
		)
