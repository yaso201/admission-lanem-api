# PERF-1 — Pagination serveur & performance en CI — Preuves

**Mandat :** PERF-1 (failles 15 & 17), protocole DEC-L (recon → une pause → exécution continue → rapport, *arrêt au push*).
**Branche :** `mandat/perf-1` sur les 3 dépôts, chacune issue de la tête PROD.

| Dépôt | Tête PROD (base) | Write-set |
|---|---|---|
| back `admission` | `ebed28e` (OBS-1 back) | `admission/api/staff.py`, `admission/tests/test_staff_read.py`, `admission/tests/test_pieces_resubmit.py`, `docs/PERF-1-PREUVES.md` |
| front management | `c851502` (OBS-1 mgmt) | `src/pages/liste-dossiers.astro` |
| front applicant | `3cd2bc1` (A11Y-1) | `lighthouserc.json` *(fichier de config uniquement — frontière GOUV-1 respectée : aucune page applicant touchée)* |

---

## 1. Faille 15 — « Tous les dossiers » silencieusement incomplet au-delà du 200e

### Constat (recon) — pire que décrit
`list_dossiers` (ancien `staff.py:888`) plafonnait à 200 par défaut ; le front appelait `API.listDossiers()` **sans paramètre**, re-filtrait/re-paginait en mémoire et **ignorait `total`/`limit`**. Trois défauts cumulés :
1. au-delà du 200e dossier (par date de modification), la liste était **silencieusement tronquée** ;
2. la recherche `q` était appliquée **en Python APRÈS le `limit`** → même une recherche « serveur » ne voyait que les 200 premiers ;
3. `total = len(dossiers)` = taille de la page renvoyée, **pas** le vrai COUNT DB.

### Correctif
Pagination + filtrage + recherche **côté serveur** (`frappe.get_list`, DocPerms + cloisonnement DEC-262 préservés) :
- **DEC-A** : vrai `total` (COUNT sur le jeu filtré) + `limit`/`offset` consommés par le front.
- **DEC-B** : débordement visible — le front affiche « X affichés / N au total » + pagination numérotée pilotée serveur.
- **DEC-C** : recherche = **LIKE DB** sur 4 champs (`applicant_name`, `name`, `email`, `phone`) via `or_filters`, **sans post-limite**. Collation `utf8mb4_unicode_ci` = insensible accents **et** casse → équivaut au `normalized()` client (aucune divergence à corriger côté client).
- **DEC-D** : files enrichies (`queue=`) non exprimables en SQL (`paiement_a_confirmer`, `notes_absentes`, `notes_saisies`, `bourse_demandee`) — **pré-bornées** par statut/prépa/offline puis filtrées en Python avec la logique **exacte** de `_notes_state`/`_bourse_state` (jeux courts).
- **Pas de nouvel endpoint** (DEC-D) : `list_dossiers` enrichi, `api.js` inchangé (transmet déjà les paramètres).

### Preuve d'intégration — jeu à 300 dossiers en dev (315 au total)
Module transitoire `zz_perf_proof.py` : seed 300 → preuve → purge, cycle unique. Dossier cible **placé nommément au rang 250** (`ZZTEST Cible Ébéniste 250`, nom accentué). Sortie capturée :

```
TOTAL=315 (attendu 315 = 300 seeds + 15 existants)
PAGINE=315 UNIQUES=315 COMPLETE=True          ← pagination COMPLÈTE, aucun doublon, aucun absent
CIBLE=26270003023 RANG=250 AU_DELA_200=True    ← cible exactement au rang 250 (> 200)
ABSENT_DU_CAP200=True                          ← l'ANCIEN cap 200 l'aurait manquée (faille reproduite)
RECHERCHE_ebeniste(sans accent)=True           ← trouve la cible rang 250 par terme SANS accent
RECHERCHE_EBENISTE(accent+maj)=True
RECHERCHE_beniste(partiel)=True                ← recherche partielle
RECHERCHE_dupont(majuscule cherché minuscule)=True   ← nom en MAJUSCULES trouvé en minuscules
COUT_LIST_MS=37.6 (moyenne 3 appels, COUNT+facets+page inclus)
FACETS_prog=3 sessions=3 statuts=6
COUNTS_by_status={'BRO':64,'SOU':63,'ETU':60,'ATT':60,'INS':61,'ACC':7} queues={'paiement_a_confirmer':0,'notes_absentes':30,'notes_saisies':0,'bourse_demandee':17}
PURGE reste_ZZTEST=0 total_final=15            ← purge prouvée, retour à l'état initial
```

**Lecture** (check-list de sortie, point par point) :
- *Aucun dossier silencieusement absent* : `PAGINE=UNIQUES=TOTAL=315`, pagination exhaustive.
- *Recherche au-delà du 200e* : la cible est au **rang 250** ; l'ancien cap 200 l'excluait (`ABSENT_DU_CAP200=True`) ; la recherche DB la **remonte** (`RECHERCHE_ebeniste=True`), y compris par terme non accentué → la vraie faille est fermée.
- *Collation sur cas réels* : accentué (`Ébéniste`→`ebeniste`), majuscules (`DUPONT`→`dupont`), partiel (`béniste`) — les quatre remontent. **Aucune divergence** entre recherche DB et affichage → rien à ajuster côté client (DEC-C confirmé).
- *Compteur = vrai total serveur* : `total=315` (COUNT), pas la taille de page.
- *Coût des COUNT/facets mesuré* (garde-fou décision 3) : **~38 ms** à chaud pour un `list_dossiers()` complet (COUNT `GROUP BY status` + 3 compteurs de files + facets + page enrichie) sur 315 dossiers. Facets calculées sur le **jeu filtré par la recherche**, pas sur toute la base.

### Comportement préservé (DEC-C)
Filtres (programme/session/statuts), tri (`recent`/`ancien`/`nom`) et recherche produisent le même résultat qu'avant, désormais **complet** au lieu de plafonné. Files enrichies : logique `_notes_state`/`_bourse_state` réutilisée telle quelle → badges et lignes cohérents. **Aucune divergence à signaler.**

### Tests unitaires (back)
`TestListDossiers` mis à jour : `test_uses_get_list_for_permissions` (get_list + clés `total`/`facets`/`counts`), `test_search_is_db_like_over_four_fields` (or_filters LIKE sur les 4 champs), `test_response_shape_pagination_facets_counts` (limit/offset échoués), `test_queue_filters_enriched_predicate` (bourse **demandée** seule, proposée exclue). `test_r11_list_dossiers_expose_resoumis` asserte `_LIST_FIELDS`.

---

## 2. Faille 17 — performance exclue de la CI Lighthouse

### Constat
`lighthouserc.json` : `onlyCategories: [accessibility, best-practices, seo]` → **performance jamais mesurée**. Aucun throttling (desktop par défaut), aucun budget.

### Correctif (DEC-E/F)
`settings` — performance réintégrée + réseau contraint **Slow 3G** (décision 4) :
```json
"onlyCategories": ["accessibility","best-practices","seo","performance"],
"throttlingMethod": "simulate",
"throttling": { "rttMs":400, "throughputKbps":400, "requestLatencyMs":400,
                "downloadThroughputKbps":400, "uploadThroughputKbps":400, "cpuSlowdownMultiplier":4 },
"formFactor": "mobile",
"screenEmulation": { "mobile":true, "width":360, "height":640, "deviceScaleFactor":2 }
```

### Avant / après (mesure Lighthouse)
| | Avant | Après (config committée) |
|---|---|---|
| Catégorie performance | **non mesurée** (exclue) | **mesurée**, Slow 3G mobile |
| Score (suite 15 pages) | — | **0,41 → 0,68** selon la page |
| Throttling | aucun (desktop) | rtt 400 ms · 400 kbps · CPU ×4 |
| Budget | aucun | 3 assertions **en avertissement** |

Mesure réelle via `lhci collect` sur les **15 URLs de la CI** (single run, comme la CI). Extrêmes observés :

| Métrique | Pire page | Valeur observée |
|---|---|---|
| Score performance | `/paiement-sop/` | **0,41** |
| `resource-summary:total:size` | `/bourses/` | **≈ 1 079 Ko** (≈ 1 104 896 o) |
| `resource-summary:script:size` | pages SPA | **≈ 15 Ko** (≈ 15 360 o) |

> Le front applicant est **borné par le transfert** : même une page légale à 0 Ko de JS score 0,41–0,49, car ~1 Mo à 400 kbps ≈ 20 s de téléchargement → LCP ≈ TTI ≈ **23,6 s** de façon stable. Le score, lui, varie de run à run (~0,56–0,72 sur l'accueil).

### Budget posé — seuils, marges et justification (décision 5)
Tous en **avertissement** (DEC-F) : ils **remontent** une régression sans **casser** le build. `accessibility` reste seul en `error` (0,9).

| Assertion | Seuil | Observé (pire page) | Marge | Justification |
|---|---|---|---|---|
| `categories:performance` | `minScore 0.30` | 0,41 | **+0,11 (≈ +27 %)** | Filet grossier. La marge couvre à la fois la pire page (0,41) **et** la variance run-à-run (~±0,08). Score borné par le transfert → seuil = « en-dessous, quelque chose a cassé ». |
| `resource-summary:total:size` | `maxNumericValue 1300000` | ≈ 1 104 896 o | **+195 104 o (≈ +17,6 %)** | **Le levier actionnable** et déterministe (les octets ne varient pas d'un run à l'autre). Marge = tolérance de croissance légitime ; au-delà, un ajout d'actif lourd est signalé. |
| `resource-summary:script:size` | `maxNumericValue 30000` | ≈ 15 360 o | **+14 640 o (≈ +95 %)** | JS applicant ≈ 15 Ko max. Seuil à ~2× l'observé : passe au vert aujourd'hui, alerte si une lib lourde double le poids JS. |

**Choix assumé : pas de budget sur un timing brut** (`interactive`/`LCP`). Le LCP ≈ TTI ≈ 23,6 s est **entièrement en aval** du poids de transfert, déjà budgété directement, et c'est la métrique la plus bruitée en single-run simulate. Un seuil de timing serait soit toujours-en-alerte (12 s), soit dénué de sens (26 s). Le score `performance` (qui pondère déjà TTI/TBT/LCP) + le budget de transfert capturent le signal sans bruit.

### Preuve du comportement en CI
```
lhci assert (15 pages, config committée) → « All results processed! » exit 0
   → baseline VERTE : perf ≥0,41>0,30, total ≤1079Ko<1270Ko, script ≤15Ko<29Ko (aucun avertissement)
lhci assert avec total=1Ko (breach forcé)  → « ⚠️ resource-summary.total.size warning » × N, exit 0
   → DEC-F confirmé : un dépassement de budget AVERTIT, ne casse PAS le build
```

---

## 3. Baseline & hygiène
- **Suite back : `Ran 1124 tests` — `OK`** (0 échec, 0 erreur), inchangée par le lot.
- Front management : build Astro propre + pilotage jsdom pleine page (pagination serveur, presets enrichis, page 2 → offset 50) vert.
- **Purge prouvée** : 300 dossiers de test ZZTEST supprimés, `reste_ZZTEST=0`, `total_final=15` (état initial). Modules transitoires `zz_perf_*.py` supprimés.

## 4. Arrêt au push
Conformément à DEC-L, le lot s'arrête au push de `mandat/perf-1` sur les 3 dépôts. Fusion, migration et déploiement relèvent de l'architecte.

### Observation (hors périmètre, à noter)
Le répertoire généré `.lighthouseci/` est **versionné** dans le dépôt applicant (rapports `lhr-*.json` committés avant cette branche). C'est de la sortie de CI ; un `.gitignore` serait plus propre. Non traité ici (frontière GOUV-1 / hors write-set PERF-1) — signalé pour arbitrage.
