"""Tests C4-FRONT — endpoints d'appoint lecture (whoami / list_dossiers / get_dossier /
download_receipt / stats_direction).

Le front REFLÈTE la sécurité serveur : chaque endpoint reste role-gardé ici. Les listes
passent par frappe.get_list (DocPerms + cloisonnement DEC-262), le détail par
check_permission. download_receipt = ARGENT (reçu PDF, Confirmed only, même gabarit que
le mail candidat). Style unitaire mocké, aligné suite existante.
"""

import json
import types
from unittest import TestCase
from unittest.mock import MagicMock, patch

STAFF = "admission.api.staff"

STAFF_ROLES_T = ("Admission Administratif", "Admission Responsable",
                 "Admission Direction", "System Manager")


def _patches():
    return (
        patch(f"{STAFF}._ok", side_effect=lambda d: {"ok": True, "data": d, "error": None}),
        patch(f"{STAFF}._error", side_effect=lambda c, m, s=400: {"ok": False, "data": None, "error": {"code": c}}),
    )


class TestWhoami(TestCase):
    def test_returns_roles_intersection_and_csrf(self):
        ok, err = _patches()
        with patch(f"{STAFF}.frappe") as mf, ok, err:
            mf.session.user = "estelle@lanem.bj"
            mf.get_roles.return_value = ["Admission Administratif", "Employee", "All"]
            mf.db.get_value.return_value = "Estelle Gbaguidi"
            mf.sessions.get_csrf_token.return_value = "csrf-123"
            from admission.api.staff import whoami
            res = whoami()
            mf.only_for.assert_called_once_with(STAFF_ROLES_T)
        self.assertEqual(res["data"]["roles"], ["Admission Administratif"])  # ∩ rôles admission
        self.assertEqual(res["data"]["csrf_token"], "csrf-123")
        self.assertEqual(res["data"]["full_name"], "Estelle Gbaguidi")


class TestListDossiers(TestCase):
    def _row(self, **kw):
        base = {"name": "CAN-1", "applicant_name": "Ama K", "programme_code": "LIS",
                "programme_label": "Licence", "level_code": "LIS-L1", "session": "SES-1",
                "status": "ETU", "conditionnel": 0, "bac_verified": 0,
                "requested_scholarships": None, "proposed_scholarships": None,
                "validated_scholarships": None, "notes_concours": None, "notes_validated": 0,
                "rang_liste_attente": None,
                "creation": "2026-06-01", "modified": "2026-06-02"}
        base.update(kw)
        return types.SimpleNamespace(**base)

    def test_uses_get_list_for_permissions(self):
        # IMPÉRATIF DEC-262 : la liste passe par get_list (perms + cloisonnement), pas get_all
        ok, err = _patches()
        with patch(f"{STAFF}.frappe") as mf, ok, err:
            mf.get_list.return_value = [self._row()]
            mf.get_all.return_value = []
            mf.db.get_value.return_value = 0
            from admission.api.staff import list_dossiers
            res = list_dossiers()
            mf.get_list.assert_called_once()
        self.assertEqual(res["data"]["dossiers"][0]["dossier_id"], "CAN-1")
        self.assertEqual(res["data"]["dossiers"][0]["bourse"], "aucune")

    def test_bourse_and_notes_states(self):
        ok, err = _patches()
        with patch(f"{STAFF}.frappe") as mf, ok, err:
            mf.get_list.return_value = [self._row(
                requested_scholarships='["B-A"]', proposed_scholarships='["B-A"]',
                notes_concours='{"Maths": 14}', notes_validated=0)]
            mf.get_all.return_value = []
            mf.db.get_value.return_value = 1  # session prépa
            from admission.api.staff import list_dossiers
            res = list_dossiers()
        d = res["data"]["dossiers"][0]
        self.assertEqual(d["bourse"], "proposee")   # proposee > demandee
        self.assertEqual(d["notes"], "saisies")
        self.assertTrue(d["is_prepa"])

    def test_search_filters_rows(self):
        ok, err = _patches()
        with patch(f"{STAFF}.frappe") as mf, ok, err:
            mf.get_list.return_value = [self._row(), self._row(name="CAN-2", applicant_name="Zo B")]
            mf.get_all.return_value = []
            mf.db.get_value.return_value = 0
            from admission.api.staff import list_dossiers
            res = list_dossiers(q="ama")
        self.assertEqual(len(res["data"]["dossiers"]), 1)


class TestGetDossierStaff(TestCase):
    def test_checks_read_permission(self):
        # check_permission explicite → compatible cloisonnement DEC-262
        ok, err = _patches()
        with patch(f"{STAFF}.frappe") as mf, ok, err:
            app = MagicMock()
            app.name = "CAN-1"
            app.session = None
            app.pieces = []
            app.notes_concours = None
            app.requested_scholarships = None
            app.proposed_scholarships = None
            app.validated_scholarships = None
            app.promo_rate = 0
            app.acompte_xof = 0
            mf.db.exists.return_value = True
            mf.get_doc.return_value = app
            mf.get_all.return_value = []
            from admission.api.staff import get_dossier
            res = get_dossier(dossier_id="CAN-1")
            app.check_permission.assert_called_once_with("read")
        self.assertTrue(res["ok"])

    def test_invalid_dossier(self):
        ok, err = _patches()
        with patch(f"{STAFF}.frappe") as mf, ok, err:
            mf.db.exists.return_value = False
            from admission.api.staff import get_dossier
            res = get_dossier(dossier_id="CAN-X")
        self.assertEqual(res["error"]["code"], "INVALID_DOSSIER")


class TestDownloadReceipt(TestCase):
    def _run(self, status="Confirmed"):
        ok, err = _patches()
        with patch(f"{STAFF}.frappe") as mf, ok, err, \
             patch("frappe.utils.pdf.get_pdf", return_value=b"%PDF"), \
             patch("admission.api.receipt._get_legal_text", return_value=""), \
             patch("admission.api.receipt.render_receipt_html", return_value="<html>"):
            payment = MagicMock()
            payment.name = "AFP-1"
            payment.payment_status = status
            payment.applicant = "CAN-1"
            payment.applicant_fee = None
            applicant = MagicMock()
            mf.db.exists.return_value = True
            mf.get_doc.side_effect = lambda dt, name: payment if dt == "Applicant Fee Payment" else applicant
            from admission.api.staff import download_receipt
            res = download_receipt(payment_id="AFP-1")
            return res, mf, applicant

    def test_confirmed_payment_streams_pdf(self):
        res, mf, applicant = self._run("Confirmed")
        applicant.check_permission.assert_called_once_with("read")  # DEC-262
        self.assertEqual(mf.local.response.filename, "recu-AFP-1.pdf")
        self.assertEqual(mf.local.response.type, "pdf")
        self.assertEqual(mf.local.response.filecontent, b"%PDF")

    def test_pending_payment_rejected(self):
        # ARGENT : pas de reçu pour un paiement non confirmé
        res, mf, _ = self._run("Pending")
        self.assertEqual(res["error"]["code"], "NOT_CONFIRMED")


class TestStatsDirection(TestCase):
    def test_direction_only_and_shape(self):
        ok, err = _patches()
        with patch(f"{STAFF}.frappe") as mf, ok, err:
            mf.db.sql.side_effect = [
                [("SOU", 5), ("INS", 2)],          # par statut
                [("LIS", 6), ("PRE", 1)],          # par programme
                [("application", 175000.0)],       # encaissé
                [("SES-1", 2)],                     # INS par session
            ]
            mf.get_all.return_value = [frappe_dict({"name": "SES-1", "label": "Oct 2026",
                                                    "academic_year": "2026-2027",
                                                    "programme_code": "LIS", "is_open": 1,
                                                    "opens_on": None, "closes_on": None})]
            from admission.api.staff import stats_direction
            res = stats_direction()
            mf.only_for.assert_called_once_with(("Admission Direction", "System Manager"))
        self.assertEqual(res["data"]["par_statut"]["SOU"], 5)
        self.assertEqual(res["data"]["encaisse_xof"]["application"], 175000.0)
        self.assertEqual(res["data"]["sessions"][0]["inscrits"], 2)


class frappe_dict(dict):
    """Mini _dict : accès attribut + item (comme frappe._dict) pour les rows mockées."""
    def __getattr__(self, k):
        try:
            return self[k]
        except KeyError as e:
            raise AttributeError(k) from e

    def __setattr__(self, k, v):
        self[k] = v
