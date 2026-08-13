"""DOCUMENTS-JOUR-EPREUVE — liste d'émargement + état du concours (imprimables back-office).

Génération À LA DEMANDE (reflète l'état à l'impression — un candidat réglé au guichet apparaît).
Réutilise le socle convocation (constantes ECOLE, logo allégé, _fmt_date_fr, les 3 correctifs de
rendu + la leçon pagination). PAYSAGE. Multi-page : `<thead>` répété par le moteur (en-tête doc +
colonnes) + numérotation « Page X sur Y » par l'option moteur (jamais du CSS positionné). AUCUNE
garde de session (jour de l'épreuve = session fermée). AUCUNE saisie de présence : rempli à la main.
"""

from __future__ import annotations

import frappe
from frappe.utils import escape_html
from frappe.utils.pdf import get_pdf

from admission.api.convocation import ADRESSE, LOGO_URL, _fmt_date_fr, _fmt_time_fr
from admission.api.public import FRAIS1_FEE_TYPES, pieces_requises_non_verifiees

BLANK_LINES = 10   # lignes vierges pour le guichet du matin (§ « une dizaine »)


def _pdf_landscape(footer_text):
    return {"orientation": "Landscape", "page-size": "A4",
            "margin-top": "8mm", "margin-bottom": "15mm", "margin-left": "8mm", "margin-right": "8mm",
            "footer-center": footer_text, "footer-font-size": "8", "footer-spacing": "4"}


def _convoques_of_session(session_name):
    """Convoqués d'une session = frais de candidature CONFIRMÉ. Tri alphabétique. `verifie` pilote
    la mention « sous réserve ». Lecture pure (aucune garde de session)."""
    session = frappe.get_doc("Admission Session", session_name)
    rows = frappe.get_all("Admission Applicant", filters={"session": session_name},
                          fields=["name", "applicant_name", "programme_label", "convocation_number"])
    convoques = []
    for a in rows:
        fee = frappe.get_all("Applicant Fee",
                             filters={"applicant": a.name, "fee_type": ["in", list(FRAIS1_FEE_TYPES)]},
                             pluck="name", limit=1)
        if not fee:
            continue
        if not frappe.db.exists("Applicant Fee Payment",
                                {"applicant_fee": fee[0], "payment_status": ["in", ["Confirmed", "Paid"]]}):
            continue
        verifie = not pieces_requises_non_verifiees(frappe.get_doc("Admission Applicant", a.name))
        convoques.append({
            "numero": a.convocation_number or "—",
            "nom": (a.applicant_name or a.name),
            "parcours": a.programme_label or "",
            "verifie": verifie,
        })
    convoques.sort(key=lambda c: c["nom"].lower())
    return session, convoques


def _doc_header(session, titre):
    salle = escape_html(getattr(session, "exam_room", "") or "")
    salle_txt = f" · {salle}" if salle else ""   # la valeur salle est affichée telle quelle (peut déjà porter « Salle »)
    return (f'<td class="dh" colspan="{{cols}}"><table class="dh-t"><tr>'
            f'<td class="dh-logo"><img src="{LOGO_URL}" height="34" alt="LaNEM"/></td>'
            f'<td class="dh-mid"><span class="dh-name">LA NOUVELLE ECOLE DES METIERS</span>'
            f'<span class="dh-title"> — {titre}</span><br>'
            f'<span class="dh-sub">{escape_html(getattr(session, "programme_label", "") or "")} · '
            f'{escape_html(getattr(session, "label", "") or "")} · '
            f'{_fmt_date_fr(session.exam_date, weekday=True)} · '
            f'appel {_fmt_time_fr(getattr(session, "exam_call_time", None))}{salle_txt}</span></td>'
            f'</tr></table></td>')


_STYLE = """<style>
*{box-sizing:border-box;-webkit-print-color-adjust:exact;print-color-adjust:exact}
body{font-family:Helvetica,Arial,sans-serif;font-size:9pt;color:#1a1a1a;margin:0}
table{width:100%;border-collapse:collapse}
.dh{border-bottom:2pt solid #1F124A;padding:0 0 6pt}
.dh-t{width:100%} .dh-t td{border:none;padding:0}
.dh-logo{width:42px;vertical-align:middle} .dh-mid{vertical-align:middle}
.dh-name{font-size:12pt;font-weight:700;color:#1F124A} .dh-title{font-size:12pt;font-weight:700;color:#B05B0A}
.dh-sub{font-size:8.5pt;color:#4D483D}
thead th{background:#F5F2FB;border:.5pt solid #1F124A;padding:5pt 4pt;font-size:7.5pt;font-weight:700;text-transform:uppercase;letter-spacing:.03em;color:#1F124A;text-align:center}
thead th.l{text-align:left}
tbody td{border:.5pt solid #C9C2B2;padding:5pt 5pt;height:24pt;vertical-align:middle}
tbody td.c{text-align:center} tbody td.num{font-family:'Courier New',monospace;text-align:center}
.reserve{color:#B05B0A;font-weight:700;font-size:7.5pt;white-space:nowrap}
.blank td{height:24pt}
.section-title{font-size:11pt;font-weight:700;text-transform:uppercase;color:#1F124A;margin:12pt 0 6pt;padding-bottom:3pt;border-bottom:.75pt solid #1F124A}
.kv{width:60%;border-collapse:collapse;margin-bottom:6pt}
.kv td{border-bottom:.5pt solid #E4DFD3;padding:5pt 8pt;font-size:10pt}
.kv td.k{color:#4D483D;width:60%} .kv td.v{font-weight:700;text-align:right}
.box{border:.75pt solid #1F124A;padding:8pt 10pt;margin:6pt 0;min-height:60pt}
.box-title{font-size:9pt;font-weight:700;text-transform:uppercase;color:#1F124A;margin-bottom:4pt}
.grid2{width:100%} .grid2 td{width:50%;vertical-align:top;padding:0 6pt;border:none}
.sig-row{margin-top:16pt} .sig-cell{display:inline-block;min-width:60mm;border-top:.75pt solid #1a1a1a;padding-top:4pt;margin-right:18mm;font-size:9pt;text-align:center}
.foot-block{margin-top:14pt;border-top:1pt solid #1F124A;padding-top:8pt}
</style>"""


def render_emargement_html(session, convoques):
    """Liste d'émargement — paysage, 7 colonnes (date de naissance omise, Option A), 3 colonnes de
    signature (3 épreuves), mention « sous réserve », lignes vierges numérotées, en-tête répété."""
    header = _doc_header(session, "Liste d'émargement").format(cols=7)
    body = ""
    n = 0
    for c in convoques:
        n += 1
        reserve = ' <span class="reserve">— sous réserve</span>' if not c["verifie"] else ""
        body += (f'<tr><td class="c">{n}</td><td class="num">{escape_html(c["numero"])}</td>'
                 f'<td class="l">{escape_html(c["nom"])}{reserve}</td>'
                 f'<td></td><td></td><td></td><td></td></tr>')
    for _ in range(BLANK_LINES):   # lignes vierges (guichet du matin) — NON comptées au pied
        n += 1
        body += f'<tr class="blank"><td class="c">{n}</td><td></td><td></td><td></td><td></td><td></td><td></td></tr>'

    total = len(convoques)
    reserve_n = sum(1 for c in convoques if not c["verifie"])
    foot = (f'<div class="foot-block"><table class="grid2"><tr>'
            f'<td><strong>Total convoqués : {total}</strong>'
            f'{f" · dont {reserve_n} sous réserve" if reserve_n else ""}<br><br>'
            f'Présents : __________&nbsp;&nbsp;&nbsp; Absents : __________</td>'
            f'<td style="text-align:right">Le responsable de salle&nbsp;:<br><br>'
            f'<span class="sig-cell">Nom et signature</span></td></tr></table></div>')

    return (f'<!doctype html><html lang="fr"><head><meta charset="utf-8"><title>Émargement</title>'
            f'{_STYLE}</head><body><table>'
            f'<thead><tr>{header}</tr>'
            f'<tr><th>N°</th><th>N° de convocation</th><th class="l">Nom et prénoms</th>'
            f'<th>Mathématiques</th><th>Sciences physiques</th><th>Culture générale</th>'
            f'<th>Observations</th></tr></thead>'
            f'<tbody>{body}</tbody></table>{foot}</body></html>')


def render_etat_html(session, convoques):
    """État du concours — récapitulatif chiffré (dont répartition par parcours) + zones à remplir."""
    header = _doc_header(session, "État du concours").format(cols=1)
    total = len(convoques)
    reserve_n = sum(1 for c in convoques if not c["verifie"])
    from collections import Counter
    par_parcours = Counter(c["parcours"] or "—" for c in convoques)
    parcours_rows = "".join(
        f'<tr><td class="k">{escape_html(p)}</td><td class="v">{cnt}</td></tr>'
        for p, cnt in sorted(par_parcours.items()))

    return (f'<!doctype html><html lang="fr"><head><meta charset="utf-8"><title>État du concours</title>'
            f'{_STYLE}</head><body>'
            f'<table><thead><tr>{header}</tr></thead></table>'
            f'<h2 class="section-title">Récapitulatif</h2>'
            f'<table class="kv"><tr><td class="k">Nombre de convoqués</td><td class="v">{total}</td></tr>'
            f'<tr><td class="k">Dont dossier sous réserve</td><td class="v">{reserve_n}</td></tr>'
            f'{parcours_rows}</table>'
            f'<h2 class="section-title">Décompte</h2>'
            f'<table class="grid2"><tr><td><div class="box"><div class="box-title">Présents</div></div></td>'
            f'<td><div class="box"><div class="box-title">Absents (à lister)</div></div></td></tr></table>'
            f'<h2 class="section-title">Incidents</h2><div class="box"><div class="box-title">Consignation libre</div></div>'
            f'<h2 class="section-title">Signatures</h2>'
            f'<div class="sig-row"><span class="sig-cell">Responsable de salle</span>'
            f'<span class="sig-cell">Surveillant</span><span class="sig-cell">Surveillant</span></div>'
            f'</body></html>')


def build_emargement_pdf(session_name):
    session, convoques = _convoques_of_session(session_name)
    html = render_emargement_html(session, convoques)
    footer = f"Page [page] sur [topage]  ·  {len(convoques)} convoqués"
    return get_pdf(html, options=_pdf_landscape(footer)), session


def build_etat_pdf(session_name):
    session, convoques = _convoques_of_session(session_name)
    html = render_etat_html(session, convoques)
    footer = f"État du concours  ·  Page [page] sur [topage]  ·  {len(convoques)} convoqués"
    return get_pdf(html, options=_pdf_landscape(footer)), session
