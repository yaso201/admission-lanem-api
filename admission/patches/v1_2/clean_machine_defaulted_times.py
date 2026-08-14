"""CAL-FIX-0 (CAL-09) : purge les heures d'épreuve machine-défautées.

Frappe pose nowtime() — précision µs — sur tout champ Time absent d'un insert
(frappe/model/create_new.py, set_dynamic_default_values) : V-LEARN-CAL-01. Signature
infaillible : µs > 0 (une saisie humaine UI/API vaut toujours .000000). Sur une session à
épreuve, la paire quasi-égale ainsi posée fait rejeter par A07 toute proposition légitime
(preuve E4, RAPPORT-CAL-AUDIT §7.4) et imprimerait une heure d'appel absurde sur la
convocation. Idempotent ; no-op attendu en PROD (inventaire 14/08/2026 : 0 ligne).
"""

import frappe

FIELDS = ("exam_call_time", "exam_start_time")


def execute():
	if not frappe.db.has_column("Admission Session", "exam_call_time"):
		return
	touched = {}
	for field in FIELDS:
		names = [r[0] for r in frappe.db.sql(
			f"SELECT name FROM `tabAdmission Session` WHERE MICROSECOND(`{field}`) > 0")]
		if names:
			frappe.db.sql(
				f"UPDATE `tabAdmission Session` SET `{field}` = NULL WHERE name IN %(names)s",
				{"names": names})
		touched[field] = names
	frappe.db.commit()
	sessions = sorted(set(touched["exam_call_time"]) | set(touched["exam_start_time"]))
	frappe.logger("patches").info(
		f"clean_machine_defaulted_times : call={len(touched['exam_call_time'])} "
		f"start={len(touched['exam_start_time'])} — sessions : {sessions or 'aucune'}")
