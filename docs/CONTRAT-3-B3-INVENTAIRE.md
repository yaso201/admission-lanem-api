# CONTRAT-3 — B3 : inventaire des sur-réponses candidates, **ZÉRO suppression** (DEC-C)

> Comme CONTRAT-2, comme la ligne DEC-C : **le doute conserve.** Ce document *nomme* des champs
> servis sans consommateur *front* prouvé ; il n'en supprime **aucun**. L'absence d'un consommateur
> front ne prouve pas l'absence de consommateur *tout court* (moniteur, webhook, écran futur —
> **cas d'arrêt #5**). L'inventaire maître reste `CONTRAT-1-INVENTAIRE-CHAMPS-SANS-CONSOMMATEUR.md`.

## Ce que CONTRAT-3 apporte à B3

Les 6 endpoints lourds n'avaient **aucun schéma** jusqu'ici — leurs champs n'étaient donc pas
*énumérables* pour une analyse de consommateur. Les 6 schémas posés (`additionalProperties:false`)
**figent et listent** ces surfaces : c'est le socle qui rend une future analyse « champ ⇄ consommateur »
tractable sur ces endpoints. **CONTRAT-3 documente ; il ne coupe pas.**

## Candidat substantié (méthode, sur `staff.get_dossier`)

Grep de consommation sur le front management de référence (`7ca1dd7`, `dossier.astro` = seul
consommateur de `staff.get_dossier`) :

| Champ servi | Consommateurs front management | Statut |
|---|---|---|
| `bac_profile` | **0** (tout `src/`) | **candidat mort** — conservé (cas d'arrêt #5) |
| `person_id` | 3 | consommé |
| `bac_verified` | 1 | consommé |
| `acompte_xof` | 2 | consommé |
| `conditionnel` | 6 | consommé |
| `promo` | 4 | consommé |
| `related_dossiers` | 2 | consommé |

`bac_profile` est le seul candidat mort *front* substantié sur cet endpoint. **Il n'est pas
supprimé** : il peut alimenter un moniteur/rapport hors-front, et le sérialiseur le calcule déjà.
Le retirer relèverait d'un lot de minimisation dédié (patron B1 de CONTRAT-2 : consommateur unique
*prouvé sur les 3 dépôts*, falsifiabilité, sonde PROD), **pas** d'un lot de couverture de contrat.

## Les 5 autres endpoints

`calendar_list / session_detail / pending_queue / institutional_transfer_targets / list_notes_roster`
servent des formes déjà consommées par le calendrier / les notes / le transfert (schémas ci-joints,
`x-consumers` renseigné). Aucune sur-réponse flagrante repérée ; analyse fine renvoyée à un lot de
minimisation, hors périmètre CONTRAT-3.

## Verdict B3

**Zéro suppression dans ce lot.** Un candidat nommé (`bac_profile`), conservé. L'inventaire maître
CONTRAT-1 reste la référence ; la décision de couper quoi que ce soit reste un **arbitrage à part**,
jamais un effet de bord d'un lot de couverture.
