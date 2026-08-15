"""NOTES-FIX-2 (DEC-J) — journal APPEND-ONLY des modifications de notes de concours.

Écrit par le serveur (staff._log_note_changes) dans la MÊME transaction que la note.
Aucune écriture staff par l'API REST (perms lecture seule) ; pas de purge V1 (rétention
au registre). Équivalent du « Grade history » Moodle, consulté par l'écran notes (C8).
"""

from frappe.model.document import Document


class AdmissionNoteChangeLog(Document):
	pass
