# CONTRAT-1 — Inventaire des champs sans consommateur (DEC-D)

> **Aucune suppression.** Un champ sans consommateur front peut servir ailleurs (moniteur externe,
> webhook, écran futur). Cet inventaire **signale et recommande** ; l'architecte arbitre.
> Source : `AUDIT-360-A3-CONTRATS §2.1` (colonne « Ignorés / fantômes / réserve ») + `§2.3`.
> Marquage dans les schémas : les champs consommés sont en `required` ; les ignorés restent dans
> `properties` (non requis) — le back peut les retirer sans casser le contrat, mais **pas en silence**
> (`additionalProperties:false` sur les schémas stricts force la mise à jour du contrat).

## Champs servis mais non lus par le consommateur concerné

| Endpoint | Champs ignorés (I) | Recommandation |
|---|---|---|
| `admin_data.get_audit_log` | `entries[].status`, `entries[].ref` | Réserve — utile à un écran d'audit enrichi. **Conserver, annoter réserve.** |
| `admin_staff.list_staff` | `staff[].user_type`, `total` (top-level) | `total` : le front recompte `staff.length`. **Conserver** (`total` = API honnête, coût nul). |
| `calendar_view.calendar_list` / `session_detail` | `is_open`, `application_fee_xof` | `is_open` est un miroir d'état ; `application_fee_xof` sert d'autres écrans. **Conserver.** |
| `public.list_programmes` | `niveaux[].level_order`, `partner_name`, `dd_component_1`, `dd_component_2` | `level_order` sert le tri éventuel ; `dd_component_*` = réserve double-diplôme. **Conserver.** |
| `public.list_sessions` | `opens_on`, `status` | Le front grise via `selectable`. **Conserver** (`status` = source de `selectable`). |
| `public.get_frais` | `devise`, `fee_type`, `simulation_disclaimer_version`, `textes_legaux`, `rib.version`, section `programme` | `textes_legaux` relus par l'endpoint dédié. **Trim candidat** pour le consommateur paiement, mais vérifier moniteur/reçu avant. |
| `public.get_legal_documents` | `type`, `content_hash`, `effective_date` | `content_hash` = preuve de consentement (valeur légale). **Conserver impérativement.** |
| `public.get_dossier` | `promotion` (top-level) | Ne pas confondre avec `get_frais.promotions_actives` (consommé). **Conserver, annoter.** |
| `public.get_recovered_dossier` | **sur-réponse** : profil bac, bourses, promotion, paiements, convocation, conditionnel, motifs, rang | Rend tout `_serialize_dossier` alors que `reprise.astro` lit un sous-ensemble. **Réduire à la vue reprise** (moins de PII exposée par OTP) — arbitrage. |
| `staff.institutional_transfer_targets` | `source_session`, `targets[].academic_year`, `targets[].capacity_warning` | Réserve d'affichage. **Conserver.** |
| `staff.institutional_transfer_preview` | `source_session`, `target_session`, `dossiers`, `capacity.before`, `capacity.exceeded` | `dossiers` = liste nominative non affichée (aperçu = comptes). **Trim `dossiers`** si non requis ailleurs — arbitrage (surface PII). |
| `staff.get_dossier` | `session.academic_year`, `bac_profile`, `motif_desistement`, `paiements[].{paid_at,receipt_number,justificatif}`, `notes.{moyenne,absent,eliminatoire,coefficients}`, `promo.captured_date` | Beaucoup de réserve d'écran détail. **Conserver** (dossier.astro peut évoluer). |
| `staff.stats_direction` | `sessions[].{academic_year, programme_code, opens_on, closes_on}` | Réserve tableau Direction. **Conserver.** |
| `staff.list_notes_roster` / `export_notes_template` / `valider_notes_masse_preview` | `session_id` (top-level), et pour la preview `dossiers[]` | `session_id` : la page connaît déjà la session (coût nul). `dossiers[]` de la preview : la modale n'affiche que des comptes — **revue incomplète signalée** (le Responsable valide sans voir la liste). **Arbitrage** : afficher `dossiers[]`, ou le retirer. |

## Fantômes et compteurs — état à jour

- **`fedapay`/`kkiapay` (2 champs fantômes A3)** : **CORRIGÉ** par LEGAL-HYGIENE (back sert `fedapay`). Le
  contrat `admin_config.get_config_health` l'épingle désormais ; le rejeu E-01 prouve qu'une régression
  vers `kkiapay` rougirait.
- **5 compteurs d'exploitation (A3 : ignorés)** : **RENDUS** par OBS-1 (`exploitation.astro`). Le contrat
  `admin_ops.get_ops_health` les marque tous `required`.
- **`public.classify_bac`** : wrapper exporté (`admission-tunnel.js:691`) **sans page appelante** →
  consommateur **dormant**, pas actif. **Conserver** (utilisé au build/futur), annoter dormant.

## Recommandation d'ensemble

1. **Ne rien supprimer sans confirmer l'absence de consommateur EXTERNE** (moniteur `health.check`,
   webhooks, reçus PDF, exports). L'audit ne couvrait que les 3 dépôts SPA.
2. **Deux sur-réponses à surface PII** méritent un arbitrage prioritaire : `get_recovered_dossier`
   (tout le dossier via OTP) et `institutional_transfer_preview.dossiers` (liste nominative). Réduire =
   moindre exposition ; conserver = simplicité. **Décision architecte.**
3. Le reste = réserve d'affichage à coût nul → **conserver + annoter** dans les schémas de contrat au fil
   de l'extension du périmètre.
