"""Tests FIX-SCHEDULER-ORPHELINS (ADM-DEBT-62) — câblage de expire_stale_online_pending.

La fonction (PAY-CONFIRM-AGENT phase e) passe les Pending Online périmés (>48h) en Rejected.
Ce lot la CÂBLE dans scheduler_events["daily"] (chemin dotté direct, appel sans arg → défaut 48h).
On ne teste pas la logique (déjà couverte par test_pay_online_core) mais le CÂBLAGE : entrée
enregistrée + le chemin résout bien la fonction validée.
"""

from unittest import TestCase

import frappe

SCHEDULER_PATH = "admission.api.public.expire_stale_online_pending"


class TestSchedulerOrphans(TestCase):
    def test_expire_stale_registered_in_daily(self):
        import admission.hooks as h
        self.assertIn(SCHEDULER_PATH, h.scheduler_events.get("daily", []))

    def test_path_resolves_to_validated_function(self):
        from admission.api.public import expire_stale_online_pending
        fn = frappe.get_attr(SCHEDULER_PATH)
        self.assertIs(fn, expire_stale_online_pending)  # même fonction validée (non modifiée)
        self.assertTrue(callable(fn))
