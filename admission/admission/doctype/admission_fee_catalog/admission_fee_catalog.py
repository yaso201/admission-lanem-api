"""Admission Fee Catalog — local mirror of UF admission tariffs.

Replicated from UF via pull (fee_catalog_sync, daily scheduler).
Zero Link to external Doctypes (admission autonome).

Ref: ADM-UF-3, pattern Phase A (referential_sync).
"""

from __future__ import annotations

import frappe
from frappe.model.document import Document


class AdmissionFeeCatalog(Document):

    def before_insert(self):
        self._set_catalog_key()

    def before_save(self):
        self._set_catalog_key()

    def _set_catalog_key(self):
        # ADM-DEBT-64 : clé 3 segments program-level-fee_type, IDENTIQUE aux écrivains sync
        # (fee_catalog_sync._upsert_entry, scholarship_sync._upsert_annual_amounts/_store_scholarship_cap)
        # et au lecteur _resolve_fee_from_catalog — défaut level = DEFAULT. L'ancien format
        # 2 segments rendait toute nouvelle entrée introuvable (frais/scolarité/cap).
        self.catalog_key = f"{self.program_code}-{self.level_code or 'DEFAULT'}-{self.fee_type}"
