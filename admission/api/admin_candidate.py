"""LOT B (SM BACK-OFFICE) — support candidat (déblocage).

Gestes de support réservés au super-admin, ABSENTS aujourd'hui (un candidat bloqué était
dans une impasse jusqu'à expiration des compteurs) :
 - clear_candidate_throttle : purge les compteurs de rate-limit (`rl:`) d'un dossier ;
 - reissue_candidate_access : rotation du token + re-OTP, lien de reprise ENVOYÉ AU CANDIDAT
   (jamais affiché au staff — même anti-fuite que recover_dossier) ;
 - rectify_candidate_pii : rectification PII sur demande (RGPD), champs en liste blanche.

Anti-abus : garde SM, motif OBLIGATOIRE, `log_event` (ref non-PII = dossier_id, jamais les
valeurs PII), reissue n'expose RIEN à l'écran. Réf : SPEC-ADMISSION-SM-BACKOFFICE §4 (B).
"""

import frappe
from frappe.utils import add_days, now_datetime

from admission.api._log import log_event
from admission.api.public import (
    TOKEN_TTL_DAYS,
    _error,
    _generate_token,
    _hash,
    _ok,
)

SM_ROLES = ("Admission SM", "System Manager")
# Champs PII rectifiables sur demande (identité only — pas de champ d'état/argent).
RECTIFIABLE = {"first_name", "last_name", "email", "phone"}


def _purge_rl(needle):
    """Purge les clés de rate-limit Redis contenant `needle` (dossier_id ou e-mail).

    La clé est `rl:{cmd}:{ip}:{needle}` — l'IP candidat est inconnue, d'où un glob Redis
    (`*needle`). KEYS est O(N) sur le keyspace : acceptable pour un geste SM rare sur un site
    de petite taille. Renvoie le nombre de clés supprimées."""
    cache = frappe.cache
    pattern = cache.make_key(f"rl:*{needle}")
    matched = cache.keys(pattern)
    if matched:
        cache.delete_value(matched, make_keys=False)
    return len(matched or [])


def _guard_active(dossier_id):
    """Dossier existant et non anonymisé, sinon _error. Renvoie (doc, None) ou (None, _error)."""
    if not dossier_id or not frappe.db.exists("Admission Applicant", dossier_id):
        return None, _error("INVALID_DOSSIER", "Dossier inconnu.", 404)
    doc = frappe.get_doc("Admission Applicant", dossier_id)
    if doc.anonymized:
        return None, _error("DOSSIER_ANONYMIZED", "Dossier anonymisé : aucune action de support.", 409)
    return doc, None


@frappe.whitelist()
def clear_candidate_throttle(dossier_id=None, motif=None):
    """Purge les compteurs de rate-limit d'un dossier (OTP/token bloqués). Motif obligatoire."""
    frappe.only_for(SM_ROLES)
    doc, err = _guard_active(dossier_id)
    if err:
        return err
    if not motif or not str(motif).strip():
        return _error("MOTIF_REQUIRED", "Le motif est obligatoire.", 400)
    cleared = _purge_rl(doc.name)
    if doc.email:  # recover_dossier est limité par e-mail (pas par dossier_id)
        cleared += _purge_rl(doc.email.strip().lower())
    log_event("admin_clear_throttle", "success", dossier_id=doc.name,
              cleared=cleared, motif=str(motif).strip()[:140])
    return _ok({"dossier_id": doc.name, "throttle_keys_cleared": cleared})


@frappe.whitelist()
def reissue_candidate_access(dossier_id=None, motif=None):
    """Rotation token + re-OTP ; lien de reprise ENVOYÉ AU CANDIDAT (jamais retourné au staff).

    Miroir staff de recover_dossier : l'ancien lien meurt, la double barrière (OTP) se ré-arme.
    Purge aussi le throttle pour que le candidat puisse re-demander un OTP. Motif obligatoire."""
    frappe.only_for(SM_ROLES)
    doc, err = _guard_active(dossier_id)
    if err:
        return err
    if not motif or not str(motif).strip():
        return _error("MOTIF_REQUIRED", "Le motif est obligatoire.", 400)
    new_token = _generate_token()
    doc.dossier_token_hash = _hash(new_token)
    doc.token_expires_at = add_days(now_datetime(), TOKEN_TTL_DAYS)
    doc.otp_verified = 0  # nouvelle session → re-vérification OTP (double barrière, SEC-4)
    doc.save(ignore_permissions=True)
    _purge_rl(doc.name)
    if doc.email:
        _purge_rl(doc.email.strip().lower())
    frappe.db.commit()
    from admission.api.notifications import send_recovery_link
    send_recovery_link(doc, new_token)  # AU CANDIDAT — le token ne transite jamais par l'écran staff
    log_event("admin_reissue_access", "success", dossier_id=doc.name, motif=str(motif).strip()[:140])
    return _ok({"dossier_id": doc.name, "reissued": True})  # AUCUN token dans la réponse


@frappe.whitelist()
def rectify_candidate_pii(dossier_id=None, fields=None, motif=None):
    """Rectification PII sur demande (RGPD). `fields` = dict {champ: valeur} ⊆ liste blanche
    identité. Motif obligatoire. Le log ne contient JAMAIS les valeurs (seulement les champs)."""
    frappe.only_for(SM_ROLES)
    doc, err = _guard_active(dossier_id)
    if err:
        return err
    if not motif or not str(motif).strip():
        return _error("MOTIF_REQUIRED", "Le motif est obligatoire.", 400)
    if isinstance(fields, str):
        import json
        try:
            fields = json.loads(fields)
        except (ValueError, TypeError):
            return _error("FIELDS_INVALID", "Format de champs invalide (objet JSON attendu).", 400)
    if not isinstance(fields, dict) or not fields:
        return _error("FIELDS_REQUIRED", "Aucun champ à rectifier.", 400)
    illegal = [k for k in fields if k not in RECTIFIABLE]
    if illegal:
        return _error("FIELD_NOT_ALLOWED",
                      f"Champs non rectifiables : {', '.join(illegal)}. "
                      f"Autorisés : {', '.join(sorted(RECTIFIABLE))}.", 400)
    for key, value in fields.items():
        doc.set(key, (str(value).strip() if value is not None else None))
    if "first_name" in fields or "last_name" in fields:
        doc.applicant_name = " ".join(filter(None, [doc.first_name, doc.last_name])).strip()
    doc.save(ignore_permissions=True)  # validate (format e-mail, etc.) s'applique
    log_event("admin_rectify_pii", "success", dossier_id=doc.name,
              fields=",".join(sorted(fields)), motif=str(motif).strip()[:140])
    return _ok({"dossier_id": doc.name, "rectified": sorted(fields)})
