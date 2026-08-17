"""LEGAL-HYGIENE (B1/B2) — corrige les Admission Legal Document : KkiaPay → FedaPay (FEDAPAY SA),
et CGV « acceptés » → « proposés » (juste que le drapeau paiement soit à 0 ou 1).

Versionnement natif du doctype : on N'ÉCRASE PAS. Pour chaque type, on lit la version ACTIVE, on
applique la substitution EXACTE (fail-closed : la chaîne attendue DOIT être présente), et on INSÈRE
une nouvelle version `is_active=1` — `validate()` recalcule `content_hash` et désactive l'ancienne,
qui reste en base : les consentements déjà recueillis pointent son hash et gardent leur preuve.

Identité (confirmée, canal marchand) : FEDAPAY SA, Ste Rita, Quartier Tonato, 8ᵉ arr., Cotonou, Bénin
(RCCM RB/COT/19B24720, IFU 3201910819942). Entité béninoise → « hors champ des transferts » préservé.

Idempotent : si la version active ne contient plus « KkiaPay », le type est déjà migré → skip.
"""
import frappe

NEW_VERSION = "1.1"

# Substitutions EXACTES par type (relues sur le contenu en base — LEGAL-2026-00001/2/4).
SUBS = {
    "CGV": [
        ("moyens de paiement acceptés", "moyens de paiement proposés"),   # B2
        ("du prestataire KkiaPay", "du prestataire FedaPay"),             # B1
    ],
    "PRIVACY_POLICY": [
        ("| KkiaPay | Paiement | Bénin |", "| FedaPay | Paiement | Bénin |"),
    ],
    "DATA_TRANSFER_CONSENT": [
        ("| KkiaPay | Cotonou |",
         "| FedaPay (FEDAPAY SA) | Ste Rita, Quartier Tonato, 8ᵉ arr., Cotonou |"),
    ],
}


def execute():
    for doc_type, subs in SUBS.items():
        names = frappe.get_all(
            "Admission Legal Document",
            filters={"document_type": doc_type, "is_active": 1}, pluck="name")
        if not names:
            frappe.logger("legal").error(
                f"[legal-fedapay] aucune version active pour {doc_type} — rien à migrer")
            continue
        active = frappe.get_doc("Admission Legal Document", names[0])
        text = active.content_text or ""
        if "KkiaPay" not in text:
            # déjà migré (idempotence) — ne pas empiler une version identique
            continue
        for old, new in subs:
            if old not in text:
                # fail-closed : on ne devine pas un contenu légal
                frappe.throw(
                    f"[legal-fedapay] chaîne attendue absente dans {doc_type} ({active.name}) : {old!r}")
            text = text.replace(old, new)
        # Le doctype REFUSE deux versions actives du même type (validate() throw). On désactive
        # d'abord l'ancienne — écriture directe, sans toucher son content_hash : elle RESTE en base,
        # les consentements déjà recueillis pointent son hash et gardent leur preuve.
        frappe.db.set_value("Admission Legal Document", active.name, "is_active", 0)
        frappe.get_doc({
            "doctype": "Admission Legal Document",
            "document_type": doc_type,
            "version": NEW_VERSION,
            "effective_date": frappe.utils.today(),
            "is_active": 1,                         # validate() désactive l'ancienne + recalcule le hash
            "content_text": text,
            "legal_references": active.get("legal_references"),
        }).insert(ignore_permissions=True)
        frappe.logger("legal").info(
            f"[legal-fedapay] {doc_type} : nouvelle version {NEW_VERSION} active (KkiaPay→FedaPay)")
