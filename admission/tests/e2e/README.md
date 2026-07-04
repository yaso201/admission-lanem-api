# E2E versionnés — admission (ENABLER-FIXTURES-E2E)

Outillage de **preuve** réutilisable (vagues 3-5 + audit management). Il ne modifie **aucun
comportement applicatif** : il rend éprouvables les actions post-SOU sur le bundle **live**.

Pattern éprouvé (17 assertions en 3c-3), désormais **versionné et paramétré** :
- session Frappe mintée server-side (bypass 2FA) pour un compte staff donné ;
- Chrome headless (puppeteer) avec le cookie `sid` injecté (cross-subdomain same-site) ;
- navigation + assertions DOM sur `staff-rec.lanem.bj`.

**Pas de nouveau framework** (ni Playwright ni Cypress) — on capitalise sur puppeteer + `bench console`.

## Fichiers
| Fichier | Rôle |
|---|---|
| `mint_session.py` | mint d'une session pour `MINT_USER` (défaut `admin.admissions@lanem.bj`) → `MINTED_SID` |
| `lib_session.mjs` | `mintSid(user)` + `launchAuthed(sid)` — chemins/secrets **par environnement** |
| `demo_mark_admissible.mjs` | démo : admettre (Responsable) un dossier **ETU** fixture depuis l'UI /dossier |
| `.env.example` | modèle des variables (valeurs factices) — copier en `.env` (gitignored) |

## Prérequis
- `puppeteer` installé localement ; renseigner `PUPPETEER_PATH`.
- Accès SSH recette (clé `.pem`) — **jamais versionnée** ; renseigner `FIXTURE_SSH_KEY`/`FIXTURE_SSH_HOST`.
- Copier `.env.example` → `.env` et compléter. **Ne jamais committer `.env` ni la clé.**

## Fabriquer une fixture puis jouer la démo
```bash
# 1) Construire un dossier ETU par CHEMIN MÉTIER (server-side, recette) — imprime FIXTURE_ID::<id>
bench --site api-admission-rec.lanem.bj execute \
  admission.tests.fixtures.recette_fixtures.build_one --kwargs "{'target':'ETU'}"

# 2) Renseigner FIXTURE_ID=<id> dans .env, puis lancer la démo (machine locale) :
node --env-file=.env admission/tests/e2e/demo_mark_admissible.mjs

# 3) Purger les fixtures (baseline restaurée) :
bench --site api-admission-rec.lanem.bj execute \
  admission.tests.fixtures.recette_fixtures.purge
```

## Fixtures multi-états (`admission/tests/fixtures/recette_fixtures.py`)
`build_to(target)` construit **par chemin métier** (vraies API) jusqu'à `target` ∈
`{BRO, SOP, SOU, ETU, ADM, ACC, INS, REF}`. Tag email `fixture-<runid>@e2e.lanem.test` (domaine
factice). `purge()` supprime tout dossier taggé + son courrier. `status_counts()` = compteurs par
statut (preuve baseline avant==après). Aucun état gardé n'est seedé (invariants R3/3c respectés).

## B-3 — suite de non-régression durable (UI candidat + management exhaustive)

Deux harnais navigateur pilotent les **bundles LIVE** (jamais un harness réordonné) :

| Fichier | Rôle |
|---|---|
| `e2e_management.mjs` | 20 transitions métier par l'UI staff (`/dossier`, `/gestion-sessions`) : clic → (modale) → toast → statut. Multi-rôles (admin/resp/dir), 1 session mintée par rôle. `B3_FILTER=nom1,nom2` cible un sous-ensemble. |
| `e2e_candidat.mjs` | Tunnel candidat de bout en bout (`/identite` create → OTP → `/pieces` → `/paiement` SOP → `/suivi`) + re-soumission après rejet. Interception UPLOAD-3G (hors `*.lanem.bj` bloqué). |

Lancer :
```bash
node --env-file=.env admission/tests/e2e/e2e_management.mjs   # Bloc 2
node --env-file=.env admission/tests/e2e/e2e_candidat.mjs     # Bloc 1
```

Points d'entrée fixtures ajoutés (server-side, base loopback `ADMISSION_FIXTURE_BASE=http://127.0.0.1:8000`
+ en-tête Host — le 403 Cloudflare interdit le loopback via l'URL publique) :
- `ui_context` — session/niveau/date-bac d'amorçage du tunnel candidat.
- `emit_otp(dossier)` — code OTP lu dans l'Email Queue (saisi par le navigateur).
- `build_ui(kind)` — préconditions UI par chemin métier : `SOU_VERIFIED`, `SOP_PENDING`, `ETU_COND`,
  `ACO_DIPLOMA`, `ACO_VERIF`, `ACC_F2_PENDING`, `ACC_F2_PAID` (sinon délègue à `build_to`).
- `open_disposable` / `session_state` — close_session sur **session jetable isolée** (jamais la session
  recette partagée) ; preuve = `is_open` passe à 0.
- `stage_reject(dossier)` — confirme le frais 1 (SOP→SOU) + rejette une pièce → amorce la re-soumission.

Durcissements (B-2 code-review) : `purge_after` (décorateur → baseline restaurée en `finally` même sur
exception) ; session jetable **runid-suffixée** (`SES-AUDIT-DISPOSABLE-<runid>`, teardown par préfixe →
concurrence-safe). Le rate-limit anti-abus de `create_dossier` (20/h/IP) est purgé + retry dans `_create`
(clé de CACHE infra, jamais une donnée métier) pour ne pas faire échouer la construction de la preuve.
