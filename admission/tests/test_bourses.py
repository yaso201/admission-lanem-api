"""Tests C2-BOURSES — proposition (Responsable) / validation Direction à l'ACC / notification / capture R1.

Phase a : champs doctype (proposed_scholarships + stamps proposition/validation read-only).
Phase b : helpers ARGENT (_parse_scholarship_keys, _check_scholarship_selection).
Phase c : propose_scholarships (Responsable, ETU/ATT, ⊆ requested + existence miroir — l'exclusivité
          n'est PAS tranchée à la proposition, ruling R3).
Phase d : accept_admission / lift_condition avec bourses_validees (R2 : geste atomique Direction,
          UN SEUL save bourses+ACC → _on_accepted frais 2 préservé ; R3 : EXCLUSIVITY_CONFLICT).
Phase e : notification bourses AVEC la décision (R4 : noms + taux + disclaimer, JAMAIS de montants).
Phase f : capture promo DANS la cascade partagée (R1/DEC-228 : frais 1 only, jamais frais 2).
Style unitaire mocké, aligné suite existante (test_etude/test_aco).
"""

import json
import os
import types
from unittest import TestCase
from unittest.mock import MagicMock, patch
from admission.api.permissions import roles_at_or_above  # FIX-ROLES-HIERARCHIE : source unique de l'ordre

STAFF = "admission.api.staff"
PUBLIC = "admission.api.public"


def _row(**kw):
    return types.SimpleNamespace(**kw)


# Miroir UF répliqué (test) : 2 bourses exclusives (groupe EXC) + 1 additive sans groupe.
MIRROR_ROWS = [
    _row(mirror_key="B-EXC-A", scholarship_name="Bourse Excellence A", rate=0.30, exclusivity_group="EXC"),
    _row(mirror_key="B-EXC-B", scholarship_name="Bourse Excellence B", rate=0.20, exclusivity_group="EXC"),
    _row(mirror_key="B-MERITE", scholarship_name="Bourse Mérite", rate=0.15, exclusivity_group=""),
]


def _rows_for(keys):
    return [r for r in MIRROR_ROWS if r.mirror_key in (keys or [])]


def _patches():
    return (
        patch(f"{STAFF}._ok", side_effect=lambda d: {"ok": True, "data": d, "error": None}),
        patch(f"{STAFF}._error", side_effect=lambda c, m, s=400: {"ok": False, "data": None, "error": {"code": c}}),
    )


def _app(status="ETU", requested='["B-EXC-A", "B-EXC-B", "B-MERITE"]'):
    a = MagicMock()
    a.name = "CAN-2026-00001"
    a.status = status
    a.requested_scholarships = requested
    a.validated_scholarships = None
    return a


# ── Phase a : champs doctype ─────────────────────────────────────────────────


class TestBoursesFields(TestCase):
    def setUp(self):
        jf = os.path.join(os.path.dirname(__file__), "..", "admission", "doctype",
                          "admission_applicant", "admission_applicant.json")
        doc = json.load(open(jf))
        self.fields = {f["fieldname"]: f for f in doc["fields"]}
        self.order = doc["field_order"]

    def test_proposed_scholarships_json(self):
        f = self.fields.get("proposed_scholarships")
        self.assertIsNotNone(f, "proposed_scholarships absent")
        self.assertEqual(f["fieldtype"], "JSON")
        self.assertIn("proposed_scholarships", self.order)

    def test_stamps_read_only(self):
        # Trace non falsifiable (pattern maison decided_by / notes_validated_by / bac_verified_by)
        for name, ftype in (
            ("scholarships_proposed_by", "Link"),
            ("scholarships_proposed_date", "Datetime"),
            ("scholarships_validated_by", "Link"),
            ("scholarships_validated_date", "Datetime"),
        ):
            f = self.fields.get(name)
            self.assertIsNotNone(f, f"{name} absent")
            self.assertEqual(f["fieldtype"], ftype, name)
            self.assertEqual(f.get("read_only"), 1, f"{name} doit être read-only")


# ── Phase b : helpers (gardes ARGENT) ────────────────────────────────────────


class TestParseScholarshipKeys(TestCase):
    def _parse(self, bourses):
        from admission.api.staff import _parse_scholarship_keys
        return _parse_scholarship_keys(bourses)

    def test_list_ok_and_trimmed(self):
        keys, err = self._parse(["B-A", " B-B "])
        self.assertIsNone(err)
        self.assertEqual(keys, ["B-A", "B-B"])

    def test_json_string_ok(self):
        keys, err = self._parse('["B-A", "B-B"]')
        self.assertIsNone(err)
        self.assertEqual(keys, ["B-A", "B-B"])

    def test_dedup(self):
        keys, err = self._parse(["B-A", "B-A", "B-B"])
        self.assertIsNone(err)
        self.assertEqual(keys, ["B-A", "B-B"])

    def test_empty_list_ok(self):
        keys, err = self._parse([])
        self.assertIsNone(err)
        self.assertEqual(keys, [])

    def test_not_a_list_rejected(self):
        for bad in ('{"a": 1}', "pas-du-json", 42, {"a": 1}):
            keys, err = self._parse(bad)
            self.assertIsNone(keys, f"{bad!r} aurait dû être rejeté")
            self.assertIsNotNone(err)

    def test_invalid_key_rejected(self):
        for bad in ([""], ["  "], [1], [None]):
            keys, err = self._parse(bad)
            self.assertIsNone(keys, f"{bad!r} aurait dû être rejeté")


class TestCheckScholarshipSelection(TestCase):
    def _run(self, keys, requested='["B-EXC-A", "B-EXC-B", "B-MERITE"]', enforce=True, rows=None):
        app = _app(requested=requested)
        ok, err = _patches()
        with patch(f"{STAFF}.frappe") as mf, ok, err:
            mf.get_all.return_value = _rows_for(keys) if rows is None else rows
            from admission.api.staff import _check_scholarship_selection
            return _check_scholarship_selection(app, keys, enforce_exclusivity=enforce)

    def test_valid_subset_passes(self):
        self.assertIsNone(self._run(["B-MERITE"]))

    def test_empty_selection_passes(self):
        self.assertIsNone(self._run([]))

    def test_not_requested_rejected(self):
        res = self._run(["B-FANTOME"])
        self.assertEqual(res["error"]["code"], "SCHOLARSHIP_NOT_REQUESTED")

    def test_unknown_in_mirror_rejected(self):
        # Demandée au dépôt mais disparue du miroir UF depuis → refus explicite
        res = self._run(["B-EXC-A"], rows=[])
        self.assertEqual(res["error"]["code"], "SCHOLARSHIP_UNKNOWN")

    def test_exclusivity_conflict_rejected(self):
        # R3 : 2 bourses du même groupe d'exclusivité → 409 (ce qui est validé est notifié)
        res = self._run(["B-EXC-A", "B-EXC-B"], enforce=True)
        self.assertEqual(res["error"]["code"], "EXCLUSIVITY_CONFLICT")

    def test_exclusivity_not_enforced_at_proposal(self):
        # R3 : l'exclusivité n'est tranchée qu'à la VALIDATION (Direction)
        self.assertIsNone(self._run(["B-EXC-A", "B-EXC-B"], enforce=False))

    def test_exclusive_plus_additive_passes(self):
        self.assertIsNone(self._run(["B-EXC-A", "B-MERITE"], enforce=True))


# ── Phase c : propose_scholarships (Responsable) ─────────────────────────────


class TestProposeScholarships(TestCase):
    def _run(self, status="ETU", bourses=None, requested='["B-EXC-A", "B-EXC-B", "B-MERITE"]'):
        app = _app(status=status, requested=requested)
        ok, err = _patches()
        with patch(f"{STAFF}.frappe") as mf, ok, err, \
             patch(f"{STAFF}.now_datetime", return_value="2026-06-11 10:00:00"):
            mf.db.exists.return_value = True
            mf.get_doc.return_value = app
            mf.get_all.return_value = _rows_for(bourses if isinstance(bourses, list) else [])
            mf.session.user = "resp@lanem.bj"
            from admission.api.staff import propose_scholarships
            res = propose_scholarships(dossier_id="CAN-2026-00001", bourses=bourses)
            return app, res, mf

    def test_responsable_proposes_in_etu(self):
        app, res, mf = self._run(bourses=["B-EXC-A", "B-MERITE"])
        mf.only_for.assert_called_once_with(roles_at_or_above("Admission Responsable"))
        self.assertEqual(json.loads(app.proposed_scholarships), ["B-EXC-A", "B-MERITE"])
        self.assertEqual(app.scholarships_proposed_by, "resp@lanem.bj")
        self.assertEqual(app.scholarships_proposed_date, "2026-06-11 10:00:00")
        app.save.assert_called_once_with(ignore_permissions=True)
        self.assertEqual(res["data"]["proposed_scholarships"], ["B-EXC-A", "B-MERITE"])

    def test_att_allowed(self):
        app, res, mf = self._run(status="ATT", bourses=["B-MERITE"])
        app.save.assert_called_once()

    def test_invalid_state_rejected(self):
        app, res, mf = self._run(status="ADM", bourses=["B-MERITE"])
        self.assertEqual(res["error"]["code"], "INVALID_STATE")
        app.save.assert_not_called()

    def test_not_requested_rejected(self):
        app, res, mf = self._run(bourses=["B-EXC-A"], requested='["B-MERITE"]')
        self.assertEqual(res["error"]["code"], "SCHOLARSHIP_NOT_REQUESTED")
        app.save.assert_not_called()

    def test_exclusive_pair_allowed_at_proposal(self):
        # R3 : la proposition peut contenir 2 exclusives — la Direction tranchera
        app, res, mf = self._run(bourses=["B-EXC-A", "B-EXC-B"])
        app.save.assert_called_once()

    def test_format_invalid_rejected(self):
        app, res, mf = self._run(bourses="pas-une-liste")
        self.assertEqual(res["error"]["code"], "BOURSES_FORMAT_INVALID")
        app.save.assert_not_called()


# ── Phase d : validation Direction atomique (R2) ─────────────────────────────


class TestAcceptAdmissionWithBourses(TestCase):
    def _run(self, bourses_validees, status="ADM", requested='["B-EXC-A", "B-EXC-B", "B-MERITE"]'):
        app = _app(status=status, requested=requested)
        ok, err = _patches()
        with patch(f"{STAFF}.frappe") as mf, ok, err, \
             patch(f"{STAFF}.now_datetime", return_value="2026-06-11 10:00:00"), \
             patch(f"{STAFF}.send_decision_notification") as gen:
            mf.db.exists.return_value = True
            mf.get_doc.return_value = app
            mf.get_all.return_value = _rows_for(bourses_validees if isinstance(bourses_validees, list) else [])
            mf.session.user = "dir@lanem.bj"
            from admission.api.staff import accept_admission
            res = accept_admission(dossier_id="CAN-2026-00001", bourses_validees=bourses_validees)
            return app, res, mf, gen

    def test_atomic_validation_with_acc(self):
        app, res, mf, gen = self._run(["B-EXC-A", "B-MERITE"])
        mf.only_for.assert_called_once_with(roles_at_or_above("Admission Direction"))
        self.assertEqual(app.status, "ACC")
        self.assertEqual(json.loads(app.validated_scholarships), ["B-EXC-A", "B-MERITE"])
        self.assertEqual(app.scholarships_validated_by, "dir@lanem.bj")
        self.assertEqual(app.scholarships_validated_date, "2026-06-11 10:00:00")
        # R2 IMPÉRATIF : UN SEUL save (bourses + ACC atomiques) via le contrôleur → _on_accepted (frais 2)
        app.save.assert_called_once_with(ignore_permissions=True)
        mf.db.set_value.assert_not_called()
        self.assertEqual(res["data"]["validated_scholarships"], ["B-EXC-A", "B-MERITE"])

    def test_exclusivity_conflict_blocks_everything(self):
        # R3 : sélection incohérente → AUCUNE transition, AUCUNE écriture, AUCUN mail
        app, res, mf, gen = self._run(["B-EXC-A", "B-EXC-B"])
        self.assertEqual(res["error"]["code"], "EXCLUSIVITY_CONFLICT")
        self.assertEqual(app.status, "ADM")
        app.save.assert_not_called()
        gen.assert_not_called()

    def test_not_requested_blocks(self):
        app, res, mf, gen = self._run(["B-EXC-A"], requested='["B-MERITE"]')
        self.assertEqual(res["error"]["code"], "SCHOLARSHIP_NOT_REQUESTED")
        app.save.assert_not_called()

    def test_backward_compatible_without_param(self):
        # bourses_validees omis → comportement C1-ETUDE strictement inchangé
        app = _app(status="ADM")
        ok, err = _patches()
        with patch(f"{STAFF}.frappe") as mf, ok, err, patch(f"{STAFF}.send_decision_notification") as gen:
            mf.db.exists.return_value = True
            mf.get_doc.return_value = app
            from admission.api.staff import accept_admission
            res = accept_admission(dossier_id="CAN-2026-00001")
        self.assertEqual(app.status, "ACC")
        app.save.assert_called_once_with(ignore_permissions=True)
        self.assertEqual(res["data"]["validated_scholarships"], [])
        gen.assert_called_once()

    def test_notification_includes_bourses(self):
        # D11 §6.3 : la bourse est notifiée AVEC la décision — noms + taux du miroir UF
        app, res, mf, gen = self._run(["B-MERITE"])
        gen.assert_called_once()
        self.assertEqual(gen.call_args[0][1], "admission acceptée")
        self.assertEqual(gen.call_args.kwargs.get("bourses"),
                         [{"scholarship_name": "Bourse Mérite", "rate": 0.15}])

    def test_empty_list_validates_none(self):
        # Direction peut explicitement ne RIEN valider : validated = [] (différent d'omis)
        app, res, mf, gen = self._run([])
        self.assertEqual(app.status, "ACC")
        self.assertEqual(json.loads(app.validated_scholarships), [])
        self.assertEqual(gen.call_args.kwargs.get("bourses"), [])


class TestLiftConditionWithBourses(TestCase):
    def _run(self, bourses_validees, bac_verified=1):
        app = _app(status="ACO")
        app.bac_verified = bac_verified
        ok, err = _patches()
        with patch(f"{STAFF}.frappe") as mf, ok, err, \
             patch(f"{STAFF}.now_datetime", return_value="2026-06-11 10:00:00"), \
             patch(f"{STAFF}.send_decision_notification") as gen:
            mf.db.exists.return_value = True
            mf.get_doc.return_value = app
            mf.get_all.return_value = _rows_for(bourses_validees if isinstance(bourses_validees, list) else [])
            mf.session.user = "dir@lanem.bj"
            from admission.api.staff import lift_condition
            res = lift_condition(dossier_id="CAN-2026-00001", bourses_validees=bourses_validees)
            return app, res, mf, gen

    def test_atomic_validation_on_lift(self):
        # La branche conditionnelle (ACO→ACC) porte le MÊME geste de validation Direction
        app, res, mf, gen = self._run(["B-MERITE"])
        self.assertEqual(app.status, "ACC")
        self.assertEqual(json.loads(app.validated_scholarships), ["B-MERITE"])
        app.save.assert_called_once_with(ignore_permissions=True)
        self.assertEqual(gen.call_args.kwargs.get("bourses"),
                         [{"scholarship_name": "Bourse Mérite", "rate": 0.15}])

    def test_exclusivity_conflict_blocks(self):
        app, res, mf, gen = self._run(["B-EXC-A", "B-EXC-B"])
        self.assertEqual(res["error"]["code"], "EXCLUSIVITY_CONFLICT")
        self.assertEqual(app.status, "ACO")
        app.save.assert_not_called()

    def test_inv_human_preserved(self):
        # INV-HUMAN (C1-ACO) : bac non vérifié → AUCUNE levée, même avec des bourses
        app, res, mf, gen = self._run(["B-MERITE"], bac_verified=0)
        self.assertEqual(res["error"]["code"], "BAC_NOT_VERIFIED")
        app.save.assert_not_called()


# ── Phase e : notification (R4 : taux, jamais de montants) ───────────────────


class TestBoursesMail(TestCase):
    """LOT M : le corps passe par email_template.render_candidate_email ; les invariants
    R4 (taux jamais montants, échappement, disclaimer) sont vérifiés sur le HTML rendu."""

    def _applicant(self):
        from types import SimpleNamespace
        return SimpleNamespace(name="CAN-2026-00001", applicant_name="Ama Doe",
                               programme_label="Licence Informatique")

    def test_bourses_block_names_rates_disclaimer(self):
        from admission.api.notifications import _decision_kwargs
        from admission.api.email_template import render_candidate_email
        kw, _ = _decision_kwargs(self._applicant(), "admission acceptée",
                                 bourses=[{"scholarship_name": "Bourse <Excellence>", "rate": 0.25},
                                          {"scholarship_name": "Mérite", "rate": 0.125}])
        html = render_candidate_email(**kw)
        self.assertIn("Bourse &lt;Excellence&gt;", html)  # échappement anti-injection (DAT)
        self.assertIn("25", html)
        self.assertIn("12,5", html)         # taux format FR (virgule)
        self.assertIn("sera calcul", html)  # disclaimer R4 « montant final calculé à l'inscription »
        self.assertNotIn("XOF", html)       # JAMAIS de montants engageants (DEC-206 : UF calcule)

    def test_empty_no_section(self):
        from admission.api.email_template import _bourses_table
        self.assertEqual(_bourses_table([]), "")
        self.assertEqual(_bourses_table(None), "")

    def test_decision_kwargs_includes_bourses(self):
        from admission.api.notifications import _decision_kwargs
        kw, _ = _decision_kwargs(self._applicant(), "admission acceptée",
                                 bourses=[{"scholarship_name": "Bourse Mérite", "rate": 0.15}])
        self.assertEqual(kw["bourses"], [("Bourse Mérite", "15")])
        self.assertEqual(kw["status"], "accepte")

    def test_decision_kwargs_without_bourses_unchanged(self):
        from admission.api.notifications import _decision_kwargs
        from admission.api.email_template import render_candidate_email
        kw, _ = _decision_kwargs(self._applicant(), "refusé", motif="Niveau insuffisant")
        self.assertNotIn("bourses", kw)
        html = render_candidate_email(**kw)
        self.assertNotIn("Bourses", html)
        self.assertIn("Niveau insuffisant", html)

    def test_format_rate_percent(self):
        from admission.api.notifications import _format_rate_percent
        self.assertEqual(_format_rate_percent(0.25), "25")
        self.assertEqual(_format_rate_percent(0.125), "12,5")
        self.assertEqual(_format_rate_percent("invalide"), "0")


# ── Phase f : capture promo dans la cascade (R1/DEC-228) ─────────────────────


class TestCascadePromoCapture(TestCase):
    """R1 : la promo est figée à la CONFIRMATION du frais 1, par la cascade PARTAGÉE
    (confirm offline staff + webhook online) — jamais au frais 2, jamais sans fee."""

    def _fee(self, fee_type):
        f = MagicMock()
        f.fee_type = fee_type
        f.status = "Pending"
        return f

    @patch(f"{PUBLIC}._capture_promo_if_eligible")
    def test_frais1_application_captures(self, cap):
        from admission.api.public import apply_confirmed_payment_cascade
        app = MagicMock(); app.status = "SOP"
        apply_confirmed_payment_cascade(app, self._fee("application"))
        cap.assert_called_once_with(app)

    @patch(f"{PUBLIC}._capture_promo_if_eligible")
    def test_frais1_competition_captures(self, cap):
        # Prépa : le frais 1 est de type competition (_resolve_frais1_fee_type)
        from admission.api.public import apply_confirmed_payment_cascade
        app = MagicMock(); app.status = "SOP"
        apply_confirmed_payment_cascade(app, self._fee("competition"))
        cap.assert_called_once_with(app)

    @patch(f"{PUBLIC}._capture_promo_if_eligible")
    def test_frais2_enrollment_never_captures(self, cap):
        # DEC-228 : une promo ouverte APRÈS le frais 1 ne doit JAMAIS être figée au frais 2
        from admission.api.public import apply_confirmed_payment_cascade
        app = MagicMock(); app.status = "ACC"
        apply_confirmed_payment_cascade(app, self._fee("enrollment"))
        cap.assert_not_called()

    @patch(f"{PUBLIC}._capture_promo_if_eligible")
    def test_no_fee_no_capture(self, cap):
        from admission.api.public import apply_confirmed_payment_cascade
        app = MagicMock(); app.status = "SOP"
        apply_confirmed_payment_cascade(app, None)
        cap.assert_not_called()
