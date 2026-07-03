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
