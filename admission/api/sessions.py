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


def _state(session) -> str:
    """État lifecycle : `lifecycle_state` (source de vérité) quand c'est un état VALIDE, sinon repli
    sur le miroir is_open (rows historiques sans le champ, valeur corrompue) — Open ⟺ is_open=1,
    sinon Closed. GESTION-CALENDRIER."""
    st = _field(session, "lifecycle_state")
    if st in ("Draft", "Open", "Closed"):
        return st
    return "Open" if _field(session, "is_open") else "Closed"


def set_lifecycle(name, state, update_modified=True):
    """Transition d'état au niveau base (chemins hors doc.save : auto-fermeture, clôture, endpoints).
    Pose lifecycle_state ET son miroir is_open ensemble — zéro divergence."""
    frappe.db.set_value(
        "Admission Session", name,
        {"lifecycle_state": state, "is_open": 1 if state == "Open" else 0},
        update_modified=update_modified,
    )


def is_session_selectable(session) -> bool:
    """Sélectionnable = état Open ET non échue. Utilisé par le catalogue ET la garde.
    Brouillon/Fermée → jamais sélectionnable. `closes_on` absent = pas d'échéance."""
    if not session:
        return False
    if _state(session) != "Open":
        return False
    closes_on = _field(session, "closes_on")
    if closes_on and getdate(closes_on) < _today():
        return False  # échue
    return True


def session_display_status(session) -> str:
    """'brouillon' (Draft → invisible du candidat, masqué du catalogue) ·
    'a_venir' (Open + non échue → sélectionnable) ·
    'echue' (Open/Closed, date dépassée → visible, NON sélectionnable) ·
    'fermee' (Closed, date non dépassée → masquée du catalogue)."""
    st = _state(session)
    if st == "Draft":
        return "brouillon"
    closes_on = _field(session, "closes_on")
    echue = bool(closes_on) and getdate(closes_on) < _today()
    if echue:
        return "echue"
    if st == "Open":
        return "a_venir"
    return "fermee"


def close_expired_sessions():
    """Tâche quotidienne : ferme les sessions échues (`closes_on < aujourd'hui`, Porto-Novo).
    Ne considère QUE les sessions Open → les BROUILLONS sont ignorés par construction (GK2).
    Idempotente (ne re-ferme pas), journalisée, NE TOUCHE AUCUN DOSSIER, ne rouvre jamais.
    `closes_on` absent → non fermée, SIGNALÉE (GS6). Enregistrable via scheduler ou bench."""
    today = _today()
    closed, skipped_no_date = [], []
    for s in frappe.get_all("Admission Session", filters={"lifecycle_state": "Open"},
                            fields=["name", "closes_on"]):
        if not s.closes_on:
            skipped_no_date.append(s.name)
            frappe.logger("session_lifecycle").warning(
                f"Session {s.name} sans closes_on — non fermée (à dater manuellement)."
            )
            continue
        if getdate(s.closes_on) < today:
            set_lifecycle(s.name, "Closed")
            closed.append({"session": s.name, "closes_on": str(s.closes_on)})
            frappe.logger("session_lifecycle").info(
                f"Session {s.name} fermée automatiquement (closes_on {s.closes_on} < {today})."
            )
    if closed:
        frappe.db.commit()
    return {"today": str(today), "closed": closed, "skipped_no_date": skipped_no_date}
