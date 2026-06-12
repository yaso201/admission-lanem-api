"""Tests SOCLE-0-CONSULT — cloisonnement consultation générique, débranché par défaut.

Couvre les décisions de cadrage :
 - OFF par défaut → aucune restriction (tout staff voit tout — MVP).
 - ON → cloisonnement selon un axe PARAMÉTRABLE (status / session / …).
 - Bascule OFF→ON par CONFIG SEULE (test_toggle_by_config_only — le cœur).
 - fail-closed : ON sans scope OU axe invalide → l'utilisateur ne voit rien (+refus).
 - System Manager bypass (voit tout, même ON).
 - has_permission refuse TOUS les ptypes hors périmètre.
 - mapping par défaut seedé aligné Workflow, inerte tant que OFF.
 - branchement réel : hooks enregistrés pour Admission Applicant.

Style aligné sur la suite existante : unittest.TestCase + frappe mocké (pas de DB).
"""

import json
import os
from unittest import TestCase
from unittest.mock import MagicMock, patch

PERM = "admission.api.permissions"

# Mapping de référence aligné sur le Workflow (allow_edit).
WORKFLOW_SCOPES = {
    "Admission Administratif": ["BRO", "SOP", "SOU", "INC", "DES"],
    "Admission Responsable": ["ETU", "ATT", "ADM", "ACO"],
    "Admission Direction": ["ACC", "INS", "REF"],
}

VALID_FIELDS = {"status", "session", "programme_code", "level_code", "name"}


def _mock_frappe(roles=None, settings=None, session_user="agent@lanem.bj"):
    """Construit un faux `frappe` pour piloter les entrées de la logique de cloisonnement."""
    mf = MagicMock()
    mf.session.user = session_user
    mf.get_roles.return_value = list(roles or [])
    s = dict(settings or {})
    mf.db.get_single_value.side_effect = lambda dt, field: s.get(field)
    mf.get_meta.return_value.has_field.side_effect = lambda fn: fn in VALID_FIELDS
    # escape réaliste (quote + double les apostrophes) pour vérifier l'anti-injection
    mf.db.escape.side_effect = lambda v: "'%s'" % str(v).replace("'", "''")
    return mf


def _settings(enabled, axis="status", scopes=None):
    return {
        "consultation_cloisonnee": 1 if enabled else 0,
        "consultation_axis": axis,
        "consultation_role_scopes": json.dumps(scopes if scopes is not None else WORKFLOW_SCOPES),
    }


def _doc(axis_value, axis="status"):
    d = MagicMock()
    d.get.side_effect = lambda k: axis_value if k == axis else None
    return d


class TestConsultOff(TestCase):
    """OFF par défaut = comportement MVP (tout visible)."""

    def test_query_conditions_off_returns_empty(self):
        mf = _mock_frappe(roles=["Admission Responsable"], settings=_settings(False))
        with patch(f"{PERM}.frappe", mf):
            from admission.api.permissions import get_permission_query_conditions
            self.assertEqual(get_permission_query_conditions("agent@lanem.bj"), "")

    def test_has_permission_off_defers(self):
        mf = _mock_frappe(roles=["Admission Responsable"], settings=_settings(False))
        with patch(f"{PERM}.frappe", mf):
            from admission.api.permissions import has_permission
            self.assertIsNone(has_permission(_doc("BRO"), "read", "agent@lanem.bj"))


class TestConsultOnState(TestCase):
    """ON, axe = status (aligné Workflow)."""

    def test_query_conditions_on_filters_by_state(self):
        mf = _mock_frappe(roles=["Admission Responsable"], settings=_settings(True))
        with patch(f"{PERM}.frappe", mf):
            from admission.api.permissions import get_permission_query_conditions
            cond = get_permission_query_conditions("agent@lanem.bj")
        self.assertIn("`tabAdmission Applicant`.`status` in (", cond)
        self.assertIn("'ETU'", cond)
        self.assertIn("'ACO'", cond)
        self.assertNotIn("'ACC'", cond)  # ACC appartient à la Direction, pas au Responsable
        self.assertNotIn("'BRO'", cond)

    def test_has_permission_in_scope_defers_out_denies(self):
        mf = _mock_frappe(roles=["Admission Responsable"], settings=_settings(True))
        with patch(f"{PERM}.frappe", mf):
            from admission.api.permissions import has_permission
            self.assertIsNone(has_permission(_doc("ETU"), "read", "agent@lanem.bj"))
            self.assertFalse(has_permission(_doc("BRO"), "read", "agent@lanem.bj"))

    def test_has_permission_denies_all_ptypes_out_of_scope(self):
        mf = _mock_frappe(roles=["Admission Responsable"], settings=_settings(True))
        with patch(f"{PERM}.frappe", mf):
            from admission.api.permissions import has_permission
            for ptype in ("read", "write", "delete", "submit"):
                self.assertFalse(
                    has_permission(_doc("REF"), ptype, "agent@lanem.bj"),
                    msg=f"ptype {ptype} devrait être refusé hors périmètre",
                )


class TestConsultBypass(TestCase):
    """System Manager voit tout, même ON."""

    def test_system_manager_query_empty(self):
        mf = _mock_frappe(roles=["System Manager", "Admission Responsable"], settings=_settings(True))
        with patch(f"{PERM}.frappe", mf):
            from admission.api.permissions import get_permission_query_conditions
            self.assertEqual(get_permission_query_conditions("boss@lanem.bj"), "")

    def test_system_manager_has_permission_defers(self):
        mf = _mock_frappe(roles=["System Manager"], settings=_settings(True))
        with patch(f"{PERM}.frappe", mf):
            from admission.api.permissions import has_permission
            self.assertIsNone(has_permission(_doc("BRO"), "read", "boss@lanem.bj"))


class TestConsultFailClosed(TestCase):
    """ON + pas de scope OU axe invalide → fail-closed (rien)."""

    def test_no_scope_for_user_sees_nothing(self):
        mf = _mock_frappe(roles=["Some Unmapped Role"], settings=_settings(True))
        with patch(f"{PERM}.frappe", mf):
            from admission.api.permissions import get_permission_query_conditions, has_permission
            cond = get_permission_query_conditions("ghost@lanem.bj")
            self.assertIn("__no_scope__", cond)
            self.assertFalse(has_permission(_doc("ETU"), "read", "ghost@lanem.bj"))

    def test_invalid_axis_fails_closed(self):
        mf = _mock_frappe(roles=["Admission Responsable"], settings=_settings(True, axis="evil; DROP TABLE"))
        with patch(f"{PERM}.frappe", mf):
            from admission.api.permissions import get_permission_query_conditions, has_permission
            cond = get_permission_query_conditions("agent@lanem.bj")
            self.assertIn("__cloisonnement_misconfigured__", cond)
            self.assertNotIn("DROP TABLE", cond)  # axe non interpolé brut
            self.assertFalse(has_permission(_doc("ETU"), "read", "agent@lanem.bj"))


class TestConsultAxisParametrable(TestCase):
    """L'axe est paramétrable : changer d'axe = config, pas code."""

    def test_axis_session(self):
        scopes = {"Admission Responsable": ["SES-2026-10"]}
        mf = _mock_frappe(roles=["Admission Responsable"], settings=_settings(True, axis="session", scopes=scopes))
        with patch(f"{PERM}.frappe", mf):
            from admission.api.permissions import get_permission_query_conditions
            cond = get_permission_query_conditions("agent@lanem.bj")
        self.assertIn("`tabAdmission Applicant`.`session` in (", cond)
        self.assertIn("'SES-2026-10'", cond)
        self.assertNotIn("`status`", cond)


class TestConsultToggle(TestCase):
    """LE cœur : bascule OFF→ON par CONFIG SEULE, même fonction, même code."""

    def test_toggle_by_config_only(self):
        from admission.api.permissions import get_permission_query_conditions
        roles = ["Admission Responsable"]
        # OFF
        mf_off = _mock_frappe(roles=roles, settings=_settings(False))
        with patch(f"{PERM}.frappe", mf_off):
            off = get_permission_query_conditions("agent@lanem.bj")
        # ON — SEUL le flag de config change
        mf_on = _mock_frappe(roles=roles, settings=_settings(True))
        with patch(f"{PERM}.frappe", mf_on):
            on = get_permission_query_conditions("agent@lanem.bj")
        self.assertEqual(off, "")                 # OFF = aucune restriction
        self.assertIn("`status` in (", on)        # ON = cloisonné
        self.assertNotEqual(off, on)              # bascule effective sans toucher au code


class TestConsultSeedAndWiring(TestCase):
    """Mapping par défaut seedé aligné Workflow + hooks réellement branchés."""

    def test_default_role_scopes_seeded_workflow_aligned(self):
        here = os.path.dirname(__file__)
        jf = os.path.join(here, "..", "admission", "doctype", "admission_settings", "admission_settings.json")
        doc = json.load(open(jf))
        field = next(f for f in doc["fields"] if f["fieldname"] == "consultation_role_scopes")
        seeded = json.loads(field["default"])
        self.assertEqual(seeded, WORKFLOW_SCOPES)
        # OFF par défaut
        flag = next(f for f in doc["fields"] if f["fieldname"] == "consultation_cloisonnee")
        self.assertIn(str(flag.get("default", "0")), ("0", "", "None"))

    def test_hooks_wired_for_admission_applicant(self):
        import admission.hooks as h
        self.assertEqual(
            h.permission_query_conditions.get("Admission Applicant"),
            "admission.api.permissions.get_permission_query_conditions",
        )
        self.assertEqual(
            h.has_permission.get("Admission Applicant"),
            "admission.api.permissions.has_permission",
        )
