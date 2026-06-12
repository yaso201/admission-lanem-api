"""Tests FIX-CATALOG-KEY (ADM-DEBT-64) — catalog_key 3 segments program-level-fee_type.

Le contrôleur AdmissionFeeCatalog réécrivait la clé en 2 segments (program-fee_type) alors
que les écrivains sync ET le lecteur _resolve_fee_from_catalog utilisent 3 segments → toute
nouvelle entrée insérée via contrôleur était introuvable (frais 2 None, scolarité base 0,
cap bourses retombant sur 0.50 par accident) et le sync re-tentait l'insert → DuplicateEntryError.

Part A : contrôleur — clé 3 segments, défaut level DEFAULT (aligné écrivains sync).
Part B : confrontation de shape producteur (contrôleur) / consommateur (lecteur public).
Part C : patch de renommage — idempotent, collision skippée (0 perte).
Style unitaire mocké, aligné suite existante.
"""

import json
import os
import types
from unittest import TestCase
from unittest.mock import MagicMock, call, patch

PUBLIC = "admission.api.public"
PATCH_MOD = "admission.patches.v1_0.rename_catalog_keys_three_segments"


def _doc(program_code="LIC", level_code="LIC-L1", fee_type="annual"):
    return types.SimpleNamespace(
        program_code=program_code, level_code=level_code, fee_type=fee_type, catalog_key=None
    )


# ── Part A : contrôleur ──────────────────────────────────────────────────────


class TestCatalogKeyController(TestCase):
    def _set_key(self, doc):
        from admission.admission.doctype.admission_fee_catalog.admission_fee_catalog import (
            AdmissionFeeCatalog,
        )
        AdmissionFeeCatalog._set_catalog_key(doc)

    def test_key_three_segments(self):
        doc = _doc("LIC", "LIC-L1", "annual")
        self._set_key(doc)
        self.assertEqual(doc.catalog_key, "LIC-LIC-L1-annual")

    def test_hooks_delegate_to_set_catalog_key(self):
        # before_insert ET before_save posent la clé (un seul point de vérité)
        from admission.admission.doctype.admission_fee_catalog.admission_fee_catalog import (
            AdmissionFeeCatalog,
        )
        for hook in (AdmissionFeeCatalog.before_insert, AdmissionFeeCatalog.before_save):
            doc = MagicMock()
            hook(doc)
            doc._set_catalog_key.assert_called_once()

    def test_empty_level_defaults_to_default(self):
        # Même défaut que les écrivains sync (level_code DEFAULT) et le lecteur
        for empty in ("", None):
            doc = _doc("PRE", empty, "competition")
            self._set_key(doc)
            self.assertEqual(doc.catalog_key, "PRE-DEFAULT-competition")

    def test_cap_key_matches_scholarship_sync(self):
        # _store_scholarship_cap écrit SCHOLARSHIP-DEFAULT-cap : le contrôleur doit produire
        # la MÊME clé (sinon le cap bourses retombe silencieusement sur 0.50 — ARGENT)
        doc = _doc("SCHOLARSHIP", "DEFAULT", "cap")
        self._set_key(doc)
        self.assertEqual(doc.catalog_key, "SCHOLARSHIP-DEFAULT-cap")

    def test_fee_type_select_accepts_cap(self):
        # Dérogation write-set signalée (FIX-CATALOG-KEY) : sans l'option "cap" au Select,
        # _store_scholarship_cap lève ValidationError → le cap UF n'a JAMAIS pu être stocké
        # (le défaut 0.50 masquait la panne). Gate « cap lu = vrai cap UF ».
        jf = os.path.join(os.path.dirname(__file__), "..", "admission", "doctype",
                          "admission_fee_catalog", "admission_fee_catalog.json")
        fields = {f["fieldname"]: f for f in json.load(open(jf))["fields"]}
        options = (fields["fee_type"].get("options") or "").split("\n")
        self.assertIn("cap", options, "fee_type doit accepter 'cap' (plafond bourses, scholarship_sync)")


# ── Part B : shape producteur/consommateur ───────────────────────────────────


class TestCatalogKeyShapeMatch(TestCase):
    def test_controller_key_is_readable_by_resolver(self):
        # Confrontation directe : la clé produite par le contrôleur = la 1re clé lue par
        # _resolve_fee_from_catalog_uncached (même programme/level/fee_type).
        from admission.admission.doctype.admission_fee_catalog.admission_fee_catalog import (
            AdmissionFeeCatalog,
        )
        doc = _doc("LIC", "LIC-L1", "enrollment")
        AdmissionFeeCatalog._set_catalog_key(doc)

        with patch(f"{PUBLIC}.frappe") as mf:
            mf.db.get_value.return_value = 50000
            from admission.api.public import _resolve_fee_from_catalog_uncached
            amount = _resolve_fee_from_catalog_uncached("LIC", "enrollment", "LIC-L1")

        self.assertEqual(amount, 50000.0)
        first_key_read = mf.db.get_value.call_args_list[0][0][1]
        self.assertEqual(first_key_read, doc.catalog_key)  # producteur == consommateur

    def test_controller_default_matches_resolver_fallback(self):
        # Sans level : le contrôleur produit PROG-DEFAULT-ft, exactement le fallback lecteur.
        from admission.admission.doctype.admission_fee_catalog.admission_fee_catalog import (
            AdmissionFeeCatalog,
        )
        doc = _doc("LIC", None, "application")
        AdmissionFeeCatalog._set_catalog_key(doc)

        with patch(f"{PUBLIC}.frappe") as mf:
            mf.db.get_value.side_effect = [None, 25000]  # miss L9 → hit fallback DEFAULT
            from admission.api.public import _resolve_fee_from_catalog_uncached
            amount = _resolve_fee_from_catalog_uncached("LIC", "application", "L9")

        self.assertEqual(amount, 25000.0)
        fallback_key_read = mf.db.get_value.call_args_list[1][0][1]
        self.assertEqual(fallback_key_read, doc.catalog_key)


# ── Part C : patch de renommage (idempotent, 0 perte) ────────────────────────


class TestRenamePatch(TestCase):
    def _run(self, rows, exists=False):
        with patch(f"{PATCH_MOD}.frappe") as mf:
            mf.get_all.return_value = [types.SimpleNamespace(**r) for r in rows]
            mf.db.exists.return_value = exists
            from admission.patches.v1_0.rename_catalog_keys_three_segments import execute
            execute()
            return mf

    def test_two_segment_row_renamed(self):
        mf = self._run([{"name": "LIC-enrollment", "program_code": "LIC",
                         "level_code": "LIC-L1", "fee_type": "enrollment"}])
        mf.rename_doc.assert_called_once_with(
            "Admission Fee Catalog", "LIC-enrollment", "LIC-LIC-L1-enrollment", force=True
        )
        # le champ autoname suit le nouveau name
        mf.db.set_value.assert_called_once_with(
            "Admission Fee Catalog", "LIC-LIC-L1-enrollment",
            "catalog_key", "LIC-LIC-L1-enrollment", update_modified=False,
        )

    def test_missing_level_renamed_to_default(self):
        mf = self._run([{"name": "SCHOLARSHIP-cap", "program_code": "SCHOLARSHIP",
                         "level_code": "", "fee_type": "cap"}])
        mf.rename_doc.assert_called_once_with(
            "Admission Fee Catalog", "SCHOLARSHIP-cap", "SCHOLARSHIP-DEFAULT-cap", force=True
        )

    def test_correct_row_untouched_idempotent(self):
        mf = self._run([{"name": "LIC-DEFAULT-annual", "program_code": "LIC",
                         "level_code": "DEFAULT", "fee_type": "annual"}])
        mf.rename_doc.assert_not_called()
        mf.db.set_value.assert_not_called()

    def test_collision_skipped_no_data_loss(self):
        # Cible déjà présente (maintenue par le sync) → skip + log, JAMAIS de delete/écrasement
        mf = self._run([{"name": "LIC-annual", "program_code": "LIC",
                         "level_code": "DEFAULT", "fee_type": "annual"}], exists=True)
        mf.rename_doc.assert_not_called()
        mf.delete_doc.assert_not_called()
