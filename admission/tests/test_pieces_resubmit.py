"""Lot 3c-3a — back candidat re-soumission (modèle A : le dossier reste SOU).

Style mocké (cohérent test_pieces_verification) : frappe mocké par module, helpers de critère
purs. La sérialisation candidat 4-états+motif, le CTA token et la notif Administratifs réels
sont prouvés à la recette (Phase 4).
"""

from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from admission.tests.test_pieces_verification import _piece, _app

PUBLIC = "admission.api.public"
STAFF = "admission.api.staff"
NOTIF = "admission.api.notifications"


def _serial_app(pieces, status="SOU"):
    """Applicant minimal pour _serialize_dossier (sub-helpers mockés à part)."""
    return SimpleNamespace(
        name="CAN-1", status=status, programme_code="LIC-MI", programme_label="Lic MI",
        level_code="LIC-MI-L1", session="SES-1", bac_profile="bac_s",
        first_name="A", last_name="B", email="a@b.c", phone="+229", bac_date=None,
        conditionnel=0, motif_incompletude=None, pieces=pieces,
    )


# ───────────────────────── Bloc A — sérialisation candidat (4-états + motif) ─────────────────────────

class TestSerializeCandidat(TestCase):
    def _serialize(self, pieces, status="SOU"):
        from admission.api.public import _serialize_dossier
        return _serialize_dossier(_serial_app(pieces, status))["pieces"]

    @patch(f"{PUBLIC}._build_promotion_section", return_value={})
    @patch(f"{PUBLIC}._build_bourses_section", return_value={})
    @patch(f"{PUBLIC}._session_doc", return_value=None)
    @patch(f"{PUBLIC}._get_fee_and_payment", return_value=(None, None))
    @patch(f"{PUBLIC}.frappe")
    def test_r1_statut_reel_4_etats(self, mf, mfee, msess, mb, mp):
        mf.db.get_value.return_value = None
        by = {p["code"]: p for p in self._serialize([
            _piece("a", status="verified"), _piece("b", status="uploaded"),
            _piece("c", status="rejected", reject_reason="x"), _piece("d", status="missing"),
        ])}
        # 4 états RÉELS distincts (verified≠uploaded, rejected≠missing)
        self.assertEqual(by["a"]["statut_reel"], "verified")
        self.assertEqual(by["b"]["statut_reel"], "uploaded")
        self.assertEqual(by["c"]["statut_reel"], "rejected")
        self.assertEqual(by["d"]["statut_reel"], "missing")
        # statut rétro-compatible INCHANGÉ (le front existant ne casse pas)
        self.assertEqual(by["a"]["statut"], "deposee")
        self.assertEqual(by["b"]["statut"], "deposee")
        self.assertEqual(by["c"]["statut"], "manquante")
        self.assertEqual(by["d"]["statut"], "manquante")

    @patch(f"{PUBLIC}._build_promotion_section", return_value={})
    @patch(f"{PUBLIC}._build_bourses_section", return_value={})
    @patch(f"{PUBLIC}._session_doc", return_value=None)
    @patch(f"{PUBLIC}._get_fee_and_payment", return_value=(None, None))
    @patch(f"{PUBLIC}.frappe")
    def test_r2_motif_expose_si_rejected(self, mf, mfee, msess, mb, mp):
        mf.db.get_value.return_value = None
        by = {p["code"]: p for p in self._serialize([
            _piece("c", status="rejected", reject_reason="Illisible / floue", reject_comment="flou"),
            _piece("a", status="verified"),
        ])}
        self.assertEqual(by["c"]["reject_reason"], "Illisible / floue")
        self.assertEqual(by["c"]["reject_comment"], "flou")
        self.assertIsNone(by["a"]["reject_reason"])
        self.assertIsNone(by["a"]["reject_comment"])

    @patch(f"{PUBLIC}._build_promotion_section", return_value={})
    @patch(f"{PUBLIC}._build_bourses_section", return_value={})
    @patch(f"{PUBLIC}._session_doc", return_value=None)
    @patch(f"{PUBLIC}._get_fee_and_payment", return_value=(None, None))
    @patch(f"{PUBLIC}.frappe")
    def test_r3_requise_effective_non_regresse(self, mf, mfee, msess, mb, mp):
        from admission.api.public import requise_effective
        mf.db.get_value.return_value = None
        pieces = [
            _piece("d", required=1, staff_requirement="default"),
            _piece("r", required=0, staff_requirement="required"),
            _piece("w", required=1, staff_requirement="waived"),
        ]
        by = {p["code"]: p for p in self._serialize(pieces)}
        for code, row in zip(["d", "r", "w"], pieces):
            self.assertEqual(by[code]["requise"], requise_effective(row))


# ───────────────────────── Bloc B — endpoint candidate_resubmit ─────────────────────────

class TestCandidateResubmit(TestCase):
    def _call(self, mf, mget, app):
        mget.return_value = app
        from admission.api.public import candidate_resubmit
        return candidate_resubmit(dossier_id="CAN-1", token="t")

    @patch(f"{NOTIF}.send_resubmit_staff_notification")
    @patch(f"{PUBLIC}._require_otp_verified", return_value=None)
    @patch(f"{PUBLIC}._get_applicant")
    @patch(f"{PUBLIC}.frappe")
    def test_r4_bloque_si_piece_rejected(self, mf, mget, motp, mnotif):
        app = _app([_piece("a", status="verified"), _piece("b", status="rejected")], status="SOU")
        res = self._call(mf, mget, app)
        self.assertEqual(res["error"]["code"], "PIECES_REJECTED_PENDING")
        mf.db.set_value.assert_not_called()   # fail-fast : aucun effet
        mnotif.assert_not_called()

    @patch(f"{NOTIF}.send_resubmit_staff_notification")
    @patch(f"{PUBLIC}._require_otp_verified", return_value=None)
    @patch(f"{PUBLIC}._get_applicant")
    @patch(f"{PUBLIC}.frappe")
    def test_r5_autorise_pose_resoumis_et_notifie(self, mf, mget, motp, mnotif):
        app = _app([_piece("a", status="verified"), _piece("b", status="missing")], status="SOU")
        res = self._call(mf, mget, app)
        self.assertTrue(res["ok"])
        mf.db.set_value.assert_called_with("Admission Applicant", app.name, "resoumis", 1)
        mnotif.assert_called_once()

    @patch(f"{NOTIF}.send_resubmit_staff_notification")
    @patch(f"{PUBLIC}._require_otp_verified", return_value=None)
    @patch(f"{PUBLIC}._get_applicant")
    @patch(f"{PUBLIC}.frappe")
    def test_r6_reste_sou(self, mf, mget, motp, mnotif):
        app = _app([_piece("a", status="verified")], status="SOU")
        self._call(mf, mget, app)
        self.assertEqual(app.status, "SOU")   # aucune transition
        for c in mf.db.set_value.call_args_list:
            self.assertNotIn("status", c.args)  # resoumis seulement, jamais status

    @patch(f"{NOTIF}.send_resubmit_staff_notification")
    @patch(f"{PUBLIC}._require_otp_verified", return_value=None)
    @patch(f"{PUBLIC}._get_applicant")
    @patch(f"{PUBLIC}.frappe")
    def test_r12_une_seule_notif(self, mf, mget, motp, mnotif):
        app = _app([_piece("a", status="missing"), _piece("b", status="missing"),
                    _piece("c", status="uploaded")], status="SOU")
        self._call(mf, mget, app)
        mnotif.assert_called_once()   # 1 geste candidat = 1 notif, jamais par pièce

    @patch(f"{NOTIF}.send_resubmit_staff_notification")
    @patch(f"{PUBLIC}._require_otp_verified", return_value=None)
    @patch(f"{PUBLIC}._get_applicant")
    @patch(f"{PUBLIC}.frappe")
    def test_r13_hors_sou_refuse(self, mf, mget, motp, mnotif):
        app = _app([_piece("a", status="verified")], status="ETU")
        res = self._call(mf, mget, app)
        self.assertEqual(res["error"]["code"], "INVALID_STATE")
        mf.db.set_value.assert_not_called()
        mnotif.assert_not_called()


# ───────────────────────── Bloc C — extinction sur verify/reject ─────────────────────────

class TestExtinctionResoumis(TestCase):
    @patch(f"{STAFF}._record_piece_verdict")
    @patch(f"{STAFF}.now_datetime", return_value="t")
    @patch(f"{STAFF}.frappe")
    def test_r7_verify_eteint_resoumis(self, mf, mnow, mrec):
        mf.db.exists.return_value = True
        app = _app([_piece("a", status="uploaded")]); app.resoumis = 1
        mf.get_doc.return_value = app
        from admission.api.staff import verify_piece
        verify_piece(dossier_id="CAN-1", piece_code="a")
        self.assertEqual(app.resoumis, 0)

    @patch(f"{STAFF}._record_piece_verdict")
    @patch(f"{STAFF}.now_datetime", return_value="t")
    @patch(f"{STAFF}.frappe")
    def test_r8_reject_eteint_resoumis(self, mf, mnow, mrec):
        mf.db.exists.return_value = True
        app = _app([_piece("a", status="uploaded")]); app.resoumis = 1
        mf.get_doc.return_value = app
        from admission.api.staff import reject_piece
        reject_piece(dossier_id="CAN-1", piece_code="a", reason="Illisible / floue", comment="flou")
        self.assertEqual(app.resoumis, 0)

    @patch(f"{NOTIF}.send_resubmit_staff_notification")
    @patch(f"{STAFF}._record_piece_verdict")
    @patch(f"{STAFF}.now_datetime", return_value="t")
    @patch(f"{PUBLIC}._require_otp_verified", return_value=None)
    @patch(f"{PUBLIC}._get_applicant")
    @patch(f"{STAFF}.frappe")
    @patch(f"{PUBLIC}.frappe")
    def test_r9_deuxieme_boucle(self, mfp, mfs, mget, motp, mnow, mrec, mnotif):
        app = _app([_piece("a", status="uploaded")], status="SOU"); app.resoumis = 0
        mget.return_value = app
        mfp.db.set_value.side_effect = lambda dt, n, f, v=None: (
            setattr(app, f, v) if f == "resoumis" else None)
        mfs.db.exists.return_value = True
        mfs.get_doc.return_value = app
        from admission.api.public import candidate_resubmit
        from admission.api.staff import verify_piece
        candidate_resubmit(dossier_id="CAN-1", token="t")
        self.assertEqual(app.resoumis, 1)                 # 1ère boucle
        verify_piece(dossier_id="CAN-1", piece_code="a")  # staff re-vérifie
        self.assertEqual(app.resoumis, 0)                 # éteint
        candidate_resubmit(dossier_id="CAN-1", token="t")  # 2ème boucle (pièce verified, 0 rejected)
        self.assertEqual(app.resoumis, 1)                 # se rallume


# ───────────────────────── Bloc D — CTA récap tokenisé (rotation) ─────────────────────────

class TestRecapCtaTokenise(TestCase):
    @patch(f"{STAFF}.send_pieces_recap_notification")
    @patch(f"{STAFF}.log_event")
    @patch(f"{STAFF}.add_days", return_value="2026-07-08 00:00:00")
    @patch(f"{STAFF}.now_datetime", return_value="2026-07-01 00:00:00")
    @patch(f"{STAFF}.frappe")
    def test_r10_cta_recap_tokenise_valide(self, mf, mnow, madd, mlog, msend):
        from admission.api.public import _hash, TOKEN_TTL_DAYS
        from admission.api.email_template import _portal_link
        mf.db.exists.return_value = True
        app = _app([_piece("a", status="verified")], status="SOU")  # tout-terminal → non bloqué
        mf.get_doc.return_value = app
        from admission.api.staff import notify_pieces_recap
        res = notify_pieces_recap(dossier_id="CAN-1")
        self.assertTrue(res["ok"])
        # (i) token rotaté
        self.assertTrue(app.dossier_token_hash)
        self.assertEqual(app.otp_verified, 0)
        self.assertTrue(app.token_expires_at)
        madd.assert_called_with("2026-07-01 00:00:00", TOKEN_TTL_DAYS)   # expiry 7j
        # (ii) notif reçoit le token
        args, kwargs = msend.call_args
        tok = kwargs.get("token") or (args[3] if len(args) > 3 else None)
        self.assertTrue(tok)
        # (iii) lien VALIDE : hash stocké == _hash(tok)
        self.assertEqual(app.dossier_token_hash, _hash(tok))
        # (iv) CTA → /reprise tokenisé, pas /suivi
        url = _portal_link(app, token=tok)
        self.assertIn("/reprise", url)
        self.assertIn("token=", url)
        self.assertNotIn("/suivi", url)


# ───────────────────────── Bloc E — list_dossiers expose resoumis ─────────────────────────

class TestListExposeResoumis(TestCase):
    @patch(f"{STAFF}.frappe")
    def test_r11_list_dossiers_expose_resoumis(self, mf):
        mf.get_list.return_value = []
        from admission.api.staff import list_dossiers
        list_dossiers()
        fields = mf.get_list.call_args.kwargs.get("fields", [])
        self.assertIn("resoumis", fields)

    def _row(self, resoumis):
        return SimpleNamespace(
            name="CAN-1", applicant_name="A B", programme_code="LIC-MI", programme_label="Lic MI",
            level_code="LIC-MI-L1", session="SES-1", status="SOU", conditionnel=0, bac_verified=0,
            resoumis=resoumis, rang_liste_attente=None,
            creation="2026-07-01 00:00:00", modified="2026-07-01 00:00:00",
        )

    @patch(f"{STAFF}.frappe")
    def test_r11bis_reponse_shaped_contient_resoumis(self, mf):
        # R11bis — teste la SORTIE (le dict shaped renvoyé au front), pas les fields du get_list.
        # C'est le trou de R11 (3c-3a) : le champ était lu de la DB mais jeté au reshaping.
        mf.get_list.return_value = [self._row(resoumis=1)]
        mf.get_all.return_value = []   # enrichissements (fees/pending/pieces/sessions) vides
        from admission.api.staff import list_dossiers
        res = list_dossiers()
        d = res["data"]["dossiers"][0]
        self.assertIs(d["resoumis"], True)

    @patch(f"{STAFF}.frappe")
    def test_r11bis_faux_par_defaut(self, mf):
        mf.get_list.return_value = [self._row(resoumis=0)]
        mf.get_all.return_value = []
        from admission.api.staff import list_dossiers
        res = list_dossiers()
        self.assertIs(res["data"]["dossiers"][0]["resoumis"], False)
