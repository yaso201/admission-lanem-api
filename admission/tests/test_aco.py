"""Tests C1-ACO — admission conditionnelle (DEC-214) : vérification diplôme + levée.

Phase a : pièce diplôme bac (bac_attente) + champs de vérification (bac_verified/_by/_date).
Phases b/c : endpoints staff (verify_bac_diploma / lift_condition / refuse_condition /
conditional_admission) role-gardés, INV-HUMAN (levée jamais auto ; exige bac_verified).
Style unitaire mocké, aligné suite existante.
"""

import json
import os
import types
from unittest import TestCase
from unittest.mock import MagicMock, patch

STAFF = "admission.api.staff"
PUBLIC = "admission.api.public"


def _doctype():
    jf = os.path.join(os.path.dirname(__file__), "..", "admission", "doctype",
                      "admission_applicant", "admission_applicant.json")
    return json.load(open(jf))


def _patches():
    return (
        patch(f"{STAFF}._ok", side_effect=lambda d: {"ok": True, "data": d, "error": None}),
        patch(f"{STAFF}._error", side_effect=lambda c, m, s=400: {"ok": False, "data": None, "error": {"code": c}}),
    )


def _aco_app(status="ACO", conditionnel=1, diploma_status="uploaded", bac_verified=0, notes_validated=1):
    """Dossier conditionnel en ACO, avec une pièce diplome_bac au status donné (None = pièce absente)."""
    a = MagicMock()
    a.name = "CAN-2026-00001"
    a.status = status
    a.conditionnel = conditionnel
    a.bac_verified = bac_verified
    a.notes_validated = notes_validated
    a.validated_scholarships = None  # C2-BOURSES : lift lit les bourses validées pour le mail
    pieces = [types.SimpleNamespace(piece_code="releves_terminale", status="uploaded")]
    if diploma_status is not None:
        pieces.append(types.SimpleNamespace(piece_code="diplome_bac", status=diploma_status))
    a.pieces = pieces
    return a


def _run_staff(fn_name, app, is_prepa=False, user="dir@lanem.bj", **kwargs):
    """Exécute un endpoint de décision ACO avec frappe/notifs mockés. Retourne (res, mf, gen, prepa)."""
    ok, err = _patches()
    with patch(f"{STAFF}.frappe") as mf, ok, err, \
         patch(f"{STAFF}.now_datetime", return_value="2026-06-11 10:00:00"), \
         patch(f"{STAFF}._is_prepa", return_value=is_prepa), \
         patch(f"{STAFF}.send_decision_notification") as gen, \
         patch(f"{STAFF}.send_prepa_decision_notification") as prepa:
        mf.db.exists.return_value = True
        mf.get_doc.return_value = app
        mf.session.user = user
        import admission.api.staff as staff
        res = getattr(staff, fn_name)(dossier_id="CAN-2026-00001", **kwargs)
        return res, mf, gen, prepa


# ── Phase a : pièce diplôme + champs vérification ──────────────────────────

class TestDiplomePiece(TestCase):
    def test_diplome_bac_in_bac_attente(self):
        from admission.api.public import PIECES_BY_BAC_PROFILE
        codes = [p["code"] for p in PIECES_BY_BAC_PROFILE["bac_attente"]]
        self.assertIn("diplome_bac", codes, "diplome_bac doit être attendu pour bac_attente (uploadable)")

    def test_diplome_bac_not_required_at_submission(self):
        from admission.api.public import PIECES_BY_BAC_PROFILE
        piece = next(p for p in PIECES_BY_BAC_PROFILE["bac_attente"] if p["code"] == "diplome_bac")
        # Le bac-en-attente n'a pas son diplôme à la soumission : requise=False ;
        # l'exigence est portée par la gate DIPLOMA_MISSING à la vérification.
        self.assertFalse(piece["requise"])


class TestPiecesByProfile(TestCase):
    """Lot 1 — liste métier des pièces par profil (GL1-GL4 + unicité des codes)."""

    def _codes(self, profile):
        from admission.api.public import PIECES_BY_BAC_PROFILE
        return [p["code"] for p in PIECES_BY_BAC_PROFILE[profile]]

    def test_gl1_decompte_et_codes_par_profil(self):
        from admission.api.public import PIECES_BY_BAC_PROFILE
        universelles = ["identite", "photo", "cv", "motivation"]
        attente_annee = universelles + ["releves_terminale", "attestation_scolarite", "diplome_bac", "releve_bac"]
        attendu = {
            "bac_anterieur": universelles + ["diplome_bac", "releve_bac", "justificatifs_post_bac"],
            "bac_attente": attente_annee,
            "bac_annee": attente_annee,
        }
        for profile, codes in attendu.items():
            self.assertEqual(self._codes(profile), codes, f"{profile} : liste de codes inattendue")
        self.assertEqual(len(PIECES_BY_BAC_PROFILE["bac_anterieur"]), 7)
        self.assertEqual(len(PIECES_BY_BAC_PROFILE["bac_attente"]), 8)
        self.assertEqual(len(PIECES_BY_BAC_PROFILE["bac_annee"]), 8)

    def test_gl2_universelles_presentes_et_requises(self):
        from admission.api.public import PIECES_BY_BAC_PROFILE
        for profile, pieces in PIECES_BY_BAC_PROFILE.items():
            by_code = {p["code"]: p for p in pieces}
            for code in ("identite", "photo", "cv", "motivation"):
                self.assertIn(code, by_code, f"{code} manquante dans {profile}")
                self.assertTrue(by_code[code]["requise"], f"{code} doit être requise ({profile})")

    def test_gl3_diplome_releve_optionnels_attente_annee_requis_anterieur(self):
        from admission.api.public import PIECES_BY_BAC_PROFILE
        for profile in ("bac_attente", "bac_annee"):
            by_code = {p["code"]: p for p in PIECES_BY_BAC_PROFILE[profile]}
            self.assertFalse(by_code["diplome_bac"]["requise"], f"diplome_bac optionnel sur {profile}")
            self.assertFalse(by_code["releve_bac"]["requise"], f"releve_bac optionnel sur {profile}")
        ant = {p["code"]: p for p in PIECES_BY_BAC_PROFILE["bac_anterieur"]}
        self.assertTrue(ant["diplome_bac"]["requise"], "diplome_bac requis sur bac_anterieur")
        self.assertTrue(ant["releve_bac"]["requise"], "releve_bac requis sur bac_anterieur")

    def test_gl4_attente_egale_annee(self):
        from admission.api.public import PIECES_BY_BAC_PROFILE
        self.assertEqual(PIECES_BY_BAC_PROFILE["bac_attente"], PIECES_BY_BAC_PROFILE["bac_annee"],
                         "bac_attente et bac_annee doivent être la MÊME liste (factorisée)")

    def test_unicite_codes_par_profil(self):
        """Garde-fou _sync_pieces : un doublon de code dans un profil casse la child table (dédoublonnée
        par piece_code). Itère CHAQUE profil ; échoue ROUGE si doublon."""
        from admission.api.public import PIECES_BY_BAC_PROFILE
        for profile, pieces in PIECES_BY_BAC_PROFILE.items():
            codes = [p["code"] for p in pieces]
            self.assertEqual(len(codes), len(set(codes)), f"DOUBLON de code dans {profile} : {codes}")

    def test_fusion_mention_sur_justificatifs(self):
        from admission.api.public import PIECES_BY_BAC_PROFILE
        piece = next(p for p in PIECES_BY_BAC_PROFILE["bac_anterieur"] if p["code"] == "justificatifs_post_bac")
        self.assertIn("fusionner", piece["label"].lower(), "mention fusion absente du libellé justificatifs")


class TestBacVerifiedFields(TestCase):
    def setUp(self):
        self.doc = _doctype()
        self.fields = {f["fieldname"]: f for f in self.doc["fields"]}

    def test_bac_verified_check_readonly(self):
        f = self.fields.get("bac_verified")
        self.assertIsNotNone(f, "bac_verified absent")
        self.assertEqual(f["fieldtype"], "Check")
        self.assertEqual(f["read_only"], 1)

    def test_bac_verified_by_link_user_readonly(self):
        f = self.fields.get("bac_verified_by")
        self.assertIsNotNone(f, "bac_verified_by absent")
        self.assertEqual(f["fieldtype"], "Link")
        self.assertEqual(f["options"], "User")
        self.assertEqual(f["read_only"], 1)

    def test_bac_verified_date_datetime_readonly(self):
        f = self.fields.get("bac_verified_date")
        self.assertIsNotNone(f, "bac_verified_date absent")
        self.assertEqual(f["fieldtype"], "Datetime")
        self.assertEqual(f["read_only"], 1)

    def test_in_field_order(self):
        for fn in ("bac_verified", "bac_verified_by", "bac_verified_date"):
            self.assertIn(fn, self.doc["field_order"])


# ── Phase b : verify_bac_diploma (Administratif) ───────────────────────────

class TestVerifyBacDiploma(TestCase):
    def _run(self, app, user="adm@lanem.bj"):
        ok, err = _patches()
        with patch(f"{STAFF}.frappe") as mf, ok, err, \
             patch(f"{STAFF}.now_datetime", return_value="2026-06-11 10:00:00"):
            mf.db.exists.return_value = True
            mf.get_doc.return_value = app
            mf.session.user = user
            from admission.api.staff import verify_bac_diploma
            res = verify_bac_diploma(dossier_id="CAN-2026-00001")
            return res, mf

    def test_administratif_role(self):
        app = _aco_app()
        res, mf = self._run(app)
        mf.only_for.assert_called_once_with(("Admission Administratif", "System Manager"))

    def test_sets_bac_verified(self):
        app = _aco_app()
        res, mf = self._run(app)
        self.assertEqual(app.bac_verified, 1)
        self.assertEqual(app.bac_verified_by, "adm@lanem.bj")
        self.assertEqual(app.bac_verified_date, "2026-06-11 10:00:00")
        app.save.assert_called_once()
        self.assertEqual(res["data"]["bac_verified"], 1)

    def test_does_not_transition(self):
        app = _aco_app()
        self._run(app)
        self.assertEqual(app.status, "ACO")  # la levée est l'étape c (Direction), pas ici

    def test_requires_aco_state(self):
        app = _aco_app(status="ETU")
        res, mf = self._run(app)
        self.assertEqual(res["error"]["code"], "INVALID_STATE")
        app.save.assert_not_called()

    def test_requires_conditional(self):
        app = _aco_app(conditionnel=0)
        res, mf = self._run(app)
        self.assertEqual(res["error"]["code"], "NOT_CONDITIONAL")
        app.save.assert_not_called()

    def test_diploma_missing_when_absent(self):
        app = _aco_app(diploma_status=None)  # pas de pièce diplome_bac du tout
        res, mf = self._run(app)
        self.assertEqual(res["error"]["code"], "DIPLOMA_MISSING")
        app.save.assert_not_called()

    def test_diploma_missing_when_not_uploaded(self):
        app = _aco_app(diploma_status="missing")  # pièce présente mais non déposée
        res, mf = self._run(app)
        self.assertEqual(res["error"]["code"], "DIPLOMA_MISSING")
        self.assertEqual(app.bac_verified, 0)
        app.save.assert_not_called()

    def test_invalid_dossier(self):
        ok, err = _patches()
        with patch(f"{STAFF}.frappe") as mf, ok, err:
            mf.db.exists.return_value = False
            from admission.api.staff import verify_bac_diploma
            res = verify_bac_diploma(dossier_id="CAN-UNKNOWN")
        self.assertEqual(res["error"]["code"], "INVALID_DOSSIER")


# ── Phase c : conditional_admission / lift_condition / refuse_condition ─────

class TestConditionalAdmission(TestCase):
    def test_responsable_etu_to_aco_licence(self):
        app = _aco_app(status="ETU")
        res, mf, gen, prepa = _run_staff("conditional_admission", app, is_prepa=False, user="resp@lanem.bj")
        mf.only_for.assert_called_once_with(("Admission Responsable", "System Manager"))
        self.assertEqual(app.status, "ACO")
        self.assertEqual(app.decided_by, "resp@lanem.bj")   # stamp
        self.assertEqual(app.decision_date, "2026-06-11 10:00:00")
        app.save.assert_called_once()
        gen.assert_called_once()                            # mail générique « admission conditionnelle »
        self.assertEqual(gen.call_args[0][1], "admission conditionnelle")
        prepa.assert_not_called()

    def test_requires_etu(self):
        app = _aco_app(status="SOU")
        res, mf, gen, prepa = _run_staff("conditional_admission", app)
        self.assertEqual(res["error"]["code"], "INVALID_STATE")
        app.save.assert_not_called()

    def test_requires_conditional(self):
        app = _aco_app(status="ETU", conditionnel=0)
        res, mf, gen, prepa = _run_staff("conditional_admission", app)
        self.assertEqual(res["error"]["code"], "NOT_CONDITIONAL")
        app.save.assert_not_called()

    def test_prepa_requires_validated_notes(self):
        app = _aco_app(status="ETU", notes_validated=0)
        res, mf, gen, prepa = _run_staff("conditional_admission", app, is_prepa=True)
        self.assertEqual(res["error"]["code"], "NOTES_NOT_VALIDATED")  # cohérence C1-CONCOURS
        app.save.assert_not_called()

    def test_prepa_validated_uses_prepa_mail(self):
        app = _aco_app(status="ETU", notes_validated=1)
        res, mf, gen, prepa = _run_staff("conditional_admission", app, is_prepa=True)
        self.assertEqual(app.status, "ACO")
        prepa.assert_called_once()      # Prépa entre en ACO avec notes validées → mail Prépa
        gen.assert_not_called()         # jamais les deux


class TestLiftCondition(TestCase):
    def test_direction_aco_to_acc_via_save(self):
        app = _aco_app(status="ACO", bac_verified=1)
        res, mf, gen, prepa = _run_staff("lift_condition", app)
        mf.only_for.assert_called_once_with(("Admission Direction", "System Manager"))
        self.assertEqual(app.status, "ACC")
        # IMPÉRATIF : via save() (le contrôleur) → _on_accepted (frais 2) ; PAS de court-circuit
        app.save.assert_called_once_with(ignore_permissions=True)
        mf.db.set_value.assert_not_called()
        gen.assert_called_once()
        self.assertEqual(gen.call_args[0][1], "admission acceptée")

    def test_requires_bac_verified(self):
        app = _aco_app(status="ACO", bac_verified=0)
        res, mf, gen, prepa = _run_staff("lift_condition", app)
        self.assertEqual(res["error"]["code"], "BAC_NOT_VERIFIED")  # INV-HUMAN : pas de levée sans vérif
        app.save.assert_not_called()
        gen.assert_not_called()

    def test_requires_aco(self):
        app = _aco_app(status="ADM", bac_verified=1)
        res, mf, gen, prepa = _run_staff("lift_condition", app)
        self.assertEqual(res["error"]["code"], "INVALID_STATE")
        app.save.assert_not_called()


class TestRefuseCondition(TestCase):
    def test_direction_aco_to_ref_with_motif(self):
        app = _aco_app(status="ACO")
        res, mf, gen, prepa = _run_staff("refuse_condition", app, motif="Bac non obtenu")
        mf.only_for.assert_called_once_with(("Admission Direction", "System Manager"))
        self.assertEqual(app.status, "REF")
        self.assertEqual(app.motif_refus, "Bac non obtenu")
        self.assertEqual(app.decided_by, "dir@lanem.bj")   # stamp (miroir refuse)
        app.save.assert_called_once()
        self.assertEqual(gen.call_args[0][1], "refusé")
        self.assertEqual(gen.call_args.kwargs.get("motif"), "Bac non obtenu")  # le candidat saura pourquoi

    def test_motif_required(self):
        app = _aco_app(status="ACO")
        res, mf, gen, prepa = _run_staff("refuse_condition", app, motif="   ")
        self.assertEqual(res["error"]["code"], "MOTIF_REQUIRED")
        app.save.assert_not_called()

    def test_requires_aco(self):
        app = _aco_app(status="ETU")
        res, mf, gen, prepa = _run_staff("refuse_condition", app, motif="x")
        self.assertEqual(res["error"]["code"], "INVALID_STATE")
        app.save.assert_not_called()
