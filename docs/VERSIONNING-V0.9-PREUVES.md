# VERSIONNING-V0.9 — dossier de preuves pré-fusion

Date : 2026-08-16

Nature : versionnage et documentation, isolés et vérifiés en développement

Version cible commune : **0.9.0**
Décision : **DEC-331**

Le mandat s'arrête au push de trois branches `mandat/versionning-v09`. Aucun merge, tag,
déploiement ou changement de donnée de production n'est effectué par l'agent.

## 1. L1 — état initial exact

| Trace avant changement | Back | Management | Applicant |
|---|---:|---:|---:|
| Version déclarée | `admission/__init__.py` : `0.0.1` | `package.json` : `0.0.0` | `package.json` : `0.0.0` |
| Lockfile | sans objet ; `pyproject.toml` porte `dynamic = ["version"]` | `package-lock.json` : `0.0.0` aux deux entrées racine | absent |
| Tags locaux | aucun | aucun | aucun |
| Tags distants (`ls-remote --tags origin`) | aucun | aucun | aucun |
| CHANGELOG | absent | absent | absent |
| Affichage dans l'interface | absent | absent | absent |

Avant le lot, l'endpoint PROD `admission.api.health.check` répondait en HTTP 200/healthy mais ne
retournait aucun champ de version. Les HTML et en-têtes des deux fronts ne portaient ni version
d'application ni méta équivalente. Aucune autre trace de version produit n'a été trouvée.

## 2. Schéma retenu — DEC-331

Les trois composants partagent un seul numéro `MAJEUR.MINEUR.CORRECTIF` :

- **MAJEUR** : nouveau cycle d'admission ou refonte de périmètre ;
- **MINEUR** : nouvelle capacité livrée ;
- **CORRECTIF** : correctif ou hotfix.

Le tag est annoté et posé par l'architecte après fusion, jamais par l'agent. Son message renvoie au
CHANGELOG unique du corpus. V0.9.0 désigne le produit actuel complet hors paiement en ligne ;
V1.0.0 ajoutera FedaPay et V1.1.0 absorbera les dettes devenues mûres.

## 3. Write-set déclaré contre diff réel

### Back — base `6091f87`

| Fichier | Diff réel |
|---|---|
| `admission/__init__.py` | `__version__` passe de `0.0.1` à `0.9.0` |
| `admission/api/health.py` | import de la version et champ top-level `version`, aucune sonde ni règle modifiée |
| `docs/VERSIONNING-V0.9-PREUVES.md` | présent dossier de preuves |

Commit métier : `ffb3d5795e34cd881cb8bc4d42a5c790331cbe58`.

### Front management — base `af90984`

| Fichier | Diff réel |
|---|---|
| `package.json` | version `0.9.0` ; ajout mécanique de la fin de ligne finale |
| `package-lock.json` | strictement les deux valeurs racine `0.0.0` → `0.9.0` |
| `src/layouts/Layout.astro` | estampille de version après le contenu des pages authentifiées |
| `src/layouts/BareLayout.astro` | même estampille sur les pages de connexion |

Commit métier : `bc498e4d4d73ce61d4230b59c1e39c3d9a6505eb`.

### Front applicant — base `aaece83`

| Fichier | Diff réel |
|---|---|
| `package.json` | version `0.9.0` ; ajout mécanique de la fin de ligne finale |
| `src/components/Footer.astro` | version dans le pied riche |
| `src/components/FooterLegalStrip.astro` | version dans le ruban légal du tunnel |

Commit métier : `f3c103b7972ba38c7b799707dae7b0612187841f`.

Les quatre estampilles lisent le manifeste au build et utilisent uniquement les jetons du design
system. Aucun fichier `public/`, endpoint métier, Doctype, workflow ou règle n'est touché ; aucun
bump CAL-13 n'est donc requis. `git show --stat` et `git show --check` ont été contrôlés après
chaque commit métier.

### Corpus non versionné — write-set direct

- `CHANGELOG.md` neuf : neuf chantiers, langage utilisateur, dates et SHA en références secondaires ;
- `DETTES-REPORTEES-V1.1.md` neuf : angles morts, convention `ZZTEST-`/`@test.lanem.bj` et onze micro-dettes ;
- `VUE_CONSOLIDEE_PROJET_v2.md` : carte V0.9/V1.0/V1.1, statuts clos et baseline 1044/3 ;
- `M02-Registre-Decisions.md` : DEC-331, DEC-322/323/323-bis déployées, DEC-216 FedaPay ;
- `SPEC-CONTRAT-API-APPLICANT.md` : hébergement CORS réel Cloudflare ↔ Contabo.

Les mentions OVH des ADR et plans historiques restent intactes : elles consignent un choix ou un
repli passé. Les deux assertions normatives actives du contrat applicant et de DEC-217 sont corrigées.

## 4. Gates binaires et compteurs

| Gate | Résultat | Preuve |
|---|---|---|
| Version cohérente | **PASS** | back + 2 manifestes + 2 entrées racine du lock = `0.9.0` |
| Health expose la version | **PASS** | exécution dev : `"version": "0.9.0"` au niveau supérieur |
| Tests health | **13/13 PASS** | `admission.tests.test_health` |
| Suite globale back | **1044 tests / 3 erreurs préexistantes** | baseline exacte, mêmes trois `setUpClass` MagicMock/after_commit |
| Recette notes | **48 PASS / 0 FAIL** | cleanup des fixtures annoncé |
| Contrat CSV | **7/7 PASS** | `admission.tests.test_notes_csv` |
| Build management | **20 pages, PASS** | Astro complet, aucun avertissement de build |
| Build applicant | **19 pages, PASS** | 4/4 textes légaux tirés de l'API PROD, aucun avertissement de build |
| JSDOM pleine page | **4/4 PASS** | management normal+bare, applicant tunnel+riche ; version exacte |
| Couverture HTML utile | **19 management + 19 applicant** | seules exclusions : redirection racine management et `/admin` CMS |
| Tags posés par l'agent | **0** | interdiction respectée |

Les trois erreurs globales sont inchangées :

1. `admission.tests.test_calendar.TestCal09DecE.setUpClass` ;
2. `admission.tests.test_roles_hierarchy.TestRolesHierarchyHelper.setUpClass` ;
3. `admission.tests.test_sm_l0.TestHardenPatch.setUpClass`.

La sonde health dev est `degraded` uniquement parce que `candidate_portal_url` manque dans la
configuration locale ; DB, catalogues et fuseau sont verts. Le champ de version est présent dans
les branches healthy et degraded.

## 5. CHANGELOG et dettes

Le CHANGELOG commence par deux lignes qui présentent le produit et le périmètre de V0.9.0, puis
couvre : GESTION-CALENDRIER, CAL-FIX-0→3, CAL-HOTFIX, CAL-AMEL, CAL-AMEL-R, NOTES,
DOUBLONS-VUE, TRANSFERT-SESSION et DOSSIERS-IDENTITE.

Chaque dette ouverte indique description, raison du report, déclencheur et effort. La capacité
bloquante de salle est explicitement classée **à ne pas construire**. Le commentaire convocation
de `ceb9775` est classé « examiné et déjà levé, effort 0 » : à la base management `af90984`, le
front dit déjà que `staff.py` sert la disponibilité pour une session ouverte, avec date d'épreuve
et frais 1 confirmé.

## 6. Transparence

- `npm ci` management et `npm install --no-save --no-package-lock` applicant ont écrit seulement
  dans les `node_modules` ignorés. npm signale 15 vulnérabilités préexistantes applicant
  (3 faibles, 1 modérée, 11 élevées), hors write-set.
- Le premier build applicant s'est fermé comme prévu sans `PUBLIC_API_BASE`. Le premier rejeu avec
  l'URL PROD a été bloqué par le sandbox réseau ; le rejeu autorisé en lecture a réussi.
- `npm test` applicant reste à **35/36** avec l'unique rouge préexistant accepté :
  `tests/pull-legal.test.mjs:57` attend « Conditions générales de vente », tandis que
  `scripts/legal-map.mjs` porte « Conditions générales de candidature en ligne ».
- Aucun fichier généré légal, build ou dépendance ignorée n'apparaît au diff. Aucun comportement
  métier ni aucune donnée n'a changé. `SES-TEST-100` et les 14 dossiers restent intouchés.
- Le corpus n'a pas de dépôt Git : ses cinq fichiers sont modifiés directement, conformément au
  repli validé, et ne peuvent pas recevoir de SHA de commit.

## 7. Instructions post-fusion pour l'architecte

1. Fusionner les trois branches validées sur leurs dépôts respectifs. Vérifier que les têtes
   fusionnées contiennent les trois commits métier ci-dessus et le commit de preuves annoncé dans
   le rapport de push.
2. Déployer le back, redémarrer web/workers, puis sonder sans authentification :

   ```sh
   curl -sS https://api-admissions.lanem.bj/api/method/admission.api.health.check
   ```

   Attendu : HTTP 200, `status=healthy`, `version=0.9.0`; toute réponse 503 doit être diagnostiquée
   par le détail des sondes avant de poursuivre.
3. Laisser Cloudflare Pages construire les deux fronts avec
   `PUBLIC_API_BASE=https://api-admissions.lanem.bj`, puis vérifier :

   ```sh
   curl -sS https://<management-host>/liste-dossiers | grep 'Version 0.9.0'
   curl -sS https://<management-host>/connexion | grep 'Version 0.9.0'
   curl -sS https://<applicant-host>/identite | grep 'Version 0.9.0'
   curl -sS https://<applicant-host>/suivi | grep 'Version 0.9.0'
   ```

4. Après fusion et vérifications vertes, poser le même tag annoté dans **chacun des trois dépôts**,
   depuis sa tête `main` fusionnée :

   ```sh
   git tag -a v0.9.0 -m "Admission LaNEM 0.9.0 — voir le corpus specifications/CHANGELOG.md"
   git push origin v0.9.0
   ```

   Contrôler ensuite `git show v0.9.0 --no-patch` et `git ls-remote --tags origin v0.9.0` dans
   chaque dépôt. Ne jamais déplacer ou recréer silencieusement un tag publié.
5. Reporter dans le corpus les trois SHA de merge/tag si les têtes finales diffèrent des commits
   métier, puis annoncer V0.9.0 déployée. Aucun `migrate` n'est requis par ce lot.
