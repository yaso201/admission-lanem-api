"""Lot 3c-1 — contrôle documentaire par pièce (back socle).

Style unitaire mocké (cohérent test_aco / test_etude : les transitions staff sont mockées car le
Workflow Frappe exige le rôle au save — exercé en vrai à la recette). Les helpers de critère sont
testés purs ; les endpoints via frappe mocké. La preuve d'intégration réelle (schéma + Workflow REJ
+ Verdict + garde + download) est faite à la recette (Phase 4).
"""

from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import MagicMock, patch

PUBLIC = "admission.api.public"
STAFF = "admission.api.staff"


def _piece(code, required=1, status="uploaded", staff_requirement="default",
           reject_reason=None, reject_comment=None, file=None, label=None):
    return SimpleNamespace(piece_code=code, label=label or code, required=required, status=status,
                           staff_requirement=staff_requirement, reject_reason=reject_reason,
                           reject_comment=reject_comment, file=file, verdict_at=None, verdict_by=None)


def _app(pieces, status="SOU", name="CAN-1"):
    a = MagicMock()
    a.name = name
    a.status = status
    a.pieces = pieces
    return a


# ───────────────────────── Helpers de critère (purs) ─────────────────────────

class TestRequiseEffective(TestCase):
    def test_default_suit_le_structurel(self):
        from admission.api.public import requise_effective
        self.assertTrue(requise_effective(_piece("x", required=1, staff_requirement="default")))
        self.assertFalse(requise_effective(_piece("x", required=0, staff_requirement="default")))

    def test_required_surcharge_optionnelle(self):
        # H : require d'un structurellement optionnel → effective True
        from admission.api.public import requise_effective
        self.assertTrue(requise_effective(_piece("x", required=0, staff_requirement="required")))

    def test_waived_surcharge_requise(self):
        from admission.api.public import requise_effective
        self.assertFalse(requise_effective(_piece("x", required=1, staff_requirement="waived")))


class TestHelpersGardes(TestCase):
    def test_pieces_requises_non_verifiees(self):
        from admission.api.public import pieces_requises_non_verifiees
        app = _app([
            _piece("a", required=1, status="verified"),
            _piece("b", required=1, status="uploaded"),       # non verified → bloque
            _piece("c", required=1, status="missing", staff_requirement="waived"),  # waived → exclu
        ])
        out = pieces_requises_non_verifiees(app)
        self.assertEqual([p["code"] for p in out], ["b"])

    def test_notify_blocked_uploaded_non_traite(self):
        from admission.api.public import notify_pieces_blocked
        app = _app([_piece("a", required=1, status="uploaded")])
        self.assertTrue(notify_pieces_blocked(app))

    def test_notify_blocked_missing_default_non_qualifie(self):
        from admission.api.public import notify_pieces_blocked
        app = _app([_piece("a", required=1, status="missing", staff_requirement="default")])
        self.assertTrue(notify_pieces_blocked(app))

    def test_notify_autorise_terminal(self):
        from admission.api.public import notify_pieces_blocked
        app = _app([
            _piece("a", required=1, status="verified"),
            _piece("b", required=1, status="rejected"),
            _piece("c", required=1, status="missing", staff_requirement="required"),  # qualifié → à fournir
            _piece("d", required=0, status="missing", staff_requirement="waived"),
        ])
        self.assertFalse(notify_pieces_blocked(app))

    def test_pieces_recap(self):
        from admission.api.public import pieces_recap
        app = _app([
            _piece("a", required=1, status="rejected", reject_reason="Illisible / floue", reject_comment="flou"),
            _piece("b", required=1, status="missing", staff_requirement="required"),
            _piece("c", required=1, status="verified"),
        ])
        r = pieces_recap(app)
        self.assertEqual([p["code"] for p in r["rejetees"]], ["a"])
        self.assertEqual([p["code"] for p in r["a_fournir"]], ["b"])

    def test_v20_garde_paiement_independante_de_requise_effective(self):
        # V20 : la garde paiement (Lot 3a) compte sur required STRUCTUREL, pas requise_effective.
        # Un require staff (post-paiement) n'affecte donc pas la garde paiement.
        from admission.api.public import pieces_requises_manquantes, requise_effective
        p = _piece("diplome_bac", required=0, status="missing", staff_requirement="required")
        app = _app([p])
        self.assertEqual(pieces_requises_manquantes(app), [])   # required=0 → pas de blocage paiement
        self.assertTrue(requise_effective(p))                   # mais requise_effective True (gardes 3c)

    def test_v19_requise_effective_remonte(self):
        # V19 (cœur) : une pièce required par staff → requise_effective True (ce que get_dossier renvoie).
        from admission.api.public import requise_effective
        self.assertTrue(requise_effective(_piece("diplome_bac", required=0, staff_requirement="required")))


# ───────────────────────── Endpoints staff (frappe mocké) ─────────────────────────

class TestVerifyRejectPiece(TestCase):
    @patch(f"{STAFF}._record_piece_verdict")
    @patch(f"{STAFF}.now_datetime", return_value="2026-06-27 10:00:00")
    @patch(f"{STAFF}.frappe")
    def test_v1_verify(self, mf, mnow, mrec):
        mf.db.exists.return_value = True
        mf.session.user = "agent@lanem.bj"
        row = _piece("identite", status="uploaded")
        mf.get_doc.return_value = _app([row])
        from admission.api.staff import verify_piece
        res = verify_piece(dossier_id="CAN-1", piece_code="identite")
        self.assertTrue(res["ok"])
        self.assertEqual(row.status, "verified")
        self.assertEqual(row.verdict_by, "agent@lanem.bj")
        self.assertEqual(row.verdict_at, "2026-06-27 10:00:00")
        mrec.assert_called_once_with("CAN-1", "identite", "verify")

    @patch(f"{STAFF}._record_piece_verdict")
    @patch(f"{STAFF}.now_datetime", return_value="2026-06-27 10:00:00")
    @patch(f"{STAFF}.frappe")
    def test_v2_reject(self, mf, mnow, mrec):
        mf.db.exists.return_value = True
        row = _piece("identite", status="uploaded")
        mf.get_doc.return_value = _app([row])
        from admission.api.staff import reject_piece
        res = reject_piece(dossier_id="CAN-1", piece_code="identite",
                           reason="Illisible / floue", comment="flou")
        self.assertTrue(res["ok"])
        self.assertEqual(row.status, "rejected")
        self.assertEqual(row.reject_reason, "Illisible / floue")
        self.assertEqual(row.reject_comment, "flou")
        mrec.assert_called_once_with("CAN-1", "identite", "reject",
                                     reason="Illisible / floue", comment="flou")

    @patch(f"{STAFF}._record_piece_verdict")
    @patch(f"{STAFF}.now_datetime", return_value="t")
    @patch(f"{STAFF}.frappe")
    def test_va_autre_force_commentaire(self, mf, mnow, mrec):
        mf.db.exists.return_value = True
        mf.get_doc.return_value = _app([_piece("identite", status="uploaded")])
        from admission.api.staff import reject_piece
        ko = reject_piece(dossier_id="CAN-1", piece_code="identite", reason="Autre", comment="")
        self.assertFalse(ko["ok"])
        self.assertEqual(ko["error"]["code"], "COMMENT_REQUIRED")
        ok = reject_piece(dossier_id="CAN-1", piece_code="identite", reason="Autre", comment="cas particulier")
        self.assertTrue(ok["ok"])

    @patch(f"{STAFF}._record_piece_verdict")
    @patch(f"{STAFF}.now_datetime", return_value="t")
    @patch(f"{STAFF}.frappe")
    def test_reason_invalide(self, mf, mnow, mrec):
        mf.db.exists.return_value = True
        mf.get_doc.return_value = _app([_piece("identite", status="uploaded")])
        from admission.api.staff import reject_piece
        res = reject_piece(dossier_id="CAN-1", piece_code="identite", reason="n'importe quoi", comment="x")
        self.assertEqual(res["error"]["code"], "REASON_INVALID")

    @patch(f"{STAFF}._record_piece_verdict")
    @patch(f"{STAFF}.now_datetime", return_value="t")
    @patch(f"{STAFF}.frappe")
    def test_v8_bloque_hors_sou(self, mf, mnow, mrec):
        mf.db.exists.return_value = True
        mf.get_doc.return_value = _app([_piece("identite", status="uploaded")], status="ETU")
        from admission.api.staff import verify_piece
        res = verify_piece(dossier_id="CAN-1", piece_code="identite")
        self.assertEqual(res["error"]["code"], "INVALID_STATE")
        mrec.assert_not_called()

    @patch(f"{STAFF}.frappe")
    def test_v13_role_garde(self, mf):
        mf.only_for.side_effect = PermissionError("403")
        from admission.api.staff import verify_piece
        with self.assertRaises(PermissionError):
            verify_piece(dossier_id="CAN-1", piece_code="identite")

    @patch(f"{STAFF}._record_piece_verdict")
    @patch(f"{STAFF}.now_datetime", return_value="t")
    @patch(f"{STAFF}.frappe")
    def test_v9_fusion_verify_diplome(self, mf, mnow, mrec):
        mf.db.exists.return_value = True
        app = _app([_piece("diplome_bac", status="uploaded")])
        mf.get_doc.return_value = app
        from admission.api.staff import verify_piece
        verify_piece(dossier_id="CAN-1", piece_code="diplome_bac")
        self.assertEqual(app.bac_verified, 1)

    @patch(f"{STAFF}._record_piece_verdict")
    @patch(f"{STAFF}.now_datetime", return_value="t")
    @patch(f"{STAFF}.frappe")
    def test_v9bis_reject_diplome_verified_remet_bac_verified_0(self, mf, mnow, mrec):
        # révision : un diplôme déjà verified peut être re-rejeté → bac_verified repasse 0.
        mf.db.exists.return_value = True
        app = _app([_piece("diplome_bac", status="verified")])
        mf.get_doc.return_value = app
        from admission.api.staff import reject_piece
        res = reject_piece(dossier_id="CAN-1", piece_code="diplome_bac", reason="Non conforme", comment="")
        self.assertTrue(res["ok"])
        self.assertEqual(app.bac_verified, 0)

    @patch(f"{STAFF}._record_piece_verdict")
    @patch(f"{STAFF}.now_datetime", return_value="t")
    @patch(f"{STAFF}.frappe")
    def test_v17_historique_revision(self, mf, mnow, mrec):
        # verify puis reject (révision) sur la même pièce → 2 verdicts ordonnés.
        mf.db.exists.return_value = True
        row = _piece("identite", status="uploaded")
        mf.get_doc.return_value = _app([row])
        from admission.api.staff import verify_piece, reject_piece
        verify_piece(dossier_id="CAN-1", piece_code="identite")
        reject_piece(dossier_id="CAN-1", piece_code="identite", reason="Expirée", comment="")
        actions = [c.args[2] for c in mrec.call_args_list]
        self.assertEqual(actions, ["verify", "reject"])


class TestRequireWaive(TestCase):
    @patch(f"{STAFF}._record_piece_verdict")
    @patch(f"{STAFF}.frappe")
    def test_v5_require(self, mf, mrec):
        mf.db.exists.return_value = True
        row = _piece("diplome_bac", required=0, status="missing")
        mf.get_doc.return_value = _app([row])
        from admission.api.staff import require_piece
        from admission.api.public import requise_effective
        require_piece(dossier_id="CAN-1", piece_code="diplome_bac")
        self.assertEqual(row.staff_requirement, "required")
        self.assertTrue(requise_effective(row))
        mrec.assert_called_once_with("CAN-1", "diplome_bac", "require")

    @patch(f"{STAFF}._record_piece_verdict")
    @patch(f"{STAFF}.frappe")
    def test_v6_waive(self, mf, mrec):
        mf.db.exists.return_value = True
        row = _piece("identite", required=1, status="missing")
        mf.get_doc.return_value = _app([row])
        from admission.api.staff import waive_piece
        from admission.api.public import requise_effective
        waive_piece(dossier_id="CAN-1", piece_code="identite")
        self.assertEqual(row.staff_requirement, "waived")
        self.assertFalse(requise_effective(row))

    @patch(f"{STAFF}._record_piece_verdict")
    @patch(f"{STAFF}.frappe")
    def test_v7_revisable(self, mf, mrec):
        mf.db.exists.return_value = True
        row = _piece("diplome_bac", required=0, status="missing")
        mf.get_doc.return_value = _app([row])
        from admission.api.staff import require_piece, waive_piece
        require_piece(dossier_id="CAN-1", piece_code="diplome_bac")
        waive_piece(dossier_id="CAN-1", piece_code="diplome_bac")
        self.assertEqual(row.staff_requirement, "waived")
        self.assertEqual([c.args[2] for c in mrec.call_args_list], ["require", "waive"])

    @patch(f"{STAFF}._record_piece_verdict")
    @patch(f"{STAFF}.frappe")
    def test_vr1_reset_depuis_required(self, mf, mrec):
        # VR1 + VR3 : reset d'un required → default ; requise_effective revient au structurel.
        mf.db.exists.return_value = True
        row = _piece("diplome_bac", required=0, status="missing", staff_requirement="required")
        mf.get_doc.return_value = _app([row])
        from admission.api.staff import reset_piece_requirement
        from admission.api.public import requise_effective
        res = reset_piece_requirement(dossier_id="CAN-1", piece_code="diplome_bac")
        self.assertTrue(res["ok"])
        self.assertEqual(row.staff_requirement, "default")
        self.assertFalse(requise_effective(row))   # default → required structurel (0)
        mrec.assert_called_once_with("CAN-1", "diplome_bac", "reset")

    @patch(f"{STAFF}._record_piece_verdict")
    @patch(f"{STAFF}.frappe")
    def test_vr2_reset_depuis_waived(self, mf, mrec):
        # VR2 : reset d'un waived → default.
        mf.db.exists.return_value = True
        row = _piece("identite", required=1, status="missing", staff_requirement="waived")
        mf.get_doc.return_value = _app([row])
        from admission.api.staff import reset_piece_requirement
        from admission.api.public import requise_effective
        reset_piece_requirement(dossier_id="CAN-1", piece_code="identite")
        self.assertEqual(row.staff_requirement, "default")
        self.assertTrue(requise_effective(row))     # default → required structurel (1)

    @patch(f"{STAFF}._record_piece_verdict")
    @patch(f"{STAFF}.frappe")
    def test_vr4_bloque_hors_sou(self, mf, mrec):
        mf.db.exists.return_value = True
        mf.get_doc.return_value = _app([_piece("identite", staff_requirement="required")], status="ETU")
        from admission.api.staff import reset_piece_requirement
        res = reset_piece_requirement(dossier_id="CAN-1", piece_code="identite")
        self.assertEqual(res["error"]["code"], "INVALID_STATE")
        mrec.assert_not_called()

    @patch(f"{STAFF}.frappe")
    def test_vr5_role_garde(self, mf):
        mf.only_for.side_effect = PermissionError("403")
        from admission.api.staff import reset_piece_requirement
        with self.assertRaises(PermissionError):
            reset_piece_requirement(dossier_id="CAN-1", piece_code="identite")


class TestReUpload(TestCase):
    @patch(f"{PUBLIC}._record_piece_verdict")
    @patch(f"{PUBLIC}.log_event")
    @patch(f"{PUBLIC}.now_datetime", return_value="t")
    @patch(f"{PUBLIC}.frappe")
    def test_v3_reupload_rejected_efface_rejet(self, mf, mnow, mlog, mrec):
        row = _piece("identite", status="rejected", reject_reason="Illisible / floue", reject_comment="flou")
        app = _app([row])
        from admission.api.public import _mark_piece_uploaded
        res = _mark_piece_uploaded(app, "identite", "FILE-1")
        self.assertTrue(res["ok"])
        self.assertEqual(row.status, "uploaded")
        self.assertIsNone(row.reject_reason)
        self.assertIsNone(row.reject_comment)
        mrec.assert_called_once_with("CAN-1", "identite", "reset")

    @patch(f"{PUBLIC}._record_piece_verdict")
    @patch(f"{PUBLIC}.log_event")
    @patch(f"{PUBLIC}.now_datetime", return_value="t")
    @patch(f"{PUBLIC}.frappe")
    def test_v4_reupload_verified_interdit(self, mf, mnow, mlog, mrec):
        row = _piece("identite", status="verified", file="OLD")
        app = _app([row])
        from admission.api.public import _mark_piece_uploaded
        res = _mark_piece_uploaded(app, "identite", "FILE-NEW")
        self.assertFalse(res["ok"])
        self.assertEqual(res["error"]["code"], "PIECE_ALREADY_VERIFIED")
        self.assertEqual(row.status, "verified")     # inchangée
        self.assertEqual(row.file, "OLD")
        mrec.assert_not_called()


class TestRejectReopenDossier(TestCase):
    @patch(f"{STAFF}.log_event")
    @patch(f"{STAFF}.send_decision_notification")
    @patch(f"{STAFF}._stamp_decision")
    @patch(f"{STAFF}.frappe")
    def test_v12_reject_then_reopen(self, mf, mstamp, mnotif, mlog):
        mf.db.exists.return_value = True
        app = _app([], status="SOU")
        mf.get_doc.return_value = app
        from admission.api.staff import reject_dossier, reopen_dossier
        r1 = reject_dossier(dossier_id="CAN-1", motif="Pièces non conformes après 3 relances")
        self.assertEqual(r1["data"]["status"], "REJ")
        self.assertEqual(app.status, "REJ")
        self.assertEqual(app.motif_rejet, "Pièces non conformes après 3 relances")
        mnotif.assert_called_once()
        app.status = "REJ"
        r2 = reopen_dossier(dossier_id="CAN-1")
        self.assertEqual(r2["data"]["status"], "SOU")
        self.assertEqual(app.status, "SOU")
        self.assertIsNone(app.motif_rejet)

    @patch(f"{STAFF}.frappe")
    def test_reject_motif_obligatoire(self, mf):
        mf.db.exists.return_value = True
        mf.get_doc.return_value = _app([], status="SOU")
        from admission.api.staff import reject_dossier
        res = reject_dossier(dossier_id="CAN-1", motif="  ")
        self.assertEqual(res["error"]["code"], "MOTIF_REQUIRED")

    @patch(f"{STAFF}.frappe")
    def test_reject_role_garde(self, mf):
        mf.only_for.side_effect = PermissionError("403")
        from admission.api.staff import reject_dossier
        with self.assertRaises(PermissionError):
            reject_dossier(dossier_id="CAN-1", motif="x")


class TestNotifyRecap(TestCase):
    @patch(f"{STAFF}.send_pieces_recap_notification")
    @patch(f"{STAFF}.log_event")
    @patch(f"{STAFF}.frappe")
    def test_v14_bloque_si_uploaded_non_traite(self, mf, mlog, msend):
        mf.db.exists.return_value = True
        mf.get_doc.return_value = _app([_piece("a", required=1, status="uploaded")], status="SOU")
        from admission.api.staff import notify_pieces_recap
        res = notify_pieces_recap(dossier_id="CAN-1")
        self.assertEqual(res["error"]["code"], "PIECES_NON_TRAITEES")
        msend.assert_not_called()

    @patch(f"{STAFF}.send_pieces_recap_notification")
    @patch(f"{STAFF}.log_event")
    @patch(f"{STAFF}.frappe")
    def test_v15_autorise_et_envoie(self, mf, mlog, msend):
        mf.db.exists.return_value = True
        mf.get_doc.return_value = _app([
            _piece("a", required=1, status="rejected", reject_reason="Illisible / floue"),
            _piece("b", required=1, status="missing", staff_requirement="required"),
            _piece("c", required=1, status="verified"),
        ], status="SOU")
        from admission.api.staff import notify_pieces_recap
        res = notify_pieces_recap(dossier_id="CAN-1")
        self.assertTrue(res["ok"])
        self.assertEqual(res["data"]["rejetees"], 1)
        self.assertEqual(res["data"]["a_fournir"], 1)
        msend.assert_called_once()


class TestDownloadPiece(TestCase):
    @patch(f"{STAFF}.frappe")
    def test_v16_download_staff(self, mf):
        mf.db.exists.return_value = True
        row = _piece("identite", status="uploaded", file="FILE-1")
        app = _app([row])
        file_doc = MagicMock(); file_doc.file_name = "id.pdf"; file_doc.get_content.return_value = b"%PDF-1"
        mf.get_doc.side_effect = [app, file_doc]
        from admission.api.staff import download_piece_file
        download_piece_file(dossier_id="CAN-1", piece_code="identite")
        self.assertEqual(mf.local.response.filename, "id.pdf")
        self.assertEqual(mf.local.response.filecontent, b"%PDF-1")

    @patch(f"{STAFF}.frappe")
    def test_download_sans_fichier(self, mf):
        mf.db.exists.return_value = True
        mf.get_doc.return_value = _app([_piece("identite", status="missing", file=None)])
        from admission.api.staff import download_piece_file
        res = download_piece_file(dossier_id="CAN-1", piece_code="identite")
        self.assertEqual(res["error"]["code"], "PIECE_FILE_NOT_FOUND")
