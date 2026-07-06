"""FIX-D-CONF-02 — gestion gracieuse du perdant de la course à la création du frais.

La contrainte unique `(applicant, fee_type)` (patch v1_2) garantit l'invariant EN BASE ; le perdant
d'une course concurrente voit son insert lever `UniqueValidationError`. `_ensure_fee` et
`_ensure_enrollment_fee` doivent alors `rollback()` (REPEATABLE READ : voir le commit du gagnant) puis
retomber sur le fee existant (idempotence : « garantir qu'il existe » réussit toujours). Miroir R3.

Style mocké : on force l'insert à lever, on vérifie rollback + re-lecture + retour du fee gagnant.
La preuve concurrence THREADS RÉELS (≥5 runs) est jouée en Phase 4 (audit_bloc4.d_conf_02, real-DB).
"""

from unittest import TestCase
from unittest.mock import MagicMock, patch

import frappe

PUB = "admission.api.public"


def _applicant():
    a = MagicMock()
    a.name = "CAN-2026-00042"
    a.session = "SES-X"
    a.person_id = "PERS-1"
    a.programme_code = "LIS"
    a.level_code = "LIS-L1"
    return a


class TestEnrollmentFeeLoserGraceful(TestCase):
    def test_unique_violation_returns_existing_fee(self):
        winner = MagicMock(name="winner_fee")
        loser_insert = MagicMock()
        loser_insert.insert.side_effect = frappe.exceptions.UniqueValidationError("dup")
        with patch(f"{PUB}._resolve_fee_from_catalog", return_value=50000), \
             patch(f"{PUB}.frappe") as mf:
            mf.exceptions.UniqueValidationError = frappe.exceptions.UniqueValidationError
            mf.UniqueValidationError = frappe.exceptions.UniqueValidationError
            # 1er get_all (check) → aucun ; 2e (re-query après rollback) → le fee du gagnant
            mf.get_all.side_effect = [[], ["AFF-WINNER"]]
            # get_doc : (1) le nouveau fee à insérer (lève) ; (2) le fee existant relu
            mf.get_doc.side_effect = [loser_insert, winner]
            from admission.api.public import _ensure_enrollment_fee
            result = _ensure_enrollment_fee(_applicant())
        self.assertIs(result, winner)                 # retombe sur le fee du gagnant
        mf.db.rollback.assert_called_once()           # a bien fini la transaction pour voir le commit
        self.assertEqual(mf.get_all.call_count, 2)    # check puis re-lecture

    def test_nominal_no_violation_creates_fee(self):
        # non-régression : sans course, on crée normalement (0 rollback)
        new_fee = MagicMock(name="new_fee")
        with patch(f"{PUB}._resolve_fee_from_catalog", return_value=50000), \
             patch(f"{PUB}.frappe") as mf:
            mf.exceptions.UniqueValidationError = frappe.exceptions.UniqueValidationError
            mf.UniqueValidationError = frappe.exceptions.UniqueValidationError
            mf.get_all.return_value = []              # aucun existant
            mf.get_doc.return_value = new_fee
            from admission.api.public import _ensure_enrollment_fee
            result = _ensure_enrollment_fee(_applicant())
        self.assertIs(result, new_fee)
        new_fee.insert.assert_called_once()
        mf.db.rollback.assert_not_called()

    def test_existing_fee_short_circuits(self):
        # idempotence : un fee existe déjà → retour direct, pas d'insert
        existing = MagicMock(name="existing")
        with patch(f"{PUB}.frappe") as mf:
            mf.get_all.return_value = ["AFF-EXIST"]
            mf.get_doc.return_value = existing
            from admission.api.public import _ensure_enrollment_fee
            result = _ensure_enrollment_fee(_applicant())
        self.assertIs(result, existing)
        mf.db.rollback.assert_not_called()


class TestFrais1FeeLoserGraceful(TestCase):
    def test_unique_violation_returns_existing_fee(self):
        winner = MagicMock(name="winner_fee1")
        loser_insert = MagicMock()
        loser_insert.insert.side_effect = frappe.exceptions.UniqueValidationError("dup")
        session = MagicMock(programme_code="LIS", application_fee_xof=25000)
        with patch(f"{PUB}._session_doc", return_value=session), \
             patch(f"{PUB}._resolve_frais1_fee_type", return_value="application"), \
             patch(f"{PUB}._resolve_fee_from_catalog", return_value=25000), \
             patch(f"{PUB}.frappe") as mf:
            mf.exceptions.UniqueValidationError = frappe.exceptions.UniqueValidationError
            mf.UniqueValidationError = frappe.exceptions.UniqueValidationError
            mf.get_all.side_effect = [[], ["AFF-WINNER1"]]
            mf.get_doc.side_effect = [loser_insert, winner]
            from admission.api.public import _ensure_fee
            result = _ensure_fee(_applicant())
        self.assertIs(result, winner)
        mf.db.rollback.assert_called_once()
