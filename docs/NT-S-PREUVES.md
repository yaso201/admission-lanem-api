# NT-S — dossier de preuves pré-fusion

Date : 2026-08-17

Nature : invariants serveur admission, exécutés dans le worktree back isolé
`admission/worktrees/nt-s-back`, branche `mandat/nt-s`. Aucun front n'est nécessaire.
Le mandat s'arrête au push de cette branche : aucun merge, déploiement ou accès PROD en écriture.

Base réelle : **`80bbcaf3988e06a4cffc20075ddec1ff6e9bdabf`**, identique à
`origin/main` au départ. Cette tête inclut OUVERTURE-SOP. Le front management est resté sur
`b0eb3f6` et n'a pas reçu de worktree, l'alignement des actions étant intégralement calculé par
le back.

## 1. Verdict

Le CRUD générique des trois rôles métier ne peut plus modifier les dossiers, frais ou paiements.
Les endpoints dédiés restent fonctionnels. Les décisions Prépa exigent une validation de notes
**antérieure** à la décision, même sous System Manager ou `ignore_permissions=True`. Un paiement
confirmé est irréversible pour tous. Les clôtures Prépa bloquantes sont refusées en totalité et
le dry-run nomme les dossiers à corriger. Les actions de paiement servies au front et les endpoints
partagent la même matrice d'états. Les coefficients Licence sont refusés par le serveur.

## 2. Write-set déclaré contre diff réel

| Fichier | Intervention |
|---|---|
| `admission_applicant.json` | retrait de `write` aux rôles Administratif, Responsable et Direction ; lecture conservée ; System Manager inchangé |
| `admission_applicant.py` | garde décision Prépa au contrôleur ; break-glass tracé ; warning même acteur ; fermeture de session via bypass Workflow ciblé, jamais du contrôleur |
| `applicant_fee.json` | retrait de `write` à Administratif, conformément à l'arbitrage |
| `applicant_fee.py` | warning structuré des écritures break-glass |
| `applicant_fee_payment.json` | retrait de `write` à Administratif ; champ interne `confirmed_by` |
| `applicant_fee_payment.py` | irréversibilité `Confirmed` ; warning break-glass |
| `api/_actions.py` | source unique des bornes frais 1/frais 2 et alignement `can_manage_payments` |
| `api/staff.py` | gardes confirmation/acceptation/coefficients ; `confirmed_by` ; clôture atomique via contrôleur |
| `tests/test_nt_s.py` | 18 tests ciblés et traceur DEV transactionnel |
| `tests/test_lot_w.py` | contrat de clôture mis à jour : tout-ou-rien et `doc.save` |
| `tests/test_pay_confirm.py` | mocks rendus fidèles à l'état et au type de frais ; preuve `confirmed_by` |
| `tests/test_pay_receipt.py` | mock rendu fidèle à la nouvelle garde d'état |
| `docs/NT-S-PREUVES.md` | présent dossier |

Hors diff : `public.py`, `exam_grading.py`, les fichiers calendrier, les fronts et le corpus.
Aucun patch applicatif n'est requis : `bench migrate` synchronise les DocPerms et le champ
`confirmed_by` depuis les JSON. Aucun asset public n'est touché, donc CAL-13 est sans objet.

### Champs protégés

- `Admission Applicant` : statut et session ; motifs, acteur/date de décision et rang ; notes,
  validation et ses stamps ; condition, vérification du bac et ses stamps ; resoumission ;
  propositions/validations de bourses et leurs stamps ; table des pièces.
- `Applicant Fee` : dossier, session, identité, type, montant et statut.
- `Applicant Fee Payment` : liens, canal/source, montant, statut/date, justificatif, données
  fournisseur et idempotence, réconciliation, notification UF et `confirmed_by`.

Le verrou de permission est volontairement plus fort qu'une liste de champs : pour les rôles
métier, toute mutation générique est fermée. Les actes légitimes passent par les endpoints et
leurs `save(ignore_permissions=True)` gardés.

## 3. Invariants livrés

### DEC-A — double verrou

Les DocPerms ferment REST/Desk/Workflow génériques aux rôles métier. Les contrôleurs portent les
invariants absolus afin qu'un endpoint interne, un script ou un System Manager ne les contourne
pas. Les trois scénarios de l'audit ont été rejoués sur la base DEV après migrate.

Le System Manager garde une voie d'incident. Une écriture générique sur chacun des trois doctypes
produit à la fois une ligne `Version` (`track_changes=1`) et un warning JSON
`break_glass_sensitive_write`. Le warning ne contient jamais les anciennes/nouvelles valeurs.
Cette voie ne contourne ni la garde Prépa ni l'irréversibilité d'un paiement confirmé.

### DEC-B — décision Prépa

Une transition vers `ADM`, `ATT`, `ACO`, `REF` ou `ACC` vérifie au contrôleur que
`notes_validated` valait déjà 1 dans le document sauvegardé et vaut encore 1. Il est donc impossible
de forger `notes_validated=1` dans la même sauvegarde que la décision. La garde est en outre
réaffirmée dans `accept_admission` ; les endpoints ADM/ATT/REF/ACO la portaient déjà.

Licence reste hors de cette règle : `mark_admissible` sans notes aboutit.

### DEC-C — bornes de paiement

| Type de frais | États autorisés |
|---|---|
| `application`, `competition` | `BRO`, `SOP`, `SOU` |
| `enrollment` | `ACC` |

La même source est appelée par `confirm_offline_payment`, `initiate_online_payment` et
`can_manage_payments`. La borne s'applique à toute nouvelle confirmation `Pending`; le rejeu d'un
paiement déjà acquis reste idempotent, même si le dossier a avancé, et ne produit aucun effet.
Les preuves endpoint refusent frais 1 en `ETU` et frais 2 en `SOU` ; les
tests historiques prouvent aussi l'initiation frais 1 refusée en `REF`, frais 2 refusée en `ETU`,
et les chemins nominaux frais 1 `SOP` / frais 2 `ACC`.

### DEC-D — paiement confirmé irréversible

Le contrôleur `Applicant Fee Payment` refuse toute transition depuis `Confirmed` vers une autre
valeur, y compris sous Administrator/System Manager. Aucun acte d'annulation comptable n'est créé
en V1. Celui-ci reste une capacité V1.1 : endpoint dédié, motif obligatoire, journal et compensation
UF explicite.

### DEC-E/F/G

- Les coefficients d'une session non-Prépa retournent `NOT_PREPA` avant validation ou écriture.
- La confirmation offline enregistre le compte dans `confirmed_by`. Si ce même compte décide
  ensuite du dossier, le contrôleur émet `actor_separation/same_actor_payment_decision` au niveau
  warning, sans bloquer la décision.
- L'autorité de `SOU→ETU` reste Administratif dans le code. La correction documentaire D11 est
  proposée au corpus, sans modification documentaire dans ce lot.

### Clôture de session Prépa

Le dry-run renvoie `can_execute=false`, `blocking_dossiers` triés et un message tel que
« 3 dossiers Prépa sans notes validées : X, Y, Z ». L'exécution effectue la même précondition avant
toute écriture. Si un échec survient ensuite, session, dossiers et rejets de Pending sont rollbackés
ensemble et aucune notification n'est envoyée. Les transitions utilisent désormais le contrôleur ;
seule la topologie Workflow incomplète est bypassée par un flag privé borné par `try/finally`.

## 4. Rejeu runtime DEV et purge

Commande :

```sh
PYTHONPATH=<worktree>:<bench>/apps/frappe \
  bench --site admission-dev.localhost execute admission.tests.test_nt_s.run_runtime_trace
```

Résultat final :

```text
NT_S_RUNTIME_TRACE::{
  "confirmed_irreversible": "REFUSED_FOR_SM",
  "licence_coefficients": "NOT_PREPA",
  "licence_without_notes": "ADM_OK",
  "notes_dedicated": "OK_LOG_DELTA_1",
  "notes_generic_crud": "PERMISSION_REFUSED",
  "prepa_close": "DRY_RUN_NAMED_AND_EXECUTION_REFUSED",
  "prepa_controller": "NOTES_NOT_VALIDATED",
  "prepa_generic_crud": "PERMISSION_REFUSED",
  "prepa_generic_workflow": "PERMISSION_REFUSED",
  "prepa_sm_combined_forge": "REFUSED",
  "purge": "BASELINE_RESTORED",
  "same_actor": "WARNING_NON_BLOCKING",
  "sm_break_glass": "THREE_DOCTYPES_VERSION_PLUS_WARNING"
}
```

Le traceur crée uniquement des objets `ZZTEST-`/`@test.lanem.bj` sous savepoint, restaure
l'utilisateur et les flags, rollbacke, puis compare exactement les compteurs avant/après de sept
doctypes (`Admission Applicant`, frais, paiements, deux journaux, `User`, `Version`). La première
extension du harnais a employé une valeur Select de réconciliation invalide et a été refusée par
Frappe ; son `finally` a rollbacké. La valeur a été remplacée par une option valide, puis la preuve
finale ci-dessus et `BASELINE_RESTORED` ont été obtenues.

## 5. Balayage exhaustif des écritures directes

Recherche : tous les `db.set_value`, `db.sql`, `db.delete` des sources Python, puis inspection des
appels dynamiques. Résultat : **aucun SQL `UPDATE`, `INSERT`, `DELETE` ou `REPLACE`** contre
`tabAdmission Applicant`, `tabApplicant Fee` ou `tabApplicant Fee Payment`.

| Chemin direct restant | Verdict |
|---|---|
| `_recette_notes.py` | fixture DEV seulement : pose ETU et résidu de note pour les scénarios ; jamais importée par une route runtime |
| `convocation.py` | légitime technique : numéro et dates d'envoi/réémission de convocation |
| `bridge.py` | légitime technique : état, date et erreur du pont campus |
| `notifications.py` | légitime technique : drapeaux/date de rappels J-6, J-4 et SOP |
| `notify_uf.py` | légitime technique : `uf_notified` et date, après notification acquise |
| `retention.py` | légitime et spécialisé : anonymisation PII/credentials, purge OTP et date de préavis ; aucun statut de décision |
| `public.py` — dossier | endpoints candidat gardés : `INC→SOU`, marqueur `resoumis`, `BRO→SOP`, cascade confirmée `BRO/SOP→SOU` ; aucune transition de décision |
| `public.py` — paiement | Pending supplanté/rejeté et marqueur de réconciliation, avec sélection préalable des Pending ; jamais `Confirmed→Pending` |
| `webhook.py` | chemin fournisseur : métadonnées de réconciliation et `Pending→Rejected` sous verrou ; la promotion confirmée utilise `doc.save` |
| `staff.py` — transfert | `Applicant Fee.session` suit le dossier, acte explicite DEC-AB ; aucun statut comptable changé |
| `staff.py` — clôture | seuls paiements sélectionnés `Pending` passent `Rejected`; le dossier lui-même a été **converti** de `db.set_value` à `doc.save` |

Aucun appel direct restant ne pose un état de décision (`ADM/ATT/ACO/REF/ACC`) ni ne rétrograde un
paiement confirmé. Les écritures techniques ne gagnent rien à déclencher le Workflow ; les actes
métier restants ont chacun leur endpoint et leurs préconditions. Aucun cas hors write-set n'est
signalé.

## 6. Tests, migrate et baselines

### TDD et ciblés

- RED initial NT-S : `Ran 16`, `failures=4`, `errors=13` avant implémentation.
- GREEN final NT-S : **18/18**.
- Traceur réel : toutes les clés ci-dessus, purge exacte.
- Modules touchés/reliés : `test_pay_confirm` 7/7, `test_pay_receipt` 5/5,
  `test_pay_state_guard` 6/6, `test_justificatif` 9/9, `test_available_actions` 25/25,
  `test_concours` 48/48, `test_lot_w` 12/12, `test_transition_log` 8/8,
  `test_aco` 31/31.

### Migration DEV

`bench --site admission-dev.localhost migrate`, avec le `PYTHONPATH` du worktree : **succès**.
Synchronisation des doctypes Frappe/admission, hooks `after_migrate` et reconstruction recherche
enfilée, aucune erreur. Le traceur post-migrate prouve les nouvelles permissions au runtime.

### Baselines finales — ligne de résumé, jamais le code de sortie

| Harnais | Résultat |
|---|---|
| Suite back | **Ran 1100 tests — errors=3, failures=0** |
| Recette notes | **48 PASS / 0 FAIL**, `Cleanup : fixtures purgées` |
| Contrat CSV | **7/7 OK** |
| Syntaxe | `py_compile` ciblé + `git diff --check` : propres |

Les trois erreurs sont exactement les trois `setUpClass` préexistants, signature
`_pickle.PicklingError: MagicMock` dans RQ/`after_commit` : `TestCal09DecE`,
`TestRolesHierarchyHelper`, `TestHardenPatch`.

La baseline transmise 1069/3 était antérieure aux têtes imposées. La base réelle `80bbcaf`
contient ensuite CAL-14 (**+6 tests**) et OUVERTURE-SOP (**+7 tests**) : **1082/3** avant NT-S.
NT-S ajoute 18 tests, donc **1100/3**, sans nouveau rouge.

## 7. Check-list NT-S

1. ✅ Rejeu CRUD et Workflow Desk Responsable ETU→ADM : permissions refusées ;
   `ignore_permissions` : garde notes.
2. ✅ Notes par CRUD Administratif refusées ; invalidation dédiée : +1 journal.
3. ✅ `Confirmed→Pending` refusé sous System Manager.
4. ✅ Garde contrôleur sur ADM/ACC/REF et validation nécessairement antérieure.
5. ✅ Licence sans notes : `ADM_OK` au runtime.
6. ✅ Bornes frais 1/frais 2 prouvées aux helpers et endpoints ; `can_manage_payments` aligné.
7. ✅ Coefficients Licence : `NOT_PREPA` au runtime.
8. ✅ Même acteur : warning unique, décision non bloquée.
9. ✅ Break-glass : `Version` + warning sur les trois doctypes ; deux invariants absolus fermés.
10. ✅ Migrate, suite 1100/3, recette 48/48 et CSV 7/7.

`SES-TEST-100` et les cinq dossiers Prépa PROD n'ont jamais été modifiés par ce lot ; leur état est
un constat en lecture seule hérité de l'audit. Toute la preuve d'écriture est DEV et rollbackée.

## 8. Propositions corpus

- **DEC-A** : trois doctypes admission sensibles sans write générique pour A/R/D ; mutations par
  endpoints ; System Manager break-glass `Version` + warning sans valeurs.
- **DEC-B** : toute décision Prépa exige une validation de notes antérieure ; invariant contrôleur,
  applicable à tous les chemins, System Manager inclus.
- **DEC-C** : frais 1 seulement BRO/SOP/SOU ; frais 2 seulement ACC ; source unique back pour UX et
  endpoints.
- **DEC-D** : `Confirmed` irréversible en V1. Acte d'annulation comptable explicite reporté V1.1.
- **DEC-E** : coefficients réservés aux sessions Prépa, garde serveur `NOT_PREPA`.
- **DEC-F** : même compte paiement+décision autorisé mais warning structuré, corrélé au dossier.
- **DEC-G** : SOU→ETU relève de l'Administratif ; corriger D11.
- **DEC-H (issue du cas d'arrêt 7)** : clôture Prépa refusée atomiquement si une transition REF
  viserait un dossier sans notes validées ; dry-run nominatif obligatoire.

## 9. Instructions post-fusion et déploiement — architecte

1. Fusionner `mandat/nt-s` en fast-forward sur le dépôt back seulement. Vérifier le SHA annoncé,
   `git show --stat` et un arbre propre. Aucun front à fusionner.
2. Avant migration PROD, sauvegarder la base et relever en lecture seule : nombre de sessions,
   dossiers, dossiers Prépa sans notes validées, frais et paiements Confirmed.
3. Sur PROD : `git reset upstream/main`, puis **`bench --site <site> migrate`** et `bench restart`.
   Le migrate est obligatoire pour les DocPerms et `confirmed_by`.
4. Contrôler en lecture seule après migrate :
   - le champ `Applicant Fee Payment.confirmed_by` existe, Link User, read-only/hidden ;
   - A/R/D ont `write=0` sur `Admission Applicant`, Administratif a `write=0` sur les deux doctypes
     financiers, System Manager conserve `write=1` ;
   - les compteurs relevés à l'étape 2 sont inchangés ; en particulier aucun des cinq dossiers
     Prépa historiques n'a été régularisé automatiquement.
5. Sonder les endpoints sans rôle et attendre un **403, jamais 404** : confirmation offline,
   décision admissible, acceptation, coefficients et clôture de session.
6. Sur le bench DEV/recette désigné — jamais sur les dossiers PROD — rejouer
   `admission.tests.test_nt_s` puis `run_runtime_trace`; exiger 18/18 et
   `purge=BASELINE_RESTORED`.
7. Avec un dossier `ZZTEST-` Prépa en recette, vérifier que le dry-run de clôture affiche le numéro
   bloquant avant toute confirmation. Ne pas exécuter une clôture réelle en PROD pour cette sonde.

## 10. Transparence

- Aucun front n'a été modifié : l'API de détail calcule déjà les actions via `_actions.py` ; les
  cinq familles « actif puis rejeté » disparaissent au rendu sans changement Astro.
- Un ancien test de reçu utilisait des `MagicMock` sans état ni type de frais. Il a d'abord échoué
  dans la suite complète, puis a été adapté mécaniquement ; le module est vert 5/5.
- Le premier essai d'extension du traceur break-glass a utilisé une valeur non autorisée du Select
  `reconciliation`; Frappe l'a correctement rejetée et le `finally` a rollbacké. La preuve finale
  emploie une option valide et restaure exactement les compteurs.
- Aucun tag, merge, déploiement, patch de données ou mutation PROD n'est réalisé par l'agent.
