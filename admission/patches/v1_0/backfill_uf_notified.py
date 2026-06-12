"""OBS-1-BACKFILL — marque uf_notified=1 sur les paiements Confirmed déjà répercutés à UF.

But : éviter qu'au 1er re-drive (admission.api.notify_uf.redrive_uf_notifications) tout
l'historique des paiements Confirmed (uf_notified=0 par défaut) soit re-POSTé à UF.

⚠️ CONDITIONNEL (recon OBS-1-BACKFILL) : on ne marque QUE si le flux UF tournait réellement,
c.-à-d. si `uf_backoffice_url` est configuré. Si la config est absente (le flux skippait →
AUCUN paiement n'a été notifié), on ne marque RIEN : sinon on marquerait des paiements non
notifiés → désync permanente (le re-drive les sauterait). En config absente, le re-drive les
traitera correctement une fois UF branché.

Idempotent : ne touche que `uf_notified=0` → rejouable sans effet.
Ref : OBS-1, recon OBS-1-BACKFILL (uf_url absent en dev → hypothèse « flux tournait » invalidée).
"""

import frappe
from frappe.utils import now_datetime

from admission.api.notify_uf import _get_uf_config


def execute():
	if not _get_uf_config():
		frappe.logger("retention").info(
			"OBS-1 backfill skipped: uf_backoffice_url not configured "
			"(flux UF inactif → aucun paiement notifié → le re-drive les traitera quand UF sera branché)."
		)
		return

	names = frappe.get_all(
		"Applicant Fee Payment",
		filters={"payment_status": "Confirmed", "uf_notified": 0},
		pluck="name",
	)
	for name in names:
		paid_at = frappe.db.get_value("Applicant Fee Payment", name, "paid_at")
		frappe.db.set_value(
			"Applicant Fee Payment", name,
			{"uf_notified": 1, "uf_notified_at": paid_at or now_datetime()},
			update_modified=False,
		)
	frappe.db.commit()
	frappe.logger("retention").info(
		f"OBS-1 backfill: {len(names)} paiement(s) Confirmed marqué(s) uf_notified=1 (flux UF actif)."
	)
