# admission-lanem-api — consignes pour les agents

**Rôle :** back admission : app Frappe v15 `admission` 1.0.0 — **en production**
**Domaine propriétaire :** admission · **Bench :** admission
**Branche principale :** `main` — on n'y pousse jamais directement : tout passe par une demande de fusion.
**Registres du domaine (corpus `si-lanem-corpus`) :** `admission/DEC-ADMISSION.md` · `admission/CORRECTIONS-ADMISSION.md`
· transverse : `_commun/M02-Registre-Decisions.md` · banc : `campus/gestion-contruction/BANC-DEV.md`

## 1. Avant d'écrire une ligne

1. **Pas de mandat, pas de code.** Un mandat est un fichier `MANDAT-<ID>.md` lu **sur GitHub**
   (`si-lanem-corpus/_commun/mandats/`), jamais un texte collé dans la conversation (DEC-ORG-01).
2. **Write-set = contrat.** Tu n'écris que dans les chemins listés par le mandat. Besoin d'un autre fichier :
   **STOP**, demande d'extension à l'architecte (DEC-ORG-04). Un garde-fou refuse l'écriture hors write-set.
3. **Cinq phases, deux pauses.** Reconnaissance (lecture seule) → Plan → ⏸️ PAUSE 1 → Exécution →
   ⏸️ PAUSE 2 (= la demande de fusion) → Vérification → Rapport.
4. **Branche `mandat/<ID>`**, une demande de fusion vers `main`. Tu ne fusionnes jamais toi-même (DEC-ORG-03),
   et tu ne supprimes aucune branche distante : fusion et suppression des branches fusionnées sont des gestes de
   la MOA, sauf si le mandat dit autre chose (V-LEARN-MEP30-05, V-LEARN-MEP30-06).
5. **Fin de session = tout est poussé.** Un commit qui ne quitte pas la machine n'existe pas (DEC-ORG-02).
   C'est ainsi que le back M1/M1c a été perdu le 22/10/2026.
6. **Avant la phase 1 : `gh auth status`.** Si tu ne peux pas écrire sur GitHub, STOP tout de suite, pas à la
   pause 1 (V-LEARN-MEP30-01).
7. **Le rapport vit sur sa branche du corpus dès la phase 1**, jamais dans un brouillon local : un brouillon de
   session s'efface d'un jour à l'autre (V-LEARN-MEP30-03).

## 2. Secrets — DEC-346 (`_commun/PROCEDURE-SECRETS.md`)

- Tu ne reçois **jamais** de mot de passe. Pour prouver un clic : tu te fabriques une **clé d'API Frappe**
  sur un compte de test `rf-g-*`, tu la révoques en fin de vague, et tu inscris les deux gestes à `BANC-DEV.md`
  (date, compte, mandat — **jamais la valeur**).
- Aucun secret dans un commit, un rapport, un message. Un secret commité est **réputé compromis** : STOP, incident.
- Aucun accès recette / production depuis un banc de développement.

## 3. Ce dépôt

| Élément | Valeur constatée |
|---|---|
| Apps / paquets | app `admission` seule (`pyproject.toml`, flit) — dépôt d'app, pas de bench |
| Suites de tests | `bench --site <site> run-tests --app admission` ; un module : `bench --site <site> run-tests --module admission.tests.<module>` (constatée, non exécutée) · E2E `node --env-file=.env admission/tests/e2e/e2e_management.mjs` et `e2e_candidat.mjs` : **visent la recette**, interdits depuis un banc (§2) |
| Baseline de référence | à re-mesurer (MEP-11) — aucune baseline valide au 10/11/2026 |
| Dépendances inter-dépôts | expose aux fronts `admission.api.public.*`, `local_promo.*`, `staff.*`, `calendar*`, `admin_*`, `account.*`, `health.*` (contrat : `admission/contracts/schemas/`) · appelle `emela-campus-back` (`portal_app.api.admission_bridge.receive_inscription`, `admission_catalogue`, `admission_levels`, `external.v1.person_api.ensure_person`) : absents du back au 10/11/2026 · appelle `emela-gestion-back` (`university_finance.api.admission_*`, `fee_catalog`, `scholarship_catalog`) · FedaPay (webhook `admission.api.webhook.payment`) |
| Pièges connus | DEC-331 (version unique, tag posé par l'architecte) · DEC-276 (fail-loud sur les secrets serveur) · DEC-272 (cascade de paiement unique) · DEC-253 (CORS à origines exactes) · ADM-DEBT-27 (`create_dossier` dépend de `ensure_person` campus) · ADM-DEBT-55 (HTTPS hors dev pour campus/UF) · ADM-DEBT-56 (`developer_mode=0` en production) · V-ADM-LEARN-03 (Doctype à Links : `test_records = []`) |

## 4. Règles apprises (à respecter sans les redécouvrir)

**Tous les dépôts**
- V-LEARN-MEP30-04 · pour relever les règles d'un `.gitignore`, lire aussi les négations `!` placées plus bas

**Frappe (dépôt bench)**
- V1-LEARN-01 · chemin d'un Doctype dans une app : `apps/<app>/<app>/<module>/doctype/<dt>/`
- V2-LEARN-01 · les `.json` de `<app>/<app>/fixtures/` sont chargés au `bench migrate` ; fixtures de test → `<app>/<app>/tests/fixtures/`
- V2-LEARN-02 · relancer `bench migrate` après tout déplacement de `.json`
- V3-LEARN-01 · Doctype déplacé entre apps : supprimer le JSON source (sinon l'ordre `apps.txt` décide du module)
- V3-LEARN-02 · un Doctype avec Links peut amplifier la cascade `make_test_records`
- STAB-BH-LEARN-01 · helpers de test : nommage dynamique, jamais d'autoname statique
- STAB-RESIDU-LEARN-01/02/03 · nettoyer les Doctypes à clé d'idempotence en `setUp` ; contourner le throttle de création d'utilisateurs par helper test-only ; mocker **tous** les axes temporels
- DEC-161/162/163 · sites `*.failed-*` nettoyés sous 24 h ; `apps.txt` synchronisés ; `site_config.json` jamais versionné
- DEC-171 · nouveau module = `LaNEM <Domaine>`
- Graphe des apps **acyclique** (DEC-160) : n'ajouter aucun cycle ; pas de test automatique dans ce dépôt

## 5. Le rapport

Format standard (Reconnaissance · Plan · Exécution · Audits automatiques · Vérifications · Tests · Anomalies ·
Apprentissages). Compteurs **exacts** et comparés à la baseline ; « préexistant » distingué de « causé ».
Il se dépose dans le corpus, **par demande de fusion**, au chemin fixé par le mandat.

**Commits** : identité git habituelle du dépôt. Par défaut, garde la ligne de co-auteur de ton outil : on doit
savoir qu'un agent a écrit. Un mandat qui fixe un message exact dit s'il la veut (`VERDICT-MANDAT-ZERO-PC2.md` §3).

## 6. Interdits

Pousser sur `main` · fusionner · supprimer une branche distante · écrire hors write-set · stocker ou
afficher un secret · toucher la production · réécrire l'historique (`--force`) · déclarer une gate close
(c'est l'architecte).
