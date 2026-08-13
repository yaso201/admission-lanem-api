"""CONVOCATION-PREPA — convocation au concours (miroir du reçu : HTML → get_pdf → sendmail).

Déclenchée à la CONFIRMATION du frais de candidature si la session porte une `exam_date`
(aucun cas particulier de parcours). Envoi UNIQUE (drapeau `convocation_sent_at`). Trois accès :
pièce jointe e-mail · téléchargement candidat (suivi) · édition back-office (guichet).
Le contenu est FIGÉ (texte de référence, aucune reformulation). NON-BLOQUANT : un échec de
génération/envoi n'interrompt jamais la confirmation du paiement.

Rendu PDF : wkhtmltopdf 0.12.6 patched-qt — variables CSS OK (vérifié), polices SYSTÈME
(aucune dépendance réseau), fonds via print-color-adjust, logo allégé (~28 Ko).
"""

from __future__ import annotations

import frappe
from frappe.utils import escape_html, getdate, now_datetime
from frappe.utils.pdf import get_pdf

from admission.api._log import log_event
from admission.api.numbering import build_convocation_number

# A4 SANS marge moteur : la `.page` (210mm) porte ses propres marges (padding 16mm). Sinon
# wkhtmltopdf ajoute ses marges par défaut → débordement horizontal (droite rognée) + vertical
# (chaque section déborde sur 2 pages physiques → 4 pages au lieu de 2).
_PDF_OPTIONS = {"page-size": "A4", "margin-top": "0mm", "margin-bottom": "0mm",
                "margin-left": "0mm", "margin-right": "0mm"}

LOGO_URL = "/assets/admission/images/lanem-seal-sm.png"
MAPS_LINK = "https://maps.app.goo.gl/byoic5CEGYZgrybm6"
ADRESSE = "Quartier Ménontin, Rue de l'A.B.S.S.A — non loin de Canal 3 Bénin, Cotonou, Bénin"
ARRETE = ("Arrêté N° : 0754/MESRS/DC/SGM/DGES/DOSES/CJ/SA/028SGG25 du 18/09/2025<br>"
          "Adresse : Cotonou, Ménontin dans la rue de l'A.B.S.S.A<br>"
          "Tel : +229 01.54.54.50.54 / 01.99.72.51.51 · Email : bonjour@lanem.bj · Site : www.lanem.bj")

_JOURS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
_MOIS = ["", "janvier", "février", "mars", "avril", "mai", "juin", "juillet",
         "août", "septembre", "octobre", "novembre", "décembre"]


def _fmt_date_fr(d, weekday=False):
    if not d:
        return ""
    d = getdate(d)
    s = f"{d.day} {_MOIS[d.month]} {d.year}"
    return f"{_JOURS[d.weekday()]} {s}" if weekday else s


def _fmt_time_fr(t):
    if not t:
        return ""
    if hasattr(t, "total_seconds"):            # datetime.timedelta (champ Time)
        total = int(t.total_seconds())
        h, m = total // 3600, (total % 3600) // 60
    else:
        p = str(t).split(":")
        h, m = int(p[0]), int(p[1] if len(p) > 1 else 0)
    return f"{h:02d} h {m:02d}"


def ensure_convocation_number(applicant, session):
    """Numéro DÉFINITIF, attribué une seule fois (à l'émission), conservé en ré-édition.
    Idempotent : renvoie l'existant s'il est déjà posé."""
    if getattr(applicant, "convocation_number", None):
        return applicant.convocation_number
    num = build_convocation_number(session.exam_date)
    frappe.db.set_value("Admission Applicant", applicant.name, "convocation_number", num,
                        update_modified=False)
    applicant.convocation_number = num
    return num


def _pieces_non_verifiees(applicant):
    from admission.api.public import pieces_requises_non_verifiees
    return pieces_requises_non_verifiees(applicant)


def render_convocation_html(applicant, session, payment, numero, dossier_verifie):
    """HTML de la convocation (fonction pure). Contenu FIGÉ = texte de référence. A4 portrait,
    2 pages, en-tête/pied répétés. `dossier_verifie` False → bloc « sous réserve » affiché."""
    parcours = escape_html(getattr(session, "programme_label", "") or "")
    session_lib = escape_html(getattr(session, "label", "") or "")
    nom = escape_html(getattr(applicant, "applicant_name", "") or "")
    ref = escape_html(applicant.name)
    # Date de naissance : le dossier admission NE la stocke PAS (voir rapport). On ne l'INVENTE pas
    # → ligne omise. L'identification reste sans ambiguïté : numéro + nom + référence de dossier.
    naissance = ""
    date_conf = _fmt_date_fr(getattr(payment, "paid_at", None))
    date_epreuve = _fmt_date_fr(session.exam_date, weekday=True)
    heure_appel = _fmt_time_fr(getattr(session, "exam_call_time", None))
    heure_debut = _fmt_time_fr(getattr(session, "exam_start_time", None))
    salle = escape_html(getattr(session, "exam_room", "") or "")
    date_emission = _fmt_date_fr(now_datetime())

    salle_row = f"<tr><td>Salle</td><td>{salle}</td></tr>" if salle else ""
    naissance_row = f"<tr><td>Date de naissance</td><td>{naissance}</td></tr>" if naissance else ""

    reserve = "" if dossier_verifie else """
      <h2 class="section-title">Admission à concourir sous réserve</h2>
      <div class="reserve">
        <div class="callout-title">Dossier en cours de vérification</div>
        <p class="body">Votre dossier n'a pas encore fait l'objet d'une vérification complète. Vous êtes admis(e) à concourir sous réserve de sa régularité.</p>
        <p class="body" style="margin-bottom:0">Les pièces manquantes ou non conformes doivent être produites avant le début des épreuves. À défaut, votre copie ne pourra pas être prise en compte, quel que soit le résultat obtenu.</p>
      </div>"""

    def header():
        return f"""<header class="ph">
      <table class="ph-t"><tr>
        <td class="ph-logo"><img src="{LOGO_URL}" height="46" alt="LaNEM"/></td>
        <td class="ph-mid"><div class="ph-name">La Nouvelle École des Métiers</div>
          <div class="ph-sub">LaNEM · Cotonou, Bénin</div>
          <div class="ph-slogan">Oser, Innover, Bâtir</div></td>
        <td class="ph-right"><div class="ph-doc">Convocation au concours d'entrée</div>
          <div class="ph-doc-sub">{parcours} — {session_lib}</div>
          <div class="ph-note">Document personnel, ne peut être cédé.</div></td>
      </tr></table></header>"""

    def footer(n):
        return f"""<footer class="pf"><table class="pf-t"><tr>
        <td class="pf-l">{ARRETE}</td>
        <td class="pf-n">Page {n} / 2</td></tr></table></footer>"""

    return f"""<!doctype html><html lang="fr"><head><meta charset="utf-8">
<title>Convocation {numero} — LaNEM</title><style>
*{{box-sizing:border-box;-webkit-print-color-adjust:exact;print-color-adjust:exact}}
html,body{{margin:0;padding:0}}
body{{font-family:Helvetica,Arial,sans-serif;color:#1a1a1a;font-size:10.5pt;line-height:1.45}}
.page{{width:210mm;padding:12mm 15mm 9mm;page-break-after:always}}
.page:last-child{{page-break-after:auto}}
.ph-t,.pf-t{{width:100%;border-collapse:collapse}}
.ph{{border-bottom:2pt solid #1F124A;padding-bottom:8px;margin-bottom:11pt}}
.ph-logo{{width:54px;vertical-align:middle}} .ph-mid{{vertical-align:middle}} .ph-right{{vertical-align:middle;text-align:right}}
.ph-name{{font-size:12pt;font-weight:700;color:#1F124A}} .ph-sub{{font-size:8.5pt;color:#4D483D}}
.ph-slogan{{font-size:8pt;font-weight:700;letter-spacing:.12em;text-transform:uppercase;color:#B05B0A;margin-top:2px}}
.ph-doc{{font-size:8pt;font-weight:700;letter-spacing:.14em;text-transform:uppercase;color:#1F124A}}
.ph-doc-sub{{font-size:8pt;color:#4D483D;margin-top:2px}} .ph-note{{font-size:7pt;color:#6D6557;font-style:italic;margin-top:1px}}
.doc-title{{font-size:20pt;font-weight:700;color:#1F124A;margin:0 0 3pt}}
.doc-sub{{font-size:10pt;color:#4D483D;margin:0 0 10pt}}
.section-title{{font-size:10.5pt;font-weight:700;text-transform:uppercase;letter-spacing:.06em;color:#1F124A;margin:9pt 0 5pt;padding-bottom:3pt;border-bottom:.75pt solid #1F124A}}
table.kv,table.ep{{width:100%;border-collapse:collapse;font-size:10pt;margin-bottom:4pt}}
.kv td{{padding:4pt 8pt;border-bottom:.5pt solid #E4DFD3;vertical-align:top}}
.kv td:first-child{{width:38%;color:#4D483D;font-size:8.5pt;text-transform:uppercase;letter-spacing:.05em;font-weight:600}}
.kv td:last-child{{font-weight:600}}
.kv.boxed{{background:#F5F2FB;border-left:3pt solid #1F124A}}
.ep th{{text-align:left;padding:5pt 8pt;font-size:8pt;font-weight:700;text-transform:uppercase;color:#4D483D;border-bottom:1pt solid #1F124A}}
.ep td{{padding:4pt 8pt;border-bottom:.5pt solid #E4DFD3}} .ep td:last-child,.ep th:last-child{{text-align:right;width:30%}}
p.body{{margin:0 0 6pt;line-height:1.5}}
ul.plain{{margin:4pt 0 0;padding-left:14pt}} ul.plain li{{margin-bottom:4pt;line-height:1.5}}
.callout{{background:#F5F2FB;border-left:3pt solid #1F124A;padding:7pt 12pt;margin:5pt 0}}
.callout-title{{font-size:9pt;font-weight:700;text-transform:uppercase;color:#1F124A;margin-bottom:4pt}}
.reserve{{background:#FEF6E7;border-left:3pt solid #B05B0A;padding:7pt 12pt;margin:5pt 0;color:#5a3405}}
.reserve .callout-title{{color:#B05B0A}}
.maps{{font-size:9pt;margin:2pt 0 0}} .maps a{{color:#1F124A}}
.consignes div{{margin-bottom:4pt;line-height:1.45}} .consignes strong{{color:#1F124A}}
.contact{{font-size:10pt;font-weight:600;color:#1F124A}}
.sig{{text-align:right;margin-top:16pt}} .sig-line{{display:inline-block;border-top:.75pt solid #1a1a1a;padding-top:4pt;min-width:52mm;text-align:center}}
.sig-name{{font-weight:700;color:#1F124A}} .sig-role{{font-size:8.5pt;color:#4D483D}}
.issue{{margin-top:12pt;padding:6pt 10pt;background:#F5F2FB;border-left:3pt solid #1F124A;font-size:8.5pt;color:#4D483D}}
.pf{{margin-top:12pt;padding-top:8pt;border-top:.5pt solid #C9C2B2;font-size:7pt;color:#4D483D}}
.pf-n{{text-align:right;font-weight:600;white-space:nowrap;vertical-align:bottom}}
</style></head><body>
<section class="page">
  {header()}
  <h1 class="doc-title">Convocation au concours d'entrée</h1>
  <p class="doc-sub">{parcours} — {session_lib}</p>
  <h2 class="section-title" style="margin-top:0">Identification</h2>
  <table class="kv boxed">
    <tr><td>Numéro de convocation</td><td>{numero}</td></tr>
    <tr><td>Nom et prénoms</td><td>{nom}</td></tr>
    <tr><td>Référence du dossier</td><td>{ref}</td></tr>
    {naissance_row}
  </table>
  <p class="body" style="margin-top:11pt">Madame, Monsieur,</p>
  <p class="body">Votre candidature au {parcours} a bien été enregistrée et les frais de candidature ont été confirmés le {date_conf}.</p>
  <p class="body">Vous êtes convoqué(e) à l'épreuve du concours d'entrée dans les conditions précisées ci-dessous.</p>
  <h2 class="section-title">Date et lieu</h2>
  <table class="kv">
    <tr><td>Date</td><td>{date_epreuve}</td></tr>
    <tr><td>Heure d'appel</td><td>{heure_appel}</td></tr>
    <tr><td>Début des épreuves</td><td>{heure_debut}</td></tr>
    <tr><td>Lieu</td><td>LA NOUVELLE ECOLE DES METIERS</td></tr>
    <tr><td>Adresse</td><td>{ADRESSE}</td></tr>
    {salle_row}
  </table>
  <p class="maps">Retrouvez-nous plus facilement sur la carte : <a href="{MAPS_LINK}">{MAPS_LINK}</a></p>
  <h2 class="section-title">Déroulé des épreuves</h2>
  <p class="body">Le concours comporte trois épreuves&nbsp;:</p>
  <table class="ep"><thead><tr><th>Épreuve</th><th>Durée</th></tr></thead><tbody>
    <tr><td>Mathématiques</td><td>1 h 30 à 2 h</td></tr>
    <tr><td>Sciences physiques</td><td>1 h 30 à 2 h</td></tr>
    <tr><td>Culture générale</td><td>45 minutes</td></tr>
  </tbody></table>
  {footer(1)}
</section>
<section class="page">
  {header()}
  <h2 class="section-title" style="margin-top:0">À présenter obligatoirement</h2>
  <div class="callout"><div class="callout-title">Sans ces deux éléments, vous ne serez pas admis(e) à composer</div>
    <ul class="plain">
      <li>la présente convocation — sur papier ou présentée sur votre téléphone&nbsp;;</li>
      <li>une pièce d'identité originale comportant une photographie — carte nationale d'identité, passeport ou permis de conduire en cours de validité.</li>
    </ul></div>
  {reserve}
  <h2 class="section-title">Consignes</h2>
  <div class="consignes">
    <div><strong>Ponctualité.</strong> Présentez-vous à l'heure d'appel indiquée. Aucun candidat ne sera admis en salle après le début de la première épreuve.</div>
    <div><strong>Lieu.</strong> Les candidats se présentant dans un autre lieu que celui figurant sur la présente convocation ne sont pas admis à composer.</div>
    <div><strong>Matériel autorisé.</strong> Stylo à bille bleu ou noir, crayon, gomme, règle.</div>
    <div><strong>Interdit en salle.</strong> Téléphones et objets connectés — éteints et rangés pendant les épreuves —, documents personnels, notes et calculatrices.</div>
    <div><strong>Résultats.</strong> Ils vous seront communiqués par courriel dans un délai d'une semaine après le concours.</div>
  </div>
  <h2 class="section-title">Contact</h2>
  <p class="body">Pour toute difficulté relative à la présente convocation&nbsp;:</p>
  <p class="contact">bonjour@lanem.bj &nbsp;·&nbsp; +229 01 54 54 50 54</p>
  <div class="sig"><div class="sig-line"><div class="sig-name">L'équipe Admission</div><div class="sig-role">Service des admissions · LaNEM</div></div></div>
  <div class="issue">Convocation <strong>{numero}</strong> — émise le {date_emission}</div>
  {footer(2)}
</section>
</body></html>"""


def _frais1_confirmed_payment(applicant):
    """Le paiement Confirmed du frais de candidature (pour la date de confirmation). None si absent."""
    from admission.api.public import FRAIS1_FEE_TYPES
    names = frappe.get_all(
        "Applicant Fee",
        filters={"applicant": applicant.name, "fee_type": ["in", list(FRAIS1_FEE_TYPES)]},
        pluck="name", limit=1,
    )
    if not names:
        return None
    pay = frappe.get_all(
        "Applicant Fee Payment",
        filters={"applicant_fee": names[0], "payment_status": ["in", ["Confirmed", "Paid"]]},
        fields=["name", "paid_at"], order_by="paid_at desc", limit=1,
    )
    return frappe.get_doc("Applicant Fee Payment", pay[0].name) if pay else None


def build_convocation_pdf(applicant, session):
    """Génère la convocation à la demande (numéro DÉFINITIF attribué/relu, PDF). Réutilisée par
    l'e-mail ET les deux téléchargements (candidat + back-office) → une seule source de vérité."""
    numero = ensure_convocation_number(applicant, session)
    payment = _frais1_confirmed_payment(applicant)
    verifie = not _pieces_non_verifiees(applicant)
    html = render_convocation_html(applicant, session, payment, numero, verifie)
    return get_pdf(html, options=_PDF_OPTIONS), numero


def send_convocation(applicant, session):
    """Génère + attribue le numéro + PDF + e-mail (couleur d'accent). NON-BLOQUANT. Pose le
    drapeau `convocation_sent_at` (envoi unique). Ré-envoi ignoré si le drapeau est déjà posé."""
    if getattr(applicant, "convocation_sent_at", None):
        return  # déjà envoyée — envoi unique
    try:
        pdf, numero = build_convocation_pdf(applicant, session)
        verifie = not _pieces_non_verifiees(applicant)
        email = getattr(applicant, "email", None)
        if email:
            frappe.sendmail(
                recipients=[email],
                subject=f"Convocation au concours — {numero}",
                message=_email_body(applicant, session, numero, verifie),
                attachments=[{"fname": f"convocation-{numero}.pdf", "fcontent": pdf}],
            )
        frappe.db.set_value("Admission Applicant", applicant.name, "convocation_sent_at",
                            now_datetime(), update_modified=False)
        applicant.convocation_sent_at = now_datetime()
        log_event("convocation", "sent", dossier_id=applicant.name, ref=numero)
    except Exception:
        # NON-BLOQUANT (comme le reçu) : un échec PDF/SMTP n'interrompt jamais la confirmation.
        frappe.log_error(title="Convocation send failed", message=frappe.get_traceback())
        log_event("convocation", "send_failed", dossier_id=applicant.name, level="error")


def _email_body(applicant, session, numero, verifie):
    """Courriel d'accompagnement — RÉUTILISE le gabarit candidat existant, seule la couleur
    d'accent change (status='convocation') pour distinguer des autres notifications."""
    from admission.api.email_template import _portal_link, render_candidate_email
    nom = getattr(applicant, "applicant_name", "") or ""
    dossier = applicant.name
    date_ep = _fmt_date_fr(session.exam_date, weekday=True)
    return render_candidate_email(
        nom=nom, dossier=dossier, filiere=getattr(session, "programme_label", "") or "",
        status="convocation",
        intro="Votre paiement a été confirmé : <strong>votre convocation au concours d'entrée "
              "est disponible</strong>. Elle est jointe à ce message et reste téléchargeable à "
              "tout moment depuis votre espace de suivi.",
        meta=[("Numéro de convocation", numero, True),
              ("Date de l'épreuve", date_ep),
              ("Heure d'appel", _fmt_time_fr(getattr(session, "exam_call_time", None))),
              ("Début des épreuves", _fmt_time_fr(getattr(session, "exam_start_time", None)))],
        attachment={"label": "Convocation au concours", "fname": f"convocation-{numero}.pdf"},
        secondary={"label": "Ouvrir mon espace de suivi", "url": _portal_link(applicant)},
        signoff="Nous vous souhaitons une bonne préparation. — L'équipe des admissions, LaNEM",
        preheader=f"Convocation {numero} · appel à "
                  f"{_fmt_time_fr(getattr(session, 'exam_call_time', None))}. Présentez convocation + pièce d'identité.",
        subject=f"Convocation au concours — {numero}",
    )
