# REPRISE-DOSSIER — Dossier de preuves

**Mandat** : rétablir la reprise de brouillon (DEC-332/333, option B) après le constat
RECON-REPRISE : « aucun chemin ne permet de reprendre un brouillon depuis un navigateur
vierge avec pour seul actif une adresse e-mail » (DEC-323-bis a supprimé `recover_dossier`
`limit=1` sans équivalent d'écriture, commit `72b8cf6`).

**Base** : back `4e98fed` (= `origin/main`, FedaPay fusionné) · front applicant `0d3e6b6`
(= `origin/main`, FedaPay fusionné). Branches `mandat/reprise-dossier` sur worktrees isolés.
Baseline constatée avant travaux : `Ran 1046 tests — FAILED (errors=3)` (3 `setUpClass`
pré-existants : `test_calendar.TestCal09DecE`, `test_roles_hierarchy.TestRolesHierarchyHelper`,
`test_sm_l0.TestHardenPatch`).

---

## 0. Question transversale INVERSÉE — prouvée

> Depuis un navigateur vierge, avec pour seul actif une adresse e-mail, un candidat
> **reprend un brouillon et le modifie.**

Traceur bout en bout dev (module transitoire, DB + Redis + HTTP réels, purgé) :
e-mail → OTP identité (hash Redis + Lua réels) → **liste de 5 dossiers** (3 BRO sur
3 sessions + 1 INC + 1 SOU) → claim → **écriture réelle persistée** (`classify_bac`,
`bac_date` en base) — y compris **par HTTP en invité** (claim guest → jeton d'édition →
écriture HTTP OK, `otp_verified=1` + horodatage posés).

## 1. Ce qui est livré

### Back (`admission/api/public.py`)

| Élément | Contenu |
|---|---|
| `CANDIDATE_EDITABLE_STATUSES = ("BRO", "INC")` | Source **unique** de DEC-332 — consommée par le claim, la garde d'écriture et `reprenable`. |
| `claim_recovered_dossier(recovery_token, dossier_id)` | DEC-333 option B. **3 contrôles serveur** (condition 1 du GO) : session de consultation Redis valide · dossier ∈ liste résolue par l'OTP (jamais le param d'URL) · état ∈ BRO/INC. Puis rotation du jeton de dossier (patron `reissue_candidate_access`), TTL 7 j glissants, `otp_verified=1` + `otp_verified_at` (**invariant alimenté, pas assoupli** : l'OTP d'identité vient de prouver la même adresse, même canal que `verify_otp`). Journalisé (condition 3) : `log_event("claim_recovered_dossier","success", dossier_id, identity=<HMAC e-mail 12c>)` — non-PII. Rate-limit : fenêtre `verify-ip` existante (30/h). |
| `_require_candidate_editable(applicant, piece_row=None)` | **Condition 2 du GO** : l'état est revérifié à CHAQUE écriture. BRO/INC passent ; exceptions NOMINATIVES = flux conçus : **SOU** + pièce `rejected` **ou** `missing` requise_effective (miroir exact de `pieces_recap`, Lot 3c) · **ACO** + `diplome_bac` (C1-ACO/DEC-214). Tout le reste → **409 `STATE_READ_ONLY`**. Câblée dans `upload_piece_file` (avant toute manipulation de fichier) et `classify_bac`. `resubmit_complement` (INC-only) et `candidate_resubmit` (SOU-only) gardaient déjà leur état. |
| `reprenable` | Servi par le back dans les résumés `verify_recovery_otp` **et** le détail `get_recovered_dossier` (clé additive, hors `_serialize_dossier` partagé) — front **pur renderer** (patron FIX-PROGRESSION). |
| Catch bavard (V-LEARN-CAL-03) | `_log_invalid_dossier` sur les 4 sites `INVALID_DOSSIER` du chemin reprise (`request_otp`, `verify_otp`, `get_dossier`, `classify_bac`) : cause réelle au journal (`DoesNotExistError…` / `Jeton de dossier absent.` / `…invalide.`), réponse générique INCHANGÉE, jamais le jeton. **Niveau `error` à dessein** (voir V-LEARN ci-dessous). `_get_applicant` distingue désormais « Jeton de dossier absent. » de « …invalide. » (journal seulement). |
| Anti-énumération renforcée | Le traceur HTTP a révélé un **oracle d'existence pré-existant** : `frappe.get_doc` (DoesNotExist) posait « Admission Applicant N not found » dans `_server_messages` malgré le message générique. `_log_invalid_dossier` purge `message_log` → réponse HTTP prouvée **sans** `_server_messages`. |

### Front applicant

| Élément | Contenu |
|---|---|
| `admission-tunnel.js` (région DEC-323/U) | `api.claimRecoveredDossier(recoveryToken, dossierId, cb)` — seul appel du bloc à **adopter** un jeton (patron `verify_otp`) ; un refus n'adopte rien. |
| `/reprise` — liste | Item restructuré (conteneur + bouton consulter + bouton **« Reprendre »** ssi `reprenable` — deux boutons frères, validité HTML). Claim → `saveDossier` → `routeByStatus()` → `resolveStep` (source unique, **retour à l'étape réelle** : BRO+pièces manquantes→`/pieces`, BRO complet→`/recapitulatif`, INC→`/suivi` carte complément). |
| `/reprise` — détail | Bouton « Reprendre ce dossier » ssi `reprenable` ; consultation inchangée pour tous les états. |
| Écran orphelin **rebranché** | Créance morte (lien tokenisé périmé OU ancrage rassis → `INVALID_DOSSIER`) : purge de l'ancrage + bascule Mode A→Mode B avec le message actionnable exact : **« Ce lien n'est plus valide. Indiquez votre adresse e-mail pour retrouver vos dossiers. »** Plus jamais le cul-de-sac « Identifiants de dossier invalides ». |
| Indice `?dossier=` | **Pré-sélection, jamais autorisation** : surligné (`is-hinted` + scroll) ssi présent dans la liste résolue par l'OTP ; numéro étranger ignoré ; le serveur re-contrôle l'appartenance au claim. |
| CAL-13 | **Bump global `?v=4`→`?v=5`** — les 11 pages (liste §5). |

## 2. Preuves d'exécution

### TDD (RED observés avant chaque GREEN)

- Back : `test_claim_recovered_dossier.py` — RED 18/22 (endpoint absent ×9, `reprenable` absent, gardes absentes, catch muet ×3 ; les 4 verts = pins de non-régression 3c/ACO) → GREEN **23/23**. Second cycle RED→GREEN pour niveau `error` + purge oracle.
- Front tunnel : RED 4/4 (`claimRecoveredDossier is not a function`) → GREEN 4/4.
- Front pleine page (jsdom, style **calculé**) : **RED de falsifiabilité** contre le dist v0.9.0 (4 comportements nouveaux échouent, comportement conservé passe) → GREEN **6/6** contre le dist du worktree.

### Traceur bout en bout (sortie intégrale conservée en session)

| Check-list | Preuve |
|---|---|
| 1. Vierge → reprise + modification | claim + `classify_bac` persistée ×4 (3 BRO + INC), **aussi via HTTP guest** |
| 2. Multi-brouillons | 3 BRO sur `SES-2026-10`/`SES-2026-LIC`/`SES-A2-RT` : 3 listés, 3 repris **indépendamment** (3 jetons distincts) |
| 3. DEC-332 | 4 `reprenable` (3 BRO+INC), SOU sans bouton (jsdom) et claim forcé → **409 STATE_READ_ONLY** (serveur) |
| 4. Portée | jeton du dossier B sur dossier C → `INVALID_DOSSIER` (HMAC par doc) |
| 5. TTL/rotation | ancien jeton post-claim → `INVALID_DOSSIER` · `token_expires_at` passé → `TOKEN_EXPIRED` · session consultation expirée → `RECOVERY_SESSION_INVALID` |
| 6. Bonne étape | données servies = statut BRO + 8 requises manquantes → `resolveStep` → `/pieces` (résolveur inchangé, e2e FIX-RETOUR-DOSSIER) |
| 7. Catch bavard | HTTP réel : réponse générique **sans `_server_messages`**, journal `admission.log` : `{"step":"request_otp","status":"invalid_dossier","dossier_id":"00000000000","reason":"DoesNotExistError: … not found"}` |
| 8. Consultation | `verify_recovery_otp`/`get_recovered_dossier` inchangés (tests DEC-323 verts), payload **sans aucun jeton** |
| Condition 2 GO | BRO→**SOP pendant la session d'édition** : `upload_piece_file` ET `classify_bac` → `STATE_READ_ONLY` |
| Purge | 5+1 dossiers supprimés, clés Redis purgées, **0 résidu** ; traceur transitoire supprimé |

### Baselines finales (vérifiées sur la LIGNE DE RÉSUMÉ, jamais l'exit code)

| Harnais | Base (4e98fed) | Worktree | Verdict |
|---|---|---|---|
| Suite back | `Ran 1046 — errors=3` | `Ran 1069 — errors=3` (**mêmes 3** setUpClass, 0 failure) | ✅ +23, zéro régression |
| Recette notes | 48 PASS / 0 FAIL | **48 PASS / 0 FAIL** | ✅ |
| CSV | 7/7 | **7/7 OK** | ✅ |
| Front tests | 35 pass / 1 fail (pull-legal, pré-existant) | 45 pass / **même** 1 fail | ✅ +10 |
| Build front | — | exit 0, 19 pages, 0 warning | ✅ |

Deux fixtures pré-existantes mises à niveau (write-set tests) : `test_upload_file._applicant`
et `test_sec_critique.test_classify_bac_not_gated_on_otp` déclarent désormais `status="BRO"`
(les MagicMock présupposaient implicitement un état modifiable — DEC-332 l'explicite).

## 3. Écarts DEC-332 constatés, non corrigés (arbitrage architecte)

1. **`candidate_resubmit`** : écriture candidat en **SOU** (« j'ai fini de re-déposer », Lot 3c-3a, geste conçu). Écart LITTÉRAL à DEC-332, cohérent avec l'exception « correction initiée par le personnel ». Laissé tel quel.
2. **Resserrement assumé** : en SOU, une pièce déjà `uploaded` (sous revue, non rejetée) ne peut **plus** être remplacée par le candidat (avant : possible). Conforme à l'esprit DEC-332 ; aucun flux UI ne l'offrait.
3. **`request_data_deletion`** : laissé hors garde d'état (droit RGPD à l'effacement, valable en tout état) — lecture à confirmer.
4. Paiements : hors périmètre DEC-332 (transactions, gardes d'état propres D-CONF-01).

## 4. Risque e-mail partagé (condition 4 — documenté, pas construit)

La résolution d'identité reste **par e-mail seul** (constat VERIF-DOUBLONS). Hier : une
adresse partagée (cybercafé, famille) permettait de **lire** les dossiers d'autrui après
OTP ; désormais elle permet d'en **modifier** les brouillons (BRO/INC uniquement — les
dossiers engagés SOP+ restent hors d'atteinte, et chaque claim est journalisé
dossier+identité+horodatage). Conséquence assumée du modèle d'identité, à arbitrer plus
tard — piste : **corroboration DOB au claim** (DEC-322 rend la DOB obligatoire aux
nouveaux dépôts), volontairement **non** construite dans ce lot.

## 5. CAL-13 — pages bumpées `?v=5` (11/11)

`index` · `identite` · `pieces` · `recapitulatif` · `paiement` · `paiement-sop` ·
`paiement-accepte` · `confirmation` · `suivi` · `reprise` · `bourses`
(vérifié : 11 occurrences `?v=5`, 0 résidu `?v=4`).

## 6. Propositions corpus (l'architecte répercute)

- **DEC-332 (M02)** : « États modifiables par le candidat : BRO et INC uniquement ; SOP et
  au-delà lecture seule, correction par le personnel. Gardée à CHAQUE écriture serveur
  (`_require_candidate_editable`), exceptions nominatives : SOU re-dépôt pièce
  rejetée/à-fournir (3c) ; ACO `diplome_bac` (C1-ACO). Consultation possible dans tous les
  états (DEC-323 complété, pas remis en cause). »
- **DEC-333 (M02)** : « Reprise par jeton d'édition mono-dossier (`claim_recovered_dossier`),
  émis après OTP identité ssi l'état l'autorise ; jeton de dossier ordinaire (rotation,
  TTL 7 j) ; `otp_verified` alimenté par l'OTP identité (même canal que `verify_otp`) ;
  claim journalisé (dossier + HMAC identité + horodatage). Options A et C rejetées. »
- **DEC-323-bis — correction** : la formule « l'ancienne reprise limit=1 disparaît
  (remplacée) » n'a jamais été honorée : DOSSIERS-IDENTITE remplaçait une reprise (écriture)
  par une consultation (lecture). REPRISE-DOSSIER referme l'écart — noter que la reprise
  self-service est rétablie via DEC-333, le minting staff (`reissue_candidate_access`)
  inchangé.
- **V-LEARN de série** (déjà acté par l'architecte) : `bench run-tests` **retourne exit 0
  même en FAILED** — toute vérification se fait sur la ligne de résumé. Corollaires
  découverts dans ce lot : un pipe `| tail` masque aussi l'exit du build (`pull-legal`
  fail-closed avalé) ; le logger frappe **filtre sous ERROR hors dev-server**
  (`default_log_level = logging.ERROR`) → un « catch bavard » en warning est un catch muet
  déguisé, d'où le niveau `error` ; `frappe.get_doc` laisse un **oracle d'existence** dans
  `_server_messages` si le catch ne purge pas `message_log`.

## 7. Instructions post-fusion (OPS)

1. Back : fusion → `bench --site <site> migrate` (aucun patch schéma dans ce lot — migrate
   par hygiène), restart. Vérifier au ping : `claim_recovered_dossier` répond 403
   `RECOVERY_SESSION_INVALID` en guest (preuve de routage, cf. smoke).
2. Front : fusion → build Pages (env `PUBLIC_API_BASE` prod) → vérifier **11/11 pages en
   `?v=5`** en ligne (leçon FedaPay).
3. Rejouer `scenario_recette.run` **sur l'environnement de recette** : il est cassé sur le
   dev local par une donnée d'env pré-existante (`INVALID_LEVEL : Niveau PRE-P1 inconnu`,
   miroir catalogue incomplet), à l'identique sur main — non-régression prouvée par
   comportement identique, mais le 48/48 scénario complet doit être re-constaté en recette.
4. Recette fonctionnelle minimale : depuis un navigateur vierge — « Retrouver mes
   dossiers » → OTP → Reprendre un BRO → déposer une pièce ; vérifier le refus sur un SOU ;
   vérifier le repli d'un vieux lien e-mail vers le parcours identité.

## 8. Hors périmètre, constaté

- Faux-rouge front pré-existant : `pull-legal.test.mjs` « titre CGV » (échoue à l'identique
  sur `main` intact — titre renommé sans mise à jour du test ; legal hors write-set).
- 3 `setUpClass` back pré-existants (inchangés).
- `_recette_notes` imprime `NameError: name 'admission' is not defined` en fin de run via
  `bench execute` (mécanique frappe `eval` du retour, sans effet sur les 48 PASS) —
  cosmétique, pré-existant.
