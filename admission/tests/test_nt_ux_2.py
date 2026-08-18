"""NT-UX-2 — la confirmation de paiement déclenche, depuis BRO/SOP, la transition workflow
« Confirm Payment » → SOU réservée à l'Administratif. Pour un rôle non-Administratif, on l'expose
comme blocage de RÔLE (grisé + acteur) via `blocked_actions`, au lieu d'un bouton actif rejeté
après saisie (« actif puis rejeté »). Preuve : Direction/Responsable bloqués, Administratif inchangé.
unittest.TestCase pur — aucune DB.
"""
import types
from unittest import TestCase

from admission.api._actions import blocked_actions


def _a(status="SOP", **k):
    base = dict(status=status, bac_verified=0, notes_validated=0, notes_concours=None,
                requested_scholarships="[]", conditionnel=0)
    base.update(k)
    return types.SimpleNamespace(**base)


ADMIN = ["Admission Administratif"]
RESP = ["Admission Responsable"]
DIR = ["Admission Direction"]
SYSMGR = ["System Manager"]


def _confirm_block(applicant, roles):
    blk = [b for b in blocked_actions(applicant, roles, is_prepa=False, ctx={})
           if b["action"] == "confirm_payment"]
    return blk[0] if blk else None


class TestNtUx2ConfirmPaymentRoleBlock(TestCase):
    def test_direction_sees_confirm_reserved_at_sop(self):
        blk = _confirm_block(_a("SOP"), DIR)
        self.assertIsNotNone(blk, "la Direction doit voir la confirmation grisée, pas active")
        self.assertEqual(blk["actor"], "Administratif")
        self.assertEqual(blk["reason"], "Réservé à l'Administratif")
        self.assertEqual(blk["code"], "RESERVED_TO_ADMINISTRATIF")

    def test_responsable_also_reserved_at_sop(self):
        self.assertIsNotNone(_confirm_block(_a("SOP"), RESP))

    def test_administratif_not_blocked_strictly_unchanged(self):
        # DEC-D : le parcours Administratif reste STRICTEMENT identique — aucun blocage.
        self.assertIsNone(_confirm_block(_a("SOP"), ADMIN))

    def test_sysmgr_not_blocked(self):
        # _EXA autorise (Administratif, System Manager) → le super-admin confirme.
        self.assertIsNone(_confirm_block(_a("SOP"), SYSMGR))

    def test_bro_also_reserved(self):
        self.assertIsNotNone(_confirm_block(_a("BRO"), DIR))

    def test_no_block_outside_confirm_transition_states(self):
        # frais 2 à ACC / autres états : confirmer ne transitionne pas → aucun rejet workflow →
        # aucun blocage de rôle (comportement inchangé, DEC-C : on ne ferme que le vrai cas).
        for st in ("ACC", "ETU", "SOU", "ATT", "INS"):
            self.assertIsNone(_confirm_block(_a(st), DIR), st)

    def test_block_shape_matches_nt_ux_contract(self):
        # même forme {action, actor, code, reason} que les blocages d'état (contrat blocked_actions).
        blk = _confirm_block(_a("SOP"), DIR)
        self.assertEqual(set(blk.keys()), {"action", "actor", "code", "reason"})
