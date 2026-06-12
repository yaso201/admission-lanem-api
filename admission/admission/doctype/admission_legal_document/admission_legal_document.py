import hashlib

import frappe
from frappe.model.document import Document


class AdmissionLegalDocument(Document):

    def validate(self):
        self.content_hash = hashlib.sha256(
            (self.content_text or "").encode("utf-8")
        ).hexdigest()

        if self.is_active:
            existing = frappe.get_all(
                "Admission Legal Document",
                filters={
                    "document_type": self.document_type,
                    "is_active": 1,
                    "name": ["!=", self.name],
                },
                pluck="name",
                limit=1,
            )
            if existing:
                frappe.throw(
                    f"Un document actif de type {self.document_type} existe deja: "
                    f"{existing[0]}. Desactivez-le avant d'en activer un nouveau."
                )
