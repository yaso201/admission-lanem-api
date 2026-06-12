"""Tests C3-ENROLL (T8) — endpoint staff.enroll : inscription réelle ACC→INS.

Direction-only, pré-vérification des gates (frais 2 payé + consentement DATA_TRANSFER)
pour un retour API propre, puis save() (contrôleur) → gates re-vérifiées + on_update
déclenche le pont campus + double-check UF. Style unitaire mocké (aligné test_etude).
"""

from unittest import TestCase
from unittest.mock import MagicMock, patch

STAFF = "admission.api.staff"


def _app(status="ACC"):
    a = MagicMock()
    a.name = "CAN-2026-00001"
    a.status = status
    return a


def _patches():
    return (
        patch(f"{STAFF}._ok", side_effect=lambda d: {"ok": True, "data": d, "error": None}),
        patch(f"{STAFF}._error", side_effect=lambda c, m, s=400: {"ok": False, "data": None, "error": {"code": c}}),
    )


class TestEnroll(TestCase):
    def _run(self, status="ACC", gate_exc=None, consent_exc=None):
        app = _app(status)
        ok, err = _patches()
        with patch(f"{STAFF}.frappe") as mf, ok, err, \
             patch("admission.api.public._check_enrollment_fee_paid", side_effect=gate_exc) as gate, \
             patch("admission.api.legal._require_consent_record", side_effect=consent_exc) as consent:
            mf.db.exists.return_value = True
            mf.get_doc.return_value = app
            from admission.api.staff import enroll
            res = enroll(dossier_id="CAN-2026-00001")
            return app, res, mf, gate, consent

    def test_direction_acc_to_ins_via_save(self):
        app, res, mf, gate, consent = self._run("ACC")
        mf.only_for.assert_called_once_with(("Admission Direction", "System Manager"))
        gate.assert_called_once_with("CAN-2026-00001")          # gate frais 2 payé
        consent.assert_called_once_with("CAN-2026-00001", "DATA_TRANSFER")
        self.assertEqual(app.status, "INS")
        # IMPÉRATIF : via save() (contrôleur) → gates re-vérifiées + on_update → pont/double-check
        app.save.assert_called_once_with(ignore_permissions=True)
        mf.db.set_value.assert_not_called()
        self.assertEqual(res["data"]["status"], "INS")

    def test_idempotent_when_already_ins(self):
        app, res, mf, gate, consent = self._run("INS")
        self.assertTrue(res["data"]["idempotent"])
        app.save.assert_not_called()
        gate.assert_not_called()

    def test_invalid_state_from_adm(self):
        app, res, mf, gate, consent = self._run("ADM")
        self.assertEqual(res["error"]["code"], "INVALID_STATE")
        app.save.assert_not_called()

    def test_gate_failed_unpaid_frais2(self):
        app, res, mf, gate, consent = self._run("ACC", gate_exc=Exception("frais 2 non payé"))
        self.assertEqual(res["error"]["code"], "GATE_FAILED")
        self.assertEqual(app.status, "ACC")   # AUCUNE transition
        app.save.assert_not_called()          # le pont n'est jamais déclenché

    def test_gate_failed_missing_consent(self):
        app, res, mf, gate, consent = self._run("ACC", consent_exc=Exception("consentement DATA_TRANSFER absent"))
        self.assertEqual(res["error"]["code"], "GATE_FAILED")
        app.save.assert_not_called()

    def test_invalid_dossier(self):
        ok, err = _patches()
        with patch(f"{STAFF}.frappe") as mf, ok, err:
            mf.db.exists.return_value = False
            from admission.api.staff import enroll
            res = enroll(dossier_id="CAN-UNKNOWN")
        self.assertEqual(res["error"]["code"], "INVALID_DOSSIER")
