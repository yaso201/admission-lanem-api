# Spécifications — Admission LaNEM

Guide de référence des fonctionnalités spécifiées mais non encore livrées, et des règles
transverses qui les encadrent. Les décisions qui les fondent sont consignées dans
[DECISIONS.md](DECISIONS.md).

Une spécification décrit **ce que le système doit faire et pourquoi**. Elle ne décrit pas
comment l'écrire : le code en décide, et les commentaires `DEC-nnn` renvoient ici.

| Spécification | Statut |
|---|---|
| [Réorientation de filière](#reorientation-de-filiere) | Arbitrée — à implémenter |

---

## Règles transverses

Ces principes s'appliquent à toute évolution et ont déjà été payés au prix fort une fois
chacun. Ils ne se renégocient pas au cas par cas.

**Le front est un pur renderer.** Une règle métier vit au back, en un seul endroit, et le
front l'affiche. Deux implémentations divergent toujours — c'est la leçon du résolveur
d'étape, des actions disponibles, et des libellés de frais qui existaient en double.

**Un état dérivé se recalcule, il ne se mémorise pas.** Les navigateurs restaurent les
formulaires sans émettre d'événement, et le bfcache ne rejoue aucun script. Toute fonction
de synchronisation doit être idempotente et appelée depuis tous les points d'entrée, y
compris `pageshow`. Mieux encore : relire l'état au moment de l'action.

**Une valeur métier n'existe qu'à un seul endroit.** Pas de duplication entre back et
front, ni entre deux pages. Un repli affiche le code brut plutôt qu'une valeur fausse.

**Ce qui élargit est permis, ce qui restreint est interdit.** Règle du calendrier,
généralisable : prolonger une échéance est recevable, l'avancer ne l'est pas.

**Une ligne de frais payée n'est jamais modifiée.** Elle porte la preuve du versement et
le reçu émis. On la neutralise, on ne la réécrit pas.

---

## Réorientation de filière

> Fondement : [DEC-348](DECISIONS.md#dec-348) à [DEC-354](DECISIONS.md#dec-354).

### Objet

Permettre à un dossier de changer de programme — et donc de session — en conservant son
identité, ses pièces, son historique et les sommes déjà versées.

### Déroulé

```
1. PROPOSITION   Responsable   →  programme cible, session cible, complément calculé
2. ACCEPTATION   Candidat      →  depuis son espace, jeton + OTP vérifié
3. APPLICATION   Système       →  transaction unique, tout ou rien
```

Rien n'est modifié avant l'acceptation du candidat ([DEC-352](DECISIONS.md#dec-352)).

### Modèle de données

**Nouveau doctype `Admission Applicant Transfer Proposal`** (table enfant du dossier) :

| Champ | Type | Rôle |
|---|---|---|
| `from_programme`, `to_programme` | Data | filières source et cible |
| `from_level`, `to_level` | Data | niveaux correspondants |
| `target_session` | Link | session cible, ouverte et non échue |
| `fee_delta_xof` | Currency | complément dû, 0 si aucun |
| `credit_xof` | Currency | avoir généré, 0 si aucun |
| `proposed_by`, `proposed_on` | Data, Datetime | traçabilité de la proposition |
| `candidate_response` | Select | `Pending` / `Accepted` / `Refused` |
| `responded_on` | Datetime | horodatage de la réponse |

**`Admission Applicant Transfer Log`** — champs à ajouter : `from_programme`,
`to_programme`, `from_level`, `to_level`, `fee_delta_xof`, `credit_xof`, `notes_archived`.

**`Applicant Fee`** — champ à ajouter : `credit_xof` (Currency, défaut 0). Le **reste à
payer** devient `amount_xof - credit_xof`. Nouveau statut `Transferred` pour neutraliser
une ligne caduque sans la détruire.

> ⚠️ Toutes les gardes de paiement doivent lire le reste à payer, et non `amount_xof` seul.
> C'est le changement le plus diffus de cette spécification.

### Traitement des frais

Barème actuel :

| Programme | Frais 1 | Type | Frais 2 |
|---|---|---|---|
| PREPA | 10 000 | `competition` | 75 000 |
| LIC-* | 25 000 | `application` | 50 000 |
| BACH-*, DD-* | 40 000 | `application` | 75 000 |

Le **type** change, pas seulement le montant : une bascule convertit, elle ne déplace pas.

**Cas A — frais 1 impayé.** Mutation simple de la ligne : `fee_type`, `amount_xof`,
`session`. Aucun mouvement d'argent.

**Cas B — payé, cible plus chère.** *(Prépa → Licence : 10 000 versés, 25 000 dus)*
La ligne d'origine passe à `Transferred`. Une nouvelle ligne `application 25 000` est créée
avec `credit_xof = 10 000` : reste à payer **15 000**.

**Cas C — payé, cible moins chère.** *(Licence → Prépa : 25 000 versés, 10 000 dus)*
La ligne d'origine passe à `Transferred`. La nouvelle ligne est couverte, et l'avoir
résiduel de **15 000** est porté en `credit_xof` sur la ligne `enrollment`
([DEC-349](DECISIONS.md#dec-349)).

### Invalidations obligatoires

**Snapshot promo** — `local_promo_snapshot` et `final_annual_xof` remis à zéro. Le droit
sera recalculé à la confirmation du frais 1. Sans cela, un candidat basculé de Licence vers
Double Diplôme conserverait un tarif figé de 380 000 pour une scolarité de 1 640 000.

**Notes de concours** — archivées dans le journal, puis effacées ; `notes_validated` à 0
([DEC-350](DECISIONS.md#dec-350)).

**Convocation** — annulée si émise. Le mécanisme existe (`reissue_transfer_convocation`).

**`level_code`** — remappé vers le niveau correspondant du programme cible. Sans
correspondance évidente, l'agent choisit explicitement.

**Pièces** — *non* invalidées. Elles dépendent du profil bac et non du programme :
transférées telles quelles, avec leur statut de vérification.

### Gardes

| Contrôle | Règle |
|---|---|
| Rôle | Responsable (exact) |
| États éligibles | `BRO`, `SOU`, `ETU`, `INC`, `ATT`, `ABS`, `ADM`, `ACO` |
| États fermés | `ACC`, `INS`, `REF`, `REJ`, `DES` |
| `ADM` / `ACO` | repassent automatiquement à `ETU` |
| Quota | une bascule par dossier |
| Session cible | ouverte, non échue, capacité vérifiée |
| Atomicité | une transaction : tout réussit ou rien |

### Effet sur le workflow

`_is_prepa()` se déduit de la session et non du programme : changer de session fait donc
basculer automatiquement le workflow — concours ou dossier — sur les douze points où il
diverge. Aucune intervention supplémentaire n'est nécessaire.

**Complément impayé** — le dossier garde son état ; `mark_admissible` refuse tant que le
reste à payer n'est pas nul ([DEC-354](DECISIONS.md#dec-354)).

**Désistement** — un avoir non consommé est perdu ([DEC-353](DECISIONS.md#dec-353)). Cette
information figure dans l'écran d'acceptation.

### Côté candidat

Endpoint `public.respond_programme_transfer(dossier_id, token, accept)`, gardé par
`_require_otp_verified` comme les paiements.

Dans `/suivi`, une carte dédiée : filière proposée, montant du complément ou de l'avoir,
mention de la perte en cas de désistement, deux actions — accepter, refuser.

### Tests attendus

- les trois cas de frais, montants et statuts vérifiés en base ;
- refus depuis chaque état fermé ;
- `ADM` et `ACO` repassent bien à `ETU` ;
- snapshot promo effacé et recalculé sur le nouveau barème ;
- notes archivées dans le journal **et** absentes du dossier ;
- pièces conservées avec leur statut de vérification ;
- quota : seconde bascule refusée ;
- `mark_admissible` refusé tant qu'un reste à payer subsiste ;
- proposition refusée par le candidat : dossier strictement inchangé.

Chaque test doit être vérifié **en échec** avant d'être considéré comme protecteur : un
test qui passe avec et sans le correctif ne protège de rien.

### Préalable

Cette fonctionnalité touche l'argent, le workflow et les décisions d'admission.
**Elle ne doit pas être développée directement en production.**
