"""REPRISE-DOSSIER (DEC-332/333) — jeton d'édition mono-dossier après OTP identité.

Couvre le contrat complet du GO architecte :
- condition 1 : le claim se vérifie côté SERVEUR (session de consultation valide +
  appartenance du dossier à l'identité + état modifiable) — trois contrôles, aucun raccourci ;
- condition 2 : l'état est revérifié à CHAQUE écriture (un BRO passé SOP en cours de
  session d'édition voit ses écritures refusées), exceptions NOMINATIVES des flux conçus
  (3c : SOU pièce rejetée/à-fournir ; C1-ACO : diplôme du bac conditionnel) ;
- condition 3 : le claim est journalisé (dossier + identité non-PII + horodatage) ;
- DEC-333 : le jeton émis est un jeton de dossier ORDINAIRE (rotation, portée un dossier) ;
  otp_verified est ALIMENTÉ (pas assoupli) par l'OTP d'identité ;
- front pur renderer : `reprenable` est servi par le back (résumés + détail consultation) ;
- catch bavard (V-LEARN-CAL-03) : les catch INVALID_DOSSIER journalisent la cause réelle,
  réponse générique inchangée (anti-énumération), jamais le jeton en clair.

Tests sur base RÉELLE (dossiers insérés puis PURGÉS en tearDown — V-LEARN-PURGE-14) et
Redis réel du bench pour la session de consultation (patron TestRecoveryOtpRedisAtomicity).
"""

import json
import uuid
from datetime import timedelta
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_days, get_datetime, now_datetime

from admission.api import public
from admission.api.public import (
    IDENTITY_RECOVERY_SESSION_TTL_SECONDS,
    _generate_token,
    _hash,
    _identity_recovery_key,
)

PUB = "admission.api.public"


def _any_session():
    name = frappe.get_all("Admission Session", pluck="name", limit_page_length=1)
    if not name:
        raise AssertionError("Aucune Admission Session sur le site de test — pré-requis fixture.")
    return name[0]


class _ClaimBase(FrappeTestCase):
    """Fixtures réelles : dossiers jetables (e-mail unique par test) + session Redis réelle."""

    def setUp(self):
        frappe.local.response = {}
        frappe.local.request_ip = f"test-claim-{uuid.uuid4().hex[:12]}"
        self.email = f"claim-{uuid.uuid4().hex[:10]}@test.invalid"
        self.session_name = _any_session()
        self._created = []
        self._redis_keys = []

    def tearDown(self):
        for name in self._created:
            for log in frappe.get_all(
                "Admission Applicant Transition Log", filters={"applicant": name}, pluck="name"
            ):
                frappe.delete_doc(
                    "Admission Applicant Transition Log", log, force=True, ignore_permissions=True
                )
            for fee in frappe.get_all(
                "Applicant Fee Payment", filters={"applicant": name}, pluck="name"
            ):
                frappe.delete_doc("Applicant Fee Payment", fee, force=True, ignore_permissions=True)
            if frappe.db.exists("Admission Applicant", name):
                frappe.delete_doc("Admission Applicant", name, force=True, ignore_permissions=True)
        for key in self._redis_keys:
            frappe.cache.delete(key)
        frappe.db.commit()
        # Preuve de purge : plus AUCUN dossier de l'e-mail jetable.
        self.assertEqual(
            frappe.db.count("Admission Applicant", {"email": self.email}), 0,
            "purge tearDown incomplète",
        )

    def _mk(self, status="BRO", pieces=None, otp_verified=0, **over):
        """Insère un dossier réel avec jeton connu ; retourne (name, token).

        Insert en BRO (état initial Workflow) puis db.set_value vers l'état cible —
        l'insert direct hors-BRO déclencherait WorkflowPermissionError (transition refusée).
        """
        token = _generate_token()
        doc = frappe.get_doc({
            "doctype": "Admission Applicant",
            "status": "BRO",
            "first_name": "Claim",
            "last_name": f"Test{len(self._created)}",
            "email": self.email,
            "phone": "+22990000000",
            "programme_code": "LIC",
            "level_code": "L1",
            "session": self.session_name,
            "dossier_token_hash": _hash(token),
            "token_expires_at": add_days(now_datetime(), 7),
            "otp_verified": otp_verified,
            **over,
        })
        for piece in pieces or []:
            doc.append("pieces", piece)
        doc.insert(ignore_permissions=True)
        if status != "BRO":
            frappe.db.set_value("Admission Applicant", doc.name, "status", status,
                                update_modified=False)
        frappe.db.commit()
        self._created.append(doc.name)
        return doc.name, token

    def _open_session(self, names):
        """Sème une session de consultation réelle en Redis (même clé que verify_recovery_otp)."""
        recovery_token = _generate_token()
        key = _identity_recovery_key(frappe.cache, "session", recovery_token)
        frappe.cache.setex(key, IDENTITY_RECOVERY_SESSION_TTL_SECONDS, json.dumps(names))
        self._redis_keys.append(key)
        return recovery_token


class TestClaimRecoveredDossier(_ClaimBase):
    def test_claim_bro_emits_rotated_edit_token_and_feeds_invariant(self):
        name, old_token = self._mk("BRO")
        recovery = self._open_session([name])
        result = public.claim_recovered_dossier(recovery_token=recovery, dossier_id=name)
        self.assertTrue(result["ok"], result)
        data = result["data"]
        self.assertEqual(data["dossier_id"], name)
        self.assertEqual(data["statut"], "BRO")
        new_token = data["token"]
        self.assertTrue(new_token and new_token != old_token)
        row = frappe.db.get_value(
            "Admission Applicant", name,
            ["dossier_token_hash", "otp_verified", "otp_verified_at", "token_expires_at"],
            as_dict=True,
        )
        self.assertEqual(row.dossier_token_hash, _hash(new_token))          # rotation effective
        self.assertEqual(int(row.otp_verified), 1)                          # invariant ALIMENTÉ
        self.assertIsNotNone(row.otp_verified_at)
        self.assertGreater(get_datetime(row.token_expires_at),
                           now_datetime() + timedelta(days=6))

    def test_claim_inc_allowed(self):
        name, _ = self._mk("INC")
        recovery = self._open_session([name])
        result = public.claim_recovered_dossier(recovery_token=recovery, dossier_id=name)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["data"]["statut"], "INC")

    def test_claim_read_only_states_refused_without_side_effect(self):
        for status in ("SOP", "SOU", "ETU", "ADM", "ACC"):
            name, _ = self._mk(status)
            hash_before = frappe.db.get_value("Admission Applicant", name, "dossier_token_hash")
            recovery = self._open_session([name])
            result = public.claim_recovered_dossier(recovery_token=recovery, dossier_id=name)
            self.assertFalse(result["ok"], status)
            self.assertEqual(result["error"]["code"], "STATE_READ_ONLY", status)
            after = frappe.db.get_value(
                "Admission Applicant", name, ["dossier_token_hash", "otp_verified"], as_dict=True
            )
            self.assertEqual(after.dossier_token_hash, hash_before, status)   # aucune rotation
            self.assertEqual(int(after.otp_verified), 0, status)              # invariant intact

    def test_claim_outside_allowlist_refused(self):
        mine, _ = self._mk("BRO")
        other, _ = self._mk("BRO")
        recovery = self._open_session([mine])   # la session ne couvre QUE `mine`
        result = public.claim_recovered_dossier(recovery_token=recovery, dossier_id=other)
        self.assertEqual(result["error"]["code"], "RECOVERY_DOSSIER_FORBIDDEN")

    def test_claim_without_valid_consultation_session_refused(self):
        name, _ = self._mk("BRO")
        result = public.claim_recovered_dossier(recovery_token="jamais-emis", dossier_id=name)
        self.assertEqual(result["error"]["code"], "RECOVERY_SESSION_INVALID")
        missing = public.claim_recovered_dossier(recovery_token=None, dossier_id=name)
        self.assertEqual(missing["error"]["code"], "RECOVERY_SESSION_INVALID")

    def test_claim_anonymized_dossier_unavailable(self):
        name, _ = self._mk("BRO", anonymized=1)
        recovery = self._open_session([name])
        result = public.claim_recovered_dossier(recovery_token=recovery, dossier_id=name)
        self.assertEqual(result["error"]["code"], "INVALID_DOSSIER")

    def test_claim_kills_previous_token_and_new_token_works(self):
        name, old_token = self._mk("BRO")
        recovery = self._open_session([name])
        new_token = public.claim_recovered_dossier(
            recovery_token=recovery, dossier_id=name
        )["data"]["token"]
        dead = public.get_dossier(dossier_id=name, token=old_token)
        self.assertEqual(dead["error"]["code"], "INVALID_DOSSIER")   # l'ancien lien meurt
        alive = public.get_dossier(dossier_id=name, token=new_token)
        self.assertTrue(alive["ok"], alive)

    def test_edit_token_scope_is_single_dossier(self):
        a, _ = self._mk("BRO")
        b, _ = self._mk("BRO")
        recovery = self._open_session([a, b])
        token_a = public.claim_recovered_dossier(recovery_token=recovery, dossier_id=a)["data"]["token"]
        crossed = public.get_dossier(dossier_id=b, token=token_a)
        self.assertEqual(crossed["error"]["code"], "INVALID_DOSSIER")   # jamais le dossier d'autrui

    def test_claim_is_journalised_without_pii(self):
        name, _ = self._mk("BRO")
        recovery = self._open_session([name])
        with patch(f"{PUB}.log_event") as log:
            public.claim_recovered_dossier(recovery_token=recovery, dossier_id=name)
        calls = [c for c in log.call_args_list if c.args[:2] == ("claim_recovered_dossier", "success")]
        self.assertEqual(len(calls), 1)
        kwargs = calls[0].kwargs
        self.assertEqual(kwargs["dossier_id"], name)
        self.assertTrue(kwargs.get("identity"))                      # trace d'identité…
        self.assertNotIn(self.email, str(kwargs["identity"]))        # …jamais l'e-mail en clair


class TestReprenableServedByBack(_ClaimBase):
    """Front pur renderer (patron FIX-PROGRESSION) : la règle d'états vit au back."""

    def test_summaries_carry_reprenable_flag(self):
        bro, _ = self._mk("BRO")
        inc, _ = self._mk("INC")
        sou, _ = self._mk("SOU")
        ref, _ = self._mk("REF")
        flags = {
            row["dossier_id"]: row["reprenable"]
            for row in public._identity_recovery_summaries([bro, inc, sou, ref])
        }
        self.assertEqual(flags, {bro: True, inc: True, sou: False, ref: False})

    def test_recovered_detail_carries_reprenable_flag(self):
        bro, _ = self._mk("BRO")
        sou, _ = self._mk("SOU")
        recovery = self._open_session([bro, sou])
        detail_bro = public.get_recovered_dossier(recovery_token=recovery, dossier_id=bro)
        detail_sou = public.get_recovered_dossier(recovery_token=recovery, dossier_id=sou)
        self.assertTrue(detail_bro["data"]["reprenable"])
        self.assertFalse(detail_sou["data"]["reprenable"])


class TestWriteStateReverified(_ClaimBase):
    """Condition 2 du GO : la garde d'état joue à CHAQUE écriture, pas seulement au claim."""

    _PIECE = {"piece_code": "releves_terminale", "label": "Relevés", "required": 1, "status": "missing"}

    def test_upload_refused_when_dossier_left_editable_states(self):
        for status in ("SOP", "ETU", "ADM", "ACC", "REF"):
            name, token = self._mk(status, pieces=[dict(self._PIECE)], otp_verified=1)
            result = public.upload_piece_file(
                dossier_id=name, token=token, piece_code="releves_terminale"
            )
            self.assertEqual(result["error"]["code"], "STATE_READ_ONLY", status)

    def test_upload_gate_open_in_bro_and_inc(self):
        for status in ("BRO", "INC"):
            name, token = self._mk(status, pieces=[dict(self._PIECE)], otp_verified=1)
            result = public.upload_piece_file(
                dossier_id=name, token=token, piece_code="releves_terminale"
            )
            # Pas de fichier multipart en test unitaire : l'erreur suivante prouve
            # que la garde d'état a LAISSÉ PASSER (fail-fast ordonné état → fichier).
            self.assertEqual(result["error"]["code"], "PIECE_FILE_INVALID", status)

    def test_upload_gate_open_sou_rejected_piece_flux_3c(self):
        piece = dict(self._PIECE, status="rejected")
        name, token = self._mk("SOU", pieces=[piece], otp_verified=1)
        result = public.upload_piece_file(
            dossier_id=name, token=token, piece_code="releves_terminale"
        )
        self.assertEqual(result["error"]["code"], "PIECE_FILE_INVALID")

    def test_upload_gate_open_sou_missing_required_piece_a_fournir(self):
        name, token = self._mk("SOU", pieces=[dict(self._PIECE)], otp_verified=1)
        result = public.upload_piece_file(
            dossier_id=name, token=token, piece_code="releves_terminale"
        )
        self.assertEqual(result["error"]["code"], "PIECE_FILE_INVALID")

    def test_upload_refused_sou_already_uploaded_piece(self):
        piece = dict(self._PIECE, status="uploaded")
        name, token = self._mk("SOU", pieces=[piece], otp_verified=1)
        result = public.upload_piece_file(
            dossier_id=name, token=token, piece_code="releves_terminale"
        )
        self.assertEqual(result["error"]["code"], "STATE_READ_ONLY")

    def test_upload_gate_open_sou_staff_required_piece(self):
        piece = dict(self._PIECE, required=0, staff_requirement="required")
        name, token = self._mk("SOU", pieces=[piece], otp_verified=1)
        result = public.upload_piece_file(
            dossier_id=name, token=token, piece_code="releves_terminale"
        )
        self.assertEqual(result["error"]["code"], "PIECE_FILE_INVALID")

    def test_upload_aco_diploma_only(self):
        diplome = {"piece_code": "diplome_bac", "label": "Diplôme", "required": 1, "status": "missing"}
        name, token = self._mk("ACO", pieces=[diplome, dict(self._PIECE)], otp_verified=1)
        ok_gate = public.upload_piece_file(dossier_id=name, token=token, piece_code="diplome_bac")
        self.assertEqual(ok_gate["error"]["code"], "PIECE_FILE_INVALID")   # gate ouverte
        blocked = public.upload_piece_file(
            dossier_id=name, token=token, piece_code="releves_terminale"
        )
        self.assertEqual(blocked["error"]["code"], "STATE_READ_ONLY")      # tout le reste fermé

    def test_classify_bac_state_guard(self):
        bro, bro_token = self._mk("BRO")
        ok = public.classify_bac(bac_date="2024-07-01", dossier_id=bro, token=bro_token)
        self.assertTrue(ok["ok"], ok)
        sou, sou_token = self._mk("SOU")
        refused = public.classify_bac(bac_date="2024-07-01", dossier_id=sou, token=sou_token)
        self.assertEqual(refused["error"]["code"], "STATE_READ_ONLY")


class TestInvalidDossierCatchBavard(_ClaimBase):
    """V-LEARN-CAL-03 : la cause réelle au journal, le message générique au client."""

    def test_bad_token_logs_cause_without_leaking_token(self):
        name, _ = self._mk("BRO")
        with patch(f"{PUB}.log_event") as log:
            result = public.request_otp(dossier_id=name, token="MAUVAIS-JETON-xyz")
        self.assertEqual(result["error"]["code"], "INVALID_DOSSIER")   # réponse INCHANGÉE
        calls = [c for c in log.call_args_list if c.args and c.args[1] == "invalid_dossier"]
        self.assertEqual(len(calls), 1)
        kwargs = calls[0].kwargs
        # `error`, pas `warning` : le défaut frappe hors dev-server est ERROR — un catch
        # bavard filtré par le logger serait un catch muet déguisé.
        self.assertEqual(kwargs.get("level"), "error")
        self.assertIn("Jeton", kwargs.get("reason", ""))               # cause réelle classée
        self.assertNotIn("MAUVAIS-JETON-xyz", str(kwargs))             # jamais le jeton

    def test_unknown_dossier_logs_distinct_cause(self):
        with patch(f"{PUB}.log_event") as log:
            result = public.request_otp(dossier_id="00000000000", token="peu-importe")
        self.assertEqual(result["error"]["code"], "INVALID_DOSSIER")
        calls = [c for c in log.call_args_list if c.args and c.args[1] == "invalid_dossier"]
        self.assertEqual(len(calls), 1)
        self.assertIn("DoesNotExist", calls[0].kwargs.get("reason", ""))

    def test_unknown_dossier_leaves_no_server_message_oracle(self):
        """Anti-énumération RÉELLE : frappe.get_doc(DoesNotExist) pose « … not found » dans
        message_log → fuite `_server_messages` dans la réponse HTTP malgré le message
        générique. Le catch doit PURGER message_log (l'oracle d'existence disparaît)."""
        result = public.request_otp(dossier_id="00000000000", token="peu-importe")
        self.assertEqual(result["error"]["code"], "INVALID_DOSSIER")
        self.assertFalse(list(getattr(frappe.local, "message_log", []) or []),
                         "message_log doit être purgé (oracle d'existence)")

    def test_all_three_reprise_sites_log(self):
        name, _ = self._mk("BRO")
        for endpoint in (public.request_otp, public.verify_otp, public.get_dossier):
            with patch(f"{PUB}.log_event") as log:
                if endpoint is public.verify_otp:
                    result = endpoint(dossier_id=name, token="faux", email_otp="000000")
                else:
                    result = endpoint(dossier_id=name, token="faux")
            self.assertEqual(result["error"]["code"], "INVALID_DOSSIER", endpoint.__name__)
            self.assertTrue(
                any(c.args and c.args[1] == "invalid_dossier" for c in log.call_args_list),
                f"{endpoint.__name__} ne journalise pas la cause réelle",
            )
