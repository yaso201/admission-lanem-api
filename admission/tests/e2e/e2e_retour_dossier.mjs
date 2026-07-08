// FIX-RETOUR-DOSSIER — preuve GR1-GR6 sur bundle LIVE (recette). Fixtures par CHEMIN MÉTIER
// (build_reflet → build_to), navigateur réel (puppeteer), aucun statut seedé.
//   GR1  symptôme : INC → upload pièce → re-soumettre → atterrit /suivi (JAMAIS /paiement)
//   GR2  candidat payé : URL directe /paiement et /recapitulatif → redirigé /suivi (INC puis SOU)
//   GR3  geste terminal INC visible sur /pieces (bouton re-soumettre), CTA tunnel masqué
//   GR4  lien INC tokenisé : ouvre le dossier sur un contexte VIERGE (token du mail INC)
//   GR5  non-régression in-place : carte ACO (upload diplôme) + carte ACC (frais 2)
//   GR6  garde argent runtime : submit_payment_online sur frais1 confirmé → 409 ALREADY_PAID
// Prérequis : back branche déployé recette + front applicant déployé. Lancer :
//   node --env-file=admission/tests/e2e/.env admission/tests/e2e/e2e_retour_dossier.mjs
import {
  launchPlain, armInterception, APPLICANT_URL,
  benchExec, purgeFixtures, statusCounts, pick, emitOtp,
} from './lib_session.mjs';
import { writeFileSync } from 'node:fs';

const API = 'https://api-admission-rec.lanem.bj/api/method/admission.api.public.';
const SHOTS = process.env.PROOF_SHOTS_DIR || '/tmp';
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const results = [];
const rec = (n, pass, d) => { results.push(pass); console.log((pass ? 'PASS ' : 'FAIL ') + n + (d ? ' :: ' + d : '')); };

function buildFixture(etat) {
  const out = benchExec('build_reflet', { etat });
  const id = pick(out, 'FIXTURE_ID'); const token = pick(out, 'FIXTURE_TOKEN');
  if (!id || !token) throw new Error(`fixture ${etat}: id/token absent\n` + out.slice(-400));
  return { id, token };
}

async function anchor(page, id, token) {
  await page.evaluateOnNewDocument((o) => {
    try { localStorage.setItem('emela.admission.resume', JSON.stringify(o)); } catch (e) { /* ignore */ }
  }, { id, token, exp: 9999999999999 });
}

async function goTo(page, path) {
  await page.goto(APPLICANT_URL + path, { waitUntil: 'domcontentloaded', timeout: 60000 });
  await sleep(2500);   // gardes getDossier + redirect éventuel
}

async function shot(page, name) {
  try { await page.screenshot({ path: `${SHOTS}/${name}.png`, fullPage: false }); } catch (e) { /* ignore */ }
}

function apiGetDossier(id, token) {
  return fetch(`${API}get_dossier?dossier_id=${encodeURIComponent(id)}&token=${encodeURIComponent(token)}`)
    .then((r) => r.json()).then((j) => j.message || j);
}

console.log('== FIX-RETOUR-DOSSIER — preuve bundle LIVE ==');
const baseline = statusCounts();
console.log('baseline avant :', baseline);

const browser = await launchPlain();
try {
  /* ---- Fixture INC (frais1 CONFIRMÉ par chemin métier, token = celui du mail INC) ---- */
  const inc = buildFixture('INC');
  console.log('fixture INC :', inc.id);

  /* GR4 — contexte VIERGE : le lien tokenisé du mail INC ouvre le dossier (adoption + OTP + routage) */
  {
    const page = await browser.newPage();
    await armInterception(page);   // PAS d'anchor : localStorage vierge = appareil vierge
    await page.goto(`${APPLICANT_URL}/reprise/?dossier=${inc.id}&token=${inc.token}`,
      { waitUntil: 'domcontentloaded', timeout: 60000 });
    await page.waitForSelector('#dossier-pill', { timeout: 20000 }).catch(() => {});
    await sleep(2500);             // adoptFromUrl + sendCode (nouveau mail OTP)
    const pill = await page.evaluate(() => (document.getElementById('dossier-pill') || {}).textContent || '');
    rec('GR4a lien INC tokenisé adopté sur contexte vierge', pill.includes(inc.id), `pill="${pill}"`);
    // OTP réel (file mail serveur) → vérification → routage state-aware
    const code = emitOtp(inc.id);
    await page.evaluate((c) => {
      const ds = Array.from(document.querySelectorAll('#card-otp input')).slice(0, 6);
      ds.forEach((d, i) => { d.value = c[i]; d.dispatchEvent(new Event('input', { bubbles: true })); });
    }, code);
    await page.click('#btn-verify');
    await page.waitForNavigation({ waitUntil: 'domcontentloaded', timeout: 30000 }).catch(() => {});
    await sleep(2000);
    const url1 = page.url();
    rec('GR4b routage post-OTP INC → /suivi (resolveStep)', /\/suivi\/?$/.test(new URL(url1).pathname), url1);
    await shot(page, '01-gr4-reprise-vierge-vers-suivi');
    // token roté par verify_otp : relire l'ancre pour la suite
    const anchored = await page.evaluate(() => JSON.parse(localStorage.getItem('emela.admission.resume') || '{}'));
    if (anchored.token) { inc.token = anchored.token; }
    await page.close();
  }

  /* GR2 (INC payé) — URL DIRECTE /paiement et /recapitulatif → redirigé /suivi */
  for (const path of ['/paiement/', '/recapitulatif/']) {
    const page = await browser.newPage();
    await armInterception(page);
    await anchor(page, inc.id, inc.token);
    await goTo(page, path);
    const landed = new URL(page.url()).pathname;
    rec(`GR2 INC payé : URL directe ${path} → /suivi`, /\/suivi\/?$/.test(landed), `atterri ${landed}`);
    if (path === '/paiement/') { await shot(page, '02-gr2-paiement-direct-redirige-suivi'); }
    await page.close();
  }

  /* GR1+GR3 — carte INC sur /suivi → /pieces : geste terminal visible, CTA tunnel masqué,
     upload complémentaire, re-soumission → atterrit /suivi (LE SYMPTÔME, tué) */
  {
    const page = await browser.newPage();
    await armInterception(page);
    await anchor(page, inc.id, inc.token);
    await goTo(page, '/suivi/');
    const cardTxt = await page.evaluate(() => (document.getElementById('sv-action') || {}).textContent || '');
    rec('GR1a carte INC sur /suivi (motif + lien pièces)', /Complément requis/.test(cardTxt) && /pièce à compléter/i.test(cardTxt), '');
    await shot(page, '03-gr1-suivi-carte-inc');

    await goTo(page, '/pieces/');
    const st = await page.evaluate(() => ({
      resubmitVisible: !(document.getElementById('resubmit-box') || { hidden: true }).hidden,
      btnLabel: (document.getElementById('resubmit-btn') || {}).textContent || '',
      nextHidden: (document.getElementById('ab-next') || {}).hidden === true,
    }));
    rec('GR3a bloc re-soumission VISIBLE en INC sur /pieces', st.resubmitVisible, '');
    rec('GR3b bouton = « Re-soumettre mon dossier »', /Re-soumettre mon dossier/.test(st.btnLabel), `label="${st.btnLabel}"`);
    rec('GR3c CTA tunnel (« Continuer vers le récapitulatif ») MASQUÉ en INC', st.nextHidden, '');
    await shot(page, '04-gr3-pieces-inc-geste-terminal');

    // upload d'une pièce COMPLÉMENTAIRE (re-dépôt réel : 1er input file de la liste)
    writeFileSync('/tmp/fixture-piece.png', Buffer.from(
      'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==', 'base64'));
    const inputHandle = await page.$('#doc-list input[type=file]');
    rec('GR1b input upload présent (re-dépôt possible en INC)', !!inputHandle, '');
    if (inputHandle) {
      await inputHandle.uploadFile('/tmp/fixture-piece.png');
      await sleep(6000);   // upload 3G-safe + re-render
      const after = await page.evaluate(() => ({
        nextHidden: (document.getElementById('ab-next') || {}).hidden === true,
        resubmitEnabled: !(document.getElementById('resubmit-btn') || {}).disabled,
      }));
      rec('GR1c après upload : CTA tunnel TOUJOURS masqué (plus de bascule paiement)', after.nextHidden, '');
      rec('GR1d après upload : re-soumission activable', after.resubmitEnabled, '');
      await shot(page, '05-gr1-pieces-apres-upload');
    }

    // geste terminal : re-soumettre → INC→SOU → atterrit /suivi (jamais /paiement)
    await page.click('#resubmit-btn');
    await page.waitForNavigation({ waitUntil: 'domcontentloaded', timeout: 30000 }).catch(() => {});
    await sleep(2000);
    const landed = new URL(page.url()).pathname;
    rec('GR1e SYMPTÔME TUÉ : re-soumission INC → atterrit /suivi', /\/suivi\/?$/.test(landed), `atterri ${landed}`);
    const d1 = await apiGetDossier(inc.id, inc.token);
    rec('GR1f dossier re-soumis : statut SOU (repart en étude)', d1.ok && d1.data.statut === 'SOU', `statut=${d1.ok && d1.data.statut}`);
    await shot(page, '06-gr1-atterrissage-suivi-apres-resoumission');
    await page.close();
  }

  /* GR2bis (SOU payé) — même dossier après re-soumission : /paiement direct → /suivi */
  {
    const page = await browser.newPage();
    await armInterception(page);
    await anchor(page, inc.id, inc.token);
    await goTo(page, '/paiement/');
    const landed = new URL(page.url()).pathname;
    rec('GR2bis SOU payé : URL directe /paiement → /suivi', /\/suivi\/?$/.test(landed), `atterri ${landed}`);
    await page.close();
  }

  /* GR6 runtime — garde ARGENT intacte : initiation paiement sur frais1 confirmé → 409 ALREADY_PAID */
  {
    const r = await fetch(API + 'submit_payment_online', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ dossier_id: inc.id, token: inc.token, consent_refund: 1, idempotency_key: 'proof-' + inc.id }),
    }).then((x) => x.json()).then((j) => j.message || j);
    const code = r && r.error && r.error.code;
    rec('GR6 garde argent : submit_payment_online (frais1 confirmé) → ALREADY_PAID', code === 'ALREADY_PAID', `code=${code}`);
  }

  /* GR5 — non-régression patterns in-place : carte ACO (upload diplôme) + carte ACC (frais 2) */
  {
    const aco = buildFixture('ACO');
    const page = await browser.newPage();
    await armInterception(page);
    await anchor(page, aco.id, aco.token);
    await goTo(page, '/suivi/');
    const acoSt = await page.evaluate(() => {
      const a = document.getElementById('sv-action');
      return { txt: (a || {}).textContent || '', hasUpload: !!(a && a.querySelector('input[type=file]')) };
    });
    rec('GR5a carte ACO in-place intacte (upload diplôme sur /suivi)', /conditionnelle/i.test(acoSt.txt) && acoSt.hasUpload, '');
    await shot(page, '07-gr5-carte-aco-intacte');
    await page.close();
  }
  {
    const acc = buildFixture('ACC');
    const page = await browser.newPage();
    await armInterception(page);
    await anchor(page, acc.id, acc.token);
    await goTo(page, '/suivi/');
    const accTxt = await page.evaluate(() => (document.getElementById('sv-action') || {}).textContent || '');
    rec('GR5b carte ACC in-place intacte (frais 2 sur /suivi)', /admission/i.test(accTxt) && /inscription|frais/i.test(accTxt), '');
    await shot(page, '08-gr5-carte-acc-intacte');
    await page.close();
  }
} finally {
  await browser.close();
  const purged = purgeFixtures();
  console.log('purge :', JSON.stringify(purged));
  console.log('baseline après :', statusCounts());
}

const pass = results.filter(Boolean).length;
console.log(`\n== BILAN : ${pass}/${results.length} PASS ==`);
process.exit(pass === results.length ? 0 : 1);
