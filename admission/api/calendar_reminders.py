"""CAL-AMEL (DEC-O) — rappels calendrier via le NRM existant (scheduler daily + sendmail +
résolution Has Role, patron send_resubmit_staff_notification).

Jalons : J-7 / J-1 avant clôture (Resp+Dir) · J-14 / J-7 avant épreuve si salle OU heures
manquantes (Resp) · hebdomadaire si proposition en attente > 7 j (Dir).

IDEMPOTENCE (obligatoire, DEC-O) : UNE notification par jalon par session — doctype
`Admission Session Reminder` (patron des flags J4/J6 des rappels pièces, sans polluer le
doctype session). Retard de scheduler → UN SEUL envoi (le jalon le plus proche prime, les
jalons antérieurs sont posés sans envoi — collapse anti-rafale). Rejeu du job = no-op.
"""

from __future__ import annotations

import frappe
from frappe.utils import date_diff, getdate, nowdate


def _staff_emails(roles):
    """Destinataires par rôle — patron NRM (Has Role → User enabled, jamais Administrator/Guest)."""
    users = frappe.get_all("Has Role", filters={"role": ["in", roles], "parenttype": "User"},
                           pluck="parent")
    users = [u for u in set(users) if u not in ("Administrator", "Guest")]
    if not users:
        return []
    return sorted({e for e in frappe.get_all(
        "User", filters={"name": ["in", users], "enabled": 1}, pluck="email") if e})


def _sent(session, jalon):
    return bool(frappe.db.exists("Admission Session Reminder", {"session": session, "jalon": jalon}))


def _mark(session, jalon):
    frappe.get_doc({"doctype": "Admission Session Reminder", "session": session,
                    "jalon": jalon, "sent_on": nowdate()}).insert(ignore_permissions=True)


def _mail(recipients, subject, lines):
    if not recipients:
        return
    frappe.sendmail(recipients=recipients, subject=subject,
                    message="<p>" + "</p><p>".join(lines) + "</p>")


def send_calendar_reminders():
    """Job daily (hooks). Retourne les compteurs par famille (testable, journalisé)."""
    today = getdate(nowdate())
    resp_dir = _staff_emails(["Admission Responsable", "Admission Direction"])
    resp = _staff_emails(["Admission Responsable"])
    dir_only = _staff_emails(["Admission Direction"])
    sent = {"cloture": 0, "epreuve": 0, "pending": 0}

    # ── Famille 1 : clôture des dépôts (sessions Open) — J-7 puis J-1, Resp + Dir ──
    for s in frappe.get_all("Admission Session", filters={"lifecycle_state": "Open"},
                            fields=["name", "label", "closes_on"]):
        if not s.closes_on:
            continue
        d = date_diff(s.closes_on, today)
        if 0 <= d <= 1 and not _sent(s.name, "cloture_j1"):
            _mail(resp_dir, f"Clôture imminente (J-{d}) — {s.name}",
                  [f"La session « {s.label} » ({s.name}) clôt ses dépôts le {s.closes_on}.",
                   f"Fiche : /calendrier-session?s={s.name}"])
            _mark(s.name, "cloture_j1")
            if not _sent(s.name, "cloture_j7"):
                _mark(s.name, "cloture_j7")   # collapse : jamais 2 mails en rattrapage
            sent["cloture"] += 1
        elif 0 <= d <= 7 and not _sent(s.name, "cloture_j7"):
            _mail(resp_dir, f"Clôture dans {d} j — {s.name}",
                  [f"La session « {s.label} » ({s.name}) clôt ses dépôts le {s.closes_on} (J-{d}).",
                   f"Fiche : /calendrier-session?s={s.name}"])
            _mark(s.name, "cloture_j7")
            sent["cloture"] += 1

    # ── Famille 2 : épreuve sous 14 j SANS salle ou SANS heures — J-14 puis J-7, Resp ──
    for s in frappe.get_all("Admission Session", filters={"exam_date": ["is", "set"]},
                            fields=["name", "label", "exam_date", "exam_room",
                                    "exam_call_time", "exam_start_time"]):
        manque = ([] if s.exam_room else ["salle"]) + \
                 ([] if (s.exam_call_time and s.exam_start_time) else ["heures"])
        if not manque:
            continue
        d = date_diff(s.exam_date, today)
        subj = f"Épreuve dans {d} j — {s.name} : " + " et ".join(manque) + " manquante(s)"
        lines = [f"L'épreuve de « {s.label} » ({s.name}) a lieu le {s.exam_date} — "
                 + " et ".join(manque) + " non renseignée(s).",
                 f"Fiche : /calendrier-session?s={s.name}"]
        if 0 <= d <= 7 and not _sent(s.name, "epreuve_j7"):
            _mail(resp, subj, lines)
            _mark(s.name, "epreuve_j7")
            if not _sent(s.name, "epreuve_j14"):
                _mark(s.name, "epreuve_j14")
            sent["epreuve"] += 1
        elif 0 <= d <= 14 and not _sent(s.name, "epreuve_j14"):
            _mail(resp, subj, lines)
            _mark(s.name, "epreuve_j14")
            sent["epreuve"] += 1

    # ── Famille 3 : proposition en attente > 7 j — HEBDOMADAIRE, Direction ──
    rows = frappe.get_all("Admission Session Pending Change", fields=["parent", "requested_on"],
                          parent_doctype="Admission Session")
    stale = sorted({r.parent for r in rows
                    if r.requested_on and date_diff(today, str(r.requested_on)[:10]) > 7})
    for name in stale:
        last = frappe.get_all("Admission Session Reminder",
                              filters={"session": name, "jalon": "pending_hebdo"},
                              fields=["sent_on"], order_by="sent_on desc", limit=1)
        if last and date_diff(today, last[0].sent_on) < 7:
            continue   # hebdomadaire : au plus une par semaine
        _mail(dir_only, f"Propositions en attente de validation — {name}",
              [f"La session {name} porte des propositions en attente depuis plus de 7 jours.",
               "File de validation : /calendrier-validations"])
        _mark(name, "pending_hebdo")
        sent["pending"] += 1

    frappe.db.commit()
    frappe.logger("calendar_reminders").info(f"rappels calendrier : {sent}")
    return sent
