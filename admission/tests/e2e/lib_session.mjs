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

export async function launchAuthed(sid) {
  const browser = await puppeteer.launch({ headless: 'new', args: ['--no-sandbox'] });
  const cookie = { name: 'sid', value: sid, domain: SITE, path: '/', secure: true, httpOnly: true, sameSite: 'Lax' };
  if (browser.setCookie) await browser.setCookie(cookie);
  else { const p = await browser.newPage(); await p.setCookie(cookie); await p.close(); }
  return browser;
}
