"""Numérotation structurée des dossiers et reçus (formats validés 15/06/2026).

DOSSIER (Admission Applicant) : `XXXX YYY NNNN`
  XXXX = année académique (session.academic_year "2026-2027" -> "2627")
  YYY  = famille (parcours) + index AUTO dans la famille, persisté sur
         Admission Programme.numbering_code (stable à vie ; les nouveaux programmes
         prennent l'index suivant SANS intervention).
  NNNN = compteur par (année + formation).
  Ex. 1ʳᵉ candidature LIS 2026-2027 -> 2627 201 0001 = "26272010001".

REÇU (Applicant Fee Payment) : `XX AA NNNNN`
  XX    = année civile du paiement (2 chiffres).
  AA    = source (1=admission) + canal (Online=1, Bank=2, Cash=3).
  NNNNN = compteur par (année + AA).
  Ex. 1er paiement en ligne -> 26 11 00001 = "261100001".

`make_autoname` gère le compteur par préfixe (le ".####" est un délimiteur consommé,
pas un littéral) : "2627201.####" -> "26272010001", série "2627201".
"""

import re

import frappe
from frappe.model.naming import make_autoname
from frappe.utils import getdate, nowdate

FAMILY_BY_PARCOURS = {"Prépa": 1, "Licence": 2, "Bachelor": 3, "Double-Diplomation": 4}
CANAL_BY_MODE = {"Online": "1", "Bank": "2", "Cash": "3"}
SOURCE_ADMISSION = "1"


def _current_academic_code():
    """Repli quand la session est absente : année civile -> AAAA (AA + AA+1)."""
    y = getdate(nowdate()).year
    return f"{y % 100:02d}{(y + 1) % 100:02d}"


def academic_year_code(session_name):
    """'2026-2027' -> '2627'. Tolère séparateurs variés et années 2/4 chiffres."""
    ay = frappe.db.get_value("Admission Session", session_name, "academic_year") if session_name else None
    parts = [p for p in re.split(r"[^0-9]+", (ay or "").strip()) if p]
    if len(parts) >= 2:
        return parts[0][-2:] + parts[1][-2:]
    if len(parts) == 1 and len(parts[0]) >= 4:
        y = int(parts[0][:4])
        return f"{y % 100:02d}{(y + 1) % 100:02d}"
    return _current_academic_code()


def assign_programme_numbering_code(doc):
    """Auto-assigne numbering_code (YYY) si absent : famille(parcours) + index suivant
    DANS la famille (max+1). Persisté → stable à vie. Idempotent (no-op si déjà posé)."""
    if doc.get("numbering_code"):
        return
    family = FAMILY_BY_PARCOURS.get(doc.parcours)
    if not family:
        return  # parcours inconnu : pas de code (dossier prendra YYY=000 en repli)
    prefix = str(family)
    existing = frappe.get_all(
        "Admission Programme",
        filters={"numbering_code": ["like", f"{prefix}__"]},
        pluck="numbering_code",
    )
    indices = [int(c[1:]) for c in existing if c and c[0] == prefix and c[1:].isdigit()]
    nxt = (max(indices) + 1) if indices else 1
    doc.numbering_code = f"{family}{nxt:02d}"


def build_dossier_name(applicant):
    """XXXX + YYY + compteur(4) par (année+formation)."""
    xxxx = academic_year_code(applicant.get("session"))
    yyy = (frappe.db.get_value("Admission Programme", applicant.programme_code, "numbering_code")
           if applicant.get("programme_code") else None) or "000"
    return make_autoname(f"{xxxx}{yyy}.####")


def build_receipt_name(payment):
    """XX + AA(source+canal) + compteur(5) par (année+AA)."""
    xx = f"{getdate(nowdate()).year % 100:02d}"
    canal = CANAL_BY_MODE.get(payment.get("payment_mode"), "0")
    aa = SOURCE_ADMISSION + canal
    return make_autoname(f"{xx}{aa}.#####")
