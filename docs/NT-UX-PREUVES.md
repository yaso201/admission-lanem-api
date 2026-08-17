# NT-UX — Dossier de preuves (actions explicables)

> Mandat DEC-L. Deux worktrees, branche `mandat/nt-ux` : back `admission-lanem-api` @ e7b5e7f ·
> front management `lanem-admission-management` @ 5297f19. **Arrêt au push** ; fusion/déploiement =
> architecte. Origine : demande utilisateur (« actions mutuellement exclusives… pas direct ni
> intuitif… éviter les mauvaises manipulations »). NT-S a posé les gardes serveur ; **ce lot rend
> l'ordre lisible en exposant ce que le serveur décide déjà** (DEC-G : aucune garde touchée).

---

## Conception — `blocked_actions` sans dupliquer les gardes (DEC-A, GK9)

Le moteur `_actions.py` : chaque règle folde état+conditions+rôle. `action_context(applicant)`
**miroite** les gardes endpoint. Le lot ajoute 4 préconditions miroir (aucune règle réécrite) :
`notes_ok` (=`not is_prepa or notes_validated`, miroir de `_require_validated_notes_if_prepa`),
`coef_complete` (fusionné dans get_dossier, session), `diploma_uploaded` (miroir de
`_has_uploaded_diploma`), + bourses (DEC-D). Ces conditions sont **ajoutées aux règles** → les
boutons qui s'affichaient puis échouaient quittent `available_actions` (GP6 rétabli).

Une table **`_BLOCKED`** (déclaration UX pure : `state` = clause d'état SANS la condition
franchissable · `ok` = lecture de la condition dans `action_context` · `code`/`reason`/`actor`).
`blocked_actions()` : `state` vrai **et** action non-disponible **et** condition non satisfaite →
grisée. `state` faux → **absente** (pas de mur de 29 boutons). La règle vit dans le registre/endpoint ;
`_BLOCKED` n'expose que le libellé + l'acteur qui débloque.

`get_dossier` sert désormais `blocked_actions` + `awaiting` (DEC-C, états SOP/INC sans geste staff)
+ `bourses.default_validees` (DEC-D, sélection non conflictuelle via `_apply_exclusivity_local`,
**lecture seule** — aucun endpoint mutant, cas d'arrêt 5 évité).

---

## LE LIVRABLE — capture textuelle du rendu des 7 familles (jsdom pleine page)

Rendu réel du panneau d'actions de `dossier.astro` (dist bâti), piloté en jsdom pleine page. Ce que
l'Administratif voit exactement — **le bouton actif-qui-échoue est remplacé par un bouton grisé qui
dit POURQUOI et QUI doit agir** :

```
### FAMILLES 1-4 — Prépa ETU, notes NON validées (coef posés) — vue Administratif
  Saisir les notes de concours…   ·   Désister le dossier…            ← DISPONIBLES (prochain geste réel)
  Déclarer admissible        │ Notes du concours à valider — Responsable   ← GRISÉ
  Mettre en liste d'attente  │ Notes du concours à valider — Responsable   ← GRISÉ
  Admission conditionnelle   │ Notes du concours à valider — Responsable   ← GRISÉ
  Refuser                    │ Notes du concours à valider — Responsable   ← GRISÉ

### FAMILLE 5 — Saisir les notes : coefficients NON posés
  Saisir les notes du concours │ Coefficients des épreuves à poser — Responsable   ← GRISÉ

### FAMILLE 6 — Vérifier le diplôme : pièce diplôme NON déposée (ACO)
  Vérifier le diplôme          │ Diplôme du bac à déposer — Candidat              ← GRISÉ

### DEC-C — SOU pièces non vérifiées (vue Direction)
  Mettre à l'étude             │ Pièces requises à vérifier — Administratif        ← GRISÉ

### DEC-C — SOP : paiement à confirmer (awaiting)
  « Paiement des frais de candidature à confirmer avant la mise à l'étude — Administratif »

### DEC-C — INC : complément attendu (awaiting)
  « Complément demandé — en attente du candidat — Candidat »

### FAMILLE 7 — Accepter avec bourses (vue Direction) : aucune exclusive cochée par défaut
  Excellence — 50 %  → COCHÉE    [groupe MERITE (exclusif)]
  Mérite — 30 %      → décochée   [groupe MERITE (exclusif)]     ← plus jamais deux exclusives cochées
  Sociale — 20 %     → COCHÉE     [cumulable]
  ⇒ options exclusives cochées par défaut : 1 (la mieux dotée) — pré-validé AVANT l'ouverture
```

**aucun jsdomError.** Les `{code, reason, actor}` rendus sont EXACTEMENT ceux que le back
`blocked_actions()` produit (verrouillés par les tests unitaires §baselines).

---

## Couverture des DEC

| DEC | Preuve |
|---|---|
| **A** contrat enrichi | `get_dossier` sert `blocked_actions` (clé·acteur·code·raison) + `awaiting`. Conditions calculées serveur (miroir action_context) — le front ne déduit rien. |
| **B** désactiver, pas masquer | Rendu grisé (patron /notes répliqué inline : bouton `disabled` + raison + acteur) APRÈS les disponibles. Hors état ⇒ absent (test `test_hors_etat_reste_absent`, `…non_conditionnel_reste_absent`). |
| **C** ordre visible | `awaiting` SOP/INC nomme condition+acteur ; SOU/ACC/ACO couverts par `blocked_actions` (start_review/enroll/verify_bac). 5 cas rendus ci-dessus. |
| **D** bourses | `default_validees` serveur (une par groupe d'exclusivité, la mieux dotée) ; `boursesField` coche cette sélection — famille 7 : 1 exclusive cochée, pas 2. Conflit résolu AVANT la modale. |
| **E** libellés | `ADM` = **Admissible** sur les 3 écrans (`dossier.astro:335`, `liste-dossiers.astro:132`, `tableau-direction.astro:150` — extension accordée) ; `ACC` = **Accepté** inchangé (décision architecte). Grep de contrôle : 0 « ADM='Admis' » résiduel. |
| **F** verdict de pièce | Libellé « **Réviser le verdict** » quand un verdict existe (verified/rejected) ; **confirmation** ajoutée sur `verify` d'une pièce refusée (`emModal` existant → pas de bump ui.js). |
| **G** aucun assouplissement | `git diff` sur les gardes NT-S = **vide** ; seuls `_actions.py` (registre/exposition), `get_dossier` (sérialisation), front, tests touchés. Les endpoints refusent toujours (matrice de cohérence verte). |

---

## Baselines (constatées)

| Baseline | Résultat |
|---|---|
| Back `run-tests --app admission` | **1111/3** (1102 base + **9 tests `TestBlockedActions`** ; 3 `setUpClass` CONNUES : test_calendar/roles_hierarchy/sm_l0 — RQ/Redis, hors write-set) |
| `test_available_actions` (dont **matrice de cohérence** registre↔gardes) | **34/34 OK** — mes conditions ajoutées sont concordantes avec les gardes endpoint |
| Build management | **Complete!**, sans avertissement (dossier/liste-dossiers/tableau compilés) |
| jsdom pleine page 7 familles | **vert**, aucun jsdomError |
| recette / CSV notes | subsumées dans les 1111 verts (aucun module en erreur hors les 3 connues) |

`ui.js` **non modifié** (DEC-F réutilise `emModal({fields:[]})`) → **pas de bump CAL-13**. Dette
signalée : `ui.js` est chargé **sans `?v=`** (Layout/BareLayout) — même piège CAL-13, latent, hors mandat.

---

## Write-set & cas d'arrêt

Touché : back `admission/api/_actions.py` (registre + `_BLOCKED`/`blocked_actions` + helpers),
`admission/api/staff.py` (get_dossier : `coef_complete`, `blocked_actions`, `awaiting`,
`_bourses_payload`/`default_validees` — **aucun endpoint mutant, aucune garde**),
`admission/tests/test_available_actions.py` ; front `dossier.astro`, `liste-dossiers.astro`,
`tableau-direction.astro` (DEC-E, **extension accordée**, ligne ADM seule). `docs/NT-UX-PREUVES.md`.

Aucun cas d'arrêt rencontré : les raisons se calculent toutes sans affaiblir une garde (elles
miroitent l'existant) ; bourses en lecture seule ; aucune écriture concurrente dans les worktrees.
