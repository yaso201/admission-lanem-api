# Journal des versions — LaNEM Admissions

## v1.0.0 — Le paiement en ligne est actif
*19 août 2026*

Les candidats peuvent désormais **régler leurs frais de candidature en ligne** (mobile money),
directement depuis leur dossier. Le paiement est **confirmé automatiquement** et le dossier passe
en traitement, sans intervention manuelle du service des admissions.

Cette version ouvre officiellement les admissions. Elle clôt le cycle de préparation entamé à la **v0.9.3** :

- **Sécurité et conformité vérifiées de bout en bout.** L'audit complet de l'application est soldé :
  chaque action sensible est tracée, les données personnelles sont protégées (droit à l'effacement,
  consentements conservés comme preuve), et un double-clic sur « Payer » ne crée **jamais** de double
  débit.
- **Fiabilité éprouvée en conditions réelles.** Les corrections issues des tests de recette sont
  intégrées ; les échanges entre le dossier candidat et le back-office sont désormais garantis par des
  contrôles automatiques.
- **Production remise à neuf.** Toutes les données de test ont été retirées avant l'ouverture : les
  premiers vrais candidats trouvent un système vierge, avec le calendrier de campagne 2026-2027 en place.
- **Paiement en ligne ouvert.** Le plafond de test est levé et la chaîne complète — checkout →
  confirmation → dossier soumis — fonctionne en production.

Aucun changement dans la manière de déposer un dossier : identité, pièces, récapitulatif, paiement.
Seule nouveauté visible pour le candidat : le règlement en ligne est proposé au moment de payer.
