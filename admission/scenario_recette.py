"""SCÉNARIO RECETTE multi-personas — OUTIL PERMANENT, rejouable (smoke-test du parcours complet).

Auto-décor (session dédiée, frais PRE seedés, purge des résidus) et AUTO-CLEANUP total.

5 candidats × 3 staff réels, sur une session Prépa DÉDIÉE (copie de SES-2026-10) :
  · Awa    — parcours complet : tunnel HTTP → SOP → concours → ADM → ACC → frais 2 → INS → PONT campus
  · Bintou — bac en attente : ACO → dépôt diplôme (HTTP) → vérif Adm → levée Direction → ACC
  · Koffi  — admissible puis REFUSÉ par la Direction (W2)
  · Fatou  — déclare un virement puis SE DÉSISTE (W1) — son Pending doit passer Rejected (F3)
  · Marc   — instruit mais jamais conclu → REF par CLÔTURE DE SESSION (W4)
Candidat = HTTP réel (8010) ; staff = sessions réelles (set_user des 3 comptes de rôle) ;
pont = HTTP réel vers le campus (8000). Résultats imprimés à CHAQUE étape. Cleanup total.

bench --site admission-dev.localhost execute admission.scenario_recette.run
"""

import email as email_mod
import re

import frappe
import requests as _requests
from frappe.utils import now_datetime

BASE = "http://127.0.0.1:8010/api/method/admission.api.public."
HOST = {"Host": "admission-dev.localhost:8010"}
SESSION_ID = "SES-SCENARIO-RECETTE"
STAFF = {"Estelle (Administratif)": "estelle.gbaguidi@lanem.bj",
         "Karim (Responsable)": "karim.sossou@lanem.bj",
         "Pr. Adjovi (Direction)": "adjovi.mensah@lanem.bj"}

http = _requests.Session()
http.headers.update(HOST)

RESULTS = {"pass": 0, "fail": 0}
STEP = {"n": 0}
CREATED = []   # dossiers enregistrés DÈS création → cleanup garanti même sur abort
RUN_ID = [""]


def step(label, ok, detail=""):
    STEP["n"] += 1
    RESULTS["pass" if ok else "fail"] += 1
    print(f"  [{'✓' if ok else '✗'}] É{STEP['n']:02d} {label} — {detail}")
    if not ok:
        raise AssertionError(f"É{STEP['n']} {label}: {detail}")


def _payload(resp):
    d = resp.json()
    return d.get("message") or d


def _as_staff(label):
    frappe.set_user(STAFF[label])


def _otp_for(dossier):
    """Code OTP réel : dernier mail en file pour ce dossier, décodé MIME, validé HMAC."""
    from admission.api.public import _hash_otp
    frappe.db.commit()  # snapshot MVCC (commits du serveur HTTP)
    for q in frappe.get_all("Email Queue", fields=["name", "message"],
                            order_by="creation desc", limit=10):
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


def _tunnel_to_sop(prenom, nom, email, date_bac, persona):
    """Tunnel candidat COMPLET en HTTP : create → OTP → verify → pièces → declare bank → SOP."""
    r = _payload(http.post(BASE + "create_dossier", json={
        "session": SESSION_ID, "level_code": "PRE-A1",
        "identite": {"prenom": prenom, "nom": nom, "email": email,
                     "tel": "+22990000020", "date_bac": date_bac},
        "consent_data_processing": 1, "consent_cgv": 1,
        "idempotency_key": f"scenario-{RUN_ID[0]}-{email}",
    }))
    step(f"{persona} crée son dossier (HTTP, Person campus résolue)", r.get("ok"),
         f"{r.get('data', {}).get('dossier_id')} — {r.get('error') or 'token reçu'}")
    dossier, token = r["data"]["dossier_id"], r["data"]["token"]
    CREATED.append(dossier)

    r = _payload(http.post(BASE + "request_otp", json={"dossier_id": dossier, "token": token}))
    assert r.get("ok"), f"request_otp: {r}"
    code = _otp_for(dossier)
    step(f"{persona} reçoit l'OTP par e-mail (code décodé == hash HMAC)", bool(code), "code 6 chiffres en file")

    r = _payload(http.post(BASE + "verify_otp",
                           json={"dossier_id": dossier, "token": token, "email_otp": code}))
    step(f"{persona} vérifie l'OTP (e-mail seul, token tourné)", r.get("ok"), "nouveau token adopté")
    token = r["data"]["token"]

    # PNG rendu UNIQUE par persona/run (octets de queue) → pas de collision de dédup File
    png = (open(frappe.get_app_path("admission", "public", "images", "lanem-seal.png"), "rb").read()
           + frappe.generate_hash(length=12).encode())
    d = _payload(http.get(BASE + "get_dossier", params={"dossier_id": dossier, "token": token}))["data"]
    required = [p["code"] for p in d["pieces"] if p["requise"]]
    for piece in required:
        rr = _payload(http.post(BASE + "upload_piece_file",
                                data={"dossier_id": dossier, "token": token, "piece_code": piece},
                                files={"file": (f"{piece}.png", png, "image/png")}))
        assert rr.get("ok"), rr
    step(f"{persona} dépose ses pièces (binaire HTTP, magic bytes)", True,
         f"{len(required)} pièce(s) requise(s) déposée(s)")

    r = _payload(http.post(BASE + "declare_payment_offline", json={
        "dossier_id": dossier, "token": token, "mode": "bank", "consent_refund": 1,
        "idempotency_key": f"scenario-pay-{RUN_ID[0]}-{email}"}))
    step(f"{persona} déclare un virement (frais 1) → SOP + mail RIB Coris",
         r.get("ok") and r["data"]["statut"] == "SOP", f"statut={r['data']['statut']}")
    return dossier, token


def _confirm_frais(dossier, persona, label="frais 1"):
    from admission.api import staff
    frappe.db.commit()  # MVCC : voir les écritures du serveur HTTP (declare candidat)
    _as_staff("Estelle (Administratif)")
    r = staff.confirm_offline_payment(dossier_id=dossier, payment_mode="bank",
                                      justificatif="/private/files/justif-scenario.pdf")
    frappe.set_user("Administrator"); frappe.db.commit()
    step(f"Estelle confirme le {label} de {persona} (justificatif, cascade, reçu)",
         r.get("ok"), f"statut={(r.get('data') or {}).get('status') or (r.get('error') or {}).get('code')}")
    return r


def _instruit(dossier, persona, notes=True):
    from admission.api import staff
    frappe.db.commit()  # MVCC
    _as_staff("Estelle (Administratif)")
    r = staff.start_review(dossier_id=dossier)
    step(f"Estelle met le dossier de {persona} en étude", r.get("ok"), "SOU→ETU")
    if notes:
        r = staff.saisir_note_concours(dossier_id=dossier,
                                       notes={"Mathématiques": 14, "Culture générale": 12})
        step(f"Estelle saisit les notes de concours de {persona} (Prépa)", r.get("ok"), "non validées")
        _as_staff("Karim (Responsable)")
        r = staff.valider_notes_concours(dossier_id=dossier)
        step(f"Karim valide les notes de {persona} (séparation saisie≠validation)", r.get("ok"), "notes_validated=1")
    frappe.set_user("Administrator"); frappe.db.commit()


def _purge_dossier(n):
    for f in frappe.get_all("File", filters={"attached_to_doctype": "Admission Applicant",
                                             "attached_to_name": n}, pluck="name"):
        frappe.delete_doc("File", f, force=True, ignore_permissions=True)
    frappe.db.delete("Applicant Fee Payment", {"applicant": n})
    frappe.db.delete("Applicant Fee", {"applicant": n})
    frappe.db.delete("Admission Consent Record", {"applicant": n})
    frappe.db.delete("Admission Applicant Transition Log", {"applicant": n})
    frappe.delete_doc("Admission Applicant", n, force=True, ignore_permissions=True)


def run():
    from admission.api import staff
    from admission.api.bridge import _send_bridge_notification
    emails, dossiers, person_ids = [], [], []
    try:
        # ── Décor : session Prépa dédiée ──
        if not frappe.db.exists("Admission Session", SESSION_ID):
            src = frappe.get_doc("Admission Session", "SES-2026-10")
            sess = frappe.copy_doc(src)
            sess.session_code = SESSION_ID
            sess.label = "Session scénario recette"
            sess.is_open = 1
            sess.insert(ignore_permissions=True)
            frappe.db.commit()
        # Décor : le catalogue UF (miroir) n'a AUCUNE entrée PRE en DEV (famille ADM-DEBT-72,
        # signalé) — seed contrôlé des 3 types de frais PRE, retirés au cleanup.
        for fee_type, amount in (("application", 15000), ("enrollment", 50000), ("annual", 800000)):
            key = f"PRE-DEFAULT-{fee_type}"
            if not frappe.db.exists("Admission Fee Catalog", key):
                frappe.get_doc({"doctype": "Admission Fee Catalog", "program_code": "PRE",
                                "level_code": "DEFAULT", "fee_type": fee_type,
                                "amount_xof": amount}).insert(ignore_permissions=True)
        frappe.db.commit()
        from admission.api.public import _invalidate_catalog_cache
        _invalidate_catalog_cache()
        RUN_ID[0] = frappe.generate_hash(length=8)
        # DEV : purge des dossiers scénario RÉSIDUELS de runs interrompus
        for leftover in frappe.get_all("Admission Applicant",
                                       filters={"email": ("like", "scenario-%@exemple.bj")},
                                       pluck="name"):
            _purge_dossier(leftover)
        frappe.db.commit()
        frappe.cache.delete_keys("rl:")  # DEV : noms CAN-* réutilisés entre runs → quotas cumulés
        # DEV : purge des File ORPHELINS de runs interrompus (dossier supprimé, fichier
        # physique disparu) — sinon la dédup par content_hash crashe sur get_content.
        for f in frappe.get_all("File", filters={"attached_to_doctype": "Admission Applicant"},
                                fields=["name", "attached_to_name"]):
            if not frappe.db.exists("Admission Applicant", f.attached_to_name):
                frappe.delete_doc("File", f.name, force=True, ignore_permissions=True)
        frappe.db.commit()
        print(f"Décor : session Prépa dédiée {SESSION_ID} (copie SES-2026-10), 3 staff réels ;")
        print("        + seed catalogue frais PRE (application/enrollment/annual) — ADM-DEBT-72 signalée.\n")
        print("NOTE : miroir bourses UF vide en DEV → sous-étape bourses sautée (logique couverte par tests C2).\n")

        # ════ AWA — parcours complet jusqu'au campus ════
        print("— AWA (Licence-type, parcours nominal complet) —")
        awa_email = "scenario-awa@exemple.bj"; emails.append(awa_email)
        awa, awa_tok = _tunnel_to_sop("Awa", "Sossa", awa_email, "2024-06-01", "Awa")
        dossiers.append(awa)
        _confirm_frais(awa, "Awa")
        _instruit(awa, "Awa")
        _as_staff("Karim (Responsable)")
        r = staff.mark_admissible(dossier_id=awa)
        step("Karim déclare Awa admissible (mail Prépa avec notes)", r.get("ok"), "ETU→ADM")
        _as_staff("Pr. Adjovi (Direction)")
        r = staff.accept_admission(dossier_id=awa)
        step("Pr. Adjovi accepte l'admission d'Awa (frais 2 émis)", r.get("ok"), "ADM→ACC")
        frappe.set_user("Administrator"); frappe.db.commit()
        r = _payload(http.post(BASE + "declare_enrollment_payment_offline", json={
            "dossier_id": awa, "token": awa_tok, "mode": "bank",
            "consent_refund": 1, "consent_data_transfer": 1,
            "idempotency_key": f"scenario-awa-frais2-{RUN_ID[0]}"}))
        step("Awa déclare le virement des FRAIS 2 (consentements requis)", r.get("ok"),
             f"ventilation={(r.get('data') or {}).get('ventilation') or (r.get('error') or {}).get('code')}")
        _confirm_frais(awa, "Awa", label="frais 2")
        _as_staff("Pr. Adjovi (Direction)")
        r = staff.enroll(dossier_id=awa)
        step("Pr. Adjovi inscrit Awa (gates frais 2 + consentement)", r.get("ok"), "ACC→INS")
        frappe.set_user("Administrator"); frappe.db.commit()
        result = _send_bridge_notification(awa)
        bstatus = (result.get("message") or {}).get("status")
        step("PONT : Awa transmise au campus (HTTP réel, X-API-Key)",
             bstatus in ("ok", "already_ins", "created_and_ins"), f"status métier={bstatus}")
        pid = frappe.get_value("Admission Applicant", awa, "person_id"); person_ids.append(pid)
        marked = frappe.db.get_value("Admission Applicant", awa, "bridge_notified")
        step("PONT : acquittement marqué sur le dossier", marked == 1, "bridge_notified=1")

        # ════ BINTOU — bac en attente → ACO → levée ════
        print("\n— BINTOU (bac en attente → admission conditionnelle) —")
        b_email = "scenario-bintou@exemple.bj"; emails.append(b_email)
        bintou, b_tok = _tunnel_to_sop("Bintou", "Adjo", b_email, "2026-06-01", "Bintou")
        dossiers.append(bintou)
        cond = frappe.db.get_value("Admission Applicant", bintou, "conditionnel")
        step("Profil bac_attente classé conditionnel par le back", cond == 1, "conditionnel=1")
        _confirm_frais(bintou, "Bintou")
        _instruit(bintou, "Bintou")
        _as_staff("Karim (Responsable)")
        r = staff.conditional_admission(dossier_id=bintou)
        step("Karim prononce l'admission conditionnelle de Bintou", r.get("ok"), "ETU→ACO")
        frappe.set_user("Administrator"); frappe.db.commit()
        png = (open(frappe.get_app_path("admission", "public", "images", "lanem-seal.png"), "rb").read()
               + frappe.generate_hash(length=12).encode())
        r = _payload(http.post(BASE + "upload_piece_file",
                               data={"dossier_id": bintou, "token": b_tok, "piece_code": "diplome_bac"},
                               files={"file": ("diplome.png", png, "image/png")}))
        step("Bintou dépose son diplôme du bac (HTTP, depuis le suivi)", r.get("ok"), "diplome_bac déposé")
        frappe.db.commit()  # MVCC : upload HTTP
        _as_staff("Estelle (Administratif)")
        r = staff.verify_bac_diploma(dossier_id=bintou)
        step("Estelle vérifie le diplôme (INV-HUMAN, pas de levée)", r.get("ok"), "bac_verified=1")
        _as_staff("Pr. Adjovi (Direction)")
        r = staff.lift_condition(dossier_id=bintou)
        step("Pr. Adjovi lève la condition de Bintou", r.get("ok"), "ACO→ACC (frais 2 émis)")
        frappe.set_user("Administrator"); frappe.db.commit()

        # ════ KOFFI — admissible puis refusé par la Direction (W2) ════
        print("\n— KOFFI (admissible, refusé en validation Direction) —")
        k_email = "scenario-koffi@exemple.bj"; emails.append(k_email)
        koffi, _ = _tunnel_to_sop("Koffi", "Mensah", k_email, "2023-06-01", "Koffi")
        dossiers.append(koffi)
        _confirm_frais(koffi, "Koffi")
        _instruit(koffi, "Koffi")
        _as_staff("Karim (Responsable)")
        staff.mark_admissible(dossier_id=koffi)
        _as_staff("Pr. Adjovi (Direction)")
        r = staff.refuse(dossier_id=koffi, motif="Capacité d'accueil atteinte sur ce niveau.")
        step("Pr. Adjovi REFUSE un admissible (W2, motif notifié)", r.get("ok"), "ADM→REF par la Direction")
        frappe.set_user("Administrator"); frappe.db.commit()

        # ════ FATOU — désistement (W1) avec Pending offline ════
        print("\n— FATOU (déclare un virement puis se désiste) —")
        f_email = "scenario-fatou@exemple.bj"; emails.append(f_email)
        fatou, _ = _tunnel_to_sop("Fatou", "Bio", f_email, "2024-06-01", "Fatou")
        dossiers.append(fatou)
        frappe.db.commit()  # MVCC : declare HTTP de Fatou
        _as_staff("Estelle (Administratif)")
        r = staff.withdraw(dossier_id=fatou, motif="Désistement à la demande de la candidate (appel du 12/06).")
        step("Estelle désiste Fatou (W1, motif obligatoire, mail neutre)", r.get("ok"), "SOP→DES")
        frappe.set_user("Administrator"); frappe.db.commit()
        pend = frappe.get_all("Applicant Fee Payment",
                              filters={"applicant": fatou}, fields=["payment_status"])
        step("F3 : son Pending virement est passé Rejected (rien d'encaissable)",
             all(p.payment_status == "Rejected" for p in pend), f"{[p.payment_status for p in pend]}")
        _as_staff("Estelle (Administratif)")
        r = staff.confirm_offline_payment(dossier_id=fatou, payment_mode="bank", justificatif="x")
        step("F2 : toute confirmation sur dossier clos est REFUSÉE",
             not r.get("ok") and r["error"]["code"] in ("INVALID_STATE", "NO_PENDING_PAYMENT"),
             f"code={r['error']['code']}")
        frappe.set_user("Administrator"); frappe.db.commit()

        # ════ MARC — instruit, jamais conclu ════
        print("\n— MARC (instruit, jamais conclu — candidat à la clôture) —")
        m_email = "scenario-marc@exemple.bj"; emails.append(m_email)
        marc, _ = _tunnel_to_sop("Marc", "Dossou", m_email, "2022-06-01", "Marc")
        dossiers.append(marc)
        _confirm_frais(marc, "Marc")
        _instruit(marc, "Marc", notes=False)
        step("Marc reste en étude (notes jamais saisies)", True, "ETU — dossier dormant")

        # ════ CLÔTURE DE SESSION (W4, Direction) ════
        print("\n— CLÔTURE DE SESSION (Pr. Adjovi) —")
        frappe.db.commit()  # MVCC
        _as_staff("Pr. Adjovi (Direction)")
        prev = staff.close_session(session=SESSION_ID)
        step("Prévisualisation (dry-run, AUCUNE écriture)", prev["data"]["dry_run"],
             f"bascules={prev['data']['bascules']}")
        r = staff.close_session(session=SESSION_ID, dry_run=0)
        frappe.set_user("Administrator"); frappe.db.commit()
        step("Clôture exécutée : instruits→REF, non-aboutis→DES, session fermée",
             r["data"]["echecs"] == 0, f"REF={r['data']['refuses']} DES={r['data']['desistes']}")
        statuts = {n: frappe.db.get_value("Admission Applicant", n, "status") for n in dossiers}
        attendu = {awa: "INS", bintou: "DES", koffi: "REF", fatou: "DES", marc: "REF"}
        step("États finaux conformes (Awa INS intouchée ; Bintou ACC non confirmé → DES ; Marc ETU → REF)",
             statuts == attendu, f"{ {k.split('-')[-1]: v for k, v in statuts.items()} }")

        # ════ Bilan courrier ════
        frappe.db.commit()
        print("\n— BILAN COURRIER (Email Queue) —")
        for label, em in [("Awa", awa_email), ("Bintou", b_email), ("Koffi", k_email),
                          ("Fatou", f_email), ("Marc", m_email)]:
            n = frappe.db.count("Email Queue Recipient", {"recipient": em})
            print(f"    {label:<7} {em:<28} → {n} mail(s)")
        total = sum(frappe.db.count("Email Queue Recipient", {"recipient": e}) for e in emails)
        step("Chaque persona a été notifié à chaque geste qui le concerne", total >= 20,
             f"{total} mails en file au total")

        print(f"\nSCÉNARIO RECETTE : {RESULTS['pass']} étapes PASS / {RESULTS['fail']} FAIL")
    finally:
        frappe.set_user("Administrator")
        # mails
        for em in emails:
            for parent in set(frappe.get_all("Email Queue Recipient",
                                             filters={"recipient": em}, pluck="parent")):
                frappe.delete_doc("Email Queue", parent, force=True, ignore_permissions=True)
        # dossiers + dépendances (registre CREATED : couvre aussi les aborts mi-tunnel)
        for n in set(CREATED + dossiers):
            _purge_dossier(n)
        if frappe.db.exists("Admission Session", SESSION_ID):
            frappe.delete_doc("Admission Session", SESSION_ID, force=True, ignore_permissions=True)
        for fee_type in ("application", "enrollment", "annual"):
            frappe.delete_doc("Admission Fee Catalog", f"PRE-DEFAULT-{fee_type}",
                              force=True, ignore_permissions=True)
        frappe.db.commit()
        print(f"Cleanup admission : {len(set(CREATED + dossiers))} dossiers + session scénario supprimés "
              f"(campus : SA/Person {person_ids} à purger côté campus).")
