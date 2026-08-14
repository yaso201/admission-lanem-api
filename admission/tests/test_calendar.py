"""GESTION-CALENDRIER — cycle de vie 3 états (Draft/Open/Closed), miroir is_open, invisibilité
brouillon, auto-fermeture qui ignore les brouillons, catalogue candidat.

DB réelle (style test_convocation / test_exam_documents). _MARK = ZZCAL.
Étape 1 du lot : état + miroir + non-régression des consommateurs.
"""

from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_days, now_datetime, nowdate

from admission.api.sessions import (
    is_session_selectable,
    session_display_status,
    close_expired_sessions,
)
from admission.api.calendar_rules import evaluate_change, enforce_document_change, field_policies

_MARK = "ZZCAL"
_PROG = "ZZCALPROG"   # programme isolé pour le catalogue candidat


def _purge():
    apps = frappe.get_all("Admission Applicant", filters={"applicant_name": ["like", f"{_MARK}%"]}, pluck="name")
    for a in apps:
        frappe.db.delete("Applicant Fee Payment", {"applicant": a})
        frappe.db.delete("Applicant Fee", {"applicant": a})
    if apps:
        frappe.db.delete("Admission Applicant", {"name": ["in", apps]})
    # V-LEARN-PURGE-14 : db.delete du parent NE cascade PAS la table enfant → purger explicitement
    # les lignes pending_changes (sinon orphelins → pending_queue tombe).
    frappe.db.delete("Admission Session Pending Change", {"parent": ["like", f"{_MARK}%"]})
    frappe.db.delete("Admission Session", {"session_code": ["like", f"{_MARK}%"]})
    frappe.db.commit()


class TestCalendarState(FrappeTestCase):
    def setUp(self):
        _purge()

    def tearDown(self):
        frappe.db.rollback()
        _purge()

    def _session(self, suffix, *, state=None, is_open=None, closes_on=None, programme=_PROG):
        vals = {
            "doctype": "Admission Session",
            "session_code": f"{_MARK}-{suffix}",
            "label": f"Session {suffix}",
            "programme_code": programme,
            "programme_label": "Cycle test",
            "academic_year": "2026-2027",
            "opens_on": "2026-06-01",
            "closes_on": closes_on or "2026-12-01",
            "bac_results_date": "2027-01-15",
            "application_fee_xof": 10000,
            # heures réelles : sinon les Time non renseignés valent nowtime() (défaut Frappe) →
            # faux positif de cohérence dès qu'un exam_date est posé sur la session.
            "exam_call_time": "07:30:00", "exam_start_time": "08:00:00",
        }
        if state is not None:
            vals["lifecycle_state"] = state
        if is_open is not None:
            vals["is_open"] = is_open
        return frappe.get_doc(vals).insert(ignore_permissions=True, ignore_mandatory=True)

    # ── Miroir : lifecycle_state est la source, is_open en dérive ────────────────
    def test_draft_forces_is_open_zero(self):
        s = self._session("D", state="Draft")
        self.assertEqual(s.is_open, 0)
        self.assertEqual(frappe.db.get_value("Admission Session", s.name, "is_open"), 0)

    def test_open_state_forces_is_open_one(self):
        s = self._session("O", state="Open")
        self.assertEqual(s.is_open, 1)

    def test_legacy_is_open_derives_state(self):
        # insert historique : is_open posé, lifecycle_state absent → dérivé
        s = self._session("L", is_open=1)
        self.assertEqual(s.lifecycle_state, "Open")
        s2 = self._session("L0", is_open=0)
        self.assertEqual(s2.lifecycle_state, "Closed")

    # ── Sélectionnabilité par état ──────────────────────────────────────────────
    def test_selectable_by_state(self):
        future = add_days(nowdate(), 30)
        past = add_days(nowdate(), -1)
        self.assertFalse(is_session_selectable(self._session("SD", state="Draft", closes_on=future)))
        self.assertTrue(is_session_selectable(self._session("SO", state="Open", closes_on=future)))
        self.assertFalse(is_session_selectable(self._session("SE", state="Open", closes_on=past)))   # échue
        self.assertFalse(is_session_selectable(self._session("SC", state="Closed", closes_on=future)))

    # ── Statut d'affichage : le brouillon a son propre statut (masqué candidat) ──
    def test_display_status(self):
        future = add_days(nowdate(), 30)
        past = add_days(nowdate(), -1)
        self.assertEqual(session_display_status(self._session("PD", state="Draft", closes_on=future)), "brouillon")
        self.assertEqual(session_display_status(self._session("PO", state="Open", closes_on=future)), "a_venir")
        self.assertEqual(session_display_status(self._session("PE", state="Open", closes_on=past)), "echue")
        self.assertEqual(session_display_status(self._session("PC", state="Closed", closes_on=future)), "fermee")

    # ── GK2 : l'auto-fermeture ignore les brouillons ────────────────────────────
    def test_auto_close_ignores_draft(self):
        past = add_days(nowdate(), -1)
        draft = self._session("AD", state="Draft", closes_on=past)
        opened = self._session("AO", state="Open", closes_on=past)
        close_expired_sessions()
        self.assertEqual(frappe.db.get_value("Admission Session", draft.name, "lifecycle_state"), "Draft")
        self.assertEqual(frappe.db.get_value("Admission Session", opened.name, "lifecycle_state"), "Closed")
        self.assertEqual(frappe.db.get_value("Admission Session", opened.name, "is_open"), 0)

    # ── GK2 : le catalogue candidat exclut le brouillon ─────────────────────────
    def test_candidate_catalogue_excludes_draft(self):
        from admission.api.public import list_sessions
        self._session("CD", state="Draft", closes_on=add_days(nowdate(), 30))
        self._session("CO", state="Open", closes_on=add_days(nowdate(), 30))
        frappe.db.commit()
        resp = list_sessions(programme=_PROG)
        ids = [s["id"] for s in resp["data"]["sessions"]]
        self.assertIn(f"{_MARK}-CO", ids)
        self.assertNotIn(f"{_MARK}-CD", ids)

    # ── GK5 (niveau contrôleur) : un save qui AVANCE l'épreuve d'une Open est refusé,
    #    un save qui la REPORTE passe — la règle vit dans validate(), pas que dans l'endpoint ─
    def test_validate_blocks_direct_advance_allows_postpone(self):
        s = self._session("V", state="Open", closes_on=add_days(nowdate(), 30))
        frappe.db.set_value("Admission Session", s.name, "exam_date", "2026-09-20", update_modified=False)
        doc = frappe.get_doc("Admission Session", s.name)
        doc.exam_date = "2026-09-10"   # avance → refusé par validate()
        with self.assertRaises(frappe.ValidationError):
            doc.save(ignore_permissions=True)
        doc = frappe.get_doc("Admission Session", s.name)
        doc.exam_date = "2026-09-30"   # report → accepté
        doc.save(ignore_permissions=True)
        self.assertEqual(str(frappe.db.get_value("Admission Session", s.name, "exam_date")), "2026-09-30")


class TestCalendarRules(FrappeTestCase):
    """Étape 2 — SOURCE UNIQUE des règles (§4 : élargir permis, restreindre interdit).
    Fonction pure evaluate_change(state, field, old, new) → verdict."""

    D1, D2 = "2026-09-10", "2026-09-20"   # D2 > D1 (report/prolongation) ; D1 < D2 (avancer)

    # ── Brouillon : tout libre ──────────────────────────────────────────────────
    def test_draft_all_free(self):
        cases = [("closes_on", "2026-09-10", "2026-09-20"), ("exam_date", "2026-09-11", "2026-09-01"),
                 ("opens_on", "2026-06-01", "2026-06-15"), ("application_fee_xof", "10000", "12000"),
                 ("programme_code", "PREPA", "LIC")]
        for field, old, new in cases:
            v = evaluate_change("Draft", field, old, new)
            self.assertTrue(v["allowed"], field)
            self.assertFalse(v["requires_validation"], field)
            self.assertFalse(v["triggers_reissue"], field)

    # ── GK5 : prolonger clôture OK (validation) · avancer REFUSÉ ─────────────────
    def test_open_closes_extend_ok_validation(self):
        v = evaluate_change("Open", "closes_on", self.D1, self.D2)   # prolonge
        self.assertTrue(v["allowed"])
        self.assertTrue(v["requires_validation"])
        self.assertFalse(v["triggers_reissue"])

    def test_open_closes_advance_refused(self):
        v = evaluate_change("Open", "closes_on", self.D2, self.D1)   # avance
        self.assertFalse(v["allowed"])

    # ── GK6 : reporter épreuve OK (validation + réémission) · avancer REFUSÉ ─────
    def test_open_exam_postpone_ok_reissue(self):
        v = evaluate_change("Open", "exam_date", self.D1, self.D2)
        self.assertTrue(v["allowed"])
        self.assertTrue(v["requires_validation"])
        self.assertTrue(v["triggers_reissue"])

    def test_open_exam_advance_refused(self):
        v = evaluate_change("Open", "exam_date", self.D2, self.D1)
        self.assertFalse(v["allowed"])

    def test_open_exam_time_and_room_reissue(self):
        for field, old, new in (("exam_call_time", "07:30:00", "08:00:00"),
                                 ("exam_start_time", "08:00:00", "08:30:00"),
                                 ("exam_room", "Salle A", "Salle B")):
            v = evaluate_change("Open", field, old, new)
            self.assertTrue(v["allowed"], field)
            self.assertTrue(v["requires_validation"], field)
            self.assertTrue(v["triggers_reissue"], field)

    # ── GK7 : résultats du bac = libre, sans validation ─────────────────────────
    def test_open_bac_results_free(self):
        v = evaluate_change("Open", "bac_results_date", self.D1, self.D2)
        self.assertTrue(v["allowed"])
        self.assertFalse(v["requires_validation"])
        self.assertFalse(v["triggers_reissue"])

    # ── §4 : structure & ouverture figées sur session publiée ───────────────────
    def test_open_structure_locked(self):
        cases = [("opens_on", "2026-06-01", "2026-06-15"), ("application_fee_xof", "10000", "12000"),
                 ("programme_code", "PREPA", "LIC"), ("label", "Session A", "Session B")]
        for field, old, new in cases:
            v = evaluate_change("Open", field, old, new)
            self.assertFalse(v["allowed"], field)

    # ── Point 2 ratifié : session Fermée = mêmes règles épreuve (convoqués existent) ─
    def test_closed_exam_postpone_reissue(self):
        v = evaluate_change("Closed", "exam_date", self.D1, self.D2)
        self.assertTrue(v["allowed"])
        self.assertTrue(v["requires_validation"])
        self.assertTrue(v["triggers_reissue"])

    def test_closed_closes_locked(self):
        v = evaluate_change("Closed", "closes_on", self.D1, self.D2)
        self.assertFalse(v["allowed"])

    def test_closed_bac_free(self):
        v = evaluate_change("Closed", "bac_results_date", self.D1, self.D2)
        self.assertTrue(v["allowed"])
        self.assertFalse(v["requires_validation"])

    # ── no-op : une non-modification est toujours acceptée, sans effet ──────────
    def test_noop_allowed_no_side_effect(self):
        v = evaluate_change("Open", "exam_date", self.D1, self.D1)
        self.assertTrue(v["allowed"])
        self.assertFalse(v["requires_validation"])
        self.assertFalse(v["triggers_reissue"])

    # ── Défense-en-profondeur : validate() refuse un avancer sur appel direct ───
    def test_enforce_blocks_advance_exam(self):
        before = frappe._dict(lifecycle_state="Open", is_open=1, exam_date=self.D2)
        after = frappe._dict(lifecycle_state="Open", is_open=1, exam_date=self.D1)   # avance
        with self.assertRaises(frappe.ValidationError):
            enforce_document_change(before, after)

    def test_enforce_allows_postpone_exam(self):
        before = frappe._dict(lifecycle_state="Open", is_open=1, exam_date=self.D1)
        after = frappe._dict(lifecycle_state="Open", is_open=1, exam_date=self.D2)   # report
        enforce_document_change(before, after)   # ne lève pas

    def test_enforce_ignores_state_transition(self):
        # Draft → Open : la transition passe par l'endpoint ; validate ne juge pas les dates ici
        before = frappe._dict(lifecycle_state="Draft", is_open=0, exam_date=self.D2)
        after = frappe._dict(lifecycle_state="Open", is_open=1, exam_date=self.D2)
        enforce_document_change(before, after)   # ne lève pas


class TestCalendarDuplication(FrappeTestCase):
    """Étape 3 — le cœur du lot : reprendre un ensemble de sessions et décaler les dates,
    créer des BROUILLONS, aperçu avant création, aucune collision, jamais toucher la source."""

    def setUp(self):
        _purge()
        self.src = frappe.get_doc({
            "doctype": "Admission Session", "session_code": f"{_MARK}-SRC",
            "label": "Prépa 3e", "programme_code": _PROG, "programme_label": "Cycle Préparatoire",
            "academic_year": "2026-2027", "opens_on": "2026-06-06", "closes_on": "2026-08-25",
            "bac_results_date": "2026-07-15", "application_fee_xof": 15000,
            "exam_date": "2026-08-26", "exam_call_time": "07:30:00", "exam_start_time": "08:00:00",
            "exam_room": "Salle A", "lifecycle_state": "Open",
        }).insert(ignore_permissions=True)
        frappe.db.commit()

    def tearDown(self):
        frappe.db.rollback()
        _purge()

    def _plans(self, **kw):
        from admission.api.calendar import _compute_duplicates
        return _compute_duplicates([self.src.name], kw.get("shift_days", 364), kw.get("academic_year"))

    # ── GK1 : aperçu = dates décalées, année bumpée, code dérivé, état Draft, sans écriture ─
    def test_preview_shifts_dates_and_is_draft(self):
        before_count = frappe.db.count("Admission Session", {"session_code": ["like", f"{_MARK}%"]})
        plan = self._plans()["sessions"][0]
        self.assertEqual(plan["lifecycle_state"], "Draft")
        self.assertEqual(plan["academic_year"], "2027-2028")
        self.assertTrue(plan["new_code"].endswith("2728"))
        # +364 j : clôture 2026-08-25 → 2027-08-24 (même jour de semaine)
        from frappe.utils import getdate, add_days
        self.assertEqual(plan["closes_on"], str(add_days(getdate("2026-08-25"), 364)))
        self.assertEqual(plan["exam_date"], str(add_days(getdate("2026-08-26"), 364)))
        # structure/heures/salle reprises (non décalées)
        self.assertEqual(plan["programme_code"], _PROG)
        self.assertEqual(plan["exam_room"], "Salle A")
        self.assertEqual(str(plan["exam_call_time"]), "07:30:00")
        # aucune écriture pendant l'aperçu
        self.assertEqual(frappe.db.count("Admission Session", {"session_code": ["like", f"{_MARK}%"]}), before_count)

    # ── §3 : 364 j conserve le jour de la semaine ───────────────────────────────
    def test_shift_preserves_weekday(self):
        from frappe.utils import getdate
        plan = self._plans()["sessions"][0]
        self.assertEqual(getdate(plan["closes_on"]).weekday(), getdate("2026-08-25").weekday())

    # ── GK1 : la création pose des BROUILLONS ; GK-nondestr : la source est intacte ─
    def test_create_makes_draft_and_never_touches_source(self):
        from admission.api.calendar import _create_duplicates
        src_before = frappe.get_doc("Admission Session", self.src.name).as_dict()
        res = _create_duplicates([self.src.name], 364, None)
        self.assertEqual(len(res["created"]), 1)
        new = frappe.get_doc("Admission Session", res["created"][0])
        self.assertEqual(new.lifecycle_state, "Draft")
        self.assertEqual(new.is_open, 0)
        self.assertEqual(new.programme_code, _PROG)
        self.assertEqual(str(new.closes_on), "2027-08-24")
        # source strictement inchangée
        src_after = frappe.get_doc("Admission Session", self.src.name).as_dict()
        for f in ("lifecycle_state", "is_open", "opens_on", "closes_on", "exam_date", "academic_year"):
            self.assertEqual(str(src_before[f]), str(src_after[f]), f)

    # ── GK3 : suppression d'un brouillon possible ; refusée hors brouillon ───────
    def test_delete_draft_ok_and_guarded(self):
        from admission.api.calendar import _create_duplicates, _delete_draft
        new_name = _create_duplicates([self.src.name], 364, None)["created"][0]
        _delete_draft(new_name)   # brouillon → supprimé
        self.assertFalse(frappe.db.exists("Admission Session", new_name))
        with self.assertRaises(frappe.ValidationError):   # source Open → refus
            _delete_draft(self.src.name)

    # ── GK1 #2 : collision d'identifiant signalée dans l'aperçu ──────────────────
    def test_code_collision_flagged(self):
        # pré-crée la session au code dérivé → l'aperçu doit signaler + ajuster
        frappe.get_doc({
            "doctype": "Admission Session", "session_code": f"{_MARK}-SRC-2728",
            "label": "occupant", "programme_code": _PROG, "programme_label": "x",
            "academic_year": "2027-2028", "opens_on": "2027-06-06", "closes_on": "2027-08-24",
            "bac_results_date": "2027-07-15", "application_fee_xof": 15000, "lifecycle_state": "Draft",
        }).insert(ignore_permissions=True)
        plan = self._plans()["sessions"][0]
        self.assertTrue(plan["code_adjusted"])
        self.assertNotEqual(plan["new_code"], f"{_MARK}-SRC-2728")


class TestCalendarMakerChecker(FrappeTestCase):
    """Étape 4 — le Responsable saisit, la Direction valide (§5). Ouverture = Direction ;
    changement de date = pending (l'ancienne valeur s'applique, GK8) → validation Direction."""

    def setUp(self):
        _purge()

    def tearDown(self):
        frappe.db.rollback()
        _purge()

    def _open_session(self, suffix, closes_on=None):
        return frappe.get_doc({
            "doctype": "Admission Session", "session_code": f"{_MARK}-{suffix}",
            "label": f"S {suffix}", "programme_code": _PROG, "programme_label": "Cycle test",
            "academic_year": "2026-2027", "opens_on": "2026-06-01",
            "closes_on": closes_on or add_days(nowdate(), 10), "bac_results_date": "2027-01-15",
            "application_fee_xof": 10000, "exam_date": "2026-12-20",
            "exam_call_time": "07:30:00", "exam_start_time": "08:00:00", "lifecycle_state": "Open",
        }).insert(ignore_permissions=True)

    def _draft(self, suffix):
        return frappe.get_doc({
            "doctype": "Admission Session", "session_code": f"{_MARK}-{suffix}",
            "label": f"D {suffix}", "programme_code": _PROG, "programme_label": "Cycle test",
            "academic_year": "2027-2028", "opens_on": "2027-06-01", "closes_on": "2027-08-25",
            "bac_results_date": "2028-01-15", "application_fee_xof": 10000, "lifecycle_state": "Draft",
        }).insert(ignore_permissions=True)

    # ── GK4 : ouvrir un brouillon (→ Open) ; refusé si pas un brouillon ──────────
    def test_open_draft_to_open(self):
        from admission.api.calendar import _open_session
        d = self._draft("OPEN")
        _open_session(d.name)
        self.assertEqual(frappe.db.get_value("Admission Session", d.name, "lifecycle_state"), "Open")
        self.assertEqual(frappe.db.get_value("Admission Session", d.name, "is_open"), 1)
        with self.assertRaises(frappe.ValidationError):   # déjà Open → refus
            _open_session(d.name)

    # ── GK3 : édition libre d'un brouillon (structure + dates) ──────────────────
    def test_update_draft_free(self):
        from admission.api.calendar import _update_draft
        d = self._draft("UPD")
        _update_draft(d.name, {"application_fee_xof": 20000, "closes_on": "2027-09-30", "label": "Renommée"})
        self.assertEqual(frappe.db.get_value("Admission Session", d.name, "application_fee_xof"), 20000)
        self.assertEqual(str(frappe.db.get_value("Admission Session", d.name, "closes_on")), "2027-09-30")

    # ── GK8 : proposer une prolongation → pending ; l'ANCIENNE valeur s'applique ─
    def test_propose_extend_pending_old_applies_then_validate(self):
        from admission.api.calendar import _propose_changes, _validate_changes
        from admission.api.sessions import is_session_selectable
        old_close = add_days(nowdate(), 10)
        new_close = add_days(nowdate(), 40)
        s = self._open_session("EXT", closes_on=old_close)
        _propose_changes(s.name, {"closes_on": str(new_close)})
        # pending posé ; valeur LIVE inchangée
        doc = frappe.get_doc("Admission Session", s.name)
        self.assertEqual(len(doc.pending_changes), 1)
        self.assertEqual(doc.pending_changes[0].change_field, "closes_on")
        self.assertEqual(str(doc.closes_on), str(old_close))    # l'ancienne valeur s'applique
        self.assertTrue(is_session_selectable(doc))
        # la Direction valide → la nouvelle valeur s'applique, pending purgé
        _validate_changes(s.name)
        doc = frappe.get_doc("Admission Session", s.name)
        self.assertEqual(str(doc.closes_on), str(new_close))
        self.assertEqual(len(doc.pending_changes), 0)

    # ── GK5 : proposer d'AVANCER la clôture est refusé par le serveur ───────────
    def test_propose_advance_refused(self):
        from admission.api.calendar import _propose_changes
        s = self._open_session("ADV", closes_on=add_days(nowdate(), 20))
        with self.assertRaises(frappe.ValidationError):
            _propose_changes(s.name, {"closes_on": str(add_days(nowdate(), 5))})   # avance
        self.assertEqual(len(frappe.get_doc("Admission Session", s.name).pending_changes), 0)

    # ── GK7 : la date des résultats du bac s'applique SANS validation ───────────
    def test_propose_bac_immediate_no_pending(self):
        from admission.api.calendar import _propose_changes
        s = self._open_session("BAC")
        _propose_changes(s.name, {"bac_results_date": "2027-02-01"})
        doc = frappe.get_doc("Admission Session", s.name)
        self.assertEqual(str(doc.bac_results_date), "2027-02-01")   # appliqué direct
        self.assertEqual(len(doc.pending_changes), 0)               # aucun pending

    # ── Report d'épreuve : pending puis validation applique la nouvelle date ────
    def test_propose_exam_postpone_then_validate(self):
        from admission.api.calendar import _propose_changes, _validate_changes
        s = self._open_session("EXAM")
        _propose_changes(s.name, {"exam_date": "2027-01-10"})
        doc = frappe.get_doc("Admission Session", s.name)
        self.assertEqual(str(doc.exam_date), "2026-12-20")   # ancienne date tant que non validé
        res = _validate_changes(s.name)
        self.assertEqual(str(frappe.db.get_value("Admission Session", s.name, "exam_date")), "2027-01-10")
        self.assertTrue(res["reissue_triggered"])   # un champ d'épreuve a changé

    # ── Rejet : la Direction écarte les propositions, l'ancienne valeur demeure ──
    def test_reject_discards_pending(self):
        from admission.api.calendar import _propose_changes, _reject_changes
        s = self._open_session("REJ", closes_on=add_days(nowdate(), 10))
        _propose_changes(s.name, {"closes_on": str(add_days(nowdate(), 40))})
        _reject_changes(s.name)
        doc = frappe.get_doc("Admission Session", s.name)
        self.assertEqual(len(doc.pending_changes), 0)
        self.assertEqual(str(doc.closes_on), str(add_days(nowdate(), 10)))   # inchangée

    # ── Un nouveau propose sur le même champ REMPLACE le pending (pas d'empilement) ─
    def test_propose_replaces_same_field(self):
        from admission.api.calendar import _propose_changes
        s = self._open_session("RPL", closes_on=add_days(nowdate(), 10))
        _propose_changes(s.name, {"closes_on": str(add_days(nowdate(), 30))})
        _propose_changes(s.name, {"closes_on": str(add_days(nowdate(), 45))})
        doc = frappe.get_doc("Admission Session", s.name)
        self.assertEqual(len(doc.pending_changes), 1)
        self.assertEqual(doc.pending_changes[0].proposed_value, str(add_days(nowdate(), 45)))


class TestCalendarReissue(FrappeTestCase):
    """Étape 5 — GK6 (preuve maîtresse) : valider un REPORT d'épreuve réémet les convocations,
    avec un objet EXPLICITE ; un changement NON-épreuve ne réémet rien."""

    def setUp(self):
        _purge()
        self.session = frappe.get_doc({
            "doctype": "Admission Session", "session_code": f"{_MARK}-RS",
            "label": "Prépa concours", "programme_code": _PROG, "programme_label": "Cycle Préparatoire",
            "academic_year": "2026-2027", "opens_on": "2026-06-01", "closes_on": add_days(nowdate(), 10),
            "bac_results_date": "2027-01-15", "application_fee_xof": 10000, "exam_date": "2026-12-20",
            "exam_call_time": "07:30:00", "exam_start_time": "08:00:00", "lifecycle_state": "Open",
        }).insert(ignore_permissions=True)
        # un convoqué : frais 1 CONFIRMÉ, convocation déjà envoyée (drapeau posé)
        self.app = frappe.get_doc({
            "doctype": "Admission Applicant", "applicant_name": f"{_MARK} KODJO", "programme_code": _PROG,
            "session": self.session.name, "email": "kodjo@example.test",
            "convocation_number": "12260001", "convocation_sent_at": "2026-11-01 09:00:00",
        }).insert(ignore_permissions=True, ignore_mandatory=True)
        fee = frappe.get_doc({"doctype": "Applicant Fee", "applicant": self.app.name, "fee_type": "application",
                              "amount_xof": 10000, "status": "Paid"}).insert(ignore_permissions=True, ignore_mandatory=True)
        pay = frappe.get_doc({"doctype": "Applicant Fee Payment", "applicant": self.app.name, "applicant_fee": fee.name,
                              "payment_mode": "Cash", "amount_xof": 10000, "payment_status": "Pending"}
                             ).insert(ignore_permissions=True, ignore_mandatory=True)
        frappe.db.set_value("Applicant Fee Payment", pay.name, "payment_status", "Confirmed", update_modified=False)
        frappe.db.commit()

    def tearDown(self):
        frappe.db.rollback()
        _purge()

    # ── GK6 : report d'épreuve → réémission avec objet explicite + nouvelle date ─
    @patch("frappe.sendmail")
    def test_exam_postpone_reissues_with_explicit_subject(self, mock_sendmail):
        from admission.api.calendar import _propose_changes, _validate_changes
        _propose_changes(self.session.name, {"exam_date": "2027-01-10"})
        res = _validate_changes(self.session.name)
        self.assertTrue(res["reissue_triggered"])
        self.assertEqual(mock_sendmail.call_count, 1)
        kw = mock_sendmail.call_args.kwargs
        self.assertEqual(kw["subject"], "Report de votre épreuve — nouvelle date")
        self.assertIn("date de votre épreuve a été modifiée", kw["message"])
        self.assertTrue(any(a["fname"] == "convocation-12260001.pdf" for a in kw["attachments"]))
        # le drapeau d'envoi est re-daté (réémission effective, pas bloquée par l'envoi unique)
        self.assertNotEqual(
            str(frappe.db.get_value("Admission Applicant", self.app.name, "convocation_sent_at")),
            "2026-11-01 09:00:00")

    # ── un changement NON-épreuve (prolongation clôture) ne réémet AUCUNE convocation ─
    @patch("frappe.sendmail")
    def test_closes_extend_does_not_reissue(self, mock_sendmail):
        from admission.api.calendar import _propose_changes, _validate_changes
        _propose_changes(self.session.name, {"closes_on": str(add_days(nowdate(), 40))})
        res = _validate_changes(self.session.name)
        self.assertFalse(res["reissue_triggered"])
        self.assertEqual(mock_sendmail.call_count, 0)


class TestCoherenceA07(FrappeTestCase):
    """A07 — cohérence INTER-CHAMPS dans la source unique, appliquée par le serveur."""

    def setUp(self):
        _purge()

    def tearDown(self):
        frappe.db.rollback()
        _purge()

    def _open(self, closes, exam):
        return frappe.get_doc({
            "doctype": "Admission Session", "session_code": f"{_MARK}-COH",
            "label": "Coh", "programme_code": _PROG, "programme_label": "Cycle test",
            "academic_year": "2026-2027", "opens_on": "2026-06-01", "closes_on": closes,
            "bac_results_date": "2027-01-15", "application_fee_xof": 10000, "exam_date": exam,
            "exam_call_time": "07:30:00", "exam_start_time": "08:00:00", "lifecycle_state": "Open",
        }).insert(ignore_permissions=True)

    def test_coherence_errors_unit(self):
        from admission.api.calendar_rules import coherence_errors
        self.assertTrue(coherence_errors({"closes_on": "2026-09-28", "exam_date": "2026-09-26"}))   # exam<=closes
        self.assertTrue(coherence_errors({"closes_on": "2026-09-26", "exam_date": "2026-09-26"}))   # égal → refusé (strict)
        self.assertFalse(coherence_errors({"closes_on": "2026-09-25", "exam_date": "2026-09-26"}))  # ok
        self.assertTrue(coherence_errors({"opens_on": "2026-09-10", "closes_on": "2026-09-05"}))    # closes<opens
        # heures : contrôle gated sur exam_date présent (session à épreuve)
        self.assertTrue(coherence_errors({"exam_date": "2026-09-26", "exam_call_time": "08:00:00", "exam_start_time": "07:30:00"}))
        self.assertFalse(coherence_errors({"exam_date": "2026-09-26", "exam_call_time": "07:30:00", "exam_start_time": "08:00:00"}))
        self.assertFalse(coherence_errors({"exam_call_time": "08:00:00", "exam_start_time": "07:30:00"}))  # sans exam_date → ignoré
        self.assertFalse(coherence_errors({"opens_on": "2026-06-01"}))   # champ partiel → aucune erreur
        self.assertFalse(coherence_errors({}))

    def test_validate_rejects_incoherent_save(self):
        s = self._open(add_days(nowdate(), 10), add_days(nowdate(), 20))
        doc = frappe.get_doc("Admission Session", s.name)
        doc.closes_on = add_days(nowdate(), 25)   # clôture APRÈS l'épreuve → incohérent
        with self.assertRaises(frappe.ValidationError):
            doc.save(ignore_permissions=True)

    def test_propose_extension_beyond_exam_refused(self):
        from admission.api.calendar import _propose_changes
        s = self._open(add_days(nowdate(), 10), add_days(nowdate(), 20))
        with self.assertRaises(frappe.ValidationError):   # prolongation qui dépasse l'épreuve
            _propose_changes(s.name, {"closes_on": str(add_days(nowdate(), 25))})
        self.assertEqual(len(frappe.get_doc("Admission Session", s.name).pending_changes), 0)
        # une prolongation qui RESTE avant l'épreuve passe (pending)
        r = _propose_changes(s.name, {"closes_on": str(add_days(nowdate(), 15))})
        self.assertIn("closes_on", r["pending"])


class TestLabelA06(FrappeTestCase):
    """A06 — pas de date figée dans le libellé (elle vit dans exam_date) + défaut décalage 364."""

    def setUp(self):
        _purge()

    def tearDown(self):
        frappe.db.rollback()
        _purge()

    def test_strip_label_date(self):
        from admission.api.calendar import _strip_label_date
        self.assertEqual(_strip_label_date("Prépa — Session 4 (concours 07/09/2026)"), "Prépa — Session 4")
        self.assertEqual(_strip_label_date("Bachelor X — rentrée 2026"), "Bachelor X — rentrée 2026")  # intact

    def _src(self):
        return frappe.get_doc({
            "doctype": "Admission Session", "session_code": f"{_MARK}-LBL",
            "label": "Prépa — Session 4 (concours 07/09/2026)", "programme_code": _PROG,
            "programme_label": "Cycle test", "academic_year": "2026-2027", "opens_on": "2026-06-01",
            "closes_on": "2026-08-25", "bac_results_date": "2027-01-15", "application_fee_xof": 10000,
            "exam_date": "2026-08-26", "exam_call_time": "07:30:00", "exam_start_time": "08:00:00",
            "lifecycle_state": "Open",
        }).insert(ignore_permissions=True)

    def test_duplication_strips_label_date(self):
        from admission.api.calendar import _compute_duplicates
        src = self._src()
        plan = _compute_duplicates([src.name], 364, None)["sessions"][0]
        self.assertEqual(plan["label"], "Prépa — Session 4")
        self.assertNotIn("concours", plan["label"])

    def test_shift_zero_defaults_364(self):
        from admission.api.calendar import _compute_duplicates
        src = self._src()
        self.assertEqual(_compute_duplicates([src.name], 0, None)["shift_days"], 364)
        self.assertEqual(_compute_duplicates([src.name], None, None)["shift_days"], 364)


class TestFieldPolicies(FrappeTestCase):
    """GK9 — la politique par champ SERVIE au front (source unique). Une sonde bidon normaliserait
    les dates en None==None (faux « aucun changement ») → tout champ date faussement éditable.
    Ces tests fixent le contrat que calendar_view.session_detail expose à l'écran."""

    def _pol(self, state):
        return field_policies(frappe._dict(lifecycle_state=state, is_open=1 if state == "Open" else 0))

    def test_open_structure_locked_dates_constrained(self):
        p = self._pol("Open")
        self.assertFalse(p["opens_on"]["editable"])                         # structure figée (publié engage)
        self.assertTrue(p["closes_on"]["editable"])
        self.assertTrue(p["closes_on"]["requires_validation"])
        self.assertEqual(p["closes_on"]["constraint"], "extend_only")
        self.assertFalse(p["closes_on"]["triggers_reissue"])
        self.assertTrue(p["exam_date"]["editable"])
        self.assertEqual(p["exam_date"]["constraint"], "postpone_only")
        self.assertTrue(p["exam_date"]["triggers_reissue"])
        self.assertTrue(p["exam_call_time"]["triggers_reissue"])
        self.assertTrue(p["exam_room"]["triggers_reissue"])
        self.assertTrue(p["bac_results_date"]["editable"])
        self.assertFalse(p["bac_results_date"]["requires_validation"])

    def test_closed_only_exam_and_bac(self):
        p = self._pol("Closed")
        self.assertFalse(p["closes_on"]["editable"])                        # clôture figée une fois fermée
        self.assertFalse(p["opens_on"]["editable"])
        self.assertTrue(p["exam_date"]["editable"])
        self.assertTrue(p["exam_date"]["triggers_reissue"])
        self.assertEqual(p["exam_date"]["constraint"], "postpone_only")
        self.assertTrue(p["bac_results_date"]["editable"])

    def test_draft_all_editable(self):
        p = self._pol("Draft")
        for f, pol in p.items():
            self.assertTrue(pol["editable"], f)
            self.assertFalse(pol["requires_validation"], f)

    def test_every_field_carries_a_reason(self):
        # APPLIQUER §4 : un champ grisé sans `reason` est un défaut de recette.
        for state in ("Draft", "Open", "Closed"):
            for f, pol in self._pol(state).items():
                self.assertTrue((pol["reason"] or "").strip(), f"{state}/{f}")


class TestCalendarView(FrappeTestCase):
    """Intégration de calendar_view.py (proposé au handoff) : lecture groupée + policies + pending.
    Le front en dépend intégralement — testé comme tout back."""

    def setUp(self):
        _purge()
        self.draft = frappe.get_doc({
            "doctype": "Admission Session", "session_code": f"{_MARK}-VD", "label": "Draft view",
            "programme_code": _PROG, "programme_label": "Cycle test", "academic_year": "2027-2028",
            "opens_on": "2027-06-01", "closes_on": "2027-08-25", "bac_results_date": "2028-01-15",
            "application_fee_xof": 10000, "lifecycle_state": "Draft",
        }).insert(ignore_permissions=True)
        self.open = frappe.get_doc({
            "doctype": "Admission Session", "session_code": f"{_MARK}-VO", "label": "Open view",
            "programme_code": _PROG, "programme_label": "Cycle test", "academic_year": "2026-2027",
            "opens_on": "2026-06-01", "closes_on": add_days(nowdate(), 10), "bac_results_date": "2027-01-15",
            "application_fee_xof": 10000, "exam_date": "2026-12-20",
            "exam_call_time": "07:30:00", "exam_start_time": "08:00:00", "lifecycle_state": "Open",
        }).insert(ignore_permissions=True)
        frappe.db.commit()

    def tearDown(self):
        frappe.db.rollback()
        _purge()

    def test_calendar_list_groups_and_exposes_default_shift(self):
        from admission.api.calendar_view import calendar_list
        data = calendar_list()["data"]
        self.assertEqual(data["default_shift_days"], 364)      # le front lit CE défaut (pas de 364 en dur)
        ays = [g["academic_year"] for g in data["groups"]]
        self.assertIn("2027-2028", ays)
        self.assertIn("2026-2027", ays)
        # chaque session porte policies + pending (le front ne calcule rien)
        allsess = [s for g in data["groups"] for s in g["sessions"]]
        one = next(s for s in allsess if s["name"] == self.open.name)
        self.assertIn("policies", one)
        self.assertIn("closes_on", one["policies"])
        self.assertEqual(one["display_status"], "a_venir")
        self.assertEqual(one["exam_call_time"], "07:30")        # timedelta → 'HH:MM'

    def test_session_detail_carries_policies_and_can_delete(self):
        from admission.api.calendar_view import session_detail
        d = session_detail(self.draft.name)["data"]
        self.assertTrue(d["can_delete"])                        # brouillon sans dossier
        self.assertFalse(d["policies"]["opens_on"]["editable"] is None)
        o = session_detail(self.open.name)["data"]
        self.assertFalse(o["can_delete"])                       # Open → non supprimable

    def test_pending_queue_lists_proposals(self):
        from admission.api.calendar import _propose_changes
        from admission.api.calendar_view import pending_queue
        _propose_changes(self.open.name, {"closes_on": str(add_days(nowdate(), 40))})
        q = pending_queue()["data"]
        self.assertEqual(q["total"], 1)
        self.assertEqual(q["items"][0]["change"]["change_field"], "closes_on")
        self.assertEqual(q["items"][0]["session"]["name"], self.open.name)
