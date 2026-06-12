"""ADM-LEG — Legal infrastructure helpers.

Consent recording, legal document retrieval, and seed placeholders.
All consent records are IMMUTABLE after creation.

Ref: ADM-LEG, loi 2017-20 (art. 28/29/36), DEC-222.
"""

from __future__ import annotations

import hashlib

import frappe
from frappe.utils import now_datetime, today


SIMULATION_DISCLAIMER_FALLBACK = (
    "Estimation INDICATIVE — non garantie. "
    "Sous réserve de validation de la bourse par la Direction. "
    "Le montant réel reste le plein tarif tant qu'aucune bourse n'est validée. "
    "Aucun engagement contractuel."
)

CONSENT_TYPE_TO_DOCUMENT_TYPE = {
    "DATA_PROCESSING": "PRIVACY_POLICY",
    "CGV": "CGV",
    "REFUND_ACKNOWLEDGMENT": "REFUND_POLICY",
    "DATA_TRANSFER": "DATA_TRANSFER_CONSENT",
}


def _get_client_ip():
    # DAT-2 : source canonique Frappe (= session_ip / Activity Log), PAS le header XFF brut.
    # Un X-Forwarded-For forgé par le client n'altère plus l'IP enregistrée. La
    # non-spoofabilité reste une propriété OPS (reverse-proxy de confiance écrasant XFF) ;
    # cf. note ops DAT-2. Offline (pas de requête → request_ip non posé) → "".
    return getattr(frappe.local, "request_ip", None) or ""


def _get_user_agent():
    request = getattr(frappe, "request", None)
    if not request:
        return ""
    headers = getattr(request, "headers", {})
    if hasattr(headers, "get"):
        return (headers.get("User-Agent") or "")[:500]
    return ""


def _get_active_legal_document(document_type):
    names = frappe.get_all(
        "Admission Legal Document",
        filters={"document_type": document_type, "is_active": 1},
        pluck="name",
        limit=1,
    )
    if not names:
        return None
    return frappe.get_doc("Admission Legal Document", names[0])


def _record_consent(applicant_name, consent_type, legal_document_name,
                    client_ip=None, user_agent=None):
    doc = frappe.get_doc("Admission Legal Document", legal_document_name)
    record = frappe.get_doc(
        {
            "doctype": "Admission Consent Record",
            "applicant": applicant_name,
            "legal_document": legal_document_name,
            "consent_type": consent_type,
            "accepted_at": now_datetime(),
            "client_ip": client_ip or _get_client_ip(),
            "user_agent": user_agent or _get_user_agent(),
            "version_hash": doc.content_hash,
        }
    )
    record.insert(ignore_permissions=True)
    return record.name


def _has_consent(applicant_name, consent_type):
    return bool(
        frappe.db.exists(
            "Admission Consent Record",
            {"applicant": applicant_name, "consent_type": consent_type},
        )
    )


def _require_consent_record(applicant_name, consent_type):
    if not _has_consent(applicant_name, consent_type):
        frappe.throw(
            f"Consentement {consent_type} requis pour le dossier {applicant_name}. "
            f"Le candidat doit accepter avant de poursuivre."
        )


def _get_consent_proof(applicant_name, consent_type):
    record = frappe.get_all(
        "Admission Consent Record",
        filters={"applicant": applicant_name, "consent_type": consent_type},
        fields=["version_hash", "accepted_at"],
        order_by="accepted_at desc",
        limit=1,
    )
    if not record:
        return None
    return {
        "version_hash": record[0].version_hash,
        "accepted_at": str(record[0].accepted_at),
    }


def _get_versioned_disclaimer():
    doc = _get_active_legal_document("SIMULATION_DISCLAIMER")
    if doc:
        return doc.content_text, doc.content_hash
    return SIMULATION_DISCLAIMER_FALLBACK, None


def _get_active_legal_texts():
    docs = frappe.get_all(
        "Admission Legal Document",
        filters={"is_active": 1},
        fields=["document_type", "version", "content_text", "content_hash"],
        limit=200,
    )
    result = {}
    for d in docs:
        result[d.document_type.lower()] = {
            "type": d.document_type,
            "version": d.version,
            "content_text": d.content_text,
            "content_hash": d.content_hash,
        }
    return result


def _get_active_legal_texts_meta():
    """PERF-1 : version+hash des textes actifs (SANS content_text) → payload léger pour get_frais.

    Le content_text complet se charge à la demande via get_legal_documents (ADM-LEG). Caché ;
    invalidé on_update Admission Legal Document (delete_keys 'admission:').
    """
    cache = None
    try:
        cache = frappe.cache()
        wrapped = cache.get_value("admission:legal:active")
        if isinstance(wrapped, dict) and "v" in wrapped:
            return wrapped["v"]
    except Exception:
        cache = None
    docs = frappe.get_all(
        "Admission Legal Document",
        filters={"is_active": 1},
        fields=["document_type", "version", "content_hash"],
        limit=200,
    )
    result = {
        d.document_type.lower(): {
            "type": d.document_type,
            "version": d.version,
            "content_hash": d.content_hash,
        }
        for d in docs
    }
    if cache is not None:
        try:
            cache.set_value("admission:legal:active", {"v": result}, expires_in_sec=24 * 60 * 60)
        except Exception:
            pass
    return result


_LEGAL_PLACEHOLDERS = [
    {
        "document_type": "CGV",
        "version": "PLACEHOLDER-V0",
        "content_text": "[Conditions Generales de Vente — a remplir par le juriste (F10). "
                        "Ref: OHADA, loi beninoise 2007-21.]",
        "legal_references": "OHADA, loi 2007-21",
    },
    {
        "document_type": "PRIVACY_POLICY",
        "version": "PLACEHOLDER-V0",
        "content_text": "[Politique de confidentialite — a remplir par le juriste (F10). "
                        "Ref: loi 2017-20 art.28 (consentement), art.29 (tracabilite).]",
        "legal_references": "Loi 2017-20 art.28, art.29",
    },
    {
        "document_type": "REFUND_POLICY",
        "version": "PLACEHOLDER-V0",
        "content_text": "[Politique de remboursement — a remplir par le juriste (F10). "
                        "Les frais de candidature et d'inscription ne sont pas remboursables. "
                        "Ref: DEC-222.]",
        "legal_references": "DEC-222, loi 2007-21",
    },
    {
        "document_type": "DATA_TRANSFER_CONSENT",
        "version": "PLACEHOLDER-V0",
        "content_text": "[Consentement au transfert de donnees entre systemes — a remplir (F10). "
                        "Vos donnees seront transferees du systeme d'admission vers le systeme campus "
                        "pour finaliser votre inscription. "
                        "Ref: loi 2017-20 art.36.]",
        "legal_references": "Loi 2017-20 art.36",
    },
    {
        "document_type": "SIMULATION_DISCLAIMER",
        "version": "PLACEHOLDER-V0",
        "content_text": SIMULATION_DISCLAIMER_FALLBACK,
        "legal_references": "ADM-UF-4",
    },
]


def seed_legal_placeholders():
    for placeholder in _LEGAL_PLACEHOLDERS:
        existing = frappe.get_all(
            "Admission Legal Document",
            filters={"document_type": placeholder["document_type"]},
            pluck="name",
            limit=1,
        )
        if existing:
            continue

        content_hash = hashlib.sha256(
            placeholder["content_text"].encode("utf-8")
        ).hexdigest()

        doc = frappe.get_doc(
            {
                "doctype": "Admission Legal Document",
                "document_type": placeholder["document_type"],
                "version": placeholder["version"],
                "content_text": placeholder["content_text"],
                "content_hash": content_hash,
                "effective_date": today(),
                "is_active": 1,
                "legal_references": placeholder["legal_references"],
            }
        )
        doc.insert(ignore_permissions=True)

    frappe.db.commit()
