"""Tests PERF-1 — cache couche catalogue + invalidation + payload allégé.

- _cache_get_or_set : hit (sert le cache, pas de compute) / miss (compute + set TTL) / cache None.
- _invalidate_catalog_cache : vide le namespace admission ; handler doc_event délègue.
- _resolve_fee / _get_promotions : clé namespacée ; promos = date dans la clé (anti-périmé).
- _get_active_legal_texts_meta : version+hash SANS content_text (allègement 3G).

Ref: AUDIT-PERF1-CACHE, décisions (invalidation événementielle+TTL ; payload allégé).
"""

from __future__ import annotations

from unittest import TestCase
from unittest.mock import MagicMock, patch

import frappe as _real_frappe


def setUpModule():
    try:
        _real_frappe.local.flags
    except Exception:
        _real_frappe.local.flags = _real_frappe._dict(in_test=True)


PUB = "admission.api.public"
LEGAL = "admission.api.legal"


class TestCachePrimitive(TestCase):
    @patch(f"{PUB}.frappe")
    def test_hit_returns_cached_no_compute(self, mock_frappe):
        cache = MagicMock(); cache.get_value.return_value = {"v": "CACHED"}
        mock_frappe.cache.return_value = cache
        from admission.api.public import _cache_get_or_set
        compute = MagicMock(return_value="FRESH")
        self.assertEqual(_cache_get_or_set("k", 3600, compute), "CACHED")
        compute.assert_not_called()

    @patch(f"{PUB}.frappe")
    def test_miss_computes_and_sets_ttl(self, mock_frappe):
        cache = MagicMock(); cache.get_value.return_value = None
        mock_frappe.cache.return_value = cache
        from admission.api.public import _cache_get_or_set
        compute = MagicMock(return_value="FRESH")
        self.assertEqual(_cache_get_or_set("k", 3600, compute), "FRESH")
        compute.assert_called_once()
        self.assertEqual(cache.set_value.call_args.kwargs.get("expires_in_sec"), 3600)

    @patch(f"{PUB}.frappe")
    def test_caches_none_result(self, mock_frappe):
        cache = MagicMock(); cache.get_value.return_value = {"v": None}
        mock_frappe.cache.return_value = cache
        from admission.api.public import _cache_get_or_set
        compute = MagicMock(return_value="SHOULD-NOT-RUN")
        self.assertIsNone(_cache_get_or_set("k", 3600, compute))
        compute.assert_not_called()


class TestInvalidation(TestCase):
    @patch(f"{PUB}.frappe")
    def test_invalidate_clears_admission_namespace(self, mock_frappe):
        cache = MagicMock(); mock_frappe.cache.return_value = cache
        from admission.api.public import _invalidate_catalog_cache
        _invalidate_catalog_cache()
        cache.delete_keys.assert_called()
        args = [str(c[0][0]) for c in cache.delete_keys.call_args_list]
        self.assertTrue(any("admission" in a for a in args))

    @patch(f"{PUB}._invalidate_catalog_cache")
    def test_doc_event_handler_delegates(self, mock_inv):
        from admission.api.public import invalidate_catalog_cache
        invalidate_catalog_cache(doc=MagicMock(), method="on_update")
        mock_inv.assert_called_once()


class TestCachedHelpers(TestCase):
    @patch(f"{PUB}.frappe")
    def test_resolve_fee_uses_namespaced_key(self, mock_frappe):
        cache = MagicMock(); cache.get_value.return_value = None
        mock_frappe.cache.return_value = cache
        mock_frappe.db.get_value.return_value = 25000
        from admission.api.public import _resolve_fee_from_catalog
        result = _resolve_fee_from_catalog("LIS", "application", "L1")
        self.assertEqual(result, 25000.0)
        self.assertIn("admission:fee:LIS-L1-application", cache.set_value.call_args[0][0])

    @patch(f"{PUB}.getdate")
    @patch(f"{PUB}.frappe")
    def test_promos_cache_key_includes_date(self, mock_frappe, mock_getdate):
        from datetime import date as _date
        cache = MagicMock(); cache.get_value.return_value = None
        mock_frappe.cache.return_value = cache
        mock_frappe.get_all.return_value = []
        mock_getdate.return_value = _date(2026, 6, 9)
        from admission.api.public import _get_promotions_for_programme
        _get_promotions_for_programme("LIS")
        # date dans la clé → une promo expirée à minuit n'est jamais servie le lendemain
        self.assertIn("2026-06-09", cache.set_value.call_args[0][0])


class TestPayloadAllege(TestCase):
    @patch(f"{LEGAL}.frappe")
    def test_legal_meta_excludes_content_text(self, mock_frappe):
        cache = MagicMock(); cache.get_value.return_value = None
        mock_frappe.cache.return_value = cache
        mock_frappe.get_all.return_value = [
            _real_frappe._dict(document_type="CGV", version="V1", content_hash="abc")
        ]
        from admission.api.legal import _get_active_legal_texts_meta
        result = _get_active_legal_texts_meta()
        self.assertIn("cgv", result)
        self.assertNotIn("content_text", result["cgv"])
        self.assertEqual(result["cgv"]["content_hash"], "abc")
