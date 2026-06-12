"""Tests LOT RIB-SETTINGS — compte d'encaissement (solution 1 légère validée architecte).

Rotation versionnée du PDF (ancien DÉTRUIT), no-op à contenu identique, diffusion
(mail de vérification à l'éditeur + alertes), consommateurs (get_bank/_rib_attachment/
get_frais.rib), gate DATA-rib. Tests d'intégration sur le singleton RÉEL du site :
setUp capture l'état, tearDown le RESTAURE à l'identique (même hash ⇒ même version).
"""

import hashlib
from unittest import TestCase
from unittest.mock import patch

import frappe


def _settings():
    return frappe.get_doc("Admission Settings")


def _current_pdf_content():
    url = frappe.db.get_value("Admission Settings", "Admission Settings", "rib_pdf")
    if not url:
        return None
    name = frappe.db.get_value("File", {"file_url": url}, "name")
    content = frappe.get_doc("File", name).get_content()
    return content.encode() if isinstance(content, str) else content


def _upload_and_save(content, **values):
    """Simule la saisie finance : staging File + save du singleton (déclenche on_update)."""
    from frappe.utils.file_manager import save_file
    staging = save_file("rib-staging-test.pdf", content,
                        "Admission Settings", "Admission Settings", is_private=1)
    doc = _settings()
    doc.rib_pdf = staging.file_url
    for k, v in values.items():
        setattr(doc, k, v)
    with patch("admission.admission.doctype.admission_settings.admission_settings"
               ".AdmissionSettings._broadcast_rib_change"):
        doc.save(ignore_permissions=True)
    frappe.db.commit()


def _sweep_disk():
    """PIÈGE save_file legacy : il écrit le physique DEUX fois (module-level nom suffixé
    + File.before_insert) — la première écriture reste orpheline (aucune ligne File).
    Le chemin d'upload Desk réel n'a pas ce défaut ; on balaie les orphelins de test."""
    import os
    base = frappe.get_site_path("private", "files")
    for f in os.listdir(base):
        if f.startswith(("rib-staging-test", "rib-bad")) \
                and not frappe.get_all("File", filters={"file_url": f"/private/files/{f}"},
                                       limit=1):
            os.remove(os.path.join(base, f))


def _normalize_to_seed():
    """État de référence HERMÉTIQUE : purge toute version/staging puis re-seed depuis
    admission/seed/rib_coris_bank.pdf via le chemin de rotation réel."""
    for f in frappe.get_all("File", filters={"attached_to_doctype": "Admission Settings"},
                            pluck="name"):
        frappe.delete_doc("File", f, force=True, ignore_permissions=True)
    for f in frappe.get_all("File", filters={"file_name": ("like", "rib-staging-test%")},
                            pluck="name"):
        frappe.delete_doc("File", f, force=True, ignore_permissions=True)
    frappe.db.set_value("Admission Settings", "Admission Settings",
                        {"rib_pdf": None, "rib_pdf_hash": None, "rib_version": None},
                        update_modified=False)
    frappe.db.commit()
    seed = open(frappe.get_app_path("admission", "seed", "rib_coris_bank.pdf"), "rb").read()
    _upload_and_save(seed)
    return seed


class TestRibRotation(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._orig_pdf = _normalize_to_seed()
        cls._orig = frappe.db.get_value(
            "Admission Settings", "Admission Settings",
            ["rib_banque", "rib_titulaire", "rib_iban", "rib_bic", "rib_version", "rib_pdf_hash"],
            as_dict=True)
        assert cls._orig.rib_version, "normalisation ratée"

    @classmethod
    def tearDownClass(cls):
        # Restauration à l'identique : même contenu ⇒ même hash ⇒ même version qu'avant.
        _upload_and_save(cls._orig_pdf,
                         rib_banque=cls._orig.rib_banque, rib_titulaire=cls._orig.rib_titulaire,
                         rib_iban=cls._orig.rib_iban, rib_bic=cls._orig.rib_bic)
        restored = frappe.db.get_value("Admission Settings", "Admission Settings", "rib_version")
        assert restored == cls._orig.rib_version, "restauration ratée"
        _sweep_disk()
        super().tearDownClass()

    def test_rotation_destroys_old_version(self):
        old_version = frappe.db.get_value("Admission Settings", "Admission Settings", "rib_version")
        new_content = self._orig_pdf + b"%%rotation-test"
        _upload_and_save(new_content)
        v = frappe.db.get_value("Admission Settings", "Admission Settings",
                                ["rib_version", "rib_pdf", "rib_pdf_hash"], as_dict=True)
        expected = hashlib.sha256(new_content).hexdigest()
        self.assertEqual(v.rib_pdf_hash, expected)
        self.assertEqual(v.rib_version, expected[:8])
        self.assertNotEqual(v.rib_version, old_version)
        # L'ANCIENNE version n'existe plus (ni File canonique, ni staging résiduel)
        leftovers = frappe.get_all("File", filters={
            "attached_to_doctype": "Admission Settings",
            "file_name": ("like", f"RIB-LaNEM-v{old_version}%")})
        self.assertEqual(leftovers, [])
        stagings = frappe.get_all("File", filters={"file_name": ("like", "rib-staging-test%")})
        self.assertEqual(stagings, [], "stagings non purgés (after_commit)")
        # Une SEULE version vivante
        alive = frappe.get_all("File", filters={
            "attached_to_doctype": "Admission Settings",
            "file_name": ("like", "RIB-LaNEM-v%")})
        self.assertEqual(len(alive), 1)

    def test_same_content_is_noop(self):
        before = frappe.db.get_value("Admission Settings", "Admission Settings",
                                     ["rib_version", "rib_pdf"], as_dict=True)
        current = _current_pdf_content()
        _upload_and_save(current)  # re-upload du MÊME contenu
        after = frappe.db.get_value("Admission Settings", "Admission Settings",
                                    ["rib_version", "rib_pdf"], as_dict=True)
        self.assertEqual(after.rib_version, before.rib_version)  # pas de rotation

    def test_non_pdf_rejected(self):
        # Invariant : un contenu non-PDF est rejeté (par Frappe à l'upload OU par la
        # garde magic du contrôleur) et l'état du singleton reste INCHANGÉ.
        before = frappe.db.get_value("Admission Settings", "Admission Settings",
                                     ["rib_version", "rib_pdf_hash"], as_dict=True)
        with self.assertRaises(Exception):
            _upload_and_save(b"MZ\x90 not a pdf")
        frappe.db.rollback()
        after = frappe.db.get_value("Admission Settings", "Admission Settings",
                                    ["rib_version", "rib_pdf_hash"], as_dict=True)
        self.assertEqual(after, before)


class TestRibConsumers(TestCase):
    def test_get_bank_reads_settings(self):
        from admission.api.email_template import get_bank
        bank = get_bank()
        self.assertIsNotNone(bank)
        self.assertEqual(bank["iban"],
                         frappe.db.get_value("Admission Settings", "Admission Settings", "rib_iban"))
        self.assertTrue(bank["version"])

    def test_offline_mail_without_bank_sends_no_coordinates(self):
        # R0.1b : aucune coordonnée périmée — gabarit « coordonnées communiquées » sans PJ.
        import types
        from admission.api import notifications
        app = types.SimpleNamespace(name="CAN-X", applicant_name="T", email="t@x.bj",
                                    programme_label="L")
        fee = types.SimpleNamespace(amount_xof=15000)
        with patch.object(notifications, "get_bank", return_value=None), \
             patch.object(notifications, "frappe") as mf:
            notifications.send_offline_submission(app, fee, "bank")
        kw = mf.sendmail.call_args.kwargs
        self.assertNotIn("IBAN", kw["message"])
        self.assertNotIn("attachments", kw)
        self.assertIn("communiquées", kw["message"])

    def test_gate_data_rib_pass_when_seeded(self):
        from admission.api.recette_check import _check_rib_pdf
        status, detail = _check_rib_pdf()
        self.assertEqual(status, "PASS")
        self.assertIn("PDF versionné", detail)
