# DOSSIERS-IDENTITE — dossier de preuves pré-fusion

Date : 2026-08-16

Nature : implémentation isolée et vérifiée en dev, non fusionnée, non déployée

Décisions réalisées : DEC-322 (T), DEC-323 (U), DEC-323-bis (U-bis)

| Dépôt | Base | Commit métier |
|---|---|---|
| back admission | `131aa5bf325955920b68156936803872c023d3ba` | `72b8cf62c3a9816f320a3cd39e0102887de09a30` |
| front management | `a6337e2e18f66052ed778d25d530454f7a2674fe` | `af90984263d0612111920f84a4e77b679e67a8c4` |
| front applicant | `4f2f9acbbebfb6e004854d3dd102dfa93fe7c7f7` | acte 0 `64a8c8bed7a2e27c62a001bb50e5d23b4136eeca`, puis métier `aaece838f7bdc95e1f286efd00eef7b2d3a98285` |

Le commit qui ajoute le présent dossier sera donné dans le rapport de push : un commit ne peut
pas porter sa propre empreinte. Aucun merge, déploiement ou changement PROD n'a été effectué.

## 1. Architecture et contrats

### DEC-322 — date de naissance

- Le champ `Admission Applicant.date_of_birth` existait déjà et reste **optionnel au DocType** :
  les 14 dossiers historiques sans valeur restent valides, sans patch ni rattrapage inventé.
- `create_dossier` exige la date pour tout nouveau dépôt. L'âge calendaire admis est 14–60 ans
  inclus, via `DATE_OF_BIRTH_MIN_AGE` et `DATE_OF_BIRTH_MAX_AGE`.
- Le tunnel impose la même règle et envoie `identite.date_of_birth`. Le serveur reste l'autorité.
- La DOB apparaît dans la sérialisation candidat et dans les lectures staff existantes. Elle
  n'entre dans aucun export de notes.

### DEC-323 — récupération par identité

- `recover_dossier` ne choisit plus un dossier et ne tourne plus son token. Il enfile toujours le
  même job court, adresse connue ou inconnue, puis renvoie exactement :
  `Si cette adresse porte des candidatures, un code vient d'être envoyé`.
- Le job interroge l'adresse exacte, sans état exclu (actifs et clos), génère un code à 6 chiffres,
  le conserve en HMAC dans Redis pendant 10 minutes et réutilise `send_email_otp(..., now=True)`
  dans le worker. Une adresse inconnue emprunte le même job, qui se termine à vide.
- Le script Lua Redis valide atomiquement le code, compte les tentatives, consomme le code au
  succès et l'invalide au 5e échec. Un code expiré ou rejoué rencontre la même absence de clé.
- Après succès, un jeton opaque aléatoire autorise pendant 30 minutes **uniquement la lecture**
  des docnames placés dans son allowlist Redis. `get_recovered_dossier` est un GET ; aucun endpoint
  d'écriture n'accepte ce jeton. Il n'autorise ni paiement, ni pièce, ni décision, ni rotation des
  tokens historiques de dossier. Le front le garde dans une variable de page : ni localStorage,
  ni sessionStorage, ni URL.
- Redis est obligatoire : demande, vérification et détail renvoient tous un 503 propre
  `Service temporairement indisponible, réessayez` si la sonde Redis échoue. Aucun fail-open.
- Seuils nommés : 3 demandes/h/e-mail, 20 demandes/h/IP, 30 vérifications/h/IP, 5 essais/code.
  Les clés e-mail/IP sont HMAC et ne révèlent pas l'adresse. Un blocage IP journalise IP,
  horodatage et compteur dans `admission-security`, afin de mesurer les faux positifs NAT.

### DEC-323-bis et T3

- L'ancien `limit=1` a disparu du chemin de récupération ; la requête prend jusqu'à 500 dossiers
  non anonymisés, triés par création. L'ancrage local 30 minutes du dossier juste créé est intact.
- La recherche staff porte nom, numéro, e-mail et téléphone, avec normalisation NFD/casse. Les
  filtres programme/session/statut, le tri, le compteur X/Y et le reset visible suivent CAL-FIX-3.
  Les options et presets sans résultat sont masqués.
- La fiche staff affiche DOB, e-mail et téléphone à côté du bandeau DEC-S. Le rapprochement
  automatique n'a pas changé : strict `person_id`, jamais de comparaison floue.

## 2. Write-set déclaré contre diff

### Back — 10 fichiers dans le commit métier

| Fichier/zone | Objet exact |
|---|---|
| `admission/api/public.py` | gate DOB ; stockage ; Redis OTP/rates/session ; 3 routes récupération ; sérialisation DOB |
| `admission/api/staff.py` | champs identité, recherche normalisée, DOB au détail ; aucune mutation |
| `admission/scenario_recette.py` | payload de dépôt mis au contrat DOB |
| `admission/tests/fixtures/recette_fixtures.py` | payload de fixture mis au contrat DOB |
| 4 tests existants | attentes recovery/DOB/staff adaptées |
| 2 nouveaux tests | sécurité Redis et traceur dev avec purge |

Aucun doctype, patch, workflow, endpoint financier, calendrier ou module de notes n'est modifié.
Les adaptations `scenario_recette` et fixture sont des harnais déclarés dans le write-set tests.

### Front management — 2 fichiers

- `src/pages/liste-dossiers.astro` : recherche/normalisation, trois filtres, tri, X/Y, reset,
  presets vides masqués.
- `src/pages/dossier.astro` : bloc identité et duo visuel avec le bandeau DEC-S.

`api.js` est inchangé : ses méthodes génériques suffisent, donc aucun bump management artificiel.

### Front applicant — 13 fichiers dans le commit métier

- `public/scripts/admission-tunnel.js` : trois appels récupération, sans `saveDossier`.
- `src/pages/identite.astro` : DOB requise, garde client, payload et snapshot.
- `src/pages/reprise.astro` : e-mail → OTP → liste → détail read-only.
- 11 pages du parcours : bump uniforme `admission-tunnel.js?v=2` vers `?v=3` (CAL-13).
- `tests/admission-tunnel.test.mjs` : contrat réseau et absence d'ancrage du jeton opaque.

### Acte 0 applicant, séparé

Le commit `64a8c8b` contient uniquement la dérive CONVOCATION-PREPA relue : téléchargement PDF
candidat conditionnel et son rendu dans `suivi.astro`, plus le bump CAL-13 `?v=2` sur les pages
consommatrices. `git show --stat` annonce 12 fichiers, 54 insertions et 13 suppressions ;
`git show --check` est vide. Le commit métier suivant a lui aussi été ajouté par pathspec explicite,
puis contrôlé par `show --stat` et `show --check` avant tout commit dans un autre dépôt.

## 3. Check-list de sortie — 9 gates

| # | Gate | Preuves nominatives |
|---:|---|---|
| 1 | Acte 0 séparé et worktrees propres avant code neuf | `64a8c8b`, `show --stat`, `show --check`, build applicant 19 pages |
| 2 | DOB absente/implausible refusée ; dépôt complet stocke la DOB | `TestDateOfBirthGate` (3 tests) et traceur `test_two_dossiers_active_and_closed_are_consulted_without_token_rotation` via vrai `create_dossier` |
| 3 | bon/expiré/rejoué/6e essai | `TestRecoveryOtpRedisAtomicity` : expiration Redis réelle, consommation+rejeu, 5 erreurs puis essai suivant refusé |
| 4 | identité à 3 dossiers dont 1 clos ; liste et détail justes | traceur DB/Redis réel, statuts `BRO/BRO/REF`, allowlist et détail DOB concordants |
| 5 | ancienne reprise `limit=1` absente ; ancrage local intact | grep du corps `recover_dossier` vide ; tests `saveDossier/getDossier` et nouveaux tests API sans stockage |
| 6 | recherche/filtre/tri/fiche staff | `test_search_is_accent_insensitive_and_covers_email_phone`, jsdom liste et dossier : e-mail, X/Y, reset, badge, DOB et lien associé |
| 7 | CSV notes inchangé | diff hunks staff limité aux lignes 897–1086, `_CSV_HEADER`/export hors diff ; `test_notes_csv` 7/7 |
| 8 | anti-abus réellement déclenché | `test_email_rate_limit_triggers_on_fourth_request` sur Redis réel ; test journal IP avec IP/heure/compteur |
| 9 | traceur bout en bout et purge | vrai dépôt DOB → job OTP → 3 résumés → détail → hashes historiques identiques → OTP consommé ; `residue_probe` = `{"count": 0, "names": []}` |

Le test fail-closed couvre les trois points d'accès (demande, vérification, détail) et exige le
même code 503. Le test du worker prouve l'appel du canal courriel existant sans token dossier.

## 4. Compteurs dev et baselines

### Back

- Module DOB/OTP final : **12/12 OK**.
- Traceur DB/Redis réel : **1/1 OK**, puis `residue_probe` à zéro.
- Staff read : **10/10 OK** ; notifications/recovery : **23/23 OK** ; CSV : **7/7 OK**.
- Recette NOTES-CONCOURS : **48 PASS / 0 FAIL**, cleanup annoncé.
- Suite globale : **1 044 tests / 3 erreurs**. Baseline reçue : **1 031/3** ; le lot ajoute
  13 tests et aucune erreur. Les trois erreurs sont les mêmes `after_commit`/`MagicMock` :
  1. `admission.tests.test_calendar.TestCal09DecE.setUpClass` ;
  2. `admission.tests.test_roles_hierarchy.TestRolesHierarchyHelper.setUpClass` ;
  3. `admission.tests.test_sm_l0.TestHardenPatch.setUpClass`.

### Fronts

- Applicant : **35 verts / 1 rouge préexistant**. Signature acceptée :
  `tests/pull-legal.test.mjs:57` attend `titre: "Conditions générales de vente"`, alors que
  `scripts/legal-map.mjs:17` porte déjà `Conditions générales de candidature en ligne`.
  Ce test hors write-set n'est ni masqué ni corrigé.
- Build applicant : **19 pages**, aucun avertissement final ; pull légal public 4/4.
- Build management : **20 pages**, aucun avertissement final.
- jsdom pleine page applicant `/identite` : garde 14–60 et payload DOB ; `/reprise` : OTP,
  liste, détail et localStorage vide, zéro erreur runtime.
- jsdom pleine page management `/liste-dossiers` : presets vides, recherche normalisée, X/Y,
  reset et badge ; `/dossier` : DOB/contact, bandeau et lien associé, zéro erreur runtime.
- Le bump CAL-13 est uniforme : les 11 références applicant servent `admission-tunnel.js?v=3`.

Aucune migration n'a été jouée : Redis évite volontairement doctype, patch, tâche de purge et
TTL applicatif. `date_of_birth` existait déjà ; aucun schéma n'a changé.

## 5. Transparence

- Au premier trace, les suppressions du `tearDown` ont été rollbackées par le runner et deux
  dossiers tagués sont restés. Ils ont été inventoriés puis supprimés **nominativement**
  (`26270000751`, `26270000750`). Le harnais committe désormais sa purge ; deux rejeux suivants
  ont fini à `{"count": 0, "names": []}`.
- La première suite globale après ajout des champs a donné 1044/8 : cinq mocks historiques
  `SimpleNamespace` ne portaient pas `email`/`phone`/`date_of_birth`. Les lectures additives ont
  été rendues rétrocompatibles avec `getattr` ; le module fautif passe 17/17, puis la globale
  revient à 1044/3.
- `npm install --no-save --no-package-lock` a servi uniquement dans les `node_modules` ignorés.
  npm signale 15 vulnérabilités sur l'arbre applicant (3 faibles, 1 modérée, 11 élevées) et 3 sur
  management (1 faible, 2 élevées), non introduites dans les manifests ni traitées hors mandat.
- Aucun accès, merge, déploiement ou changement de donnée PROD. `SES-TEST-100` est intacte.
- Aucun fichier de registre/corpus n'existe dans les trois write-sets isolés. Le présent document
  porte donc la mémoire pré-fusion et fournit ci-dessous la mise à jour prête à reporter.

## 6. Instructions post-fusion pour l'architecte

1. Fusionner les **trois** branches validées. Déployer le back avant les fronts afin d'éviter une
   fenêtre où `/reprise` appelle des routes absentes. Ne pas cherry-pick un seul côté.
2. Back : aucun migrate requis par ce lot. Mettre le code à jour, redémarrer web/workers, puis
   vérifier que `date_of_birth` existe toujours et qu'aucun patch DOSSIERS-IDENTITE n'apparaît.
3. Vérifier les comptes PROD en lecture avant/après : 22 sessions, 14 dossiers, et aucune mutation
   par le déploiement. Les 14 historiques doivent rester valides avec DOB éventuellement vide.
4. Construire/déployer les deux fronts puis sonder :

   ```text
   curl -sS https://<applicant-host>/reprise | grep '/scripts/admission-tunnel.js?v=3'
   curl -sS 'https://<applicant-host>/scripts/admission-tunnel.js?v=3' | grep 'verifyRecoveryOtp'
   curl -sS https://<applicant-host>/identite | grep 'date-naissance'
   curl -sS https://<management-host>/liste-dossiers | grep 'f-status'
   curl -sS https://<management-host>/dossier | grep 'identity-dob'
   ```

5. Sonde de garde : `get_recovered_dossier` sans jeton doit répondre 403, jamais 200/404. Les
   routes `recover_dossier`, `verify_recovery_otp`, `get_recovered_dossier` doivent exister.
6. Avec une fixture explicitement autorisée, hors `SES-TEST-100`, rejouer e-mail → OTP → liste de
   trois dossiers dont un clos → détail. Vérifier avant/après les hashes des tokens historiques,
   puis purger la fixture et obtenir un compte résiduel zéro. Ne jamais utiliser un candidat réel.
7. Avec une session staff existante, rechercher la fixture par e-mail puis téléphone, exercer les
   trois filtres, vérifier X/Y/reset, le bloc DOB/contact et les liens DEC-S. Un utilisateur sans
   rôle staff doit rester refusé par les endpoints staff.
8. Surveiller `admission-security` pour `rate_limited_ip` (IP, heure, compteur) pendant la première
   période de candidatures. Ajuster seulement les constantes IP sur faits terrain ; conserver 3/h
   par e-mail.

### Mise à jour du registre après déploiement vert

```text
DEC-322 (T) — DÉPLOYÉE — DOB obligatoire aux nouveaux dépôts, âge 14–60, historiques tolérés.
DEC-323 (U) — DÉPLOYÉE — OTP e-mail Redis fail-closed puis liste/détail de tous les dossiers.
DEC-323-bis (U-bis) — DÉPLOYÉE — reprise limit=1 remplacée, ancrage dossier local conservé.
SHA back: 72b8cf62c3a9816f320a3cd39e0102887de09a30
SHA management: af90984263d0612111920f84a4e77b679e67a8c4
SHA applicant: aaece838f7bdc95e1f286efd00eef7b2d3a98285
Acte 0 applicant: 64a8c8bed7a2e27c62a001bb50e5d23b4136eeca
```
