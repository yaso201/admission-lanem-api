// CONFORMITÉ-E2E Bloc 1 (B-3) — TUNNEL CANDIDAT sur le bundle LIVE (admission-rec).
// Parcours réel bout-en-bout dans le navigateur : create (formulaire /identite) → OTP (lu server-side
// depuis l'Email Queue) → dépôt des pièces → déclaration OFFLINE (SOP) → suivi ; puis re-soumission
// après rejet d'une pièce par le staff. Interception UPLOAD-3G : tout hôte hors *.lanem.bj est bloqué.
// Aucun code applicatif touché. Lancer : node --env-file=.env admission/tests/e2e/e2e_candidat.mjs
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {
  launchPlain, armInterception, APPLICANT_URL,
  benchExec, emitOtp, purgeFixtures, statusCounts, pick,
} from './lib_session.mjs';

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const results = [];
function rec(name, pass, detail) {
  results.push({ name, pass });
  console.log((pass ? 'PASS ' : 'FAIL ') + name + (detail ? ' :: ' + detail : ''));
}

// PNG valide (magic bytes) — pièce déposée
const PNG = path.join(os.tmpdir(), 'e2e-b3-piece.png');
fs.writeFileSync(PNG, Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==', 'base64'));

// contexte d'amorçage (session ouverte + niveau + date bac profil standard)
const ctx = benchExec('ui_context');
const SESSION = pick(ctx, 'UICTX_SESSION');
const LEVEL = pick(ctx, 'UICTX_LEVEL');
const DATEBAC = pick(ctx, 'UICTX_DATEBAC');
const TAGDOMAIN = pick(ctx, 'UICTX_TAGDOMAIN');
const email = `fixture-b3ui-${Date.now().toString(36)}${Math.floor(Math.random() * 1e4)}@${TAGDOMAIN}`;

console.log('== CONFORMITÉ-E2E Bloc 1 (B-3) — tunnel candidat sur bundle LIVE ==');
console.log(`baseline avant : ${statusCounts()}  (session=${SESSION} level=${LEVEL} email=${email})`);

const browser = await launchPlain();
let dossier = null;
try {
  const page = await browser.newPage();
  await armInterception(page);

  // ── create : formulaire /identite réel ────────────────────────────────────────────────────
  await page.goto(`${APPLICANT_URL}/identite/?session=${encodeURIComponent(SESSION)}`, { waitUntil: 'domcontentloaded', timeout: 60000 });
  await page.waitForFunction((lvl) => {
    const s = document.getElementById('niveau');
    return s && [...s.options].some((o) => o.value === lvl);
  }, { timeout: 25000 }, LEVEL).catch(() => {});
  const niveauOk = await page.evaluate((lvl) => {
    const s = document.getElementById('niveau');
    return !!(s && [...s.options].some((o) => o.value === lvl));
  }, LEVEL);
  rec('1. /identite chargé, niveaux de la session peuplés', niveauOk, `niveau=${LEVEL}`);

  await page.type('#prenom', 'Fixture');
  await page.type('#nom', 'B3UI');
  await page.type('#email', email);
  await page.type('#tel', '+22990000000');
  await page.select('#niveau', LEVEL);
  await page.evaluate((v) => { const d = document.getElementById('datebac'); d.value = v; d.dispatchEvent(new Event('input', { bubbles: true })); }, DATEBAC);
  await page.evaluate(() => {
    for (const id of ['consent-dp', 'consent-cgv']) { const c = document.getElementById(id); if (c && !c.checked) { c.checked = true; c.dispatchEvent(new Event('change', { bubbles: true })); } }
  });
  await page.waitForFunction(() => { const b = document.getElementById('cta-identite'); return b && !b.disabled; }, { timeout: 15000 }).catch(() => {});
  const ctaOk = await page.evaluate(() => { const b = document.getElementById('cta-identite'); return b && !b.disabled; });
  rec('2. formulaire valide → bouton « Créer mon dossier » actif', ctaOk);
  await page.click('#cta-identite');

  // ── OTP : lu server-side (Email Queue) puis saisi ─────────────────────────────────────────
  await page.waitForFunction(() => { const b = document.getElementById('otp-body'); return b && !b.hidden; }, { timeout: 30000 }).catch(() => {});
  dossier = await page.evaluate(() => window.AdmissionTunnel.getDossierId());
  rec('3. dossier créé (ancré localStorage) + phase OTP', !!dossier, `dossier=${dossier}`);

  let code = null;
  for (let i = 0; i < 5 && !code; i++) { try { code = emitOtp(dossier); } catch (e) { await sleep(2500); } }
  await page.evaluate((c) => {
    const ds = [...document.querySelectorAll('.otp-digit')];
    ds.forEach((d, i) => { d.value = c[i] || ''; d.dispatchEvent(new Event('input', { bubbles: true })); });
  }, code || '000000');
  await page.click('#otp-verify');
  await page.waitForFunction(() => location.pathname.replace(/\/$/, '').endsWith('/pieces'), { timeout: 25000 }).catch(() => {});
  const onPieces = await page.evaluate(() => location.pathname.includes('/pieces'));
  rec('4. OTP (lu server-side) vérifié → redirection vers les pièces', onPieces, `otp=${code ? 'ok' : 'introuvable'}`);

  // ── dépôt des pièces requises ─────────────────────────────────────────────────────────────
  await page.waitForSelector('#doc-list', { timeout: 20000 }).catch(() => {});
  await sleep(1500);
  let inputs = await page.$$('#doc-list input[type="file"]');
  for (const inp of inputs) { await inp.uploadFile(PNG); await sleep(2200); }
  await page.waitForFunction(() => { const b = document.getElementById('ab-next'); return b && !b.disabled; }, { timeout: 30000 }).catch(() => {});
  const gateOk = await page.evaluate(() => { const b = document.getElementById('ab-next'); return b && !b.disabled; });
  rec('5. pièces requises déposées → étape franchissable', gateOk, `${inputs.length} champ(s) fichier`);

  // ── déclaration OFFLINE (SOP) ─────────────────────────────────────────────────────────────
  await page.goto(`${APPLICANT_URL}/paiement/`, { waitUntil: 'domcontentloaded', timeout: 60000 });
  await page.waitForSelector('.pay-path[data-path="sop"]', { timeout: 20000 }).catch(() => {});
  await page.click('.pay-path[data-path="sop"]');
  await sleep(500);
  await page.evaluate(() => { const c = document.getElementById('sop-cb'); if (c && !c.checked) { c.checked = true; c.dispatchEvent(new Event('change', { bubbles: true })); } });
  await page.waitForFunction(() => { const b = document.getElementById('pay-cta'); return b && !b.disabled; }, { timeout: 10000 }).catch(() => {});
  await page.click('#pay-cta');
  await page.waitForFunction(() => location.pathname.includes('/paiement-sop'), { timeout: 25000 }).catch(() => {});
  const onSop = await page.evaluate(() => location.pathname.includes('/paiement-sop'));
  rec('6. déclaration offline → soumission provisoire (SOP)', onSop, `url=${await page.evaluate(() => location.pathname)}`);

  // ── suivi ─────────────────────────────────────────────────────────────────────────────────
  await page.goto(`${APPLICANT_URL}/suivi/`, { waitUntil: 'domcontentloaded', timeout: 60000 });
  await page.waitForSelector('#sv-badge-txt', { timeout: 20000 }).catch(() => {});
  await sleep(1500);
  const badge = await page.evaluate(() => { const e = document.getElementById('sv-badge-txt'); return e ? e.textContent.trim() : ''; });
  rec('7. suivi affiche l\'état du dossier au candidat', !!badge, `badge="${badge}"`);

  // ── re-soumission après rejet d'une pièce ─────────────────────────────────────────────────
  const rj = benchExec('stage_reject', { dossier });
  const rejected = pick(rj, 'REJECTED_PIECE');
  await page.goto(`${APPLICANT_URL}/pieces/`, { waitUntil: 'domcontentloaded', timeout: 60000 });
  await page.waitForSelector('#doc-list', { timeout: 20000 }).catch(() => {});
  await sleep(2000);
  const rejVisible = await page.evaluate(() => !!document.querySelector('.uperror-title, .uperror'));
  rec('8. le candidat VOIT la pièce rejetée + motif', rejVisible, `pièce=${rejected}`);

  inputs = await page.$$('#doc-list input[type="file"]');
  for (const inp of inputs) { await inp.uploadFile(PNG); await sleep(2200); }
  await page.waitForFunction(() => { const b = document.getElementById('resubmit-btn'); return b && b.offsetParent !== null; }, { timeout: 15000 }).catch(() => {});
  await page.click('#resubmit-btn').catch(() => {});
  await sleep(2500);
  const msg = await page.evaluate(() => { const e = document.getElementById('resubmit-msg'); return e ? e.textContent.trim() : ''; });
  const msgErr = await page.evaluate(() => { const e = document.getElementById('resubmit-msg'); return !!(e && e.classList.contains('is-error')); });
  rec('9. re-dépôt + « j\'ai re-déposé » → re-soumission acceptée', !!msg && !msgErr, `msg="${msg}"`);

  await page.close();
} finally {
  await browser.close();
  const purged = purgeFixtures();
  console.log('purge :', JSON.stringify(purged));
  console.log('baseline après :', statusCounts());
}
const ok = results.filter((r) => r.pass).length;
console.log(`\n===== CANDIDAT ${ok}/${results.length} — ${ok === results.length ? 'OK' : 'ÉCHEC'} =====`);
process.exit(ok === results.length ? 0 : 3);
