// Runs the pure view functions of bioscan/gardener/web/app.js under node (no DOM): `node --test 'tests/gardener/web/*.test.js'`.
// app.js hands them out through module.exports only when loaded by node; the asset cache is seeded with its own store().
'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');

const app = require(path.join(__dirname, '../../../bioscan/gardener/web/app.js'));
const { store, aiPill, groupsOf, groupState, groupBlocksHTML, encOn, encOrder } = app;

// ---------- groupsOf ----------
test('groupsOf: bursts and unassigned singles become groups ordered by capture time and numbered', () => {
  store([
    { id: 'g1', taken_at: '2026-05-01T10:00:03' }, { id: 'g2', taken_at: '2026-05-01T10:00:04' },
    { id: 'g3', taken_at: '2026-05-01T10:00:01' },
    { id: 'g4', taken_at: '2026-05-01T10:00:02' },
    { id: 'g5', taken_at: '2026-05-01T10:00:05' },
  ]);
  const enc = {
    encounters: [
      { task_id: 't1', bursts: [{ id: 'b1', rep: 'g2', asset_ids: ['g1', 'g2'], size: 2, run: 'r1' }] },
      { task_id: 't2', bursts: [{ id: null, rep: 'g4', asset_ids: ['g4'], size: 1 }] },
    ],
    unassigned: ['g5', 'g3'],
  };
  const gs = groupsOf(enc);
  assert.deepEqual(gs.map(g => [g.n, g.id]), [[1, 'one:g3'], [2, 'one:g4'], [3, 'b1'], [4, 'one:g5']]);
  const b1 = gs[2];
  assert.deepEqual(b1, { id: 'b1', burst: 'b1', rep: 'g2', asset_ids: ['g1', 'g2'], candidates: ['g2'], photos: 2, run: 'r1', task: 't1', n: 3 });
  assert.equal(gs[1].burst, null);          // a burst without an id is a single, named after its rep
  assert.equal(gs[1].run, null);            // no run given → null
  assert.equal(gs[1].task, 't2');
  assert.deepEqual(gs[0], { id: 'one:g3', burst: null, rep: 'g3', asset_ids: ['g3'], candidates: ['g3'], photos: 1, n: 1 });
});

test('groupsOf: no unassigned list, and assets not in the cache sort first', () => {
  store([{ id: 'h1', taken_at: '2026-05-01T09:00:00' }]);
  const gs = groupsOf({ encounters: [{ task_id: 't', bursts: [
    { id: 'bA', rep: 'h1', asset_ids: ['h1'], size: 1 },
    { id: 'bB', rep: 'h-missing', asset_ids: ['h-missing'], size: 1 },
  ] }] });
  assert.deepEqual(gs.map(g => [g.n, g.id]), [[1, 'bB'], [2, 'bA']]);
});

test('groupsOf: empty input gives no groups', () => {
  assert.deepEqual(groupsOf({ encounters: [] }), []);
});

// ---------- groupBlocksHTML ----------
const G = (n, run, photos, task) => ({ id: 'G' + n, n, run, photos, rep: 'R' + n, task: task || 'T' });
const tileOf = g => `<i data-t="${g.id}"></i>`;
const runs = html => html.match(/<section class="gr-run"[\s\S]*?<\/section>/g) || [];

test('groupBlocksHTML: consecutive groups with the same run are framed with group and photo counts', () => {
  const html = groupBlocksHTML([G(1, null, 1), G(2, 'r', 3), G(3, 'r', 2), G(4, 'r', 1), G(5, null, 4)], tileOf);
  const r = runs(html);
  assert.equal(r.length, 1);
  assert.match(r[0], /同一对象：第 2 到 4 组/);
  assert.match(r[0], /连着的 3 组 · 6 张/);
  assert.match(r[0], /data-ids="R2,R3,R4"/);
  assert.match(r[0], /data-task="T"/);
  for (const n of [2, 3, 4]) assert.ok(r[0].includes(`data-t="G${n}"`));
  for (const n of [1, 5]) assert.ok(!r[0].includes(`data-t="G${n}"`));
  // unframed tiles keep their order, each side of the run in its own plain grid
  assert.deepEqual(html.match(/data-t="G\d"/g), ['data-t="G1"', 'data-t="G2"', 'data-t="G3"', 'data-t="G4"', 'data-t="G5"']);
  assert.equal((html.match(/<div class="gr-grid">/g) || []).length, 3);
});

test('groupBlocksHTML: null runs are never framed', () => {
  const html = groupBlocksHTML([G(1, null, 1), G(2, null, 2), G(3, undefined, 1)], tileOf);
  assert.equal(runs(html).length, 0);
  assert.equal((html.match(/<div class="gr-grid">/g) || []).length, 1);
});

test('groupBlocksHTML: the same run id on non-adjacent groups is not framed', () => {
  assert.equal(runs(groupBlocksHTML([G(1, 'r', 1), G(2, null, 1), G(3, 'r', 1)], tileOf)).length, 0);
  assert.equal(runs(groupBlocksHTML([G(1, 'r', 1), G(2, 's', 1), G(3, 'r', 1)], tileOf)).length, 0);
});

test('groupBlocksHTML: a single group is never framed', () => {
  const html = groupBlocksHTML([G(1, 'r', 5)], tileOf);
  assert.equal(runs(html).length, 0);
  assert.equal(html, '<div class="gr-grid"><i data-t="G1"></i></div>');
});

test('groupBlocksHTML: two separate runs give two frames', () => {
  const r = runs(groupBlocksHTML([G(1, 'a', 1), G(2, 'a', 1), G(3, 'b', 2), G(4, 'b', 2)], tileOf));
  assert.equal(r.length, 2);
  assert.match(r[0], /连着的 2 组 · 2 张/);
  assert.match(r[1], /连着的 2 组 · 4 张/);
});

test('groupBlocksHTML: no groups gives no HTML', () => {
  assert.equal(groupBlocksHTML([], tileOf), '');
});

// ---------- groupState ----------
test('groupState: untouched, partial, done, all rejected, nothing in the cache', () => {
  store([
    { id: 's1', use: 'pending' }, { id: 's2' }, { id: 's3', use: 'edit' }, { id: 's4', use: 'record' }, { id: 's5', use: 'reject' },
  ]);
  const st = ids => groupState({ asset_ids: ids });
  assert.deepEqual(st(['s1', 's2']), { kept: 0, out: 0, done: false, any: false });
  assert.deepEqual(st(['s1', 's3', 's5']), { kept: 1, out: 1, done: false, any: true });
  assert.deepEqual(st(['s3', 's4', 's5']), { kept: 2, out: 1, done: true, any: true });
  assert.deepEqual(st(['s5']), { kept: 0, out: 1, done: true, any: true });
  assert.deepEqual(st(['s-missing']), { kept: 0, out: 0, done: false, any: false });
});

// ---------- aiPill ----------
test('aiPill: no pill without a suggestion, or with verdict none and no score', () => {
  assert.equal(aiPill({ analysis: 'done' }), '');
  assert.equal(aiPill({ analysis: 'done', ai: null }), '');
  assert.equal(aiPill({ analysis: 'done', ai: { verdict: 'none', score: null } }), '');
  assert.equal(aiPill({ analysis: 'done', ai: { verdict: 'none' } }), '');
});

test('aiPill: analysis states, offline pending first', () => {
  assert.equal(aiPill({ analysis: 'pending', online: false }), '<span class="pill p-off">原片离线 · 分析未完成</span>');
  assert.equal(aiPill({ analysis: 'pending', online: true }), '<span class="pill p-wait">等待分析</span>');
  assert.equal(aiPill({ analysis: 'pending' }), '<span class="pill p-wait">等待分析</span>');   // online unknown is not offline
  assert.equal(aiPill({ analysis: 'running' }), '<span class="pill p-run">分析中</span>');
  assert.equal(aiPill({ analysis: 'failed' }), '<span class="pill p-fail">分析失败</span>');
  assert.equal(aiPill({ analysis: 'unavailable' }), '<span class="pill p-fail">能力不可用</span>');
});

test('aiPill: verdict pills with the score', () => {
  assert.equal(aiPill({ analysis: 'done', ai: { verdict: 'pick', score: 0.873 } }), '<span class="pill p-ai-pick">AI 建议保留 · 0.87</span>');
  assert.equal(aiPill({ analysis: 'done', ai: { verdict: 'reject', score: 3.21 } }), '<span class="pill p-ai-reject">AI 建议舍弃 · 3.2</span>');
  assert.equal(aiPill({ analysis: 'done', ai: { verdict: 'none', score: 0.5 } }), '<span class="pill p-ai-maybe">AI 无明确建议 · 0.50</span>');
  assert.equal(aiPill({ analysis: 'done', ai: { verdict: 'pick' } }), '<span class="pill p-ai-pick">AI 建议保留 · —</span>');
});

// ---------- encOn / encOrder ----------
test('encOn: framing only when sorted by time and nothing is filtered', () => {
  const ws = o => Object.assign({ encQuery: { task: 't' }, enc: { by: new Map() }, order: 'time', filter: 'all' }, o);
  assert.equal(encOn(ws()), true);
  assert.equal(encOn(ws({ lf: { flag: 'all', stars: 0, ai: '' } })), true);
  assert.equal(encOn(null), false);
  assert.equal(encOn(ws({ encQuery: null })), false);
  assert.equal(encOn(ws({ enc: null })), false);
  assert.equal(encOn(ws({ order: 'score' })), false);
  assert.equal(encOn(ws({ filter: 'pick' })), false);
  assert.equal(encOn(ws({ lf: { flag: 'reject' } })), false);
  assert.equal(encOn(ws({ lf: { flag: 'all', stars: 3 } })), false);
  assert.equal(encOn(ws({ lf: { flag: 'all', ai: 'pick' } })), false);
});

test('encOrder: frames of a group sit together at the place of its first frame; others keep load order', () => {
  const gA = { id: 'A' }, gB = { id: 'B' };
  const by = new Map([['a1', gA], ['a2', gA], ['a3', gA], ['b1', gB], ['b2', gB]]);
  const all = ['a1', 'x', 'b1', 'a2', 'y', 'b2', 'a3'];
  assert.deepEqual(encOrder({ all, enc: { by } }), ['a1', 'a2', 'a3', 'x', 'b1', 'b2', 'y']);
  assert.deepEqual(all, ['a1', 'x', 'b1', 'a2', 'y', 'b2', 'a3']);   // input untouched
});
