"""Tests PAY-FIX-FRAIS2-ALL-MODES Phase 1 — frais 2 (enrollment) online câblé sur le bon fee_type.

Bug recette : frais 2 online ne pré-créait pas de Pending sur l'enrollment fee → webhook insert
→ _ensure_fee (application) → AFP sur le frais 1 → gate ACC→INS bloquée (Koffi/John).
Correctif : extension du cœur (descriptor_amount + ventilation) + prepare_enrollment_online_payment
(pré-crée le Pending sur l'enrollment fee) + adoption par submit_enrollment + initiate(fee_type) ;
durcissement défensif du fallback insert webhook (refus explicite si contexte non-application).
Frais 1 inchangé (non-régression). Style unitaire mocké.
"""

from unittest import TestCase
from unittest.mock import MagicMock, patch

PUBLIC = "admission.api.public"
STAFF = "admission.api.staff"
WEBHOOK = "admission.api.webhook"
LEGAL = "admission.api.legal"


class TestPrepareEnrollmentOnline(TestCase):
    def test_precreates_pending_on_enrollment_fee_with_ventilation(self):
        with patch(f"{PUBLIC}.frappe") as mf, patch(f"{PUBLIC}.secrets") as ms, \
             patch(f"{PUBLIC}._online_payment_exists", return_value=False):
            ms.token_hex.return_value = "r2"
            inserted = MagicMock(); mf.get_doc.return_value = inserted
            from admission.api.public import prepare_enrollment_online_payment
            applicant = MagicMock(); applicant.name = "CAN-1"
            fee = MagicMock(); fee.name = "ENR-1"; fee.amount_xof = 50000
            desc = prepare_enrollment_online_payment(applicant, fee, acompte_xof=10000, idempotency_key=None)
        doc = mf.get_doc.call_args[0][0]
        self.assertEqual(doc["applicant_fee"], "ENR-1")   # Pending LIÉ à l'enrollment fee (pas frais 1)
        self.assertEqual(doc["amount_xof"], 50000)        # AFP = montant du frais 2 (acompte séparé)
        self.assertEqual(doc["payment_status"], "Pending")
        self.assertEqual(doc["payment_mode"], "Online")
        inserted.insert.assert_called_once_with(ignore_permissions=True)
        self.assertEqual(desc["amount_xof"], 60000)       # descriptor = total (frais2 + acompte)
        self.assertEqual(desc["ventilation"], {"frais2": 50000, "acompte": 10000})
        self.assertEqual(desc["fee_type"], "enrollment")  # hint pour le durcissement webhook
        self.assertEqual(applicant.acompte_xof, 10000)
        applicant.save.assert_called_once()

    def test_no_acompte_no_save(self):
        with patch(f"{PUBLIC}.frappe"), patch(f"{PUBLIC}.secrets") as ms, \
             patch(f"{PUBLIC}._online_payment_exists", return_value=True):
            ms.token_hex.return_value = "r2"  # référence réelle (sérialisée dans desc["data"])
            from admission.api.public import prepare_enrollment_online_payment
            applicant = MagicMock(); fee = MagicMock(); fee.name = "ENR-1"; fee.amount_xof = 50000
            desc = prepare_enrollment_online_payment(applicant, fee, acompte_xof=0)
            applicant.save.assert_not_called()
        self.assertEqual(desc["amount_xof"], 50000)
        self.assertEqual(desc["ventilation"]["acompte"], 0)


class TestFrais1DescriptorUnchanged(TestCase):
    def test_frais1_descriptor_no_extra_keys(self):
        with patch(f"{PUBLIC}.frappe"), patch(f"{PUBLIC}.secrets") as ms, \
             patch(f"{PUBLIC}._online_payment_exists", return_value=True):
            ms.token_hex.return_value = "r1"
            from admission.api.public import prepare_online_payment
            fee = MagicMock(); fee.amount_xof = 15000
            desc = prepare_online_payment(MagicMock(), fee)
        # LOT KKIAPAY : +public_key/sandbox/data (besoins widget) — toujours SANS ventilation
        # ni fee_type au frais 1 (non-régression candidat).
        self.assertEqual(set(desc), {"provider", "mode", "amount_xof", "reference",
                                     "webhook_required", "public_key", "sandbox", "data"})
        self.assertEqual(desc["amount_xof"], 15000)


class TestInitiateFeeType(TestCase):
    def test_agent_initiates_enrollment(self):
        with patch(f"{STAFF}.frappe") as mf, \
             patch(f"{STAFF}._ensure_enrollment_fee") as menr, \
             patch(f"{STAFF}.prepare_enrollment_online_payment", return_value={"fee_type": "enrollment", "reference": "RA"}) as mprep, \
             patch(f"{STAFF}._ok", side_effect=lambda d: {"ok": True, **d}), \
             patch(f"{STAFF}._error", side_effect=lambda c, m, s=400: {"ok": False, "code": c}):
            mf.db.exists.return_value = True
            applicant = MagicMock(); applicant.status = "ACC"  # W3/B0.5 : frais 2 depuis ACC
            fee = MagicMock()
            mf.get_doc.return_value = applicant; menr.return_value = fee
            from admission.api.staff import initiate_online_payment
            res = initiate_online_payment(dossier_id="CAN-1", fee_type="enrollment", acompte_xof=5000)
        mprep.assert_called_once()
        self.assertEqual(mprep.call_args.kwargs.get("acompte_xof"), 5000)
        self.assertEqual(res["fee_type"], "enrollment")

    def test_agent_enrollment_fee_unavailable(self):
        with patch(f"{STAFF}.frappe") as mf, \
             patch(f"{STAFF}._ensure_enrollment_fee", return_value=None), \
             patch(f"{STAFF}._ok", side_effect=lambda d: {"ok": True, **d}), \
             patch(f"{STAFF}._error", side_effect=lambda c, m, s=400: {"ok": False, "code": c}):
            mf.db.exists.return_value = True
            applicant = MagicMock(); applicant.status = "ACC"  # W3/B0.5
            mf.get_doc.return_value = applicant
            from admission.api.staff import initiate_online_payment
            res = initiate_online_payment(dossier_id="CAN-1", fee_type="enrollment")
        self.assertFalse(res["ok"])
        self.assertEqual(res["code"], "FEE_NOT_AVAILABLE")


class TestWebhookInsertHardening(TestCase):
    """LOT KKIAPAY : l'insert fallback est SUPPRIMÉ — tout webhook sans Pending lié est
    rejeté 409, quel que soit le type de frais (le durcissement PAY-FIX-FRAIS2 devient
    la règle générale)."""

    @staticmethod
    def _rq(mfw, ref):
        import json as _json
        mfw.conf = {"admission_payment_webhook_secret": "s"}
        mfw.request.data = _json.dumps({"transactionId": "TX-1", "event": "transaction.success",
                                        "stateData": {"reference": ref}})
        mfw.get_request_header.return_value = "s"

    @patch(f"{PUBLIC}.frappe")  # _error utilise public.frappe.local.response
    @patch(f"{WEBHOOK}._find_payment_by_reference", return_value=None)
    @patch(f"{WEBHOOK}.frappe")
    def test_enrollment_without_pending_rejected(self, mfw, _find, _mfpub):
        self._rq(mfw, "R-ENROLL")
        from admission.api.webhook import payment
        res = payment()
        self.assertFalse(res["ok"])
        self.assertEqual(res["error"]["code"], "PAYMENT_NOT_INITIATED")

    @patch(f"{PUBLIC}.frappe")
    @patch(f"{WEBHOOK}._find_payment_by_reference", return_value=None)
    @patch(f"{WEBHOOK}.frappe")
    def test_application_without_pending_rejected_too(self, mfw, _find, _mfpub):
        # AVANT : frais 1 sans Pending → insert compat. APRÈS : rejet uniforme (initiation requise).
        self._rq(mfw, "R-APP")
        from admission.api.webhook import payment
        res = payment()
        self.assertFalse(res["ok"])
        self.assertEqual(res["error"]["code"], "PAYMENT_NOT_INITIATED")
