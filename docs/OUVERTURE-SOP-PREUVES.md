# OUVERTURE-SOP — Dossier de preuves

**Mandat** : ouvrir la plateforme sans attendre la validation du compte marchand FedaPay
(plafond 5 000 XOF, frais réels 10 000–75 000 → aucun paiement en ligne n'aboutit).
Fermer PROPREMENT l'initiation en ligne (DEC-334/335), sans toucher à la confirmation
(DEC-336), réversible par configuration seule. Second objet : confort de saisie OTP (DEC-337).

**Base** : back **`1daeca1`** · front applicant **`5b34348`** (= `origin/main`,
REPRISE-DOSSIER fusionné ; CAL-14 non fusionné — frontière `calendar.py`/management respectée,
zéro contact). Branches `mandat/ouverture-sop`. Baseline constatée : `Ran 1069 — errors=3`
(mêmes 3 `setUpClass` connus).

---

## ⚡ LA COMMANDE DE RÉACTIVATION (à exécuter sans contexte, le jour où FedaPay valide)

```bash
# Sur le serveur PROD (frappe@169.58.164.137), bench ~/bench-admission :
bench --site <site> set-config online_payment_enabled 1
bench restart

# Vérification (le refus disparaît — INVALID_DOSSIER = la porte laisse passer jusqu'à l'auth) :
curl -s -X POST https://api-admissions.lanem.bj/api/method/admission.api.public.submit_payment_online \
  -H "Content-Type: application/json" -d '{"dossier_id":"X","token":"x"}'
# attendu : {"error":{"code":"INVALID_DOSSIER"}}  — et NON "ONLINE_PAYMENT_DISABLED"
```

Aucun déploiement, aucun code. La fermeture est la même commande avec `0`.
Le front suit automatiquement (il lit `get_frais.online_payment_enabled`).

## 1. Livré

### Back (`public.py`)

| Élément | Contenu |
|---|---|
| `_online_payment_enabled()` | Lecture conf `online_payment_enabled` (patron `fedapay_*`), `cint` (robuste aux chaînes de set-config). **Absent = OUVERT — commenté au point de lecture** (condition 2 du GO) : compatibilité dev/suites ; en production le drapeau est TOUJOURS posé explicitement. |
| `_require_online_payment_enabled()` | Garde **fail-fast, avant auth** (état global, aucun oracle) → **`ONLINE_PAYMENT_DISABLED` 503**, message orienté action (« arrive prochainement… déclarez votre paiement ») — jamais une panne. |
| Câblage | **Les deux initiations** : `submit_payment_online` (frais 1) et `submit_enrollment_payment_online` (frais 2), tout premier contrôle. `declare_*_offline` (SOP) **jamais** gaté. `webhook.py`/`fedapay.py` **non touchés** (DEC-336). |
| `get_frais` | Clé additive `online_payment_enabled` — le front lit, ne décide pas. |

### Front applicant

| Élément | Contenu |
|---|---|
| `paiement.astro` | Drapeau fermé → chemin en ligne **RETIRÉ** (display none, pas grisé), panneau retiré, **message DEC-335 verbatim** (« Paiement des frais de candidature — … par espèces ou virement bancaire … *Le paiement par mobile money sera disponible prochainement.* »), **SOP sélectionné et au centre** ; la voie SOP est ré-affichée même si le RIB manque (l'espèce reste possible). Clé absente = ouvert (compat back antérieur). |
| `suivi.astro` (micro-édition sanctionnée) | **Un seul hunk, ligne 534, 18+/3−** — strictement le renderer des canaux frais 2 : bouton « Payer en ligne » construit ssi drapeau ouvert (sinon note « prochainement » + « Par virement » devient primaire). `getFrais` déjà appelé par la carte et caché par le tunnel → **0 requête ajoutée**. Lanceur `AT.payment` et logique reprise **intacts**. |
| `otp-digits.js` (**nouveau**, `?v=1`) | DEC-337, **une implémentation, trois consommateurs** (identité ×1, reprise ×2) : auto-avance · backspace · collage réparti puis validé · **Entrée depuis toute case** · **auto-validation one-shot au 6ᵉ** (armée à l'init, désarmée après tir, **ré-armée uniquement par `reset()`**) · `reset()` = vidé + focus 1ʳᵉ + ré-armé, appelé sur refus serveur. |
| `identite.astro` | Charge `otp-digits.js?v=1`, câble `otpCtl`, refus → `reset()` ; **bloc M4 retiré** (remplacé par l'implémentation partagée). |
| `reprise.astro` | Les 2 groupes (Mode A + OTP consultation) câblés ; refus → `reset()` sur les deux ; le chemin lien-prérempli (`autoVerify`) inchangé, son échec ré-arme. |

## 2. Preuves d'exécution

### TDD (RED observés)

- Back `test_online_payment_flag.py` : RED 3 ciblés (garde absente ×2, clé get_frais absente) + **pin DEC-336 vert d'emblée** (le webhook n'a jamais consulté le drapeau — miroir exact de `test_promotes_existing_pending` avec conf `online_payment_enabled: 0`) → GREEN **7/7**.
- Front `otp-digits.test.mjs` : RED 9/9 (module absent) → GREEN **9/9** (dont **comptage d'appels** : one-shot, ré-armement par reset, Entrée explicite même désarmé).
- Pages : **falsifiabilité** 0/6 contre le dist antérieur (pré-OUVERTURE) → **6/6** contre le dist du worktree.

### Traceur SOP bout en bout (dev, purgé — check-list 5)

Drapeau **fermé** pendant tout le parcours :

| Étape | Preuve |
|---|---|
| Initiation en ligne sur le dossier réel | `ONLINE_PAYMENT_DISABLED` ✓ |
| Déclaration candidat (virement, réf `VIR-TRACEUR-1`) | ok → dossier **SOP**, paiement **Pending/Bank** |
| Confirmation Administratif (justificatif obligatoire) | paiement **Confirmed**, `paid_at` posé, source `banque`, fee **Paid**, dossier **SOU**, **reçu numéroté `261200326`** (format XX AA NNNNN ✓), 1 mail émis |
| Rejeu de la confirmation | `idempotent: true`, toujours **1 seul** paiement |
| Purge | 0 résidu |

### Bascule réelle par configuration + restart (check-lists 2 & 3, HTTP)

| État | `submit` | `enrollment` | `get_frais.flag` | SOP (`declare`) |
|---|---|---|---|---|
| Clé absente | `INVALID_DOSSIER` (porte ouverte) | — | `True` | — |
| `set-config 0` + **restart** | **`ONLINE_PAYMENT_DISABLED` HTTP 503** | idem | `False` | `INVALID_DOSSIER` (non gaté) |
| `set-config 1` + **restart** | `INVALID_DOSSIER` (rouvert) | — | `True` | — |

Même code aux trois états — la réversibilité est une pure commande de configuration.
Conf dev restaurée (clé retirée) après preuve.

### Baselines finales (ligne de résumé, jamais l'exit code)

| Harnais | Base | Worktree |
|---|---|---|
| Suite back | 1069 / errors=3 | **1076 / errors=3** (mêmes 3 `setUpClass`, 0 failure) |
| Recette notes | 48/48 | **48 PASS / 0 FAIL** |
| CSV | 7/7 | **7/7 OK** |
| Front | 35+1 faux-rouge | **60 pass / même 1** (pull-legal pré-existant, identique sur main) |
| Build | — | exit 0, 19 pages, 0 warning |
| **CAL-13** | — | **`admission-tunnel.js` : 0 octet modifié** → pas de bump global ; `otp-digits.js` est un asset neuf, son `?v=1` initial suffit |

## 3. Check-list de sortie (9/9)

1. ✅ Drapeau 0 : aucun bouton en ligne (jsdom, style calculé), message DEC-335 verbatim, SOP visible/actif.
2. ✅ Chemin forcé → refus serveur 503 `ONLINE_PAYMENT_DISABLED` (HTTP, les 2 endpoints).
3. ✅ Drapeau 1 : réapparition **sans redéploiement** (set-config + restart réels).
4. ✅ Webhook intact drapeau 0 (test miroir, `webhook.py` non touché).
5. ✅ SOP complet : déclaration → SOP → confirmation → SOU → reçu `261200326`.
6. ✅ OTP : 6ᵉ auto · Entrée · collage réparti+validé (unit 9/9 + intégration pleine page).
7. ✅ Refus → cases vidées + focus 1ʳᵉ + **compteur décrémenté une seule fois** (comptage d'appels : 1 puis ré-armement → 2).
8. ✅ Les deux écrans portent le comportement (identité : câblage + M4 remplacé ; reprise : 2 groupes ; implémentation unique).
9. ✅ Baselines exactes, build propre, jsdom vert, CAL-13 sans objet vérifié.

## 4. Instructions post-fusion (OPS)

1. **Back** : fusion → sur PROD `git reset upstream/main` + `bench restart`, puis **POSER LE DRAPEAU EXPLICITEMENT** (condition 1 du GO — « absent = ouvert » est un défaut de compatibilité, pas un mode d'exploitation) :
   ```bash
   bench --site <site> set-config online_payment_enabled 0
   bench restart
   ```
2. **Smoke OBLIGATOIRE** (sinon l'oubli se découvre par un candidat qui échoue sur le plafond) :
   ```bash
   curl -s -X POST https://api-admissions.lanem.bj/api/method/admission.api.public.submit_payment_online \
     -H "Content-Type: application/json" -d '{"dossier_id":"X","token":"x"}'
   # ATTENDU : "ONLINE_PAYMENT_DISABLED" (503). Toute autre réponse = drapeau mal posé.
   ```
3. **Front** : push main → Pages ; vérifier sur `admissions.lanem.bj/paiement/` (avec un dossier de test) que le message « prochainement » s'affiche et que le chemin en ligne a disparu ; `/identite` et `/reprise` chargent `otp-digits.js?v=1`.
4. Réactivation : la commande en tête de ce document.

## 5. Propositions corpus (l'architecte répercute)

- **DEC-334** : « Drapeau serveur `online_payment_enabled` (conf de site). Autorité = serveur : à 0, les DEUX initiations en ligne (frais 1 et frais 2) refusent `ONLINE_PAYMENT_DISABLED` 503 avant auth. Absent = ouvert (compatibilité, commenté au point de lecture) ; en production, TOUJOURS posé explicitement + smoke post-déploiement. Bascule = set-config + restart, jamais un déploiement. »
- **DEC-335** : « Bouton en ligne RETIRÉ (jamais grisé/échouant) ; message orienté action verbatim (mobile money = nouveauté à venir) ; SOP au centre. Appliqué à /paiement (frais 1) ET à la carte frais 2 de /suivi. »
- **DEC-336** : « Le webhook ne consulte pas le drapeau — prouvé par test miroir avec conf fermée ; `webhook.py`/`fedapay.py` non modifiés. »
- **DEC-337** : « Confort OTP : implémentation unique `otp-digits.js` (3 consommateurs). Auto-validation one-shot au 6ᵉ (armée/désarmée/ré-armée par reset), Entrée explicite toujours permise, collage réparti+validé, refus → vidé+focus+ré-armé. Preuve = comptage d'appels serveur. »

## 6. Hors périmètre, constaté

- Faux-rouge front pré-existant `pull-legal.test.mjs` (identique sur main — déjà documenté au lot REPRISE-DOSSIER).
- 3 `setUpClass` back pré-existants (inchangés).
- `scenario_recette.run` : toujours cassé en dev local (donnée d'env `INVALID_LEVEL PRE-P1`, pré-existant) — à rejouer en recette.
