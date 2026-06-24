import frappe


def execute():
	"""R3 (D-RACE-FEE-01) — invariant « au plus un Applicant Fee Payment Confirmed par applicant_fee »
	garanti EN BASE, gap-free.

	Colonne GÉNÉRÉE (DB-calculée à CHAQUE write — save ET set_value, impossible à contourner par un
	chemin oublié) : `confirmed_fee = applicant_fee si Confirmed, sinon NULL`. Index UNIQUE : MariaDB
	autorise plusieurs NULL → unicité conditionnelle sur les SEULS Confirmed. La 2ᵉ promotion concurrente
	du même fee lève alors 1062 → `frappe.UniqueValidationError` → branche orphelin (handler).

	Hors doctype JSON (patch-owned) : le migrate ne drope pas une colonne absente du meta (vérifié) ;
	la déclarer en JSON ferait gérer Frappe une colonne plate, en conflit avec l'expression générée.

	Idempotent. SUPPOSE 0 doublon Confirmed/fee (nettoyé AVANT ce patch — sinon l'index échoue, garde-fou
	voulu)."""
	table = "tabApplicant Fee Payment"

	# 1. colonne générée PERSISTENT (= STORED, indexable sous MariaDB 10.11)
	if not frappe.db.sql(f"SHOW COLUMNS FROM `{table}` LIKE 'confirmed_fee'"):
		frappe.db.sql(
			f"ALTER TABLE `{table}` ADD COLUMN `confirmed_fee` VARCHAR(140) "
			f"AS (IF(`payment_status`='Confirmed', `applicant_fee`, NULL)) PERSISTENT"
		)

	# 2. index UNIQUE (échoue si doublon résiduel → c'est la garde : nettoyer la data AVANT)
	if not frappe.db.sql(f"SHOW INDEX FROM `{table}` WHERE Key_name='unique_confirmed_fee'"):
		frappe.db.sql(
			f"ALTER TABLE `{table}` ADD UNIQUE INDEX `unique_confirmed_fee` (`confirmed_fee`)"
		)
