"""Notifications email au candidat — LOT M (design email-handoff, template unique).

Tous les corps passent par email_template.render_candidate_email (statuts/couleurs
sémantiques — voir specifications/email-handoff/README.md). Pattern NON-BLOQUANT
(comme receipt.py) : un échec d'envoi ne casse JAMAIS le flux métier. Entrées
utilisateur échappées par le template (anti-injection). Jamais de code OTP en
clair dans les logs ni le preheader (sécurité).

Liens (A0.2) : le lien TOKENISÉ n'apparaît que dans les mails émis au moment où le
token est détenu en clair (création, recovery — rotation) ; partout ailleurs, lien
de suivi générique. L'OTP reste exigé à l'arrivée pour les actions (double barrière).
"""

import json

import frappe
from frappe.utils import escape_html

from admission.api._log import log_event
from admission.api.email_template import (
    SCHOOL,
    _portal_link,
    get_bank,
    render_candidate_email,
)
from admission.api.receipt import ECOLE


# ── Socle d'envoi (NON-BLOQUANT, commun à tous les mails candidat) ─────────────


def _send_candidate_mail(applicant, subject, message, event, attachments=None, now=False):
    """Socle COMMUN d'envoi d'un mail candidat (non-bloquant ; skip si pas d'email).

    now=True → envoi SYNCHRONE (pendant la requête) pour les mails sensibles au délai (OTP) :
    court-circuite la file d'attente (flushée par lots → latence). Les autres mails restent
    en file (now=False) pour ne pas alourdir chaque requête."""
    try:
        email = getattr(applicant, "email", None)
        if not email:
            log_event(event, "skipped_no_email", dossier_id=getattr(applicant, "name", None))
            return
        kwargs = {"recipients": [email], "subject": subject, "message": message}
        if attachments:
            kwargs["attachments"] = attachments
        if now:
            kwargs["now"] = True
        frappe.sendmail(**kwargs)
        log_event(event, "sent", dossier_id=getattr(applicant, "name", None))
    except Exception:
        log_event(event, "failed", dossier_id=getattr(applicant, "name", None), level="warning")
        frappe.logger("notifications").warning(f"{event} failed (non-blocking): {frappe.get_traceback()}")


def _full_name(applicant):
    return (getattr(applicant, "applicant_name", None)
            or " ".join(filter(None, [getattr(applicant, "first_name", ""),
                                      getattr(applicant, "last_name", "")]))).strip()


def _programme(applicant):
    return getattr(applicant, "programme_label", None) or getattr(applicant, "level_code", "") or ""


def _fmt_montant(amount):
    """Format FR d'un montant XOF fourni par le back (AFFICHAGE seul, aucun calcul)."""
    try:
        return f"{int(float(amount or 0)):,}".replace(",", " ")
    except (ValueError, TypeError):
        return str(amount or "")


def _format_rate_percent(rate):
    """0.25 → '25', 0.125 → '12,5' — TAUX en format FR, jamais de montant (DEC-206)."""
    try:
        return f"{float(rate) * 100:g}".replace(".", ",")
    except (ValueError, TypeError):
        return "0"


def _parse_notes(raw):
    """Parse notes_concours (JSON string ou dict) → dict. Non-bloquant ({} si invalide)."""
    if not raw:
        return {}
    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return {}


# ── Phase décision — mapping label → statut/couleur/corps (handoff §5) ─────────
# ⚠️ ADM (admissible, Responsable) ≠ ACC (admission acceptée, Direction) : deux mails,
# deux couleurs. Les labels sont CEUX réellement passés par staff.py (signatures intactes).

_DECISION_VIEW = {  # label (lower) → (status, intro, subject, preheader, with_cta)
    "admissible": ("admissible",
        "Bonne nouvelle : au terme de l'étude de votre dossier, votre candidature est retenue "
        "comme admissible. La Direction procédera à la confirmation finale de votre admission — "
        "vous en serez informé(e) très prochainement.",
        "Votre candidature LaNEM — décision (Admissible)",
        "Votre dossier est retenu comme admissible. La Direction confirmera très prochainement.", False),
    "admis": ("admissible",  # libellé Prépa (mark_admissible) — mail admissible AVEC notes
        "Votre concours est validé : vous êtes admissible. Voici le détail de vos résultats. "
        "La Direction confirmera votre admission très prochainement.",
        "Votre candidature LaNEM — décision (Admissible)",
        "Concours validé : vous êtes admissible. Voici vos résultats.", False),
    "liste d'attente": ("attente",
        "Après l'étude de votre dossier, votre candidature est placée sur liste d'attente. Si une "
        "place se libère, nous vous recontacterons par ordre de rang — sans démarche de votre part.",
        "Votre candidature LaNEM — décision (Liste d'attente)",
        "Votre candidature est placée sur liste d'attente.", False),
    "admission conditionnelle": ("conditionnelle",
        "Votre admission est prononcée « sous réserve » : elle est conditionnée à la présentation "
        "de votre diplôme du baccalauréat. Dès sa vérification, votre admission sera confirmée.",
        "Votre candidature LaNEM — décision (Admis sous réserve)",
        "Vous êtes admis(e) sous réserve de présentation de votre diplôme du baccalauréat.", True),
    "admission acceptée": ("accepte",
        "Nous avons le plaisir de vous confirmer votre admission définitive à LaNEM, prononcée par "
        "la Direction. Voici la marche à suivre pour réserver votre place.",
        "Félicitations — votre admission à LaNEM est confirmée",
        "Votre admission est confirmée. Confirmez votre place avant la date limite.", True),
    "refusé": ("refuse",
        "Après une étude attentive de votre dossier, le jury n'a pas pu retenir votre candidature "
        "pour cette session.",
        "Votre candidature LaNEM — décision (Candidature non retenue)",
        "Décision relative à votre candidature LaNEM.", False),
}


def _decision_kwargs(applicant, decision_label, motif=None, bourses=None, notes=None):
    """Construit les kwargs render pour une décision (factorisation générique/Prépa)."""
    nom = _full_name(applicant)
    status, intro, subject, preheader, with_cta = _DECISION_VIEW.get(
        (decision_label or "").strip().lower(), _DECISION_VIEW["admissible"])
    filiere = _programme(applicant)
    kw = dict(nom=nom, dossier=getattr(applicant, "name", ""), filiere=filiere, status=status,
              intro=intro, subject=subject, preheader=preheader,
              meta=[("Candidat", nom), ("Dossier", getattr(applicant, "name", ""), True),
                    ("Programme", filiere)])
    if notes:
        kw["notes"] = notes
    if motif:
        kw["motif"] = motif
    if bourses:  # ACC : bourses validées (taux %, ruling R4 — jamais de montants)
        kw["bourses"] = [(b["scholarship_name"], _format_rate_percent(b["rate"])) for b in bourses]
    if with_cta:
        if status == "accepte":
            kw["cta"] = {"label": "Confirmer ma place", "url": _portal_link(applicant)}
            kw["cta_intro"] = "Confirmez votre inscription avant la date limite."
        else:  # conditionnelle
            kw["cta"] = {"label": "Téléverser mon diplôme", "url": _portal_link(applicant)}
            kw["cta_intro"] = "Déposez votre diplôme du bac dès que vous l'avez pour lever la condition."
    else:
        kw["secondary"] = {"label": "Ouvrir mon espace candidat", "url": _portal_link(applicant)}
    return kw, subject


def send_decision_notification(applicant, decision_label, motif=None, bourses=None):
    """Notifie le candidat (générique/Licence, SANS notes) de la décision. NON-BLOQUANT.

    Signature INCHANGÉE (appelants staff.py intacts). `bourses` : liste
    [{scholarship_name, rate}] des bourses validées (C2/R4, taux indicatifs)."""
    kw, subject = _decision_kwargs(applicant, decision_label, motif=motif, bourses=bourses)
    _send_candidate_mail(applicant, subject, render_candidate_email(**kw), "decision_notification")


def send_prepa_decision_notification(applicant, decision_label):
    """Notifie le candidat Prépa de la décision AVEC ses notes de concours (DEC-197).
    NON-BLOQUANT. Signature INCHANGÉE."""
    notes = [(str(k), str(v)) for k, v in _parse_notes(getattr(applicant, "notes_concours", None)).items()]
    kw, subject = _decision_kwargs(applicant, decision_label, notes=notes or None)
    _send_candidate_mail(applicant, subject, render_candidate_email(**kw), "prepa_decision_notification")


# ── Complément requis (INC) — désormais sur le socle commun ────────────────────


def send_incompletude_notification(applicant, motif):
    """Notifie le candidat de l'incomplétude (entrée INC). NON-BLOQUANT.
    Signature INCHANGÉE. Statut `complement` (ambre) + CTA de reprise."""
    nom = _full_name(applicant)
    html = render_candidate_email(
        nom=nom, dossier=getattr(applicant, "name", ""), filiere="", status="complement",
        intro="Votre dossier est presque complet : il manque un élément pour que nous puissions "
              "poursuivre son instruction. Le détail figure ci-dessous.",
        meta=[("Candidat", nom), ("Dossier", getattr(applicant, "name", ""), True),
              ("Programme", _programme(applicant))],
        motif=motif or "",
        cta={"label": "Reprendre ma candidature", "url": _portal_link(applicant)},
        cta_intro="Corrigez les éléments demandés depuis votre espace, puis re-soumettez votre dossier.",
        preheader="Une pièce manque pour poursuivre l'instruction de votre dossier.",
        subject="Votre candidature LaNEM — complément requis",
        signoff="Merci de votre réactivité. — Le Service des admissions, LaNEM",
    )
    _send_candidate_mail(applicant, "Votre candidature LaNEM — complément requis", html,
                         "incompletude_notification")


# ── Compte créé (BRO) — lien de reprise tokenisé (A0.2) ────────────────────────


def send_account_created(applicant, token):
    """Bienvenue + n° de dossier + LIEN DE REPRISE tokenisé (A0.2 : seul moment où le
    token est détenu en clair avec la rotation/recovery). NON-BLOQUANT."""
    nom = _full_name(applicant)
    html = render_candidate_email(
        nom=nom, dossier=applicant.name, filiere="", status="compte",
        intro="Bienvenue ! Votre espace candidat LaNEM est ouvert. Conservez précieusement cet "
              "e-mail : le bouton ci-dessous vous permet de reprendre votre candidature à tout "
              "moment, depuis n'importe quel appareil.",
        meta=[("Candidat", nom), ("Dossier", applicant.name, True),
              ("Programme", _programme(applicant))],
        cta={"label": "Accéder à mon espace", "url": _portal_link(applicant, token=token)},
        cta_intro="Un code de vérification vous sera demandé à la connexion (sécurité).",
        signoff="Toute l'équipe vous souhaite la bienvenue. — Le Service des admissions, LaNEM",
        preheader=f"Votre espace candidat LaNEM est ouvert · dossier {applicant.name}. Conservez cet e-mail.",
        subject="Bienvenue chez LaNEM — votre espace candidat est ouvert",
    )
    _send_candidate_mail(applicant, "Bienvenue chez LaNEM — votre espace candidat est ouvert",
                         html, "account_created")


def send_recovery_link(applicant, token):
    """M7 — renvoi du lien de reprise (recovery par email, token TOURNÉ). NON-BLOQUANT."""
    nom = _full_name(applicant)
    html = render_candidate_email(
        nom=nom, dossier=applicant.name, filiere="", status="compte",
        intro="Vous avez demandé à retrouver l'accès à votre candidature. Voici votre nouveau "
              "lien de reprise — l'ancien lien est désactivé par sécurité.",
        meta=[("Candidat", nom), ("Dossier", applicant.name, True)],
        cta={"label": "Reprendre ma candidature", "url": _portal_link(applicant, token=token)},
        cta_intro="Un code de vérification vous sera demandé à la connexion (sécurité).",
        signoff="Si vous n'êtes pas à l'origine de cette demande, ignorez cet e-mail — votre "
                "dossier reste protégé par le code de vérification.",
        preheader="Votre nouveau lien de reprise LaNEM. L'ancien lien est désactivé.",
        subject="Reprendre votre candidature LaNEM",
    )
    _send_candidate_mail(applicant, "Reprendre votre candidature LaNEM", html, "recovery_link")


# ── Code OTP e-mail (M3) ───────────────────────────────────────────────────────


def send_email_otp(applicant, email_otp, minutes=10, token=None):
    """Livre le code OTP e-mail. NON-BLOQUANT. SÉCURITÉ : jamais de code dans les
    logs ni le sujet/preheader. `token` (clair au moment du request_otp) → ajoute un
    lien de reprise « un tap » avec OTP pré-saisi (corps uniquement, pas le sujet) ;
    le front auto-vérifie en POST et purge l'URL. Le SMS (phone_otp) = OPS (A0.1)."""
    nom = _full_name(applicant)
    kwargs = dict(
        nom=nom, dossier=applicant.name, filiere="", status="otp",
        intro="Pour sécuriser votre espace candidat, saisissez le code ci-dessous dans la page "
              "de vérification. Il confirme que cette adresse e-mail vous appartient.",
        meta=[("Candidat", nom), ("Dossier", applicant.name, True)],
        otp={"code": email_otp, "minutes": minutes},
        signoff="Service des admissions, LaNEM",
        preheader="Votre code de vérification LaNEM — valable 10 minutes. Ne le partagez jamais.",
        subject="Votre code de vérification LaNEM",
    )
    if token:
        kwargs["cta"] = {
            "label": "Reprendre ma candidature",
            "url": _portal_link(applicant, token=token, otp=email_otp),
        }
        kwargs["cta_intro"] = ("Sur mobile, ce bouton vous ramène directement dans votre candidature, "
                               "code déjà saisi. Sinon, recopiez le code ci-dessus.")
    html = render_candidate_email(**kwargs)
    _send_candidate_mail(applicant, "Votre code de vérification LaNEM", html, "email_otp", now=True)


# ── SOP — instructions de paiement (M5, mode-aware : virement RIB / espèces) ───


def _rib_attachment():
    """PJ RIB officiel — LOT RIB-SETTINGS : lue depuis Admission Settings (rib_pdf), nom
    VERSIONNÉ (corps et PJ de la même génération ; l'ancien fichier est détruit à la
    rotation — aucun exemplaire périmé ne peut partir). Non-bloquant : None si absent."""
    try:
        bank = get_bank()
        if not bank or not bank.get("pdf_url"):
            return None
        file_name = frappe.db.get_value("File", {"file_url": bank["pdf_url"]}, "name")
        if not file_name:
            return None
        content = frappe.get_doc("File", file_name).get_content()
        if isinstance(content, str):
            content = content.encode()
        return {"fname": f"RIB-LaNEM-v{bank['version']}.pdf", "fcontent": content}
    except Exception:
        frappe.logger("notifications").warning("RIB PDF illisible — mail SOP envoyé sans PJ.")
        return None


def send_offline_submission(applicant, fee, mode, reminder=False, fee_label="frais de dossier"):
    """Mail instructions SOP (M5) — la soumission devient définitive à la confirmation
    du paiement. mode ∈ {cash, bank}. `reminder=True` : relance J+7 (M9, même corps,
    intro adaptée). `fee_label` : « frais de dossier » (frais 1) ou « frais
    d'inscription » (frais 2 offline). NON-BLOQUANT. Montants = fee.amount_xof
    (AFFICHAGE seul, aucun calcul)."""
    nom = _full_name(applicant)
    montant = _fmt_montant(getattr(fee, "amount_xof", 0))
    prefix = "Rappel — " if reminder else ""
    is_frais1 = fee_label == "frais de dossier"
    subject = (f"{prefix}Votre soumission est enregistrée — instructions de paiement" if is_frais1
               else f"{prefix}Règlement des {fee_label} — instructions de paiement")
    common = dict(
        nom=nom, dossier=applicant.name, filiere="", status="sop",
        meta=[("Candidat", nom), ("Dossier", applicant.name, True),
              ("Programme", _programme(applicant))],
        signoff=("Votre soumission devient définitive à la confirmation du paiement. "
                 "— Service des admissions, LaNEM" if is_frais1 else
                 "Votre inscription sera finalisée à la confirmation du paiement. "
                 "— Service des admissions, LaNEM"),
        subject=subject,
    )
    rappel = ("Sauf erreur de notre part, nous n'avons pas encore reçu votre règlement. "
              if reminder else "")
    contexte = ("Votre candidature est enregistrée à titre provisoire. Pour la rendre définitive, "
                if is_frais1 else "Pour finaliser votre inscription, ")
    attachments = None
    if str(mode).strip().lower() == "bank":
        bank = get_bank()
        if not bank:
            # LOT RIB-SETTINGS (R0.1b) : pas de compte configuré → AUCUNE coordonnée
            # périmée n'est envoyée ; le candidat est orienté vers le contact humain.
            log_event("offline_submission", "rib_missing", dossier_id=applicant.name, level="warning")
            html = render_candidate_email(**common,
                intro=f"{rappel}{contexte}réglez les {fee_label} par virement bancaire. "
                      "Les coordonnées bancaires vous seront communiquées par le service des "
                      "admissions — répondez à cet e-mail ou appelez-nous.",
                cta={"label": "Suivre mon dossier", "url": _portal_link(applicant)},
                preheader=f"Réglez {montant} FCFA par virement — coordonnées communiquées par le service.")
            _send_candidate_mail(applicant, subject, html,
                                 "sop_reminder" if reminder else "offline_submission")
            return
        attachment = _rib_attachment()
        attachments = [attachment] if attachment else None
        html = render_candidate_email(**common,
            intro=f"{rappel}{contexte}réglez les {fee_label} par virement bancaire à l'aide des "
                  "coordonnées ci-dessous. Dès réception, nous vous enverrons votre reçu officiel.",
            instructions={"title": "Coordonnées bancaires — virement", "amount": montant,
                "ref_label": "Référence du virement", "reference": applicant.name,
                "rows": [("Titulaire", bank["titulaire"]), ("Banque", bank["banque"]),
                         ("IBAN", bank["iban"], True), ("BIC / SWIFT", bank["bic"], True)],
                "note": "Indiquez impérativement votre numéro de dossier en référence du virement, "
                        "sans quoi le rapprochement sera retardé. Le RIB officiel est joint en PDF."},
            attachment={"label": "RIB officiel — LaNEM",
                        "fname": attachments[0]["fname"]} if attachments else None,
            cta={"label": "Suivre mon dossier", "url": _portal_link(applicant)},
            cta_intro="Suivez l'état de votre paiement et de votre candidature depuis votre espace.",
            preheader=(f"Soumission provisoire enregistrée · réglez {montant} FCFA par virement pour la finaliser."
                       if is_frais1 else f"Réglez {montant} FCFA par virement pour finaliser votre inscription."))
    else:  # cash
        html = render_candidate_email(**common,
            intro=f"{rappel}{contexte}présentez-vous à la Direction de l'école pour régler les "
                  f"{fee_label} en espèces. Un reçu officiel vous sera remis et envoyé par e-mail.",
            instructions={"title": "Paiement en espèces — à la Direction", "amount": montant,
                "ref_label": "À présenter au guichet", "reference": applicant.name,
                "rows": [("Lieu", "Direction — LaNEM"), ("Adresse", ECOLE["address"]),
                         ("Horaires", "Lun – Ven · 8h00 – 17h00"), ("Contact", ECOLE["phone"])],
                "note": "Munissez-vous de votre numéro de dossier ; il vous sera demandé au "
                        "guichet pour établir votre reçu."},
            cta={"label": "Suivre mon dossier", "url": _portal_link(applicant)},
            cta_intro="Suivez l'état de votre paiement et de votre candidature depuis votre espace.",
            preheader=(f"Soumission provisoire enregistrée · réglez {montant} FCFA en espèces à la Direction."
                       if is_frais1 else f"Réglez {montant} FCFA en espèces à la Direction pour finaliser votre inscription."))
    _send_candidate_mail(applicant, subject, html,
                         "sop_reminder" if reminder else "offline_submission",
                         attachments=attachments)


# ── INS — inscription confirmée (M4) ──────────────────────────────────────────


def send_enrolled(applicant, student_id=None):
    """Félicitations inscription (ACC→INS). NON-BLOQUANT. `student_id` optionnel
    (créé en asynchrone côté campus — souvent inconnu au moment de l'envoi)."""
    nom = _full_name(applicant)
    prog = _programme(applicant)
    meta = [("Étudiant", nom)]
    if student_id:
        meta.append(("N° étudiant", student_id, True))
    meta.append(("Programme", prog))
    campus_url = (frappe.conf.get("campus_student_portal_url") or "https://campus.lanem.bj").rstrip("/")
    html = render_candidate_email(
        nom=nom, dossier=applicant.name, filiere="", status="inscrit", meta=meta,
        intro="Félicitations ! Votre inscription est finalisée et votre place est confirmée. "
              "Bienvenue parmi les étudiants de LaNEM. Vos accès à l'espace étudiant vous "
              "parviendront par e-mail.",
        cta={"label": "Accéder à l'espace étudiant", "url": campus_url},
        cta_intro="Retrouvez votre emploi du temps, vos documents et les informations de rentrée.",
        signoff="Toute l'équipe vous souhaite une excellente rentrée. — Le Service des admissions, LaNEM",
        preheader="Félicitations : votre inscription est finalisée. Bienvenue parmi les étudiants de LaNEM !",
        subject="Votre inscription à LaNEM est confirmée",
    )
    _send_candidate_mail(applicant, "Votre inscription à LaNEM est confirmée", html, "enrolled")


# ── ETU — dossier en étude (OPTIONNEL, implémenté NON CÂBLÉ — handoff §6) ──────


def send_under_review(applicant):
    """Mail de réassurance SOU→ETU. NON CÂBLÉ par défaut (éviter de saturer la boîte —
    handoff §6) : pour l'activer, l'appeler depuis staff.start_review."""
    nom = _full_name(applicant)
    html = render_candidate_email(
        nom=nom, dossier=applicant.name, filiere="", status="etu",
        intro="Votre dossier est complet et vient d'entrer en phase d'étude par notre "
              "commission d'admission. Aucune action n'est requise de votre part à ce stade.",
        meta=[("Candidat", nom), ("Dossier", applicant.name, True),
              ("Programme", _programme(applicant))],
        secondary={"label": "Suivre l'avancement de mon dossier", "url": _portal_link(applicant)},
        signoff="Merci de votre patience. — Le Service des admissions, LaNEM",
        preheader="Votre dossier est complet et entre en phase d'étude par notre commission.",
        subject="Votre dossier est en cours d'étude",
    )
    _send_candidate_mail(applicant, "Votre dossier est en cours d'étude", html, "under_review")


# ── Préavis d'anonymisation (M9 — brouillons BRO inactifs, RGPD transparence) ──


def send_purge_notice(applicant, days_left=7):
    """Préavis avant anonymisation d'un brouillon inactif (retention.py). NON-BLOQUANT."""
    nom = _full_name(applicant)
    html = render_candidate_email(
        nom=nom, dossier=applicant.name, filiere="", status="complement",
        intro=f"Votre brouillon de candidature est inactif depuis plusieurs semaines. Sans action "
              f"de votre part sous {days_left} jours, il sera supprimé et vos données personnelles "
              f"anonymisées, conformément à notre politique de conservation.",
        meta=[("Candidat", nom), ("Dossier", applicant.name, True)],
        cta={"label": "Reprendre ma candidature", "url": _portal_link(applicant)},
        cta_intro="Reprenez votre candidature pour conserver votre dossier.",
        signoff="Service des admissions, LaNEM",
        preheader=f"Votre brouillon sera supprimé dans {days_left} jours sans action de votre part.",
        subject="Votre brouillon de candidature expire bientôt",
    )
    _send_candidate_mail(applicant, "Votre brouillon de candidature expire bientôt", html, "purge_notice")


# ── DES — désistement / clôture (LOT W1/W4) ───────────────────────────────────


def send_withdrawal_notification(applicant, motif=None):
    """Notifie la clôture du dossier (désistement DES) — geste staff W1 ou clôture de
    session W4. Ton NEUTRE (pas un refus). NON-BLOQUANT."""
    nom = _full_name(applicant)
    html = render_candidate_email(
        nom=nom, dossier=applicant.name, filiere="", status="etu",
        intro="Votre dossier de candidature a été clôturé (désistement). "
              "Si cette clôture ne correspond pas à votre demande, contactez le service "
              "des admissions en répondant simplement à cet e-mail.",
        meta=[("Candidat", nom), ("Dossier", applicant.name, True),
              ("Programme", _programme(applicant))],
        motif=motif or None,
        signoff="Nous restons à votre disposition. — Le Service des admissions, LaNEM",
        preheader="Votre dossier de candidature a été clôturé (désistement).",
        subject="Votre candidature LaNEM — clôture du dossier",
    )
    _send_candidate_mail(applicant, "Votre candidature LaNEM — clôture du dossier", html, "withdrawal")


# ── Scheduler : relance SOP J+7 (M9, hooks.py daily) ───────────────────────────


SOP_REMINDER_AFTER_DAYS = 7


def remind_dormant_sop_dossiers():
    """Relance UNIQUE des dossiers SOP dormants (déclaré offline, jamais payé) après J+7.

    Anti-double-envoi : flag `sop_reminder_sent_at` (posé via db.set_value
    update_modified=False — ne PAS rafraîchir `modified`, les fenêtres de
    rétention s'appuient dessus). Le mode/montant sont relus depuis le paiement
    offline Pending d'origine ; un dossier sans paiement Pending est ignoré
    (déjà confirmé ou rejeté entre-temps). Non-bloquant par dossier.
    """
    from frappe.utils import add_days, now_datetime

    cutoff = add_days(now_datetime(), -SOP_REMINDER_AFTER_DAYS)
    names = frappe.get_all(
        "Admission Applicant",
        filters={
            "status": "SOP",
            "modified": ["<", cutoff],
            "anonymized": ["!=", 1],
            "sop_reminder_sent_at": ["is", "not set"],
        },
        pluck="name",
    )
    sent = 0
    for name in names:
        try:
            applicant = frappe.get_doc("Admission Applicant", name)
            pending = frappe.get_all(
                "Applicant Fee Payment",
                filters={"applicant": name, "payment_status": "Pending",
                         "payment_mode": ["in", ["Cash", "Bank"]]},
                fields=["payment_mode", "applicant_fee"],
                order_by="creation desc", limit=1,
            )
            if not pending or not pending[0].applicant_fee:
                continue
            fee = frappe.get_doc("Applicant Fee", pending[0].applicant_fee)
            send_offline_submission(applicant, fee, pending[0].payment_mode.lower(), reminder=True)
            frappe.db.set_value("Admission Applicant", name,
                                "sop_reminder_sent_at", now_datetime(), update_modified=False)
            sent += 1
        except Exception:
            frappe.logger("notifications").warning(
                f"SOP reminder failed for {name} (non-blocking): {frappe.get_traceback()}")
    frappe.db.commit()
    frappe.logger("notifications").info(f"SOP reminders sent: {sent}/{len(names)}")
    return {"sop_reminders_sent": sent}
