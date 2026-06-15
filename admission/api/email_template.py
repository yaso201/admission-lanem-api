"""email_template.py — Template e-mail candidat LaNEM « email-safe » (LOT M).

Port 1:1 du handoff specifications/email-handoff (source de vérité design :
email-template.js — toute évolution visuelle doit être répercutée des deux côtés).
Un template UNIQUE piloté par STATUS ; sous-blocs optionnels (meta, OTP, paiement,
instructions SOP, notes, bourses, motif, pièce jointe, CTA). Fonctions PURES :
aucun envoi ici — les envois restent dans notifications.py (socle non-bloquant).

Adaptations locales vs handoff (assumées, documentées) :
  - SCHOOL dérive de receipt.ECOLE (source unique d'identité) + logo URL ABSOLUE
    (frappe.utils.get_url — affichage réel chez le client mail = URL publique, recette) ;
  - BANK = coordonnées RÉELLES Coris Bank (A0.3 — specifications/RIB.pdf, copié en
    public/docs/rib_coris_bank.pdf pour la pièce jointe du mail SOP virement) ;
  - liens légaux du footer branchés sur le portail candidat (conf candidate_portal_url) ;
  - helper _portal_link : lien tokenisé (A0.2 : reprise multi-appareil, OTP exigé à
    l'arrivée pour les actions — double barrière) ou suivi générique sans token.
"""

import frappe
from frappe.utils import escape_html as _esc

from admission.api.receipt import ECOLE

# ── Identité (SOURCE UNIQUE : receipt.ECOLE) ─────────────────────────────────
SCHOOL = {
    "name": ECOLE["name"],
    "full": ECOLE["fullName"],
    "slogan": ECOLE["slogan"],
    "email": ECOLE["email"],
    "tel": ECOLE["phone"],
    "address": ECOLE["address"],
    "logo_url": None,  # résolu dynamiquement par _logo_src() (URL absolue du site)
}

LOGO_ASSET = "/assets/admission/images/lanem-seal.png"

# ── Coordonnées bancaires RÉELLES (A0.3 — specifications/RIB.pdf) ────────────
def get_bank():
    """LOT RIB-SETTINGS — compte d'encaissement depuis Admission Settings (source UNIQUE,
    éditée par le rôle Admission Finance). None si non configuré → les consommateurs
    masquent le canal virement (jamais de coordonnées périmées codées en dur)."""
    values = frappe.db.get_value(
        "Admission Settings", "Admission Settings",
        ["rib_banque", "rib_titulaire", "rib_iban", "rib_bic", "rib_pdf", "rib_version"],
        as_dict=True) or {}
    if not (values.get("rib_iban") and values.get("rib_banque")):
        return None
    return {"banque": values.rib_banque, "titulaire": values.rib_titulaire or "",
            "iban": values.rib_iban, "bic": values.rib_bic or "",
            "pdf_url": values.rib_pdf, "version": values.rib_version or ""}


def _portal_base():
    """Base du portail candidat (front applicant). Conf `candidate_portal_url` ;
    défaut dev = Astro local. En recette/prod : poser l'URL publique (OPS)."""
    return (frappe.conf.get("candidate_portal_url") or "http://localhost:4321").rstrip("/")


def _portal_link(applicant=None, token=None):
    """Lien vers l'espace candidat.

    A0.2 : avec `token` (détenu en clair UNIQUEMENT à la création/rotation/recovery),
    lien de REPRISE tokenisé — l'OTP reste exigé à l'arrivée pour les actions (SEC-4,
    double barrière). Sans token : page de suivi générique (le candidat utilise son
    lien de reprise reçu par mail)."""
    base = _portal_base()
    if token and applicant is not None:
        return f"{base}/reprise?dossier={applicant.name}&token={token}"
    return f"{base}/suivi"


def _legal_link(slug):
    return f"{_portal_base()}/legal/{slug}"


def _logo_src():
    """URL ABSOLUE du sceau (les clients mail ne résolvent pas les chemins relatifs).
    En dev l'URL locale ne s'affichera que localement — publique en recette (OPS)."""
    try:
        return frappe.utils.get_url(LOGO_ASSET)
    except Exception:
        return None

# Couleurs chartées emela / LaNEM
INK = "#1F124A"
ACCENT = "#F59E0B"
PAPER = "#FFFFFF"
CANVAS = "#F0EDE5"
SAND_50 = "#F8F6F1"
T1, T2, T3, HAIR = "#1F124A", "#4D483D", "#574F40", "#C9C2B2"

FONT = "'Plus Jakarta Sans',-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Arial,sans-serif"
MONO = "'JetBrains Mono','SFMono-Regular',Consolas,'Courier New',monospace"

# STATUT par réponse — source unique
STATUS = {
    # ─ DÉCISIONS — couleur sémantique propre à chaque état du workflow ─
    "admissible": {"band": "#1D5FA8", "soft": "#EAF1FB", "eyebrow": "Décision",      "label": "Admissible",              "mark": "✓"},
    "attente":    {"band": "#2D1B69", "soft": "#F5F2FB", "eyebrow": "Décision",      "label": "Liste d’attente",         "mark": "•"},
    "conditionnelle": {"band": "#9A6B16", "soft": "#FBF6E9", "eyebrow": "Décision",  "label": "Admis sous réserve",       "mark": "✓"},
    "accepte":    {"band": "#047857", "soft": "#ECFDF5", "eyebrow": "Décision",      "label": "Admission confirmée",      "mark": "✓"},
    "refuse":     {"band": "#B91C1C", "soft": "#FEF2F2", "eyebrow": "Décision",      "label": "Candidature non retenue", "mark": ""},
    "admis":      {"band": "#047857", "soft": "#ECFDF5", "eyebrow": "Décision",      "label": "Admis",                   "mark": "✓"},  # alias rétro-compat (= accepte)
    # ─ DOSSIER — action requise sur le dossier ─
    "complement": {"band": "#B05B0A", "soft": "#FEF6E7", "eyebrow": "Votre dossier", "label": "Complément requis",        "mark": "!"},
    # ─ TRANSACTION financière ─
    "paiement":   {"band": "#047857", "soft": "#ECFDF5", "eyebrow": "Paiement",      "label": "Paiement confirmé",       "mark": "✓"},
    # ─ SÉCURITÉ ─
    "otp":        {"band": "#2D1B69", "soft": "#F5F2FB", "eyebrow": "Sécurité",      "label": "Vérification e-mail",         "mark": ""},
    # ─ CHANGEMENT D'ÉTAT (NON-décision) — violet unifié, seul le corps change ─
    "compte":     {"band": "#5B3FA8", "soft": "#F5F2FB", "eyebrow": "Bienvenue",      "label": "Compte créé",                "mark": "✓"},
    "sop":        {"band": "#5B3FA8", "soft": "#F5F2FB", "eyebrow": "Soumission",    "label": "Soumission provisoire",       "mark": "•"},
    "etu":        {"band": "#5B3FA8", "soft": "#F5F2FB", "eyebrow": "Votre dossier", "label": "Dossier en étude",            "mark": "•"},
    "inscrit":    {"band": "#5B3FA8", "soft": "#F5F2FB", "eyebrow": "Inscription",   "label": "Inscription confirmée",       "mark": "✓"},
}


# ── Sous-blocs ───────────────────────────────────────────────────────────────
def _logo(tone):
    light = tone == "light"
    name = "#FFFFFF" if light else INK
    sub = "rgba(255,255,255,.72)" if light else "#574F40"
    # Sceau réel (URL absolue) dans le FOOTER (fond clair) ; le bandeau ink d'en-tête
    # garde le wordmark texte — lisible quelles que soient les images bloquées.
    logo_url = SCHOOL.get("logo_url") or _logo_src()
    if logo_url and not light:
        return (f'<img src="{logo_url}" width="56" alt="{SCHOOL["full"]}" '
                f'style="display:block;border:0;height:auto;border-radius:8px;">')
    box_bg = ACCENT if light else INK
    box_fg = INK if light else "#FFFFFF"
    return (
        f'<table role="presentation" cellpadding="0" cellspacing="0" border="0"><tr>'
        f'<td width="40" height="40" align="center" valign="middle" style="background:{box_bg};border-radius:9px;'
        f'font-family:{FONT};font-size:14px;font-weight:800;color:{box_fg};">LN</td>'
        f'<td style="padding-left:11px;font-family:{FONT};line-height:1.15;">'
        f'<div style="font-size:18px;font-weight:800;letter-spacing:-.02em;color:{name};">LaNEM</div>'
        f'<div style="font-size:10.5px;font-weight:500;color:{sub};margin-top:2px;">La Nouvelle École des Métiers</div>'
        f'</td></tr></table>'
    )


def _meta_grid(nom, dossier, filiere, meta=None):
    def cell(k, v, w, mono=False):
        ff = MONO if mono else FONT
        fs = "12px" if mono else "13px"
        return (
            f'<td class="sm-stack" width="{w}%" style="width:{w}%;padding:12px 14px;background:{PAPER};'
            f'border-right:1px solid {HAIR};vertical-align:top;">'
            f'<div style="font-family:{FONT};font-size:10px;font-weight:700;letter-spacing:.06em;'
            f'text-transform:uppercase;color:#857B62;">{k}</div>'
            f'<div style="font-family:{ff};font-size:{fs};font-weight:700;color:{T1};margin-top:4px;">{v}</div></td>'
        )
    # meta : [(label, valeur, mono?), …] (1 à 3 cellules) — sinon défaut Candidat/Dossier/Filière
    src = meta or [("Candidat", nom), ("Dossier", dossier, True), ("Filière", filiere)]
    w = round(100 / len(src), 2)
    cells = "".join(cell(c[0], _esc(c[1]), w, c[2] if len(c) > 2 else False) for c in src)
    return (
        f'<tr><td style="padding:0 30px;" class="sm-px">'
        f'<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" '
        f'style="margin:22px 0;border:1px solid {HAIR};border-radius:6px;border-collapse:separate;overflow:hidden;">'
        f'<tr>{cells}</tr></table></td></tr>'
    )


def _otp_code(otp):
    """Code de vérification proéminent (6 chiffres) + validité + rappel sécurité. otp={code,minutes}."""
    if not otp:
        return ""
    minutes = otp.get("minutes", 10)
    return (
        f'<tr><td style="padding:8px 30px 0;" class="sm-px">'
        f'<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" '
        f'style="background:#F5F2FB;border:1px solid #DDD6FE;border-radius:12px;border-collapse:separate;overflow:hidden;">'
        f'<tr><td align="center" style="padding:24px 20px 20px;">'
        f'<div style="font-family:{FONT};font-size:11px;font-weight:700;letter-spacing:.1em;text-transform:uppercase;color:#7C6BB0;">Votre code de vérification</div>'
        f'<div style="font-family:{MONO};font-size:42px;font-weight:600;letter-spacing:.34em;color:{INK};margin-top:12px;padding-left:.34em;line-height:1;">{_esc(otp["code"])}</div>'
        f'<div style="font-family:{FONT};font-size:13px;color:{T3};margin-top:14px;">Ce code est valable <strong style="color:{T2};">{_esc(str(minutes))}&nbsp;minutes</strong>.</div>'
        f'</td></tr>'
        f'<tr><td style="padding:0 22px 20px;">'
        f'<div style="font-family:{FONT};font-size:11.5px;line-height:1.55;color:#6E649A;border-top:1px solid #E6E0F5;padding-top:14px;text-align:center;">'
        f'Ne partagez ce code avec personne — LaNEM ne vous le demandera jamais. '
        f'Si vous n’êtes pas à l’origine de cette demande, ignorez cet e-mail.</div>'
        f'</td></tr></table></td></tr>'
    )


def _payment_summary(paiement):
    """Hero du reçu : montant proéminent + reçu n° + date (thème vert). paiement={montant,recu,date}."""
    if not paiement:
        return ""
    OK, OK_SOFT, OK_HAIR = "#047857", "#ECFDF5", "#BBF7D0"
    return (
        f'<tr><td style="padding:6px 30px 0;" class="sm-px">'
        f'<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" '
        f'style="background:{OK_SOFT};border:1px solid {OK_HAIR};border-radius:10px;border-collapse:separate;overflow:hidden;"><tr>'
        f'<td class="sm-stack" style="padding:18px 20px;vertical-align:top;">'
        f'<div style="font-family:{FONT};font-size:10.5px;font-weight:700;letter-spacing:.08em;text-transform:uppercase;color:{OK};">Montant réglé</div>'
        f'<div style="font-family:{MONO};font-size:30px;font-weight:600;letter-spacing:-.02em;color:{OK};margin-top:4px;line-height:1.1;">'
        f'{_esc(paiement["montant"])}<span style="font-family:{FONT};font-size:14px;font-weight:700;color:#0B7A52;">&nbsp;FCFA</span></div></td>'
        f'<td class="sm-stack" align="right" valign="top" style="padding:18px 20px;vertical-align:top;border-left:1px solid {OK_HAIR};">'
        f'<div style="font-family:{FONT};font-size:10px;font-weight:700;letter-spacing:.06em;text-transform:uppercase;color:#6F9B86;">Reçu n°</div>'
        f'<div style="font-family:{MONO};font-size:13px;font-weight:700;color:{T1};margin-top:3px;">{_esc(paiement["recu"])}</div>'
        f'<div style="font-family:{FONT};font-size:10px;font-weight:700;letter-spacing:.06em;text-transform:uppercase;color:#6F9B86;margin-top:11px;">Date</div>'
        f'<div style="font-family:{FONT};font-size:13px;font-weight:700;color:{T1};margin-top:3px;">{_esc(paiement["date"])}</div></td>'
        f'</tr></table></td></tr>'
    )


def _attachment_note(attachment):
    """Mention pièce jointe — chip « PDF » + nom de fichier. attachment={label?,fname}."""
    if not attachment:
        return ""
    label = attachment.get("label") or "Reçu officiel"
    return (
        f'<tr><td style="padding:16px 30px 0;" class="sm-px">'
        f'<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" '
        f'style="background:{SAND_50};border:1px solid {HAIR};border-radius:8px;border-collapse:separate;"><tr>'
        f'<td width="54" align="center" valign="middle" style="padding:14px 0 14px 16px;">'
        f'<table role="presentation" cellpadding="0" cellspacing="0" border="0"><tr>'
        f'<td width="38" height="22" align="center" valign="middle" style="background:#B91C1C;border-radius:4px;'
        f'font-family:{FONT};font-size:10px;font-weight:800;letter-spacing:.04em;color:#FFFFFF;">PDF</td></tr></table></td>'
        f'<td valign="middle" style="padding:14px 16px 14px 12px;">'
        f'<div style="font-family:{FONT};font-size:13px;font-weight:700;color:{T1};">{_esc(label)}</div>'
        f'<div style="font-family:{MONO};font-size:11.5px;color:{T3};margin-top:2px;">'
        f'{_esc(attachment["fname"])}<span style="font-family:{FONT};">&nbsp;— joint à cet e-mail</span></div></td>'
        f'</tr></table></td></tr>'
    )


def _instructions_block(ins):
    """Instructions de paiement (SOP) — mode-aware : virement (RIB) ou espèces (Direction).

    ins = {title, amount, ref_label?, reference, rows:[(label, valeur, mono?), …], note?} — thème violet.
    """
    if not ins:
        return ""
    V, VSOFT, VHAIR, VLINE = "#5B3FA8", "#F5F2FB", "#E0D8F5", "#EFEAFA"
    rows = "".join(
        (
            f'<tr>'
            f'<td style="padding:11px 0;border-bottom:1px solid {VLINE};font-family:{FONT};font-size:13px;'
            f'color:{T3};vertical-align:top;width:38%;">{_esc(r[0])}</td>'
            f'<td align="right" style="padding:11px 0 11px 14px;border-bottom:1px solid {VLINE};'
            f'font-family:{MONO if (len(r) > 2 and r[2]) else FONT};font-size:{"13px" if (len(r) > 2 and r[2]) else "14px"};'
            f'font-weight:700;color:{T1};">{_esc(r[1])}</td></tr>'
        )
        for r in (ins.get("rows") or [])
    )
    note = (
        f'<div style="font-family:{FONT};font-size:12px;line-height:1.55;color:{T3};margin-top:12px;">'
        f'{_esc(ins["note"])}</div>'
        if ins.get("note") else ""
    )
    return (
        f'<tr><td style="padding:6px 30px 0;" class="sm-px">'
        f'<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" '
        f'style="background:{VSOFT};border:1px solid {VHAIR};border-radius:12px;border-collapse:separate;overflow:hidden;">'
        f'<tr><td style="padding:18px 20px 0;font-family:{FONT};font-size:11px;font-weight:700;'
        f'letter-spacing:.08em;text-transform:uppercase;color:{V};">{_esc(ins["title"])}</td></tr>'
        f'<tr><td style="padding:12px 20px 4px;">'
        f'<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%"><tr>'
        f'<td class="sm-stack" valign="top" style="vertical-align:top;">'
        f'<div style="font-family:{FONT};font-size:10px;font-weight:700;letter-spacing:.06em;'
        f'text-transform:uppercase;color:#9389B5;">Montant à régler</div>'
        f'<div style="font-family:{MONO};font-size:26px;font-weight:600;letter-spacing:-.02em;color:{V};'
        f'margin-top:3px;line-height:1.1;">{_esc(ins["amount"])}'
        f'<span style="font-family:{FONT};font-size:13px;font-weight:700;color:#7A6BA8;">&nbsp;FCFA</span></div></td>'
        f'<td class="sm-stack" align="right" valign="top" style="vertical-align:top;">'
        f'<div style="font-family:{FONT};font-size:10px;font-weight:700;letter-spacing:.06em;'
        f'text-transform:uppercase;color:#9389B5;">{_esc(ins.get("ref_label") or "Référence à indiquer")}</div>'
        f'<div style="display:inline-block;margin-top:5px;background:#FFFFFF;border:1px solid {VHAIR};'
        f'border-radius:6px;padding:5px 10px;font-family:{MONO};font-size:13px;font-weight:700;color:{INK};">'
        f'{_esc(ins["reference"])}</div></td>'
        f'</tr></table></td></tr>'
        f'<tr><td style="padding:8px 20px 18px;">'
        f'<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" '
        f'style="border-top:1px solid {VHAIR};">{rows}</table>{note}</td></tr>'
        f'</table></td></tr>'
    )


def _notes_table(notes):
    if not notes:
        return ""
    rows = "".join(
        f'<tr><td style="padding:13px 0;border-bottom:1px solid #F0EDE5;font-family:{FONT};font-size:14px;color:{T2};">{_esc(k)}</td>'
        f'<td align="right" style="padding:13px 0;border-bottom:1px solid #F0EDE5;font-family:{MONO};font-size:15px;'
        f'font-weight:600;color:{T1};white-space:nowrap;">{_esc(v)}<span style="color:#857B62;font-size:12px;">/20</span></td></tr>'
        for k, v in notes
    )
    return (
        f'<tr><td style="padding:6px 30px 0;" class="sm-px">'
        f'<div style="font-family:{FONT};font-size:11px;font-weight:700;letter-spacing:.1em;text-transform:uppercase;'
        f'color:#857B62;margin-bottom:2px;">Résultats au concours</div>'
        f'<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" '
        f'style="border-top:2px solid {INK};">{rows}</table>'
        f'<div style="font-family:{FONT};font-size:12px;color:{T3};margin-top:10px;line-height:1.5;">'
        f'Notes à titre indicatif — le détail des coefficients figure dans votre espace candidat.</div></td></tr>'
    )


def _bourses_table(bourses):
    if not bourses:
        return ""
    rows = "".join(
        f'<tr><td style="padding:11px 14px;border-bottom:1px solid #F0EDE5;font-family:{FONT};font-size:14px;color:{T2};">{_esc(name)}</td>'
        f'<td align="right" style="padding:11px 14px;border-bottom:1px solid #F0EDE5;font-family:{MONO};font-size:14px;'
        f'font-weight:600;color:#047857;white-space:nowrap;">{_esc(rate)}&nbsp;%</td></tr>'
        for name, rate in bourses
    )
    return (
        f'<tr><td style="padding:18px 30px 0;" class="sm-px">'
        f'<div style="font-family:{FONT};font-size:11px;font-weight:700;letter-spacing:.1em;text-transform:uppercase;'
        f'color:#047857;margin-bottom:8px;">Bourses validées par la Direction</div>'
        f'<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" '
        f'style="background:{SAND_50};border:1px solid {HAIR};border-radius:8px;border-collapse:separate;overflow:hidden;">{rows}</table>'
        f'<div style="font-family:{FONT};font-size:12px;color:{T3};margin-top:10px;line-height:1.5;">'
        f'Taux indicatifs — le montant final de la scolarité sera calculé à l’inscription.</div></td></tr>'
    )


def _motif_block(motif, st):
    if not motif:
        return ""
    return (
        f'<tr><td style="padding:20px 30px 0;" class="sm-px">'
        f'<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" '
        f'style="background:{st["soft"]};border:1px solid {HAIR};border-radius:8px;border-collapse:separate;">'
        f'<tr><td style="padding:16px 18px;">'
        f'<div style="font-family:{FONT};font-size:11px;font-weight:700;letter-spacing:.06em;text-transform:uppercase;color:{st["band"]};">Motif</div>'
        f'<div style="font-family:{FONT};font-size:14px;line-height:1.55;color:{T2};margin-top:6px;">{_esc(motif)}</div>'
        f'</td></tr></table></td></tr>'
    )


def _cta_block(cta, cta_intro):
    if not cta:
        return ""
    return (
        f'<tr><td style="padding:26px 30px 0;" class="sm-px">'
        f'<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" '
        f'style="background:{INK};border-radius:10px;border-collapse:separate;"><tr>'
        f'<td class="sm-stack" style="padding:18px 20px;font-family:{FONT};">'
        f'<div style="font-size:11px;font-weight:700;letter-spacing:.08em;text-transform:uppercase;color:{ACCENT};">Action requise</div>'
        f'<div style="font-size:14px;color:#EDE9FE;margin-top:5px;line-height:1.45;">{_esc(cta_intro or "")}</div></td>'
        f'<td align="right" valign="middle" style="padding:18px 20px;" class="sm-stack sm-center sm-px2">'
        f'<!--[if mso]><table role="presentation" cellpadding="0" cellspacing="0" border="0"><tr><td bgcolor="#FFFFFF" style="border-radius:6px;"><![endif]-->'
        f'<a href="{_esc(cta["url"])}" style="display:inline-block;background:#FFFFFF;color:{INK};font-family:{FONT};'
        f'font-size:14px;font-weight:700;text-decoration:none;padding:11px 20px;border-radius:6px;white-space:nowrap;">{_esc(cta["label"])}&nbsp;→</a>'
        f'<!--[if mso]></td></tr></table><![endif]--></td></tr></table></td></tr>'
    )


def _secondary_link(secondary):
    if not secondary:
        return ""
    return (
        f'<tr><td style="padding:16px 30px 0;" class="sm-px">'
        f'<a href="{_esc(secondary["url"])}" style="font-family:{FONT};font-size:14px;font-weight:600;color:#5B3FA8;'
        f'text-decoration:none;border-bottom:1px solid #DDD6FE;padding-bottom:1px;">{_esc(secondary["label"])}</a></td></tr>'
    )


def _footer(dossier):
    return (
        f'<tr><td style="padding:0 30px;" class="sm-px">'
        f'<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" style="margin-top:8px;border-top:1px solid {HAIR};">'
        f'<tr><td style="padding:24px 0 0;">'
        f'<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%"><tr>'
        f'<td valign="middle">{_logo("dark")}</td>'
        f'<td align="right" valign="middle" style="font-family:{FONT};font-style:italic;font-weight:700;font-size:13px;color:#857B62;" class="sm-hide">{SCHOOL["slogan"]}</td>'
        f'</tr></table>'
        f'<div style="font-family:{FONT};font-size:12px;line-height:1.65;color:{T3};margin-top:16px;">'
        f'{SCHOOL["full"]}<br>{SCHOOL["address"]}<br>'
        f'<a href="mailto:{SCHOOL["email"]}" style="color:#5B3FA8;font-weight:600;text-decoration:none;">{SCHOOL["email"]}</a> · {SCHOOL["tel"]}</div>'
        f'<div style="font-family:{FONT};font-size:11.5px;line-height:1.6;color:#857B62;margin-top:14px;">'
        f'Vous recevez cet e-mail parce que vous avez déposé une candidature auprès de {SCHOOL["name"]} (dossier&nbsp;{_esc(dossier)}). '
        f'Message automatique relatif au traitement de votre dossier — pour toute question, répondez simplement à cet e-mail.</div>'
        f'<div style="font-family:{FONT};font-size:11.5px;line-height:1.6;color:#857B62;margin-top:8px;">'
        f'<strong style="color:{T3};">Confidentiel</strong> — destiné au seul candidat. Si vous n’êtes pas le destinataire, merci de supprimer ce message.</div>'
        f'<div style="margin-top:16px;font-family:{FONT};font-size:11.5px;">'
        f'<a href="{_legal_link("politique-de-confidentialite")}" style="color:#5B3FA8;font-weight:600;text-decoration:none;">Politique de confidentialité</a>&nbsp;&nbsp;·&nbsp;&nbsp;'
        f'<a href="{_legal_link("mentions-legales")}" style="color:#5B3FA8;font-weight:600;text-decoration:none;">Mentions légales</a>&nbsp;&nbsp;·&nbsp;&nbsp;'
        f'<a href="{_legal_link("donnees-personnelles")}" style="color:#5B3FA8;font-weight:600;text-decoration:none;">Mes données personnelles</a></div>'
        f'<div style="font-family:{FONT};font-size:11px;color:#C9C2B2;margin:14px 0 26px;">© 2026 {SCHOOL["full"]}. Tous droits réservés.</div>'
        f'</td></tr></table></td></tr>'
    )


# ── Render principal ───────────────────────────────────────────────────────────
def render_candidate_email(
    nom, dossier, filiere, status, intro,
    notes=None, bourses=None, motif=None,
    cta=None, cta_intro=None, secondary=None,
    signoff=None, preheader="", subject="",
    meta=None, paiement=None, attachment=None, otp=None, instructions=None,
):
    """Construit le HTML email-safe complet d'un mail candidat (fonction pure).

    status ∈ {'admis','refuse','complement','attente','paiement','compte','otp','sop','etu','inscrit'}.
    notes : list[(label, note)] ; bourses : list[(nom, taux)] ; cta/secondary : {'label','url'}.
    meta : list[(label, valeur, mono?)] override des cellules d'en-tête (1 à 3).
    paiement : {'montant','recu','date'} ; attachment : {'label'?,'fname'} ; otp : {'code','minutes'?}.
    """
    st = STATUS.get(status, STATUS["admis"])
    mark = ""
    if st["mark"]:
        mark = (
            f'<table role="presentation" cellpadding="0" cellspacing="0" border="0" '
            f'style="display:inline-block;margin-right:12px;vertical-align:middle;"><tr>'
            f'<td width="34" height="34" align="center" valign="middle" style="background:rgba(255,255,255,.18);'
            f'border-radius:999px;font-family:{FONT};font-size:17px;font-weight:700;color:#FFFFFF;">{st["mark"]}</td></tr></table>'
        )
    filiere_band = (
        f'<span style="font-family:{FONT};font-size:16px;font-weight:600;color:rgba(255,255,255,.86);">&nbsp;&nbsp;{_esc(filiere)}</span>'
        if filiere and status in ("admis", "accepte", "admissible", "conditionnelle") else ""
    )

    return f"""<!doctype html>
<html lang="fr" xmlns:v="urn:schemas-microsoft-com:vml" xmlns:o="urn:schemas-microsoft-com:office:office">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="x-apple-disable-message-reformatting">
<meta name="color-scheme" content="light dark">
<meta name="supported-color-schemes" content="light dark">
<title>{_esc(subject or SCHOOL["name"])}</title>
<!--[if mso]><style>*{{font-family:Arial,sans-serif!important}}</style><![endif]-->
<style>
  @media only screen and (max-width:600px){{
    .container{{width:100%!important}}
    .sm-px{{padding-left:20px!important;padding-right:20px!important}}
    .sm-px2{{padding-left:20px!important;padding-right:20px!important}}
    .sm-stack{{display:block!important;width:100%!important;border-right:0!important;border-bottom:1px solid {HAIR}!important}}
    .sm-center{{text-align:left!important}}
    .sm-hide{{display:none!important}}
    .status-value{{font-size:30px!important}}
  }}
  @media (prefers-color-scheme:dark){{
    .bg{{background:#110628!important}} .card{{background:#1A1140!important}}
    .t1{{color:#FFFFFF!important}} .t2{{color:#D8D2EC!important}}
    .meta-cell{{background:#1A1140!important;border-color:#3A2F63!important}}
    .hair{{border-color:#3A2F63!important}} .foot{{background:#110628!important}}
  }}
  a{{text-decoration:none}}
</style>
</head>
<body class="bg" style="margin:0;padding:0;background:{CANVAS};">
<div style="display:none;max-height:0;overflow:hidden;mso-hide:all;font-size:1px;line-height:1px;color:{CANVAS};">{_esc(preheader)}&#8203;&#8203;&#8203;&#8203;&#8203;&#8203;&#8203;&#8203;</div>
<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" class="bg" style="background:{CANVAS};">
  <tr><td align="center" style="padding:24px 12px;">
    <!--[if mso]><table role="presentation" cellpadding="0" cellspacing="0" border="0" width="600"><tr><td><![endif]-->
    <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="600" class="container" style="width:600px;max-width:600px;">
      <tr><td style="background:{INK};padding:22px 30px;border-radius:14px 14px 0 0;" class="sm-px">
        <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%"><tr>
          <td valign="middle">{_logo("light")}</td>
          <td align="right" valign="middle" style="font-family:{FONT};font-size:11px;font-weight:600;letter-spacing:.08em;text-transform:uppercase;color:rgba(255,255,255,.66);" class="sm-hide">Service des admissions</td>
        </tr></table></td></tr>
      <tr><td style="background:{st['band']};padding:26px 30px;" class="sm-px">
        <div style="font-family:{FONT};font-size:11px;font-weight:700;letter-spacing:.12em;text-transform:uppercase;color:rgba(255,255,255,.72);">{st['eyebrow']}</div>
        <div style="margin-top:8px;">{mark}<span class="status-value" style="font-family:{FONT};font-size:38px;font-weight:800;letter-spacing:-.03em;color:#FFFFFF;vertical-align:middle;">{_esc(st['label'])}</span>{filiere_band}</div>
      </td></tr>
      <tr><td class="card sm-px" style="background:{PAPER};padding:28px 30px 4px;">
        <div class="t1" style="font-family:{FONT};font-size:15px;font-weight:600;color:{T1};">Bonjour {_esc(nom)},</div>
        <div class="t2" style="font-family:{FONT};font-size:15px;line-height:1.6;color:{T2};margin-top:8px;">{_esc(intro)}</div>
      </td></tr>
      <tr><td class="card" style="background:{PAPER};">
        <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%">
          {_meta_grid(nom, dossier, filiere, meta)}
          {_payment_summary(paiement)}
          {_instructions_block(instructions)}
          {_otp_code(otp)}
          {_notes_table(notes)}
          {_bourses_table(bourses)}
          {_motif_block(motif, st)}
          {_attachment_note(attachment)}
          {_cta_block(cta, cta_intro)}
          {_secondary_link(secondary)}
          <tr><td style="padding:24px 30px 26px;" class="sm-px">
            <div class="t2" style="font-family:{FONT};font-size:13px;color:{T2};">{_esc(signoff or (SCHOOL['name'] + ' · Service des admissions'))}</div>
          </td></tr>
        </table></td></tr>
      <tr><td class="foot" style="background:{SAND_50};border-radius:0 0 14px 14px;">
        <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%">{_footer(dossier)}</table>
      </td></tr>
    </table>
    <!--[if mso]></td></tr></table><![endif]-->
  </td></tr>
</table>
</body>
</html>"""


