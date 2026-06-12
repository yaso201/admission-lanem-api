"""Tests LOT F (A0.4) — public.upload_piece_file : dépôt binaire direct candidat.

Couverture : gate OTP, pièce attendue AVANT stockage, multipart requis, extension,
signature binaire (magic bytes — un .pdf renommé est refusé), taille bornée,
succès (File privé attaché + pièce marquée). Style unitaire mocké.
"""

import io
import types
from unittest import TestCase
from unittest.mock import MagicMock, patch

import frappe as _real_frappe


def setUpModule():
    try:
        _real_frappe.local.flags
    except Exception:
        _real_frappe.local.flags = _real_frappe._dict(in_test=True)


PUB = "admission.api.public"

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
PDF = b"%PDF-1.4 fake"


def _storage(filename, content):
    return types.SimpleNamespace(filename=filename, stream=io.BytesIO(content))


def _applicant(pieces=("cni", "diplome-bac"), otp=1):
    a = MagicMock()
    a.name = "CAN-2026-00001"
    a.otp_verified = otp
    rows = []
    for code in pieces:
        row = MagicMock()
        row.piece_code = code
        rows.append(row)
    a.pieces = rows
    return a


class TestUploadPieceFile(TestCase):
    def _call(self, mock_frappe, applicant, storage, piece_code="cni"):
        mock_frappe.request.files = {"file": storage} if storage else {}
        with patch(f"{PUB}._get_applicant", return_value=applicant), \
             patch("frappe.utils.file_manager.save_file") as save_file:
            saved = MagicMock(); saved.name = "FILE-0001"
            save_file.return_value = saved
            from admission.api.public import upload_piece_file
            res = upload_piece_file(dossier_id=applicant.name, token="tok", piece_code=piece_code)
            return res, save_file

    @patch(f"{PUB}.frappe")
    def test_success_png(self, mock_frappe):
        applicant = _applicant()
        res, save_file = self._call(mock_frappe, applicant, _storage("photo.PNG", PNG))
        self.assertTrue(res["ok"])
        self.assertEqual(res["data"], {"piece_code": "cni", "status": "deposee"})
        args = save_file.call_args[0]
        self.assertEqual(args[0], "cni-CAN-2026-00001.png")   # nom NORMALISÉ (pas le nom client)
        self.assertEqual(args[2:4], ("Admission Applicant", "CAN-2026-00001"))
        self.assertTrue(save_file.call_args.kwargs.get("is_private"))
        self.assertEqual(applicant.pieces[0].file, "FILE-0001")
        self.assertEqual(applicant.pieces[0].status, "uploaded")

    @patch(f"{PUB}.frappe")
    def test_otp_required(self, mock_frappe):
        res, save_file = self._call(mock_frappe, _applicant(otp=0), _storage("a.png", PNG))
        self.assertFalse(res["ok"])
        self.assertEqual(res["error"]["code"], "OTP_REQUIRED")
        save_file.assert_not_called()

    @patch(f"{PUB}.frappe")
    def test_unexpected_piece_rejected_before_storage(self, mock_frappe):
        res, save_file = self._call(mock_frappe, _applicant(), _storage("a.png", PNG),
                                    piece_code="inconnu")
        self.assertEqual(res["error"]["code"], "PIECE_NOT_EXPECTED")
        save_file.assert_not_called()   # pas de File orphelin

    @patch(f"{PUB}.frappe")
    def test_missing_file(self, mock_frappe):
        res, save_file = self._call(mock_frappe, _applicant(), None)
        self.assertEqual(res["error"]["code"], "PIECE_FILE_INVALID")
        save_file.assert_not_called()

    @patch(f"{PUB}.frappe")
    def test_bad_extension(self, mock_frappe):
        res, save_file = self._call(mock_frappe, _applicant(), _storage("script.exe", PNG))
        self.assertEqual(res["error"]["code"], "PIECE_FILE_INVALID")
        save_file.assert_not_called()

    @patch(f"{PUB}.frappe")
    def test_renamed_file_rejected_by_magic(self, mock_frappe):
        # Un binaire quelconque renommé en .pdf doit être refusé (signature contrôlée).
        res, save_file = self._call(mock_frappe, _applicant(), _storage("vrai.pdf", b"MZ\x90\x00 exe"))
        self.assertEqual(res["error"]["code"], "PIECE_FILE_INVALID")
        save_file.assert_not_called()

    @patch(f"{PUB}.PIECE_MAX_BYTES", 100)
    @patch(f"{PUB}.frappe")
    def test_too_large(self, mock_frappe):
        res, save_file = self._call(mock_frappe, _applicant(),
                                    _storage("gros.png", PNG + b"\x00" * 200))
        self.assertEqual(res["error"]["code"], "PIECE_FILE_TOO_LARGE")
        save_file.assert_not_called()

    @patch(f"{PUB}.frappe")
    def test_pdf_accepted(self, mock_frappe):
        res, _ = self._call(mock_frappe, _applicant(), _storage("releve.pdf", PDF),
                            piece_code="diplome-bac")
        self.assertTrue(res["ok"])
