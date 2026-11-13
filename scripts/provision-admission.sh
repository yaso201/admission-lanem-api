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
BENCH="$ROOT/admission"
SECRETS="${EMELA_DEV_SECRETS:-$HOME/.emela-dev-secrets}"
REPO="${ADMISSION_REPO:-https://github.com/yaso201/admission-lanem-api.git}"
REF="${ADMISSION_REF:-main}"
APP=admission
PYTHON="${EMELA_PYTHON:-python3.11}"
DB_HOST=127.0.0.1; DB_PORT=3306
WEB_PORT=8010; SOCKETIO_PORT=9010; REDIS_CACHE_PORT=6384; REDIS_QUEUE_PORT=6385
SITES=(admission-dev admission-test)
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
    git -C "apps/$APP" fetch -q origin "$REF" && git -C "apps/$APP" merge -q --ff-only "origin/$REF"
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
      --db-name "$(printf '%s' "$site" | tr '-' '_')" \
      --db-root-username root --db-root-password "$ROOT_PW" \
      --mariadb-user-host-login-scope=localhost \
      --admin-password "$ADMIN_PW" </dev/null
    unset ADMIN_PW
  fi
  bench --site "$site" set-config mute_emails 1        # C5 : aucun courriel ne part du banc
  bench --site "$site" list-apps | grep -qE "^$APP( |\$)" || bench --site "$site" install-app "$APP" </dev/null
  bench --site "$site" migrate </dev/null
done
bench --site admission-test set-config allow_tests true
unset ROOT_PW

# ---------------------------------------------------------------- 5. assets
step "bench build"
bench build || warn "bench build en échec"

# ---------------------------------------------------------------- 6. contrôles de sortie
step "contrôles"
printf '%-10s %s\n' frappe "$(git -C apps/frappe rev-parse --short=7 HEAD)" "$APP" "$(git -C "apps/$APP" rev-parse --short=7 HEAD)"
for site in "${SITES[@]}"; do
  echo "-- $site"; bench --site "$site" list-apps
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
