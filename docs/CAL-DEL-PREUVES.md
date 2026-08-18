# CAL-DEL — Suppression d'une session sans engagement — Preuves

**Mandat :** CAL-DEL — la Direction peut supprimer une session créée par erreur **tant qu'aucune candidature n'y est rattachée**. Protocole DEC-L (recon → une pause → exécution continue → rapport, *arrêt au push*).
**Branche :** `mandat/cal-del` sur les 2 dépôts, depuis les têtes PROD : **back `971d91e`**, **management `69e1e26`**.

| Dépôt | Write-set |
|---|---|
| back `admission` | `api/calendar_rules.py` (verdict GK9), `api/calendar.py` (garde), `api/calendar_view.py` (`can_delete`), `doctype/admission_session_change_log` (+acte `suppression`), `tests/test_calendar.py`, `docs/CAL-DEL-PREUVES.md` |
| front management | `src/pages/calendrier.astro` — **`<script>` uniquement** (bouton conditionnel + modale nommée) ; **aucune ligne CSS** → frontière DS-CLEAN (CSS) respectée |

---

## 1. Recon DEC-D — l'inventaire complet (doublement confirmé)
Balayage exhaustif (direct + agent indépendant) de tout objet référençant une `Admission Session`. **Aucun Dynamic Link.**

| Objet | Réf → session | Comportement | Traitement CAL-DEL |
|---|---|---|---|
| Admission Applicant | `session` (**Link**) | bloque (`LinkExistsError`) | 🔴 refus « Des dossiers sont rattachés » |
| Applicant Fee | `session` (**Link**) | bloque | 🔴 refus « Des frais sont rattachés » (précision architecte) |
| Admission Applicant Transfer Log | `from_session`, `to_session` (**Link**) | bloque | 🔴 refus « référencée par un transfert » — **le piège** : 0 dossier aujourd'hui, référencée hier |
| Admission Session (child `pending_changes`) | table enfant | cascade | 🔴 refus « proposition en attente » (maker-checker en vol) |
| Admission Session Reminder | `session` (**Data**) | orphelin sinon | 🟠 **supprimé explicitement** (satellite système) |
| Admission Session Change Log | `session` (**Data**) | survit | 🟢 **journal DEC-P — SURVIT** à l'objet (la trace) |
| Admission Note Change Log | `session` (**Data**) | survit | 🟢 audit ; n'existe pas à 0 dossier |

**Filet structurel (D1)** : `frappe.delete_doc` garde l'intégrité des liens **ACTIVE** — refuse tout Link engageant manqué, jamais d'orphelin ni de cascade destructrice. Les vérifications explicites donnent le **motif exact** avant d'y arriver.

## 2. Conception — verdict GK9 unique (D5)
`calendar_rules.deletion_verdict(session) → {deletable, reason, required_role, state}` — **source unique** consommée par le garde serveur (`calendar._delete_draft`) **et** le `can_delete` du front (`calendar_view._serialize`). Zéro divergence bouton↔serveur (patron NT-UX).
- **États éligibles (DEC-C)** : `Draft`, `Open`. `Closed` → jamais (« appartient à l'historique »).
- **Rôle par état (DEC-A)** : `Open` → `DIR_UP` (Direction), `Draft` → `RESP_UP` (Responsable, **inchangé**).
- **Ordre de suppression (V-LEARN-PURGE-14)** : journaliser `suppression` (trace Data qui survit, code + libellé + état) → supprimer les rappels (satellite standalone) → `delete_doc` (intégrité ON) → un seul commit.

## 3. Check-list de sortie — 9 points prouvés

| # | Cas | Preuve |
|---|---|---|
| **1** | Open · 0 dossier · Direction → bouton + modale nommée + suppression effective | back `test_open_empty_is_deletable` (verdict deletable, required_role DIR, session supprimée) · jsdom : bouton présent (dir/OPENOK) |
| **2** | Open · ≥1 dossier (incl. `BRO`) → aucun bouton + refus serveur | back `test_open_with_dossier_blocked` (motif « dossiers », `ValidationError`, session intacte) · jsdom : pas de bouton (OPENENG) |
| **3** | Closed → non supprimable | back `test_closed_not_deletable` (`required_role=None`, motif « historique », refus) · jsdom : pas de bouton (CLOSED) |
| **4** | Draft → **inchangé**, Responsable+ | back `test_draft_deletable_resp` (deletable, required_role RESP) · jsdom : bouton présent (resp/DRAFT) |
| **5** | Responsable sur Open → refusé ; Direction → autorisé | back `test_role_gate_by_state` (`only_for(DIR_UP)` pour Open, `RESP_UP` pour Draft) · jsdom : resp ne voit **pas** le bouton sur Open |
| **6** | Open · 0 dossier · **transfert** → refus motivé | back `test_open_with_transfer_blocked_both_directions` — `from_session` **et** `to_session`, sur sessions à 0 dossier, motif « transfert ». **Le cas réel qui motive DEC-D.** |
| **7** | La suppression laisse une ligne de journal | back `test_deletion_journals_surviving_line` (acte `suppression`, `author`+`at`, **libellé « Prépa Octobre » dans la trace**, objet parti, trace restée) |
| **8** | Aucun orphelin après suppression | back `test_deletion_leaves_no_orphan` (rappel présent avant → nettoyé après ; session absente) |
| **9** | Non-régression | suite back **1160 / 0 / 0** · CONTRAT-1 vert (voir §5) · build management propre · jsdom vert |

Couverture additionnelle : `test_open_with_fee_blocked` (frais orphelins), `test_open_with_pending_change_blocked` (proposition), `test_can_delete_reflects_predicate` (le drapeau reflète le prédicat complet).

## 4. Modale nommée (DEC-F) — capture jsdom pleine page
Pilotage jsdom de `calendrier.astro` (vraies libs EmelaUI + EmelaCal, EmelaAPI stubée) :
```
title       : "Supprimer ZZ-OPENOK ?"                       (code présent)
body        : "Cycle préparatoire — Prépa Octobre — la session disparaît définitivement.
               Aucune candidature n’y est rattachée. Acte irréversible."   (libellé + conséquence)
submitLabel : "Supprimer ZZ-OPENOK"     danger : true
onSubmit    → API.calendarDeleteDraft("ZZ-OPENOK")
✓ aucun jsdomError
```

## 5. CONTRAT-1 — reste vert, aucun schéma touché
`can_delete` **reste un booléen** (change de valeur, pas de forme). Le seul schéma CONTRAT-1 sur les sessions est `public.list_sessions` (côté **candidat**, sans `can_delete`) ; `calendar_list`/`session_detail` (management) ne sont pas sous contrat. → **aucune mise à jour de schéma**, tests de contrat verts (inclus dans les 1160).

## 6. Migrate requis (D7)
L'acte `suppression` est une **nouvelle option Select** de `Admission Session Change Log.action_type` → **`bench migrate` requis au déploiement** (sync du docfield ; aucun patch de données). Sans lui, `validate_select` rejetterait l'insertion du journal.

## 7. Instructions post-fusion
- Ordre : back d'abord (le front en dépend), puis management.
- **`bench migrate`** sur le back (option Select) — **pas** de patch de données.
- Le lot livre la **capacité** ; il n'exécute **aucune** suppression en production (SES-TEST-100 et les dossiers PROD intouchés).
- Le cas réel `SES-TEST-100-2728` (Open, 0 dossier) devient supprimable par la Direction — à exécuter par l'utilisateur si souhaité, hors de ce lot.

## 8. Arrêt au push
Conformément à DEC-L : arrêt au push des deux branches `mandat/cal-del`. Fusion, migrate et déploiement appartiennent à l'architecte.
