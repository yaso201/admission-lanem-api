# PIECES-REQ — Exigence figée après dépôt — Preuves

**Mandat :** PIECES-REQ — l'exigence d'une pièce (Exiger / Dispenser / Réinitialiser) se décide **tant que la pièce est absente** ; dès qu'elle est déposée, seul le **verdict** subsiste (DEC-O). Protocole DEC-L (recon → une pause → exécution continue → rapport, *arrêt au push*).
**Branche :** `mandat/pieces-req` sur les 2 dépôts. Têtes constatées après fusions : **back `28df720`** (CAL-DEL — DS-CLEAN n'a pas touché le back), **management `e89aa7d`** (DS-CLEAN fusionné). **Baseline back : 1160 / 0 / 0.**

| Dépôt | Write-set |
|---|---|
| back `admission` | `api/staff.py` (garde des 3 endpoints d'exigence), `tests/test_pieces_verification.py`, `docs/PIECES-REQ-PREUVES.md` |
| front management | `src/pages/dossier.astro` — `renderPieces`/`renderSummary` uniquement (boutons, badges, compteur) |

---

## 1. Recon — le défaut, localisé
Les endpoints de **verdict** gardent déjà `status=="missing"` (`verify_piece`/`reject_piece` → `PIECE_NOT_UPLOADED`). Les **3 endpoints d'exigence** n'avaient **aucune garde de statut** :

| Endpoint | Écrivait `staff_requirement` sans vérifier le statut |
|---|---|
| `require_piece` (staff.py) | ❌ |
| `waive_piece` | ❌ |
| `reset_piece_requirement` | ❌ |

*(Le « 5 endpoints » de l'audit = les 5 endpoints d'état de pièce ; 2 sont du verdict, déjà gardés. Seuls ces 3 écrivent `staff_requirement`, et leur unique consommateur est `dossier.astro` via `api.js`.)*

**États réels** : `status ∈ {missing, uploaded, verified, rejected}` · `staff_requirement ∈ {default, required, waived}`. Source unique d'exigence : `public.requise_effective` (`waived→False`, `required→True`, `default→structurel`).

**Complétude** : `requise_effective` exclut déjà les waived → une pièce dispensée puis déposée n'entre pas dans la complétude **par construction**. Aucun endpoint hors write-set touché (cas d'arrêt #1 évité).

## 2. Correctif

**Back (DEC-O-1/2/4/8)** — garde partagée, miroir INVERSE de la garde de verdict :
```python
if row.status != "missing":
    return _requirement_locked_error(action)   # 409 REQUIREMENT_LOCKED
```
Messages actionnables : `waive` → « Une pièce déposée ne se dispense pas : refusez-la avec un motif (traçable) » (nomme le bon geste, DEC-O-2) ; `require`/`reset` → « L'exigence se décide avant le dépôt ; cette pièce est déjà déposée ».

**Front (DEC-O-6/7 + D-C/D-D)** — `dossier.astro`, *masquer pas griser* :
- Boutons Exiger/Dispenser/Réinitialiser rendus **seulement si `statut==='missing'`**.
- Badges de surcharge **Exigée ET Dispensée** masqués dès `statut!=='missing'` (la surcharge devient inerte — **aucune purge**, elle reste en base).
- Compteur « Complétude X / Y » rendu **`requise`-based** (waived exclu). Badge « à fournir/Complètes » aligné sur le **même critère `fournie` (uploaded|verified)** → les deux compteurs **concordent**.

**Aucun nouveau champ serveur** (le front a déjà `statut` et `requise`) → `get_dossier` inchangé → **CONTRAT-1 vert** (get_dossier hors contrat de toute façon).

## 3. Rendu d'une ligne de pièce dans les 4 états — capture jsdom pleine page
Pilotage réel de `dossier.astro` (vraie lib EmelaUI, `getDossier` stubée) :
```
  statut     pièce               boutons                     badge surcharge
  missing    p_miss_default      [require,waive]             —
  missing    p_miss_req          [waive,reset]               Exigée
  uploaded   p_uploaded_req      [verify,reject,view]        —      ← Exigée MASQUÉE (le bug corrigé)
  verified   p_verified          [reject,view]               —
  rejected   p_rejected          [verify,view]               —
```
*(Sur `missing`, l'axe exigence n'affiche que les transitions utiles : une pièce déjà `required` montre Dispenser + Réinitialiser, pas Exiger — comportement inchangé. Le verdict `verify`/`reject` reste offert selon le statut, exactement comme avant.)*

## 4. Check-list de sortie — 8 points prouvés

| # | Cas | Preuve |
|---|---|---|
| **1** | `missing` : Exiger/Dispenser/Réinitialiser présents et fonctionnels | jsdom (axe exigence présent sur missing) + back `test_v5_require`/`v6_waive`/`vr1`/`vr2` (inchangés, verts) |
| **2** | déposée (`uploaded`/`verified`/`rejected`) : 3 boutons absents, badge « Exigée » disparu | jsdom (axe absent + badge `—` sur les 3 états) |
| **3** | chemin forcé : `require_piece` sur pièce déposée → refus serveur motivé | back `test_require_locked_on_deposited_states` (REQUIREMENT_LOCKED, surcharge inchangée) |
| **4** | `missing` retrouvé → boutons réapparaissent | back `test_require_allowed_again_when_missing` (règle suit `piece.status`) |
| **5** | complétude juste : dispensée+déposée ne compte pas ; X/Y exact + **concordance** | jsdom : MIXTE « 3 à fournir » = 5−2 ; **COMPLET « Complètes » ⟺ X/Y au max** |
| **6** | aucune purge | back `test_reset_locked_on_deposited` (surcharge `required` **préservée** en base, inerte) |
| **7** | verdict inchangé (Vérifier/Refuser/Réviser) | jsdom (verdict présent sur uploaded + rejected) + tests verdict existants verts |
| **8** | non-régression | suite back **1164 / 0 / 0** (1160 + 4) · CONTRAT-1 vert · build management propre · jsdom vert |

**DEC-O-2 spécifique** : `test_waive_locked_message_guides_to_reject` vérifie que le message oriente vers « refusez ». **D-C** : `waived`+déposée → badge Dispensée masqué (jsdom).

## 5. Concordance des deux compteurs (exigence de vérification D-D)
```
MIXTE   badge « 3 à fournir »   X/Y = 2 / 5   → à-fournir(3) == total−done(5−2) ✓
COMPLET badge « Complètes »     X/Y = 2 / 2   → « Complètes » ⟺ X/Y au maximum ✓
```
Les deux indicateurs disent désormais **la même chose** : une pièce requise rejetée compte comme « à fournir » (dossier non complet) dans les deux, une waived-déposée est exclue des deux.

## 6. Coordination NT-UX-2
`dossier.astro` évolue en parallèle (NT-UX-2 ajoute un signal rôle-workflow au **bloc Actions / bouton Confirmer**, `renderActions`). **Mes régions** : `renderPieces` (l.~421 badge, ~434 badges surcharge, ~449 boutons exigence) et `renderSummary` (l.~652 compteur). **Disjointes** de `renderActions` → rebase sans conflit, dans l'ordre de libération.

## 7. Arrêt au push
Conformément à DEC-L : arrêt au push des deux branches. Fusion et déploiement appartiennent à l'architecte. **Aucune migration** (aucun doctype touché).
