# CONTRAT-1 — Schéma machine & tests de contrat : dossier de preuves

> Mandat DEC-L (recon → pause unique → exécution → rapport). **Arrêt au push · fusion/déploiement à
> l'architecte.** SHA constatés : back **`ebed28e`** · management **`c851502`** · applicant **`460bdef`**.
> Branches `mandat/contrat-1` (3 worktrees). Source citée, non refaite : `AUDIT-360-A1-INVENTAIRE`
> (table A1.2) + `AUDIT-360-A3-CONTRATS §2` (verdicts par champ). **Aucun fichier applicatif modifié**
> (diff : uniquement des fichiers NEUFS).

## Pourquoi un JSON Schema écrit à la main, et pas généré ? (DEC-A — à lire en premier)

Le critère de DEC-A : *le schéma ne doit pas pouvoir diverger silencieusement du code.* Les deux voies
littérales proposées étaient, **sous ce write-set, toutes deux des cas d'arrêt** :

- **« génération depuis le code »** — les endpoints construisent des `dict` **impérativement, sans
  annotation de type**. Il n'y a rien à introspecter statiquement.
- **« schéma déclaratif dont le code dérive »** — exigerait de **modifier les endpoints** pour qu'ils
  construisent leurs réponses depuis le schéma. Interdit (write-set + DEC-C : ne rien changer).

La voie retenue — **schéma déclaratif + validation exécutable BIDIRECTIONNELLE** — tient le critère
*mieux* que les deux voies littérales, parce qu'elle le tient **des deux côtés** :

| Voie | Garantit… |
|---|---|
| Génération depuis le code | le schéma suit le back — rien sur ce que le front consomme |
| Code dérivé du schéma | peut être mal édité ; exige de réécrire les endpoints |
| **Validation bidirectionnelle (retenue)** | le back **ne peut pas abandonner** un champ consommé (test back), le front **ne peut pas dépendre** d'un champ non garanti (test consommateur). **Prouvé à chaque run, dans les deux sens.** |

Le schéma est **le contrat épinglé**, vérifié contre la réalité à chaque exécution ; sa vérité n'est pas
affirmée, elle est **falsifiable** (voir plus bas). C'est pourquoi il est écrit à la main : ce n'est pas
un document mort, c'est la cible d'une double validation continue.

## Le mécanisme

- **Validateur autonome** `admission/contracts/validator.py` — ~90 lignes, **zéro dépendance**
  (`jsonschema` absent du bench). Sous-ensemble **volontairement minimal** : `type`, `required`,
  `properties`, `additionalProperties`, `items`, `enum`, `nullable`. **Refuse de valider en silence**
  tout mot-clé non supporté (`$ref`, `anyOf`…). **Testé lui-même** : `test_contract_validator.py`
  (13 tests) prouve qu'il ROUGIT sur chaque famille de violation (dont le piège `bool` ≠ `integer`) —
  *un validateur qui valide tout serait pire que pas de validateur.*
- **Schémas** `admission/contracts/schemas/<endpoint>.json` — décrivent le **`data`** ; l'enveloppe
  `{ok,data,error}` est composée une seule fois (`registry.py`). `required` = **champs consommés**
  (A3 §2.1) ; métadonnées `x-source`/`x-level`/`x-consumers` par fichier.
- **Test back** `test_contract_back.py` — appelle l'endpoint RÉEL (fixtures existantes, user Administrator
  comme les tests actuels) et valide la réponse. **Garantie primaire** de non-divergence, en un dépôt.
- **Test consommateur** (management + applicant, JS) — les champs LUS par le front (encodés depuis A3,
  `fichier:ligne`) doivent être **garantis par le schéma**. Le MÊME JSON Schema sert Python et JS.

## Falsifiabilité — prouvée, pas affirmée (DEC-B)

- **`test_contract_replays.py::TestFalsifiability`** : sur **4 endpoints réels** (config_health,
  ops_health, whoami, degraded_status), retirer **chaque** champ consommé (`required`) de la réponse
  fait ROUGIR la validation, avec l'erreur citant le champ. (≥3 exigé.)
- **Démonstration en direct** (rapportée, pas seulement expliquée) : en ajoutant temporairement un champ
  requis `refund_total` au schéma `get_degraded_status` (que le back ne sert pas), le test back a
  réellement échoué :
  ```
  AssertionError: ['$.data.refund_total: champ requis absent'] != []
  FAILED (failures=1)
  ```
  puis schéma restauré → vert. Le contrat rougit quand back et schéma divergent.

## Rejeu des 3 incidents historiques — ROUGE sur la forme d'origine

| Incident | Contrat qui l'attrape | Preuve (test) |
|---|---|---|
| **E-01** champ fantôme (`get_config_health` servait `kkiapay`, front lit `fedapay`) | la forme d'origine `kkiapay` validée contre le contrat `fedapay` **rougit** (fedapay requis absent + kkiapay non documenté) ; la forme courante `fedapay` est verte | `TestReplayE01PhantomField` (back) + `consumer.test.mjs` rejeu E-01 (management) |
| **E-02** double déballage (`close_session` : front lisait `data.data.total` → 0) | le chemin double-déballé `data.total` **n'existe pas** dans le data-schéma → rouge ; `total` (déballage correct) existe | `TestReplayE02DoubleUnwrap` (back) + `consumer.test.mjs` rejeu E-02 (management) |
| **CAL-10** paramètre `"null"` (chaîne) | `stringified_null_keys({academic_year:"null"})` → `["academic_year"]` (rouge) ; payload propre → `[]` | `TestReplayCAL10StringifiedNull` (back) |

Les formes d'origine sont **reconstruites depuis l'audit** (les bugs sont corrigés en PROD) ; chaque
rejeu montre le **rouge sur la forme buggée** et le **vert sur la forme corrigée** — le contrat discrimine.

## Niveau atteint par endpoint (DEC-E)

| # A1.2 | Endpoint | Consommateur | Schéma | Conformité back | Test conso. | Niveau |
|---|---|---|---|:--:|:--:|---|
| 1 | `admin_config.get_config_health` | management | ✓ | ✓ | ✓ (E-01) | **complet** |
| 4 | `admin_ops.get_ops_health` | management | ✓ | ✓ (additif) | ✓ | **complet** |
| 5 | `admin_referentiel.get_degraded_status` | management | ✓ | ✓ | ✓ | **complet** |
| 20 | `staff.whoami` | management | ✓ | ✓ | ✓ | **complet** |
| 27 | `staff.stats_direction` (décision) | management | ✓ | ✓ | ✓ | **complet** |
| 12 | `public.list_sessions` | applicant | ✓ | ✓ | ✓ | **complet** |
| 11 | `public.list_programmes` | applicant | ✓ | ✓ | ✓ | **complet** |
| — | `staff.close_session` (décision, E-02) | management | ✓ | différée (fixture session) | ✓ (E-02) | **schéma + conso. + rejeu** |
| 21 | `staff.list_dossiers` | management | **différé** | — | — | **différé — frontière PERF-1** |

**Frontière PERF-1** : `list_dossiers` et `liste-dossiers.astro` changent de contrat avec PERF-1
(livré/poussé `mandat/perf-1` back `2b2289b`, **non fusionné**). Mon socle est `ebed28e` (pré-PERF-1).
Décrire `list_dossiers` maintenant produirait un schéma **immédiatement périmé** (sa nouvelle pagination :
`total` = vrai décompte, `limite`, filtre/recherche serveur). **Décrit en dernier = reporté à la fusion
PERF-1** : le schéma sera rédigé depuis le contrat post-PERF-1 et back-testé à ce moment. `staff.py` **non
modifié**.

## Portée livrée — honnêteté DEC-F

Ce lot livre **le mécanisme complet, prouvé bout en bout** (validateur testé + schéma + conformité back +
conformité consommateur + falsifiabilité + 3 rejeux) sur une **première tranche de P1** : les endpoints
**porteurs des incidents** (E-01/E-02/CAL-10), les **diagnostics SM**, la **décision Direction** et les
**lectures publiques**. L'**inventaire DEC-D couvre TOUS les endpoints** (voir doc dédié).

Restent à couvrir en conformité back les endpoints **à fixtures lourdes** (`get_dossier`,
`staff.get_dossier`, `get_frais`, `get_recovered_dossier`, `calendar_*`, transferts, notes) : ils
partagent un fixture « dossier complet ». C'est la **continuation naturelle (sous-lot b)** — le mécanisme
et le harnais sont prêts, seul le fixture partagé reste à écrire. Je le signale comme convenu plutôt que
d'étirer le lot.

## Non-régression & coût

- **Suite back complète : `Ran 1148 tests — OK`** (baseline **1123** intacte + **25** tests de contrat,
  0 échec/0 erreur).
- **Coût mesuré** : les 25 tests de contrat = **~0,67 s** en isolation (validateur 0,002 s + back 0,47 s
  + rejeux 0,19 s) → **~2 % de la suite**, très en dessous du seuil de 30 %. **Gardés intégrés.**
- **Consommateur** : management `node --test tests/contract/*.test.mjs` = 7/7 ; applicant
  `node --test` = 2/2 (dont rejeux E-01/E-02 côté management).
- **Aucun fichier applicatif modifié** : `git status` = uniquement des fichiers `??` neufs, aucun `M`,
  dans les 3 dépôts. `package.json` non touché (tests lancés en direct / via le glob existant).

## Sur la relation canonique cross-dépôts

Le **canonique** est dans le back (`admission/contracts/schemas/`). Les fronts en portent une **copie
déclarée** (en-tête de `check.mjs`). La garantie que le back sert bien ces champs est portée par la
conformité back **dans le dépôt back** ; la copie front est l'**attente déclarée** du front. Amélioration
future (hors périmètre) : un check de synchronisation copie↔canonique. Documenté ici plutôt qu'affirmé.

## Fichiers (tous neufs)

- **back** : `admission/contracts/{validator,registry,consumer}.py`, `admission/contracts/schemas/*.json`
  (8), `admission/tests/test_contract_{validator,back,replays}.py`, `docs/CONTRAT-1-*.md`.
- **management** : `tests/contract/{check.mjs,consumer.test.mjs,schemas/*.json}`.
- **applicant** : `tests/contract-consumer.test.mjs`, `tests/contract/{check.mjs,schemas/*.json}`.
