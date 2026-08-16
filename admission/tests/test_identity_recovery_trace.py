"""Traceur dev DEC-323 : données réelles → Redis OTP → liste → détail → purge."""

import uuid
from types import SimpleNamespace
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase


PUB = "admission.api.public"


def residue_probe():
    """Sonde de dossier de preuves : toujours imprime un objet, même quand le compte vaut 0."""
    names = frappe.get_all(
        "Admission Applicant",
        filters={"email": ["like", "trace-identite-%@e2e.lanem.test"]},
        pluck="name",
    )
    return {"count": len(names), "names": names}


class TestIdentityRecoveryEndToEnd(FrappeTestCase):
    def setUp(self):
        self.created = []
        self.redis_keys = []
        frappe.local.response = {}
        frappe.local.request_ip = "127.0.0.1"

    def tearDown(self):
        if self.redis_keys:
            frappe.cache.delete(*self.redis_keys)
        for name in reversed(self.created):
            if frappe.db.exists("Admission Applicant", name):
                frappe.delete_doc(
                    "Admission Applicant", name, force=True, ignore_permissions=True,
                )
        # Le runner Frappe rollbacke la transaction de test ; la purge doit au contraire
        # survivre au run pour garantir zéro objet de preuve résiduel.
        frappe.db.commit()

    def _insert(self, session, email, suffix, date_of_birth=None):
        from admission.api.public import _hash

        doc = frappe.get_doc({
            "doctype": "Admission Applicant",
            "status": "BRO",
            "first_name": "Trace",
            "last_name": suffix,
            "email": email,
            "phone": "+22990000000",
            "date_of_birth": date_of_birth,
            "programme_code": session.programme_code,
            "programme_label": session.programme_label,
            "level_code": "TRACE-L1",
            "session": session.name,
            "person_id": f"TRACE-PERSON-{suffix}",
            "dossier_token_hash": _hash(f"historical-token-{suffix}"),
        }).insert(ignore_permissions=True)
        self.created.append(doc.name)
        return doc

    def _create_through_endpoint(self, session, level_code, email):
        """Joue le vrai create_dossier jusqu'à l'insert ; seules les dépendances externes sont isolées."""
        from admission.api.public import create_dossier

        previous_request = getattr(frappe.local, "request", None)
        previous_form = getattr(frappe.local, "form_dict", frappe._dict())
        frappe.local.request = None
        frappe.local.form_dict = frappe._dict({
            "session": session.name,
            "level_code": level_code,
            "identite": {
                "prenom": "Trace", "nom": "DEPOT", "email": email,
                "tel": "+22990000001", "date_of_birth": "2000-01-02",
                "date_bac": "2024-06-01",
            },
            "consent_data_processing": 1,
            "consent_cgv": 1,
            "idempotency_key": f"trace-identity-{uuid.uuid4().hex}",
        })
        legal = SimpleNamespace(name="LEGAL-TRACE")
        try:
            with patch("admission.api.sessions.is_session_selectable", return_value=True), \
                 patch(f"{PUB}._resolve_person_from_campus", return_value="TRACE-PERSON-PRIMARY"), \
                 patch(f"{PUB}._ensure_fee"), \
                 patch(f"{PUB}._classify_bac_date", return_value="bac_anterieur"), \
                 patch(f"{PUB}._pieces_for_profile", return_value=[]), \
                 patch(f"{PUB}._sync_pieces"), \
                 patch("admission.api.legal._get_active_legal_document", return_value=legal), \
                 patch("admission.api.legal._record_consent"), \
                 patch("admission.api.notifications.send_account_created"), \
                 patch(f"{PUB}.frappe.enqueue"):
                result = create_dossier()
        finally:
            frappe.local.request = previous_request
            frappe.local.form_dict = previous_form
        self.assertTrue(result["ok"], result)
        self.created.append(result["data"]["dossier_id"])
        return frappe.get_doc("Admission Applicant", result["data"]["dossier_id"])

    def test_two_dossiers_active_and_closed_are_consulted_without_token_rotation(self):
        from admission.api.public import (
            _identity_recovery_key,
            get_recovered_dossier,
            send_identity_recovery_otp,
            verify_recovery_otp,
        )

        sessions = frappe.get_all(
            "Admission Session",
            fields=["name", "programme_code", "programme_label"],
            limit_page_length=500,
        )
        session = level = None
        for candidate in sessions:
            levels = frappe.get_all(
                "Admission Level Mirror",
                filters={"program_code": candidate.programme_code},
                fields=["level_code", "program_code"], limit_page_length=1,
            )
            if levels:
                session, level = candidate, levels[0]
                break
        self.assertIsNotNone(session, "Aucune paire session/niveau disponible pour le traceur")
        email = f"trace-identite-{uuid.uuid4().hex}@e2e.lanem.test"
        active = self._create_through_endpoint(session, level.level_code, email)
        self.assertEqual(str(frappe.db.get_value("Admission Applicant", active.name, "date_of_birth")), "2000-01-02")
        historical = self._insert(session, email, "HISTORIQUE")  # DOB historique vide accepté
        closed = self._insert(session, email, "CLOS", "1999-05-06")
        frappe.db.set_value("Admission Applicant", closed.name, "status", "REF", update_modified=False)
        frappe.db.commit()

        hashes_before = {
            row.name: row.dossier_token_hash for row in frappe.get_all(
                "Admission Applicant", filters={"name": ["in", [active.name, historical.name, closed.name]]},
                fields=["name", "dossier_token_hash"],
            )
        }

        with patch(f"{PUB}._generate_otp", return_value="654321"), \
             patch("admission.api.notifications.send_email_otp") as send_email:
            send_identity_recovery_otp(email)
        send_email.assert_called_once()

        cache = frappe.cache
        otp_key = _identity_recovery_key(cache, "otp", email)
        attempts_key = _identity_recovery_key(cache, "attempts", email)
        self.redis_keys.extend([otp_key, attempts_key])

        # Les limites sont testées séparément ; ce traceur isole le parcours métier.
        with patch(f"{PUB}._check_identity_rate_limits", return_value=None), \
             patch(f"{PUB}._generate_token", return_value=f"trace-{uuid.uuid4().hex}"):
            verified = verify_recovery_otp(email=email, otp="654321")
        self.assertTrue(verified["ok"], verified)
        items = verified["data"]["dossiers"]
        self.assertEqual(
            {item["dossier_id"] for item in items},
            {active.name, historical.name, closed.name},
        )
        self.assertEqual({item["statut"] for item in items}, {"BRO", "REF"})

        recovery_token = verified["data"]["recovery_token"]
        session_key = _identity_recovery_key(cache, "session", recovery_token)
        self.redis_keys.append(session_key)
        detail = get_recovered_dossier(recovery_token=recovery_token, dossier_id=active.name)
        self.assertTrue(detail["ok"], detail)
        self.assertEqual(detail["data"]["identite"]["date_naissance"], "2000-01-02")
        refused = get_recovered_dossier(recovery_token=recovery_token, dossier_id="CAN-HORS-LISTE")
        self.assertEqual(refused["error"]["code"], "RECOVERY_DOSSIER_FORBIDDEN")

        hashes_after = {
            row.name: row.dossier_token_hash for row in frappe.get_all(
                "Admission Applicant", filters={"name": ["in", [active.name, historical.name, closed.name]]},
                fields=["name", "dossier_token_hash"],
            )
        }
        self.assertEqual(hashes_after, hashes_before)  # aucun lien historique tourné
        self.assertFalse(cache.exists(otp_key))        # OTP réellement consommé
