"""OBS-2 — log applicatif structuré + corrélation dossier_id (non-PII).

log_event() émet un message JSON corrélable via frappe.logger("admission") :
{step, status, dossier_id|ref, ...champs}. Complète l'Error Log natif (OBS-1, log_error
pour les exceptions) — il ne le remplace pas. JAMAIS de PII en clair (cohérent DAT-1/DAT-2) :
n'y passer que des identifiants système (dossier_id CAN-…, person_id PERS-…,
idempotency_key, provider_reference), jamais nom/email/téléphone/IP brute.
"""

from __future__ import annotations

import json

import frappe


def log_event(step, status, *, dossier_id=None, ref=None, level="info", alert_type=None, **fields):
    """Émet un log applicatif structuré et corrélable.

    Args:
        step: étape du parcours (ex. "create_dossier", "verify_otp", "payment_online").
        status: statut (ex. "success", "failed", "skipped_no_config", "blocked", "no_person_id").
        dossier_id: docname Admission Applicant (CAN-AAAA-NNNNN), clé de corrélation non-PII.
        ref: clé non-PII alternative (idempotency_key / provider_reference) quand dossier_id
             n'existe pas encore (pré-insert) ou hors contexte dossier (webhook).
        level: "info" | "warning" | "error" (défaut "info").
        alert_type: OBS-2 — marque un point HIGH curé : route l'événement vers l'alerte
             temps réel (Telegram) EN PLUS du log. Réservé à l'ensemble curé (~8 types) —
             ne pas poser sur tout (garantie anti-volume, le reste va au digest).
        **fields: champs additionnels NON-PII uniquement.

    Non-bloquant : un échec de logging ne doit jamais interrompre le flux métier.
    """
    payload = {"step": step, "status": status}
    if dossier_id:
        payload["dossier_id"] = dossier_id
    if ref:
        payload["ref"] = ref
    if fields:
        payload.update(fields)
    try:
        logger = frappe.logger("admission")
        getattr(logger, level, logger.info)(json.dumps(payload, ensure_ascii=False, default=str))
    except Exception:
        # Le logging ne casse jamais le métier (cohérent OBS-1 : non-bloquant).
        pass
    if alert_type:
        # OBS-2 : import paresseux (acyclique — alerting n'importe jamais _log) ; allowlist
        # STRICTE dossier_id/ref (identifiants internes) — le texte libre (error, fields)
        # n'est JAMAIS transmis à l'alerte (anti-PII). L'échec d'alerte ne casse rien.
        try:
            from admission.api.alerting import send_high_alert
            send_high_alert(alert_type, dossier_id=dossier_id, ref=ref)
        except Exception:
            pass
