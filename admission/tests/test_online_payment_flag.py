"""OUVERTURE-SOP (DEC-334/335/336) — drapeau serveur `online_payment_enabled`.

Contrat prouvé :
- drapeau à 0 : les DEUX endpoints d'initiation en ligne refusent avec le code dédié
  ONLINE_PAYMENT_DISABLED (503), AVANT toute authentification (fail-fast, aucun effet
  de bord) — le serveur est l'autorité, un front contourné ne passe pas ;
- robustesse set-config : la valeur "0" (chaîne) ferme aussi (cint) ;
- drapeau absent ou à 1 : comportement historique inchangé (absent = OUVERT, choix de
  compatibilité — en production le drapeau est TOUJOURS posé explicitement) ;
- get_frais expose l'état au front (pur renderer : le front lit, ne décide pas) ;
- DEC-336 : le webhook CONFIRME une transaction même drapeau à 0 (l'initiation est
  fermée, jamais la confirmation) — sans toucher webhook.py ;
- le flux SOP (declare_payment_offline) n'est PAS gaté par le drapeau.
"""

from contextlib import contextmanager
from unittest import TestCase
from unittest.mock import MagicMock, patch

import frappe
from frappe.tests.utils import FrappeTestCase

from admission.api import public

PUB = "admission.api.public"
WEBHOOK = "admission.api.webhook"
FLAG = "online_payment_enabled"


@contextmanager
def _flag(value):
    """Pose/retire le drapeau dans la conf du process, restauration garantie."""
    conf = frappe.local.conf
    had = FLAG in conf
    old = conf.get(FLAG)
    if value is None:
        conf.pop(FLAG, None)
    else:
        conf[FLAG] = value
    try:
        yield
    finally:
        if had:
            conf[FLAG] = old
        else:
            conf.pop(FLAG, None)


class TestOnlinePaymentFlagGate(FrappeTestCase):
    def setUp(self):
        frappe.local.response = {}

    def test_flag_zero_refuses_both_initiations_before_auth(self):
        """Identifiants BIDON : le refus dédié AVANT l'auth prouve le fail-fast."""
        with _flag(0):
            for endpoint in (public.submit_payment_online, public.submit_enrollment_payment_online):
                frappe.local.response = {}
                result = endpoint(dossier_id="BIDON", token="bidon")
                self.assertFalse(result["ok"], endpoint.__name__)
                self.assertEqual(result["error"]["code"], "ONLINE_PAYMENT_DISABLED", endpoint.__name__)
                self.assertEqual(frappe.local.response.get("http_status_code"), 503, endpoint.__name__)
                # Message orienté action (DEC-335) : une nouveauté à venir, jamais une panne.
                self.assertIn("prochainement", result["error"]["message"])

    def test_flag_string_zero_from_set_config_also_closes(self):
        with _flag("0"):
            result = public.submit_payment_online(dossier_id="BIDON", token="bidon")
            self.assertEqual(result["error"]["code"], "ONLINE_PAYMENT_DISABLED")

    def test_flag_absent_keeps_historic_behavior(self):
        """Absent = OUVERT (compatibilité) : la porte laisse passer jusqu'à l'auth."""
        with _flag(None):
            result = public.submit_payment_online(dossier_id="BIDON", token="bidon")
            self.assertEqual(result["error"]["code"], "INVALID_DOSSIER")   # la garde a laissé passer

    def test_flag_one_keeps_path_open(self):
        with _flag(1):
            result = public.submit_enrollment_payment_online(dossier_id="BIDON", token="bidon")
            self.assertEqual(result["error"]["code"], "INVALID_DOSSIER")

    def test_sop_declaration_is_never_gated_by_the_flag(self):
        """DEC-334 ne ferme QUE l'initiation en ligne — le flux SOP reste la voie ouverte."""
        with _flag(0):
            result = public.declare_payment_offline(dossier_id="BIDON", token="bidon")
            self.assertEqual(result["error"]["code"], "INVALID_DOSSIER")   # pas ONLINE_PAYMENT_DISABLED


class TestFlagExposedToFront(FrappeTestCase):
    def setUp(self):
        frappe.local.response = {}

    def test_get_frais_mirrors_flag_state(self):
        with _flag(0):
            closed = public.get_frais(programme="LIC")
        with _flag(None):
            open_default = public.get_frais(programme="LIC")
        self.assertTrue(closed["ok"] and open_default["ok"])
        self.assertFalse(closed["data"]["online_payment_enabled"])
        self.assertTrue(open_default["data"]["online_payment_enabled"])


class TestWebhookIgnoresFlag(TestCase):
    """DEC-336 — miroir EXACT de test_promotes_existing_pending, drapeau à 0 :
    une transaction initiée avant la coupure se confirme quand même."""

    @patch(f"{WEBHOOK}.notify_uf_payment")
    @patch(f"{WEBHOOK}.send_payment_receipt")
    @patch(f"{WEBHOOK}.apply_confirmed_payment_cascade")
    @patch(f"{WEBHOOK}.verify_transaction", return_value={"status": "SUCCESS", "amount": 15000})
    @patch(f"{WEBHOOK}.now_datetime", return_value="2026-06-13 10:00:00")
    @patch(f"{WEBHOOK}._find_payment_by_reference")
    @patch(f"{WEBHOOK}.valid_webhook_signature", return_value=True)
    @patch(f"{WEBHOOK}.frappe")
    def test_confirmation_succeeds_with_flag_zero(self, mf, _sig, mfind, _now, mver, mcasc, msend, _mnotify):
        import json as _json

        # Câblage IDENTIQUE à _rq/_payload de test_webhook_promotion — seule différence :
        # la conf porte le drapeau FERMÉ. Le webhook ne doit pas le consulter (DEC-336).
        mf.conf = {"fedapay_webhook_secret": "whsec_test", FLAG: 0}
        payload = {"name": "transaction.approved",
                   "entity": {"id": "TX-1", "status": "approved", "amount": 15000,
                              "custom_metadata": {"provider_reference": "REF-1"}}}
        mf.request.data = _json.dumps(payload)
        mf.get_request_header.return_value = "t=1700000000,s=deadbeef"
        mf.local.response = {}
        pending = MagicMock()
        pending.payment_status = "Pending"
        pending.name = "REC-1"
        pending.applicant = "CAN-2026-00001"
        pending.applicant_fee = "AFF-1"
        pending.amount_xof = 15000
        mfind.return_value = pending
        mf.db.get_value.return_value = "Pending"
        mf.db.exists.return_value = False
        applicant = MagicMock()
        fee = MagicMock()
        mf.get_doc.side_effect = lambda dt, name=None: applicant if dt == "Admission Applicant" else fee

        from admission.api.webhook import payment
        result = payment()

        self.assertTrue(result["ok"], result)
        self.assertEqual(pending.payment_status, "Confirmed")   # la confirmation ABOUTIT
        mcasc.assert_called_once()                              # cascade jouée
        msend.assert_called_once()                              # reçu envoyé
