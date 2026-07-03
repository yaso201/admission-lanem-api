// ENABLER-FIXTURES-E2E — scénario DÉMO : une action POST-SOU réelle sur le bundle live.
// Un dossier ETU fixture (construit par chemin métier via recette_fixtures.build_one target=ETU) est
// admis par un Responsable (resp.recette) DEPUIS l'UI /dossier : bouton « Admettre » → toast → refresh.
// Prérequis env : FIXTURE_ID = id du dossier ETU fixture. Voir .env.example.
import { mintSid, launchAuthed } from './lib_session.mjs';

const STAFF_URL = process.env.FIXTURE_STAFF_URL || 'https://staff-rec.lanem.bj';
const ID = process.env.FIXTURE_ID;
const RESP = process.env.FIXTURE_RESP_USER || 'resp.recette@lanem.bj';
if (!ID) { console.error('env FIXTURE_ID manquant (id du dossier ETU fixture)'); process.exit(2); }

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const results = [];
const rec = (n, pass, d) => { results.push(pass); console.log((pass ? 'PASS ' : 'FAIL ') + n + (d ? ' :: ' + d : '')); };
const readStatus = (page) => page.evaluate(() => {
  const el = document.querySelector('#dh-state .st-code');
  return el ? el.textContent.trim() : 'none';
});

const sid = mintSid(RESP);
const browser = await launchAuthed(sid);
try {
  const page = await browser.newPage();
  await page.goto(STAFF_URL + '/dossier?c=' + ID, { waitUntil: 'networkidle2', timeout: 45000 });
  await page.waitForSelector('#act-body .act-btn', { timeout: 20000 }).catch(() => {});
  await sleep(1200);

  const before = await readStatus(page);
  rec('1. dossier fixture chargé en ETU', before === 'ETU', 'statut=' + before);

  const clicked = await page.evaluate(() => {
    const b = [...document.querySelectorAll('#act-body .act-btn')].find((x) => /Admettre/.test(x.textContent));
    if (!b) return false; b.click(); return true;
  });
  rec('2. bouton « Admettre » présent (rôle Responsable) et cliqué', clicked);

  await page.waitForFunction(
    () => [...document.querySelectorAll('div[role="status"]')].some((t) => /admis/i.test(t.textContent)),
    { timeout: 15000 },
  ).catch(() => {});
  const toast = await page.evaluate(() =>
    [...document.querySelectorAll('div[role="status"]')].map((t) => t.textContent).find((x) => /admis/i.test(x)) || 'none');
  rec('3. toast succès « Dossier admis (ADM) » affiché', /admis/i.test(toast), toast);

  await sleep(2500);   // refresh() re-render après l'action
  const after = await readStatus(page);
  rec('4. après refresh, statut passé à ADM', after === 'ADM', 'statut=' + after);

  await page.close();
} finally {
  await browser.close();
}
const ok = results.every(Boolean);
console.log(`\n===== DEMO ${results.filter(Boolean).length}/${results.length} — ${ok ? 'OK' : 'ÉCHEC'} =====`);
process.exit(ok ? 0 : 3);
