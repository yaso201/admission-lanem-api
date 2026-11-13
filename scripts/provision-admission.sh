#!/usr/bin/env bash
# provision-admission.sh — monte un bench de développement pour l'app admission, depuis GitHub, jusqu'aux sites semés.
# MANDAT-02 (MEP-10). Idempotent : relancé sur un bench existant, il ne refait que ce qui manque.
#
# Ce dépôt est une APP, pas un bench : le script crée ~/emela-dev/admission (bench neuf) et y installe l'app.
# Aucun secret n'est écrit ni affiché par ce script. Il lit :
#   - le mot de passe root MariaDB : $MARIADB_ROOT_PASSWORD, sinon $SECRETS/mariadb-root
#   - le mot de passe Administrator de <site> : $ADMIN_PASSWORD_<SITE> (ex. ADMIN_PASSWORD_ADMISSION_DEV),
#     sinon $SECRETS/admin.<site>
# Banc isolé : aucune configuration campus_base_url / uf_backoffice_url / uf_api_* (aucun appel sortant) ;
# les scripts manuels scenario_recette.py et _recette_notes.py ne sont JAMAIS exécutés sur le banc
# (adresses de personnels réels) ; aucun e2e (ils visent la recette).
# Prérequis (BANC-DEV.md) : ~/emela-dev/outils/env.sh (Python 3.11, bench, Node 22, yarn 1, redis-server),
# MariaDB 10.11 (MacPorts) servi sur 127.0.0.1:3306.
set -euo pipefail
umask 077

ROOT="${EMELA_DEV_ROOT:-$HOME/emela-dev}"
[ -r "$ROOT/outils/env.sh" ] && . "$ROOT/outils/env.sh"
SECRETS="${EMELA_DEV_SECRETS:-$HOME/.emela-dev-secrets}"
REPO="${ADMISSION_REPO:-https://github.com/yaso201/admission-lanem-api.git}"
REF="${ADMISSION_REF:-main}"
APP=admission
PYTHON="${EMELA_PYTHON:-python3.11}"
DB_HOST=127.0.0.1; DB_PORT=3306
# Dossier, sites, bases et ports surchargeables pour monter un second bench à côté du banc en service, sans le
# toucher (VERDICT-MANDAT-02-PC2, D2). Valeurs par défaut = le banc.
BENCH="${ADMISSION_BENCH:-$ROOT/admission}"
SITE_DEV="${ADMISSION_SITE_DEV:-admission-dev}"; SITE_TEST="${ADMISSION_SITE_TEST:-admission-test}"
DB_DEV="${ADMISSION_DB_DEV:-$(printf '%s' "$SITE_DEV" | tr '-' '_')}"
DB_TEST="${ADMISSION_DB_TEST:-$(printf '%s' "$SITE_TEST" | tr '-' '_')}"
WEB_PORT="${ADMISSION_WEB_PORT:-8010}"; SOCKETIO_PORT="${ADMISSION_SOCKETIO_PORT:-9010}"
REDIS_CACHE_PORT="${ADMISSION_REDIS_CACHE_PORT:-6384}"; REDIS_QUEUE_PORT="${ADMISSION_REDIS_QUEUE_PORT:-6385}"
SITES=("$SITE_DEV" "$SITE_TEST")
TIME_ZONE=Africa/Porto-Novo                              # B2
SMTP_FERME_PORT="${EMELA_SMTP_FERME_PORT:-2525}"         # B1 : port local qui doit rester fermé
# Même frappe que les benches campus et gestion (étiquette v15.103.2).
FRAPPE_SHA=665fce241eeed17b8869168cd9103d795f39e3e1
LOG="$ROOT/journaux/provision-admission-$(date +%Y%m%d-%H%M%S).log"
WARN=0

mkdir -p "$ROOT/journaux"
exec > >(tee -a "$LOG") 2>&1
step() { printf '\n== %s — %s\n' "$(date +%H:%M:%S)" "$*"; }
warn() { WARN=$((WARN + 1)); printf 'AVERTISSEMENT %d : %s\n' "$WARN" "$*"; }
die()  { printf 'ARRÊT : %s\n' "$*" >&2; exit 1; }

secret() {
  local v="${!1:-}"
  if [ -z "$v" ]; then
    [ -r "$SECRETS/$2" ] || die "secret absent : $2 (ni \$$1 ni $SECRETS/$2)"
    v="$(cat "$SECRETS/$2")"
  fi
  [ -n "$v" ] || die "secret vide : $2"
  printf '%s' "$v"
}
site_var() { printf 'ADMIN_PASSWORD_%s' "$(printf '%s' "$1" | tr 'a-z-' 'A-Z_')"; }
db_of() { if [ "$1" = "$SITE_DEV" ]; then printf '%s' "$DB_DEV"; else printf '%s' "$DB_TEST"; fi; }

# B1 (VERDICT-MANDAT-02-PC2) — courriel. Site -dev : mute_emails reste, et un compte de courriel factice sert
# d'expéditeur par défaut. Sans lui, frappe 15.103.2 se rabat sur EmailAccount.create_dummy(), qui n'a pas
# d'email_id : toute création d'utilisateur plante (formataddr). Le compte vise 127.0.0.1:$SMTP_FERME_PORT, port
# vérifié fermé, sans identifiant, adresse en .invalid (domaine réservé, jamais routé).
# Site -test : ni mute_emails, ni compte de courriel, ordonnanceur arrêté (les suites tournent comme en recette).
compte_courriel_factice() {  # $1 site
  if (exec 3<>"/dev/tcp/127.0.0.1/$SMTP_FERME_PORT") 2>/dev/null; then
    warn "$1 : 127.0.0.1:$SMTP_FERME_PORT est OUVERT ; compte de courriel factice non créé"
    return 0
  fi
  (cd sites && ../env/bin/python -I - "$1" "$SMTP_FERME_PORT" <<'PY'
import sys
import frappe
site, port = sys.argv[1], int(sys.argv[2])
frappe.init(site=site, sites_path=".")
frappe.connect()
NOM = "banc — aucun envoi"
# Port fermé exprès : la validation SMTP de EmailAccount.validate() échouerait ; on la saute (drapeau de patch).
frappe.local.flags.in_patch = True
d = frappe.get_doc("Email Account", NOM) if frappe.db.exists("Email Account", NOM) else frappe.new_doc("Email Account")
d.email_account_name = NOM
d.update({"email_id": "aucun-envoi@banc.invalid", "enable_outgoing": 1, "default_outgoing": 1,
          "enable_incoming": 0, "default_incoming": 0, "smtp_server": "127.0.0.1", "smtp_port": port,
          "no_smtp_authentication": 1, "use_tls": 0, "use_ssl_for_outgoing": 0, "awaiting_password": 0,
          "login_id_is_different": 0})
d.save(ignore_permissions=True)
frappe.db.commit()
n = frappe.db.count("Email Account")
print(f"{site} : compte « {NOM} » -> 127.0.0.1:{port}, sortant par défaut ; {n} compte(s) de courriel")
frappe.destroy()
PY
  ) || warn "$1 : création du compte de courriel factice en échec"
}

# Réglages de site B1 (courriel), B2 (fuseau), B3 (host_name), après install-app et migrate.
reglages_site() {  # $1 site  $2 fuseau (vide = inchangé)
  if [ "$1" = "$SITE_DEV" ]; then
    bench --site "$1" set-config mute_emails 1        # C5 : aucun courriel ne part du site -dev
  else
    bench --site "$1" set-config mute_emails None     # B1 : clé retirée sur -test
  fi
  # B3 : wkhtmltopdf charge les ressources du PDF par host_name ; pendant les suites PDF, seul le processus web
  # du site tourne (bench --site <site> serve --port $WEB_PORT), sans ordonnanceur ni workers.
  bench --site "$1" set-config host_name "http://127.0.0.1:$WEB_PORT"
  if [ -n "$2" ]; then   # B2 : sans l'assistant de configuration, frappe se rabat sur Asia/Kolkata
    bench --site "$1" execute frappe.db.set_single_value --args "['System Settings', 'time_zone', '$2']" </dev/null
  fi
  bench --site "$1" clear-cache
  if [ "$1" = "$SITE_DEV" ]; then compte_courriel_factice "$1"; fi
}

# Contrôle de sortie de B1 à B3 (n'imprime aucune valeur sensible : clés de courriel « présente » ou rien).
controle_site() {  # $1 site  $2 fuseau attendu (vide = non contrôlé)
  (cd sites && ../env/bin/python -I - "$1" "$SITE_DEV" "$2" "http://127.0.0.1:$WEB_PORT" <<'PY'
import sys
import frappe
from frappe.utils.scheduler import is_scheduler_disabled
site, dev, tz, host = sys.argv[1:5]
frappe.init(site=site, sites_path=".")
frappe.connect()
c = frappe.conf
# Comptes actifs (entrée ou sortie) seulement : un compte désactivé n'envoie rien (ex. « Jobs », posé par hrms).
comptes = frappe.get_all("Email Account", or_filters={"enable_incoming": 1, "enable_outgoing": 1}, pluck="name")
fuseau = frappe.db.get_single_value("System Settings", "time_zone")
arrete = is_scheduler_disabled(verbose=False)
print(f"{site} : mute_emails={c.get('mute_emails')} host_name={c.get('host_name')} time_zone={fuseau} "
      f"ordonnanceur_arrêté={arrete} comptes_de_courriel_actifs={comptes}")
ecarts = []
if site == dev:
    if not c.get("mute_emails"): ecarts.append("mute_emails absent")
    if comptes != ["banc — aucun envoi"]: ecarts.append("comptes de courriel ≠ le seul compte factice")
else:
    if c.get("mute_emails"): ecarts.append("mute_emails présent")
    if comptes: ecarts.append("compte de courriel actif présent")
    if not arrete: ecarts.append("ordonnanceur actif")
for k in ("mail_server", "mail_login", "mail_password"):
    if c.get(k): ecarts.append(f"clé {k} présente")
if c.get("host_name") != host: ecarts.append("host_name inattendu")
if tz and fuseau != tz: ecarts.append("fuseau inattendu")
frappe.destroy()
for e in ecarts: print("ÉCART", site, ":", e)
sys.exit(1 if ecarts else 0)
PY
  ) || warn "$1 : contrôle B1 à B3 en écart"
}

# ---------------------------------------------------------------- 0. prérequis
step "prérequis"
for c in git "$PYTHON" node yarn redis-server mariadb-admin bench; do
  command -v "$c" >/dev/null || die "outil absent : $c (charger ~/emela-dev/outils/env.sh)"
done
mariadb-admin --no-defaults -h "$DB_HOST" -P "$DB_PORT" ping >/dev/null 2>&1 \
  || die "MariaDB ne répond pas sur $DB_HOST:$DB_PORT (LaunchAgent bj.lanem.emela-dev-mariadb)"
ROOT_PW="$(secret MARIADB_ROOT_PASSWORD mariadb-root)"

# ---------------------------------------------------------------- 1. bench + frappe épinglé
if [ ! -d "$BENCH/env" ]; then
  step "bench init (frappe version-15, $PYTHON)"
  [ -e "$BENCH" ] && die "$BENCH existe sans env/ : bench incomplet, à examiner à la main (rien n'est supprimé)"
  bench init "$BENCH" --frappe-branch version-15 --python "$PYTHON" --skip-assets --no-backups --verbose
fi
cd "$BENCH"
if [ "$(git -C apps/frappe rev-parse HEAD)" != "$FRAPPE_SHA" ]; then
  step "épinglage frappe → ${FRAPPE_SHA:0:7}"
  git -C apps/frappe fetch -q --depth 1 https://github.com/frappe/frappe.git "$FRAPPE_SHA"   # distant « upstream »
  git -C apps/frappe checkout -q --detach "$FRAPPE_SHA"
  ./env/bin/python -m pip install -q -e apps/frappe </dev/null
fi

# ---------------------------------------------------------------- 2. app admission
if [ ! -d "apps/$APP" ]; then
  step "get-app $APP ($REPO, $REF)"
  bench get-app --branch "$REF" --skip-assets "$REPO" </dev/null
else
  step "mise à jour de apps/$APP (fast-forward seulement)"
  if [ -n "$(git -C "apps/$APP" status --porcelain)" ]; then
    warn "apps/$APP a des modifications locales : mise à jour sautée, rien n'est écrasé"
  else
    # bench get-app nomme le distant « upstream », pas « origin » (sans quoi la relance s'arrête ici).
    git -C "apps/$APP" fetch -q upstream "$REF" && git -C "apps/$APP" merge -q --ff-only "upstream/$REF"
  fi
fi
[ -d "apps/$APP" ] || die "apps/$APP absent après get-app"
bench setup requirements </dev/null

# ---------------------------------------------------------------- 3. configuration du bench
step "ports : web $WEB_PORT, socketio $SOCKETIO_PORT, redis $REDIS_CACHE_PORT/$REDIS_QUEUE_PORT ; MariaDB $DB_HOST:$DB_PORT"
bench set-config -g db_host "$DB_HOST"
bench set-config -g -p db_port "$DB_PORT"
bench set-config -g -p webserver_port "$WEB_PORT"
bench set-config -g -p socketio_port "$SOCKETIO_PORT"
bench set-config -g redis_cache "redis://127.0.0.1:$REDIS_CACHE_PORT"
bench set-config -g redis_queue "redis://127.0.0.1:$REDIS_QUEUE_PORT"
bench set-config -g redis_socketio "redis://127.0.0.1:$REDIS_CACHE_PORT"
bench setup redis
printf 'y\n' | bench setup procfile   # confirme l'écrasement d'un Procfile existant
for conf in config/redis_cache.conf config/redis_queue.conf; do
  redis-server "$conf" --daemonize yes >/dev/null
done

# ---------------------------------------------------------------- 4. sites
# L'after_install de l'app sème : politique de mots de passe, workflow et rôles, RIB, catalogue (17 programmes),
# index d'unicité, gabarits de courriels.
for site in "${SITES[@]}"; do
  if [ ! -d "sites/$site" ]; then
    step "new-site $site"
    ADMIN_PW="$(secret "$(site_var "$site")" "admin.$site")"
    bench new-site "$site" \
      --db-name "$(db_of "$site")" \
      --db-root-username root --db-root-password "$ROOT_PW" \
      --mariadb-user-host-login-scope=localhost \
      --admin-password "$ADMIN_PW" </dev/null
    unset ADMIN_PW
  fi
  bench --site "$site" set-config mute_emails 1   # C5 : rien ne part pendant l'installation ; -test : retiré après (B1)
  bench --site "$site" list-apps | grep -qE "^$APP( |\$)" || bench --site "$site" install-app "$APP" </dev/null
  bench --site "$site" migrate </dev/null
  step "réglages B1 à B3 sur $site"
  reglages_site "$site" "$TIME_ZONE"
done
bench --site "$SITE_TEST" set-config allow_tests true
unset ROOT_PW

# ---------------------------------------------------------------- 5. assets
step "bench build"
bench build || warn "bench build en échec"

# ---------------------------------------------------------------- 6. contrôles de sortie
step "contrôles"
printf '%-10s %s\n' frappe "$(git -C apps/frappe rev-parse --short=7 HEAD)" "$APP" "$(git -C "apps/$APP" rev-parse --short=7 HEAD)"
for site in "${SITES[@]}"; do
  echo "-- $site"; bench --site "$site" list-apps; controle_site "$site" "$TIME_ZONE"
  for k in campus_base_url uf_backoffice_url uf_api_key uf_api_secret; do
    v="$(cd "sites/$site" && python3 -c "import json,sys; print('oui' if json.load(open('site_config.json')).get('$k') else '')")"
    [ -z "$v" ] || warn "$site : $k est configuré (le banc doit rester isolé)"
  done
done
if [ -n "$(git -C "apps/$APP" status --porcelain)" ]; then
  git -C "apps/$APP" status --short
  echo "ÉCART : git status de apps/$APP non propre ($WARN avertissement(s)) ; journal : $LOG"
  exit 3
fi
echo "OK avec $WARN avertissement(s) — bench admission prêt ($BENCH) ; journal : $LOG"
