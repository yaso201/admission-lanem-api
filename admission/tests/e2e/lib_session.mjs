// ENABLER-FIXTURES-E2E — lib E2E versionnée (mint session + navigateur authentifié).
// Aucun secret ni chemin machine EN DUR : tout vient de l'environnement (voir .env.example).
//   mintSid(user)   → mint d'une session Frappe pour `user` (défaut admin.admissions) → sid
//   launchAuthed(sid) → Chrome headless avec le cookie sid injecté (cross-subdomain same-site)
import { execFileSync } from 'node:child_process';

const need = (k) => { const v = process.env[k]; if (!v) throw new Error(`env ${k} manquant — voir .env.example`); return v; };
const PUPPETEER_PATH = need('PUPPETEER_PATH');      // chemin d'entrée du module puppeteer (machine locale)
const KEY = need('FIXTURE_SSH_KEY');                 // clé privée SSH recette (JAMAIS versionnée)
const HOST = need('FIXTURE_SSH_HOST');               // ubuntu@X.X.X.X
const SITE = need('FIXTURE_SITE');                   // api-admission-rec.lanem.bj
const BENCH = process.env.FIXTURE_BENCH || '/home/ubuntu/bench-admission';
const BENCH_BIN = process.env.FIXTURE_BENCH_BIN || '/home/ubuntu/.local/bin/bench';
const MINT_PY = new URL('./mint_session.py', import.meta.url).pathname;   // versionné, à côté

const puppeteer = (await import(PUPPETEER_PATH)).default;

export function mintSid(user) {
  // durcissement : `user` est interpolé dans la commande shell distante → n'accepter qu'un email
  // strict (aucun métacaractère shell), pour écarter toute injection via un env mal formé.
  if (user && !/^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+$/.test(user)) {
    throw new Error('MINT_USER invalide (email attendu) : ' + user);
  }
  const py = execFileSync('cat', [MINT_PY], { encoding: 'utf8' });
  const envPrefix = user ? `MINT_USER=${user} ` : '';
  const out = execFileSync('ssh',
    ['-i', KEY, '-o', 'StrictHostKeyChecking=no', '-o', 'ConnectTimeout=25', HOST,
      `cd ${BENCH} && ${envPrefix}${BENCH_BIN} --site ${SITE} console`],
    { input: py, encoding: 'utf8' });
  const m = out.match(/MINTED_SID::([A-Za-z0-9]{40,})/);
  if (!m) throw new Error('mint échoué : ' + out.slice(-400));
  return m[1];
}

/** Navigateur SANS cookie (front candidat = auth par token/localStorage, pas de session Frappe). */
export async function launchPlain() {
  return puppeteer.launch({ headless: 'new', args: ['--no-sandbox'] });
}

export async function launchAuthed(sid) {
  const browser = await puppeteer.launch({ headless: 'new', args: ['--no-sandbox'] });
  const cookie = { name: 'sid', value: sid, domain: SITE, path: '/', secure: true, httpOnly: true, sameSite: 'Lax' };
  if (browser.setCookie) await browser.setCookie(cookie);
  else { const p = await browser.newPage(); await p.setCookie(cookie); await p.close(); }
  return browser;
}

/* ── B-3 : bridge fixtures server-side + multi-rôles ─────────────────────────────────────────
   Le harnais navigateur (candidat + management) AMORCE ses scénarios via des fixtures construites
   PAR CHEMIN MÉTIER côté recette (recette_fixtures). Les appels HTTP internes des fixtures visent
   la base LOOPBACK (127.0.0.1) + en-tête Host : depuis recette, l'URL Cloudflare publique renvoie
   403 (loopback bloqué) — leçon UPLOAD-3G. */

export const STAFF_URL = process.env.FIXTURE_STAFF_URL || 'https://staff-rec.lanem.bj';
export const APPLICANT_URL = process.env.FIXTURE_APPLICANT_URL || 'https://admission-rec.lanem.bj';
export const APISITE = SITE;

const ROLE_USER = {
  admin: process.env.FIXTURE_ADMIN_USER || 'admin.admissions@lanem.bj',
  resp: process.env.FIXTURE_RESP_USER || 'resp.recette@lanem.bj',
  dir: process.env.FIXTURE_DIR_USER || 'direction.recette@lanem.bj',
};
export function roleUser(role) {
  const u = ROLE_USER[role];
  if (!u) throw new Error('rôle inconnu : ' + role);
  return u;
}

const FIX = 'admission.tests.fixtures.recette_fixtures.';

/** Exécute une fonction fixtures (bench execute) server-side, base loopback. Renvoie stdout brut. */
export function benchExec(fn, kwargs) {
  const env = `ADMISSION_FIXTURE_BASE=http://127.0.0.1:8000 ADMISSION_FIXTURE_HOST=${SITE} `;
  let cmd = `cd ${BENCH} && ${env}${BENCH_BIN} --site ${SITE} execute ${FIX}${fn}`;
  if (kwargs) cmd += ` --kwargs '${JSON.stringify(kwargs)}'`;   // execFile → pas de shell local : le JSON survit
  return execFileSync('ssh',
    ['-i', KEY, '-o', 'StrictHostKeyChecking=no', '-o', 'ConnectTimeout=40', HOST, cmd],
    { encoding: 'utf8', maxBuffer: 16 * 1024 * 1024 });
}

/** Extrait le premier marqueur `PREFIX::valeur` (jusqu'au prochain blanc) d'une sortie bench. */
export function pick(out, prefix) {
  const m = out.match(new RegExp(prefix.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + '::([^\\s]+)'));
  return m ? m[1] : null;
}

/** Construit une précondition UI (recette_fixtures.build_ui) → { id, status }. */
export function buildUi(kind) {
  const out = benchExec('build_ui', { kind });
  const id = pick(out, 'FIXTURE_ID');
  if (!id) throw new Error(`build_ui ${kind} : FIXTURE_ID absent\n` + out.slice(-600));
  return { id, status: pick(out, 'FIXTURE_STATUS') };
}

export function emitOtp(dossier) {
  const out = benchExec('emit_otp', { dossier });
  const code = pick(out, 'OTP');
  if (!code) throw new Error('emit_otp : OTP absent\n' + out.slice(-600));
  return code;
}

export function purgeFixtures() {
  const out = benchExec('purge');
  return { purged: pick(out, 'PURGED'), left: (out.match(/LEFT::(\[[^\]]*\])/) || [])[1] || '[]' };
}

export function statusCounts() {
  const out = benchExec('status_counts');
  return (out.match(/STATUS_COUNTS::(.+)/) || [])[1] || '';
}

/** Bloque tout hôte hors *.lanem.bj (fonts, KkiaPay) — évite les hangs headless (leçon UPLOAD-3G). */
export async function armInterception(page) {
  await page.setRequestInterception(true);
  page.on('request', (req) => {
    let host = '';
    try { host = new URL(req.url()).hostname; } catch (e) { host = ''; }
    const ok = req.url().startsWith('data:') || /(^|\.)lanem\.bj$/.test(host);
    if (ok) req.continue(); else req.abort();
  });
}
