"""Tests C1-NOTIFS — notifications de décision candidat (génériques/Licence).

Option B : send_decision_notification générique dans notifications.py + socle commun
_send_candidate_mail factorisé avec send_prepa_decision_notification (un seul socle, deux variantes).
Style unitaire mocké.
"""

import types
from unittest import TestCase
from unittest.mock import MagicMock, patch

NOTIF = "admission.api.notifications"
STAFF = "admission.api.staff"


def _staff_app(status="ETU", notes_validated=1):
    a = MagicMock()
    a.name = "CAN-2026-00001"
    a.status = status
    a.notes_validated = notes_validated
    a.validated_scholarships = None  # C2-BOURSES : accept lit les bourses validées pour le mail
    return a


def _staff_patches():
    return (
        patch(f"{STAFF}._ok", side_effect=lambda d: {"ok": True, "data": d, "error": None}),
        patch(f"{STAFF}._error", side_effect=lambda c, m, s=400: {"ok": False, "data": None, "error": {"code": c}}),
    )


def _app(email="a@x.bj"):
    return types.SimpleNamespace(name="CAN-2026-00001", applicant_name="Ama", email=email, notes_concours="{}")


class TestSendDecisionNotification(TestCase):
    def test_admissible(self):
        with patch(f"{NOTIF}.frappe") as mf:
            from admission.api.notifications import send_decision_notification
            send_decision_notification(_app(), "admissible")
        mf.sendmail.assert_called_once()
        self.assertIn("admissible", mf.sendmail.call_args.kwargs["message"])
        self.assertEqual(mf.sendmail.call_args.kwargs["recipients"], ["a@x.bj"])

    def test_refuse_includes_motif(self):
        with patch(f"{NOTIF}.frappe") as mf:
            from admission.api.notifications import send_decision_notification
            send_decision_notification(_app(), "refusé", motif="Niveau insuffisant")
        msg = mf.sendmail.call_args.kwargs["message"]
        self.assertIn("Candidature non retenue", msg)  # libellé hero du template (handoff §5)
        self.assertIn("Niveau insuffisant", msg)

    def test_motif_escaped(self):
        captured = {}
        with patch(f"{NOTIF}.frappe") as mf:
            mf.sendmail.side_effect = lambda **kw: captured.update(kw)
            from admission.api.notifications import send_decision_notification
            send_decision_notification(_app(), "refusé", motif="<script>alert(1)</script>")
        self.assertNotIn("<script>", captured["message"])  # anti-injection email

    def test_non_blocking(self):
        with patch(f"{NOTIF}.frappe") as mf:
            mf.sendmail.side_effect = RuntimeError("smtp down")
            from admission.api.notifications import send_decision_notification
            send_decision_notification(_app(), "admis")  # ne doit PAS lever

    def test_skips_without_email(self):
        with patch(f"{NOTIF}.frappe") as mf:
            from admission.api.notifications import send_decision_notification
            send_decision_notification(_app(email=None), "admis")
            mf.sendmail.assert_not_called()


class TestDecisionWiring(TestCase):
    """Coordination Prépa/Licence (if/else strict) : un seul mail par décision, jamais deux."""

    def _decide(self, fn_name, is_prepa, **kwargs):
        app = _staff_app("ADM" if fn_name == "accept_admission" else "ETU")
        ok, err = _staff_patches()
        with patch(f"{STAFF}.frappe") as mf, ok, err, patch(f"{STAFF}._is_prepa", return_value=is_prepa), \
             patch(f"{STAFF}.now_datetime", return_value="2026-06-11 10:00:00"), \
             patch(f"{STAFF}.send_decision_notification") as gen, \
             patch(f"{STAFF}.send_prepa_decision_notification") as prepa:
            mf.db.exists.return_value = True
            mf.get_doc.return_value = app
            mf.session.user = "resp@lanem.bj"
            import admission.api.staff as staff
            getattr(staff, fn_name)(dossier_id="CAN-2026-00001", **kwargs)
            return app, gen, prepa

    def test_licence_mark_admissible_generic_only(self):
        _, gen, prepa = self._decide("mark_admissible", is_prepa=False)
        gen.assert_called_once_with(_unused := gen.call_args[0][0], "admissible")
        prepa.assert_not_called()  # pas de double envoi

    def test_prepa_mark_admissible_prepa_only(self):
        _, gen, prepa = self._decide("mark_admissible", is_prepa=True)
        prepa.assert_called_once()
        gen.assert_not_called()  # pas de double envoi

    def test_licence_refuse_passes_motif(self):
        app, gen, prepa = self._decide("refuse", is_prepa=False, motif="Niveau insuffisant")
        self.assertEqual(gen.call_args[0][1], "refusé")
        self.assertEqual(gen.call_args.kwargs.get("motif"), "Niveau insuffisant")  # le candidat saura pourquoi
        prepa.assert_not_called()

    def test_accept_admission_notifies_acceptance(self):
        _, gen, prepa = self._decide("accept_admission", is_prepa=False)
        gen.assert_called_once()
        self.assertEqual(gen.call_args[0][1], "admission acceptée")
        # C2-BOURSES : la bourse part AVEC la décision (D11 §6.3) — ici aucune validée → liste vide
        self.assertEqual(gen.call_args.kwargs.get("bourses"), [])


class TestFactoredBase(TestCase):
    def test_prepa_and_generic_share_send_base(self):
        """Factorisation : générique ET Prépa passent par le même socle _send_candidate_mail."""
        with patch(f"{NOTIF}._send_candidate_mail") as base:
            from admission.api.notifications import send_decision_notification, send_prepa_decision_notification
            send_decision_notification(_app(), "admis")
            send_prepa_decision_notification(_app(), "admis")
        self.assertEqual(base.call_count, 2)
