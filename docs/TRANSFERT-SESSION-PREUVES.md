# TRANSFERT-SESSION — dossier de preuves pré-fusion

Date : 2026-08-16

Nature : implémentation isolée, vérifiée en dev, non déployée

Branche back : `mandat/transfert-session`, base `eb2b4eedcbafbf40f3d3a8481a1ab8678ca10fc8`

Commit code back : `c69e51eb9d9abca6dde983d54920551849fffd03`

Branche front : `mandat/transfert-session`, base `0f91189b04d9a99f07b44914dee86f0100217dc2`
Commit front : `a6337e2e18f66052ed778d25d530454f7a2674fe`

Le commit qui ajoute le présent dossier est donné dans le rapport de push, car un commit ne peut
pas contenir sa propre empreinte. Aucune fusion vers `main` et aucun déploiement n'ont été faits.

## 1. Architecture retenue

- Le dossier existant est déplacé : aucun second `Admission Applicant` n'est créé.
- Tous les `Applicant Fee` existants du dossier reçoivent la session cible. Le même frais et le
  même `Applicant Fee Payment` sont conservés ; aucun second frais n'est créé.
- DEC-AB : aucun appel à `notify_uf_payment` n'existe dans le chemin de transfert. La session déjà
  transmise à UF au paiement reste une valeur historique, non opposable et non révisée.
- Le journal append-only `Admission Applicant Transfer Log` porte origine, cible, type, acteur,
  motif, justificatif éventuel, lot, occupation et résultat de réémission.
- `ABS` est le nouvel état terminal réversible pendant sept jours : `ETU → ABS → ETU` est réservé
  au Responsable, avec `System Manager` comme break-glass selon le patron existant.
- Les cibles réutilisent `is_session_selectable` et ajoutent seulement les règles du mandat : même
  `programme_code`, épreuve strictement future, toute année académique.
- La capacité est informative. Elle produit un avertissement et ne participe à aucune garde.

### DEC-AB — texte à insérer dans SPEC-CONTRAT-FINANCE-ADMISSION-UF

Le fichier de corpus n'est pas présent dans les deux dépôts autorisés. L'architecte doit y insérer
après fusion :

> La session transmise à UF est la session du dossier au moment du paiement. Cette valeur est
> historique, non opposable et n'est pas révisée lors d'un transfert. Le frais rémunère un service
> unique d'étude du dossier : le transfert conserve le même Applicant Fee et le même paiement,
> sans nouveau frais, sans double comptage et sans seconde notification UF. La traçabilité de la
> session d'origine vers la session cible vit dans Admission Applicant Transfer Log.

## 2. Write-set déclaré contre diff

### Back — 18 fichiers dans le commit code

| Zone déclarée | Diff réel | Limite observée |
|---|---:|---|
| `admission/api/staff.py` | oui | endpoints individuels, ABS, lot, payload staff, garde de périmètre, DEC-J |
| `admission/api/_actions.py` | oui | trois actions serveur data-driven, Responsable exact |
| `admission/api/convocation.py` | oui | réémission même numéro et courriel explicite de remplacement |
| `admission/api/notifications.py` | non | inutile : le patron convocation existant suffit ; aucun ajout artificiel |
| `admission/api/public.py` | oui | frontière exacte : `ABS` ajouté aux états interdisant un nouveau paiement |
| `admission/api/retention.py` | oui | `ABS` définitif entre dans la purge terminale ; sinon conservation infinie |
| schémas Applicant / Note Log | oui | état `ABS`, origine `transfert_absence` |
| nouveau Transfer Log | oui | JSON + contrôleur append-only + module |
| Workflow + patch + `patches.txt` | oui | état et deux transitions ; patch idempotent |
| tests déclarés | oui | actions, paiement, rétention, convocation et nouveau traceur |
| dossier de preuves | commit séparé | présent document |

`notifications.py` est le seul fichier déclaré non touché. Aucun fichier hors write-set métier n'est
dans le commit code. `retention.py` est justifié : à J+8, `ABS` devient terminal ; sans ajout à la
purge terminale, ces dossiers ne rejoindraient plus aucune politique de rétention.

### Front — 8 fichiers dans le commit

| Fichier | Objet |
|---|---|
| `public/scripts/api.js` | six méthodes transfert + lecture authentifiée du justificatif |
| `src/layouts/Layout.astro`, `BareLayout.astro` | bump CAL-13 uniforme `api.js?v=3` |
| `src/pages/dossier.astro` | actions, capacité, historique, motif et justificatif |
| `src/pages/liste-dossiers.astro` | état ABS et file « Absences à traiter » |
| `src/pages/calendrier-session.astro` | aperçu et exécution du lot institutionnel |
| `src/pages/notes.astro` | texte strict INV-HUMAN, aucune autre logique notes |
| `src/styles/shell.css` | style d'état ABS |

Deux commentaires CSS déjà mal fermés dans `dossier.astro` ont été réparés dans le fichier autorisé,
afin d'obtenir un build sans avertissement syntaxique. Aucun manifeste npm n'a changé.

## 3. Gates métier et preuves nominatives

| # | Gate | Preuve |
|---:|---|---|
| 1 | J-2 accepté, J-0 refusé | `TestVoluntaryWindow.test_j_minus_2_accepted`, `test_j_zero_refused` |
| 2 | cible passée refusée, future acceptée | `TestTargetSelection` (2 cas) |
| 3 | même frais/paiement, zéro second UF | `TestMoveAccountingInvariant` et E2E `test_individual_then_second_refused_fee_and_uf_unchanged` |
| 4 | même numéro, nouvelle convocation, ancienne remplacée | `TestConvocation.test_transfer_reissue_keeps_number_and_explicitly_replaces_old_document` |
| 5 | second volontaire refusé ; institutionnel/justifié hors quota | tests unitaires quota + E2E mixte des trois types |
| 6 | ABS manuel ; J+3 accepté ; J+8 refusé ; DEC-J | `TestJustifiedAbsenceWindow`, `TestAbsenceNoteJournal`, E2E ABS réel |
| 7 | coche notes = signal, jamais ABS | `test_notes_absent_signal_never_changes_workflow_status`, recette notes GN4/GN8 48/48 |
| 8 | lot N candidats = N convocations = N logs | `TestInstitutionalBatch` et E2E réel N=2 |
| 9 | cible pleine avertit mais aboutit | unitaire exact `82 / 80`, jsdom exact, E2E réel cible `2 / 1` abouti |

Arbitrage justificatif appliqué : le commentaire est obligatoire, la pièce est facultative. Le cas
J+3 passe explicitement sans pièce ; le fichier éventuel réutilise `_validate_piece_file` (type,
taille, rattachement au même dossier, anti-IDOR).

## 4. Sécurité et invariants

- Tous les actes mutatifs appellent `frappe.only_for(RESP_EXACT)` ; la Direction reste exclue du
  geste maker. Les permissions staff existantes protègent les lectures.
- Les actes individuels appellent `_guard_write_scope`. Les trois routes institutionnelles gardent
  aussi la session source via `value_in_scope(..., axis_required="session")`.
- Aucun endpoint candidat ni self-service n'a été ajouté.
- `available_actions` pilote les boutons. Le front n'invente aucune éligibilité.
- `convocation.available` a été relu et testé : date d'épreuve présente, session `Open`, paiement
  de frais 1 confirmé. Le commentaire front porte désormais cette règle exacte.
- L'invalidation d'une absence validée produit deux lignes du journal des notes : `validation`, puis
  `absent`, toutes deux d'origine `transfert_absence`, avant le retour en `ETU`.
- Le lot ne ferme pas silencieusement la session source ; l'interface l'annonce avant confirmation.

## 5. Exécution dev

### Migration

Bench existant : `admission/bench`, site `admission-dev.localhost`. Le module chargé a été forcé par
`PYTHONPATH` vers le worktree ; `bench/apps/admission` est resté propre sur `main` à `eb2b4ee`.

Commande :

```text
PYTHONPATH=<worktree-back>:<bench>/apps/frappe bench --site admission-dev.localhost migrate
```

Résultat : succès ; synchronisation du nouveau doctype ; exécution réussie de
`admission.patches.v1_2.add_transfer_session_workflow` ; hooks `after_migrate` terminés.

### Tests back

- Module TRANSFERT-SESSION final : **16/16 OK**, dont deux traceurs DB réels.
- Modules modifiés isolés : convocation **8/8**, actions **25/25**, paiement terminal **6/6**,
  rétention **14/14**.
- Recette NOTES-CONCOURS : **48 PASS / 0 FAIL**, cleanup annoncé et vérifié.
- Contrat CSV : **7/7 OK**.
- Suite globale : **1 031 tests**, **3 erreurs**.

La baseline reçue était **1 002 tests / 3 erreurs**. Le lot ajoute 29 tests et ne crée aucune erreur
supplémentaire. Les trois erreurs globales sont strictement les mêmes erreurs de harnais RQ
`after_commit`/`MagicMock`, aux mêmes classes :

1. `admission.tests.test_calendar.TestCal09DecE.setUpClass` ;
2. `admission.tests.test_roles_hierarchy.TestRolesHierarchyHelper.setUpClass` ;
3. `admission.tests.test_sm_l0.TestHardenPatch.setUpClass`.

Elles ne sont pas masquées ni corrigées dans ce mandat. Tous les modules touchés passent isolément.

### Traceur et purge

Le traceur réel committe les actes, vérifie dossier/frais/paiement/journaux, puis purge dans un
`finally` de test. Relevé après le dernier run :

```json
{"applicants": 15, "fees": 14, "payments": 12, "zzxfer_applicants": 0, "transfer_logs": 0}
```

Les compteurs généraux sont revenus à leur baseline ; aucune fixture `ZZXFER` ni ligne de transfert
ne subsiste. Les fixtures notes et CSV ont également exécuté leur cleanup.

### Front

- `npm run build` : **20 pages construites**, aucun avertissement final.
- jsdom pleine page `/dossier` : historique + action de transfert, 0 erreur runtime.
- jsdom pleine page `/liste-dossiers?preset=abs` : file et ligne ABS, 0 erreur runtime.
- jsdom pleine page `/calendrier-session` : carte lot, cible, aperçu et avertissement
  `82 / 80 places`, 0 erreur runtime.
- Contrat `api.js` : 5 appels représentatifs, routes/corps/CSRF validés.
- `/notes` : texte « signal uniquement » et « jamais le dossier en ABS » validé dans le DOM bâti.

`jsdom@26` a été installé temporairement avec `--no-save --package-lock=false` dans `node_modules`
ignoré. `package.json` et `package-lock.json` sont inchangés. npm a signalé 3 vulnérabilités de
l'arbre local (1 faible, 2 élevées), non introduites dans le diff et non modifiées hors mandat.

## 6. Instructions post-fusion pour l'architecte

1. Fusionner les deux branches validées ; ne pas cherry-pick un seul côté.
2. Sur le back déployé, exécuter `bench --site <site> migrate` et vérifier que le patch
   `add_transfer_session_workflow` est marqué exécuté.
3. Vérifier en lecture que le doctype `Admission Applicant Transfer Log`, l'option `ABS` et les
   transitions `Mark Absent` / `Transfer Justified Absence` existent.
4. Construire et déployer le front, puis sonder :

   ```text
   curl -sS https://<management-host>/dossier | grep '/scripts/api.js?v=3'
   curl -sS 'https://<management-host>/scripts/api.js?v=3' | grep 'institutional_transfer'
   curl -sS https://<management-host>/notes | grep 'signal uniquement'
   ```

5. Avec une session staff Responsable authentifiée, faire uniquement les sondes GET initiales :
   `get_dossier` doit exposer `transfer.targets/history` et `institutional_transfer_preview` doit
   restituer les compteurs/capacité. Un compte non Responsable doit être refusé par les endpoints.
6. Le rejeu mutatif post-déploiement doit utiliser une fixture explicitement autorisée hors
   `SES-TEST-100`, vérifier un unique frais/paiement, zéro seconde notification UF, la réémission,
   puis purger la fixture. Ne jamais utiliser un candidat réel pour cette preuve.
7. Surveiller les événements `transfer_session`, `institutional_transfer` et
   `reissue_transfer_failed` après ouverture au personnel.

## 7. Transparence

- Aucun accès ni changement PROD n'a été effectué dans ce mandat pré-fusion.
- Aucun merge, déploiement, checkout ou switch n'a été exécuté.
- Les dépôts principaux n'ont pas été modifiés ; seul le site du bench dev autorisé a été migré et
  utilisé par les fixtures purgées.
- Le fichier SPEC de corpus est hors des deux dépôts autorisés : son amendement DEC-AB reste une
  action explicite de l'architecte, avec le texte fourni plus haut.
