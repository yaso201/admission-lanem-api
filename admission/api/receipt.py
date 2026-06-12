"""Reçu de paiement PDF mailé au candidat (DEC-198) — PAY-CONFIRM-AGENT phase c.

À la confirmation d'un paiement (les 3 canaux), génère un reçu PDF (HTML→PDF via get_pdf) et
l'envoie au candidat avec les mentions légales (REFUND_POLICY) et l'identité école.
NON-BLOQUANT : un échec (wkhtmltopdf/SMTP absent, etc.) n'interrompt JAMAIS la confirmation.
"""

import frappe
from frappe.utils.pdf import get_pdf

from admission.api._log import log_event

# Identité institutionnelle (portée depuis le front ecole.ts — source unique côté backend pour le reçu).
ECOLE = {
    "name": "LaNEM",
    "fullName": "LaNEM — La Nouvelle École des Métiers",
    "slogan": "Oser, Innover, Bâtir !",
    "email": "bonjour@lanem.bj",
    "phone": "+229 01 54 54 50 54",
    "address": "Quartier Menontin, Rue de l'A.B.S.S.A, Cotonou, BÉNIN",
}
LOGO_URL = "/assets/admission/images/lanem-seal.png"


def _get_legal_text():
    """Mention légale paiement (REFUND_POLICY actif). Non-bloquant : "" si indisponible."""
    try:
        from admission.api.legal import _get_active_legal_document
        doc = _get_active_legal_document("REFUND_POLICY")
        return (doc.content_text or "") if doc else ""
    except Exception:
        return ""


def render_receipt_html(payment, applicant, fee, legal_text=""):
    """Construit le HTML du reçu (fonction pure, sans I/O)."""
    amount = f"{(payment.amount_xof or 0):,.0f}".replace(",", " ")
    rows = [
        ("Reçu n°", payment.receipt_number),
        ("Date", payment.paid_at),
        ("Dossier", payment.applicant),
        ("Candidat", getattr(applicant, "applicant_name", "") or ""),
        ("Mode de paiement", payment.payment_mode),
        ("Montant", f"{amount} FCFA"),
    ]
    rows_html = "".join(
        f"<tr><td class='k'>{k}</td><td class='v'>{v}</td></tr>" for k, v in rows
    )
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>
  body {{ font-family: Helvetica, Arial, sans-serif; color: #1F124A; padding: 32px; }}
  table.head {{ width: 100%; border-bottom: 3px solid #D97706; padding-bottom: 12px; }}
  table.head td {{ vertical-align: middle; border: none; }}
  td.id {{ font-size: 12px; line-height: 1.4; }}
  h1 {{ color: #D97706; font-size: 20px; margin: 24px 0 8px; }}
  table.details {{ width: 100%; border-collapse: collapse; margin-top: 8px; }}
  table.details td {{ padding: 8px 6px; border-bottom: 1px solid #eee; font-size: 13px; }}
  td.k {{ color: #6b6b6b; width: 40%; }}
  td.v {{ font-weight: 600; }}
  .legal {{ margin-top: 20px; font-size: 11px; color: #6b6b6b; }}
  .slogan {{ margin-top: 16px; color: #D97706; font-style: italic; font-size: 12px; }}
</style></head><body>
  <table class="head"><tr>
    <td width="72"><img src="{LOGO_URL}" height="56" alt="{ECOLE['name']}"/></td>
    <td class="id"><strong>{ECOLE['fullName']}</strong><br>{ECOLE['address']}<br>{ECOLE['email']} · {ECOLE['phone']}</td>
  </tr></table>
  <h1>Reçu de paiement</h1>
  <table class="details">{rows_html}</table>
  <p class="legal">{legal_text}</p>
  <p class="slogan">{ECOLE['slogan']}</p>
</body></html>"""


_MODE_FR = {"Cash": "Espèces", "Bank": "Virement bancaire", "Online": "Paiement en ligne"}


def _email_body(applicant, payment):
    """Corps du mail d'accompagnement du reçu — LOT M : template `paiement` (hero montant
    + reçu n° + mention PJ). Corrige l'anomalie d'audit : le nom n'était PAS échappé ici
    (render échappe toutes les entrées). Le PDF joint reste inchangé (render_receipt_html)."""
    from frappe.utils import formatdate

    from admission.api.email_template import _portal_link, render_candidate_email
    nom = getattr(applicant, "applicant_name", "") or ""
    dossier = getattr(payment, "applicant", "") or ""
    montant = f"{(payment.amount_xof or 0):,.0f}".replace(",", " ")
    mode = _MODE_FR.get(payment.payment_mode, payment.payment_mode or "")
    recu = payment.receipt_number or ""
    return render_candidate_email(
        nom=nom, dossier=dossier, filiere="", status="paiement",
        intro="Votre paiement a bien été confirmé. Vous trouverez votre reçu officiel "
              "en pièce jointe — conservez-le, il fait foi.",
        meta=[("Candidat", nom), ("Dossier", dossier, True), ("Mode de paiement", mode)],
        paiement={"montant": montant, "recu": recu,
                  "date": formatdate(payment.paid_at, "d MMMM yyyy")},
        attachment={"label": "Reçu officiel", "fname": f"recu-{recu}.pdf"},
        secondary={"label": "Ouvrir mon espace candidat", "url": _portal_link(applicant)},
        signoff="Merci de votre confiance. — Le Service des admissions, LaNEM",
        preheader=f"Paiement confirmé · {montant} FCFA. Votre reçu officiel est joint à cet e-mail.",
        subject=f"Reçu de paiement — {recu}",
    )


def send_payment_receipt(payment, applicant=None, fee=None):
    """Génère et envoie le reçu PDF au candidat. NON-BLOQUANT (best-effort)."""
    try:
        applicant = applicant or frappe.get_doc("Admission Applicant", payment.applicant)
        email = getattr(applicant, "email", None)
        if not email:
            log_event("payment_receipt", "skipped_no_email", dossier_id=getattr(payment, "applicant", None))
            return
        if fee is None and getattr(payment, "applicant_fee", None):
            fee = frappe.get_doc("Applicant Fee", payment.applicant_fee)
        html = render_receipt_html(payment, applicant, fee, legal_text=_get_legal_text())
        pdf = get_pdf(html)
        frappe.sendmail(
            recipients=[email],
            subject=f"Reçu de paiement — {payment.receipt_number}",
            message=_email_body(applicant, payment),
            attachments=[{"fname": f"recu-{payment.receipt_number}.pdf", "fcontent": pdf}],
        )
        log_event("payment_receipt", "sent", dossier_id=getattr(payment, "applicant", None), ref=payment.receipt_number)
    except Exception:
        log_event("payment_receipt", "failed", dossier_id=getattr(payment, "applicant", None), level="warning")
        frappe.logger("receipt").warning(f"Receipt send failed (non-blocking): {frappe.get_traceback()}")
