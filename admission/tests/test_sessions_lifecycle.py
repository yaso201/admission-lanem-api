"""SESSIONS-AUTO-FERMETURE — logique de sélectionnabilité + statut d'affichage (pure).

Critère métier : une session est sélectionnable si ouverte ET non échue
(`closes_on >= aujourd'hui`, fuseau Africa/Porto-Novo ; `closes_on` absent = pas d'échéance).
Statut d'affichage : a_venir (sélectionnable) · echue (visible, non sélectionnable) ·
fermee (fermée manuellement, masquée). Style mocké — nowdate patché, aucune DB.
"""

from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import MagicMock, patch

from admission.api.sessions import is_session_selectable, session_display_status

TODAY = "2026-08-13"


class TestSelectable(TestCase):
    @patch("admission.api.sessions.nowdate", return_value=TODAY)
    def test_open_future_selectable(self, _):
        self.assertTrue(is_session_selectable({"is_open": 1, "closes_on": "2026-10-03"}))

    @patch("admission.api.sessions.nowdate", return_value=TODAY)
    def test_open_past_not_selectable(self, _):  # échue : closes_on dépassé
        self.assertFalse(is_session_selectable({"is_open": 1, "closes_on": "2026-07-24"}))

    @patch("admission.api.sessions.nowdate", return_value=TODAY)
    def test_closed_not_selectable(self, _):
        self.assertFalse(is_session_selectable({"is_open": 0, "closes_on": "2026-10-03"}))

    @patch("admission.api.sessions.nowdate", return_value=TODAY)
    def test_no_date_open_selectable(self, _):  # closes_on absent = pas d'échéance
        self.assertTrue(is_session_selectable({"is_open": 1, "closes_on": None}))

    @patch("admission.api.sessions.nowdate", return_value=TODAY)
    def test_pivot_day_still_selectable(self, _):  # GS4 : le jour même de closes_on reste ouvert
        self.assertTrue(is_session_selectable({"is_open": 1, "closes_on": TODAY}))


class TestDisplayStatus(TestCase):
    @patch("admission.api.sessions.nowdate", return_value=TODAY)
    def test_a_venir(self, _):
        self.assertEqual(session_display_status({"is_open": 1, "closes_on": "2026-10-03"}), "a_venir")

    @patch("admission.api.sessions.nowdate", return_value=TODAY)
    def test_echue_reste_visible(self, _):  # ouverte MAIS échue → echue (avant même la tâche)
        self.assertEqual(session_display_status({"is_open": 1, "closes_on": "2026-07-24"}), "echue")

    @patch("admission.api.sessions.nowdate", return_value=TODAY)
    def test_echue_apres_fermeture(self, _):  # fermée par la tâche + date passée → toujours echue
        self.assertEqual(session_display_status({"is_open": 0, "closes_on": "2026-07-24"}), "echue")

    @patch("admission.api.sessions.nowdate", return_value=TODAY)
    def test_fermee_manuelle_masquee(self, _):  # démo : fermée, date future → masquée
        self.assertEqual(session_display_status({"is_open": 0, "closes_on": "2026-09-15"}), "fermee")


class TestCreateDossierGuard(TestCase):
    """GS3 — la GARDE serveur (l'enforcement, pas l'affichage) : create_dossier refuse
    un dossier sur une session échue/fermée, même par appel direct (onglet resté ouvert)."""

    def _call(self, mock_frappe, session):
        from admission.api.public import create_dossier
        mock_frappe.request = None
        mock_frappe.form_dict = {
            "session": session.name, "level_code": "LIS-L1",
            "identite": {"prenom": "T", "nom": "U", "email": "t@t.com"},
        }
        return create_dossier()

    @patch("admission.api.sessions.nowdate", return_value=TODAY)
    @patch("admission.api.public._session_doc")
    @patch("admission.api.public.frappe")
    def test_echue_session_refused(self, mock_frappe, mock_session, _now):
        session = MagicMock(name="SES-ECHUE")
        session.name = "SES-ECHUE"
        session.is_open = 1
        session.closes_on = "2026-07-24"  # échue : < TODAY
        mock_session.return_value = session
        result = self._call(mock_frappe, session)
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "SESSION_CLOSED")

    @patch("admission.api.sessions.nowdate", return_value=TODAY)
    @patch("admission.api.public._session_doc")
    @patch("admission.api.public.frappe")
    def test_manually_closed_session_refused(self, mock_frappe, mock_session, _now):
        session = MagicMock(name="SES-FERMEE")
        session.name = "SES-FERMEE"
        session.is_open = 0  # fermée à la main, date future → NON sélectionnable
        session.closes_on = "2026-10-03"
        mock_session.return_value = session
        result = self._call(mock_frappe, session)
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "SESSION_CLOSED")


class TestCloseExpiredTask(TestCase):
    """GS1/GS6 — tâche quotidienne : ferme les échues, saute les sans-date (signalées),
    ne touche pas les sessions à venir, commit une seule fois."""

    @patch("admission.api.sessions.nowdate", return_value=TODAY)
    @patch("admission.api.sessions.frappe")
    def test_closes_echue_skips_nodate_and_future(self, mock_frappe, _now):
        from admission.api.sessions import close_expired_sessions
        mock_frappe.get_all.return_value = [
            SimpleNamespace(name="S-ECHUE", closes_on="2026-07-24"),   # < TODAY → fermée
            SimpleNamespace(name="S-FUTURE", closes_on="2026-10-03"),  # à venir → intacte
            SimpleNamespace(name="S-NODATE", closes_on=None),          # sans date → signalée
        ]
        res = close_expired_sessions()
        self.assertEqual([c["session"] for c in res["closed"]], ["S-ECHUE"])
        self.assertEqual(res["skipped_no_date"], ["S-NODATE"])
        mock_frappe.db.set_value.assert_called_once_with(
            "Admission Session", "S-ECHUE", "is_open", 0)
        mock_frappe.db.commit.assert_called_once()

    @patch("admission.api.sessions.nowdate", return_value=TODAY)
    @patch("admission.api.sessions.frappe")
    def test_idempotent_nothing_to_close(self, mock_frappe, _now):
        from admission.api.sessions import close_expired_sessions
        mock_frappe.get_all.return_value = [
            SimpleNamespace(name="S-FUTURE", closes_on="2026-10-03"),
        ]
        res = close_expired_sessions()
        self.assertEqual(res["closed"], [])
        mock_frappe.db.set_value.assert_not_called()
        mock_frappe.db.commit.assert_not_called()  # rien fermé → pas de commit
