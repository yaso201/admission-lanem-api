# CONTRAT-2 — Couverture de contrat + réduction des sur-réponses : preuves

> Mandat DEC-L. **Arrêt au push · fusion/déploiement à l'architecte.** SHA constatés : back **`dfed29f`**
> · management **`543ff4e`** · applicant **`d7c26a6`**. Branches `mandat/contrat-2`. Sous-lot (b) de
> CONTRAT-1 + faille 19. Sources citées, non refaites : `CONTRAT-1-PREUVES.md`, `CONTRAT-1-INVENTAIRE`,
> `AUDIT-360-A3 §2`. Baseline **1167/0/0** préservée. **Ordre DEC-A respecté : réduire puis décrire.**

## Volet B — les deux vraies sur-réponses (faille 19)

### B1 — `get_recovered_dossier` réduit (minimisation APDP, DEC-D) ✅
`get_recovered_dossier` servait **tout `_serialize_dossier`** (~17 clés). `reprise.astro` (**seul
consommateur, prouvé par grep sur les 3 dépôts** : `reprise.astro:318`) n'en lit que **6** + `reprenable`.
Nouveau `_serialize_recovered()` (`public.py`, projection tolérante) ne sert plus que
`dossier_id, statut, programme, session, identite, pieces` (+ `reprenable`). **Retirés de la
récupération OTP** : `profil_bac, bourses, promotion, paiement, convocation, conditionnel, motif_*,
rang_liste_attente` — données personnelles invisibles à l'écran.

**Preuves** :
- **Parcours de reprise inchangé bout en bout** — `test_claim_recovered_dossier` (**23**) +
  `test_identity_recovery` (**12**) verts après B1 : e-mail → OTP → liste → claim → **écriture
  persistée**. C'est le parcours le plus récemment réparé ; il n'est pas cassé.
- **Minimisation prouvée** — `test_contract_dossier` : les 11 champs personnels sont **absents** de la
  réponse réduite ; les 6 clés consommées **présentes** (rendu inchangé, DEC-E).
- **Falsifiabilité (2 sens)** : retirer une clé consommée → rouge ; **réintroduire** une clé retirée
  (régression de sur-réponse) → rouge via `additionalProperties:false`.

*Note isolation de test* : un test qui **mocke** `_serialize_dossier` partiellement a révélé qu'une
projection stricte `full[k]` lèverait `KeyError` — d'où la projection **tolérante** (`if k in full`),
robuste au réel comme aux mocks. Aucun test existant modifié.

### B2 — `institutional_transfer_preview.dossiers` : **CONSOMMÉ**, pas réduit ✅
L'aperçu **servait déjà** la liste nominative `dossiers[]` sans l'afficher. **Décision (ta pause)** :
la **consommer** — patron `close_session` (A3-FIX) : nommer ce qui va basculer **avant** l'acte de masse.
`calendrier-session.astro` affiche désormais « Dossiers concernés (N) : … » dans l'aperçu.
**Contraintes tenues** : la modale **défile** (`max-height:90vh; overflow:auto`, vérifié dans `ui.js`)
→ une liste de 40 dossiers **ne casse pas l'écran** ; **affichage seul**, aucun déclencheur ajouté
(le `submit` « Transférer » est inchangé). `ui.js` non modifié.

### B3 — ~30 champs sans consommateur : **rien supprimé** (DEC-C) ✅
La faille 19 est traitée par **B1 + B2** (les deux vraies sur-réponses). Le reste est de la réserve :
`CONTRAT-1-INVENTAIRE` les classe déjà (moniteur externe / webhook / écran futur possibles). **Aucune
suppression dans ce lot** — l'absence de consommateur *front* ne prouve pas l'absence de consommateur
*tout court* (cas d'arrêt #5). Le doute conserve ; l'inventaire reste la référence, à arbitrer à part.

## Volet A — couverture de contrat

### A1 — le fixture « dossier complet » : ⚠️ dépendance d'environnement signalée
`recette_fixtures.build_to("ACO")` construit un dossier **par le vrai tunnel** — approche validée. **Mais**
il copie la session-source `SES-BACH-ASRC-2026` (catalogue recette) **absente de la base de test locale
`admission-dev.localhost`** (qui a `SES-2026-LIC` etc.). `build_to` **n'est utilisé par aucun test
unitaire** — c'est un fixture d'env recette. → Le fixture fonctionne **en recette**, pas en dev local
sans seed du catalogue. **Conséquence** : la conformité des endpoints à fixture lourde
(`get_dossier` staff, calendrier, notes, transferts) est **différée** (schéma rédigeable, exécution en
recette OU seed du catalogue en dev — à arbitrer). B1 est prouvé **sans** le fixture (projection mockée
+ e2e réels). *(Cas d'arrêt #3 non déclenché : aucun doctype modifié ; c'est un manque de DONNÉE de seed.)*

### A2/A3 — schémas + conformité (niveau déclaré par endpoint)
| Endpoint | Schéma | Conformité back | Niveau |
|---|---|---|---|
| `public.get_recovered_dossier` (B1) | ✓ | ✓ (projection réelle mockée) | **complet + minimisation** |
| `staff.list_dossiers` (**A3**, forme PERF-1 stable) | ✓ | ✓ (`test_list_dossiers_conforms`, réponse réelle) | **complet** |
| `public.get_frais` (A2) | ✓ | ✓ si session payante (skip gracieux sinon) | **complet/skip-env** |
| `staff.institutional_transfer_preview` (B2) | ✓ (`dossiers` requis) | différée (fixture transfert) | **schéma + consumer** |
| `staff.get_dossier`, `calendar_*`, notes, transferts | à rédiger | **différée** (fixture recette) | **différé — voir A1** |

### A4 — valeurs NT-UX-2 sur `blocked_actions` (documentées ici, jamais au schéma d'un autre lot)
`get_dossier` sert `blocked_actions[]` de forme inchangée `{action, actor, code, reason}` ; NT-UX-2 y a
ajouté **deux valeurs** : `action = "confirm_payment"`, `code = "RESERVED_TO_ADMINISTRATIF"`. À intégrer
au futur schéma `staff.get_dossier` (quand la conformité lourde sera activée).

## Non-régression & coût
- **Suite : `Ran 1178 tests — OK`** (baseline **1167** + 11 nouveaux, 0 échec).
- **Coût** : les tests de contrat ajoutés ≈ 1,3 s ; suite 37,9 s → **< 5 %**, sous le seuil **+10 %**. Intégrés.
- **Aucun changement de rendu** : B1 (reprise lit les mêmes 6 clés) ; B2 (affichage additif, submit inchangé).

## Portée & découpage (DEC-F)
Livré : **B1** (prouvé) · **B2** (consommé) · **B3** (conservateur) · **A3 list_dossiers** · **A2
get_frais/get_recovered** · **A4** documenté. **Différé et signalé** : conformité des endpoints à
**fixture lourde** — bloquée par la dépendance d'env de `build_to` (A1). Continuation : seed du catalogue
en dev, OU exécution de conformité en recette. Le mécanisme et les schémas restants sont prêts à poser.

## Fichiers (back = neufs sauf public.py B1 ; mgmt = calendrier-session B2)
- back : `public.py` (B1 : `_serialize_recovered`), `contracts/schemas/{get_recovered_dossier,
  list_dossiers,get_frais,institutional_transfer_preview}.json`, `tests/test_contract_dossier.py`, docs.
- management : `calendrier-session.astro` (B2, affichage `dossiers[]`), docs.
