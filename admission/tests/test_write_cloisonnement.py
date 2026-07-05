"""FIX-D-CONF-04 — le cloisonnement (has_permission) s'applique à l'ÉCRITURE, pas seulement à la
lecture. Les mutations font save(ignore_permissions=True) → le hook has_permission est court-circuité ;
_guard_write_scope le consulte EXPLICITEMENT avant la mutation.

Frontière unitaire : test_consult prouve la LOGIQUE de has_permission (in-scope→None, out→False).
Ici on prouve le CÂBLAGE : chaque endpoint consulte la garde et répond correctement (refus 403 typé
hors périmètre ; procède in-scope/OFF/bypass). On patche donc permissions.has_permission / value_in_scope.
Style mocké, aligné sur test_etude.
"""

from unittest import TestCase
from unittest.mock import MagicMock, patch

STAFF = "admission.api.staff"
PERM = "admission.api.permissions"


def _app(status="ETU"):
    a = MagicMock()
    a.name = "CAN-2026-00001"
    a.status = status
    a.rang_liste_attente = None
    a.session = "SES-REELLE"
    a.get = lambda k, default=None: getattr(a, k, default)
    return a


def _patches():
    return (
        patch(f"{STAFF}._ok", side_effect=lambda d: {"ok": True, "data": d, "error": None}),
        patch(f"{STAFF}._error", side_effect=lambda c, m, s=400: {"ok": False, "data": None, "error": {"code": c, "http": s}}),
    )


class TestWriteScopeGuard(TestCase):
    """has_permission → False (hors périmètre) : mutation REFUSÉE, dossier non muté, 403 typé."""

    def _call_refused(self, fn_name, **kwargs):
        app = _app("ETU")
        ok, err = _patches()
        with patch(f"{STAFF}.frappe") as mf, ok, err, \
             patch(f"{PERM}.has_permission", return_value=False):
            mf.db.exists.return_value = True
            mf.get_doc.return_value = app
            mf.session.user = "resp@lanem.bj"
            fn = getattr(__import__("admission.api.staff", fromlist=[fn_name]), fn_name)
            res = fn(dossier_id="CAN-2026-00001", **kwargs)
        return app, res

    def test_waitlist_out_of_scope_refused(self):
        app, res = self._call_refused("waitlist", rang=3)
        self.assertEqual(res["error"]["code"], "FORBIDDEN_SCOPE")
        self.assertEqual(res["error"]["http"], 403)
        app.save.assert_not_called()
        self.assertEqual(app.status, "ETU")  # NON muté

    def test_mark_admissible_out_of_scope_refused(self):
        app, res = self._call_refused("mark_admissible")
        self.assertEqual(res["error"]["code"], "FORBIDDEN_SCOPE")
        app.save.assert_not_called()

    def test_reject_dossier_out_of_scope_refused(self):
        app = _app("SOU")
        ok, err = _patches()
        with patch(f"{STAFF}.frappe") as mf, ok, err, patch(f"{PERM}.has_permission", return_value=False):
            mf.db.exists.return_value = True
            mf.get_doc.return_value = app
            mf.session.user = "admin@lanem.bj"
            from admission.api.staff import reject_dossier
            res = reject_dossier(dossier_id="CAN-2026-00001", motif="x")
        self.assertEqual(res["error"]["code"], "FORBIDDEN_SCOPE")
        app.save.assert_not_called()

    def test_set_waitlist_rank_out_of_scope_refused(self):
        app = _app("ATT")
        ok, err = _patches()
        with patch(f"{STAFF}.frappe") as mf, ok, err, patch(f"{PERM}.has_permission", return_value=False):
            mf.db.exists.return_value = True
            mf.get_doc.return_value = app
            mf.session.user = "resp@lanem.bj"
            from admission.api.staff import set_waitlist_rank
            res = set_waitlist_rank(dossier_id="CAN-2026-00001", rang=5)
        self.assertEqual(res["error"]["code"], "FORBIDDEN_SCOPE")
        app.save.assert_not_called()

    def test_withdraw_out_of_scope_refused(self):
        app, res = self._call_refused("withdraw", motif="désistement")
        self.assertEqual(res["error"]["code"], "FORBIDDEN_SCOPE")
        app.save.assert_not_called()


class TestPieceScopeGuard(TestCase):
    """Les 5 endpoints pièce passent par _resolve_piece_sou → une seule garde couvre les cinq."""

    def test_resolve_piece_sou_out_of_scope_returns_err(self):
        app = _app("SOU")
        ok, err = _patches()
        with patch(f"{STAFF}.frappe") as mf, ok, err, patch(f"{PERM}.has_permission", return_value=False):
            mf.db.exists.return_value = True
            mf.get_doc.return_value = app
            mf.session.user = "admin@lanem.bj"
            from admission.api.staff import _resolve_piece_sou
            applicant, row, err_res = _resolve_piece_sou("CAN-2026-00001", "diplome_bac")
        self.assertIsNone(applicant)
        self.assertEqual(err_res["error"]["code"], "FORBIDDEN_SCOPE")

    def test_resolve_piece_sou_in_scope_proceeds(self):
        app = _app("SOU")
        app.pieces = [MagicMock(piece_code="diplome_bac")]
        ok, err = _patches()
        with patch(f"{STAFF}.frappe") as mf, ok, err, patch(f"{PERM}.has_permission", return_value=None):
            mf.db.exists.return_value = True
            mf.get_doc.return_value = app
            mf.session.user = "admin@lanem.bj"
            from admission.api.staff import _resolve_piece_sou
            applicant, row, err_res = _resolve_piece_sou("CAN-2026-00001", "diplome_bac")
        self.assertIsNone(err_res)
        self.assertIs(applicant, app)


class TestCloseSessionScopeGuard(TestCase):
    def test_close_session_out_of_scope_refused(self):
        ok, err = _patches()
        with patch(f"{STAFF}.frappe") as mf, ok, err, patch(f"{PERM}.value_in_scope", return_value=False):
            mf.db.exists.return_value = True
            from admission.api.staff import close_session
            res = close_session(session="SES-ETRANGERE", dry_run=1)
        self.assertEqual(res["error"]["code"], "FORBIDDEN_SCOPE")

    def test_close_session_in_scope_proceeds_to_normal_flow(self):
        ok, err = _patches()
        sess = MagicMock(is_open=1, label="X")
        with patch(f"{STAFF}.frappe") as mf, ok, err, patch(f"{PERM}.value_in_scope", return_value=True):
            mf.db.exists.return_value = True
            mf.get_doc.return_value = sess
            mf.get_all.return_value = []          # 0 dossier → preview vide, pas de bascule
            mf.utils.cint = lambda x: int(x or 0)
            from admission.api.staff import close_session
            res = close_session(session="SES-A-MOI", dry_run=1)
        self.assertNotIn("FORBIDDEN_SCOPE", str(res))
        self.assertTrue(res["ok"])


class TestOffAndInScopeNonRegression(TestCase):
    """OFF (défaut) ET in-scope : has_permission → None → l'endpoint procède NORMALEMENT."""

    def _call_proceeds(self, has_perm_ret):
        app = _app("ETU")
        ok, err = _patches()
        with patch(f"{STAFF}.frappe") as mf, ok, err, \
             patch(f"{STAFF}.now_datetime", return_value="2026-06-11 10:00:00"), \
             patch(f"{STAFF}._is_prepa", return_value=False), \
             patch(f"{STAFF}.send_decision_notification"), \
             patch(f"{PERM}.has_permission", return_value=has_perm_ret):
            mf.db.exists.return_value = True
            mf.get_doc.return_value = app
            mf.session.user = "resp@lanem.bj"
            from admission.api.staff import mark_admissible
            res = mark_admissible(dossier_id="CAN-2026-00001")
        return app, res

    def test_off_defers_and_mutates(self):
        # OFF → has_permission retourne None (défère) → mutation NOMINALE (non-régression stricte)
        app, res = self._call_proceeds(None)
        self.assertTrue(res["ok"])
        self.assertEqual(app.status, "ADM")
        app.save.assert_called_once()

    def test_in_scope_defers_and_mutates(self):
        # ON in-scope → has_permission retourne None aussi → mutation nominale (ne sur-bloque pas)
        app, res = self._call_proceeds(None)
        self.assertEqual(app.status, "ADM")
