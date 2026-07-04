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

def _reset_create_ratelimit():
    """Test-infra : purge le compteur d'ANTI-ABUS (rate limit) de create_dossier. Les fixtures
    partagent l'IP loopback → le plafond 20/h s'épuise en un run exhaustif. On efface une clé de
    CACHE (garde infra), JAMAIS une donnée métier : le décorateur @rate_limit reste en place en
    prod, la logique applicative est inchangée. Sans effet hors harnais de preuve."""
    try:
        frappe.cache.delete_keys("*create_dossier*")
    except Exception:
        pass


def _create(runid, suffix, date_bac="2024-06-01", session=None):
    """create_dossier seul (HTTP candidat) → dossier BRO (brouillon, avant paiement). Retry unique
    après purge du rate-limit : l'anti-abus infra (IP loopback partagée, 20/h) ne doit pas faire
    échouer la CONSTRUCTION de la preuve — la validation métier, elle, reste intégralement jouée."""
    email = f"fixture-{runid}-{suffix}@{TAG_DOMAIN}"
    body = {
        "session": session or SESSION, "level_code": LEVEL,
        "identite": {"prenom": "Fixture", "nom": suffix.upper(), "email": email,
                     "tel": "+22990000000", "date_bac": date_bac},
        "consent_data_processing": 1, "consent_cgv": 1,
        "idempotency_key": f"fixture-{runid}-{suffix}"}
    r = _payload(http.post(BASE + "create_dossier", json=body))
    if not r.get("ok") and "RateLimit" in str(r):
        _reset_create_ratelimit()
        r = _payload(http.post(BASE + "create_dossier", json=body))
    assert r.get("ok"), f"create_dossier: {r}"
    dossier, token = r["data"]["dossier_id"], r["data"]["token"]
    CREATED.append(dossier)   # enregistré DÈS création → purge garantie même sur abort
    return dossier, token


def _tunnel_to_sop(runid, suffix, date_bac="2024-06-01", session=None):
    """create → OTP → verify → pièces requises → declare virement → SOP (HTTP réel candidat)."""
    dossier, token = _create(runid, suffix, date_bac, session)
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

# États atteignables par le builder. Branches (REF/REJ/INC/ATT/DES/ACO) = CHEMIN MÉTIER RÉEL
# (jamais db.set_value de statut/bac_verified/notes_validated — une fixture qui triche ne prouve rien).
BRANCHES = ("REF", "REJ", "INC", "ATT", "DES", "ACO")


DISPOSABLE_PREFIX = "SES-AUDIT-DISPOSABLE"


def _ensure_disposable_session(runid=None):
    """Session JETABLE isolée (copie de SESSION) à bac_results_date FUTUR (+400 j) → ACO durable
    (toujours conditionnel, plus de time-bomb 07-31) ET close_session sans jamais détruire les
    fixtures partagées. Nom SUFFIXÉ par runid (`SES-AUDIT-DISPOSABLE-<runid>`) → concurrence-safe :
    deux runs ne partagent plus la même session (durcissement B-2 LOW-2). Idempotent. Purgée par
    _teardown_disposable_session (balayage par PRÉFIXE). Résout D-CONF-DURA."""
    from frappe.utils import add_days, now_datetime
    runid = runid or frappe.generate_hash(length=8)
    name = f"{DISPOSABLE_PREFIX}-{runid}"
    if not frappe.db.exists("Admission Session", name):
        src = frappe.get_doc("Admission Session", SESSION)          # copie BACH-ASRC (même programme/level)
        sess = frappe.copy_doc(src)
        sess.session_code = name                                    # autoname:field:session_code → name
        sess.label = "Session jetable audit CONFORMITÉ-E2E"
        sess.bac_results_date = add_days(now_datetime(), 400)      # seuil TOUJOURS futur → bac_attente durable
        sess.is_open = 1
        sess.insert(ignore_permissions=True)
        frappe.db.commit()
    return name


def _teardown_disposable_session():
    """Balaye TOUTES les sessions jetables par PRÉFIXE — concurrence-safe : un run nettoie ses
    propres sessions ET tout résidu d'un run interrompu, sans nom partagé fragile ni force-delete
    aveugle d'une session non-jetable (durcissement B-2 LOW-2)."""
    for name in frappe.get_all("Admission Session",
                               filters={"name": ["like", f"{DISPOSABLE_PREFIX}-%"]}, pluck="name"):
        frappe.delete_doc("Admission Session", name, force=True, ignore_permissions=True)
    frappe.db.commit()


def _aco_date_bac(session):
    """date_bac produisant conditionnel=1 (bac_attente) : _classify_bac_date → bac_attente ⟺
    année(bac)==année(today) ET today < seuil de session. On renvoie une date ANNÉE COURANTE (15 jan) ;
    valable tant que le seuil de `session` est futur. Échoue FORT sinon (jamais de fixture ACO silencieuse
    — utiliser la session jetable à seuil +400 j)."""
    from frappe.utils import getdate, now_datetime
    threshold = frappe.db.get_value("Admission Session", session, "bac_results_date")
    today = getdate(now_datetime())
    if not threshold or getdate(threshold) <= today:
        raise AssertionError(
            f"ACO non constructible : session {session} bac_results_date={threshold} n'est pas FUTUR → "
            f"utiliser la session jetable (_ensure_disposable_session, seuil +400 j).")
    return str(today.replace(month=1, day=15))   # date année courante → year(bac)==year(today), today<seuil


def build_to(target, date_bac=None):
    """Construit un dossier fixture jusqu'à `target` PAR CHEMIN MÉTIER (vraies API + rôles réels).

    target ∈ LADDER {BRO,SOP,SOU,ETU,ADM,ACC,INS} ou BRANCHES {REF,REJ,INC,ATT,DES,ACO}.
    ACO exige un dossier CONDITIONNEL (bac en attente) : construit via un date_bac année-courante <
    seuil de session (_classify_bac_date → bac_attente → conditionnel=1). Aucun statut/flag n'est seedé.
    """
    from admission.api import staff
    target = (target or "ETU").upper()
    if target not in set(LADDER) | set(BRANCHES):
        raise ValueError(f"état cible inconnu: {target} (attendu {LADDER} ou {BRANCHES})")
    runid = frappe.generate_hash(length=8)
    suffix = frappe.generate_hash(length=4)
    # ACO : conditionnel requis → session JETABLE à seuil futur (durable) + date_bac attente dérivée.
    aco_session = _ensure_disposable_session(runid) if target == "ACO" else None
    dbac = date_bac or (_aco_date_bac(aco_session) if target == "ACO" else "2024-06-01")

    if target == "BRO":                                     # brouillon : create seul, avant paiement
        dossier, token = _create(runid, suffix, dbac)
        return _result(dossier, token)

    if target == "DES":                                     # désistement depuis SOP (Pending offline → F3)
        dossier, token = _tunnel_to_sop(runid, suffix, dbac)
        frappe.db.commit()                                  # MVCC : voir les écritures HTTP du tunnel
        _as_staff("admin"); r = staff.withdraw(dossier_id=dossier, motif="Fixture E2E — désistement candidat.")
        _admin()
        assert r.get("ok"), f"withdraw: {r}"
        return _result(dossier, token)

    dossier, token = _tunnel_to_sop(runid, suffix, dbac, session=aco_session)   # → SOP (session jetable si ACO)
    if target == "SOP":
        return _result(dossier, token)

    _confirm(dossier)                                       # SOP → SOU
    if target == "SOU":
        return _result(dossier, token)

    if target == "REJ":                                     # SOU → REJ (contrôle documentaire, Admin)
        _as_staff("admin"); r = staff.reject_dossier(dossier_id=dossier, motif="Fixture E2E — dossier rejeté.")
        _admin()
        assert r.get("ok"), f"reject_dossier: {r}"
        return _result(dossier, token)

    if target == "INC":                                     # SOU → INC (complément requis, Admin)
        _as_staff("admin"); r = staff.request_complement(dossier_id=dossier, motif="Fixture E2E — pièce à compléter.")
        _admin()
        assert r.get("ok"), f"request_complement: {r}"
        return _result(dossier, token)

    _verify_required(dossier)                               # garde 3c-1
    _as_staff("admin"); staff.start_review(dossier_id=dossier); _admin()   # SOU → ETU
    if target == "ETU":
        return _result(dossier, token)

    if target == "ATT":                                     # ETU → ATT (liste d'attente, rang≥1, Resp)
        _as_staff("resp"); r = staff.waitlist(dossier_id=dossier, rang=1)
        _admin()
        assert r.get("ok"), f"waitlist: {r}"
        return _result(dossier, token)

    if target == "ACO":                                     # ETU → ACO (conditionnel, Resp)
        _as_staff("resp"); r = staff.conditional_admission(dossier_id=dossier)
        _admin()
        assert r.get("ok"), f"conditional_admission (dossier conditionnel requis — date_bac attente): {r}"
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

def verify_branches(targets=("REJ", "INC", "ATT", "DES", "ACO", "REF")):
    """CONFORMITÉ-E2E Bloc F — prouve que chaque état branche est atteint PAR CHEMIN MÉTIER
    (status DB == cible). Garde-fou : aucune fixture ne triche par db.set_value."""
    frappe.set_user("Administrator")
    out = []
    for t in targets:
        res = build_to(t)
        real = frappe.db.get_value("Admission Applicant", res["dossier_id"], "status")
        cond = frappe.db.get_value("Admission Applicant", res["dossier_id"], "conditionnel")
        ok = real == t
        out.append((t, real, ok))
        extra = f" conditionnel={cond}" if t == "ACO" else ""
        print(f"BRANCH::{t}::real={real}::{'OK' if ok else 'MISMATCH'}{extra}")
    frappe.db.commit()
    print(f"BRANCHES_TOTAL::{sum(1 for _, _, o in out if o)}/{len(out)}")
    return out


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


# ── B-3 : entrées UI (E2E navigateur, bundle live) ────────────────────────────
# Ces points d'entrée AMORCENT les scénarios navigateur (candidat + management). Ils restent
# 100 % chemin métier (aucun db.set_value de statut/flag) et lisibles server-side par l'E2E.

def emit_otp(dossier):
    """Imprime le code OTP courant du dossier — lu server-side depuis l'Email Queue (HMAC vérifié),
    pour saisie par le tunnel candidat E2E. Read-only (aucune écriture applicative)."""
    frappe.set_user("Administrator")
    code = _otp_for(dossier)
    print(f"OTP::{code}")
    return code


def ui_context():
    """Contexte d'amorçage du tunnel candidat (create via le VRAI formulaire) : session ouverte,
    niveau, date bac (profil standard, non conditionnel), domaine de tag. Read-only.
    Sortie une-ligne-par-champ → parsing robuste côté E2E navigateur."""
    frappe.set_user("Administrator")
    print(f"UICTX_SESSION::{SESSION}")
    print(f"UICTX_LEVEL::{LEVEL}")
    print(f"UICTX_DATEBAC::2024-06")
    print(f"UICTX_TAGDOMAIN::{TAG_DOMAIN}")
    return {"session": SESSION, "level": LEVEL, "datebac": "2024-06", "tag_domain": TAG_DOMAIN}


def build_ui(kind):
    """Préconditions E2E UI management au-delà de build_to. Imprime FIXTURE_ID + statut.
    Toujours par chemin métier (vraies API + rôles réels) :
      ACC_F2_PENDING : ACC + frais 2 déclaré offline (Pending) → prouver « confirmer frais 2 ».
      ACC_F2_PAID    : ACC + frais 2 déclaré + confirmé (Paid, NON inscrit) → prouver « inscrire ».
      ACO_DIPLOMA    : ACO + diplôme DÉPOSÉ (non vérifié) → prouver « vérifier le diplôme ».
      ACO_VERIF      : ACO + diplôme déposé ET vérifié (bac_verified) → prouver « lever la condition ».
      SOP_PENDING    : SOP (frais 1 Pending offline) → prouver « confirmer frais 1 ».
      autre          : délègue à build_to(kind)."""
    from admission.api import staff
    frappe.set_user("Administrator")
    kind = (kind or "").upper()
    if kind in ("ACC_F2_PENDING", "ACC_F2_PAID"):
        runid = frappe.generate_hash(length=8)
        suffix = frappe.generate_hash(length=4)
        res = build_to("ACC")                                  # ACC par chemin métier (frais 2 ÉMIS)
        d, tok = res["dossier_id"], res["token"]
        _declare_enrollment(d, tok, runid, suffix)             # candidat déclare frais 2 offline → Pending
        if kind == "ACC_F2_PAID":
            _confirm(d, label="frais 2")                       # staff confirme → Paid (prêt à inscrire)
        out = _result(d, tok)
    elif kind in ("ACO_DIPLOMA", "ACO_VERIF"):
        res = build_to("ACO")
        d, tok = res["dossier_id"], res["token"]
        frappe.db.commit()
        # Chemin métier réel : le bac est arrivé → le candidat DÉPOSE le diplôme (diplome_bac).
        # verify_bac_diploma exige la pièce uploaded (DIPLOMA_MISSING sinon).
        png = _png()
        rr = _payload(http.post(BASE + "upload_piece_file",
                                data={"dossier_id": d, "token": tok, "piece_code": "diplome_bac"},
                                files={"file": ("diplome_bac.png", png, "image/png")}))
        assert rr.get("ok"), f"upload diplome_bac: {rr}"
        frappe.db.commit()
        if kind == "ACO_VERIF":                                # + l'Administratif vérifie (bac_verified=1)
            _as_staff("admin"); r = staff.verify_bac_diploma(dossier_id=d); _admin()
            assert r.get("ok"), f"verify_bac_diploma: {r}"
        out = _result(d, tok)
    elif kind == "SOU_VERIFIED":
        # SOU avec TOUTES les requises vérifiées (contrôle documentaire fait) → garde de start_review
        # levée (PIECES_NON_VERIFIEES sinon). Chemin métier : verify_piece réel par l'Administratif.
        res = build_to("SOU")
        d = res["dossier_id"]
        _verify_required(d)
        out = _result(d, res["token"])
    elif kind == "SOP_PENDING":
        out = build_to("SOP")                                  # SOP = frais 1 Pending offline
    elif kind == "ETU_COND":
        # ETU CONDITIONNEL (bac en attente) — précondition de conditional_admission via UI.
        # Session jetable (seuil futur) + date_bac attente → conditionnel=1, arrêt AVANT ACO.
        runid = frappe.generate_hash(length=8)
        suffix = frappe.generate_hash(length=4)
        sess = _ensure_disposable_session(runid)
        d, tok = _tunnel_to_sop(runid, suffix, _aco_date_bac(sess), session=sess)   # SOP conditionnel
        _confirm(d)                                            # SOP → SOU
        _verify_required(d)                                    # garde start_review
        _as_staff("admin"); staff.start_review(dossier_id=d); _admin()   # SOU → ETU (conditionnel)
        out = _result(d, tok)
    else:
        out = build_to(kind)
    # MVCC : les états terminaux du tunnel (SOP/BRO) sont écrits par le PROCESSUS HTTP ; le snapshot
    # REPEATABLE-READ du process bench est figé avant. commit() ferme la transaction → la relecture
    # suivante ouvre un snapshot FRAIS qui voit l'état réel (sinon SOP se lit BRO).
    frappe.db.commit()
    status = frappe.db.get_value("Admission Applicant", out["dossier_id"], "status")
    print(f"FIXTURE_ID::{out['dossier_id']}")
    print(f"FIXTURE_STATUS::{status}")
    return {**out, "status": status}


def dossier_state(dossier):
    """Read-only : corroboration HORS-BANDE des transitions UI à état constant (revue B-3 F1/F2) —
    le toast seul ne prouve pas l'effet. Imprime les effets DB réels : statut, rang d'attente,
    bac_verified, statut du frais 2 (enrollment) et nombre de paiements Confirmed/Paid."""
    st = frappe.db.get_value("Admission Applicant", dossier,
                             ["status", "rang_liste_attente", "bac_verified"], as_dict=True) or {}
    fee2 = frappe.db.get_value("Applicant Fee", {"applicant": dossier, "fee_type": "enrollment"},
                               "status")
    n_conf = frappe.db.count("Applicant Fee Payment",
                             {"applicant": dossier, "payment_status": ["in", ["Confirmed", "Paid"]]})
    print(f"DSTATE_STATUS::{st.get('status')}")
    print(f"DSTATE_RANG::{st.get('rang_liste_attente') or 0}")
    print(f"DSTATE_BACVERIF::{int(st.get('bac_verified') or 0)}")
    print(f"DSTATE_FEE2::{fee2 or 'none'}")
    print(f"DSTATE_NCONF::{n_conf}")
    return {"status": st.get("status"), "rang": st.get("rang_liste_attente"),
            "bac_verified": st.get("bac_verified"), "fee2": fee2, "n_confirmed": n_conf}


def session_state(session):
    """Read-only : état d'une session après clôture UI — is_open + comptes de dossiers par statut.
    Preuve robuste de close_session (l'effet définitif = is_open passe à 0), indépendante du reflet
    du toast. Imprime SESSION_OPEN + les statuts des dossiers de la session."""
    is_open = frappe.db.get_value("Admission Session", session, "is_open")
    rows = frappe.get_all("Admission Applicant", filters={"session": session},
                          fields=["status"], limit_page_length=0)
    c = dict(Counter(r.status for r in rows))
    print(f"SESSION_OPEN::{int(is_open or 0)}")
    print(f"SESSION_DOSSIERS::{c}")
    return {"is_open": int(is_open or 0), "dossiers": c}


def debug_close_scope(session):
    """Diagnostic close_session : nombre de dossiers basculables d'une `session` vus par Administrator
    (bypass) vs par le rôle Direction (get_permission_query_conditions appliqué). Un écart = la bascule
    de masse est SCOPÉE par l'acteur → des dossiers hors périmètre resteraient dans la session clôturée.
    Read-only (dry_run=1). Ne modifie rien."""
    from admission.api import staff
    frappe.set_user("Administrator")
    a = staff.close_session(session=session, dry_run=1)
    frappe.set_user(STAFF["dir"])
    d = staff.close_session(session=session, dry_run=1)
    frappe.set_user("Administrator")
    at = (a.get("data") or {}).get("total")
    dt = (d.get("data") or {}).get("total")
    print(f"CLOSE_SCOPE::admin_total={at}::dir_total={dt}::ecart={'OUI' if at != dt else 'non'}")
    return {"admin": a.get("data"), "dir": d.get("data")}


def stage_reject(dossier):
    """B-3 « resubmit » E2E : depuis un SOP créé par le NAVIGATEUR, confirme le frais 1 (SOP→SOU)
    puis rejette la 1re pièce requise (Administratif) → le candidat verra le motif et pourra
    re-déposer via l'UI. Imprime le code de pièce rejetée + le motif. 100 % chemin métier."""
    from admission.api import staff
    from admission.api.public import requise_effective
    frappe.set_user("Administrator")
    _confirm(dossier)                                          # SOP → SOU (voir le declare navigateur : MVCC dans _confirm)
    app = frappe.get_doc("Admission Applicant", dossier)
    codes = [p.piece_code for p in app.pieces if requise_effective(p)]
    assert codes, f"aucune pièce requise à rejeter sur {dossier}"
    cible, reason = codes[0], "Illisible / floue"
    _as_staff("admin")
    r = staff.reject_piece(dossier_id=dossier, piece_code=cible, reason=reason, comment="photo floue (E2E)")
    _admin()
    assert r.get("ok"), f"reject_piece: {r}"
    frappe.db.commit()
    print(f"REJECTED_PIECE::{cible}")
    print(f"REJECT_REASON::{reason}")
    return {"piece_code": cible, "reason": reason}


def open_disposable():
    """close_session E2E : crée une session JETABLE ouverte + 1 dossier SOP (non-abouti) taggé
    DEDANS → la clôture UI (Direction) doit le basculer en DES. Imprime le nom de session + le
    dossier. Session purgée par _teardown (préfixe) ; dossier par tag. Isole close_session des
    fixtures partagées (jamais la session recette réelle)."""
    frappe.set_user("Administrator")
    runid = frappe.generate_hash(length=8)
    suffix = frappe.generate_hash(length=4)
    name = _ensure_disposable_session(runid)
    d, tok = _tunnel_to_sop(runid, suffix, "2024-06-01", session=name)   # SOP dans la session jetable
    frappe.db.commit()
    print(f"DISPOSABLE::{name}")
    print(f"FIXTURE_ID::{d}")
    print(f"FIXTURE_STATUS::{frappe.db.get_value('Admission Applicant', d, 'status')}")
    return {"session": name, "dossier_id": d, "token": tok}


def build_recap_rejected():
    """Fixture RAPPELS-J4J6 (vague 3) : dossier SOU avec ≥1 pièce REJETÉE + récap envoyé (ancre posée).
    100 % chemin métier : build_to(SOU) → verify requises SAUF une → reject la dernière → notify_pieces_recap.
    Imprime l'id. Consommateur de l'enabler vague 2."""
    from admission.api import staff
    from admission.api.public import requise_effective
    frappe.set_user("Administrator")
    res = build_to("SOU")
    dossier = res["dossier_id"]
    frappe.db.commit()
    app = frappe.get_doc("Admission Applicant", dossier)
    codes = [p.piece_code for p in app.pieces if requise_effective(p)]
    if not codes:
        raise AssertionError("aucune pièce requise à rejeter (fixture recap)")
    _as_staff("admin")
    for c in codes[:-1]:
        rv = staff.verify_piece(dossier_id=dossier, piece_code=c)     # les autres → verified
        assert rv.get("ok"), f"verify_piece {c}: {rv}"
    rj = staff.reject_piece(dossier_id=dossier, piece_code=codes[-1],
                            reason="Illisible / floue", comment="flou")   # la dernière → rejected
    assert rj.get("ok"), f"reject_piece {codes[-1]}: {rj}"
    r = staff.notify_pieces_recap(dossier_id=dossier)                 # récap réel → pose l'ancre
    _admin()
    assert r.get("ok"), f"notify_pieces_recap: {r}"
    out = _result(dossier, res["token"])
    print(f"FIXTURE_ID::{out['dossier_id']}")
    print(f"FIXTURE_STATUS::{out['status']}")
    return out


def seed_recap_age(dossier, days):
    """Décor TEMPOREL non gardé : recule `pieces_recap_sent_at` de `days` jours pour éprouver les
    fenêtres J4/J6 sans attendre. Les états dossier/pièces restent 100 % chemin métier."""
    from frappe.utils import add_days, now_datetime
    frappe.set_user("Administrator")
    frappe.db.set_value("Admission Applicant", dossier, "pieces_recap_sent_at",
                        add_days(now_datetime(), -int(days)), update_modified=False)
    frappe.db.commit()
    print(f"SEEDED_RECAP_AGE::{dossier}::-{days}d")


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
    _teardown_disposable_session()   # dossiers ACO purgés ci-dessus → session jetable vidée puis supprimée
    frappe.db.commit()
    left = frappe.get_all("Admission Applicant",
                          filters={"email": ["like", f"fixture-%@{TAG_DOMAIN}"]}, pluck="name")
    print(f"PURGED::{len(ids)}")
    print(f"LEFT::{left}")
    return {"purged": len(ids), "left": left}


def purge_after(fn):
    """Décorateur d'audit : garantit `purge()` en `finally` → baseline restaurée MÊME sur exception
    mid-run (durcissement B-2 LOW-1). La purge par tag étant idempotente, un run réussi qui a déjà
    tout nettoyé voit un `finally` sans effet (PURGED::0). Aucun résidu transitoire ne subsiste."""
    import functools

    @functools.wraps(fn)
    def _wrapped(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        finally:
            purge()
    return _wrapped
