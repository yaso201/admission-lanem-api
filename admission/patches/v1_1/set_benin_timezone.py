"""D-TZ-01 : fuseau système = Africa/Porto-Novo (Bénin, UTC+1, PAS de DST).

Frappe stocke les datetimes NAÏFS = heure murale du fuseau système ; `now_datetime()` en dépend
(paid_at, verdict_at, decision_date, log_event, reçus, notifs…). Sans réglage, Frappe retombe sur
Asia/Kolkata (+5:30) → tous les horodatages décalés de +4:30. Ce patch pose la cible LaNEM et
GARANTIT que tout site (recette + PROD au premier migrate) naît avec le bon fuseau AVANT toute
écriture d'horodatage → aucun rattrapage sur une base neuve.

Config-as-code idempotente (pose la valeur cible inconditionnellement — no-op si déjà posée).
Placé en `pre_model_sync` (AVANT le model sync et TOUS les patches applicatifs) pour que
l'invariant fuseau précède toute écriture — y compris les seeds/backfills post_model_sync et les
horodatages d'audit du model sync (audit indépendant D-TZ-01 : sans ça, la garantie n'était vraie
que par chance de l'ordre actuel des patches). Les logiques RELATIVES (TTL token/OTP, cutoffs
rétention = now vs now+delta) sont intra-fuseau, donc insensibles ; seul l'horodatage ABSOLU est
corrigé. Rattrapage -4:30 des données existantes : hors ce patch (recette = tests ; PROD sur base
neuve = néant)."""

import frappe

TARGET_TZ = "Africa/Porto-Novo"


def execute():
    frappe.db.set_single_value("System Settings", "time_zone", TARGET_TZ)
    frappe.clear_cache()  # invalide le cache du Single → get_system_timezone() lit la cible
