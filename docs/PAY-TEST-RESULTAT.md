# PAY-TEST — Test réel d'idempotence FedaPay (production) — Résultat

> **Lot d'exploitation** (aucun code). Exécute `docs/PAY-IDEM-PREUVES.md` (blocs Avant→Ouvrir→Tester→Refermer)
> sur la PROD `api-admissions.lanem.bj`, argent réel, montant 100 F (< plafond FedaPay 5 000 F).
> But : prouver qu'un double-clic sur « Payer en ligne » ne crée **qu'une seule** transaction FedaPay.
> Back PROD `e1c7aed`. Correctif prouvé : PAY-IDEM (`a5fbd0b`/`be03204`).

---

## Bloc A — Avant (agent, sans signal) — ✅ FAIT

| Élément | Constat |
|---|---|
| **Drapeau `online_payment_enabled` (A2)** | **`0`** (fermé) — état à restaurer en bloc D. Confirmé `site_config.json` + `show-config`. |
| **Sauvegarde de la nuit (DEC-F)** | ✅ présente : `20260818_020004-…-database.sql.gz` (+ files), 02:00 (heure serveur WAT). |
| **Dossier de test (DEC-B)** | **`26272010003`** — « Soglo Yaovi », session `SES-TEST-100`, email **sygiovani201@gmail.com** (dossier de l'utilisateur). |
| État du dossier | **`BRO`**, OTP vérifié, **toutes les pièces requises `uploaded`** (aucune manquante → pas de blocage `PIECES_MANQUANTES`). Session `SES-TEST-100` ouverte. |
| **Frais 1 (AFF-2026-00025)** | `amount_xof` = **100.0**, statut **Pending**. Le forçage d'août a persisté → **aucune ré-écriture nécessaire** (bloc A = lecture seule). |
| **Montant réellement débité** | **100 F** — confirmé par le chemin de code : `_ensure_fee` renvoie le fee existant SANS recalcul (public.py:995) → `prepare_online_payment` pose `descriptor.amount_xof = fee.amount_xof` (public.py:2443). `_prepare_fee_channel` ne touche pas au montant. **≤ 5 000 F → sûr.** |

### ⚠️ Deux écarts à la procédure — à arbitrer AVANT d'ouvrir la fenêtre
1. **`get_frais` affiche 25 000, pas 100.** L'écran `/paiement` lit le **catalogue** (`LIC-IS/application/L1 = 25 000`), pas le frais forcé. Le **débit reste 100** (via le fee), mais l'écran candidat montrera **25 000 F** avant le checkout FedaPay qui, lui, affichera **100 F**. Impossible d'aligner l'affichage sans modifier le catalogue partagé → **impacterait tous les dossiers LIC-IS (DEC-B l'interdit)**. Écart cosmétique, sûr, mais à connaître.
2. **Une transaction FedaPay antérieure existe.** Paiement `261100006` — Online, **Rejected, 25 000 F**, réf `pay-1786895068120`. Donc le **point de départ FedaPay n'est PAS 0** pour ce dossier (A4). **0 Confirmed.** Le critère de succès devient : le double-clic crée **exactement UNE nouvelle** transaction (à 100 F, réf neuve), pas deux — l'ancienne rejetée est du bruit historique (purge interdite, DEC-E).

**Rapport à l'utilisateur : dossier prêt, débit 100 F garanti. En attente du signal pour ouvrir (bloc B).**

---

## Bloc B — Ouvrir (agent, SUR SIGNAL) — ✅ FAIT
- [x] `set-config online_payment_enabled 1` + `bench restart` — OK (workers redémarrés).
- [x] Vérifié : `site_config.json = 1` · `_online_payment_enabled() = True` · `get_frais.online_payment_enabled = True`.
- [x] **Heure d'ouverture : mardi 18/08 23:27:48 WAT.** ⚠️ Paiement en ligne ouvert pour TOUT LE MONDE → fenêtre brève.

## Bloc C — Tester (utilisateur double-clic + inspection agent) — ✅ IDEMPOTENCE PROUVÉE
Double-clic effectué. Inspection base à 23:41 :
| # | Inspection | Résultat |
|---|---|---|
| 1 | **Transaction créée par le double-clic** | ✅ **UNE seule** : `261100007`, Online, **100 F**, réf `pay-3d984be9-a645-4296-a9e6-8de4f8196028`. **Pas de doublon.** (L'ancienne `261100006` Rejected 25 000 est hors test.) |
| 2 | Applicant Fee Payment online (hors Rejected) | **1** (le nouveau). Confirmed : **0** — voir #3. |
| 6 | **Clé d'idempotence** | **UNE seule** (`pay-3d984be9-…`) portée par le paiement unique → le double-clic a rejoué la MÊME intention (cœur du correctif PAY-IDEM). |
| 3 | Statut dossier BRO → SOU | ⏳ **encore BRO** — paiement **Pending**, `paid_at` None : **le webhook FedaPay n'a pas (encore) confirmé**. |
| 4 | Délai paiement → transition | — (transition non survenue, webhook en attente) |
| 5 | Reçu + courriel | — (émis à la confirmation ; pas encore) |

**Verdict idempotence : ✅ SUCCÈS.** Le critère central — un double-clic ne crée qu'**une** transaction — est prouvé (1 paiement, 1 clé). La **confirmation** (BRO→SOU, reçu) dépend du webhook, indépendant du drapeau (`webhook.py` ne gate pas dessus), donc arrivera après fermeture le cas échéant. **Webhook non arrivé à la fermeture → cas d'arrêt #2 signalé.**

## Bloc D — Refermer (agent, OBLIGATOIRE) — ✅ FAIT
- [x] `set-config online_payment_enabled 0` + `bench restart` — OK.
- [x] **Fermeture vérifiée** : `site_config.json = 0` · `_online_payment_enabled() = False`.
- [x] **Heure de fermeture : 23:41:40 WAT** · **durée de la fenêtre : ~13 min 52 s** (23:27:48 → 23:41:40).

## Diagnostic webhook (cas d'arrêt #2) — l'utilisateur a confirmé le débit 100 F
Après confirmation du paiement par l'utilisateur, re-contrôle à 23:45 : paiement **toujours Pending**, dossier **BRO**.
- **Aucune `Error Log`** entre 23:20 et le contrôle (ni signature, ni fedapay, ni promote) ; aucune ne mentionne la réf.
- **Aucun appel visible** au endpoint `admission.api.webhook.payment` dans `logs/*.log`.
- ⇒ Faisceau : le webhook **n'a pas été reçu** par la PROD (plutôt que reçu-et-échoué). Le débit existe côté FedaPay (confirmé utilisateur), mais la promotion `_promote_payment` n'a pas eu lieu.
- **Non forcé** : je ne confirme pas le paiement à la main (hors mandat, DEC-E) ; le webhook étant indépendant du drapeau (`webhook.py` ne gate pas), il promouvra le paiement **s'il arrive** (retry FedaPay), fenêtre fermée ou non.

**À la main de l'architecte** : vérifier le tableau de bord FedaPay — le webhook est-il configuré sur `https://api-admissions.lanem.bj/api/method/admission.api.webhook.payment` ? A-t-il été **envoyé** ? Échecs de livraison / retries ? Un renvoi manuel depuis FedaPay devrait promouvoir `261100007` → dossier SOU.

## Verdict
- **Idempotence client (le but du lot) : ✅ SUCCÈS.** Double-clic → **une seule** transaction (`261100007`, 100 F, une clé). Pas de doublon.
- **Confirmation bout-en-bout : ⏳ INCOMPLÈTE** — webhook non reçu (cas d'arrêt #2, signalé, non corrigé). BRO→SOU / reçu en attente.
- **Fenêtre : fermée et vérifiée** (~14 min). **Aucun code modifié. Aucune purge. Drapeau à 0.**

## Check-list de sortie
- [x] Dossier et montant documentés · sauvegarde vérifiée (DEC-F, `20260818_020004`)
- [x] Fenêtre ouverte sur signal, heures notées (23:27:48 → 23:41:40 WAT)
- [x] **Une seule transaction FedaPay** — critère central atteint
- [~] Un seul paiement Confirmed, dossier SOU, reçu émis — **en attente webhook (cas d'arrêt #2)**
- [~] Délai paiement → transition — non mesurable (transition non survenue)
- [x] Drapeau refermé et vérifié (`online_payment_enabled: 0`, `_online_payment_enabled()=False`)
- [x] Référence de transaction et dossier documentés pour le rapprochement
- [x] Aucune purge · aucun code modifié (back `e1c7aed` inchangé)

## Traçabilité argent (DEC-C) — pour le rapprochement pré-PROD
- Dossier : `26272010003` · Frais : `AFF-2026-00025` (100 F, Pending)
- **Transaction du test** : `261100007` — Online, **100 F**, réf `pay-3d984be9-a645-4296-a9e6-8de4f8196028`, créée 23:37:32 WAT, **Pending** (à rapprocher / rembourser selon politique).
- Antérieur (hors test) : `261100006` Rejected 25 000, réf `pay-1786895068120`.
