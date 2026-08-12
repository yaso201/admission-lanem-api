"""SESSIONS-AUTO-FERMETURE — cycle de vie des sessions d'admission.

Une session dont l'épreuve est passée ne doit plus accepter de candidature, mais
reste VISIBLE (lisibilité du calendrier). Critère de fermeture : `closes_on` (date-limite
de dépôt, ANTÉRIEURE au concours) dépassée, comparé à la date LOCALE (Africa/Porto-Novo,
jamais UTC — sinon fermeture prématurée). `closes_on` absent = pas d'échéance → jamais fermé.

Trois usages d'une seule logique (pas de divergence front/back) :
  - catalogue candidat (`list_sessions`)      → afficher + flag `selectable`
  - garde serveur (`create_dossier`)          → refuser SESSION_CLOSED (l'enforcement)
  - tâche quotidienne (`close_expired_sessions`) → poser `is_open=0`, journalisé

Ref: SESSIONS-AUTO-FERMETURE (GS1-GS7).
"""

from __future__ import annotations

import frappe
from frappe.utils import getdate, nowdate


def _today():
    # Date LOCALE : nowdate() suit le fuseau système (= Africa/Porto-Novo, UTC+1).
    # GS4 : une session ne se ferme pas quelques heures avant minuit local.
    return getdate(nowdate())


def _field(session, name):
    if isinstance(session, dict):
        return session.get(name)
    return getattr(session, name, None)


def is_session_selectable(session) -> bool:
    """Sélectionnable = ouverte ET non échue. Utilisé par le catalogue ET la garde.
    `closes_on` absent = pas d'échéance (reste sélectionnable si ouverte)."""
    if not session:
        return False
    if not _field(session, "is_open"):
        return False
    closes_on = _field(session, "closes_on")
    if closes_on and getdate(closes_on) < _today():
        return False  # échue
    return True


def session_display_status(session) -> str:
    """'a_venir' (ouverte + non échue → sélectionnable) ·
    'echue' (date dépassée → visible, NON sélectionnable, quel que soit is_open) ·
    'fermee' (fermée à la main, date non dépassée → masquée du catalogue)."""
    closes_on = _field(session, "closes_on")
    echue = bool(closes_on) and getdate(closes_on) < _today()
    if echue:
        return "echue"
    if _field(session, "is_open"):
        return "a_venir"
    return "fermee"


def close_expired_sessions():
    """Tâche quotidienne : ferme les sessions échues (`closes_on < aujourd'hui`, Porto-Novo).
    Idempotente (ne re-ferme pas), journalisée, NE TOUCHE AUCUN DOSSIER, ne rouvre jamais.
    `closes_on` absent → non fermée, SIGNALÉE (GS6). Enregistrable via scheduler ou bench."""
    today = _today()
    closed, skipped_no_date = [], []
    for s in frappe.get_all("Admission Session", filters={"is_open": 1},
                            fields=["name", "closes_on"]):
        if not s.closes_on:
            skipped_no_date.append(s.name)
            frappe.logger("session_lifecycle").warning(
                f"Session {s.name} sans closes_on — non fermée (à dater manuellement)."
            )
            continue
        if getdate(s.closes_on) < today:
            frappe.db.set_value("Admission Session", s.name, "is_open", 0)
            closed.append({"session": s.name, "closes_on": str(s.closes_on)})
            frappe.logger("session_lifecycle").info(
                f"Session {s.name} fermée automatiquement (closes_on {s.closes_on} < {today})."
            )
    if closed:
        frappe.db.commit()
    return {"today": str(today), "closed": closed, "skipped_no_date": skipped_no_date}
