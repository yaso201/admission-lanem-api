"""FIX-ROLES-HIERARCHIE — modele B ascendant : Direction ⊇ Responsable ⊇ Administratif.

Matrice AS CHAQUE VRAI ROLE (frappe.set_user + in_test=False → only_for actif). Invariant SACRE :
ascendant seulement, 0 fuite descendante (un subordonne est REJETE sur les actions sensibles d'un
superieur). Statut neutre 'REF' + refuse sans motif → les actions AUTORISEES echouent sur l'etat
APRES only_for (PASSED, aucune mutation) ; les NON autorisees levent PermissionError (DENIED).
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

    def test_GH1_direction_covers_admin_resp_dir(self):
        self.assertEqual(self._gate(DIR, S.start_review, dossier_id=self.dossier), "PASSED")      # Admin
        self.assertEqual(self._gate(DIR, S.mark_admissible, dossier_id=self.dossier), "PASSED")   # Resp
        self.assertEqual(self._gate(DIR, S.accept_admission, dossier_id=self.dossier), "PASSED")  # Dir
        self.assertEqual(self._gate(DIR, S.enroll, dossier_id=self.dossier), "PASSED")            # Dir
        self.assertEqual(self._gate(DIR, S.close_session, session=self.session), "PASSED")        # Dir

    def test_GH2_responsable_admin_resp_denied_direction(self):
        self.assertEqual(self._gate(RESP, S.start_review, dossier_id=self.dossier), "PASSED")     # Admin
        self.assertEqual(self._gate(RESP, S.mark_admissible, dossier_id=self.dossier), "PASSED")  # Resp
        for fn in (S.accept_admission, S.enroll):
            self.assertEqual(self._gate(RESP, fn, dossier_id=self.dossier), "DENIED", fn.__name__)  # Dir sensibles
        self.assertEqual(self._gate(RESP, S.close_session, session=self.session), "DENIED")

    def test_GH3_administratif_only_admin_no_downward_leak(self):
        self.assertEqual(self._gate(ADMIN, S.start_review, dossier_id=self.dossier), "PASSED")    # Admin
        self.assertEqual(self._gate(ADMIN, S.mark_admissible, dossier_id=self.dossier), "DENIED") # Resp sensible
        for fn in (S.accept_admission, S.enroll):
            self.assertEqual(self._gate(ADMIN, fn, dossier_id=self.dossier), "DENIED", fn.__name__)  # Dir sensibles
        self.assertEqual(self._gate(ADMIN, S.close_session, session=self.session), "DENIED")

    def test_GH4_refuse_branched_by_state(self):
        # @ETU → RESP_UP : Resp/Dir passent, Admin rejete (refuse sans motif → 0 mutation, MOTIF_REQUIRED apres only_for)
        frappe.db.set_value("Admission Applicant", self.dossier, "status", "ETU"); frappe.db.commit()
        self.assertEqual(self._gate(ADMIN, S.refuse, dossier_id=self.dossier), "DENIED")
        self.assertEqual(self._gate(RESP, S.refuse, dossier_id=self.dossier), "PASSED")
        self.assertEqual(self._gate(DIR, S.refuse, dossier_id=self.dossier), "PASSED")
        # @ADM → DIR_UP : Dir passe, Resp/Admin rejetes
        frappe.db.set_value("Admission Applicant", self.dossier, "status", "ADM"); frappe.db.commit()
        self.assertEqual(self._gate(ADMIN, S.refuse, dossier_id=self.dossier), "DENIED")
        self.assertEqual(self._gate(RESP, S.refuse, dossier_id=self.dossier), "DENIED")
        self.assertEqual(self._gate(DIR, S.refuse, dossier_id=self.dossier), "PASSED")
