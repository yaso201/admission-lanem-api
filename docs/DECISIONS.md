# Registre des décisions — Admission LaNEM

Registre des décisions d'architecture et de politique métier. Chaque décision porte un
identifiant `DEC-nnn` **référencé depuis le code** : un commentaire `DEC-348` dans une
fonction renvoie ici, et ce fichier explique le *pourquoi* que le code ne peut pas porter.

**Convention.** La numérotation est continue et ne se réutilise jamais. Les décisions
antérieures à ce fichier (DEC-001 à DEC-345) sont citées dans le code sans être consignées
ici ; elles seront reprises au fil des reprises de code, sans rétro-documentation massive.

**Portée.** Les trois dépôts : `admission-lanem-api`, `admission-lanem` (front candidat),
`lanem-admission-management` (back-office).

| Décision | Objet | Statut |
|---|---|---|
| [DEC-346](#dec-346) | Durée de l'OTP candidat portée à 30 minutes | Appliquée |
| [DEC-347](#dec-347) | États *actionnables* distincts des états *modifiables* | Appliquée |
| [DEC-348](#dec-348) | Réorientation de filière — principe et périmètre | Arbitrée |
| [DEC-349](#dec-349) | Réorientation — trop-perçu converti en avoir | Arbitrée |
| [DEC-350](#dec-350) | Réorientation — notes de concours archivées | Arbitrée |
| [DEC-351](#dec-351) | Réorientation — états éligibles et point de fermeture | Arbitrée |
| [DEC-352](#dec-352) | Réorientation — validation explicite du candidat | Arbitrée |
| [DEC-353](#dec-353) | Réorientation — avoir perdu en cas de désistement | Arbitrée |
| [DEC-354](#dec-354) | Réorientation — blocage à l'admissibilité si complément dû | Arbitrée |

*Arbitrée* = décision prise, implémentation à venir. *Appliquée* = en production.

---

## DEC-346

**Durée de validité de l'OTP candidat portée de 10 à 30 minutes.**

Les délais de remise des e-mails sur le réseau local faisaient expirer le code avant que
le candidat ne le reçoive.

Le garde-fou reste le rate limit de `verify_otp` — 10 essais par heure et par dossier —
et non la durée : sur 30 minutes, cela représente environ 5 essais sur 10⁶ codes possibles.
Tripler la fenêtre triple le risque de force brute, mais il part de si bas qu'il reste
négligeable.

Corollaire : la valeur ne doit exister **qu'à un seul endroit**. `OTP_TTL_MINUTES` est la
source ; le preheader de l'e-mail l'interpole, le gabarit tait la validité s'il ne la
connaît pas plutôt que d'afficher une valeur fausse, et `request_otp` renvoie
`otp_ttl_minutes` que les fronts affichent. La durée de l'OTP de récupération de dossier
en dérive et suit donc automatiquement.

*Appliquée — commits `f3b8493` (API), `fd30005` (front).*

---

## DEC-347

**Un dossier peut être *actionnable* sans être *modifiable*.**

Le système ne connaissait qu'une notion — `CANDIDATE_EDITABLE_STATUSES = ("BRO", "INC")` —
et s'en servait pour deux questions différentes : « ce candidat peut-il modifier son
dossier ? » et « a-t-il encore quelque chose à y faire ? ». Un admis répond non à la
première et oui à la seconde : il doit régler ses frais d'inscription.

Conséquence du défaut : sept admis se sont retrouvés sans aucun chemin vers le paiement.
Le lien du mail d'acceptation ne portait pas de jeton, `/suivi` n'identifie le candidat
que par l'ancrage de son navigateur (30 minutes), et `/reprise` refusait les dossiers
acceptés.

`CANDIDATE_ACTIONABLE_STATUSES = EDITABLE + ("ACC", "ACO")` pilote désormais l'accès au
dossier. **La garde d'écriture `_require_candidate_editable` reste sur EDITABLE** —
élargir ce second tuple n'ouvre aucun droit d'écriture, et l'inverse serait une faille.

*Appliquée — commit `316d23a`.*

---

## DEC-348

**Un dossier peut changer de filière sans être recréé.**

Le programme est aujourd'hui traité presque comme une identité : en changer impose de
refaire une candidature complète. Un candidat orienté vers une autre filière après étude
de son profil perd ses pièces, son antériorité et ses frais.

Cas fondateur : un candidat a porté **cinq dossiers** sur deux identités (deux adresses
e-mail), payé **35 000 FCFA** dont 10 000 définitivement perdus sur un dossier désisté.
Ce n'est pas un cas limite : c'est le comportement attendu quand la réorientation n'existe
pas. Les désistements observés en portent la trace.

La réorientation est une **transition du dossier**, pas une re-création. Elle conserve
l'identité, les pièces avec leur statut de vérification, l'historique et les sommes versées.

Mécanisme de référence : `transfer_session`, étendu au changement de programme. Les pièces
dépendent du profil bac et non du programme : elles sont donc transférables telles quelles.

*Arbitrée — spécification dans [SPECIFICATIONS.md](SPECIFICATIONS.md#reorientation-de-filiere).*

---

## DEC-349

**Un trop-perçu devient un avoir ; aucun remboursement automatique.**

Les frais de candidature diffèrent selon la filière : 10 000 en Prépa (type `competition`),
25 000 en Licence, 40 000 en Bachelor et Double Diplôme (type `application`). Une bascule
crée donc soit un complément, soit un trop-perçu.

Le trop-perçu est reporté en avoir sur les frais d'inscription. Il n'est jamais remboursé.

Corollaire technique : **une ligne de frais déjà payée n'est jamais modifiée.** Elle porte
la preuve du versement et le reçu émis. La ligne caduque passe au statut `Transferred`, une
nouvelle ligne est créée, et le montant versé est imputé via un champ `credit_xof`. Le
reste à payer devient `amount_xof - credit_xof`.

---

## DEC-350

**Les notes de concours sont archivées, puis effacées du dossier.**

Elles n'ont aucun sens hors Prépa. Les conserver exposerait des données sans objet ; les
détruire effacerait une trace. Elles sont donc copiées dans le journal de transfert
(`notes_archived`) avant d'être vidées, et `notes_validated` repasse à 0.

---

## DEC-351

**La réorientation est fermée à partir de l'acceptation (`ACC`).**

États éligibles : `BRO`, `SOU`, `ETU`, `INC`, `ATT`, `ABS`, `ADM`, `ACO`.
États fermés : `ACC`, `INS`, `REF`, `REJ`, `DES`.

Au stade `ACC`, la place est réservée et les frais d'inscription engagés : la bascule
deviendrait une opération financière d'une autre nature.

`ADM` et `ACO` restent éligibles mais **repassent automatiquement à `ETU`** : une décision
d'admissibilité vaut pour un programme donné, elle ne se transporte pas. Le dossier est
réinstruit dans sa nouvelle filière.

Quota d'**une bascule par dossier**, comme le transfert volontaire de session.

---

## DEC-352

**Le candidat valide explicitement sa réorientation.**

Une réorientation modifie son projet et peut lui coûter un complément. Elle ne s'impose pas.

L'opération se déroule en trois temps : le Responsable **propose**, le candidat **accepte**
depuis son espace (jeton + OTP vérifié), le système **applique** en une transaction. Rien
n'est modifié avant l'acceptation — même logique que les propositions de changement de
session, où la valeur en vigueur reste effective jusqu'à validation.

---

## DEC-353

**Un avoir non consommé est perdu en cas de désistement.**

Il n'est ni remboursé, ni transférable vers une candidature ultérieure. Cette information
doit figurer explicitement dans l'écran d'acceptation présenté au candidat : elle l'engage.

---

## DEC-354

**Un complément de frais impayé bloque l'admissibilité, sans faire reculer le dossier.**

Après une bascule vers une filière plus chère, le frais 1 n'est plus couvert. Or
`_frais1_confirmed()` conditionne l'admissibilité, la convocation et l'acceptation.

Trois traitements étaient possibles : faire revenir le dossier à `BRO` (cohérent avec le
modèle, mais fait perdre l'antériorité de la soumission), ne rien bloquer (un candidat
pourrait être déclaré admissible sans avoir payé), ou conserver l'état en bloquant au
franchissement.

C'est la troisième qui est retenue : le dossier **garde son état**, le travail accompli est
préservé, et `mark_admissible` refuse tant que le reste à payer n'est pas nul. Le blocage
est posé là où il a un sens métier, pas en amont.
