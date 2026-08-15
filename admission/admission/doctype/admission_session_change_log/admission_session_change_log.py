"""CAL-AMEL (DEC-P) — journal APPEND-ONLY des changements de session (patron DEC-J notes).

Écrit par le serveur dans la MÊME transaction que l'acte (calendar.py / sessions.py).
Le maker-checker cesse d'être amnésique post-purge du pending. Lecture READ_UP,
écriture serveur seule, pas de purge V1 (rétention au registre)."""

from frappe.model.document import Document


class AdmissionSessionChangeLog(Document):
	pass
