# CONTRAT-3 — Couverture de contrat des endpoints lourds (volet A de CONTRAT-2) : preuves

> Mandat DEC-L. **Arrêt au push · fusion/déploiement à l'architecte.** SHA constatés : back
> **`4904719`** (tête PROD post-CONTRAT-2, branche `mandat/contrat-3`) · management de référence
> **`7ca1dd7`** (lecture des consommateurs, non modifié). **Write-set : `contracts/schemas/*` +
> `tests/*` + `docs/*` uniquement — aucun fichier applicatif.** Baseline **1178/0/0** préservée.

## Le recadrage acté (DEC-A) : `build_to` n'est pas un fixture in-process

CONTRAT-2 avait différé la conformité des endpoints lourds en supposant qu'il « suffisait de seeder
`SES-BACH-ASRC-2026` en base de test dev » pour que `recette_fixtures.build_to` tourne dans la suite.
**C'est faux, et prouvé** : `build_to` est un **pilote E2E HORS-PROCESSUS** — les gestes candidat
partent en **HTTP réel** vers `BASE` (défaut `https://api-admission-rec.lanem.bj`, le serveur de
recette), sa docstring dit « Usage : server-side, `bench execute` », jamais `run-tests`. Preuve
directe : pointé sur une session dev, `build_to("ACO")` échoue non sur une session manquante mais sur
`PermissionError: User Guest … Admission Session` — la requête est **partie sur le réseau**. **Aucun
seed de catalogue ne l'aurait rendu in-process.** La lettre de DEC-A tombe ; **l'intention est tenue** :
conformité des endpoints lourds prouvée **dans la suite standard**, par appel réel in-process.

Et le « verrou » n'en était un que pour **un seul** endpoint : **5 des 6** n'ont pas besoin de dossier.

## Les 6 endpoints, niveau déclaré par endpoint (DEC : niveau honnête)

| Endpoint | Schéma | Conformité | Niveau |
|---|---|---|---|
| `calendar_view.calendar_list` | ✓ | appel réel in-process (sessions dev) | **back-conformance** |
| `calendar_view.session_detail` | ✓ | appel réel in-process (une session dev) | **back-conformance** |
| `calendar_view.pending_queue` | ✓ | appel réel in-process (file vide conforme) | **back-conformance** |
| `staff.institutional_transfer_targets` | ✓ | appel réel in-process (une session dev) | **back-conformance** |
| `staff.list_notes_roster` | ✓ | appel réel in-process (roster vide conforme) | **back-conformance** |
| `staff.get_dossier` (**A4**) | ✓ | **VOIE (A)** dossier réel décoré + sérialiseur réel | **back-conformance (forme, pas workflow)** |

Skip gracieux si le décor manque (aucune session en base) — un test qui déclare ce qu'il ne peut
pas faire vaut mieux qu'un test qui échoue en silence.

## `get_dossier` — VOIE (A), et ce qu'elle couvre / ne couvre pas

On insère **un** `Admission Applicant` réel en état **BRO** (l'état initial — aucun statut ni verdict
avancé triché), rattaché à une session non-prépa existante, et on appelle le **vrai** `staff.get_dossier`.
Rollback automatique (`FrappeTestCase`).

- **CE QUE ça couvre** : la **forme du sérialiseur réel** — les 31 clés top-level, leurs types,
  `blocked_actions` verrouillé. `additionalProperties:false` en tête interdit toute clé fuitée.
- **CE QUE ça NE couvre PAS** : le **workflow**. Le dossier est *inséré décoré*, pas *construit par le
  tunnel métier* — ça reste le rôle des E2E (`test_claim_recovered_dossier`, `test_etude`) et de
  `build_to` en recette. Documenté explicitement dans l'en-tête du test (exigence architecte).

Deux corrections de schéma révélées par la **réponse réelle** (jamais par une forme écrite à la main),
d'où la valeur de la voie (A) : `available_actions` est une liste de **chaînes** (clés d'action), pas
d'objets ; l'échantillon INS du recon la montrait vide et masquait le type.

## A4 (DEC-F) — `blocked_actions` accepte l'item réservé Administratif

`get_dossier` sert `blocked_actions[]` de forme `{action, actor, code, reason}` (NT-UX-2 y a ajouté la
valeur réservée). Le schéma verrouille cet item (`additionalProperties:false`, `required` les 4 clés).
Preuve par le **code réel** : `_actions.blocked_actions(bro, ["Admission Direction"], is_prepa=False)`
retourne bien `{action:"confirm_payment", code:"RESERVED_TO_ADMINISTRATIF", …}` (un BRO vu par un
non-Administratif), et le schéma get_dossier **accepte** cet item injecté dans une réponse réelle.

## Falsifiabilité (DEC-D, ≥3, deux sens) — le contrat n'est pas vacant

La conformité de la réponse réelle (`test_get_dossier_real_serializer_conforms`) prouve que la base
**passe** ; chaque falsifiabilité prouve qu'une corruption **précise** la fait rougir :
1. **retirer** une clé consommée (`identite`, `statut`, `blocked_actions`, `pieces`) → rouge (`required`).
2. **ajouter** une clé hors schéma (`fuite_sur_reponse`) → rouge (`additionalProperties:false`).
3. un `blocked_action` **sans `code`** → rouge (item A4 strict).
4. `session_detail` + clé fantôme → rouge (sur un 2ᵉ endpoint).

## Non-régression & coût (DEC-G, < +10 %)

- **Suite : `Ran 1189 tests — OK`** (baseline **1178** + 11, 0 échec).
- **Coût** : le module `test_contract_endpoints` s'exécute en **~1,4 s** isolé → **< 3 %** d'une suite
  de ~50 s, sous le seuil **+10 %**. (Le temps ABSOLU de la suite varie avec la charge machine —
  37,9 s pour CONTRAT-2, 50,3 s ici : c'est la charge, pas les 11 tests légers ajoutés. La mesure
  honnête du surcoût est le runtime propre des tests ajoutés.)
- **Aucun fichier applicatif modifié** : diff = 6 schémas + 1 test + 2 docs (git status à l'appui).

## Fichiers

- `contracts/schemas/calendar_view.calendar_list.json`, `.session_detail.json`, `.pending_queue.json`
- `contracts/schemas/staff.institutional_transfer_targets.json`, `.list_notes_roster.json`, `.get_dossier.json`
- `tests/test_contract_endpoints.py` (5 conformité in-process + get_dossier voie (A) + A4 + 4 falsifiabilité)
- `docs/CONTRAT-3-PREUVES.md`, `docs/CONTRAT-3-B3-INVENTAIRE.md`
