"""CONFORMITÉ-E2E Bloc 2 — jonctions candidat↔staff (audit pur, real-DB + HTTP réel).

(A) REFLETS MENTEURS (D-CONF-05/07/08) : le staff pose une décision (motif de rejet/refus/désistement,
    rang d'attente) mais `_serialize_dossier` (get_dossier candidat) ne l'expose PAS → l'état vu par le
    candidat DIVERGE du réel. expected_finding : le champ est SET en DB, ABSENT de la sérialisation.
(B) JONCTION FONCTIONNELLE : le seul reflet croisé qui marche — reject_piece(motif) → le candidat voit
    le motif sur get_dossier → re-dépôt + candidate_resubmit → badge « Re-soumis » côté staff
    (list_dossiers) → verify_piece éteint le badge → start_review + mark_admissible → le candidat voit ADM.
    Prouvé bout-en-bout HTTP réel + rôles staff réels.

Exécution : bench --site <site> execute admission.tests.e2e.audit_bloc2.<fn>
"""
import frappe

from admission.tests.fixtures import recette_fixtures as F


def _payload(r):
    d = r.json()
    return d.get("message") or d


def _candidate_dossier(dossier, token):
    """get_dossier candidat (HTTP réel, token seul) → dict sérialisé (ce que VOIT le candidat)."""
    r = _payload(F.http.get(F.BASE + "get_dossier", params={"dossier_id": dossier, "token": token}))
    assert r.get("ok"), f"get_dossier: {r}"
    return r["data"]


# ── (A) D-CONF-05/07/08 — reflets menteurs ────────────────────────────────────────────────────

# état → (champ DB posé par le staff, présent dans la sérialisation candidat ?)
_REFLETS = {
    "REJ": "motif_rejet",           # reject_dossier — D-CONF-05
    "REF": "motif_refus",           # refuse — D-CONF-07
    "DES": "motif_desistement",     # withdraw — D-CONF-07
    "ATT": "rang_liste_attente",    # waitlist — D-CONF-08
}


@F.purge_after
def d_conf_reflets_inproc():
    """FIX-D-CONF-05/07/08 — preuve real-DB PRE-MERGE (serializer IN-PROCESS : code frais du bench execute,
    sans restart gunicorn → recette pristine). Chaque état à décision (chemin métier) → _serialize_dossier
    expose le champ FIDÈLE à la DB. La variante HTTP (`d_conf_reflets_menteurs`) est le gardien LIVE."""
    from admission.api.public import _serialize_dossier
    frappe.set_user("Administrator")
    out = []
    for etat, champ in _REFLETS.items():
        res = F.build_to(etat)
        d = res["dossier_id"]
        frappe.db.commit()
        db_val = frappe.db.get_value("Admission Applicant", d, champ)
        data = _serialize_dossier(frappe.get_doc("Admission Applicant", d))
        fidele = (champ in data) and bool(db_val) and str(data.get(champ)) == str(db_val)
        out.append((etat, champ, fidele))
        print(f"REFLET_INPROC::{etat}::champ={champ} db={db_val!r} serialized={data.get(champ)!r} "
              f"[{'FIDÈLE' if fidele else 'MENTEUR'}]")
        F.purge()
    n = sum(1 for *_, f in out if f)
    print(f"REFLETS_INPROC::{n}/{len(out)}  (D-CONF-05/07/08 serializer FERMÉ)")
    return {"fideles": n, "ferme": n == len(out), "detail": out}


@F.purge_after
def d_conf_reflets_menteurs():
    """FIX-D-CONF-05/07/08 (test-preuve INVERSÉ → gardien du fix). Le staff pose une décision motivée
    (motif rejet/refus/désistement, rang) ; le serializer candidat l'expose désormais FIDÈLEMENT au
    candidat concerné. Chemin métier réel pour chaque état. reflet FIDÈLE = champ présent ET == valeur DB
    (avant : champ ABSENT = reflet menteur)."""
    frappe.set_user("Administrator")
    out = []
    for etat, champ in _REFLETS.items():
        res = F.build_to(etat)                                   # chemin métier réel (motif/rang posé)
        d, tok = res["dossier_id"], res["token"]
        frappe.db.commit()
        db_val = frappe.db.get_value("Admission Applicant", d, champ)
        data = _candidate_dossier(d, tok)
        vu = data.get(champ)                                       # ce que VOIT le candidat
        fidele = (champ in data) and bool(db_val) and str(vu) == str(db_val)   # présent ET fidèle à la DB
        out.append((etat, champ, db_val, vu, fidele))
        print(f"REFLET::{etat}::champ={champ} db={db_val!r} candidat={vu!r} "
              f"[{'FIDÈLE' if fidele else 'MENTEUR'}]")
        F.purge()
    n_fideles = sum(1 for *_, f in out if f)
    print(f"REFLETS_FIDELES::{n_fideles}/{len(out)}  (D-CONF-05/07/08 FERMÉ)")
    return {"findings": ["D-CONF-05", "D-CONF-07", "D-CONF-08"],
            "reflets_fideles": n_fideles, "ferme": n_fideles == len(out), "detail": out}


# ── (B) Jonction fonctionnelle bout-en-bout ───────────────────────────────────────────────────

@F.purge_after
def junction_reject_resubmit_admit():
    """Le seul reflet croisé fonctionnel, prouvé HTTP réel + staff réels."""
    from admission.api import staff
    from admission.api.public import requise_effective
    frappe.set_user("Administrator")
    runid = frappe.generate_hash(length=8)
    suffix = frappe.generate_hash(length=4)
    d, tok = F._tunnel_to_sop(runid, suffix)          # → SOP (pièces uploadées, otp_verified)
    F._confirm(d)                                     # SOP → SOU
    frappe.db.commit()
    app = frappe.get_doc("Admission Applicant", d)
    codes = [p.piece_code for p in app.pieces if requise_effective(p)]
    cible = codes[0]
    r = []

    # 1) staff rejette une pièce (motif)
    F._as_staff("admin")
    rj = staff.reject_piece(dossier_id=d, piece_code=cible, reason="Illisible / floue", comment="photo floue")
    F._admin()
    assert rj.get("ok"), f"reject_piece: {rj}"

    # 2) le candidat VOIT le motif (get_dossier HTTP)
    data = _candidate_dossier(d, tok)
    piece = next(p for p in data["pieces"] if p["code"] == cible)
    r.append(("candidat voit pièce rejetée + motif",
              piece["statut_reel"] == "rejected" and piece["reject_reason"] == "Illisible / floue",
              f"statut_reel={piece['statut_reel']} motif={piece['reject_reason']!r}"))

    # 3) le candidat re-dépose (HTTP) + signale la fin (candidate_resubmit)
    png = F._png()
    up = _payload(F.http.post(F.BASE + "upload_piece_file",
                              data={"dossier_id": d, "token": tok, "piece_code": cible},
                              files={"file": (f"{cible}.png", png, "image/png")}))
    assert up.get("ok"), f"upload_piece_file: {up}"
    rs = _payload(F.http.post(F.BASE + "candidate_resubmit", json={"dossier_id": d, "token": tok}))
    assert rs.get("ok"), f"candidate_resubmit: {rs}"
    frappe.db.commit()
    resoumis_1 = frappe.db.get_value("Admission Applicant", d, "resoumis")
    r.append(("resoumis=1 après re-soumission", resoumis_1 == 1, f"resoumis={resoumis_1}"))

    # 4) le staff voit le badge « Re-soumis » (list_dossiers)
    F._as_staff("admin")
    lst = staff.list_dossiers(q=d)
    F._admin()
    row = next((x for x in lst["data"]["dossiers"] if x["dossier_id"] == d), None)
    r.append(("badge Re-soumis côté staff (list_dossiers)", bool(row) and row.get("resoumis") is True,
              f"row_resoumis={row.get('resoumis') if row else 'row absente'}"))

    # 5) le staff re-contrôle (verify_piece) → badge éteint
    F._as_staff("admin")
    staff.verify_piece(dossier_id=d, piece_code=cible)
    F._admin()
    resoumis_0 = frappe.db.get_value("Admission Applicant", d, "resoumis")
    r.append(("resoumis éteint au re-contrôle", resoumis_0 == 0, f"resoumis={resoumis_0}"))

    # 6) vérifier le reste + admettre (SOU→ETU→ADM)
    F._verify_required(d)                             # vérifie toutes les requises (dont la re-déposée)
    F._as_staff("admin"); staff.start_review(dossier_id=d); F._admin()
    F._as_staff("resp"); staff.mark_admissible(dossier_id=d); F._admin()

    # 7) le candidat VOIT l'admission (get_dossier)
    data2 = _candidate_dossier(d, tok)
    r.append(("candidat voit statut ADM", data2["statut"] == "ADM", f"statut={data2['statut']}"))

    ok = all(p for _, p, _ in r)
    for label, p, detail in r:
        print(f"JONCTION:: [{'OK' if p else 'FAIL'}] {label} :: {detail}")
    print(f"JONCTION_TOTAL::{sum(1 for _, p, _ in r if p)}/{len(r)}")
    return {"junction_ok": ok, "detail": r}
