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
    """Un Pending ONLINE survit au désistement (withdraw ne rejette que Cash/Bank) ; le webhook
    `_promote_payment` le passe Confirmed SANS vérifier applicant.status → argent encaissé sur DES."""
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

    # Le webhook fire (KkiaPay a encaissé) : promotion réelle, verify stubé SUCCESS en amont du promote.
    payment = frappe.get_doc("Applicant Fee Payment", pay_name)
    W._promote_payment(payment, "TX-AUDIT-" + ref, ref)

    status_apres = frappe.db.get_value("Admission Applicant", d, "status")
    pay_apres = frappe.db.get_value("Applicant Fee Payment", pay_name, "payment_status")
    trou = (pay_avant == "Pending" and pay_apres == "Confirmed" and status_apres == "DES")
    print(f"D-CONF-01:: statut_avant={status_avant} pay_avant={pay_avant} → "
          f"statut_apres={status_apres} pay_apres={pay_apres}")
    print(f"D-CONF-01:: [{'FINDING REPRODUIT' if trou else 'NON reproduit (corrigé ?)'}] "
          f"argent Confirmed sur dossier terminal DES")
    return {"finding": "D-CONF-01", "reproduit": trou}


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
    """Les 24 endpoints de mutation font `save(ignore_permissions=True)` → le hook `has_permission`
    n'est JAMAIS consulté sur écriture. Donc même cloisonnement ACTIVÉ (mode ON planifié prod), un
    Responsable scopé session X peut muter un dossier session Y. Mode OFF (défaut) = tout staff agit
    sur tout (assumé DEC-262). Le réglage ON est posé en transaction NON commitée puis rollback →
    aucune config recette partagée n'est modifiée."""
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
        # Cloisonnement ON transitoire : Responsable scopé sur une session ÉTRANGÈRE (NON commité)
        frappe.db.set_single_value(SETTINGS, "consultation_cloisonnee", 1)
        frappe.db.set_single_value(SETTINGS, "consultation_axis", "session")
        frappe.db.set_single_value(SETTINGS, "consultation_role_scopes",
                                   frappe.as_json({"Admission Responsable": ["SES-INEXISTANTE-AUDIT"]}))
        frappe.clear_cache()
        doc = frappe.get_doc("Admission Applicant", d)
        # (a) le hook has_permission BLOQUERAIT bien un accès hors périmètre (il fonctionne)
        out["has_permission_bloque"] = not PERM.has_permission(doc=doc, ptype="write", user=resp)
        frappe.set_user(resp)
        # (b) save() SANS ignore → has_permission consulté → PermissionError (le cloisonnement protège)
        doc_check = frappe.get_doc("Admission Applicant", d)
        doc_check.rang_liste_attente = 41
        bloque_sans_ignore = False
        try:
            doc_check.save()
        except frappe.PermissionError:
            bloque_sans_ignore = True
        out["save_normal_bloque"] = bloque_sans_ignore
        # (c) MAIS save(ignore_permissions=True) — ce que font TOUTES les 24 mutations — contourne
        doc2 = frappe.get_doc("Admission Applicant", d)
        doc2.rang_liste_attente = 42
        contourne = True
        try:
            doc2.save(ignore_permissions=True)
        except frappe.PermissionError:
            contourne = False
        frappe.set_user("Administrator")
        out["save_ignore_permissions_reussit"] = contourne
    finally:
        frappe.db.rollback()          # réglages cloisonnement JAMAIS commités
        frappe.clear_cache()
    frappe.set_user("Administrator")
    trou = (out.get("has_permission_bloque") and out.get("save_normal_bloque")
            and out.get("save_ignore_permissions_reussit"))
    print(f"D-CONF-04:: cloisonnement_off_par_defaut={off_defaut} · {out}")
    print(f"D-CONF-04:: [{'FINDING REPRODUIT' if trou else 'non reproduit'}] cloisonnement ON : "
          f"has_permission bloque mais save(ignore_permissions) contourne → IDOR écriture")
    return {"finding": "D-CONF-04", "reproduit": trou, "off_defaut": off_defaut, **out}
