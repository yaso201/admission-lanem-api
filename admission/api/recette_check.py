"""Gate de RECETTE — vérificateur exécutable de la checklist (lecture seule).

La recette du parcours candidat est essentiellement une affaire de CONFIG + données
de référence (audit parcours : « recette = config »). Ce module rend la checklist
EXÉCUTABLE et re-jouable sur n'importe quel site (recette, préprod, prod) :

    bench --site <site> execute admission.api.recette_check.run

Chaque contrôle est PASS / FAIL / WARN avec le remède exact (clé site_config, action
OPS). Aucune écriture, aucun secret affiché (présence seulement). Document compagnon :
specifications/RECETTE-CHECKLIST-ADMISSION.md (actions humaines hors-config incluses :
KkiaPay réel, SMS, DNS/HTTPS, juridique).
"""

import os

import frappe

PASS, FAIL, WARN = "PASS", "FAIL", "WARN"

LEGAL_TYPES = ("PRIVACY_POLICY", "CGV", "REFUND_POLICY", "DATA_TRANSFER_CONSENT")
STAFF_ROLES = ("Admission Administratif", "Admission Responsable", "Admission Direction")
SCHEDULER_JOBS = (  # hooks.py scheduler_events.daily — garde anti-dérive
    "admission.api.fee_catalog_sync.sync_fee_catalog",
    "admission.api.scholarship_sync.sync_scholarship_catalog",
    "admission.api.level_sync.sync_levels",
    "admission.api.retention.scheduled_retention_run",
    "admission.api.notify_uf.redrive_uf_notifications",
    "admission.api.public.expire_stale_online_pending",
    "admission.api.notifications.remind_dormant_sop_dossiers",
    "admission.api.retention.notify_expiring_drafts",
    "admission.api.bridge.redrive_bridge_notifications",  # LOT P4
)


def _https(url):
    return bool(url) and url.lower().startswith("https://")


# ── Contrôles (id, libellé, fonction → (statut, détail/remède)) ────────────────


def _check_secret(key, why):
    def check():
        if frappe.conf.get(key):
            return PASS, "posé (valeur non affichée)"
        return FAIL, f"site_config `{key}` absent — {why}"
    return check


def _check_url(key, why, required=True):
    def check():
        url = frappe.conf.get(key)
        if not url:
            return (FAIL if required else WARN), f"site_config `{key}` absent — {why}"
        if not _https(url):
            return FAIL, f"`{key}` = {url} : http en clair (gate PII DAT-2 bloquera hors developer_mode)"
        return PASS, url
    return check


def _check_dev_mode():
    if frappe.conf.get("developer_mode"):
        return FAIL, "developer_mode actif — à retirer en recette (il neutralise la gate PII https)"
    return PASS, "désactivé"


def _check_kkiapay_keys():
    """LOT KKIAPAY : sans les 3 clés marchand, la re-vérification serveur est impossible
    → le webhook rejette TOUT (fail-closed) → aucun paiement online ne se confirme."""
    missing = [k for k in ("kkiapay_public_key", "kkiapay_private_key", "kkiapay_secret_key")
               if not frappe.conf.get(k)]
    if missing:
        return FAIL, f"clés manquantes : {', '.join(missing)} — paiement online inopérant (fail-closed)"
    env = "SANDBOX" if frappe.conf.get("kkiapay_sandbox") else "LIVE"
    return PASS, f"3 clés posées (environnement {env})"


def _check_kkiapay_mode():
    if frappe.conf.get("kkiapay_mock"):
        return FAIL, "kkiapay_mock actif — la vérification provider est SIMULÉE (DEV uniquement)"
    if frappe.conf.get("kkiapay_sandbox"):
        return WARN, "kkiapay_sandbox actif — transactions de TEST (ok recette, à retirer en prod)"
    return PASS, "mode LIVE"


def _check_expose_dev_otp():
    if frappe.conf.get("expose_dev_otp"):
        return FAIL, "expose_dev_otp actif — DIVULGUE le code OTP dans la réponse API"
    return PASS, "absent"


def _check_allow_tests():
    if frappe.conf.get("allow_tests"):
        return WARN, "allow_tests actif — à retirer hors environnements de test"
    return PASS, "absent"


def _check_cors():
    origins = frappe.conf.get("allow_cors") or []
    if isinstance(origins, str):
        origins = [origins]
    if not origins:
        return FAIL, "allow_cors absent — le portail candidat (autre origine) sera bloqué"
    bad = [o for o in origins if "localhost" in o or "127.0.0.1" in o or o == "*"]
    if bad:
        return FAIL, f"allow_cors contient des origines DEV/joker : {bad} — restreindre à l'URL https du portail"
    return PASS, str(origins)


def _check_outgoing_email():
    rows = frappe.get_all("Email Account",
                          filters={"enable_outgoing": 1, "default_outgoing": 1},
                          pluck="name")
    if not rows:
        return FAIL, "aucun Email Account sortant par défaut — OTP/décisions/reçus ne partiront pas (SMTP réel à configurer)"
    return PASS, f"sortant par défaut : {rows[0]}"


def _check_rib_pdf():
    """LOT RIB-SETTINGS : le RIB vit dans Admission Settings (rôle Admission Finance)."""
    v = frappe.db.get_value("Admission Settings", "Admission Settings",
                            ["rib_iban", "rib_pdf", "rib_version"], as_dict=True) or {}
    if not v.get("rib_iban"):
        return FAIL, "compte d'encaissement NON saisi (Admission Settings, rôle Admission Finance) — canal virement masqué partout"
    if not v.get("rib_pdf"):
        return WARN, "RIB saisi mais PDF officiel absent — mails virement sans pièce jointe"
    fname = frappe.db.get_value("File", {"file_url": v.rib_pdf}, "name")
    if not fname:
        return FAIL, f"rib_pdf pointe un File inexistant ({v.rib_pdf}) — rotation à rejouer"
    return PASS, f"RIB v{v.rib_version} (IBAN + PDF versionné)"


def _check_logo_asset():
    bench_root = os.path.abspath(os.path.join(frappe.get_app_path("admission"), "..", "..", ".."))
    link = os.path.join(bench_root, "sites", "assets", "admission", "images", "lanem-seal.png")
    if os.path.exists(link):
        return PASS, "/assets/admission/images/lanem-seal.png résout"
    return FAIL, "symlink assets absent — `bench build --app admission` (logo des mails en 404 sinon)"


def _check_legal_documents():
    missing = []
    for doc_type in LEGAL_TYPES:
        if not frappe.get_all("Admission Legal Document",
                              filters={"document_type": doc_type, "is_active": 1}, limit=1):
            missing.append(doc_type)
    if missing:
        return FAIL, f"textes légaux ACTIFS manquants : {missing} — create_dossier/paiements renverront 503"
    return PASS, "4 types actifs (PRIVACY/CGV/REFUND/DATA_TRANSFER)"


def _check_open_sessions():
    sessions = frappe.get_all("Admission Session", filters={"is_open": 1},
                              fields=["name", "programme_code"])
    if not sessions:
        return FAIL, "aucune Admission Session ouverte — le tunnel n'a rien à proposer"
    return PASS, f"{len(sessions)} session(s) ouverte(s)", sessions


def _check_level_mirror():
    sessions = frappe.get_all("Admission Session", filters={"is_open": 1},
                              fields=["name", "programme_code"])
    if not frappe.db.count("Admission Level Mirror"):
        return FAIL, "miroir niveaux VIDE — create_dossier impossible (LEVEL_REQUIRED). Sync level_sync (ADM-DEBT-65) ou seed contrôlé"
    orphans = [s.programme_code for s in sessions
               if not frappe.get_all("Admission Level Mirror",
                                     filters={"program_code": s.programme_code}, limit=1)]
    if orphans:
        return FAIL, f"sessions ouvertes SANS niveaux campus : {sorted(set(orphans))} (ADM-DEBT-66)"
    return PASS, "chaque session ouverte a ≥1 niveau"


def _check_fee_catalog():
    n = frappe.db.count("Admission Fee Catalog")
    if not n:
        return FAIL, "catalogue des frais VIDE — montants frais 1/2 non résolubles (sync UF fee_catalog_sync)"
    return PASS, f"{n} entrées"


def _check_staff_roles():
    problems = []
    for role in STAFF_ROLES:
        if not frappe.db.exists("Role", role):
            problems.append(f"rôle absent : {role}")
            continue
        users = frappe.get_all("Has Role",
                               filters={"role": role, "parenttype": "User"}, pluck="parent")
        actives = [u for u in users
                   if u not in ("Administrator", "Guest")
                   and frappe.db.get_value("User", u, "enabled")]
        if not actives:
            problems.append(f"aucun utilisateur actif : {role}")
    if problems:
        return FAIL, " ; ".join(problems)
    return PASS, "3 rôles + ≥1 utilisateur actif chacun"


def _check_scheduler():
    from frappe.utils.scheduler import is_scheduler_disabled
    if is_scheduler_disabled(verbose=False):
        return FAIL, "scheduler désactivé — relances SOP/préavis purge/rétention/syncs ne tourneront pas"
    hooks = frappe.get_hooks("scheduler_events") or {}
    daily = hooks.get("daily") or []
    missing = [j for j in SCHEDULER_JOBS if j not in daily]
    if missing:
        return FAIL, f"jobs daily attendus absents des hooks : {missing}"
    return PASS, f"actif, {len(SCHEDULER_JOBS)} jobs daily câblés"


def _check_bridge_reachable():
    """LOT P3 — sonde AUTHENTIFIÉE du pont INS, sans effet (payload vide → erreur métier
    person_id AVANT toute écriture). Prouve : campus joignable + clé acceptée + scope posé."""
    from admission.api._config import _get_campus_config
    config = _get_campus_config()
    if not config:
        return FAIL, "campus_base_url/campus_api_token absents (voir SEC-campus / URL-campus)"
    import requests
    try:
        resp = requests.post(
            config["url"].rstrip("/") + "/api/method/portal_app.api.admission_bridge.receive_inscription",
            json={}, headers={"Content-Type": "application/json", "X-API-Key": config["token"]},
            timeout=8,
        )
    except requests.RequestException as exc:
        return FAIL, f"campus injoignable : {exc}"
    if resp.status_code in (401, 403):
        return FAIL, f"clé API refusée (HTTP {resp.status_code}) — vérifier le client External API Client + scope admission_bridge"
    if resp.status_code == 200:
        msg = (resp.json().get("message") or {})
        if msg.get("status") == "error" and "person_id" in str(msg.get("message", "")):
            return PASS, "pont authentifié (réponse métier attendue sans person_id)"
        return WARN, f"réponse inattendue : {str(msg)[:120]}"
    return FAIL, f"HTTP {resp.status_code} inattendu"


def _check_retention_policy():
    # Les défauts code (90j BRO…) s'appliquent si le singleton est vide — WARN informatif.
    if not frappe.db.exists("DocType", "Admission Retention Policy"):
        return FAIL, "doctype Admission Retention Policy absent"
    if frappe.db.get_single_value("Admission Retention Policy", "abandoned_bro_days"):
        return PASS, "durées posées explicitement"
    return WARN, "singleton vide — défauts code appliqués (BRO 90 j) ; à valider explicitement en recette"


CHECKS = [
    # ── Secrets (présence seulement) ──
    ("SEC-token", "Secret HMAC tokens/OTP", _check_secret(
        "token_hmac_secret", "OTP et tracking fail-loud sans lui (DEC-276) ; MÊME valeur côté campus")),
    ("SEC-webhook", "Secret webhook paiement", _check_secret(
        "admission_payment_webhook_secret", "le webhook KkiaPay sera REJETÉ (fail-closed SEC-2)")),
    ("SEC-campus", "Token API campus (ensure_person/bridge)", _check_secret(
        "campus_api_token", "create_dossier → PERSON_RESOLUTION_FAILED 503 ; client « External API Client » côté campus")),
    ("SEC-uf-key", "Clé API UF", _check_secret("uf_api_key", "notifications UF en échec (redrive s'accumulera)")),
    ("SEC-uf-secret", "Secret API UF", _check_secret("uf_api_secret", "idem uf_api_key")),
    ("SEC-kkiapay", "Clés marchand KkiaPay (3)", _check_kkiapay_keys),
    ("MODE-kkiapay", "Mode KkiaPay (mock interdit, sandbox toléré)", _check_kkiapay_mode),
    # ── URLs / transport (DAT-2 : https obligatoire hors developer_mode) ──
    ("URL-campus", "campus_base_url en https", _check_url(
        "campus_base_url", "résolution Person impossible (création de dossier bloquée)")),
    ("URL-uf", "uf_backoffice_url en https", _check_url(
        "uf_backoffice_url", "synchronisations catalogues + notifications UF inopérantes")),
    ("URL-portal", "candidate_portal_url en https", _check_url(
        "candidate_portal_url", "les liens des mails (reprise, suivi, légal) pointeront sur localhost")),
    ("URL-student", "campus_student_portal_url", _check_url(
        "campus_student_portal_url", "CTA du mail d'inscription → défaut https://campus.lanem.bj", required=False)),
    # ── Posture du site ──
    ("MODE-dev", "developer_mode désactivé", _check_dev_mode),
    ("MODE-otp", "expose_dev_otp absent", _check_expose_dev_otp),
    ("MODE-tests", "allow_tests absent", _check_allow_tests),
    ("MODE-cors", "allow_cors = origine(s) prod", _check_cors),
    # ── Mails ──
    ("MAIL-smtp", "Email Account sortant par défaut", _check_outgoing_email),
    ("DATA-rib", "Compte d'encaissement (Admission Settings)", _check_rib_pdf),
    ("MAIL-logo", "Asset logo (symlink bench build)", _check_logo_asset),
    # ── Données de référence ──
    ("DATA-legal", "4 textes légaux actifs", _check_legal_documents),
    ("DATA-session", "Session(s) d'admission ouverte(s)", lambda: _check_open_sessions()[:2]),
    ("DATA-levels", "Miroir niveaux couvre les sessions", _check_level_mirror),
    ("DATA-fees", "Catalogue des frais non vide", _check_fee_catalog),
    ("INT-bridge", "Pont INS authentifié (sonde sans effet)", _check_bridge_reachable),
    # ── Comptes / exploitation ──
    ("OPS-roles", "Rôles staff + utilisateurs actifs", _check_staff_roles),
    ("OPS-scheduler", "Scheduler actif + jobs câblés", _check_scheduler),
    ("OPS-retention", "Politique de rétention explicite", _check_retention_policy),
]


def run():
    """Exécute la checklist. Lecture seule. Renvoie {pass, warn, fail, results}."""
    results, counts = [], {PASS: 0, WARN: 0, FAIL: 0}
    for check_id, label, fn in CHECKS:
        try:
            out = fn()
            status, detail = out[0], out[1]
        except Exception as exc:  # un contrôle qui crashe est un FAIL, pas un faux PASS
            status, detail = FAIL, f"contrôle en erreur : {exc}"
        counts[status] += 1
        results.append({"id": check_id, "label": label, "status": status, "detail": detail})
        icon = {"PASS": "✓", "WARN": "~", "FAIL": "✗"}[status]
        print(f"[{icon} {status}] {check_id:<14} {label} — {detail}")
    verdict = "GO" if counts[FAIL] == 0 else "NO-GO"
    print(f"\nRECETTE {verdict} — {counts[PASS]} PASS / {counts[WARN]} WARN / {counts[FAIL]} FAIL "
          f"(site {frappe.local.site})")
    if counts[FAIL]:
        print("Remèdes : voir specifications/RECETTE-CHECKLIST-ADMISSION.md")
    return {"verdict": verdict, **{k.lower(): v for k, v in counts.items()}, "results": results}
