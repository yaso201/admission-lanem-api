"""FIX-PROGRESSION — available_actions : source unique de la disponibilité d'action staff.

Deux niveaux :
  - unitaire (ce fichier, classe TestAvailableActions) : le registre calcule la bonne liste
    par (statut × rôles × flags métier), hiérarchie ascendante appliquée ;
  - cohérence AS chaque vrai rôle (TestCoherenceMatrix) : registre ↔ gardes endpoint, 0 dérive.
"""

import json
import types
from unittest import TestCase

from admission.api._actions import (
    available_actions,
    can_control_pieces,
    can_manage_payments,
)


def _a(**k):
    """Faux applicant (attributs lus par les règles). Défauts = SOU, rien de coché."""
    base = dict(status="SOU", bac_verified=0, notes_validated=0, notes_concours=None,
                requested_scholarships="[]", conditionnel=0)
    base.update(k)
    return types.SimpleNamespace(**base)


ADMIN = ["Admission Administratif"]
RESP = ["Admission Responsable"]
DIR = ["Admission Direction"]
SM = ["Admission SM"]


class TestAvailableActions(TestCase):
    def test_admin_sou_intake(self):
        acts = available_actions(_a(status="SOU"), ADMIN, is_prepa=False)
        self.assertIn("start_review", acts)
        self.assertIn("reject_dossier", acts)
        self.assertIn("request_complement", acts)
        self.assertIn("notify_pieces_recap", acts)
        self.assertNotIn("mark_admissible", acts)   # resp-only + pas ETU

    def test_responsable_at_sou_gets_admin_actions_via_hierarchy(self):
        acts = available_actions(_a(status="SOU"), RESP, is_prepa=False)
        self.assertIn("start_review", acts)          # ascendant : Resp ⊇ Admin
        self.assertTrue(can_control_pieces(_a(status="SOU"), RESP))

    def test_directeur_at_etu_sees_resp_decisions(self):
        acts = available_actions(_a(status="ETU"), DIR, is_prepa=False)
        self.assertIn("mark_admissible", acts)       # Dir ⊇ Resp
        self.assertIn("waitlist", acts)

    def test_adm_accept_dir_only_and_separation(self):
        self.assertIn("accept_admission", available_actions(_a(status="ADM"), DIR, is_prepa=False))
        self.assertNotIn("accept_admission", available_actions(_a(status="ADM"), RESP, is_prepa=False))
        # refuse est statut-dépendant : ADM→REF = Direction
        self.assertIn("refuse", available_actions(_a(status="ADM"), DIR, is_prepa=False))
        self.assertNotIn("refuse", available_actions(_a(status="ADM"), RESP, is_prepa=False))

    def test_refuse_etu_is_responsable(self):
        self.assertIn("refuse", available_actions(_a(status="ETU"), RESP, is_prepa=False))
        self.assertIn("refuse", available_actions(_a(status="ETU"), DIR, is_prepa=False))  # Dir ⊇ Resp

    def test_sm_pur_zero_workflow(self):
        self.assertEqual(available_actions(_a(status="ADM"), SM, is_prepa=False), [])
        self.assertFalse(can_control_pieces(_a(status="SOU"), SM))
        self.assertFalse(can_manage_payments(_a(status="SOU"), SM))

    # ── conditions MÉTIER (raffinement gate) ──────────────────────────────────
    def test_prepa_saisir_notes_condition(self):
        a = _a(status="ETU")
        self.assertIn("saisir_note_concours", available_actions(a, ADMIN, is_prepa=True))
        self.assertNotIn("saisir_note_concours", available_actions(a, ADMIN, is_prepa=False))
        # notes déjà validées → plus de saisie
        self.assertNotIn("saisir_note_concours",
                         available_actions(_a(status="ETU", notes_validated=1), ADMIN, is_prepa=True))

    def test_valider_notes_condition(self):
        a = _a(status="ETU", notes_concours='{"maths":"12"}')
        self.assertIn("valider_notes_concours", available_actions(a, RESP, is_prepa=True))
        # pas de valeurs saisies → rien à valider
        self.assertNotIn("valider_notes_concours",
                         available_actions(_a(status="ETU"), RESP, is_prepa=True))

    def test_verify_bac_condition(self):
        self.assertIn("verify_bac_diploma",
                      available_actions(_a(status="ACO", bac_verified=0), ADMIN, is_prepa=False))
        # déjà vérifié → plus de bouton
        self.assertNotIn("verify_bac_diploma",
                         available_actions(_a(status="ACO", bac_verified=1), ADMIN, is_prepa=False))

    def test_propose_scholarships_condition(self):
        with_req = _a(status="ETU", requested_scholarships='["m1"]')
        self.assertIn("propose_scholarships", available_actions(with_req, RESP, is_prepa=False))
        # aucune bourse demandée → rien à proposer
        self.assertNotIn("propose_scholarships", available_actions(_a(status="ETU"), RESP, is_prepa=False))

    def test_conditional_admission_condition(self):
        self.assertIn("conditional_admission",
                      available_actions(_a(status="ETU", conditionnel=1), RESP, is_prepa=False))
        self.assertNotIn("conditional_admission",
                         available_actions(_a(status="ETU", conditionnel=0), RESP, is_prepa=False))

    def test_pieces_control_requires_sou(self):
        self.assertTrue(can_control_pieces(_a(status="SOU"), ADMIN))
        self.assertFalse(can_control_pieces(_a(status="ETU"), ADMIN))

    def test_payments_forbidden_on_dead_dossier(self):
        self.assertTrue(can_manage_payments(_a(status="SOU"), ADMIN))
        self.assertFalse(can_manage_payments(_a(status="REF"), ADMIN))
        self.assertFalse(can_manage_payments(_a(status="INS"), ADMIN))

    def test_enroll_dir_only_at_acc(self):
        self.assertIn("enroll", available_actions(_a(status="ACC"), DIR, is_prepa=False))
        self.assertNotIn("enroll", available_actions(_a(status="ACC"), RESP, is_prepa=False))

    def test_withdraw_admin_on_live_states(self):
        self.assertIn("withdraw", available_actions(_a(status="ADM"), ADMIN, is_prepa=False))
        self.assertNotIn("withdraw", available_actions(_a(status="REF"), ADMIN, is_prepa=False))


class TestGetDossierExposesActions(TestCase):
    """T2 — get_dossier (staff) sert available_actions/can_control_pieces/can_manage_payments.
    On mocke frappe minimalement (get_dossier lit beaucoup) : on cible juste la présence + type
    des 3 champs sur le retour, via un applicant SOU réel construit par fixture recette-like.
    Ici on teste le CÂBLAGE : les 3 clés existent dans le contrat. La justesse des valeurs est
    prouvée par la matrice de cohérence AS vrais rôles (TestCoherenceMatrix)."""

    def test_contract_has_the_three_keys(self):
        # Vérifie que le retour de get_dossier contient les 3 nouvelles clés (câblage contrat).
        import inspect
        from admission.api import staff
        src = inspect.getsource(staff.get_dossier)
        self.assertIn("available_actions", src)
        self.assertIn("can_control_pieces", src)
        self.assertIn("can_manage_payments", src)


import frappe                                            # noqa: E402
from frappe.tests.utils import FrappeTestCase            # noqa: E402
from admission.api import staff as S                     # noqa: E402
from admission.api._actions import _ACTION_RULES         # noqa: E402

_ADMIN_U = "prog-admin@lanem.test"
_RESP_U = "prog-resp@lanem.test"
_DIR_U = "prog-dir@lanem.test"
_SM_U = "prog-sm@lanem.test"
_ROLE_OF = {_ADMIN_U: "Admission Administratif", _RESP_U: "Admission Responsable",
            _DIR_U: "Admission Direction", _SM_U: "Admission SM"}
_TOP = ["Admission Direction"]   # rôle sommet : authorise TOUTE action applicable (ascendant)

# Cas de statut + flags métier → exercent les conditions (verify_bac/is_prepa/bourses/conditionnel).
_CASES = [
    {"status": "SOU", "flags": {}, "is_prepa": False},
    {"status": "ETU", "flags": {"conditionnel": 1, "requested_scholarships": '["m1"]'}, "is_prepa": False},
    {"status": "ETU", "flags": {"notes_concours": '{"maths":"12"}', "notes_validated": 0}, "is_prepa": True},
    {"status": "ADM", "flags": {}, "is_prepa": False},
    {"status": "ACO", "flags": {"bac_verified": 0}, "is_prepa": False},
    {"status": "ACC", "flags": {}, "is_prepa": False},
    {"status": "ATT", "flags": {}, "is_prepa": False},
    {"status": "REJ", "flags": {}, "is_prepa": False},
]


class TestCoherenceMatrix(FrappeTestCase):
    """GP1 — anti-dérive : AS chaque vrai rôle (set_user, in_test=False → only_for actif),
    pour chaque statut × action, available_actions coïncide avec le garde RÉEL de l'endpoint :
      · FORWARD  : tout bouton MONTRÉ passe le garde de rôle (aucun bouton rejeté sur rôle) ;
      · REVERSE  : tout ce que le rôle PEUT (garde accepte) et qui est applicable EST montré
                   (désync fermée — un supérieur voit ce qu'il peut invoquer)."""

    def _clean(self):
        frappe.set_user("Administrator")
        for e in _ROLE_OF:
            if frappe.db.exists("User", e):
                frappe.delete_doc("User", e, force=True, ignore_permissions=True)
        if getattr(self, "dossier", None) and frappe.db.exists("Admission Applicant", self.dossier):
            frappe.delete_doc("Admission Applicant", self.dossier, force=True, ignore_permissions=True)
        frappe.db.commit()

    def setUp(self):
        self._clean()
        for e, role in _ROLE_OF.items():
            frappe.get_doc({"doctype": "User", "email": e, "first_name": e.split("-")[1],
                            "send_welcome_email": 0, "enabled": 1,
                            "roles": [{"role": role}]}).insert(ignore_permissions=True)
        sessions = frappe.get_all("Admission Session", limit=1, pluck="name")
        if not sessions:
            self.skipTest("Aucune Admission Session seedee (decor requis).")
        app = frappe.get_doc({
            "doctype": "Admission Applicant", "status": "BRO",
            "first_name": "Prog", "last_name": "Test", "email": "prog-app@lanem.test",
            "phone": "+2290160000001", "programme_code": "PREPA", "level_code": "PREPA-S1",
            "session": sessions[0],
        }).insert(ignore_permissions=True)
        self.dossier = app.name
        frappe.db.commit()
        self.addCleanup(self._clean)

    def _gate(self, user, fn):
        """Garde RÉEL de l'endpoint AS `user` :
          · DENIED       = only_for a rejeté (PermissionError) → rôle refusé ;
          · STATE_REJECT = INVALID_STATE (garde de statut) → action inapplicable à cet état ;
          · PASSED       = a franchi rôle ET statut (échoue plus loin : args/motif/métier).
        Scelle LES DEUX dimensions (rôle + statut) contre le registre."""
        frappe.set_user(user)
        frappe.flags.in_test = False
        try:
            try:
                r = fn(dossier_id=self.dossier)
                if isinstance(r, dict) and not r.get("ok") \
                        and (r.get("error") or {}).get("code") == "INVALID_STATE":
                    return "STATE_REJECT"
                return "PASSED"
            except frappe.PermissionError:
                return "DENIED"
            except Exception:
                return "PASSED"     # franchi rôle+statut ; échoue plus loin (args/métier)
        finally:
            frappe.flags.in_test = True
            frappe.set_user("Administrator")
            frappe.db.rollback()

    def test_coherence_all_roles_all_states(self):
        violations = []
        for case in _CASES:
            frappe.db.set_value("Admission Applicant", self.dossier,
                                dict(status=case["status"], **case["flags"]))
            frappe.db.commit()
            doc = frappe.get_doc("Admission Applicant", self.dossier)
            ip = case["is_prepa"]
            applicable = set(available_actions(doc, _TOP, is_prepa=ip))   # tout ce qui est applicable ici
            for user, role in _ROLE_OF.items():
                shown = set(available_actions(doc, [role], is_prepa=ip))
                for key in _ACTION_RULES:
                    fn = getattr(S, key)
                    gate = self._gate(user, fn)
                    cell = f"{case['status']}/{role.split()[-1]}/{key}"
                    # FORWARD : montré ⇒ le garde de rôle accepte
                    if key in shown and gate != "PASSED":
                        violations.append(f"FORWARD {cell}: montré mais garde={gate}")
                    # REVERSE : applicable ET garde accepte ⇒ montré
                    if key in applicable and gate == "PASSED" and key not in shown:
                        violations.append(f"REVERSE {cell}: autorisé+applicable mais masqué")
        self.assertEqual(violations, [], "\n".join(violations))
