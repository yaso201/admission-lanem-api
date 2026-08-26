"""Émetteur d'identité admission → registre campus (ADM-1, DEC-AUTH-27).

Admission est le **2ᵉ guichet** : un candidat qui valide son OTP obtient un
`person_id` réel du registre campus (fin de la forge `PERS-REC-*`). Contrainte
FONDATRICE (DEC-AUTH-27 / ADR-003 INV-3) : le **dépôt d'un dossier ne bloque JAMAIS**
sur la disponibilité du registre. La résolution d'identité est donc **async/outbox,
POST-OTP** — jamais synchrone au dépôt.

Deux assertions distinctes, même transport outbox :
  1. **ensure_person** (POST-OTP) → résout le `person_id` réel (ADM-1) ;
  2. **add student** (À L'INS — inscription définitive, frais payés ; ruling ADM-2) →
     matérialise le badge student **natif campus** (source=campus, is_copy=0 via
     l'exception typée DEC-AUTH-26). Un admis non-inscrit (ACC) n'a PAS de badge, donc
     PAS d'accès. L'inscription (staff.enroll ACC→INS) **aboutit même campus DOWN** :
     l'assertion est enfilée, l'INS n'attend jamais le registre.

Transport = instance parallèle du patron outbox F-ADM-INS-01 (`bridge.py`) :
`frappe.enqueue(retry=3)` + flags sur l'Applicant (`person_resolved`/
`person_review_queued`/`identity_last_error`) + redrive quotidien. Enveloppe §4 +
POST X-API-Key : patron RH-01 (`benin_hr/api/identity_emitter.py`), cible whitelistée
`receive_identity_event`.

Idempotence :
  - `event_id` déterministe par Applicant → rejeu = no-op côté campus.
  - **DEC-323** (N dossiers même identité → 1 person_id) : `ensure_person` REFUSE le
    match email-seul (DEC-AUTH-02, « deux humains peuvent partager un email »). La
    corrélation se fait donc CÔTÉ ADMISSION (un Applicant antérieur au même email
    normalisé déjà résolu → son person_id) et est passée en **hint `custom_person_id`**
    → match confiant campus.

Outcomes :
  - `created`/`matched` → backfill person_id sur l'Applicant **ET ses Applicant Fees**
    (zéro fee orphelin — intégrité financière) ;
  - `review_queued` → TERMINAL EN ATTENTE (email collisionnant une Person d'un autre
    domaine → revue humaine campus). person_id reste NULL, la copie locale suffit
    (INV-3), signalé (`D-ADM-REVIEW-01`), **jamais rejoué** ;
  - 4xx récepteur → permanent, fail-fast (pas de retry) ; 5xx/réseau → retry (enqueue).

Ref : CONTRAT-AUTH-O2-v1.1 §4/§6 ; DEC-AUTH-26/27 ; DEC-322 (DOB) ; DEC-323 ; recon b9c4ca9.
"""

from __future__ import annotations

import json
import re

import frappe
import requests
from frappe.utils import now_datetime

from admission.api._config import _get_campus_config, _pii_transport_allowed
from admission.api._log import log_event

IDENTITY_ROUTE = "/api/method/portal_app.api.identity.inbound.receive_identity_event"
SOURCE = "admission"
STUDENT_AFFILIATION = "student"
# Rôle campus demandé pour le badge student. C'est un role_grant §4 (la source DEMANDE ;
# l'allowlist SEC-1 côté campus est le PLAFOND — ROLE_ALLOWLIST["student"]={"Student"} le
# confirme). Sans role_grant, provision_user n'accorde AUCUN rôle (allowlist = plafond, pas
# défaut) → compte connectable mais sans accès étudiant. Preuve runtime : has_student_role.
STUDENT_ROLE = "Student"
# person_id canonique du registre campus (autoname PERS-.#####). On refuse tout autre
# format (jamais stocker un id campus brut hors-forme — casserait le rapprochement).
PERSON_ID_PATTERN = re.compile(r"^PERS-\d{5,}$")


class IdentityReceiverError(Exception):
    """Erreur MÉTIER/permanente du récepteur (4xx : jeton/scope/autorité, malformé) ou
    exception applicative dans le corps. NON transitoire → fail-fast, aucun retry."""

    def __init__(self, message, status_code=None):
        super().__init__(message)
        self.status_code = status_code


# ---------------------------------------------------------------------------
# Déclenchement (post-OTP, non-bloquant)
# ---------------------------------------------------------------------------


def enqueue_identity_assertion(applicant_name):
    """POST-OTP, NON-BLOQUANT (DEC-27). Enfile l'assertion d'identité (retry=3).
    Appelé aux 2 chemins `otp_verified=1` (verify_otp + claim_recovered_dossier).
    Idempotent : `_send` no-op si déjà résolu / en revue."""
    frappe.enqueue(
        _send_identity_assertion,
        queue="default",
        applicant_name=applicant_name,
        is_async=True,
        retry=3,
    )


# ---------------------------------------------------------------------------
# Corrélation DEC-323 + enveloppe §4
# ---------------------------------------------------------------------------


def _normalize_email(email):
    return (email or "").strip().lower()


def _correlate_person_id(applicant):
    """DEC-323 : un Applicant ANTÉRIEUR au même email normalisé, DÉJÀ résolu → son
    person_id (hint confiant). C'est admission qui affirme « même personne » ; campus
    ne le devine pas sur un email nu (DEC-AUTH-02)."""
    rows = frappe.db.sql(
        """SELECT person_id FROM `tabAdmission Applicant`
           WHERE LOWER(TRIM(email)) = %s AND COALESCE(person_id,'') <> '' AND name <> %s
           LIMIT 1""",
        (_normalize_email(applicant.email), applicant.name),
    )
    return rows[0][0] if rows else None


def _ensure_person_event_id(applicant):
    return f"{SOURCE}:ensure_person:{applicant.name}"


def build_ensure_person_envelope(applicant):
    """Enveloppe `ensure_person` (§4). matching {source, email} + hint custom_person_id
    (DEC-323) ; attributes portent la DOB (DEC-322, 3ᵉ critère de corrélation)."""
    matching = {"source": SOURCE, "email": applicant.email}
    hint = _correlate_person_id(applicant)
    if hint:
        matching["custom_person_id"] = hint
    attributes = {"first_name": applicant.first_name, "last_name": applicant.last_name}
    if getattr(applicant, "date_of_birth", None):
        attributes["date_of_birth"] = str(applicant.date_of_birth)
    if getattr(applicant, "phone", None):
        attributes["primary_phone"] = applicant.phone
    return {
        "event_id": _ensure_person_event_id(applicant),
        "operation": "ensure_person",
        "source": SOURCE,
        "matching": matching,
        "attributes": attributes,
    }


# ---------------------------------------------------------------------------
# Orchestration outbox
# ---------------------------------------------------------------------------


def _send_identity_assertion(applicant_name):
    """Corps de l'outbox. Idempotent, non-bloquant, journalisé. Retour = dict statut."""
    if not frappe.db.exists("Admission Applicant", applicant_name):
        return {"status": "applicant_not_found", "applicant": applicant_name}
    applicant = frappe.get_doc("Admission Applicant", applicant_name)

    # Idempotence : déjà résolu (person_id posé) ou en revue → no-op terminal.
    if applicant.person_id or getattr(applicant, "person_resolved", 0):
        return {"status": "already_resolved", "applicant": applicant_name}
    if getattr(applicant, "person_review_queued", 0):
        return {"status": "review_pending", "applicant": applicant_name}

    config = _get_campus_config()
    if not config:
        # Registre non branché (dev/recette) : dépôt déjà abouti, assertion différée.
        # Pas un échec, pas de retry-storm — le redrive quotidien reprendra.
        log_event("identity_assert", "deferred_no_config", dossier_id=applicant_name, level="warning")
        return {"status": "deferred_configuration", "applicant": applicant_name}

    # DAT-2 : l'enveloppe porte de la PII (email/dob/nom) → garde de transport (pas de
    # PII sur un canal non autorisé). Même garde que bridge.py. Blocage → différé (redrive).
    if not _pii_transport_allowed(config["url"], context="identity_assert→campus"):
        log_event("identity_assert", "pii_transport_blocked", dossier_id=applicant_name, level="error")
        return {"status": "pii_transport_blocked", "applicant": applicant_name}

    try:
        result = _post_identity_event(build_ensure_person_envelope(applicant), config)
    except IdentityReceiverError as exc:
        # 4xx / erreur métier = PERMANENT → fail-fast (marque, PAS de raise = pas de retry).
        _mark_identity_error(applicant_name, f"récepteur: {exc}")
        log_event("identity_assert", "receiver_error", dossier_id=applicant_name,
                  error=str(exc), level="error", alert_type="identity_assert")
        return {"status": "receiver_error", "applicant": applicant_name}
    except requests.RequestException as exc:
        # Réseau / 5xx = TRANSITOIRE → marque + raise (enqueue retry, puis redrive).
        _mark_identity_error(applicant_name, f"réseau: {exc}")
        log_event("identity_assert", "failed", dossier_id=applicant_name,
                  error=str(exc), level="error", alert_type="identity_assert")
        raise

    outcome = (result or {}).get("outcome")
    person_id = (result or {}).get("person_id")

    if outcome in ("created", "matched") and person_id:
        person_id = str(person_id).strip().upper()
        if not PERSON_ID_PATTERN.match(person_id):
            _mark_identity_error(applicant_name, f"person_id campus hors format: {person_id}")
            log_event("identity_assert", "bad_person_id", dossier_id=applicant_name,
                      error=person_id, level="error", alert_type="identity_assert")
            return {"status": "bad_person_id", "applicant": applicant_name}
        _backfill_person_id(applicant_name, person_id)
        return {"status": "resolved", "applicant": applicant_name, "person_id": person_id}

    if outcome == "review_queued":
        # D-ADM-REVIEW-01 : email collisionnant un autre domaine → revue humaine campus.
        # TERMINAL EN ATTENTE : person_id NULL, copie locale suffit (INV-3), signalé, 0 retry.
        frappe.db.set_value("Admission Applicant", applicant_name,
                            {"person_review_queued": 1, "identity_last_error": None},
                            update_modified=False)
        frappe.db.commit()
        log_event("identity_assert", "review_queued", dossier_id=applicant_name,
                  level="warning", alert_type="identity_review")  # signal visible (pas un NULL perdu)
        return {"status": "review_queued", "applicant": applicant_name}

    # Outcome inattendu (ni created/matched+person_id, ni review) → anomalie transitoire.
    _mark_identity_error(applicant_name, f"outcome inattendu={outcome!r}")
    log_event("identity_assert", "unexpected_outcome", dossier_id=applicant_name,
              error=str(outcome), level="error", alert_type="identity_assert")
    raise IdentityReceiverError(f"outcome inattendu du récepteur: {outcome!r}")


def _backfill_person_id(applicant_name, person_id):
    """Accusé `person.resolved` : pose le person_id réel sur l'Applicant ET **tous ses
    Applicant Fees** (DEC-27, ZÉRO fee orphelin — un paiement non rattaché à une
    identité est un cauchemar de réconciliation). `update_modified=False` : les fenêtres
    de rétention reposent sur `modified`."""
    frappe.db.set_value("Admission Applicant", applicant_name,
                        {"person_id": person_id, "person_resolved": 1,
                         "person_resolved_at": now_datetime(), "identity_last_error": None},
                        update_modified=False)
    fees = frappe.get_all("Applicant Fee", filters={"applicant": applicant_name}, pluck="name")
    for fee in fees:
        frappe.db.set_value("Applicant Fee", fee, "person_id", person_id, update_modified=False)
    frappe.db.commit()
    log_event("identity_assert", "resolved", dossier_id=applicant_name,
              person_id=person_id, fees_backfilled=len(fees))


def _mark_identity_error(applicant_name, error):
    frappe.db.set_value("Admission Applicant", applicant_name,
                        "identity_last_error", str(error)[:500], update_modified=False)
    frappe.db.commit()


# ---------------------------------------------------------------------------
# Badge student natif (ADM-2, DEC-273 / DEC-AUTH-26) — assertion `add student` À L'INS
# ---------------------------------------------------------------------------


def enqueue_student_badge_assertion(applicant_name):
    """À l'INS (inscription définitive, frais payés — ruling ADM-2), NON-BLOQUANT.
    Enfile l'assertion du badge student natif (retry=3). Idempotent : `_send` no-op si
    déjà affirmé ou identité non résolue. L'INS n'attend jamais le registre (campus DOWN
    → assertion enfilée, reprise par le redrive)."""
    frappe.enqueue(
        _send_student_badge_assertion,
        queue="default",
        applicant_name=applicant_name,
        is_async=True,
        retry=3,
    )


def _student_badge_event_id(applicant):
    return f"{SOURCE}:add_student:{applicant.name}"


def build_student_badge_envelope(applicant):
    """Enveloppe §4 `add student`. La source DÉCLARÉE est `admission` (l'autorité du
    client — `authorize_inbound` SEC-2 : admission est autoritaire pour `student`) ; le
    campus MATÉRIALISE le badge natif (source=campus, is_copy=0) via l'exception typée
    DEC-AUTH-26. AUCUNE PII : person_id (identité déjà résolue) + entity_ref=dossier_id."""
    return {
        "event_id": _student_badge_event_id(applicant),
        "operation": "add",
        "source": SOURCE,
        "person_id": applicant.person_id,
        "affiliation_type": STUDENT_AFFILIATION,
        "status": "active",
        # role_grant §4 : DEMANDE le rôle Student (filtré par l'allowlist SEC-1 campus).
        "payload": {"entity_ref": applicant.name, "role_grant": STUDENT_ROLE},
    }


def _send_student_badge_assertion(applicant_name):
    """Corps de l'outbox badge. Idempotent, non-bloquant, journalisé. Retour = dict statut."""
    if not frappe.db.exists("Admission Applicant", applicant_name):
        return {"status": "applicant_not_found", "applicant": applicant_name}
    applicant = frappe.get_doc("Admission Applicant", applicant_name)

    # Idempotence : badge déjà affirmé → no-op terminal (pas de nouvel appel réseau).
    if getattr(applicant, "student_badge_asserted", 0):
        return {"status": "already_asserted", "applicant": applicant_name}

    # person_id REQUIS : le badge s'ancre à l'identité résolue (post-OTP, ADM-1). Non
    # résolu (review_queued terminal, ou retard) → on n'émet PAS de badge sans identité ;
    # le redrive reprendra quand person_id sera posé. Signalé (jamais un NULL perdu).
    if not applicant.person_id:
        log_event("student_badge", "no_person_id", dossier_id=applicant_name,
                  level="warning", alert_type="student_badge")
        return {"status": "no_person_id", "applicant": applicant_name}

    config = _get_campus_config()
    if not config:
        # Registre non branché (dev/recette) : INS déjà aboutie, assertion différée.
        log_event("student_badge", "deferred_no_config", dossier_id=applicant_name, level="warning")
        return {"status": "deferred_configuration", "applicant": applicant_name}

    # L'enveloppe ne porte AUCUNE PII (person_id + dossier_id) — pas de garde DAT-2 requise.
    try:
        badge = _post_identity_event(build_student_badge_envelope(applicant), config)
    except IdentityReceiverError as exc:
        # 4xx / erreur métier = PERMANENT → fail-fast (marque, PAS de raise = pas de retry).
        _mark_student_badge_error(applicant_name, f"récepteur: {exc}")
        log_event("student_badge", "receiver_error", dossier_id=applicant_name,
                  error=str(exc), level="error", alert_type="student_badge")
        return {"status": "receiver_error", "applicant": applicant_name}
    except requests.RequestException as exc:
        # Réseau / 5xx = TRANSITOIRE → marque + raise (enqueue retry, puis redrive).
        _mark_student_badge_error(applicant_name, f"réseau: {exc}")
        log_event("student_badge", "failed", dossier_id=applicant_name,
                  error=str(exc), level="error", alert_type="student_badge")
        raise

    # Défense en profondeur : le récepteur DOIT matérialiser natif (is_copy=0). Une copie
    # (is_copy=1) = échec de gate côté campus → SIGNALÉ (jamais silencieux), pas un raise
    # (le badge existe, l'anomalie est de réconciliation, pas de transport).
    if isinstance(badge, dict) and badge.get("is_copy") not in (0, None):
        log_event("student_badge", "not_native", dossier_id=applicant_name,
                  level="error", alert_type="student_badge")

    _mark_student_badge_asserted(applicant_name)
    return {"status": "asserted", "applicant": applicant_name, "person_id": applicant.person_id}


def _mark_student_badge_asserted(applicant_name):
    frappe.db.set_value("Admission Applicant", applicant_name,
                        {"student_badge_asserted": 1, "student_badge_asserted_at": now_datetime(),
                         "student_badge_error": None}, update_modified=False)
    frappe.db.commit()
    log_event("student_badge", "asserted", dossier_id=applicant_name)


def _mark_student_badge_error(applicant_name, error):
    frappe.db.set_value("Admission Applicant", applicant_name,
                        "student_badge_error", str(error)[:500], update_modified=False)
    frappe.db.commit()


# ---------------------------------------------------------------------------
# Transport (X-API-Key, patron RH-01) — un seul POST ; le retry est porté par enqueue
# ---------------------------------------------------------------------------


def _post_identity_event(envelope, config):
    """POST l'enveloppe §4 sous le jeton de service scopé `admission` (X-API-Key —
    JAMAIS `Authorization: Token`, qui collisionne l'auth native Frappe, V-RH01-02).
    Un seul essai : 4xx → IdentityReceiverError (permanent) ; 5xx/réseau → RequestException
    (transitoire, le retry est porté par `frappe.enqueue(retry=3)` + redrive)."""
    url = config["url"].rstrip("/") + IDENTITY_ROUTE
    headers = {"X-API-Key": config["token"], "Content-Type": "application/json"}
    body = {"payload": json.dumps(envelope, ensure_ascii=False)}

    resp = requests.post(url, json=body, headers=headers, timeout=15)
    if 400 <= resp.status_code < 500:
        raise IdentityReceiverError(f"{resp.status_code}: {_error_detail(resp)}", resp.status_code)
    resp.raise_for_status()  # 5xx → HTTPError (RequestException) → retry

    data = resp.json()
    if isinstance(data, dict) and data.get("exc"):
        raise IdentityReceiverError(str(data.get("exc")))
    message = data.get("message", data) if isinstance(data, dict) else data
    if isinstance(message, dict) and message.get("error") and message.get("status_code"):
        raise IdentityReceiverError(f"{message['status_code']}: {message['error']}",
                                    message.get("status_code"))
    return message


def _error_detail(response):
    """Message d'erreur lisible d'une réponse 4xx (sans exposer de secret)."""
    try:
        data = response.json()
    except Exception:
        return "(corps non-JSON)"
    message = data.get("message", data) if isinstance(data, dict) else data
    if isinstance(message, dict):
        return message.get("error") or message.get("exc") or str(message)[:200]
    return str(message)[:200]


# ---------------------------------------------------------------------------
# Redrive quotidien (reprend les assertions non résolues, hors revue)
# ---------------------------------------------------------------------------


def redrive_identity_assertions():
    """Reprise quotidienne (patron redrive_bridge) : ré-enfile les dossiers OTP-vérifiés,
    NON résolus et NON en revue (config absente au moment de l'OTP, panne réseau
    épuisée…). Idempotent (event_id) ; ne touche jamais un `review_queued`."""
    pending = frappe.get_all(
        "Admission Applicant",
        filters={"otp_verified": 1, "person_review_queued": 0,
                 "person_resolved": 0, "person_id": ["in", ["", None]]},
        pluck="name",
    )
    for name in pending:
        enqueue_identity_assertion(name)
    if pending:
        frappe.logger("identity_assert").info(f"redrive_identity_assertions: {len(pending)} ré-enfilés.")
    return {"redriven": len(pending)}


def redrive_student_badge_assertions():
    """Reprise quotidienne du badge student : ré-enfile les INSCRITS (status=INS) dont
    l'identité est résolue (person_id posé) mais dont le badge n'est PAS encore affirmé
    (config absente à l'INS, panne réseau épuisée, ou identité résolue APRÈS l'INS).
    Idempotent (event_id)."""
    pending = frappe.get_all(
        "Admission Applicant",
        filters={"status": "INS", "student_badge_asserted": 0, "person_id": ["is", "set"]},
        pluck="name",
    )
    for name in pending:
        enqueue_student_badge_assertion(name)
    if pending:
        frappe.logger("student_badge").info(f"redrive_student_badge_assertions: {len(pending)} ré-enfilés.")
    return {"redriven": len(pending)}
