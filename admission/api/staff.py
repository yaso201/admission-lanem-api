"""Endpoints back-office staff (PAY-CONFIRM-AGENT).

Auth = session Frappe authentifiée + rôle (PAS de token candidat ; ce sont des endpoints staff,
donc `@frappe.whitelist()` sans allow_guest → session requise). Réutilise la cascade et les
helpers de paiement existants (pas de duplication de la notif UF : portée par le hook on_payment_update).
"""

import json

import frappe
from frappe.utils import now_datetime, add_days

from admission.api._log import log_event
from admission.api.public import (
    _apply_exclusivity_local,
    _ensure_enrollment_fee,
    _ensure_fee,
    _error,
    _ok,
    _assert_fee_unpaid,
    apply_confirmed_payment_cascade,
    PAYMENT_FORBIDDEN_STATES,
    prepare_enrollment_online_payment,
    prepare_online_payment,
    PIECES_FOURNIE_STATUSES,
    requise_effective,
    pieces_requises_non_verifiees,
    notify_pieces_blocked,
    pieces_recap,
    _record_piece_verdict,
    _generate_token,
    _hash,
    TOKEN_TTL_DAYS,
)
from admission.api.notifications import (
    send_decision_notification,
    send_enrolled,
    send_incompletude_notification,
    send_pieces_recap_notification,
    send_prepa_decision_notification,
    send_withdrawal_notification,
)
from admission.api.receipt import send_payment_receipt

CONFIRM_ROLES = ("Admission Administratif", "System Manager")
OFFLINE_MODES = {"cash": "Cash", "bank": "Bank"}


def _resolve_pending_payment(dossier_id, payment_id=None):
    """Retourne l'Applicant Fee Payment à confirmer pour ce dossier, ou None si aucun en attente."""
    if payment_id:
        payment = frappe.get_doc("Applicant Fee Payment", payment_id)
        if payment.applicant != dossier_id:
            frappe.throw("Le paiement indiqué n'appartient pas à ce dossier.")
        return payment
    names = frappe.get_all(
        "Applicant Fee Payment",
        # Confirmation offline = canaux espèce/banque seulement ; on ignore les Pending Online
        # (initiation online candidat/agent), confirmés par le webhook → pas de confusion avec un orphelin.
        filters={"applicant": dossier_id, "payment_status": "Pending", "payment_mode": ["in", ["Cash", "Bank"]]},
        pluck="name",
    )
    if not names:
        return None
    if len(names) > 1:
        frappe.throw("Plusieurs paiements en attente pour ce dossier — préciser payment_id.")
    return frappe.get_doc("Applicant Fee Payment", names[0])


@frappe.whitelist()
def confirm_offline_payment(dossier_id=None, payment_mode=None, justificatif=None, payment_id=None):
    """Confirme un paiement offline (espèce/banque) — scénarios 1 & 2 (corrige le 🔴 dead-end C1).

    Rôle Administratif. Idempotent (rejeu sur un paiement déjà confirmé = no-op). Justificatif
    obligatoire (garde validate, espèce/banque). Au Confirmed : cascade fee Paid + SOP→SOU ; le
    hook on_payment_update notifie UF. Aucun appel direct à notify_uf/promo/consent.
    """
    frappe.only_for(CONFIRM_ROLES)
    if not dossier_id or not frappe.db.exists("Admission Applicant", dossier_id):
        return _error("INVALID_DOSSIER", "Dossier inconnu.", 404)
    # AUDIT-RECETTE (F2) + D-CONF-01 : pas d'encaissement sur dossier TERMINAL — un Pending qui traîne
    # sur un dossier désisté/refusé/rejeté/inscrit ne doit pas devenir de l'argent confirmé. Aligné sur
    # la constante partagée PAYMENT_FORBIDDEN_STATES (symétrie avec la garde webhook).
    current_status = frappe.db.get_value("Admission Applicant", dossier_id, "status")
    if current_status in PAYMENT_FORBIDDEN_STATES:
        return _error("INVALID_STATE",
                      f"Confirmation impossible : dossier clos ({current_status}).", 409)

    payment = _resolve_pending_payment(dossier_id, payment_id)
    if not payment:
        return _error("NO_PENDING_PAYMENT", "Aucun paiement en attente à confirmer.", 404)
    if payment.payment_status != "Pending":
        return _ok({"idempotent": True, "payment_id": payment.name, "status": payment.payment_status})

    if payment_mode:
        norm = OFFLINE_MODES.get(str(payment_mode).strip().lower())
        if not norm:
            return _error("MODE_INVALID", "Mode de paiement invalide (cash ou bank).", 400)
        payment.payment_mode = norm
    if justificatif:
        payment.justificatif = justificatif

    # ADM-DEBT-25 : canal aligné sur le mode FINAL confirmé (corrige aussi un declare
    # banque encaissé en espèces, et renseigne les Pending legacy sans source).
    payment.source = "espece" if payment.payment_mode == "Cash" else "banque"

    # D-MANUAL-ROBUST-25 : aligne la dégradation du manuel sur KkiaPay (l'invariant argent était déjà
    # garanti par l'index unique confirmed_fee, R3 — ici on remplace un 500 par un 409 propre).
    # Pré-check = garde amont B1 RÉUTILISÉE (_assert_fee_unpaid) : fee déjà crédité → 409 « déjà réglé »,
    # le Pending manuel reste INTACT (canal HUMAIN : le staff décide — rejeter/rembourser).
    fee = frappe.get_doc("Applicant Fee", payment.applicant_fee) if payment.applicant_fee else None
    if fee:
        already = _assert_fee_unpaid(fee)
        if already:
            return already

    payment.payment_status = "Confirmed"
    payment.paid_at = now_datetime()
    # save() → validate (justificatif obligatoire Cash/Bank) + hook on_payment_update (notif UF)
    try:
        payment.save(ignore_permissions=True)
    except frappe.UniqueValidationError:
        # Course perdue à l'index entre le pré-check et le save (concurrent) → dégradation gracieuse,
        # PAS de 500 ; le Pending survit (le staff traite le doublon). Trace non silencieuse.
        frappe.db.rollback()
        log_event("payment_offline", "already_paid_race", dossier_id=dossier_id, level="warning")
        return _error("ALREADY_PAID", "Ce frais vient d'être réglé par un autre paiement.", 409)

    applicant = frappe.get_doc("Admission Applicant", dossier_id)
    apply_confirmed_payment_cascade(applicant, fee)
    send_payment_receipt(payment, applicant=applicant, fee=fee)  # reçu PDF mailé (non-bloquant)

    log_event("payment_offline", "confirmed", dossier_id=dossier_id, mode=payment.payment_mode)
    return _ok({"dossier_id": dossier_id, "status": applicant.status, "payment_id": payment.name})


@frappe.whitelist()
def request_complement(dossier_id=None, motif=None):
    """C1-COMPLETUDE — renvoie un dossier en Incomplet (INC) avec motif obligatoire.

    Role-gardé PAR ÉTAT (PO-5) : Administratif depuis SOU, Responsable depuis ETU. INC seulement
    depuis SOU/ETU. Le contrôleur trace la transition (Transition Log) via le save (rôle gardé →
    validate_workflow OK). La notification candidat est posée en phase b.
    """
    if not dossier_id or not frappe.db.exists("Admission Applicant", dossier_id):
        return _error("INVALID_DOSSIER", "Dossier inconnu.", 404)
    applicant = frappe.get_doc("Admission Applicant", dossier_id)
    state = applicant.status
    if state == "SOU":
        frappe.only_for(("Admission Administratif", "System Manager"))
    elif state == "ETU":
        frappe.only_for(("Admission Responsable", "System Manager"))
    else:
        return _error("INVALID_STATE", "Renvoi en incomplet possible seulement depuis SOU ou ETU.", 409)
    if not motif or not str(motif).strip():
        return _error("MOTIF_REQUIRED", "Le motif d'incomplétude est obligatoire.", 400)
    applicant.motif_incompletude = str(motif).strip()
    applicant.status = "INC"
    applicant.save(ignore_permissions=True)
    send_incompletude_notification(applicant, applicant.motif_incompletude)  # non-bloquant
    log_event("request_complement", "success", dossier_id=applicant.name)
    return _ok({"dossier_id": applicant.name, "status": "INC"})


@frappe.whitelist()
def start_review(dossier_id=None):
    """C1-ETUDE — met un dossier en étude (SOU→ETU).

    Role-gardé **Administratif** (A1 tranchée : l'Administratif instruit/met en étude, le Responsable
    décide). Trace via Transition Log (save, rôle gardé → validate_workflow OK).
    """
    frappe.only_for(("Admission Administratif", "System Manager"))
    if not dossier_id or not frappe.db.exists("Admission Applicant", dossier_id):
        return _error("INVALID_DOSSIER", "Dossier inconnu.", 404)
    applicant = frappe.get_doc("Admission Applicant", dossier_id)
    if applicant.status != "SOU":
        return _error("INVALID_STATE", "Mise en étude possible seulement depuis Soumis (SOU).", 409)
    # Lot 3c — garde contrôle documentaire : toutes les requises EFFECTIVES doivent être 'verified'
    # avant l'étude (waived exclu via requise_effective). Le critère est partagé (public.py).
    non_verifiees = pieces_requises_non_verifiees(applicant)
    if non_verifiees:
        labels = ", ".join(p["label"] for p in non_verifiees)
        return _error("PIECES_NON_VERIFIEES",
                      f"Contrôle documentaire incomplet — pièces non vérifiées : {labels}.", 409)
    applicant.status = "ETU"
    applicant.save(ignore_permissions=True)
    log_event("start_review", "success", dossier_id=applicant.name)
    return _ok({"dossier_id": applicant.name, "status": "ETU"})


def _stamp_decision(applicant):
    """Pose decided_by (compte staff réel) + decision_date (now) — trace de la dernière décision,
    dans le MÊME save que la transition (decided_by read-only = trace non falsifiable)."""
    applicant.decided_by = frappe.session.user
    applicant.decision_date = now_datetime()


@frappe.whitelist()
def mark_admissible(dossier_id=None):
    """C1-ETUDE — décision Admissible (ETU/ATT→ADM). Responsable. decided_by/date posés."""
    frappe.only_for(("Admission Responsable", "System Manager"))
    if not dossier_id or not frappe.db.exists("Admission Applicant", dossier_id):
        return _error("INVALID_DOSSIER", "Dossier inconnu.", 404)
    applicant = frappe.get_doc("Admission Applicant", dossier_id)
    if applicant.status not in ("ETU", "ATT"):
        return _error("INVALID_STATE", "Décision admissible possible depuis En étude (ETU) ou Liste d'attente (ATT).", 409)
    notes_gate = _require_validated_notes_if_prepa(applicant)
    if notes_gate:
        return notes_gate
    _stamp_decision(applicant)
    applicant.status = "ADM"
    applicant.save(ignore_permissions=True)
    if _is_prepa(applicant):
        send_prepa_decision_notification(applicant, "admis")  # mail Prépa avec notes (DEC-197)
    else:
        send_decision_notification(applicant, "admissible")  # mail générique (Licence) — jamais les deux
    log_event("mark_admissible", "success", dossier_id=applicant.name)
    return _ok({"dossier_id": applicant.name, "status": "ADM"})


@frappe.whitelist()
def waitlist(dossier_id=None, rang=None):
    """C1-ETUDE — mise en liste d'attente (ETU→ATT). Responsable. Rang optionnel."""
    frappe.only_for(("Admission Responsable", "System Manager"))
    if not dossier_id or not frappe.db.exists("Admission Applicant", dossier_id):
        return _error("INVALID_DOSSIER", "Dossier inconnu.", 404)
    applicant = frappe.get_doc("Admission Applicant", dossier_id)
    if applicant.status != "ETU":
        return _error("INVALID_STATE", "Mise en liste d'attente possible seulement depuis En étude (ETU).", 409)
    notes_gate = _require_validated_notes_if_prepa(applicant)
    if notes_gate:
        return notes_gate
    if rang is not None:
        try:
            applicant.rang_liste_attente = int(rang)
        except (ValueError, TypeError):
            return _error("RANG_INVALID", "Rang de liste d'attente invalide.", 400)
    _stamp_decision(applicant)
    applicant.status = "ATT"
    applicant.save(ignore_permissions=True)
    if _is_prepa(applicant):
        send_prepa_decision_notification(applicant, "liste d'attente")
    else:
        send_decision_notification(applicant, "liste d'attente")
    log_event("waitlist", "success", dossier_id=applicant.name)
    return _ok({"dossier_id": applicant.name, "status": "ATT"})


@frappe.whitelist()
def refuse(dossier_id=None, motif=None):
    """C1-ETUDE + W2 (B0.2) — refus (ETU→REF par le Responsable ; ADM→REF par la DIRECTION,
    calé sur le Workflow : revenir sur une admissibilité = niveau supérieur). Motif OBLIGATOIRE.
    Role-gardé PAR ÉTAT (même pattern que request_complement)."""
    if not dossier_id or not frappe.db.exists("Admission Applicant", dossier_id):
        return _error("INVALID_DOSSIER", "Dossier inconnu.", 404)
    applicant = frappe.get_doc("Admission Applicant", dossier_id)
    if applicant.status == "ETU":
        frappe.only_for(("Admission Responsable", "System Manager"))
    elif applicant.status == "ADM":
        frappe.only_for(("Admission Direction", "System Manager"))
    else:
        return _error("INVALID_STATE", "Refus possible depuis En étude (ETU, Responsable) ou Admissible (ADM, Direction).", 409)
    notes_gate = _require_validated_notes_if_prepa(applicant)
    if notes_gate:
        return notes_gate
    if not motif or not str(motif).strip():
        return _error("MOTIF_REQUIRED", "Le motif de refus est obligatoire.", 400)
    applicant.motif_refus = str(motif).strip()
    _stamp_decision(applicant)
    applicant.status = "REF"
    applicant.save(ignore_permissions=True)
    if _is_prepa(applicant):
        send_prepa_decision_notification(applicant, "refusé")
    else:
        send_decision_notification(applicant, "refusé", motif=applicant.motif_refus)  # motif inclus
    log_event("refuse", "success", dossier_id=applicant.name)
    return _ok({"dossier_id": applicant.name, "status": "REF"})


@frappe.whitelist()
def accept_admission(dossier_id=None, bourses_validees=None):
    """C1-ETUDE — acceptation finale de l'admission (ADM→ACC). Role-gardé Direction.

    IMPÉRATIF : passe par save() (le contrôleur) pour DÉCLENCHER _on_accepted → création du frais 2
    d'inscription (_ensure_enrollment_fee). NE PAS court-circuiter (db.set_value sauterait le frais 2).
    L'acceptation est tracée par le Transition Log (acteur Direction).

    C2-BOURSES (ruling R2) : `bourses_validees` OPTIONNEL (liste de mirror_keys) — la Direction
    valide les bourses DANS LE MÊME GESTE que l'acceptation (D11 §6.3 : validées à l'ACC,
    notifiées AVEC la décision). Omis → comportement inchangé (rétro-compatible). Gardes :
    ⊆ requested, existence miroir, exclusivité (R3). Bourses + ACC partent dans le MÊME save.
    """
    frappe.only_for(("Admission Direction", "System Manager"))
    if not dossier_id or not frappe.db.exists("Admission Applicant", dossier_id):
        return _error("INVALID_DOSSIER", "Dossier inconnu.", 404)
    applicant = frappe.get_doc("Admission Applicant", dossier_id)
    if applicant.status != "ADM":
        return _error("INVALID_STATE", "Acceptation possible seulement depuis Admissible (ADM).", 409)
    if bourses_validees is not None:
        bourses_err = _apply_validated_scholarships(applicant, bourses_validees)
        if bourses_err:
            return bourses_err  # gardes AVANT toute transition : un refus de bourse ne change rien
    applicant.status = "ACC"
    applicant.save(ignore_permissions=True)  # → _on_accepted (frais 2) ; ne pas court-circuiter
    # Bourse notifiée AVEC la décision (D11 §6.3) — taux indicatifs, jamais de montants (R4).
    send_decision_notification(applicant, "admission acceptée",
                               bourses=_validated_scholarship_details(applicant))  # générique (Prépa+Licence ; notes déjà envoyées à l'admissibilité)
    log_event("accept_admission", "success", dossier_id=applicant.name)
    return _ok({"dossier_id": applicant.name, "status": "ACC",
                "validated_scholarships": json.loads(applicant.validated_scholarships or "[]")})


# ── C4-FRONT : endpoints d'appoint (lecture role-gardée pour le front management) ──
# Le front REFLÈTE la sécurité serveur (UX role-aware) — il ne la porte pas : chaque
# endpoint reste role-gardé ici. Les LISTES passent par frappe.get_list → respectent les
# DocPerms ET le cloisonnement DEC-262 (permission_query_conditions) s'il est activé.

STAFF_ROLES = ("Admission Administratif", "Admission Responsable", "Admission Direction", "Admission SM", "System Manager")


def _bourse_state(applicant_row):
    """État bourse agrégé pour l'UI : validee > proposee > demandee > aucune."""
    if json.loads(getattr(applicant_row, "validated_scholarships", None) or "[]"):
        return "validee"
    if json.loads(getattr(applicant_row, "proposed_scholarships", None) or "[]"):
        return "proposee"
    if json.loads(getattr(applicant_row, "requested_scholarships", None) or "[]"):
        return "demandee"
    return "aucune"


def _notes_state(applicant_row):
    """État notes concours pour l'UI : validees / saisies / absentes."""
    if getattr(applicant_row, "notes_validated", 0):
        return "validees"
    if getattr(applicant_row, "notes_concours", None):
        return "saisies"
    return "absentes"


@frappe.whitelist(methods=["GET"])
def whoami():
    """C4-FRONT (DEC-264) — identité de session pour le front management.

    Renvoie l'utilisateur connecté, ses rôles admission (pour l'UX role-aware) et le
    csrf_token de session (requis pour les POST cross-origin en cookie de session).
    AUCUNE donnée métier. La gate localStorage de la maquette est remplacée par CECI.
    """
    frappe.only_for(STAFF_ROLES)
    user = frappe.session.user
    roles = [r for r in frappe.get_roles(user) if r in STAFF_ROLES]
    return _ok({
        "user": user,
        "full_name": frappe.db.get_value("User", user, "full_name") or user,
        "roles": roles,
        "csrf_token": frappe.sessions.get_csrf_token(),
    })


@frappe.whitelist(methods=["GET"])
def list_dossiers(q=None, programme=None, session=None, statuts=None, limit=200):
    """C4-FRONT — liste shaped des dossiers pour les files de travail du front.

    Lecture via frappe.get_list (PAS get_all) : DocPerms + cloisonnement DEC-262 respectés.
    Enrichit chaque dossier des indicateurs d'UI (bourse, notes, pièces manquantes,
    paiement offline à confirmer) par requêtes groupées — AUCUN montant calculé ici.
    """
    frappe.only_for(STAFF_ROLES)
    filters = {"anonymized": ("!=", 1)}
    if programme:
        filters["programme_code"] = programme
    if session:
        filters["session"] = session
    if statuts:
        statuts = json.loads(statuts) if isinstance(statuts, str) else statuts
        filters["status"] = ["in", statuts]
    rows = frappe.get_list(
        "Admission Applicant",
        filters=filters,
        fields=[
            "name", "applicant_name", "programme_code", "programme_label", "level_code",
            "session", "status", "conditionnel", "bac_verified", "resoumis",
            "requested_scholarships", "proposed_scholarships", "validated_scholarships",
            "notes_concours", "notes_validated", "rang_liste_attente", "creation", "modified",
        ],
        order_by="modified desc",
        limit_page_length=min(int(limit or 200), 500),
    )
    if q:
        ql = str(q).strip().lower()
        rows = [r for r in rows if ql in (r.applicant_name or "").lower() or ql in r.name.lower()]

    names = [r.name for r in rows]
    fees_by_app, pending_offline, missing_pieces = {}, set(), {}
    if names:
        for f in frappe.get_all("Applicant Fee", filters={"applicant": ["in", names]},
                                fields=["applicant", "fee_type", "status"]):
            fees_by_app.setdefault(f.applicant, {})[f.fee_type] = f.status
        for p in frappe.get_all("Applicant Fee Payment",
                                filters={"applicant": ["in", names], "payment_status": "Pending",
                                         "payment_mode": ["in", ["Cash", "Bank"]]},
                                fields=["applicant"]):
            pending_offline.add(p.applicant)
        for pc in frappe.get_all("Applicant Piece",
                                 filters={"parent": ["in", names], "status": ["not in", PIECES_FOURNIE_STATUSES], "required": 1},
                                 fields=["parent"]):
            missing_pieces[pc.parent] = missing_pieces.get(pc.parent, 0) + 1

    is_prepa_by_session = {}
    for s in {r.session for r in rows if r.session}:
        is_prepa_by_session[s] = bool(frappe.db.get_value("Admission Session", s, "is_prepa_session"))

    dossiers = [{
        "dossier_id": r.name,
        "nom": r.applicant_name or "",
        "programme": {"code": r.programme_code, "label": r.programme_label},
        "session": r.session,
        "level_code": r.level_code,
        "statut": r.status,
        "conditionnel": bool(r.conditionnel),
        "bac_verified": bool(r.bac_verified),
        "resoumis": bool(getattr(r, "resoumis", 0)),  # 3c-3c : badge « Re-soumis » (R11bis teste la SORTIE ; getattr = idiome _bourse_state)
        "is_prepa": is_prepa_by_session.get(r.session, False),
        "bourse": _bourse_state(r),
        "notes": _notes_state(r) if is_prepa_by_session.get(r.session) else "na",
        "rang": r.rang_liste_attente,
        "paiement_a_confirmer": r.name in pending_offline,
        "pieces_manquantes": missing_pieces.get(r.name, 0),
        "frais": fees_by_app.get(r.name, {}),
        "soumis_le": str(r.creation),
        "modifie_le": str(r.modified),
    } for r in rows]
    return _ok({"dossiers": dossiers, "total": len(dossiers), "limite": min(int(limit or 200), 500)})


@frappe.whitelist(methods=["GET"])
def get_dossier(dossier_id=None):
    """C4-FRONT — détail shaped d'un dossier pour la page /dossier du front staff.

    (public.get_dossier est candidat/token — celui-ci est staff/session.) check_permission
    explicite → compatible cloisonnement DEC-262. Montants AFFICHÉS tels quels (back fait foi).
    """
    frappe.only_for(STAFF_ROLES)
    if not dossier_id or not frappe.db.exists("Admission Applicant", dossier_id):
        return _error("INVALID_DOSSIER", "Dossier inconnu.", 404)
    applicant = frappe.get_doc("Admission Applicant", dossier_id)
    applicant.check_permission("read")

    session_doc = frappe.get_doc("Admission Session", applicant.session) if applicant.session else None
    fees = frappe.get_all("Applicant Fee", filters={"applicant": dossier_id},
                          fields=["name", "fee_type", "amount_xof", "status"])
    payments = frappe.get_all("Applicant Fee Payment", filters={"applicant": dossier_id},
                              fields=["name", "applicant_fee", "payment_mode", "amount_xof",
                                      "payment_status", "paid_at", "provider_reference",
                                      "receipt_number", "justificatif"],
                              order_by="creation desc")
    transitions = frappe.get_all("Admission Applicant Transition Log",
                                 filters={"applicant": dossier_id},
                                 fields=["from_status", "to_status", "action", "transition_at",
                                         "actor", "source"],
                                 order_by="transition_at desc", limit_page_length=50)

    def _mirror_details(keys_json):
        keys = json.loads(keys_json or "[]")
        if not keys:
            return []
        rows = frappe.get_all("Admission Scholarship Mirror",
                              filters={"mirror_key": ["in", keys]},
                              fields=["mirror_key", "scholarship_name", "rate", "exclusivity_group"])
        by_key = {r.mirror_key: r for r in rows}
        return [{"mirror_key": k,
                 "nom": by_key[k].scholarship_name if k in by_key else k,
                 "taux": float(by_key[k].rate) if k in by_key else None,
                 "exclusivity_group": (by_key[k].exclusivity_group or "") if k in by_key else ""}
                for k in keys]

    return _ok({
        "dossier_id": applicant.name,
        "statut": applicant.status,
        "conditionnel": bool(applicant.conditionnel),
        "bac_verified": bool(applicant.bac_verified),
        "is_prepa": bool(session_doc.is_prepa_session) if session_doc else False,
        "identite": {"prenom": applicant.first_name, "nom": applicant.last_name,
                     "email": applicant.email, "tel": applicant.phone,
                     "nom_complet": applicant.applicant_name},
        "programme": {"code": applicant.programme_code, "label": applicant.programme_label,
                      "level_code": applicant.level_code},
        "session": {"id": applicant.session,
                    "label": session_doc.label if session_doc else None,
                    "academic_year": session_doc.academic_year if session_doc else None},
        "person_id": applicant.person_id,
        "bac_profile": applicant.bac_profile,
        "motif_incompletude": applicant.motif_incompletude,
        "motif_refus": applicant.motif_refus,
        "motif_desistement": applicant.motif_desistement,
        "rang_liste_attente": applicant.rang_liste_attente,
        "pieces": [{"code": p.piece_code, "label": p.label, "statut": p.status,
                    "requise": requise_effective(p), "staff_requirement": p.staff_requirement,
                    "reject_reason": p.reject_reason, "reject_comment": p.reject_comment,
                    "has_file": bool(p.file)} for p in (applicant.pieces or [])],
        "frais": fees,
        "paiements": payments,
        "notes": {"valeurs": json.loads(applicant.notes_concours) if applicant.notes_concours else None,
                  "validees": bool(applicant.notes_validated)},
        "bourses": {"demandees": _mirror_details(applicant.requested_scholarships),
                    "proposees": _mirror_details(applicant.proposed_scholarships),
                    "validees": _mirror_details(applicant.validated_scholarships)},
        "promo": {"code": applicant.promo_code, "rate": float(applicant.promo_rate or 0),
                  "captured_date": str(applicant.promo_captured_date or "")},
        "acompte_xof": float(applicant.acompte_xof or 0),
        "transitions": transitions,
        "soumis_le": str(applicant.creation),
    })


@frappe.whitelist(methods=["GET"])
def download_receipt(payment_id=None):
    """C4-FRONT (ARGENT) — reçu PDF téléchargeable par l'agent.

    Réutilise le MÊME gabarit que le reçu mailé au candidat (receipt.render_receipt_html) —
    une seule source de vérité visuelle/financière. Paiement Confirmed uniquement.
    check_permission sur le dossier → compatible DEC-262.
    """
    frappe.only_for(STAFF_ROLES)
    if not payment_id or not frappe.db.exists("Applicant Fee Payment", payment_id):
        return _error("INVALID_PAYMENT", "Paiement inconnu.", 404)
    payment = frappe.get_doc("Applicant Fee Payment", payment_id)
    if payment.payment_status not in ("Confirmed", "Paid"):
        return _error("NOT_CONFIRMED", "Le reçu n'existe que pour un paiement confirmé.", 409)
    applicant = frappe.get_doc("Admission Applicant", payment.applicant)
    applicant.check_permission("read")
    fee = frappe.get_doc("Applicant Fee", payment.applicant_fee) if payment.applicant_fee else None

    from frappe.utils.pdf import get_pdf
    from admission.api.receipt import _get_legal_text, render_receipt_html
    html = render_receipt_html(payment, applicant, fee, legal_text=_get_legal_text())
    frappe.local.response.filename = f"recu-{payment.name}.pdf"
    frappe.local.response.filecontent = get_pdf(html)
    frappe.local.response.type = "pdf"
    log_event("download_receipt", "success", dossier_id=payment.applicant, ref=payment.name)


@frappe.whitelist(methods=["GET"])
def stats_direction():
    """C4-FRONT — agrégats lecture seule du tableau Direction (A03 : montants ENCAISSÉS
    agrégés, jamais d'écriture comptable ni RIB). Direction-only.

    NB : agrégats globaux anonymes (pas de liste nominative) — assumés hors du
    cloisonnement par dossier DEC-262, qui s'applique aux listes.
    """
    frappe.only_for(("Admission Direction", "System Manager"))
    by_status = dict(frappe.db.sql(
        "SELECT status, COUNT(*) FROM `tabAdmission Applicant` WHERE anonymized != 1 GROUP BY status"))
    by_programme = dict(frappe.db.sql(
        "SELECT programme_code, COUNT(*) FROM `tabAdmission Applicant` WHERE anonymized != 1 GROUP BY programme_code"))
    encaisse = {row[0] or "?": float(row[1] or 0) for row in frappe.db.sql(
        """SELECT f.fee_type, COALESCE(SUM(p.amount_xof), 0)
             FROM `tabApplicant Fee Payment` p
             LEFT JOIN `tabApplicant Fee` f ON p.applicant_fee = f.name
            WHERE p.payment_status IN ('Confirmed', 'Paid')
            GROUP BY f.fee_type""")}
    sessions = frappe.get_all("Admission Session",
                              fields=["name", "label", "academic_year", "programme_code",
                                      "is_open", "opens_on", "closes_on"])
    ins_by_session = dict(frappe.db.sql(
        "SELECT session, COUNT(*) FROM `tabAdmission Applicant` WHERE status='INS' GROUP BY session"))
    for s in sessions:
        s["inscrits"] = int(ins_by_session.get(s.name, 0))
    return _ok({
        "par_statut": {k: int(v) for k, v in by_status.items()},
        "par_programme": {k: int(v) for k, v in by_programme.items()},
        "encaisse_xof": encaisse,
        "sessions": sessions,
    })


# ── C3-ENROLL : inscription réelle (ACC→INS) ──────────────────────────────────


@frappe.whitelist()
def enroll(dossier_id=None):
    """C3-ENROLL (T8) — inscription réelle (ACC→INS). Role-gardé Direction.

    Pré-vérifie les gates (frais 2 payé + consentement DATA_TRANSFER) pour un retour API
    propre, puis passe par save() (le contrôleur) : les gates sont re-vérifiées dans validate
    (defense in depth) et on_update déclenche le pont campus + double-check UF (enqueue
    retry=3). IRRÉVERSIBLE en aval (création Student/User/Fee côté campus) — l'idempotence
    est portée par les 5 étages de clés (person_id SA, job_id+IntegrationLog cascade,
    C5 Student, IntegrationLog fee sync, StudentFee UF + C5).
    """
    frappe.only_for(("Admission Direction", "System Manager"))
    if not dossier_id or not frappe.db.exists("Admission Applicant", dossier_id):
        return _error("INVALID_DOSSIER", "Dossier inconnu.", 404)
    applicant = frappe.get_doc("Admission Applicant", dossier_id)
    if applicant.status != "INS" and applicant.status != "ACC":
        return _error("INVALID_STATE", "Inscription possible seulement depuis Accepté (ACC).", 409)
    if applicant.status == "INS":
        return _ok({"dossier_id": applicant.name, "status": "INS", "idempotent": True})
    from admission.api.legal import _require_consent_record
    from admission.api.public import _check_enrollment_fee_paid
    try:
        _check_enrollment_fee_paid(applicant.name)
        _require_consent_record(applicant.name, "DATA_TRANSFER")
    except Exception as exc:
        return _error("GATE_FAILED", str(exc), 409)
    applicant.status = "INS"
    applicant.save(ignore_permissions=True)  # gates re-vérifiées + on_update → pont/double-check
    # LOT M (M4) : mail « inscription confirmée » (jalon final du parcours candidat).
    # student_id inconnu ici (créé en asynchrone côté campus) — param optionnel.
    send_enrolled(applicant)
    log_event("enroll", "success", dossier_id=applicant.name)
    return _ok({"dossier_id": applicant.name, "status": "INS"})


# ── C2-BOURSES : proposition (Responsable) / validation (Direction, à l'ACC) ──


def _parse_scholarship_keys(bourses):
    """Parse une sélection de bourses (liste ou JSON string) → (list[str] dédupliquée, None)
    ou (None, message d'erreur). Garde de format, pas de JSON libre arbitraire."""
    if isinstance(bourses, str):
        try:
            bourses = json.loads(bourses)
        except (ValueError, TypeError):
            return None, "Format de bourses invalide (liste JSON de mirror_keys attendue)."
    if not isinstance(bourses, list):
        return None, "Les bourses doivent être une liste de mirror_keys."
    keys = []
    for k in bourses:
        if not isinstance(k, str) or not k.strip():
            return None, "Mirror_key de bourse invalide."
        if k.strip() not in keys:
            keys.append(k.strip())
    return keys, None


def _check_scholarship_selection(applicant, keys, *, enforce_exclusivity):
    """Gardes ARGENT d'une sélection de bourses (C2-BOURSES) : ⊆ requested_scholarships
    (D11 §6.3 — le staff instruit la demande du candidat, il n'en crée pas), existence au
    miroir UF, et cohérence d'exclusivité (ruling R3, à la VALIDATION seulement — réutilise
    _apply_exclusivity_local, même algorithme qu'UF). Admission ne calcule AUCUN montant ici
    (DEC-206) : les taux ne servent qu'à départager l'exclusivité. Retourne un _error ou None."""
    requested = json.loads(applicant.requested_scholarships or "[]")
    not_requested = [k for k in keys if k not in requested]
    if not_requested:
        return _error(
            "SCHOLARSHIP_NOT_REQUESTED",
            f"Bourses non demandées par le candidat : {', '.join(not_requested)}.", 409,
        )
    if not keys:
        return None
    rows = frappe.get_all(
        "Admission Scholarship Mirror",
        filters={"mirror_key": ["in", keys]},
        fields=["mirror_key", "rate", "exclusivity_group"],
    )
    found = {r.mirror_key for r in rows}
    unknown = [k for k in keys if k not in found]
    if unknown:
        return _error(
            "SCHOLARSHIP_UNKNOWN",
            f"Bourses inconnues au catalogue UF : {', '.join(unknown)}.", 404,
        )
    if enforce_exclusivity:
        kept = _apply_exclusivity_local([
            {"mirror_key": r.mirror_key, "rate": float(r.rate or 0),
             "exclusivity_group": r.exclusivity_group or ""}
            for r in rows
        ])
        if len(kept) < len(rows):
            return _error(
                "EXCLUSIVITY_CONFLICT",
                "Sélection incohérente : plusieurs bourses du même groupe d'exclusivité.", 409,
            )
    return None


def _validated_scholarship_details(applicant):
    """Détails [{scholarship_name, rate}] des bourses VALIDÉES du dossier, lus au miroir UF —
    pour la notification (ruling R4 : taux indicatifs, jamais de montants, admission ne calcule pas)."""
    keys = json.loads(applicant.validated_scholarships or "[]")
    if not keys:
        return []
    rows = frappe.get_all(
        "Admission Scholarship Mirror",
        filters={"mirror_key": ["in", keys]},
        fields=["mirror_key", "scholarship_name", "rate"],
    )
    by_key = {r.mirror_key: r for r in rows}
    return [
        {"scholarship_name": by_key[k].scholarship_name, "rate": float(by_key[k].rate or 0)}
        for k in keys if k in by_key
    ]


@frappe.whitelist()
def propose_scholarships(dossier_id=None, bourses=None):
    """C2-BOURSES — proposition de bourses par le Responsable (D11 §6.3, étape 2).

    Responsable, dossier en instruction (ETU ou ATT). `bourses` = liste de mirror_keys
    ⊆ requested_scholarships + existence au miroir UF (l'exclusivité n'est tranchée qu'à la
    validation Direction — ruling R3). Stamp scholarships_proposed_by/date (read-only, trace
    non falsifiable). AUCUN effet financier : la proposition guide la validation à l'ACC.
    Re-proposition possible tant que le dossier est en ETU/ATT (la dernière fait foi).
    """
    frappe.only_for(("Admission Responsable", "System Manager"))
    if not dossier_id or not frappe.db.exists("Admission Applicant", dossier_id):
        return _error("INVALID_DOSSIER", "Dossier inconnu.", 404)
    applicant = frappe.get_doc("Admission Applicant", dossier_id)
    if applicant.status not in ("ETU", "ATT"):
        return _error("INVALID_STATE", "Proposition de bourses possible seulement en étude (ETU) ou liste d'attente (ATT).", 409)
    keys, parse_err = _parse_scholarship_keys(bourses)
    if parse_err:
        return _error("BOURSES_FORMAT_INVALID", parse_err, 400)
    guard = _check_scholarship_selection(applicant, keys, enforce_exclusivity=False)
    if guard:
        return guard
    applicant.proposed_scholarships = json.dumps(keys)
    applicant.scholarships_proposed_by = frappe.session.user
    applicant.scholarships_proposed_date = now_datetime()
    applicant.save(ignore_permissions=True)
    log_event("propose_scholarships", "success", dossier_id=applicant.name, bourses=",".join(keys))
    return _ok({"dossier_id": applicant.name, "proposed_scholarships": keys})


def _apply_validated_scholarships(applicant, bourses_validees):
    """C2-BOURSES (ruling R2) — pose validated_scholarships + stamps sur le doc EN MÉMOIRE,
    SANS save : l'écriture part dans le MÊME save que la transition ACC (validation atomique
    avec la décision, D11 §6.3). Gardes complètes (⊆ requested, existence, exclusivité R3).
    Retourne un _error ou None."""
    keys, parse_err = _parse_scholarship_keys(bourses_validees)
    if parse_err:
        return _error("BOURSES_FORMAT_INVALID", parse_err, 400)
    guard = _check_scholarship_selection(applicant, keys, enforce_exclusivity=True)
    if guard:
        return guard
    applicant.validated_scholarships = json.dumps(keys)
    applicant.scholarships_validated_by = frappe.session.user
    applicant.scholarships_validated_date = now_datetime()
    return None


# ── C1-ACO : admission conditionnelle — vérification diplôme (DEC-214) ─────────


def _has_uploaded_diploma(applicant):
    """True si la pièce diplome_bac du dossier est fournie (status uploaded)."""
    for row in (applicant.pieces or []):
        if row.piece_code == "diplome_bac" and row.status == "uploaded":
            return True
    return False


@frappe.whitelist()
def verify_bac_diploma(dossier_id=None):
    """C1-ACO — vérification du diplôme bac par l'Administratif (DEC-214, étape i).

    Réservé aux dossiers conditionnels (bac-en-attente) en admission conditionnelle (ACO). Exige la
    pièce diplome_bac déposée (status uploaded → sinon DIPLOMA_MISSING). Pose bac_verified + by/date.
    NE LÈVE PAS la condition : la levée (ACO→ACC) est l'étape Direction (lift_condition). INV-HUMAN :
    la vérification humaine (Adm) et la levée humaine (Dir) sont deux gestes séparés.
    """
    frappe.only_for(("Admission Administratif", "System Manager"))
    if not dossier_id or not frappe.db.exists("Admission Applicant", dossier_id):
        return _error("INVALID_DOSSIER", "Dossier inconnu.", 404)
    applicant = frappe.get_doc("Admission Applicant", dossier_id)
    if applicant.status != "ACO":
        return _error("INVALID_STATE", "Vérification du diplôme possible seulement en admission conditionnelle (ACO).", 409)
    if not applicant.conditionnel:
        return _error("NOT_CONDITIONAL", "Dossier non conditionnel : pas de diplôme bac à vérifier.", 409)
    if not _has_uploaded_diploma(applicant):
        return _error("DIPLOMA_MISSING", "Le diplôme du baccalauréat n'a pas été fourni.", 409)
    applicant.bac_verified = 1
    applicant.bac_verified_by = frappe.session.user
    applicant.bac_verified_date = now_datetime()
    applicant.save(ignore_permissions=True)  # status inchangé : ne transitionne PAS (levée = étape c)
    log_event("verify_bac_diploma", "success", dossier_id=applicant.name)
    return _ok({"dossier_id": applicant.name, "bac_verified": 1})


# ── Lot 3c — contrôle documentaire par pièce (verify / reject / require / waive + dossier) ─────

REJECT_REASONS = {"Illisible / floue", "Mauvaise pièce", "Incomplète", "Non conforme", "Expirée", "Autre"}


def _resolve_piece_sou(dossier_id, piece_code):
    """Garde commune des verdicts pièce : rôle Administratif + dossier SOU + pièce existante.
    Renvoie (applicant, row, err) — err = réponse _error ou None. Le rôle lève PermissionError."""
    frappe.only_for(CONFIRM_ROLES)
    if not dossier_id or not frappe.db.exists("Admission Applicant", dossier_id):
        return None, None, _error("INVALID_DOSSIER", "Dossier inconnu.", 404)
    applicant = frappe.get_doc("Admission Applicant", dossier_id)
    if applicant.status != "SOU":
        return None, None, _error("INVALID_STATE", "Contrôle documentaire possible seulement en Soumis (SOU).", 409)
    row = next((p for p in (applicant.pieces or []) if p.piece_code == piece_code), None)
    if not row:
        return None, None, _error("PIECE_NOT_FOUND", "Pièce inconnue pour ce dossier.", 404)
    return applicant, row, None


@frappe.whitelist()
def verify_piece(dossier_id=None, piece_code=None):
    """Vérifie une pièce déposée (forme conforme). Pose verified + verdict + historique. Diplôme
    fusionné : pose AUSSI bac_verified=1 (dérivé) → lift_condition inchangé."""
    applicant, row, err = _resolve_piece_sou(dossier_id, piece_code)
    if err:
        return err
    if row.status == "missing":
        return _error("PIECE_NOT_UPLOADED", "Aucune pièce déposée à examiner.", 409)
    row.status = "verified"
    row.reject_reason = None
    row.reject_comment = None
    row.verdict_at = now_datetime()
    row.verdict_by = frappe.session.user
    if row.piece_code == "diplome_bac":
        applicant.bac_verified = 1
    applicant.resoumis = 0  # 3c-3a : un re-contrôle staff éteint le marqueur « re-soumis » candidat
    applicant.save(ignore_permissions=True)
    _record_piece_verdict(applicant.name, piece_code, "verify")
    frappe.db.commit()
    return _ok({"dossier_id": applicant.name, "piece_code": piece_code, "status": "verified"})


@frappe.whitelist()
def reject_piece(dossier_id=None, piece_code=None, reason=None, comment=None):
    """Rejette une pièce déposée (motif liste + commentaire). « Autre » force le commentaire. Diplôme
    fusionné : remet bac_verified=0 (cohérence si re-rejet d'un diplôme précédemment vérifié)."""
    applicant, row, err = _resolve_piece_sou(dossier_id, piece_code)
    if err:
        return err
    if row.status == "missing":
        return _error("PIECE_NOT_UPLOADED", "Aucune pièce déposée à examiner.", 409)
    reason = (reason or "").strip()
    if reason not in REJECT_REASONS:
        return _error("REASON_INVALID", "Motif de rejet invalide.", 400)
    comment = (comment or "").strip()
    if reason == "Autre" and not comment:
        return _error("COMMENT_REQUIRED", "Un commentaire est obligatoire pour le motif « Autre ».", 400)
    row.status = "rejected"
    row.reject_reason = reason
    row.reject_comment = comment
    row.verdict_at = now_datetime()
    row.verdict_by = frappe.session.user
    if row.piece_code == "diplome_bac":
        applicant.bac_verified = 0
    applicant.resoumis = 0  # 3c-3a : un re-contrôle staff éteint le marqueur « re-soumis » candidat
    applicant.save(ignore_permissions=True)
    _record_piece_verdict(applicant.name, piece_code, "reject", reason=reason, comment=comment)
    frappe.db.commit()
    return _ok({"dossier_id": applicant.name, "piece_code": piece_code, "status": "rejected"})


@frappe.whitelist()
def require_piece(dossier_id=None, piece_code=None):
    """Exige une pièce (surcharge staff) pour CE dossier — sans toucher la liste structurelle."""
    applicant, row, err = _resolve_piece_sou(dossier_id, piece_code)
    if err:
        return err
    row.staff_requirement = "required"
    applicant.save(ignore_permissions=True)
    _record_piece_verdict(applicant.name, piece_code, "require")
    frappe.db.commit()
    return _ok({"dossier_id": applicant.name, "piece_code": piece_code, "staff_requirement": "required"})


@frappe.whitelist()
def waive_piece(dossier_id=None, piece_code=None):
    """Dispense une pièce (surcharge staff) — ne bloque plus les gardes (notif, SOU→ETU)."""
    applicant, row, err = _resolve_piece_sou(dossier_id, piece_code)
    if err:
        return err
    row.staff_requirement = "waived"
    applicant.save(ignore_permissions=True)
    _record_piece_verdict(applicant.name, piece_code, "waive")
    frappe.db.commit()
    return _ok({"dossier_id": applicant.name, "piece_code": piece_code, "staff_requirement": "waived"})


@frappe.whitelist()
def reset_piece_requirement(dossier_id=None, piece_code=None):
    """Réinitialise l'exigence à 'default' (la pièce re-suit la règle structurelle du profil via
    requise_effective). Miroir de require/waive — révisable librement tant que dossier SOU."""
    applicant, row, err = _resolve_piece_sou(dossier_id, piece_code)
    if err:
        return err
    row.staff_requirement = "default"
    applicant.save(ignore_permissions=True)
    _record_piece_verdict(applicant.name, piece_code, "reset")
    frappe.db.commit()
    return _ok({"dossier_id": applicant.name, "piece_code": piece_code, "staff_requirement": "default"})


@frappe.whitelist()
def reject_dossier(dossier_id=None, motif=None):
    """Rejet documentaire du dossier (SOU→REJ, sortie de boucle de re-soumission). Réversible (reopen).
    Role Administratif, motif obligatoire, notif candidat, PAS de remboursement (CGV)."""
    frappe.only_for(CONFIRM_ROLES)
    if not dossier_id or not frappe.db.exists("Admission Applicant", dossier_id):
        return _error("INVALID_DOSSIER", "Dossier inconnu.", 404)
    applicant = frappe.get_doc("Admission Applicant", dossier_id)
    if applicant.status != "SOU":
        return _error("INVALID_STATE", "Rejet documentaire possible seulement depuis Soumis (SOU).", 409)
    if not motif or not str(motif).strip():
        return _error("MOTIF_REQUIRED", "Le motif de rejet est obligatoire.", 400)
    applicant.motif_rejet = str(motif).strip()
    _stamp_decision(applicant)
    applicant.status = "REJ"
    applicant.save(ignore_permissions=True)
    send_decision_notification(applicant, "refusé", motif=applicant.motif_rejet)
    log_event("reject_dossier", "success", dossier_id=applicant.name)
    return _ok({"dossier_id": applicant.name, "status": "REJ"})


@frappe.whitelist()
def reopen_dossier(dossier_id=None):
    """Réouverture d'un dossier rejeté (REJ→SOU) — réversibilité du rejet documentaire. Role Administratif."""
    frappe.only_for(CONFIRM_ROLES)
    if not dossier_id or not frappe.db.exists("Admission Applicant", dossier_id):
        return _error("INVALID_DOSSIER", "Dossier inconnu.", 404)
    applicant = frappe.get_doc("Admission Applicant", dossier_id)
    if applicant.status != "REJ":
        return _error("INVALID_STATE", "Réouverture possible seulement depuis Rejeté (REJ).", 409)
    applicant.motif_rejet = None
    applicant.status = "SOU"
    applicant.save(ignore_permissions=True)
    log_event("reopen_dossier", "success", dossier_id=applicant.name)
    return _ok({"dossier_id": applicant.name, "status": "SOU"})


@frappe.whitelist()
def notify_pieces_recap(dossier_id=None):
    """Geste SÉPARÉ : 1 mail récap (rejetées + à fournir). Bloqué tant qu'une requise_effective n'a
    pas de statut terminal (uploaded non traité / missing requise non qualifié)."""
    frappe.only_for(CONFIRM_ROLES)
    if not dossier_id or not frappe.db.exists("Admission Applicant", dossier_id):
        return _error("INVALID_DOSSIER", "Dossier inconnu.", 404)
    applicant = frappe.get_doc("Admission Applicant", dossier_id)
    if applicant.status != "SOU":
        return _error("INVALID_STATE", "Notification possible seulement en Soumis (SOU).", 409)
    if notify_pieces_blocked(applicant):
        return _error("PIECES_NON_TRAITEES",
                      "Toutes les pièces requises doivent être traitées (vérifiées/rejetées) ou qualifiées avant de notifier.", 409)
    recap = pieces_recap(applicant)
    # 3c-3a : CTA tokenisé → /reprise actionnable (pas /suivi passif). Rotation du token (pattern
    # recovery : le clair n'est jamais persisté) ; l'OTP reste re-exigé à l'arrivée (double barrière).
    tok = _generate_token()
    applicant.dossier_token_hash = _hash(tok)
    applicant.token_expires_at = add_days(now_datetime(), TOKEN_TTL_DAYS)
    applicant.otp_verified = 0
    # RAPPELS-J4J6 : ancre STABLE des rappels (chaque récap = nouveau cycle) + reset des flags. Les
    # fenêtres J4/J6 se calculent sur cette date (pas sur token_expires_at qui glisse à l'accès candidat).
    applicant.pieces_recap_sent_at = now_datetime()
    applicant.rappel_j4_sent = 0
    applicant.rappel_j6_sent = 0
    applicant.save(ignore_permissions=True)
    send_pieces_recap_notification(applicant, recap["rejetees"], recap["a_fournir"], token=tok)
    log_event("notify_pieces_recap", "success", dossier_id=applicant.name)
    return _ok({"dossier_id": applicant.name,
                "rejetees": len(recap["rejetees"]), "a_fournir": len(recap["a_fournir"])})


@frappe.whitelist()
def download_piece_file(dossier_id=None, piece_code=None):
    """Visualisation staff d'une pièce (File privé). Role staff + check_permission (miroir download_receipt)."""
    frappe.only_for(STAFF_ROLES)
    if not dossier_id or not frappe.db.exists("Admission Applicant", dossier_id):
        return _error("INVALID_DOSSIER", "Dossier inconnu.", 404)
    applicant = frappe.get_doc("Admission Applicant", dossier_id)
    applicant.check_permission("read")
    row = next((p for p in (applicant.pieces or []) if p.piece_code == piece_code), None)
    if not row or not row.file:
        return _error("PIECE_FILE_NOT_FOUND", "Aucun fichier pour cette pièce.", 404)
    file_doc = frappe.get_doc("File", row.file)
    frappe.local.response.filename = file_doc.file_name
    frappe.local.response.filecontent = file_doc.get_content()
    # D-DOWNLOAD-TYPE-20 : sans response.type, Frappe sérialise filename/filecontent en JSON
    # (le front voit application/json → "Pièce indisponible."). Miroir download_receipt
    # (qui pose response.type), mais "download" (→ as_raw) car les pièces sont pdf/jpg/png :
    # as_raw dérive le Content-Type du nom de fichier (mimetypes.guess_type) → type RÉEL du
    # fichier, donc le blob front rend inline. "pdf" forcerait application/pdf (faux pour jpg/png).
    frappe.local.response.type = "download"


@frappe.whitelist()
def conditional_admission(dossier_id=None):
    """C1-ACO — admission conditionnelle (ETU→ACO). Responsable.

    Réservée aux dossiers conditionnels (bac-en-attente) : admis sous réserve de présentation du
    diplôme. Garde notes Prépa (cohérence C1-CONCOURS : un Prépa entre en ACO avec notes validées).
    stamp decided_by/date + notif (« admission conditionnelle » — Prépa avec notes / Licence générique).
    """
    frappe.only_for(("Admission Responsable", "System Manager"))
    if not dossier_id or not frappe.db.exists("Admission Applicant", dossier_id):
        return _error("INVALID_DOSSIER", "Dossier inconnu.", 404)
    applicant = frappe.get_doc("Admission Applicant", dossier_id)
    if applicant.status != "ETU":
        return _error("INVALID_STATE", "Admission conditionnelle possible seulement depuis En étude (ETU).", 409)
    if not applicant.conditionnel:
        return _error("NOT_CONDITIONAL", "Admission conditionnelle réservée aux dossiers bac-en-attente.", 409)
    notes_gate = _require_validated_notes_if_prepa(applicant)
    if notes_gate:
        return notes_gate
    _stamp_decision(applicant)
    applicant.status = "ACO"
    applicant.save(ignore_permissions=True)
    if _is_prepa(applicant):
        send_prepa_decision_notification(applicant, "admission conditionnelle")
    else:
        send_decision_notification(applicant, "admission conditionnelle")
    log_event("conditional_admission", "success", dossier_id=applicant.name)
    return _ok({"dossier_id": applicant.name, "status": "ACO"})


@frappe.whitelist()
def lift_condition(dossier_id=None, bourses_validees=None):
    """C1-ACO — levée de la condition (ACO→ACC). Direction (calé sur le Workflow Lift Condition).

    INV-HUMAN : exige bac_verified=1 (vérification humaine préalable de l'Administratif) — AUCUNE
    levée sans vérification, AUCUNE auto-levée. Passe par save() (le contrôleur) → _on_accepted →
    création du frais 2 d'inscription. NE PAS court-circuiter (db.set_value sauterait le frais 2).
    Notifie « admission acceptée » (notes déjà communiquées à l'admission conditionnelle).

    C2-BOURSES (ruling R2) : `bourses_validees` OPTIONNEL — même geste atomique de validation
    Direction que accept_admission (la branche conditionnelle atteint ACC ici, pas par ADM→ACC).
    """
    frappe.only_for(("Admission Direction", "System Manager"))
    if not dossier_id or not frappe.db.exists("Admission Applicant", dossier_id):
        return _error("INVALID_DOSSIER", "Dossier inconnu.", 404)
    applicant = frappe.get_doc("Admission Applicant", dossier_id)
    if applicant.status != "ACO":
        return _error("INVALID_STATE", "Levée de condition possible seulement depuis admission conditionnelle (ACO).", 409)
    if not applicant.bac_verified:
        return _error("BAC_NOT_VERIFIED", "Levée impossible : le diplôme bac n'a pas été vérifié.", 409)
    if bourses_validees is not None:
        bourses_err = _apply_validated_scholarships(applicant, bourses_validees)
        if bourses_err:
            return bourses_err  # gardes AVANT toute transition
    applicant.status = "ACC"
    applicant.save(ignore_permissions=True)  # → _on_accepted (frais 2) ; ne pas court-circuiter
    send_decision_notification(applicant, "admission acceptée",
                               bourses=_validated_scholarship_details(applicant))
    log_event("lift_condition", "success", dossier_id=applicant.name)
    return _ok({"dossier_id": applicant.name, "status": "ACC",
                "validated_scholarships": json.loads(applicant.validated_scholarships or "[]")})


@frappe.whitelist()
def refuse_condition(dossier_id=None, motif=None):
    """C1-ACO — refus de la condition (ACO→REF). Direction. Motif OBLIGATOIRE (échec bac).

    SPEC §5 : ACO→REF si bac non obtenu (frais 1 non remboursable, PO-3). stamp + notif « refusé »+motif.
    """
    frappe.only_for(("Admission Direction", "System Manager"))
    if not dossier_id or not frappe.db.exists("Admission Applicant", dossier_id):
        return _error("INVALID_DOSSIER", "Dossier inconnu.", 404)
    applicant = frappe.get_doc("Admission Applicant", dossier_id)
    if applicant.status != "ACO":
        return _error("INVALID_STATE", "Refus de condition possible seulement depuis admission conditionnelle (ACO).", 409)
    if not motif or not str(motif).strip():
        return _error("MOTIF_REQUIRED", "Le motif de refus est obligatoire.", 400)
    applicant.motif_refus = str(motif).strip()
    _stamp_decision(applicant)
    applicant.status = "REF"
    applicant.save(ignore_permissions=True)
    send_decision_notification(applicant, "refusé", motif=applicant.motif_refus)
    log_event("refuse_condition", "success", dossier_id=applicant.name)
    return _ok({"dossier_id": applicant.name, "status": "REF"})


# ── C1-CONCOURS : branche Prépa (DEC-197) ─────────────────────────────────────


def _is_prepa(applicant):
    """True si le dossier relève d'une session Prépa (concours), via Admission Session.is_prepa_session."""
    return bool(frappe.db.get_value("Admission Session", applicant.session, "is_prepa_session"))


def _require_validated_notes_if_prepa(applicant):
    """Garde décision Prépa : un dossier Prépa exige des notes de concours VALIDÉES avant toute
    décision. Un dossier Licence (pas de notes) n'est JAMAIS bloqué. Retourne un _error ou None."""
    if _is_prepa(applicant) and not applicant.notes_validated:
        return _error("NOTES_NOT_VALIDATED", "Décision impossible : les notes de concours ne sont pas validées.", 409)
    return None


def _validate_notes_format(notes):
    """Garde de format : `notes` = objet {label: nombre} (pas de JSON libre arbitraire).

    Retourne (parsed_dict, None) si valide, sinon (None, message d'erreur).
    """
    if isinstance(notes, str):
        try:
            notes = json.loads(notes)
        except (ValueError, TypeError):
            return None, "Format de notes invalide (JSON attendu)."
    if not isinstance(notes, dict) or not notes:
        return None, "Les notes doivent être un objet non vide {épreuve: note}."
    parsed = {}
    for label, value in notes.items():
        if not isinstance(label, str) or not label.strip():
            return None, "Libellé d'épreuve invalide."
        try:
            parsed[label.strip()] = float(value)
        except (ValueError, TypeError):
            return None, f"Note non numérique pour '{label}'."
    return parsed, None


@frappe.whitelist()
def saisir_note_concours(dossier_id=None, notes=None):
    """C1-CONCOURS — saisie des notes de concours Prépa, NON validées (DEC-197).

    Administratif, dossiers **Prépa** uniquement (is_prepa_session), en étude (ETU). `notes` = objet
    {épreuve: note numérique} (garde de format). Re-saisie → réinitialise la validation (intégrité).
    """
    frappe.only_for(("Admission Administratif", "System Manager"))
    if not dossier_id or not frappe.db.exists("Admission Applicant", dossier_id):
        return _error("INVALID_DOSSIER", "Dossier inconnu.", 404)
    applicant = frappe.get_doc("Admission Applicant", dossier_id)
    if not _is_prepa(applicant):
        return _error("NOT_PREPA", "Saisie de notes réservée aux dossiers Prépa (concours).", 409)
    if applicant.status != "ETU":
        return _error("INVALID_STATE", "Saisie de notes possible seulement en étude (ETU).", 409)
    parsed, fmt_err = _validate_notes_format(notes)
    if fmt_err:
        return _error("NOTES_FORMAT_INVALID", fmt_err, 400)
    applicant.notes_concours = json.dumps(parsed, ensure_ascii=False)
    applicant.notes_validated = 0          # re-saisie → re-validation requise (intégrité)
    applicant.notes_validated_by = None
    applicant.notes_validated_date = None
    applicant.save(ignore_permissions=True)
    log_event("saisir_note_concours", "success", dossier_id=applicant.name)
    return _ok({"dossier_id": applicant.name, "notes": parsed})


@frappe.whitelist()
def valider_notes_concours(dossier_id=None):
    """C1-CONCOURS — validation des notes de concours Prépa (séparation : saisie Adm ≠ validation Resp).

    Responsable, Prépa-only, notes saisies présentes. Idempotent (déjà validées → no-op). Pose
    notes_validated=1 + notes_validated_by (validateur réel) + notes_validated_date.
    """
    frappe.only_for(("Admission Responsable", "System Manager"))
    if not dossier_id or not frappe.db.exists("Admission Applicant", dossier_id):
        return _error("INVALID_DOSSIER", "Dossier inconnu.", 404)
    applicant = frappe.get_doc("Admission Applicant", dossier_id)
    if not _is_prepa(applicant):
        return _error("NOT_PREPA", "Validation de notes réservée aux dossiers Prépa.", 409)
    if applicant.status != "ETU":  # W6 : validation bornée à l'étude (comme la saisie)
        return _error("INVALID_STATE", "Validation des notes possible seulement en étude (ETU).", 409)
    if not applicant.notes_concours:
        return _error("NOTES_MISSING", "Aucune note saisie à valider.", 409)
    if applicant.notes_validated:
        return _ok({"dossier_id": applicant.name, "idempotent": True, "notes_validated": 1})
    applicant.notes_validated = 1
    applicant.notes_validated_by = frappe.session.user
    applicant.notes_validated_date = now_datetime()
    applicant.save(ignore_permissions=True)
    log_event("valider_notes_concours", "success", dossier_id=applicant.name)
    return _ok({"dossier_id": applicant.name, "notes_validated": 1})


@frappe.whitelist()
def initiate_online_payment(dossier_id=None, idempotency_key=None, fee_type="application", acompte_xof=0):
    """Scénario 3 : l'agent (Administratif) initie un paiement online sur le dossier (frais 1 OU frais 2).

    Réutilise le cœur candidat lié au BON dossier + bon fee_type par provider_reference (session staff +
    dossier_id, SANS token candidat). Le webhook promeut ensuite le Pending (phase d). Dispositif
    simulation en dev (DEC-216/217) ; branchement KkiaPay réel = bascule recette/prod.
    """
    frappe.only_for(CONFIRM_ROLES)
    if not dossier_id or not frappe.db.exists("Admission Applicant", dossier_id):
        return _error("INVALID_DOSSIER", "Dossier inconnu.", 404)
    applicant = frappe.get_doc("Admission Applicant", dossier_id)
    # W3/B0.5 (AUDIT-MANAGEMENT-BACK #4, ASVS 11.1.5) : garde d'état — miroir des règles
    # candidat. Avant : initiation possible sur dossier REF/DES, et création du frais 2
    # AVANT toute acceptation (incohérence argent/états).
    is_enrollment = str(fee_type).strip().lower() == "enrollment"
    allowed = ("ACC",) if is_enrollment else ("BRO", "SOP", "SOU")
    if applicant.status not in allowed:
        return _error(
            "INVALID_STATE",
            f"Initiation {'frais 2' if is_enrollment else 'frais 1'} possible seulement depuis {', '.join(allowed)}.",
            409,
        )
    if is_enrollment:
        fee = _ensure_enrollment_fee(applicant)
        if not fee:
            return _error("FEE_NOT_AVAILABLE", "Frais d'inscription indisponible au catalogue.", 500)
        descriptor = prepare_enrollment_online_payment(applicant, fee, acompte_xof=acompte_xof, idempotency_key=idempotency_key)
    else:
        fee = _ensure_fee(applicant)
        descriptor = prepare_online_payment(applicant, fee, idempotency_key=idempotency_key)
    log_event("payment_online", "initiated_by_agent", dossier_id=dossier_id, fee_type=fee_type)
    return _ok(descriptor)


# ── LOT W : cycle de vie complet (AUDIT-MANAGEMENT-BACK #2/#3/#5/#7, arbitrages B0.x) ──

# États sortants vers DES — calés sur le Workflow (Withdraw, rôle Administratif).
WITHDRAW_STATES = ("BRO", "SOP", "SOU", "ETU", "ATT", "ADM", "ACO", "ACC")

# W4 (B0.3) : mapping de clôture — instruits non conclus → REF (motif générique notifié) ;
# jamais aboutis / place non confirmée → DES. INS/REF/DES intacts. La rétention existante
# (REF/DES) prend ensuite le relais : la clôture est LE déclencheur unique de conservation.
SESSION_CLOSE_MAP = {
    "BRO": "DES", "SOP": "DES", "INC": "DES", "ACC": "DES",
    "SOU": "REF", "ETU": "REF", "ATT": "REF", "ADM": "REF", "ACO": "REF",
}


def _reject_pending_payments(dossier_id):
    """AUDIT-RECETTE (F3) + D-CONF-01 (verrou 2) — passe en Rejected TOUS les Pending d'un dossier
    qui se clôt (DES/REF par withdraw ou close_session) : Cash/Bank ET **Online**. Verrou 1
    (_promote_payment) reste le GARANT (la réconciliation « Promoted late » re-promouvrait sinon un
    Rejected) ; ce rejet est l'hygiène qui ne laisse aucun Pending encaissable qui traîne.
    update_modified=False (fenêtres de rétention)."""
    for name in frappe.get_all(
        "Applicant Fee Payment",
        filters={"applicant": dossier_id, "payment_status": "Pending",
                 "payment_mode": ["in", ["Cash", "Bank", "Online"]]},
        pluck="name",
    ):
        frappe.db.set_value("Applicant Fee Payment", name, "payment_status", "Rejected",
                            update_modified=False)
        log_event("payment_offline", "rejected_on_closure", dossier_id=dossier_id, ref=name)


@frappe.whitelist()
def withdraw(dossier_id=None, motif=None):
    """W1 (B0.1) — désistement (→DES). Administratif (calé Workflow Withdraw), motif
    OBLIGATOIRE, candidat NOTIFIÉ (ton neutre — pas un refus). decided_by/date posés
    (acte sensible tracé). Transition par save() : rôle gardé → validate_workflow OK."""
    frappe.only_for(("Admission Administratif", "System Manager"))
    if not dossier_id or not frappe.db.exists("Admission Applicant", dossier_id):
        return _error("INVALID_DOSSIER", "Dossier inconnu.", 404)
    applicant = frappe.get_doc("Admission Applicant", dossier_id)
    if applicant.status not in WITHDRAW_STATES:
        return _error("INVALID_STATE",
                      f"Désistement possible depuis {', '.join(WITHDRAW_STATES)}.", 409)
    if not motif or not str(motif).strip():
        return _error("MOTIF_REQUIRED", "Le motif de désistement est obligatoire.", 400)
    applicant.motif_desistement = str(motif).strip()
    _stamp_decision(applicant)
    applicant.status = "DES"
    applicant.save(ignore_permissions=True)
    _reject_pending_payments(applicant.name)  # F3 : pas de Pending orphelin encaissable
    send_withdrawal_notification(applicant, applicant.motif_desistement)  # non-bloquant
    log_event("withdraw", "success", dossier_id=applicant.name)
    return _ok({"dossier_id": applicant.name, "status": "DES"})


@frappe.whitelist()
def set_waitlist_rank(dossier_id=None, rang=None):
    """W5 — édite le rang de liste d'attente (Responsable, dossier ATT). rang vide → effacé.
    Le rang devient OPPOSABLE : exposé (get_dossier/list_dossiers) et trié au cockpit."""
    frappe.only_for(("Admission Responsable", "System Manager"))
    if not dossier_id or not frappe.db.exists("Admission Applicant", dossier_id):
        return _error("INVALID_DOSSIER", "Dossier inconnu.", 404)
    applicant = frappe.get_doc("Admission Applicant", dossier_id)
    if applicant.status != "ATT":
        return _error("INVALID_STATE", "Rang éditable seulement en liste d'attente (ATT).", 409)
    if rang in (None, ""):
        applicant.rang_liste_attente = None
    else:
        try:
            value = int(rang)
            if value < 1:
                raise ValueError
            applicant.rang_liste_attente = value
        except (ValueError, TypeError):
            return _error("RANG_INVALID", "Rang de liste d'attente invalide (entier ≥ 1).", 400)
    applicant.save(ignore_permissions=True)
    log_event("set_waitlist_rank", "success", dossier_id=applicant.name, rang=str(rang))
    return _ok({"dossier_id": applicant.name, "rang": applicant.rang_liste_attente})


@frappe.whitelist()
def close_session(session=None, motif=None, dry_run=1):
    """W4 (B0.3/B0.4) — clôture de session par la DIRECTION.

    dry_run=1 (DÉFAUT) → PRÉVISUALISATION : comptes par bascule, AUCUNE écriture.
    Exécution réelle : ferme la session (is_open=0) puis bascule EN MASSE les dossiers
    non aboutis selon SESSION_CLOSE_MAP, avec notification candidat (REF → mail décision
    motivée ; DES → mail clôture neutre). Motif générique par défaut, surchargé possible.

    Transitions par db.set_value + Transition Log manuel (action « Session Close ») —
    pattern établi (resubmit/cascade) : geste de masse système déclenché par la Direction ;
    le Workflow ne porte pas ces sorties pour ce rôle et INC n'a aucune sortie. decided_by/
    decision_date + motif posés par dossier (trace individuelle complète). Idempotent :
    une session déjà clôturée sans dossier basculable renvoie des comptes à zéro.
    """
    from frappe.utils import cint
    from admission.admission.doctype.admission_applicant.admission_applicant import (
        write_transition_log,
    )

    frappe.only_for(("Admission Direction", "System Manager"))
    if not session or not frappe.db.exists("Admission Session", session):
        return _error("INVALID_SESSION", "Session inconnue.", 404)
    sess = frappe.get_doc("Admission Session", session)
    rows = frappe.get_all(
        "Admission Applicant",
        filters={"session": session, "anonymized": ("!=", 1),
                 "status": ["in", list(SESSION_CLOSE_MAP)]},
        fields=["name", "status"],
    )
    preview = {}
    for r in rows:
        target = SESSION_CLOSE_MAP[r.status]
        preview.setdefault(f"{r.status}→{target}", 0)
        preview[f"{r.status}→{target}"] += 1
    if cint(dry_run):
        return _ok({"session": session, "is_open": bool(sess.is_open), "dry_run": True,
                    "bascules": preview, "total": len(rows)})

    motif = (str(motif).strip() if motif and str(motif).strip()
             else f"Clôture de la session {sess.label or session} — candidature non aboutie.")
    if sess.is_open:
        frappe.db.set_value("Admission Session", session, "is_open", 0)
    done, failed = {"REF": 0, "DES": 0}, 0
    for r in rows:
        target = SESSION_CLOSE_MAP[r.status]
        try:
            values = {"status": target, "decided_by": frappe.session.user,
                      "decision_date": now_datetime()}
            values["motif_refus" if target == "REF" else "motif_desistement"] = motif
            frappe.db.set_value("Admission Applicant", r.name, values)
            _reject_pending_payments(r.name)  # F3 : clôture = plus rien d'encaissable
            write_transition_log(r.name, r.status, target, actor=frappe.session.user,
                                 source="staff_api", action="Session Close")
            applicant = frappe.get_doc("Admission Applicant", r.name)
            if target == "REF":
                send_decision_notification(applicant, "refusé", motif=motif)
            else:
                send_withdrawal_notification(applicant, motif)
            done[target] += 1
        except Exception:
            failed += 1
            frappe.logger("staff").warning(
                f"Session close failed for {r.name}: {frappe.get_traceback()}")
    frappe.db.commit()
    log_event("close_session", "success", session=session,
              refused=done["REF"], withdrawn=done["DES"], failed=failed)
    return _ok({"session": session, "is_open": False, "dry_run": False,
                "refuses": done["REF"], "desistes": done["DES"], "echecs": failed,
                "bascules": preview, "total": len(rows)})
