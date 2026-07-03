"""ENABLER-FIXTURES-E2E — constructeur de fixtures multi-états PAR CHEMIN MÉTIER.

Dérivé du pattern éprouvé `admission/scenario_recette.py` (registre CREATED + _purge_dossier +
cleanup), mais PARAMÉTRÉ par état cible et environment-agnostic (défauts recette, surchargeables
via variables d'environnement). Il ne modifie AUCUN comportement applicatif : c'est un outil de
preuve qui rend éprouvables les états post-SOU (inatteignables faute de données en recette).

PRINCIPE (DEC ratifiée) :
  · 100 % chemin métier (vraies API) pour tout état GARDÉ par un invariant — jamais de seed d'un
    2ᵉ Confirmed (index R3 `unique_confirmed_fee`), jamais de pièce `verified` sans verdict.
  · Décor : réutilise une session OPEN existante ; seed catalogue UNIQUEMENT s'il manque.
  · start_review (SOU→ETU) est gardé par PIECES_NON_VERIFIEES → le ladder ETU vérifie d'abord
    TOUTES les pièces requises effectives (verify_piece réel, par l'Administratif).

TAG : email `fixture-<runid>@e2e.lanem.test` (domaine factice → 0 envoi réel possible).
PURGE : par tag (robuste entre invocations) + Email Queue taggée + décor seedé s'il y a lieu.

Usage (server-side, jamais exec/tmp) :
  bench --site <site> execute admission.tests.fixtures.recette_fixtures.status_counts
  bench --site <site> execute admission.tests.fixtures.recette_fixtures.build_one --kwargs "{'target':'ETU'}"
  bench --site <site> execute admission.tests.fixtures.recette_fixtures.build_states
  bench --site <site> execute admission.tests.fixtures.recette_fixtures.purge

Config par env (défauts = recette) :
  ADMISSION_FIXTURE_BASE   (défaut https://api-admission-rec.lanem.bj)
  ADMISSION_FIXTURE_HOST   (en-tête Host si BASE=127.0.0.1:<port> ; sinon vide)
  ADMISSION_FIXTURE_SESSION / _LEVEL   (défaut SES-BACH-ASRC-2026 / BACH-ASRC-B1, non-Prépa)
  ADMISSION_FIXTURE_ADMIN / _RESP / _DIR   (comptes staff par rôle)
"""

import email as email_mod
import os
import re
from collections import Counter

import frappe
import requests as _requests

TAG_DOMAIN = "e2e.lanem.test"
BASE = os.environ.get("ADMISSION_FIXTURE_BASE", "https://api-admission-rec.lanem.bj").rstrip("/") \
    + "/api/method/admission.api.public."
SESSION = os.environ.get("ADMISSION_FIXTURE_SESSION", "SES-BACH-ASRC-2026")
LEVEL = os.environ.get("ADMISSION_FIXTURE_LEVEL", "BACH-ASRC-B1")
STAFF = {
    "admin": os.environ.get("ADMISSION_FIXTURE_ADMIN", "admin.admissions@lanem.bj"),
    "resp": os.environ.get("ADMISSION_FIXTURE_RESP", "resp.recette@lanem.bj"),
    "dir": os.environ.get("ADMISSION_FIXTURE_DIR", "direction.recette@lanem.bj"),
}

LADDER = ("BRO", "SOP", "SOU", "ETU", "ADM", "ACC", "INS")  # +branche REF depuis ETU

CREATED = []  # dossiers enregistrés DÈS création → purge garantie même sur abort

http = _requests.Session()
_HOST = os.environ.get("ADMISSION_FIXTURE_HOST")
if _HOST:
    http.headers.update({"Host": _HOST})


# ── helpers HTTP / OTP / staff ────────────────────────────────────────────────

def _payload(resp):
    d = resp.json()
    return d.get("message") or d


def _admin():
    frappe.set_user("Administrator")
    frappe.db.commit()


def _as_staff(role):
    frappe.set_user(STAFF[role])


def _png():
    """PNG valide (magic bytes) + suffixe unique → pas de collision de dédup File."""
    seal = open(frappe.get_app_path("admission", "public", "images", "lanem-seal.png"), "rb").read()
    return seal + frappe.generate_hash(length=12).encode()


def _otp_for(dossier):
    """Code OTP réel : dernier mail en file pour ce dossier, décodé MIME, validé HMAC."""
    from admission.api.public import _hash_otp
    frappe.db.commit()  # snapshot MVCC : voir les écritures du serveur HTTP
    for q in frappe.get_all("Email Queue", fields=["name", "message"],
                            order_by="creation desc", limit=15):
        mime = email_mod.message_from_string(q.message or "")
        bodies = []
        for part in mime.walk():
            if part.get_content_type() in ("text/html", "text/plain"):
                pl = part.get_payload(decode=True)
                if pl:
                    bodies.append(pl.decode("utf-8", errors="replace"))
        body = "\n".join(bodies)
        if dossier not in body:
            continue
        stored = frappe.get_value("Admission Applicant", dossier, "otp_email_hash")
        for code in set(re.findall(r"(?<!\d)(\d{6})(?!\d)", body)):
            if _hash_otp(code) == stored:
                return code
    raise AssertionError(f"OTP introuvable en file pour {dossier}")


# ── étapes du chemin métier ───────────────────────────────────────────────────

def _create(runid, suffix, date_bac="2024-06-01"):
    """create_dossier seul (HTTP candidat) → dossier BRO (brouillon, avant paiement)."""
    email = f"fixture-{runid}-{suffix}@{TAG_DOMAIN}"
    r = _payload(http.post(BASE + "create_dossier", json={
        "session": SESSION, "level_code": LEVEL,
        "identite": {"prenom": "Fixture", "nom": suffix.upper(), "email": email,
                     "tel": "+22990000000", "date_bac": date_bac},
        "consent_data_processing": 1, "consent_cgv": 1,
        "idempotency_key": f"fixture-{runid}-{suffix}"}))
    assert r.get("ok"), f"create_dossier: {r}"
    dossier, token = r["data"]["dossier_id"], r["data"]["token"]
    CREATED.append(dossier)   # enregistré DÈS création → purge garantie même sur abort
    return dossier, token


def _tunnel_to_sop(runid, suffix, date_bac="2024-06-01"):
    """create → OTP → verify → pièces requises → declare virement → SOP (HTTP réel candidat)."""
    dossier, token = _create(runid, suffix, date_bac)
    r = _payload(http.post(BASE + "request_otp", json={"dossier_id": dossier, "token": token}))
    assert r.get("ok"), f"request_otp: {r}"
    code = _otp_for(dossier)
    r = _payload(http.post(BASE + "verify_otp",
                           json={"dossier_id": dossier, "token": token, "email_otp": code}))
    assert r.get("ok"), f"verify_otp: {r}"
    token = r["data"]["token"]

    png = _png()
    d = _payload(http.get(BASE + "get_dossier", params={"dossier_id": dossier, "token": token}))["data"]
    required = [p["code"] for p in d["pieces"] if p["requise"]]
    for piece in required:
        rr = _payload(http.post(BASE + "upload_piece_file",
                                data={"dossier_id": dossier, "token": token, "piece_code": piece},
                                files={"file": (f"{piece}.png", png, "image/png")}))
        assert rr.get("ok"), f"upload {piece}: {rr}"

    r = _payload(http.post(BASE + "declare_payment_offline", json={
        "dossier_id": dossier, "token": token, "mode": "bank", "consent_refund": 1,
        "idempotency_key": f"fixture-pay-{runid}-{suffix}"}))
    assert r.get("ok") and r["data"]["statut"] == "SOP", f"declare_payment_offline: {r}"
    return dossier, token


def _confirm(dossier, label="frais 1"):
    """Confirmation staff du virement (Administratif) → cascade (fee Paid, SOP→SOU)."""
    from admission.api import staff
    frappe.db.commit()  # MVCC : voir le declare candidat (serveur HTTP)
    _as_staff("admin")
    r = staff.confirm_offline_payment(dossier_id=dossier, payment_mode="bank",
                                      justificatif="/private/files/fixture-justif.pdf")
    _admin()
    assert r.get("ok"), f"confirm {label}: {r}"


def _verify_required(dossier):
    """Vérifie TOUTES les pièces requises effectives (Administratif) — garde de start_review."""
    from admission.api import staff
    from admission.api.public import requise_effective
    frappe.db.commit()  # MVCC : voir les uploads candidat
    app = frappe.get_doc("Admission Applicant", dossier)
    codes = [p.piece_code for p in app.pieces if requise_effective(p)]
    _as_staff("admin")
    for c in codes:
        r = staff.verify_piece(dossier_id=dossier, piece_code=c)
        assert r.get("ok"), f"verify_piece {c}: {r}"
    _admin()


def _declare_enrollment(dossier, token, runid, suffix):
    r = _payload(http.post(BASE + "declare_enrollment_payment_offline", json={
        "dossier_id": dossier, "token": token, "mode": "bank",
        "consent_refund": 1, "consent_data_transfer": 1,
        "idempotency_key": f"fixture-frais2-{runid}-{suffix}"}))
    assert r.get("ok"), f"declare_enrollment_payment_offline: {r}"


def _result(dossier, token):
    return {"dossier_id": dossier, "token": token,
            "status": frappe.db.get_value("Admission Applicant", dossier, "status")}


# ── constructeur par état cible ───────────────────────────────────────────────

def build_to(target):
    """Construit un dossier fixture jusqu'à `target` par chemin métier. Renvoie {dossier_id, token, status}.

    target ∈ {BRO, SOP, SOU, ETU, ADM, ACC, INS, REF}. REF = branche depuis ETU (refuse Responsable).
    """
    from admission.api import staff
    target = (target or "ETU").upper()
    if target not in set(LADDER) | {"REF"}:
        raise ValueError(f"état cible inconnu: {target} (attendu {LADDER} ou REF)")
    runid = frappe.generate_hash(length=8)
    suffix = frappe.generate_hash(length=4)

    if target == "BRO":                                     # brouillon : create seul, avant paiement
        dossier, token = _create(runid, suffix)
        return _result(dossier, token)

    dossier, token = _tunnel_to_sop(runid, suffix)          # → SOP
    if target == "SOP":
        return _result(dossier, token)

    _confirm(dossier)                                       # SOP → SOU
    if target == "SOU":
        return _result(dossier, token)

    _verify_required(dossier)                               # garde 3c-1
    _as_staff("admin"); staff.start_review(dossier_id=dossier); _admin()   # SOU → ETU
    if target == "ETU":
        return _result(dossier, token)

    if target == "REF":                                     # branche ETU → REF (Responsable)
        _as_staff("resp")
        staff.refuse(dossier_id=dossier, motif="Fixture E2E — refus de démonstration.")
        _admin()
        return _result(dossier, token)

    _as_staff("resp"); staff.mark_admissible(dossier_id=dossier); _admin()  # ETU → ADM
    if target == "ADM":
        return _result(dossier, token)

    _as_staff("dir"); staff.accept_admission(dossier_id=dossier); _admin()  # ADM → ACC
    if target == "ACC":
        return _result(dossier, token)

    _declare_enrollment(dossier, token, runid, suffix)      # frais 2 (candidat)
    _confirm(dossier, label="frais 2")                      # confirmation staff
    _as_staff("dir"); staff.enroll(dossier_id=dossier); _admin()           # ACC → INS
    return _result(dossier, token)


# ── points d'entrée (bench execute) ───────────────────────────────────────────

def status_counts():
    rows = frappe.get_all("Admission Applicant", fields=["status"], limit_page_length=0)
    c = dict(Counter(r.status for r in rows))
    print(f"STATUS_COUNTS::{c}")
    return c


def build_one(target="ETU"):
    """Construit UN dossier fixture jusqu'à `target` ; imprime son id (capté par l'E2E)."""
    frappe.set_user("Administrator")
    res = build_to(target)
    frappe.db.commit()
    print(f"FIXTURE_ID::{res['dossier_id']}")
    print(f"FIXTURE_STATUS::{res['status']}")
    return res


def build_states(targets=("ETU", "ADM", "REF")):
    """Construit un dossier par état cible (GE1). Imprime chaque id + statut atteint."""
    frappe.set_user("Administrator")
    out = []
    for t in targets:
        res = build_to(t)
        out.append(res)
        print(f"BUILT::{t}::{res['dossier_id']}::{res['status']}")
    frappe.db.commit()
    return out


def _purge_dossier(n):
    for f in frappe.get_all("File", filters={"attached_to_doctype": "Admission Applicant",
                                             "attached_to_name": n}, pluck="name"):
        frappe.delete_doc("File", f, force=True, ignore_permissions=True)
    frappe.db.delete("Applicant Fee Payment", {"applicant": n})
    frappe.db.delete("Applicant Fee", {"applicant": n})
    # Applicant Piece Verdict = doctype STANDALONE (pas une child table) inséré par verify_piece →
    # non cascadé par delete_doc de l'Applicant. À supprimer explicitement, sinon dérive de baseline.
    frappe.db.delete("Applicant Piece Verdict", {"applicant": n})
    frappe.db.delete("Admission Consent Record", {"applicant": n})
    frappe.db.delete("Admission Applicant Transition Log", {"applicant": n})
    frappe.delete_doc("Admission Applicant", n, force=True, ignore_permissions=True)


def _purge_email_queue():
    for parent in set(frappe.get_all("Email Queue Recipient",
                                     filters={"recipient": ["like", f"fixture-%@{TAG_DOMAIN}"]},
                                     pluck="parent")):
        frappe.delete_doc("Email Queue", parent, force=True, ignore_permissions=True)


def purge():
    """Purge TOUS les dossiers fixture (par tag) + leur courrier. Robuste entre invocations."""
    frappe.set_user("Administrator")
    ids = frappe.get_all("Admission Applicant",
                         filters={"email": ["like", f"fixture-%@{TAG_DOMAIN}"]}, pluck="name")
    for n in set(CREATED) | set(ids):
        if frappe.db.exists("Admission Applicant", n):
            _purge_dossier(n)
    _purge_email_queue()
    frappe.db.commit()
    left = frappe.get_all("Admission Applicant",
                          filters={"email": ["like", f"fixture-%@{TAG_DOMAIN}"]}, pluck="name")
    print(f"PURGED::{len(ids)}")
    print(f"LEFT::{left}")
    return {"purged": len(ids), "left": left}
