"""FIX-ROLES-HYBRIDE-WORKFLOW — modèle HYBRIDE (révise FIX-ROLES-HIERARCHIE) sur la couche 1a (only_for).

Matrice AS CHAQUE VRAI ROLE (frappe.set_user + in_test=False → only_for actif) :
  · OPÉRATIONNEL (start_review…) = ASCENDANT : Admin + Resp + Dir PASSED (continuité) ;
  · DÉCISION « maker » (mark_admissible, refuse@ETU…) = EXACT Responsable : Resp PASSED, Admin ET
    **Direction REJETÉS** (séparation maker-checker / SoD — la Direction ne DÉCIDE pas) ;
  · VALIDATION « checker » (accept_admission, enroll…) = EXACT Direction : Dir PASSED, Admin/Resp rejetés.
Les actions AUTORISÉES échouent plus loin sur l'état (PASSED, 0 mutation) ; les NON autorisées lèvent
PermissionError (DENIED). La couche 1b (Workflow) est prouvée end-to-end par test_available_actions.
"""

import frappe
from frappe.tests.utils import FrappeTestCase

from admission.api import staff as S
from admission.api.permissions import roles_at_or_above

ADMIN = "hier-admin@lanem.test"
RESP = "hier-resp@lanem.test"
DIR = "hier-dir@lanem.test"
ROLE_OF = {ADMIN: "Admission Administratif", RESP: "Admission Responsable", DIR: "Admission Direction"}


class TestRolesHierarchyHelper(FrappeTestCase):
    def test_expansion_sets_exact(self):
        self.assertEqual(roles_at_or_above("Admission Administratif"),
                         ("Admission Administratif", "Admission Responsable", "Admission Direction", "System Manager"))
        self.assertEqual(roles_at_or_above("Admission Responsable"),
                         ("Admission Responsable", "Admission Direction", "System Manager"))
        self.assertEqual(roles_at_or_above("Admission Direction"),
                         ("Admission Direction", "System Manager"))

    def test_sm_orthogonal_sysmgr_present(self):
        for lvl in ("Admission Administratif", "Admission Responsable", "Admission Direction"):
            expanded = roles_at_or_above(lvl)
            self.assertNotIn("Admission SM", expanded)
            self.assertIn("System Manager", expanded)


class TestRolesHierarchyMatrix(FrappeTestCase):
    def _clean(self):
        frappe.set_user("Administrator")
        for e in (ADMIN, RESP, DIR):
            if frappe.db.exists("User", e):
                frappe.delete_doc("User", e, force=True, ignore_permissions=True)
        if getattr(self, "dossier", None) and frappe.db.exists("Admission Applicant", self.dossier):
            frappe.delete_doc("Admission Applicant", self.dossier, force=True, ignore_permissions=True)
        frappe.db.commit()

    def setUp(self):
        self._clean()
        for e in (ADMIN, RESP, DIR):
            frappe.get_doc({"doctype": "User", "email": e, "first_name": e.split("-")[1],
                            "send_welcome_email": 0, "enabled": 1,
                            "roles": [{"role": ROLE_OF[e]}]}).insert(ignore_permissions=True)
        sessions = frappe.get_all("Admission Session", limit=1, pluck="name")
        if not sessions:
            self.skipTest("Aucune Admission Session seedee (decor requis).")
        self.session = sessions[0]
        app = frappe.get_doc({
            "doctype": "Admission Applicant", "status": "BRO",   # etat initial du workflow ; aucune action testee n'agit sur BRO (=> pas de mutation)
            "first_name": "Hier", "last_name": "Test", "email": "hier-app@lanem.test",
            "phone": "+2290160000000", "programme_code": "PREPA", "level_code": "PREPA-S1",
            "session": self.session,
        }).insert(ignore_permissions=True)
        self.dossier = app.name
        frappe.db.commit()                                       # decor persiste malgre le rollback de _gate
        self.addCleanup(self._clean)

    def _gate(self, user, fn, **kwargs):
        frappe.set_user(user)
        frappe.flags.in_test = False
        try:
            try:
                fn(**kwargs)
                return "PASSED"
            except frappe.PermissionError:
                return "DENIED"
            except Exception:
                return "PASSED"                                  # franchi la garde ; echoue plus loin (etat)
        finally:
            frappe.flags.in_test = True
            frappe.set_user("Administrator")
            frappe.db.rollback()

    def test_GH1_direction_operational_yes_maker_NO_checker_yes(self):
        # opérationnel ascendant : Dir PASSED
        self.assertEqual(self._gate(DIR, S.start_review, dossier_id=self.dossier), "PASSED")
        # MAKER : la Direction ne DÉCIDE pas → REJETÉE (SoD — le cœur du modèle hybride)
        self.assertEqual(self._gate(DIR, S.mark_admissible, dossier_id=self.dossier), "DENIED")
        # checker : Dir PASSED
        self.assertEqual(self._gate(DIR, S.accept_admission, dossier_id=self.dossier), "PASSED")
        self.assertEqual(self._gate(DIR, S.enroll, dossier_id=self.dossier), "PASSED")
        self.assertEqual(self._gate(DIR, S.close_session, session=self.session), "PASSED")

    def test_GH2_responsable_operational_yes_maker_yes_checker_NO(self):
        self.assertEqual(self._gate(RESP, S.start_review, dossier_id=self.dossier), "PASSED")     # opérationnel
        self.assertEqual(self._gate(RESP, S.mark_admissible, dossier_id=self.dossier), "PASSED")  # maker
        for fn in (S.accept_admission, S.enroll):
            self.assertEqual(self._gate(RESP, fn, dossier_id=self.dossier), "DENIED", fn.__name__)  # checker → refusé
        self.assertEqual(self._gate(RESP, S.close_session, session=self.session), "DENIED")

    def test_GH3_administratif_operational_yes_no_maker_no_checker(self):
        self.assertEqual(self._gate(ADMIN, S.start_review, dossier_id=self.dossier), "PASSED")    # opérationnel
        self.assertEqual(self._gate(ADMIN, S.mark_admissible, dossier_id=self.dossier), "DENIED") # maker → refusé
        for fn in (S.accept_admission, S.enroll):
            self.assertEqual(self._gate(ADMIN, fn, dossier_id=self.dossier), "DENIED", fn.__name__)  # checker → refusé
        self.assertEqual(self._gate(ADMIN, S.close_session, session=self.session), "DENIED")

    def test_GH4_refuse_maker_checker_by_state(self):
        # @ETU (maker EXACT Resp) : Resp PASSED ; Admin ET Direction REJETÉS (Dir ne décide pas — SoD)
        frappe.db.set_value("Admission Applicant", self.dossier, "status", "ETU"); frappe.db.commit()
        self.assertEqual(self._gate(ADMIN, S.refuse, dossier_id=self.dossier), "DENIED")
        self.assertEqual(self._gate(RESP, S.refuse, dossier_id=self.dossier), "PASSED")
        self.assertEqual(self._gate(DIR, S.refuse, dossier_id=self.dossier), "DENIED")   # ← hybride : Dir NE refuse PAS en ETU
        # @ADM (checker EXACT Dir) : Dir PASSED ; Resp/Admin rejetés
        frappe.db.set_value("Admission Applicant", self.dossier, "status", "ADM"); frappe.db.commit()
        self.assertEqual(self._gate(ADMIN, S.refuse, dossier_id=self.dossier), "DENIED")
        self.assertEqual(self._gate(RESP, S.refuse, dossier_id=self.dossier), "DENIED")
        self.assertEqual(self._gate(DIR, S.refuse, dossier_id=self.dossier), "PASSED")
