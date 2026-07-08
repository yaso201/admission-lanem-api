// FIX-PROGRESSION — preuve GP1-GP6 sur le bundle LIVE (staff-rec), AS chaque VRAI rôle.
// Le front est pur renderer : il rend un bouton ssi sa clé ∈ available_actions (servi par le back,
// dérivé des gardes rôle+statut+MÉTIER + hiérarchie live). Fixtures par CHEMIN MÉTIER (build_one),
// session mintée par rôle, /dossier?c=<id>, assertions sur les boutons rendus (vérité visuelle).
//   GP2  Responsable au RETOUR SOU (pièces re-déposées, non vérifiées) : voit la RE-VÉRIFICATION
//        (verify) + reject + complément — le symptôme ; start_review n'apparaît qu'APRÈS vérif (GP6).
//   GP3  Directeur voit les actions subordonnées (SOU : vérif pièces admin ; ETU : « Admettre » resp)
//   GP4  Directeur@ADM voit « Accepter » ; Responsable@ADM ne le voit PAS (séparation) ;
//        multi-rôle (SM+Direction)@ADM voit « Accepter » (l'ancien uxRole collapse montrait 0 bouton).
//   GP5  état terminal (DES) : 0 bouton + message CLAIR (jamais « undefined »).
//   GP6  un bouton MONTRÉ FONCTIONNE : après vérif, Resp clique « Mettre en étude » → ETU.
// Lancer : node --env-file=admission/tests/e2e/.env admission/tests/e2e/e2e_progression.mjs
import {
  mintSid, launchAuthed, roleUser, STAFF_URL,
  benchExec, purgeFixtures, statusCounts, pick, armInterception,
} from './lib_session.mjs';

const MULTI_USER = process.env.FIXTURE_MULTI_USER || 'yaovi.soglo@lanem.bj'; // SM + tous rôles workflow
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const results = [];
const rec = (n, pass, d) => { results.push(pass); console.log((pass ? 'PASS ' : 'FAIL ') + n + (d ? ' :: ' + d : '')); };
const SHOTS = process.env.PROOF_SHOTS_DIR || '/tmp';

function buildOne(target) {
  const out = benchExec('build_one', { target });
  const id = pick(out, 'FIXTURE_ID');
  if (!id) throw new Error(`build_one ${target}: FIXTURE_ID absent\n` + out.slice(-400));
  return id;
}

const sess = {};
async function pageFor(user) {
  if (!sess[user]) {
    const sid = mintSid(user);
    const browser = await launchAuthed(sid);
    const page = await browser.newPage();
    await armInterception(page);
    sess[user] = { browser, page };
  }
  return sess[user].page;
}
async function closeAll() { for (const s of Object.values(sess)) { try { await s.browser.close(); } catch (e) { /* ignore */ } } }

async function gotoDossier(page, id) {
  await page.goto(STAFF_URL + '/dossier?c=' + id, { waitUntil: 'domcontentloaded', timeout: 60000 });
  await page.waitForSelector('#act-body .act-btn, #act-body .act-lead', { timeout: 25000 }).catch(() => {});
  await sleep(900);
}
// état RENDU (vérité VISUELLE : boutons, vérif pièces, message vide, titre)
const panel = (page) => page.evaluate(() => ({
  btns: [...document.querySelectorAll('#act-body .act-btn')].map((b) => b.textContent.trim()),
  pieceVerify: !![...document.querySelectorAll('#pieces-body [data-pact="verify"]')].length,
  empty: (() => { const p = document.querySelector('#act-body .act-lead'); return p ? p.textContent.trim() : ''; })(),
  title: (document.getElementById('act-title') || {}).textContent || '',
}));
const has = (P, re) => P.btns.some((t) => new RegExp(re).test(t));
const readStatus = (page) => page.evaluate(() => {
  const el = document.querySelector('#dh-state .st-code'); return el ? el.textContent.trim() : 'none';
});

console.log('== FIX-PROGRESSION — preuve bundle LIVE (staff-rec) ==');
console.log('baseline avant :', statusCounts());

try {
  const SOU = buildOne('SOU');   // pièces déposées, NON vérifiées (= retour après ajout de pièces)
  const ETU = buildOne('ETU');   // pièces vérifiées, en étude
  const ADM = buildOne('ADM');
  const DES = buildOne('DES');   // état terminal
  console.log('fixtures :', { SOU, ETU, ADM, DES });

  const resp = await pageFor(roleUser('resp'));
  const dir = await pageFor(roleUser('dir'));
  const admin = await pageFor(roleUser('admin'));
  const multi = await pageFor(MULTI_USER);

  // ── GP2 — Responsable au RETOUR SOU (pièces non vérifiées) : RE-VÉRIFICATION (le symptôme) ──
  await gotoDossier(resp, SOU);
  let P = await panel(resp);
  rec('GP2 Resp@SOU voit la RE-VÉRIFICATION des pièces (verify)', P.pieceVerify, 'data-pact=verify');
  rec('GP2 Resp@SOU voit « Rejeter » + « complément »', has(P, 'Rejeter') && has(P, 'compl'), P.btns.join(' | '));
  rec('GP2 Resp@SOU : « Mettre en étude » MASQUÉ tant que pièces non vérifiées (garde métier GP6)',
    !has(P, 'Mettre en étude'), P.btns.join(' | '));
  await resp.screenshot({ path: `${SHOTS}/gp2-resp-sou-reverif.png` }).catch(() => {});

  // ── GP3 — Directeur@SOU : fait le travail Admin via hiérarchie (vérif pièces + reject/complément) ──
  await gotoDossier(dir, SOU);
  P = await panel(dir);
  rec('GP3 Dir@SOU voit la vérification des pièces (action Admin via hiérarchie)', P.pieceVerify, '');
  rec('GP3 Dir@SOU voit « Rejeter » + « complément »', has(P, 'Rejeter') && has(P, 'compl'), P.btns.join(' | '));

  // ── GP6 — vérif serveur des pièces → « Mettre en étude » APPARAÎT → clic → ETU (bouton qui marche) ──
  benchExec('stage_verify_pieces', { dossier: SOU });
  await gotoDossier(resp, SOU);
  P = await panel(resp);
  rec('GP6 Resp@SOU après vérif : « Mettre en étude » APPARAÎT', has(P, 'Mettre en étude'), P.btns.join(' | '));
  const clicked = await resp.evaluate(() => {
    const b = [...document.querySelectorAll('#act-body .act-btn')].find((x) => /Mettre en étude/.test(x.textContent));
    if (!b) return false; b.click(); return true;
  });
  await sleep(2500);
  rec('GP6 Resp clique « Mettre en étude » (bouton montré) → statut ETU', clicked && (await readStatus(resp)) === 'ETU', `clicked=${clicked}`);
  await resp.screenshot({ path: `${SHOTS}/gp6-resp-sou-to-etu.png` }).catch(() => {});

  // ── GP3 — Directeur@ETU : voit « Admettre » (action Responsable via hiérarchie) ──
  await gotoDossier(dir, ETU);
  P = await panel(dir);
  rec('GP3 Dir@ETU voit « Admettre » (action Resp via hiérarchie)', has(P, 'Admettre'), P.btns.join(' | '));

  // ── GP4 — ADM : Dir voit « Accepter » ; Resp ne le voit PAS ; multi(SM+Dir) le voit ──
  await gotoDossier(dir, ADM);
  P = await panel(dir);
  rec('GP4 Dir@ADM voit « Accepter l’admission »', has(P, 'Accepter'), P.btns.join(' | '));
  await dir.screenshot({ path: `${SHOTS}/gp4-dir-adm.png` }).catch(() => {});
  await gotoDossier(resp, ADM);
  P = await panel(resp);
  rec('GP4 Resp@ADM ne voit PAS « Accepter » (séparation des pouvoirs)', !has(P, 'Accepter'), P.btns.join(' | '));
  await gotoDossier(multi, ADM);
  P = await panel(multi);
  rec('GP4bis Multi(SM+Dir)@ADM voit « Accepter » (fin du collapse uxRole)', has(P, 'Accepter'), P.btns.join(' | '));

  // ── GP5 — état terminal (DES) : 0 bouton + message CLAIR, jamais « undefined » ──
  await gotoDossier(admin, DES);
  P = await panel(admin);
  rec('GP5 Admin@DES : 0 bouton d’action', P.btns.length === 0, P.btns.join(' | '));
  rec('GP5 Admin@DES : message CLAIR (pas « undefined »)',
    P.empty.length > 0 && !/undefined/i.test(P.empty) && !/undefined/i.test(P.title), `msg="${P.empty}" title="${P.title}"`);
  await admin.screenshot({ path: `${SHOTS}/gp5-admin-des.png` }).catch(() => {});
} finally {
  await closeAll();
  const purged = purgeFixtures();
  console.log('purge :', JSON.stringify(purged));
  console.log('baseline après :', statusCounts());
}

const pass = results.filter(Boolean).length;
console.log(`\n== BILAN : ${pass}/${results.length} PASS ==`);
process.exit(pass === results.length ? 0 : 1);
