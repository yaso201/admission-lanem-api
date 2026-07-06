// CONFORMITÉ-E2E — FIX-D-CONF-05/07/08 : le candidat VOIT la décision motivée sur /suivi (bundle LIVE
// applicant). Pour chaque état à décision (REJ/REF/DES/ATT) construit par CHEMIN MÉTIER réel, on ancre
// le dossier (id+token) dans localStorage, on charge /suivi et on asserte que la carte affiche le motif
// (ou le rang). + anti-IDOR : un token étranger ne voit pas le dossier. Aucun harness réordonné.
// Prérequis : front applicant DÉPLOYÉ avec le changement suivi.astro. Lancer :
//   node --env-file=.env admission/tests/e2e/e2e_reflets.mjs
import {
  launchPlain, armInterception, APPLICANT_URL,
  benchExec, purgeFixtures, statusCounts, pick,
} from './lib_session.mjs';

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const results = [];
const rec = (n, pass, d) => { results.push(pass); console.log((pass ? 'PASS ' : 'FAIL ') + n + (d ? ' :: ' + d : '')); };

// état → (kind fixture, sous-chaîne attendue dans la carte /suivi — motif staff verbatim ou rang)
const CASES = [
  { etat: 'REJ', attendu: 'dossier rejeté' },        // reject_dossier(motif="Fixture E2E — dossier rejeté.")
  { etat: 'REF', attendu: 'refus de démonstration' }, // refuse(motif="Fixture E2E — refus de démonstration.")
  { etat: 'DES', attendu: 'désistement candidat' },   // withdraw(motif="Fixture E2E — désistement candidat.")
  { etat: 'ATT', attendu: 'rang 1' },                 // waitlist(rang=1) → « … rang 1 »
];

async function anchorAndLoad(page, id, token) {
  await page.evaluateOnNewDocument((o) => {
    try { localStorage.setItem('emela.admission.resume', JSON.stringify(o)); } catch (e) { /* ignore */ }
  }, { id, token, exp: 9999999999999 });
  await page.goto(APPLICANT_URL + '/suivi/', { waitUntil: 'domcontentloaded', timeout: 60000 });
  await page.waitForSelector('#sv-action, #sv-blocked', { timeout: 25000 }).catch(() => {});
  await sleep(1200);
}

console.log('== CONFORMITÉ-E2E FIX-D-CONF-05/07/08 — reflets candidat sur bundle LIVE ==');
console.log('baseline avant :', statusCounts());

const browser = await launchPlain();
try {
  for (const c of CASES) {
    let id, token;
    try {
      const out = benchExec('build_reflet', { etat: c.etat });
      id = pick(out, 'FIXTURE_ID'); token = pick(out, 'FIXTURE_TOKEN');
      if (!id || !token) throw new Error('id/token absent\n' + out.slice(-400));
    } catch (e) { rec(`${c.etat} fixture`, false, e.message.split('\n')[0]); continue; }

    const page = await browser.newPage();
    await armInterception(page);
    await anchorAndLoad(page, id, token);
    const txt = await page.evaluate(() => {
      const a = document.getElementById('sv-action');
      const b = document.getElementById('sv-badge-txt');
      return { action: (a ? a.textContent : '') || '', badge: (b ? b.textContent : '') || '' };
    });
    const ok = txt.action.toLowerCase().includes(c.attendu.toLowerCase());
    rec(`${c.etat} → carte /suivi affiche « ${c.attendu} »`, ok,
        `badge="${txt.badge.trim().slice(0, 30)}" carte~"${txt.action.replace(/\s+/g, ' ').trim().slice(0, 70)}"`);

    // Anti-IDOR (GR4) : token étranger sur le même dossier → get_dossier refuse → /suivi bloqué.
    const idorPage = await browser.newPage();
    await armInterception(idorPage);
    await anchorAndLoad(idorPage, id, 'TOKEN-ETRANGER-INVALIDE');
    const blocked = await idorPage.evaluate(() => {
      const bl = document.getElementById('sv-blocked');
      const st = document.getElementById('sv-status');
      return { blocked: bl && !bl.hidden, statusShown: st && !st.hidden };
    });
    rec(`${c.etat} anti-IDOR : token étranger ne voit pas le dossier`, blocked.blocked && !blocked.statusShown,
        `blocked=${blocked.blocked} status=${blocked.statusShown}`);
    await idorPage.close();
    await page.close();
  }
} finally {
  await browser.close();
  console.log('purge :', JSON.stringify(purgeFixtures()));
  console.log('baseline après :', statusCounts());
}
const ok = results.filter(Boolean).length;
console.log(`\n===== REFLETS ${ok}/${results.length} — ${ok === results.length ? 'OK' : 'ÉCHEC'} =====`);
process.exit(ok === results.length ? 0 : 3);
