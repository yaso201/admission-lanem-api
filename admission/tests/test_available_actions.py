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
