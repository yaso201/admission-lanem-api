"""RAPPELS-J4J6 (vague 3) — rappels candidat J+4/J+6 après le récap pièces.

Style unité mocké (cohérent test_pieces_resubmit). Le job vit dans notifications.py ; `pieces_recap`
(public.py) est importée au top-level → stubbable `{NOTIF}.pieces_recap` (V-LEARN-MOCKMODULE-10).
Fenêtres pilotées par `date_diff` (jours calendaires) sur l'ancre `pieces_recap_sent_at`.
Le comportement réel (arrêts sur vraie donnée) est prouvé à la recette (Phase 4).
"""

from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import MagicMock, patch

NOTIF = "admission.api.notifications"
STAFF = "admission.api.staff"


# ───────────────────────── Bloc ancrage (staff) — J1, J2 ─────────────────────────

class TestRecapAnchor(TestCase):
    def _notify(self, app):
        with patch(f"{STAFF}.frappe") as mf, \
             patch(f"{STAFF}.notify_pieces_blocked", return_value=False), \
             patch(f"{STAFF}.pieces_recap", return_value={"rejetees": [], "a_fournir": []}), \
             patch(f"{STAFF}.send_pieces_recap_notification"), \
             patch(f"{STAFF}._generate_token", return_value="tok"), \
             patch(f"{STAFF}._hash", return_value="h"), \
             patch(f"{STAFF}.now_datetime", return_value="NOW"), \
             patch(f"{STAFF}.add_days", return_value="EXPIRES"), \
             patch(f"{STAFF}.log_event"), \
             patch(f"{STAFF}._ok", side_effect=lambda d: {"ok": True, **d}), \
             patch(f"{STAFF}._error", side_effect=lambda c, m, s=400: {"ok": False, "code": c}):
            mf.db.exists.return_value = True
            mf.get_doc.return_value = app
            from admission.api.staff import notify_pieces_recap
            return notify_pieces_recap(dossier_id="CAN-1")

    def test_j1_recap_pose_ancre_et_flags_zero(self):
        app = MagicMock(); app.status = "SOU"
        self._notify(app)
        self.assertEqual(app.pieces_recap_sent_at, "NOW")   # ancre posée
        self.assertEqual(app.rappel_j4_sent, 0)
        self.assertEqual(app.rappel_j6_sent, 0)

    def test_j2_re_recap_reset_flags(self):
        app = MagicMock(); app.status = "SOU"
        app.rappel_j4_sent = 1; app.rappel_j6_sent = 1   # cycle précédent terminé
        self._notify(app)
        self.assertEqual(app.pieces_recap_sent_at, "NOW")   # nouvelle date
        self.assertEqual(app.rappel_j4_sent, 0)             # flags remis à 0 → nouveau cycle
        self.assertEqual(app.rappel_j6_sent, 0)


# ───────────────────────── Bloc job — J3-J12 ─────────────────────────

class TestPiecesReminders(TestCase):
    def _run(self, age, j4=0, j6=0, rejetees=None, a_fournir=None):
        app = SimpleNamespace(name="CAN-1", pieces_recap_sent_at="2026-07-01 00:00:00",
                              rappel_j4_sent=j4, rappel_j6_sent=j6)
        with patch(f"{NOTIF}.frappe") as mf, \
             patch(f"{NOTIF}.now_datetime", return_value="NOW"), \
             patch(f"{NOTIF}.date_diff", return_value=age), \
             patch(f"{NOTIF}.pieces_recap",
                   return_value={"rejetees": rejetees or [], "a_fournir": a_fournir or []}), \
             patch(f"{NOTIF}.send_pieces_reminder_notification") as msend:
            mf.get_all.return_value = ["CAN-1"]
            mf.get_doc.return_value = app
            from admission.api.notifications import send_pieces_reminders
            res = send_pieces_reminders()
        fields_set = [c.args[2] for c in mf.db.set_value.call_args_list]
        return app, msend, fields_set, res

    _REJ = [{"code": "cni", "label": "CNI", "reason": "Illisible", "comment": ""}]

    def test_j3_j4_atteint_1_mail_flag_j4(self):
        _, msend, fields, _ = self._run(age=4, rejetees=self._REJ)
        self.assertEqual(msend.call_count, 1)
        self.assertEqual(fields, ["rappel_j4_sent"])          # J4 seul

    def test_j4_re_run_meme_jour_noop(self):
        _, msend, fields, _ = self._run(age=4, j4=1, rejetees=self._REJ)
        self.assertEqual(msend.call_count, 0)                 # idempotence
        self.assertEqual(fields, [])

    def test_j5_j6_atteint_mail_flag_j6(self):
        _, msend, fields, _ = self._run(age=6, j4=1, rejetees=self._REJ)
        self.assertEqual(msend.call_count, 1)
        self.assertEqual(fields, ["rappel_j6_sent"])          # j4 déjà posé

    def test_j6_retard_1_mail_deux_flags(self):
        _, msend, fields, _ = self._run(age=6, j4=0, j6=0, rejetees=self._REJ)
        self.assertEqual(msend.call_count, 1)                 # 1 SEUL mail (J6)
        self.assertEqual(set(fields), {"rappel_j6_sent", "rappel_j4_sent"})   # les DEUX flags

    def test_j7_resoumis_exclu_en_requete(self):
        with patch(f"{NOTIF}.frappe") as mf:
            mf.get_all.return_value = []
            from admission.api.notifications import send_pieces_reminders
            send_pieces_reminders()
            filters = mf.get_all.call_args.kwargs["filters"]
        self.assertEqual(filters["resoumis"], 0)

    def test_j8_zero_restante_pas_de_mail(self):
        _, msend, fields, _ = self._run(age=6, rejetees=[], a_fournir=[])
        self.assertEqual(msend.call_count, 0)
        self.assertEqual(fields, [])

    def test_j9_hors_sou_exclu_en_requete(self):
        with patch(f"{NOTIF}.frappe") as mf:
            mf.get_all.return_value = []
            from admission.api.notifications import send_pieces_reminders
            send_pieces_reminders()
            filters = mf.get_all.call_args.kwargs["filters"]
        self.assertEqual(filters["status"], "SOU")

    def test_j10_fenetre_non_atteinte(self):
        _, msend, fields, _ = self._run(age=2, rejetees=self._REJ)
        self.assertEqual(msend.call_count, 0)
        self.assertEqual(fields, [])

    def test_j12_post_j7_pas_de_rappel(self):
        _, msend, fields, _ = self._run(age=8, rejetees=self._REJ)   # ≥ TOKEN_TTL_DAYS
        self.assertEqual(msend.call_count, 0)
        self.assertEqual(fields, [])


# ───────────────────────── Bloc contenu mail — J11 ─────────────────────────

class TestReminderMail(TestCase):
    def test_j11_contenu_motifs_date_et_cta_sans_token(self):
        app = SimpleNamespace(name="CAN-1", token_expires_at="2026-07-08 00:00:00")
        rejetees = [{"code": "cni", "label": "CNI", "reason": "Illisible", "comment": "flou"}]
        a_fournir = [{"code": "photo", "label": "Photo"}]
        with patch(f"{NOTIF}._full_name", return_value="A B"), \
             patch(f"{NOTIF}._programme", return_value="Prog"), \
             patch(f"{NOTIF}.format_date", return_value="08/07/2026"), \
             patch(f"{NOTIF}._portal_link", return_value="https://x/suivi") as mportal, \
             patch(f"{NOTIF}.render_candidate_email", return_value="<html>") as mrender, \
             patch(f"{NOTIF}._send_candidate_mail"):
            from admission.api.notifications import send_pieces_reminder_notification
            send_pieces_reminder_notification(app, rejetees, a_fournir)
        kw = mrender.call_args.kwargs
        self.assertIn("Illisible", kw["motif"])          # motif du rejet
        self.assertIn("flou", kw["motif"])               # commentaire
        self.assertIn("Photo", kw["motif"])              # à-fournir
        self.assertIn("08/07/2026", kw["intro"])         # date de validité affichée
        self.assertEqual(kw["cta"]["url"], "https://x/suivi")   # CTA = lien générique
        mportal.assert_called_once_with(app)             # AUCUN token embarqué (option b)
