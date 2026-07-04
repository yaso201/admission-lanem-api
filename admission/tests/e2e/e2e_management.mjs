// CONFORMITÉ-E2E Bloc 2 (B-3) — MANAGEMENT exhaustive sur le bundle LIVE (staff-rec).
// Chaque transition métier est prouvée par l'UI RÉELLE : précondition construite par chemin métier
// (recette_fixtures.build_ui), session staff mintée par rôle, puis clic sur le bouton d'action →
// (modale) → toast div[role="status"] → statut #dh-state .st-code. Aucun code applicatif touché.
//
// Prérequis env (.env) : PUPPETEER_PATH, FIXTURE_SSH_*, FIXTURE_SITE, FIXTURE_STAFF_URL,
//   FIXTURE_{ADMIN,RESP,DIR}_USER. Lancer :  node --env-file=.env admission/tests/e2e/e2e_management.mjs
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {
  mintSid, launchAuthed, roleUser, STAFF_URL,
  buildUi, benchExec, purgeFixtures, statusCounts, pick, armInterception,
} from './lib_session.mjs';

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const results = [];
function rec(name, pass, detail) {
  results.push({ name, pass });
  console.log((pass ? 'PASS ' : 'FAIL ') + name + (detail ? ' :: ' + detail : ''));
}

// justificatif temporaire (upload obligatoire de la confirmation de paiement offline)
const JUSTIF = path.join(os.tmpdir(), 'e2e-b3-justif.png');
fs.writeFileSync(JUSTIF, Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==', 'base64'));

// un navigateur authentifié par rôle (mint 1×/rôle, réutilisé) — cookie sid cross-subdomain
const sessions = {};
async function pageFor(role) {
  if (!sessions[role]) {
    const sid = mintSid(roleUser(role));
    const browser = await launchAuthed(sid);
    const page = await browser.newPage();
    await armInterception(page);
    sessions[role] = { browser, page };
  }
  return sessions[role].page;
}
async function closeAll() {
  for (const s of Object.values(sessions)) { try { await s.browser.close(); } catch (e) { /* ignore */ } }
}

const readStatus = (page) => page.evaluate(() => {
  const el = document.querySelector('#dh-state .st-code');
  return el ? el.textContent.trim() : 'none';
});

async function gotoDossier(page, id) {
  await page.goto(STAFF_URL + '/dossier?c=' + id, { waitUntil: 'domcontentloaded', timeout: 60000 });
  await page.waitForSelector('#act-body .act-btn, #act-body .act-lead', { timeout: 25000 }).catch(() => {});
  await sleep(700);
}

async function clickActBtn(page, labelSrc) {
  return page.evaluate((src) => {
    const re = new RegExp(src);
    const b = [...document.querySelectorAll('#act-body .act-btn')].find((x) => re.test(x.textContent));
    if (!b) return false; b.click(); return true;
  }, labelSrc);
}

async function submitModal(page, modal) {
  modal = modal || {};
  await page.waitForSelector('div[role="dialog"]', { timeout: 10000 });
  await sleep(150);
  if (modal.motif !== undefined) {
    const ta = await page.$('div[role="dialog"] textarea.em-input');       // motifModal
    if (ta) await ta.type(modal.motif);
    else { const inp = await page.$('div[role="dialog"] input.em-input'); if (inp) await inp.type(modal.motif); } // close_session = input text
  }
  if (modal.rang !== undefined) {
    const inp = await page.$('div[role="dialog"] input.em-input');
    if (inp) { await inp.click({ clickCount: 3 }); await inp.type(String(modal.rang)); }   // remplace la valeur pré-remplie
  }
  if (modal.mode !== undefined) await page.select('div[role="dialog"] select.em-input', modal.mode);
  if (modal.file !== undefined) { const f = await page.$('div[role="dialog"] input[type="file"]'); if (f) await f.uploadFile(modal.file); }
  await sleep(120);
  await page.evaluate(() => { const b = document.querySelector('div[role="dialog"] button[type="submit"]'); if (b) b.click(); });
}

async function waitToast(page, reSrc) {
  await page.waitForFunction((src) => {
    const rx = new RegExp(src, 'i');
    return [...document.querySelectorAll('div[role="status"]')].some((t) => rx.test(t.textContent));
  }, { timeout: 15000 }, reSrc).catch(() => {});
  return page.evaluate((src) => {
    const rx = new RegExp(src, 'i');
    return [...document.querySelectorAll('div[role="status"]')].map((t) => t.textContent).find((x) => rx.test(x)) || 'none';
  }, reSrc);
}

async function waitStatus(page, target, timeout = 15000) {
  await page.waitForFunction((t) => {
    const el = document.querySelector('#dh-state .st-code');
    return el && el.textContent.trim() === t;
  }, { timeout }, target).catch(() => {});
  return readStatus(page);
}

// ── Table des transitions (rôle × précondition × bouton × modale × toast × statut cible) ────────
// toastSrc : source d'une regex (motifModal → « Action effectuée. » ; sinon message d'action inline).
const T = [
  { n: 1,  role: 'admin', kind: 'SOU_VERIFIED',   name: 'start_review',         label: 'Mettre en étude',        from: 'SOU', target: 'ETU', toast: 'mis en étude' },
  { n: 2,  role: 'admin', kind: 'SOU',            name: 'reject_dossier',       label: 'Rejeter le dossier',     from: 'SOU', target: 'REJ', toast: 'Action effectuée', modal: { motif: 'Audit B-3 — rejet contrôle documentaire.' } },
  { n: 3,  role: 'admin', kind: 'REJ',            name: 'reopen_dossier',       label: 'Rouvrir le dossier',     from: 'REJ', target: 'SOU', toast: 'rouvert' },
  { n: 4,  role: 'admin', kind: 'SOU',            name: 'request_complement',   label: 'Demander un complément', from: 'SOU', target: 'INC', toast: 'Action effectuée', modal: { motif: 'Audit B-3 — complément requis.' } },
  { n: 5,  role: 'admin', kind: 'ETU',            name: 'withdraw',             label: 'Désister le dossier',    from: 'ETU', target: 'DES', toast: 'Action effectuée', modal: { motif: 'Audit B-3 — désistement candidat.' } },
  { n: 6,  role: 'admin', kind: 'SOP_PENDING',    name: 'confirm_frais1',       pay: true,                        from: 'SOP', target: 'SOU', toast: 'Paiement confirmé' },
  { n: 7,  role: 'admin', kind: 'ACO_DIPLOMA',    name: 'verify_bac_diploma',   label: 'Vérifier le diplôme',    from: 'ACO', target: 'ACO', toast: 'Diplôme vérifié' },
  { n: 8,  role: 'resp',  kind: 'ETU',            name: 'waitlist',             label: "Liste d.attente",        from: 'ETU', target: 'ATT', toast: "liste d.attente", modal: {} },
  { n: 9,  role: 'resp',  kind: 'ATT',            name: 'set_waitlist_rank',    label: 'rang d.attente',         from: 'ATT', target: 'ATT', toast: 'Rang mis à jour', modal: { rang: 3 } },
  { n: 10, role: 'resp',  kind: 'ETU',            name: 'mark_admissible',      label: 'Admettre',               from: 'ETU', target: 'ADM', toast: 'admis' },
  { n: 11, role: 'resp',  kind: 'ETU',            name: 'refuse_etu',           label: 'Refuser',                from: 'ETU', target: 'REF', toast: 'Action effectuée', modal: { motif: 'Audit B-3 — refus (Responsable).' } },
  { n: 12, role: 'resp',  kind: 'ETU_COND',       name: 'conditional_admission', label: 'Admission conditionnelle', from: 'ETU', target: 'ACO', toast: 'conditionnelle' },
  { n: 13, role: 'resp',  kind: 'ATT',            name: 'mark_admissible_att',  label: 'Admettre',               from: 'ATT', target: 'ADM', toast: 'admis' },
  { n: 14, role: 'dir',   kind: 'ADM',            name: 'accept_admission',     label: 'Accepter l.admission',   from: 'ADM', target: 'ACC', toast: 'acceptée', modal: {} },
  { n: 15, role: 'dir',   kind: 'ADM',            name: 'refuse_adm',           label: 'Refuser \\(ADM\\)',       from: 'ADM', target: 'REF', toast: 'Action effectuée', modal: { motif: 'Audit B-3 — refus admissible (Direction).' } },
  { n: 16, role: 'dir',   kind: 'ACO_VERIF',      name: 'lift_condition',       label: 'Lever la condition',     from: 'ACO', target: 'ACC', toast: 'Condition levée', modal: {} },
  { n: 17, role: 'dir',   kind: 'ACO_DIPLOMA',    name: 'refuse_condition',     label: 'Refuser \\(échec bac\\)', from: 'ACO', target: 'REF', toast: 'Action effectuée', modal: { motif: 'Audit B-3 — échec au bac.' } },
  { n: 18, role: 'admin', kind: 'ACC_F2_PENDING', name: 'confirm_frais2',       pay: true,                        from: 'ACC', target: 'ACC', toast: 'Paiement confirmé' },
  { n: 19, role: 'dir',   kind: 'ACC_F2_PAID',    name: 'enroll',               label: 'Inscrire',               from: 'ACC', target: 'INS', toast: 'inscrit', modal: {} },
];

async function driveTransition(t) {
  const label = `T${String(t.n).padStart(2, '0')} [${t.role}] ${t.name}`;
  let fx;
  try { fx = buildUi(t.kind); } catch (e) { rec(label, false, 'fixture ' + t.kind + ': ' + e.message.split('\n')[0]); return; }
  const page = await pageFor(t.role);
  await gotoDossier(page, fx.id);
  const before = await readStatus(page);
  if (t.from && before !== t.from) { rec(label, false, `précondition ${t.from} attendue, obtenu ${before} (fixture ${fx.id})`); return; }

  let clicked;
  if (t.pay) {
    clicked = await page.evaluate(() => { const b = document.querySelector('#pay-body [data-confirm]'); if (!b) return false; b.click(); return true; });
    if (!clicked) { rec(label, false, 'bouton Confirmer (#pay-body) introuvable'); return; }
    await submitModal(page, { mode: 'bank', file: JUSTIF });
  } else {
    clicked = await clickActBtn(page, t.label);
    if (!clicked) { rec(label, false, `bouton introuvable (/${t.label}/)`); return; }
    if (t.modal !== undefined) await submitModal(page, t.modal);
  }

  const toast = await waitToast(page, t.toast);
  const toastOk = new RegExp(t.toast, 'i').test(toast);
  let after = before, statusOk = true;
  if (t.target) { after = await waitStatus(page, t.target); statusOk = after === t.target; }
  let err = '';
  if (!(toastOk && statusOk)) {
    err = await page.evaluate(() => {
      const e = [...document.querySelectorAll('div[role="alert"]')].map((t2) => t2.textContent).filter(Boolean);
      return e.length ? e[e.length - 1] : '';
    });
  }
  rec(label, toastOk && statusOk, `toast="${(toast || 'none').slice(0, 44)}" statut ${before}→${after} (cible ${t.target})${err ? ' ERR="' + err.slice(0, 60) + '"' : ''}`);
}

async function driveCloseSession(n) {
  const label = `T${String(n).padStart(2, '0')} [dir] close_session`;
  let sess;
  try {
    const out = benchExec('open_disposable');
    sess = pick(out, 'DISPOSABLE');
    if (!sess) throw new Error('DISPOSABLE absent\n' + out.slice(-300));
  } catch (e) { rec(label, false, 'open_disposable: ' + e.message.split('\n')[0]); return; }

  const page = await pageFor('dir');
  await page.goto(STAFF_URL + '/gestion-sessions', { waitUntil: 'domcontentloaded', timeout: 60000 });
  await page.waitForSelector('#sessions-list', { timeout: 25000 }).catch(() => {});
  await sleep(1800);   // chargement de la liste + rendu du bouton Clôturer (rôle dir)
  const clicked = await page.evaluate((s) => {
    const b = document.querySelector(`[data-close-session="${s}"]`);
    if (!b) return false; b.click(); return true;
  }, sess);
  if (!clicked) { rec(label, false, `bouton Clôturer introuvable pour ${sess}`); return; }

  let toast = 'none';
  try {
    await page.waitForSelector('div[role="dialog"]', { timeout: 20000 });   // après le dry-run serveur
    await submitModal(page, { motif: 'Audit B-3 — clôture session jetable.' });
    toast = await waitToast(page, 'Session clôturée');
  } catch (e) {
    const err = await page.evaluate(() => {
      const a = [...document.querySelectorAll('div[role="alert"]')].map((t) => t.textContent).filter(Boolean);
      return a.length ? a[a.length - 1] : '';
    }).catch(() => '');
    rec(label, false, `modale/close KO (${e.message.split('\n')[0]}) ${err ? 'ERR="' + err.slice(0, 60) + '"' : ''}`);
    return;
  }
  // Preuve DÉFINITIVE (robuste) : la session passe à is_open=0 — indépendante du reflet du toast.
  await sleep(1200);
  let isOpen = '?';
  try { isOpen = pick(benchExec('session_state', { session: sess }), 'SESSION_OPEN'); } catch (e) { /* ignore */ }
  const ok = /Session clôturée/i.test(toast) && isOpen === '0';
  rec(label, ok, `session=${sess} is_open=${isOpen} toast="${(toast || 'none').slice(0, 50)}"`);
}

// ── Exécution ──────────────────────────────────────────────────────────────────────────────────
// B3_FILTER (optionnel) = liste de noms/numéros à exécuter (debug / re-run ciblé). Ex. B3_FILTER=1,close
const FILTER = (process.env.B3_FILTER || '').split(',').map((s) => s.trim()).filter(Boolean);
const keep = (t) => !FILTER.length || FILTER.includes(t.name) || FILTER.includes(String(t.n));
const wantClose = !FILTER.length || FILTER.includes('close') || FILTER.includes('20');

console.log('== CONFORMITÉ-E2E Bloc 2 (B-3) — management exhaustive sur bundle LIVE ==');
console.log('baseline avant :', statusCounts());
try {
  for (const t of T) if (keep(t)) await driveTransition(t);
  if (wantClose) await driveCloseSession(20);
} finally {
  await closeAll();
  const purged = purgeFixtures();
  console.log('purge :', JSON.stringify(purged));
  console.log('baseline après :', statusCounts());
}
const ok = results.filter((r) => r.pass).length;
console.log(`\n===== MANAGEMENT ${ok}/${results.length} — ${ok === results.length ? 'OK' : 'ÉCHEC'} =====`);
process.exit(ok === results.length ? 0 : 3);
