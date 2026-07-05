"""CONFORMITÉ-E2E Bloc 4 — reproduction ADVERSARIALE des findings HAUT, en real-DB (AUDIT PUR).

Chaque fonction REPRODUIT le comportement bugué ACTUEL et l'asserte : un test « vert » = le trou
EXISTE (expected_finding=D-CONF-XX). Quand le lot correctif viendra, l'assertion s'inversera et ce
module deviendra la non-régression du fix. Aucun code applicatif n'est modifié ici.

Concurrence = threads Python RÉELS (chacun frappe.init+connect ; destroy en worker seulement, JAMAIS
dans le thread principal). Argent = webhook réel (`_promote_payment`, la promotion qui crée le Confirmed).
Fixtures = chemin métier strict (recette_fixtures). Purge comptant toutes les lignes.

Exécution : bench --site <site> execute admission.tests.e2e.audit_bloc4.<fn>
"""
import threading

import frappe

from admission.tests.fixtures import recette_fixtures as F


def _payload(r):
    d = r.json()
    return d.get("message") or d


def _online_pending(dossier, token, runid):
    """Crée un Pending ONLINE (frais 1) via l'endpoint RÉEL submit_payment_online (pas de seed).
    Renvoie (payment_name, provider_reference)."""
    r = _payload(F.http.post(F.BASE + "submit_payment_online", json={
        "dossier_id": dossier, "token": token, "consent_refund": 1,
        "idempotency_key": f"audit-{runid}"}))
    assert r.get("ok"), f"submit_payment_online: {r}"
    frappe.db.commit()
    row = frappe.get_all("Applicant Fee Payment",
                         filters={"applicant": dossier, "provider": "kkiapay", "payment_status": "Pending"},
                         fields=["name", "provider_reference"], order_by="creation desc", limit=1)
    assert row, "Pending online introuvable après submit_payment_online"
    return row[0].name, row[0].provider_reference


# ── D-CONF-01 : argent confirmé sur dossier TERMINAL (webhook sans garde d'état) ──────────────

@F.purge_after
def d_conf_01_argent_terminal():
    """D-CONF-01 — INVERSÉ (lot FIX-D-CONF-01). Le scénario qui REPRODUISAIT le trou (Pending online
    survivant au désistement → webhook `_promote_payment` → Confirmed sur DES) prouve désormais sa
    FERMETURE : verrou 2 (withdraw rejette AUSSI le Pending Online) + verrou 1 (garde d'état dans
    `_promote_payment` → promotion REFUSÉE sur dossier terminal, refund tracé, 0 argent confirmé).
    Ce module est devenu la non-régression du correctif."""
    import admission.api.webhook as W
    frappe.set_user("Administrator")
    runid = frappe.generate_hash(length=8)
    suffix = frappe.generate_hash(length=4)
    d, tok = F._tunnel_to_sop(runid, suffix)              # → SOP (chemin métier)
    frappe.db.commit()
    pay_name, ref = _online_pending(d, tok, runid)        # Pending ONLINE réel
    F._as_staff("admin")
    rw = frappe.get_attr("admission.api.staff.withdraw")(dossier_id=d, motif="Audit D-CONF-01 — désistement.")
    F._admin()
    assert rw.get("ok"), f"withdraw: {rw}"
    status_avant = frappe.db.get_value("Admission Applicant", d, "status")
    pay_avant = frappe.db.get_value("Applicant Fee Payment", pay_name, "payment_status")

    # Le webhook fire (KkiaPay a encaissé) : promotion réelle sur le Pending (désormais Rejected par le
    # verrou 2). Le verrou 1 (garde d'état dans _promote_payment) doit REFUSER — même via le chemin de
    # réconciliation « Promoted late » (Rejected→Confirmed) — car le dossier est DES.
    payment = frappe.get_doc("Applicant Fee Payment", pay_name)
    promoted = W._promote_payment(payment, "TX-AUDIT-" + ref, ref)

    status_apres = frappe.db.get_value("Admission Applicant", d, "status")
    pay_apres = frappe.db.get_value("Applicant Fee Payment", pay_name, "payment_status")
    reconciliation = frappe.db.get_value("Applicant Fee Payment", pay_name, "reconciliation")

    verrou2_rejet = (pay_avant == "Rejected")                       # withdraw a rejeté le Pending Online
    refund_trace = bool(reconciliation and "Refused" in reconciliation)  # perdant tracé (refund OPS)
    verrou1_refus = (promoted is False and pay_apres != "Confirmed" and status_apres == "DES")
    ferme = verrou1_refus and refund_trace                         # le trou est FERMÉ (0 argent sur DES)
    trou = not ferme                                               # inversion : reproduit ⟺ pas fermé
    print(f"D-CONF-01:: statut_avant={status_avant} pay_avant={pay_avant} → "
          f"statut_apres={status_apres} pay_apres={pay_apres} promoted={promoted} reconciliation={reconciliation!r}")
    print(f"D-CONF-01:: verrou2(rejet online)={verrou2_rejet} verrou1(refus promotion)={verrou1_refus} "
          f"refund_trace={refund_trace}")
    print(f"D-CONF-01:: [{'FERMÉ — 0 argent sur DES' if ferme else 'TROU ENCORE OUVERT'}]")
    return {"finding": "D-CONF-01", "reproduit": trou, "ferme": ferme,
            "verrou1_refus": verrou1_refus, "verrou2_rejet": verrou2_rejet, "refund_trace": refund_trace}


# ── D-CONF-02 : double frais 2 (pas de contrainte unique applicant+enrollment) ────────────────

def _accept_worker(dossier, site, out, barrier):
    frappe.init(site=site)
    frappe.connect()
    try:
        barrier.wait(timeout=20)
        frappe.set_user(F.STAFF["dir"])
        r = frappe.get_attr("admission.api.staff.accept_admission")(dossier_id=dossier)
        out.append(("accept", bool(r.get("ok")), (r.get("error") or {}).get("code")))
        frappe.db.commit()
    except Exception as e:
        out.append(("accept", False, type(e).__name__))
    finally:
        frappe.destroy()


@F.purge_after
def d_conf_02_double_frais2():
    """VRAI FINDING (structurel, NON exercé par ce test) : `_ensure_enrollment_fee` (public.py:749) est
    un check-then-insert SANS contrainte unique (applicant, fee_type) — le seul unique sur Applicant Fee
    porte sur idempotency_key, passé None → NULLs non-collidants. Aucune protection DB contre 2 frais 2.

    Ce test EXERCE la course 2×accept sur le même ADM : elle est PROTÉGÉE par le verrou optimiste Frappe
    (load_doc_before_save FOR UPDATE → TimestampMismatch sur le 2e save applicant) → 1 seul frais 2.
    La fenêtre latente RESTE ouverte hors même-doc (2 candidats submit_enrollment sur un ACC-sans-frais2,
    cas ONACCEPTED-SILENT) : NON reproductible ici, à couvrir avec un ACC-sans-frais2. Reproduit=False ne
    signifie donc PAS « corrigé » — l'index unique manque toujours (remédiation = contrainte DB)."""
    frappe.set_user("Administrator")
    site = frappe.local.site
    res = F.build_to("ADM")
    d = res["dossier_id"]
    frappe.db.commit()
    out, barrier = [], threading.Barrier(2)
    threads = [threading.Thread(target=_accept_worker, args=(d, site, out, barrier)) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=40)
    n_fee2 = frappe.db.count("Applicant Fee", {"applicant": d, "fee_type": "enrollment"})
    trou = n_fee2 > 1
    print(f"D-CONF-02:: workers={out} fees_enrollment={n_fee2}")
    print(f"D-CONF-02:: [{'FINDING REPRODUIT' if trou else 'protégé (TimestampMismatch a filtré)'}] "
          f"double frais 2 (n={n_fee2})")
    return {"finding": "D-CONF-02", "reproduit": trou, "n_fee2": n_fee2}


# ── D-CONF-03 : décision concurrente (accept ∥ refuse sur le même ADM) ─────────────────────────

def _decision_worker(dossier, kind, site, out, barrier):
    frappe.init(site=site)
    frappe.connect()
    try:
        barrier.wait(timeout=20)
        if kind == "accept":
            frappe.set_user(F.STAFF["dir"])
            r = frappe.get_attr("admission.api.staff.accept_admission")(dossier_id=dossier)
        else:
            frappe.set_user(F.STAFF["dir"])
            r = frappe.get_attr("admission.api.staff.refuse")(dossier_id=dossier, motif="Audit D-CONF-03 — refus concurrent.")
        out.append((kind, bool(r.get("ok")), (r.get("error") or {}).get("code")))
        frappe.db.commit()
    except Exception as e:
        out.append((kind, False, type(e).__name__))
    finally:
        frappe.destroy()


@F.purge_after
def d_conf_03_decision_race():
    """`accept_admission` ∥ `refuse` sur le même ADM : les deux lisent ADM avant commit. Si les DEUX
    réussissent → décision contradictoire (last-write-wins) : l'invariant « une seule décision » est
    violable. Seul filet = TimestampMismatch."""
    frappe.set_user("Administrator")
    site = frappe.local.site
    res = F.build_to("ADM")
    d = res["dossier_id"]
    frappe.db.commit()
    out, barrier = [], threading.Barrier(2)
    threads = [
        threading.Thread(target=_decision_worker, args=(d, "accept", site, out, barrier)),
        threading.Thread(target=_decision_worker, args=(d, "refuse", site, out, barrier)),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=40)
    final = frappe.db.get_value("Admission Applicant", d, "status")
    both_ok = sum(1 for _, ok, _ in out if ok) == 2
    print(f"D-CONF-03:: workers={out} statut_final={final}")
    print(f"D-CONF-03:: [{'FINDING REPRODUIT (2 décisions ok)' if both_ok else 'une seule décision a gagné'}] "
          f"statut final={final}")
    return {"finding": "D-CONF-03", "reproduit": both_ok, "final": final}


# ── D-CONF-04 : IDOR écriture — les mutations save(ignore_permissions) ignorent le cloisonnement ──

@F.purge_after
def d_conf_04_idor_write():
    """FIX-D-CONF-04 (test-preuve INVERSÉ → gardien du fix). Les mutations consultent désormais le
    cloisonnement en ÉCRITURE (`_guard_write_scope` → `has_permission`) AVANT `save(ignore_permissions)`.

    Cloisonnement ON (posé NON commité, rollback en finally → config recette JAMAIS modifiée) : un
    Responsable scopé sur une session ÉTRANGÈRE qui appelle une MUTATION RÉELLE (`waitlist`) est
    REFUSÉ (`FORBIDDEN_SCOPE` 403) et le dossier n'est PAS muté (reste ETU).
    Mode OFF (défaut) : la MÊME mutation RÉUSSIT (non-régression stricte : tout le staff agit sur tout).
    Prouvé real-DB + rôles réels, jamais un mock."""
    from admission.api import staff
    from admission.api import permissions as PERM
    frappe.set_user("Administrator")
    SETTINGS = "Admission Settings"
    resp = F.STAFF["resp"]
    res = F.build_to("ETU")
    d = res["dossier_id"]
    frappe.db.commit()
    off_defaut = not frappe.db.get_single_value(SETTINGS, "consultation_cloisonnee")
    out = {}
    try:
        # Cloisonnement ON transitoire (NON commité) : Responsable scopé sur une session ÉTRANGÈRE.
        frappe.db.set_single_value(SETTINGS, "consultation_cloisonnee", 1)
        frappe.db.set_single_value(SETTINGS, "consultation_axis", "session")
        frappe.db.set_single_value(SETTINGS, "consultation_role_scopes",
                                   frappe.as_json({"Admission Responsable": ["SES-INEXISTANTE-AUDIT"]}))
        frappe.clear_cache()
        doc = frappe.get_doc("Admission Applicant", d)
        out["has_permission_bloque"] = (PERM.has_permission(doc=doc, ptype="write", user=resp) is False)
        # ENDPOINT réel appelé par un acteur HORS périmètre → doit être REFUSÉ (la garde refuse AVANT
        # tout save/commit → l'ON non-commité est proprement annulé par le rollback).
        frappe.set_user(resp)
        r_on = staff.waitlist(dossier_id=d, rang=7)
        frappe.set_user("Administrator")
        out["endpoint_refuse"] = (not r_on.get("ok")) and (r_on.get("error") or {}).get("code") == "FORBIDDEN_SCOPE"
        out["statut_apres_refus"] = frappe.db.get_value("Admission Applicant", d, "status")  # attendu ETU
    finally:
        frappe.db.rollback()          # réglages cloisonnement JAMAIS commités → recette intacte
        frappe.clear_cache()
    frappe.set_user("Administrator")
    # Non-régression OFF (défaut) : la MÊME mutation, même acteur, RÉUSSIT (tout le staff agit sur tout).
    off_ok = None
    if off_defaut:
        frappe.set_user(resp)
        r_off = staff.waitlist(dossier_id=d, rang=7)
        frappe.set_user("Administrator")
        off_ok = bool(r_off.get("ok")) and frappe.db.get_value("Admission Applicant", d, "status") == "ATT"
    ferme = (out.get("has_permission_bloque") and out.get("endpoint_refuse")
             and out.get("statut_apres_refus") == "ETU" and (off_ok if off_defaut else True))
    print(f"D-CONF-04:: off_defaut={off_defaut} · {out} · off_nonregression={off_ok}")
    print(f"D-CONF-04:: [{'FERMÉ — écriture cloisonnée' if ferme else 'NON fermé'}] "
          f"ON hors périmètre → {'REFUSÉ (FORBIDDEN_SCOPE), dossier non muté' if out.get('endpoint_refuse') else 'NON refusé'} ; "
          f"OFF → mutation nominale ({'ok' if off_ok else 'KO'})")
    return {"finding": "D-CONF-04", "reproduit": (not ferme), "ferme": ferme,
            "off_defaut": off_defaut, "off_ok": off_ok, **out}
