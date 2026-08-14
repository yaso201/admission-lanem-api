"""Recette locale NOTES-CONCOURS — preuve GN1–GN8 au niveau endpoint, VRAIS users (set_user).
Fixtures directes (session Prépa + 5 convoqués ETU), gates joués, CLEANUP total.
  bench --site admission-dev.localhost execute admission._recette_notes.run
"""
import json

import frappe

SESSION = "SES-NOTES-RECETTE"
ADM = "estelle.gbaguidi@lanem.bj"     # Administratif
RESP = "karim.sossou@lanem.bj"        # Responsable
DIR = "adjovi.mensah@lanem.bj"        # Direction
R = {"pass": 0, "fail": 0}


def ok(label, cond, detail=""):
    R["pass" if cond else "fail"] += 1
    print(f"  [{'✓' if cond else '✗'}] {label} — {detail}")
    if not cond:
        raise AssertionError(f"{label} :: {detail}")


def _cleanup():
    frappe.set_user("Administrator")
    sessions = frappe.get_all("Admission Session", filters={"name": ["like", SESSION + "%"]}, pluck="name")
    for sess in sessions:
        for a in frappe.get_all("Admission Applicant", filters={"session": sess}, pluck="name"):
            frappe.db.delete("Applicant Fee Payment", {"applicant": a})
            frappe.db.delete("Applicant Fee", {"applicant": a})
            frappe.db.delete("Admission Applicant Transition Log", {"applicant": a})
            frappe.delete_doc("Admission Applicant", a, force=True, ignore_permissions=True)
        frappe.delete_doc("Admission Session", sess, force=True, ignore_permissions=True)
    frappe.db.commit()


def _mk_session(name=SESSION):
    frappe.get_doc({
        "doctype": "Admission Session", "session_code": name, "label": "Recette Notes",
        "programme_code": "PREPA", "programme_label": "Cycle Préparatoire", "academic_year": "2026-2027",
        "opens_on": "2026-08-01", "closes_on": "2026-08-20", "bac_results_date": "2026-07-15",
        "application_fee_xof": 15000, "is_open": 0, "lifecycle_state": "Closed",
        "is_prepa_session": 1, "exam_date": "2026-08-26", "exam_call_time": "07:30:00",
        "exam_start_time": "08:00:00",
    }).insert(ignore_permissions=True)


def _mk_candidat(prenom, nom, num, session=SESSION):
    a = frappe.get_doc({
        "doctype": "Admission Applicant", "status": "BRO", "first_name": prenom, "last_name": nom,
        "email": f"{prenom}.{nom}.{num}@rec.bj".lower(), "phone": "+22990000000", "programme_code": "PREPA",
        "programme_label": "Cycle Préparatoire", "level_code": "PRE-A1", "session": session,
        "convocation_number": num,
    }).insert(ignore_permissions=True)
    # ETU forcé en base (raw) : le Workflow Frappe interdit BRO→ETU direct ; fixture uniquement.
    frappe.db.set_value("Admission Applicant", a.name, "status", "ETU", update_modified=False)
    fee = frappe.get_doc({"doctype": "Applicant Fee", "applicant": a.name, "session": session,
                          "fee_type": "competition", "amount_xof": 15000, "status": "Paid"}
                         ).insert(ignore_permissions=True)
    frappe.get_doc({"doctype": "Applicant Fee Payment", "applicant_fee": fee.name, "applicant": a.name,
                    "payment_mode": "Cash", "amount_xof": 15000, "payment_status": "Confirmed",
                    "justificatif": "/private/files/recette-justif.pdf"}
                   ).insert(ignore_permissions=True)
    return a.name


def run():
    from admission.api import staff
    _cleanup()
    try:
        frappe.set_user("Administrator")
        _mk_session()
        ada = _mk_candidat("Ada", "Alpha", "26260001")
        bob = _mk_candidat("Bob", "Beta", "26260002")
        cora = _mk_candidat("Cora", "Gamma", "26260003")
        dan = _mk_candidat("Dan", "Delta", "26260004")
        eve = _mk_candidat("Eve", "Epsilon", "26260005")
        fred = _mk_candidat("Fred", "Zeta", "26260006")   # restera SANS notes (requirement 3)
        frappe.db.commit()
        print(f"Fixtures : 6 convoqués ETU sur {SESSION}\n--- GATES ---")

        # GN2 — coefficients portés par la session, posés par le RESPONSABLE (Administratif exclu)
        frappe.set_user(ADM)
        try:
            staff.set_exam_coefficients(session_id=SESSION, coefficients={"maths": 3, "physique": 2, "culture": 1})
            adm_blocked = False
        except frappe.PermissionError:
            adm_blocked = True
        ok("GN2 Administratif ne pose PAS les coefficients (arbitrage 3)", adm_blocked, "PermissionError")
        frappe.set_user(RESP)
        r = staff.set_exam_coefficients(session_id=SESSION, coefficients={"maths": 3, "physique": 2, "culture": 1})
        ok("GN2 Responsable pose les coefficients", r.get("ok"), str(r.get("data", {}).get("coefficients")))
        frappe.db.commit()

        # GN3 (MAÎTRE) — note hors plage refusée, y compris par APPEL DIRECT (ce run EST un appel direct)
        frappe.set_user(ADM)
        r = staff.saisir_note_concours(dossier_id=ada, notes={"maths": 25, "physique": 13, "culture": 12})
        ok("GN3 note>20 refusée (appel direct endpoint)",
           not r.get("ok") and r["error"]["code"] == "NOTES_INVALID", r.get("error", {}).get("code"))
        ok("GN3 rien n'est enregistré", not frappe.db.get_value("Admission Applicant", ada, "notes_concours"),
           "notes_concours vide")
        # 2ᵉ couche : écriture DIRECTE d'une note hors plage → validate() du dossier lève
        try:
            frappe.set_user("Administrator")
            d = frappe.get_doc("Admission Applicant", ada)
            d.notes_concours = json.dumps({"maths": 30, "physique": 10, "culture": 10})
            d.save(ignore_permissions=True)
            layer2 = False
        except frappe.ValidationError:
            layer2 = True
        ok("GN3 2ᵉ couche : validate() dossier refuse le hors-plage direct", layer2, "ValidationError")

        # GN1 — 3 notes valides → moyenne PONDÉRÉE exposée à la lecture (jamais stockée)
        frappe.set_user(ADM)
        r = staff.saisir_note_concours(dossier_id=ada, notes={"maths": 15, "physique": 10, "culture": 8})
        ok("GN1 saisie des 3 notes", r.get("ok"), str(r.get("data", {}).get("notes")))
        d = staff.get_dossier(dossier_id=ada)["data"]["notes"]
        # (15*3 + 10*2 + 8*1) / 6 = 73/6 = 12.17
        ok("GN1 moyenne pondérée calculée à la lecture", d["moyenne"] == 12.17, f"moyenne={d['moyenne']}")
        ok("GN1 la moyenne n'est PAS stockée",
           "moyenne" not in json.loads(frappe.db.get_value("Admission Applicant", ada, "notes_concours")),
           "notes_concours = notes brutes seules")

        # GN4 (MAÎTRE) — éliminatoire SIGNALÉ épreuve par épreuve, JAMAIS appliqué
        r = staff.saisir_note_concours(dossier_id=bob, notes={"maths": 18, "physique": 17, "culture": 5})
        ok("GN4 saisie (culture=5, éliminatoire)", r.get("ok"), "")
        d = staff.get_dossier(dossier_id=bob)["data"]["notes"]
        ok("GN4 signal éliminatoire sur l'épreuve culture", d["eliminatoire"] == ["culture"],
           f"eliminatoire={d['eliminatoire']}, moyenne={d['moyenne']}")
        ok("GN4 signalé MALGRÉ une bonne moyenne (15.5)", d["moyenne"] == 15.5, f"moyenne={d['moyenne']}")
        ok("GN4 AUCUNE décision automatique : statut inchangé (ETU)",
           frappe.db.get_value("Admission Applicant", bob, "status") == "ETU", "status=ETU")

        # GN8 — ABSENT ≠ 0 : pas de moyenne, pas de signal
        r = staff.saisir_note_concours(dossier_id=cora, notes={"__absent__": True})
        ok("GN8 saisie ABS", r.get("ok") and r["data"]["absent"], "absent=True")
        d = staff.get_dossier(dossier_id=cora)["data"]["notes"]
        ok("GN8 absent : aucune moyenne (jamais 0)", d["absent"] and d["moyenne"] is None, f"moyenne={d['moyenne']}")
        ok("GN8 absent : aucun signal éliminatoire", d["eliminatoire"] == [], "")

        # GN2 — VERROU : une note existe → coefficients verrouillés (endpoint + 2ᵉ couche validate)
        frappe.set_user(RESP)
        r = staff.set_exam_coefficients(session_id=SESSION, coefficients={"maths": 1, "physique": 1, "culture": 1})
        ok("GN2 verrou : coefficients refusés après 1ʳᵉ note (endpoint)",
           not r.get("ok") and r["error"]["code"] == "COEF_LOCKED", r.get("error", {}).get("code"))
        try:
            frappe.set_user("Administrator")
            s = frappe.get_doc("Admission Session", SESSION)
            s.exam_coefficients = json.dumps({"maths": 1, "physique": 1, "culture": 1})
            s.save(ignore_permissions=True)
            lock2 = False
        except frappe.ValidationError:
            lock2 = True
        ok("GN2 verrou 2ᵉ couche : validate() session refuse le changement", lock2, "ValidationError")

        # GN5 / GN7 — MÊME ordre que la feuille d'émargement (alpha) : roster == convoqués == export
        from admission.api.exam_documents import _convoques_of_session
        _, convoques = _convoques_of_session(SESSION)
        ordre_emargement = [c["nom"] for c in convoques]
        frappe.set_user(ADM)
        roster = staff.list_notes_roster(session_id=SESSION)["data"]["candidats"]
        ok("GN5 liste écran = ordre alphabétique de l'émargement",
           [c["nom"] for c in roster] == ordre_emargement, str(ordre_emargement))
        exp = staff.export_notes_template(session_id=SESSION)["data"]["csv"]
        export_names = [ln.split(",")[2] for ln in exp.strip().splitlines()[1:]]
        ok("GN7 export CSV = même ordre", export_names == ordre_emargement, str(export_names))

        # GN6 — import : aperçu 2 paniers + COMPTE d'abord ; rapprochement par identifiant, jamais par nom
        rows = [
            {"dossier_id": dan, "maths": 12, "physique": 11, "culture": 10},   # valide
            {"dossier_id": eve, "maths": 25, "physique": 11, "culture": 10},   # hors plage → problème
            {"nom": "Ada Alpha", "maths": 9, "physique": 9, "culture": 9},     # nom seul → non rapproché
            {"dossier_id": "CAN-ZZZ-9999", "maths": 8, "physique": 8, "culture": 8},  # inconnu → problème
        ]
        prev = staff.import_notes_preview(session_id=SESSION, rows=rows)["data"]
        ok("GN6 aperçu : compte à écrire annoncé d'abord", prev["compte_a_ecrire"] == 1,
           f"compte_a_ecrire={prev['compte_a_ecrire']}")
        ok("GN6 aperçu : 3 problèmes signalés", len(prev["problemes"]) == 3, f"problemes={len(prev['problemes'])}")
        ok("GN6 aperçu n'écrit RIEN", not frappe.db.get_value("Admission Applicant", dan, "notes_concours"),
           "dan encore vierge")
        res = staff.saisir_notes_masse(session_id=SESSION, rows=rows)["data"]
        ok("GN6 écriture PARTIELLE : seul le valide est écrit", res["ecrits"] == 1, f"ecrits={res['ecrits']}")
        ok("GN6 dan reçoit bien SA note (rapproché par identifiant)",
           json.loads(frappe.db.get_value("Admission Applicant", dan, "notes_concours"))["maths"] == 12.0, "")

        # GN8 — validation Responsable inchangée + précondition décision
        frappe.set_user(RESP)
        r = staff.valider_notes_concours(dossier_id=ada)
        ok("GN8 validation Responsable (maker-checker inchangé)",
           r.get("ok") and frappe.db.get_value("Admission Applicant", ada, "notes_validated") == 1,
           "notes_validated=1")

        # ── DURCISSEMENT D1–D4 : le circuit de validation est UNIQUE quelle que soit la voie ──
        print("--- DURCISSEMENT D1–D4 ---")
        # D1 — une note IMPORTÉE (CSV) est EN ATTENTE, comme une note manuelle ; le Responsable
        # n'est PAS contourné (la décision reste bloquée tant qu'il n'a pas validé).
        frappe.set_user(ADM)
        staff.saisir_notes_masse(session_id=SESSION,
                                 csv_text=f"dossier_id,maths,physique,culture,absent\n{dan},12,11,10,\n")
        ok("D1 note importée (CSV) = NON validée (pending, comme la saisie manuelle)",
           frappe.db.get_value("Admission Applicant", dan, "notes_validated") == 0, "notes_validated=0")
        frappe.set_user(RESP)
        r = staff.mark_admissible(dossier_id=dan)
        ok("D1 décision BLOQUÉE sur note importée non validée (Responsable non contournable)",
           not r.get("ok") and r["error"]["code"] == "NOTES_NOT_VALIDATED", r.get("error", {}).get("code"))
        r = staff.valider_notes_concours(dossier_id=dan)
        ok("D1 le point de passage UNIQUE valide la note importée",
           r.get("ok") and frappe.db.get_value("Admission Applicant", dan, "notes_validated") == 1, "validée")

        # D2 — un IMPORT sur des notes DÉJÀ VALIDÉES remet en attente
        frappe.set_user(ADM)
        res = staff.saisir_notes_masse(
            session_id=SESSION,
            csv_text=f"dossier_id,maths,physique,culture,absent\n{dan},10,10,10,\n")["data"]
        ok("D2 ré-import (CSV) d'un dossier déjà validé accepté", res["ecrits"] == 1, f"ecrits={res['ecrits']}")
        ok("D2 remet la note EN ATTENTE (notes_validated=0)",
           frappe.db.get_value("Admission Applicant", dan, "notes_validated") == 0, "re-pending")

        # D4 — la saisie GROUPÉE À L'ÉCRAN (rows) suit le MÊME chemin (saisir_notes_masse) → non validée
        frappe.set_user(ADM)
        res = staff.saisir_notes_masse(
            session_id=SESSION, rows=[{"dossier_id": eve, "maths": 11, "physique": 12, "culture": 13}])["data"]
        ok("D4 saisie groupée écran écrit", res["ecrits"] == 1, "")
        ok("D4 note groupée écran = NON validée (circuit unique, comme import et unitaire)",
           frappe.db.get_value("Admission Applicant", eve, "notes_validated") == 0, "pending")

        # ── D3 option B : VALIDATION PAR LOT (3 exigences + éliminatoire compté/annoncé/validé) ──
        print("--- D3-LOT : validation par lot ---")
        # État : ada validée (GN8) ; bob(élim culture=5)/cora(ABS)/dan(re-import D2)/eve(D4) pending ; fred sans notes.
        # Requirement 2 — aperçu EXPLICITE (combien, et combien à signal éliminatoire) AVANT de confirmer
        frappe.set_user(RESP)
        prev = staff.valider_notes_masse_preview(session_id=SESSION)["data"]
        ok("D3-LOT aperçu annonce le nombre à valider (4 : bob/cora/dan/eve)",
           prev["a_valider"] == 4, f"a_valider={prev['a_valider']}")
        ok("D3-LOT aperçu annonce combien à signal éliminatoire (bob), comptés jamais écartés",
           prev["avec_signal"] == 1, f"avec_signal={prev['avec_signal']}")
        # Requirement 1 — Direction EXCLUE (pas de porte dérobée)
        frappe.set_user(DIR)
        try:
            staff.valider_notes_masse(session_id=SESSION); dir_blocked = False
        except frappe.PermissionError:
            dir_blocked = True
        ok("D3-LOT Direction EXCLUE de la validation par lot (requirement 1)", dir_blocked, "PermissionError")
        # Le Responsable valide en lot, après revue d'ensemble
        frappe.set_user(RESP)
        res = staff.valider_notes_masse(session_id=SESSION)["data"]
        ok("D3-LOT Responsable valide en lot (4 dossiers en un geste)", res["valides"] == 4, f"valides={res['valides']}")
        ok("D3-LOT dossier ÉLIMINATOIRE validé comme les autres (jamais écarté, ≠ décision d'admission)",
           frappe.db.get_value("Admission Applicant", bob, "notes_validated") == 1 and res["avec_signal"] == 1,
           f"bob validé, avec_signal={res['avec_signal']}")
        # Requirement 3 — jamais un dossier SANS notes ; jamais un dossier DÉJÀ validé
        ok("D3-LOT requirement 3 : dossier SANS notes NON validé (fred)",
           frappe.db.get_value("Admission Applicant", fred, "notes_validated") == 0
           and not frappe.db.get_value("Admission Applicant", fred, "notes_concours"), "fred intact")
        again = staff.valider_notes_masse(session_id=SESSION)["data"]
        ok("D3-LOT requirement 3 : rien à re-valider (déjà validés non re-touchés)",
           again["valides"] == 0, f"valides={again['valides']}")

        # ── A08 / A09 : le verrou protège une pondération EXISTANTE, pas la présence d'une note ──
        print("--- A08/A09 : verrou coefficients ---")
        S2 = SESSION + "-A08"
        frappe.set_user("Administrator")
        _mk_session(S2)
        a08 = _mk_candidat("Gil", "Test", "26260099", session=S2)
        # résidu de note SANS coefficients (simule l'ancien format libre pré-canonique — bypass endpoint)
        frappe.db.set_value("Admission Applicant", a08, "notes_concours", '{"Maths": 14}')
        frappe.db.commit()
        ok("A08 note résiduelle SANS coefficients ne fige RIEN (rien à protéger)",
           not staff.coefficients_frozen(S2), "coefficients_frozen=False")
        frappe.set_user(RESP)
        r = staff.set_exam_coefficients(session_id=S2, coefficients={"maths": 3, "physique": 2, "culture": 1})
        ok("A08 1ʳᵉ pose des coefficients PERMISE malgré le résidu (fin du cul-de-sac)",
           r.get("ok"), str(r.get("data", {}).get("coefficients")) if r.get("ok") else r.get("error"))
        # A09 — posés + une note existe → figé ; modification refusée SERVEUR (appel direct)
        ok("A09 après pose, la note fige la pondération", staff.coefficients_frozen(S2), "frozen=True")
        r = staff.set_exam_coefficients(session_id=S2, coefficients={"maths": 1, "physique": 1, "culture": 1})
        ok("A09 modification refusée serveur dès qu'une note existe (appel direct endpoint)",
           not r.get("ok") and r["error"]["code"] == "COEF_LOCKED", r.get("error", {}).get("code"))
        try:
            frappe.set_user("Administrator")
            s2 = frappe.get_doc("Admission Session", S2)
            s2.exam_coefficients = '{"maths": 1, "physique": 1, "culture": 1}'
            s2.save(ignore_permissions=True); a09_l2 = False
        except frappe.ValidationError:
            a09_l2 = True
        ok("A09 2ᵉ couche : validate() session refuse la modification (édition directe)", a09_l2, "ValidationError")

        print(f"\n=== RECETTE NOTES-CONCOURS : {R['pass']} PASS / {R['fail']} FAIL ===")
    finally:
        _cleanup()
        print("Cleanup : fixtures purgées.")
