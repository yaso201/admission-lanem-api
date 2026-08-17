# SEC-1 — Dossier de preuves (gardes de rôle + en-têtes de sécurité)

> Mandat DEC-L, salve pré-ouverture. Trois worktrees, branche `mandat/sec-1` : back
> `admission-lanem-api` @ 932acff · front applicant `admission-lanem` @ c667b90 · front management
> `lanem-admission-management` @ 5efdee5. **Arrêt au push** ; fusion/déploiement = architecte.
> Source : AUDIT-360-A2-SECURITE.md note C (faille 1 = A01, faille 8 = en-têtes).

---

## 0. Verdict en une ligne

- **Faille 1 (A01, gardes de rôle) : FAUX POSITIF au head PROD.** Les 5 endpoints SONT gardés au
  niveau ADMIN_UP, depuis la création du socle. Aucune correction de code nécessaire ; livré à la
  place un **test de non-régression positif** + la **preuve runtime** que le garde refuse.
- **Faille 8 (en-têtes) : RÉELLE et corrigée.** `public/_headers` posé sur les deux fronts
  (X-Frame-Options, HSTS, nosniff, Referrer-Policy actifs ; CSP en Report-Only).

---

## 2. Préalable — inventaire comptes + cloisonnement (PROD, lecture seule)

| Fait | Valeur (PROD `api-admissions.lanem.bj`) |
|---|---|
| `consultation_cloisonnee` | **0 (OFF)** |
| ADMIN_UP | `{Administratif, Responsable, Direction, System Manager}` (`CONFIRM_ROLES is ADMIN_UP`) |
| Comptes login-capables | 11 (Administrator + 10 comptes staff) |
| **Comptes SOUS ADMIN_UP** | **0** |

Les 11 comptes sont tous ADMIN_UP : les deux comptes SM (`admin.admissions@`, `sm@`) portent aussi
**System Manager** (∈ ADMIN_UP) ; chaque Website User porte un rôle staff (Administratif/Responsable/
Direction). **Aucun compte orphelin sous ADMIN_UP.**

**Verdict (grille du mandat) :** cloisonnement OFF **mais** aucun compte sous ADMIN_UP → la faille,
*même si elle existait*, serait **DORMANTE** (aucun sujet pour l'exploiter). L'axe 2 ne descend pas à
D sur ce motif. Et surtout — voir §1 — **le garde existe** : la faille n'est pas présente du tout.

---

## 1. Faille 1 (A01) — FAUX POSITIF : le garde existe déjà (helper commun)

L'audit a lu les corps des 5 endpoints (`staff.py:1512-1600`) et constaté l'absence de `only_for`.
Mais leur **1ʳᵉ instruction** est `_resolve_piece_sou(dossier_id, piece_code)`, dont la **1ʳᵉ ligne**
(`staff.py:1496`) est :

```python
def _resolve_piece_sou(dossier_id, piece_code):
    """Garde commune des verdicts pièce : rôle Administratif + dossier SOU + pièce existante. …"""
    frappe.only_for(CONFIRM_ROLES)   # CONFIRM_ROLES = ADMIN_UP (staff.py:71)
    …
```

Les 5 endpoints (`verify/reject/require/waive/reset_piece_requirement`) appellent tous ce helper
**avant toute mutation** → ils sont gardés au niveau **ADMIN_UP**, exactement comme leurs pairs
(`start_review`→`only_for(ADMIN_UP)`, `reject_dossier`/`reopen_dossier`→`only_for(CONFIRM_ROLES)`).
`_guard_write_scope` (le seul contrôle que l'audit a vu) est **orthogonal** : c'est le cloisonnement
(has_permission), pas le rôle.

**Timeline (git blame)** : le `only_for(CONFIRM_ROLES)` du helper date de **c5ad47d (2026-06-27)** —
le commit *fondateur* « Lot 3c-1 contrôle documentaire par pièce ». Le garde n'a **jamais** manqué.

### Preuve runtime (dev, `frappe.PermissionError`)

Utilisateur jetable ZZTEST portant **uniquement `Admission SM`** (rôle staff ORTHOGONAL, hors ADMIN_UP) :

```
CONFIRM_ROLES is ADMIN_UP: True | ADMIN_UP=[Administratif, Direction, Responsable, System Manager]
USER test roles=['Admission SM','All','Guest'] | dans ADMIN_UP ? False
NEG: frappe.PermissionError -> GARDE EFFECTIVE, rôle insuffisant REFUSÉ (OK)
POS(Administrator): garde franchi, error.code=INVALID_DOSSIER (garde franchi → check dossier atteint)
PURGE test user: absent=True
```

→ rôle insuffisant = **refus avant tout accès dossier** ; ADMIN_UP = **franchissement**. Garde réel
et effectif. (Compte de test purgé.)

### Ce qui a été livré (et NON livré)

- **NON livré** : aucune modification de `staff.py`. J'avais d'abord ajouté `only_for(ADMIN_UP)` à
  chaque endpoint ; le test l'a détecté comme **doublon** (« only_for appelé 2 fois »). **Reverté** —
  `git diff staff.py` = vide. Doubler le garde serait du code mort trompeur (DEC #4 : aucun changement
  de comportement).
- **Livré (write-set « tests »)** : `TestSEC1PieceGuards` dans `test_pieces_verification.py` —
  verrouille le garde par la **voie positive** : chacun des 5 endpoints appelle `only_for` exactement
  **une fois avec ADMIN_UP**. Ce test **échoue si le garde du helper disparaît** (0 appel) → barrière
  de non-régression réelle. Les tests `…_role_garde` préexistants (`test_v13`, `test_vr5`) sont
  **faibles** (side_effect : passent même sans garde — une PermissionError incidente de la chaîne
  mockée les satisfait ; c'est ce qui a masqué la nature du helper) ; conservés mais **supersédés**.

---

## 8. Faille 8 (en-têtes) — RÉELLE, corrigée

Sonde live avant correction : les deux fronts ne servaient **ni CSP, ni HSTS, ni X-Frame-Options**
(seuls `x-content-type-options: nosniff` et `referrer-policy` étaient déjà présents, défaut CF).

`public/_headers` (Cloudflare Pages) posé sur **les deux fronts**. En-têtes **actifs (bloquants)** :
`X-Frame-Options: DENY` · `Strict-Transport-Security: max-age=31536000; includeSubDomains` (1 an) ·
`X-Content-Type-Options: nosniff` · `Referrer-Policy: strict-origin-when-cross-origin`. CSP en
**`Content-Security-Policy-Report-Only`** (DEC #3 : observe sans casser paiement/tunnel ; le mode
bloquant est un lot ultérieur, après lecture des rapports).

### Allowlist CSP — par OBSERVATION (DEC #2), pas par supposition

**Applicant** (runtime tunnel observé : layouts + `admission-tunnel.js` + pages live) :
- polices Google : `fonts.googleapis.com` (CSS, style-src) + `fonts.gstatic.com` (woff2, font-src) ;
- FedaPay : `cdn.fedapay.com` (checkout.js, script/connect-src) + `process.fedapay.com`
  (checkout, frame/connect-src) — **paiement désactivé (online_payment_enabled=0)** : les hôtes exacts
  du checkout restent **à confirmer sur un vrai paiement** avant le passage bloquant (d'où Report-Only) ;
- API : `api-admissions.lanem.bj` (connect-src) ;
- `'unsafe-inline'` script/style : amorçages + styles scoped Astro sans nonce (durcissement par
  nonce/hash = lot bloquant ultérieur).
- ⚠️ **Bruit écarté** : `sveltiacms.app / svelte.dev / github.com / lexical.dev` viennent UNIQUEMENT
  de `public/admin/sveltia-cms.js` (CMS vendorée à `/admin`), PAS du tunnel candidat → **non inclus**.
  En Report-Only, `/admin` générera des rapports (dépendances GitHub) — normal, à traiter au lot bloquant.

**Management** : polices **système** (aucune police externe), aucune ressource FedaPay ; seule
dépendance réseau = `api-admissions.lanem.bj` (connect-src). CSP minimale `self` + API.

### Preuve build (dist)

Les deux builds sont **propres** ; `public/_headers` est copié par Astro à la racine `dist/` (servi
par CF Pages) :
- applicant `dist/_headers` (1647 o) — XFO/HSTS/nosniff/Referrer + CSP-Report-Only avec fedapay+fonts+api.
- management `dist/_headers` (945 o) — XFO/HSTS/nosniff/Referrer + CSP-Report-Only self+api.

---

## Baselines (constatées)

| Baseline | Résultat |
|---|---|
| Back `run-tests --app admission` | **1102/3** (1101 baseline + 1 test de durcissement ; 3 erreurs `setUpClass` CONNUES : test_calendar / test_roles_hierarchy / test_sm_l0 — RQ/Redis enqueue, environnemental, **hors write-set**) |
| `test_pieces_verification` ciblé | 39/39 OK |
| Front applicant `npm test` | **61/61**, 0 fail (le fichier statique `_headers` n'affecte aucun test) |
| Builds front | applicant + management **Complete!**, `dist/_headers` présents |

---

## Cas d'arrêt rencontré

**« La correction 1 est déjà en place » (variante du cas d'arrêt #1).** La faille 1 n'existe pas au
head PROD — reporté ici plutôt que d'ajouter un garde redondant. Aucune extension de write-set, aucune
frontière A3-FIX touchée (ni `admission-tunnel.js`, ni `gestion-sessions.astro`).

## Sondes HTTP (instruction POST-FUSION — à jouer après redéploiement Pages)

```
for host in admissions.lanem.bj staff.lanem.bj; do
  echo "== $host =="; curl -sI "https://$host/" | \
    grep -iE 'x-frame-options|strict-transport-security|content-security-policy-report-only|x-content-type|referrer-policy'
done
```
Attendu sur les deux : `x-frame-options: DENY`, `strict-transport-security: max-age=31536000; includeSubDomains`,
`content-security-policy-report-only: …`, `x-content-type-options: nosniff`, `referrer-policy: strict-origin-when-cross-origin`.
Puis **lire la console navigateur** (violations CSP Report-Only) sur un parcours complet — dont un
paiement réel quand `online_payment_enabled` repassera à 1 — pour figer l'allowlist FedaPay avant le
lot bloquant.
