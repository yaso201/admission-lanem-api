"""Tests ADM-1 — émetteur d'identité admission (DEC-AUTH-27, DEC-323, DAT-2).

Couvre : 3 outcomes (created/matched→backfill Applicant+Fees zéro-orphelin ;
review_queued→terminal+visible ; erreurs), corrélation DEC-323 (hint custom_person_id),
garde transport PII (DAT-2), no-PII dans les logs (OBS-2), idempotence, format person_id.

Le commit du flux (légitime en job async) est NEUTRALISÉ CÔTÉ HARNAIS (jamais de garde
`in_test` dans le code prod) — V-LEARN D-BANC-COMMIT-01.
"""

import frappe
from unittest.mock import MagicMock, patch
from frappe.tests.utils import FrappeTestCase

from admission.api import identity_emitter as em

EM = "admission.api.identity_emitter"
CFG = {"url": "https://campus.lanem.bj:8000", "token": "svc-tok"}


def _resp(message, status=200):
    r = MagicMock()
    r.status_code = status
    r.json.return_value = {"message": message}
    r.raise_for_status.return_value = None
    return r


def _make_applicant(email="cand@x.bj", **kw):
    doc = frappe.get_doc({
        "doctype": "Admission Applicant",
        "status": "BRO",
        "first_name": kw.get("first_name", "Ama"),
        "last_name": kw.get("last_name", "Koffi"),
        "email": email,
        "phone": kw.get("phone", "+22990000000"),
        "date_of_birth": kw.get("date_of_birth", "2005-01-01"),
        "person_id": kw.get("person_id"),
        "person_resolved": kw.get("person_resolved", 0),
    })
    doc.insert(ignore_permissions=True, ignore_mandatory=True)
    return doc


class _EmitterTestBase(FrappeTestCase):
    def setUp(self):
        super().setUp()
        # V-LEARN D-BANC : le commit (légitime en job async) casserait l'isolation
        # FrappeTestCase → neutralisé CÔTÉ HARNAIS (0 garde in_test dans le code prod).
        p = patch.object(frappe.db, "commit")
        p.start()
        self.addCleanup(p.stop)


class TestOutcomes(_EmitterTestBase):
    @patch(f"{EM}._pii_transport_allowed", return_value=True)
    @patch(f"{EM}._get_campus_config", return_value=CFG)
    @patch(f"{EM}.requests.post")
    def test_created_backfills_applicant_and_fees_zero_orphan(self, mock_post, _cfg, _pii):
        app = _make_applicant()
        # 2 Applicant Fees créés AVANT résolution (person_id NULL) — la fenêtre à rattraper.
        for ft in ("application", "enrollment"):
            frappe.get_doc({"doctype": "Applicant Fee", "applicant": app.name,
                            "fee_type": ft, "amount_xof": 1000, "status": "Pending"}
                           ).insert(ignore_permissions=True, ignore_mandatory=True)
        mock_post.return_value = _resp({"outcome": "created", "person_id": "PERS-00042"})

        res = em._send_identity_assertion(app.name)

        self.assertEqual(res["status"], "resolved")
        self.assertEqual(frappe.db.get_value("Admission Applicant", app.name, "person_id"), "PERS-00042")
        self.assertEqual(frappe.db.get_value("Admission Applicant", app.name, "person_resolved"), 1)
        # ZÉRO fee orphelin : tous les Applicant Fees portent le person_id réel.
        fees = frappe.get_all("Applicant Fee", filters={"applicant": app.name},
                              fields=["person_id"])
        self.assertTrue(fees)
        self.assertTrue(all(f.person_id == "PERS-00042" for f in fees), "fee orphelin détecté")

    @patch(f"{EM}._pii_transport_allowed", return_value=True)
    @patch(f"{EM}._get_campus_config", return_value=CFG)
    @patch(f"{EM}.requests.post")
    def test_matched_backfills(self, mock_post, _cfg, _pii):
        app = _make_applicant()
        mock_post.return_value = _resp({"outcome": "matched", "person_id": "PERS-00010"})
        res = em._send_identity_assertion(app.name)
        self.assertEqual(res["status"], "resolved")
        self.assertEqual(frappe.db.get_value("Admission Applicant", app.name, "person_id"), "PERS-00010")

    @patch(f"{EM}._pii_transport_allowed", return_value=True)
    @patch(f"{EM}._get_campus_config", return_value=CFG)
    @patch(f"{EM}.requests.post")
    def test_review_queued_terminal_visible(self, mock_post, _cfg, _pii):
        app = _make_applicant()
        mock_post.return_value = _resp({"outcome": "review_queued", "person_id": None})
        with patch(f"{EM}.log_event") as mock_log:
            res = em._send_identity_assertion(app.name)
        self.assertEqual(res["status"], "review_queued")
        self.assertEqual(frappe.db.get_value("Admission Applicant", app.name, "person_review_queued"), 1)
        self.assertFalse(frappe.db.get_value("Admission Applicant", app.name, "person_id"))
        # D-ADM-REVIEW-01 : signal VISIBLE (pas un NULL perdu) — log_event review_queued + alert_type.
        review_logs = [c for c in mock_log.call_args_list if c.args[:2] == ("identity_assert", "review_queued")]
        self.assertTrue(review_logs, "review_queued doit être un signal visible")

    @patch(f"{EM}._pii_transport_allowed", return_value=True)
    @patch(f"{EM}._get_campus_config", return_value=CFG)
    @patch(f"{EM}.requests.post")
    def test_bad_person_id_rejected_not_stored(self, mock_post, _cfg, _pii):
        app = _make_applicant()
        mock_post.return_value = _resp({"outcome": "created", "person_id": "BADFORMAT-123"})
        res = em._send_identity_assertion(app.name)
        self.assertEqual(res["status"], "bad_person_id")
        self.assertFalse(frappe.db.get_value("Admission Applicant", app.name, "person_id"))


class TestDec323Correlation(_EmitterTestBase):
    def test_hint_from_prior_resolved_same_email(self):
        """DEC-323 : un Applicant antérieur au même email (casse/espaces différents) déjà
        résolu → son person_id passé en hint custom_person_id (match confiant campus)."""
        _make_applicant(email="Koffi@Example.com ", person_id="PERS-00007", person_resolved=1)
        b = _make_applicant(email="koffi@example.com")  # même identité, casse différente
        env = em.build_ensure_person_envelope(b)
        self.assertEqual(env["matching"].get("custom_person_id"), "PERS-00007")

    def test_no_hint_when_no_prior(self):
        b = _make_applicant(email="nouveau@x.bj")
        env = em.build_ensure_person_envelope(b)
        self.assertNotIn("custom_person_id", env["matching"])
        # DOB au payload (DEC-322) + email dans matching.
        self.assertEqual(env["attributes"].get("date_of_birth"), "2005-01-01")
        self.assertEqual(env["matching"]["email"], "nouveau@x.bj")


class TestGuards(_EmitterTestBase):
    @patch(f"{EM}._pii_transport_allowed", return_value=False)
    @patch(f"{EM}._get_campus_config", return_value=CFG)
    @patch(f"{EM}.requests.post")
    def test_pii_transport_blocked_no_post(self, mock_post, _cfg, _pii):
        app = _make_applicant()
        res = em._send_identity_assertion(app.name)
        self.assertEqual(res["status"], "pii_transport_blocked")
        mock_post.assert_not_called()  # DAT-2 : aucune PII émise sur canal non autorisé

    @patch(f"{EM}._pii_transport_allowed", return_value=True)
    @patch(f"{EM}._get_campus_config", return_value=CFG)
    @patch(f"{EM}.requests.post")
    def test_no_pii_in_logs(self, mock_post, _cfg, _pii):
        app = _make_applicant(email="leak@x.com", first_name="LEAKNAME")
        mock_post.return_value = _resp({"outcome": "created", "person_id": "PERS-00099"})
        with patch(f"{EM}.log_event") as mock_log:
            em._send_identity_assertion(app.name)
        blob = repr(mock_log.call_args_list)
        self.assertNotIn("leak@x.com", blob, "email (PII) fuit dans un log")
        self.assertNotIn("LEAKNAME", blob, "nom (PII) fuit dans un log")

    @patch(f"{EM}._get_campus_config", return_value=None)
    def test_deferred_when_no_config(self, _cfg):
        app = _make_applicant()
        res = em._send_identity_assertion(app.name)
        self.assertEqual(res["status"], "deferred_configuration")  # pas un échec, redrive reprendra

    @patch(f"{EM}._pii_transport_allowed", return_value=True)
    @patch(f"{EM}._get_campus_config", return_value=CFG)
    @patch(f"{EM}.requests.post")
    def test_already_resolved_is_noop(self, mock_post, _cfg, _pii):
        app = _make_applicant(person_id="PERS-00001", person_resolved=1)
        res = em._send_identity_assertion(app.name)
        self.assertEqual(res["status"], "already_resolved")
        mock_post.assert_not_called()  # idempotence : pas de nouvel appel réseau


class TestTransientVsPermanent(_EmitterTestBase):
    @patch(f"{EM}._pii_transport_allowed", return_value=True)
    @patch(f"{EM}._get_campus_config", return_value=CFG)
    @patch(f"{EM}.requests.post")
    def test_network_error_raises_for_retry(self, mock_post, _cfg, _pii):
        import requests as real_requests
        app = _make_applicant()
        mock_post.side_effect = real_requests.ConnectionError("refused")
        with self.assertRaises(real_requests.RequestException):  # → enqueue retry
            em._send_identity_assertion(app.name)

    @patch(f"{EM}._pii_transport_allowed", return_value=True)
    @patch(f"{EM}._get_campus_config", return_value=CFG)
    @patch(f"{EM}.requests.post")
    def test_4xx_receiver_error_no_retry(self, mock_post, _cfg, _pii):
        app = _make_applicant()
        mock_post.return_value = _resp({"error": "scope refusé", "status_code": 403}, status=403)
        res = em._send_identity_assertion(app.name)  # ne DOIT PAS raise (permanent)
        self.assertEqual(res["status"], "receiver_error")
