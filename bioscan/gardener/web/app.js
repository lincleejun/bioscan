/* Gardener · 视图、路由与交互。业务数据全部经 api.js 走服务 HTTP API（§6）；进度靠每 2 s 轮询。 */
'use strict';
(function () {
  // ---------- 基础工具 ----------
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));
  const ESC = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
  const esc = v => String(v == null ? '' : v).replace(/[&<>"']/g, c => ESC[c]);
  const pct = (a, b) => (b ? Math.round((a / b) * 100) : 0);
  const isField = el => !!el && (el.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName));
  const fmtTime = s => (s ? String(s).slice(0, 16).replace('T', ' ') : '—');
  const fmtBytes = n => { if (n == null) return '—'; const u = ['B', 'KB', 'MB', 'GB', 'TB']; let i = 0; while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; } return `${n.toFixed(i ? 1 : 0)} ${u[i]}`; };
  const fmtScore = s => (s == null ? '—' : s <= 1 ? s.toFixed(2) : s.toFixed(1));
  const errMsg = e => (e && e.message) || '操作失败';
  const main = $('#main');

  const S = { tasks: [], caps: [], presets: [], settings: {}, health: null, cols: [], assets: new Map(), details: new Map(), down: false };
  const ui = { view: null, params: {}, ws: null, colFilter: 'all', colKind: '', lib: {}, undo: [], draft: null, zoom: false, token: 0, colKey: '' };

  const IC = {
    tasks: '<path d="M4 6h16M4 12h16M4 18h10"/>',
    lib: '<rect x="3" y="5" width="18" height="14" rx="2"/><circle cx="9" cy="10" r="1.8"/><path d="m21 16-5-5-9 8"/>',
    play: '<path d="M8 5.5v13l10-6.5z" fill="currentColor" stroke="none"/>',
    pause: '<path d="M8.5 5.5v13M15.5 5.5v13"/>',
    stop: '<rect x="6.5" y="6.5" width="11" height="11" rx="1.5"/>',
    plus: '<path d="M12 5v14M5 12h14"/>',
    flag: '<path d="M6 21V4M6 4h11l-2.2 4L17 12H6"/>',
    x: '<path d="M6.5 6.5l11 11M17.5 6.5l-11 11"/>',
    star: '<path d="m12 3.5 2.6 5.3 5.9.9-4.3 4.1 1 5.8L12 16.9l-5.2 2.7 1-5.8-4.3-4.1 5.9-.9z"/>',
    left: '<path d="m14.5 18-6-6 6-6"/>',
    right: '<path d="m9.5 18 6-6-6-6"/>',
    undo: '<path d="M9 14 4 9l5-5"/><path d="M4 9h10.5a5.5 5.5 0 0 1 0 11H11"/>',
    compare: '<rect x="3" y="4.5" width="8" height="15" rx="1.5"/><rect x="13" y="4.5" width="8" height="15" rx="1.5"/>',
    expand: '<path d="M14.5 3.5h6v6M9.5 20.5h-6v-6M20.5 3.5l-6.5 6.5M3.5 20.5l6.5-6.5"/>',
    zoom: '<circle cx="11" cy="11" r="6.5"/><path d="m20 20-4.4-4.4M11 8v6M8 11h6"/>',
    edit: '<path d="M4 20h4L19 9l-4-4L4 16z"/><path d="m13.5 6.5 4 4"/>',
    check: '<path d="m5 12.5 4.5 4.5L19 7.5"/>',
    warn: '<path d="M12 4 2.8 19.5h18.4z"/><path d="M12 10v4.2M12 17h.01"/>',
    download: '<path d="M12 4v11M7 10.5l5 5 5-5M5 20h14"/>',
    folder: '<path d="M3.5 7.5a2 2 0 0 1 2-2h4l2 2h7a2 2 0 0 1 2 2v7.5a2 2 0 0 1-2 2h-13a2 2 0 0 1-2-2z"/>',
    retry: '<path d="M19.5 12a7.5 7.5 0 1 1-2.2-5.3"/><path d="M19.5 4.5v4.5H15"/>',
    keyboard: '<rect x="2.5" y="6" width="19" height="12" rx="2"/><path d="M6 10h.01M10 10h.01M14 10h.01M18 10h.01M7 14h10"/>',
    trash: '<path d="M4.5 7h15M9.5 7V4.5h5V7M6.5 7l1 13h9l1-13"/>',
    grid: '<rect x="4" y="4" width="6" height="6" rx="1"/><rect x="14" y="4" width="6" height="6" rx="1"/><rect x="4" y="14" width="6" height="6" rx="1"/><rect x="14" y="14" width="6" height="6" rx="1"/>',
  };
  const ic = (n, cls) => `<svg class="ic${cls ? ' ' + cls : ''}" viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">${IC[n]}</svg>`;

  // ---------- 词表 ----------
  const SCAN = {
    queued: ['排队中', 'queued'], running: ['扫描中', 'running'], paused: ['已暂停', 'paused'],
    completed: ['扫描完成', 'done'], partial: ['完成 · 部分失败或不可用', 'partial'], failed: ['扫描失败', 'partial'],
    stopped: ['已停止 · 保留结果', 'stopped'], cancelled: ['已取消', 'stopped'], interrupted: ['已中断 · 待恢复', 'paused'],
  };
  const scanOf = t => SCAN[(t.scan || {}).status] || ['未开始', 'queued'];
  const CAPSTATE = {
    not_enabled: ['未启用', 'off'], not_applicable: ['不适用', 'off'], pending: ['待运行', 'wait'], running: ['运行中', 'run'], done: ['完成', 'ok'],
    failed: ['失败', 'fail'], unrecognized: ['未识别', 'muted'], unavailable: ['不可用', 'fail'],
  };
  const VERDICT = { pick: '建议保留', reject: '建议舍弃', none: '无明确建议' };
  const GROUP = { all: '全部素材', person: '人物', species: '物种', animal: '动物', scene: '场景', other: '其他', place: '地点', day: '日期（旧）', event: '事件（旧）' };
  const GROUP_ORDER = ['all', 'person', 'species', 'animal', 'scene', 'other'];
  const VERSION_WHY = { same_capture: '同一拍摄的副本', export: '导出图（无拍摄信息，按文件编号与画面确认）' };
  const MODES = { quiet: ['安静', '占用最低，适合边用电脑边扫描'], balanced: ['均衡', '默认'], full: ['全速', '尽量占满资源'] };
  const REVIEW = { open: '审阅中', completed: '已完成', zeroed: '本组不留' };
  const LEVEL_ZH = { species: '种', genus: '属', family: '科', unconfirmed: '不确定', not_animal: '不是动物' };
  const KIND_ZH = { bird: '鸟类', mammal: '哺乳动物', other_animal: '其他动物' };
  const ID_NOTE = '物种按动物主体记录：人工确认优先，模型重跑只追加新的建议，不覆盖人的结论。';
  const PREVIEW_NOTE = '低分辨率预览（含 RAW 内嵌预览）只用于浏览，不代表完整 RAW 解码或对焦验证。';
  const XML_NOTE = 'Gardener XML 只供 Gardener 使用：它不是 XMP，Lightroom 不能直接读取。导出不会移动、改名或删除任何原片。';

  const statusChip = t => { const [l, c] = scanOf(t); return `<span class="chip st-${c}">${t.scan && t.scan.status === 'running' ? '<i class="pulse"></i>' : ''}${esc(l)}</span>`; };
  const pill = (text, tone) => `<span class="pill p-${tone}">${esc(text)}</span>`;
  const bar = (p, cls) => `<div class="bar ${cls || ''}" role="presentation"><i style="width:${Math.max(0, Math.min(100, p))}%"></i></div>`;
  const taskById = id => S.tasks.find(t => t.id === id);
  const presetName = id => { const p = S.presets.find(x => x.id === id); return p ? p.name : (id || '自定义'); };
  const capName = id => { const c = S.caps.find(x => x.id === id); return c ? c.name : id; };
  function reviewLabel(t) {
    const r = t.review || {};
    if (!r.total) return '尚无可审素材';
    if (!r.judged) return '未审';
    return r.judged >= r.total ? '已全部判断' : '已审部分';
  }

  // ---------- 素材缓存 ----------
  function store(items) { items.forEach(a => { const old = S.assets.get(a.id); S.assets.set(a.id, Object.assign(old || {}, a)); }); }
  const A = id => S.assets.get(id);

  function photo(a, kind, cls) {
    cls = cls || '';
    if (a.preview === 'ready') return `<div class="ph ${cls}"><img src="${esc(mediaUrl(a.id, kind))}" alt="${esc(a.name)}" loading="lazy" decoding="async"><span class="ph-note ph-fallback">预览无法载入${a.online ? '' : '（原片离线）'}</span></div>`;
    if (a.preview === 'failed') return `<div class="ph ph-wait ${cls}"><span class="ph-note">预览生成失败${a.online ? '' : ' · 原片离线'}</span></div>`;
    return `<div class="ph ph-wait ${cls}"><span class="ph-note">${a.online ? '等待预览' : '原片离线 · 无预览'}</span></div>`;
  }
  function aiPill(a) {
    switch (a.analysis) {
      case 'pending': return pill('等待分析', 'wait');
      case 'running': return pill('分析中', 'run');
      case 'failed': return pill('分析失败', 'fail');
      case 'unavailable': return pill('能力不可用', 'fail');
      default: break;
    }
    if (!a.ai) return pill('无 AI 建议', 'off');
    return pill(`AI ${VERDICT[a.ai.verdict] || a.ai.verdict} · ${fmtScore(a.ai.score)}`, 'ai-' + (a.ai.verdict === 'none' ? 'maybe' : a.ai.verdict));
  }
  function humanBadge(a) {
    let h = '';
    if (a.flag === 'pick') h += `<span class="hb hb-pick">${ic('flag')}Pick</span>`;
    else if (a.flag === 'reject') h += `<span class="hb hb-reject">${ic('x')}Reject</span>`;
    if (a.stars) h += `<span class="hb hb-star" aria-label="${a.stars} 星">${'★'.repeat(a.stars)}</span>`;
    return h;
  }
  const tileSub = a => [a.species && (a.species.common || a.species.taxon), a.content_label_zh, fmtTime(a.taken_at)].filter(Boolean).join(' · ');
  const tileTags = a => (a.media === 'video' ? `<span class="tb">${ic('play')}${a.duration_s ? Math.round(a.duration_s) + ' s' : '视频'}</span>` : '') + (a.online ? '' : '<span class="tb tb-off">离线</span>');
  const sigMedia = a => `${a.preview}|${a.online}`;
  const sigAi = a => [a.analysis, a.ai && a.ai.verdict, a.ai && a.ai.score, a.content_label_zh, a.species && a.species.taxon].join('|');
  const sigHuman = a => `${a.flag}|${a.stars}`;
  const tileLabel = a => `${a.name}${a.flag === 'pick' ? '，Pick' : a.flag === 'reject' ? '，Reject' : ''}${a.stars ? `，${a.stars} 星` : ''}${a.online ? '' : '，原片离线'}`;
  function burstBtn(ws, a) {
    if (!ws || !ws.collapse || !a.burst || ws.burstRep.get(a.burst.id) !== a.id) return '';
    const open = ws.expanded.has(a.burst.id);
    return `<button type="button" class="burst-btn" data-action="burst" data-id="${esc(a.burst.id)}" aria-expanded="${open}">连拍 ${a.burst.size}${open ? ' · 收起' : ' · 展开'}</button>`;
  }
  function tile(a, ws) {
    return `<div class="tile-wrap" role="listitem" data-wrap="${esc(a.id)}"><button type="button" class="tile" data-asset="${esc(a.id)}" data-sm="${esc(sigMedia(a))}" data-sa="${esc(sigAi(a))}" data-sh="${esc(sigHuman(a))}" aria-label="${esc(tileLabel(a))}"><span class="tile-media">${photo(a, 'thumb')}<span class="tile-ai">${aiPill(a)}</span><span class="tile-human">${humanBadge(a)}</span><span class="tile-tags">${tileTags(a)}</span></span><span class="tile-cap"><span class="tile-name">${esc(a.name)}</span><span class="tile-file">${esc(tileSub(a))}</span></span></button><button type="button" class="tile-check" data-action="toggle-sel" data-id="${esc(a.id)}" aria-pressed="false" aria-label="多选：${esc(a.name)}">${ic('check')}</button>${burstBtn(ws, a)}</div>`;
  }
  function flagSeg(a) {
    const b = (v, label, key, icon) => `<button type="button" class="seg-btn f-${v || 'none'}" data-action="flag" data-id="${esc(a.id)}" data-v="${v || ''}" data-key="f-${v || 'none'}-${esc(a.id)}" aria-pressed="${(a.flag || '') === (v || '')}">${icon ? ic(icon) : ''}${label}<kbd>${key}</kbd></button>`;
    return `<div class="seg flagseg" role="group" aria-label="人工 Flag">${b('pick', 'Pick', 'P', 'flag')}${b(null, '未标记', 'U')}${b('reject', 'Reject', 'X', 'x')}</div>`;
  }
  function starsHTML(a) {
    let h = '';
    for (let i = 1; i <= 5; i++) h += `<button type="button" class="star-btn${i <= a.stars ? ' on' : ''}" data-action="star" data-id="${esc(a.id)}" data-v="${i}" data-key="s${i}-${esc(a.id)}" aria-label="${i} 星" aria-pressed="${a.stars === i}">${ic('star')}</button>`;
    return `<div class="stars" role="group" aria-label="星级">${h}</div>`;
  }

  // ---------- 局部刷新 ----------
  function swap(el, html) {
    if (el._h === html) return;
    const act = document.activeElement;
    const key = act && el.contains(act) ? act.dataset.key : null;
    el.innerHTML = html;
    el._h = html;
    if (key) { const n = el.querySelector(`[data-key="${CSS.escape(key)}"]`); if (n) n.focus({ preventScroll: true }); }
  }
  const BIND = {
    'nav-tasks': () => (S.tasks.some(t => t.scan && t.scan.status === 'running') ? '<span class="nav-dot" title="有任务正在扫描"></span>' : ''),
    svc: () => svcHTML(),
    queue: () => queueHTML(),
    'task-scan': id => { const t = taskById(id); return t ? taskScanHTML(t) : ''; },
    'task-review': id => { const t = taskById(id); return t ? taskReviewHTML(t) : ''; },
    'task-actions': id => { const t = taskById(id); return t ? taskActionsHTML(t) : ''; },
    'task-head': id => { const t = taskById(id); return t ? taskHeadHTML(t) : ''; },
    'task-phases': id => { const t = taskById(id); return t ? phasesHTML(t) : ''; },
    'ws-filters': () => wsFilterChips(),
    'ws-batch': () => batchHTML(),
    'ws-count': () => wsCountHTML(),
    'col-head': () => (ui.col ? colHeadHTML(ui.col) : ''),
    'col-actions': () => (ui.col ? colActionsHTML(ui.col) : ''),
    undo: () => `<button type="button" class="btn sm ghost" data-action="undo" data-key="undo" ${ui.undo.length ? '' : 'disabled'}>${ic('undo')}撤销</button>`,
  };
  const bindEl = (name, id, tag) => `<${tag || 'div'} data-bind="${name}"${id ? ` data-id="${esc(id)}"` : ''}>${BIND[name](id)}</${tag || 'div'}>`;
  function patchBinds(only) {
    $$(only ? `[data-bind="${only}"]` : '[data-bind]').forEach(el => { const fn = BIND[el.dataset.bind]; if (fn) swap(el, fn(el.dataset.id)); });
  }
  function primeBinds(root) { $$('[data-bind]', root).forEach(el => { const fn = BIND[el.dataset.bind]; if (fn) el._h = fn(el.dataset.id); }); }
  function setHTML(el, html) { el.innerHTML = html; primeBinds(el); }
  const safeForStructure = () => !$('dialog[open]') && !isField(document.activeElement);

  function patchTiles(ids) {
    const want = ids ? new Set(ids) : null;
    $$('.tile[data-asset]').forEach(el => {
      const id = el.dataset.asset;
      if (want && !want.has(id)) return;
      const a = A(id);
      if (!a) return;
      const sm = sigMedia(a), sa = sigAi(a), sh = sigHuman(a);
      if (sm !== el.dataset.sm) { el.dataset.sm = sm; $('.ph', el).outerHTML = photo(a, 'thumb'); $('.tile-tags', el).innerHTML = tileTags(a); }
      if (sa !== el.dataset.sa) { el.dataset.sa = sa; $('.tile-ai', el).innerHTML = aiPill(a); $('.tile-file', el).textContent = tileSub(a); }
      if (sh !== el.dataset.sh) { el.dataset.sh = sh; $('.tile-human', el).innerHTML = humanBadge(a); el.setAttribute('aria-label', tileLabel(a)); }
    });
  }

  // ---------- 侧栏与队列 ----------
  function svcHTML() {
    if (S.down) return `<p class="svc-down">${ic('warn')}无法连接 Gardener 服务，正在重试…</p>`;
    const h = S.health;
    if (!h) return '<p class="hint">正在连接服务…</p>';
    const w = h.worker || {};
    const t = w.task_id && taskById(w.task_id);
    return `<p class="svc-line"><b>服务</b> ${esc(h.version)} · ${esc(h.device || '')}</p><p class="hint">${w.state === 'running' ? `后台扫描：${esc(t ? t.title : w.task_id)}` : '后台空闲'}</p>`;
  }
  function queueHTML() {
    const ts = S.tasks.filter(t => !t.archived);
    const run = ts.find(t => t.scan && t.scan.status === 'running');
    const n = s => ts.filter(t => t.scan && t.scan.status === s).length;
    return `<span class="q-label">扫描队列</span><span class="q-run">${run ? `<i class="pulse"></i>正在扫描：<b>${esc(run.title)}</b>` : '当前没有运行中的扫描'}</span><span class="q-meta">排队 ${n('queued')} · 已暂停 ${n('paused')} · 一次只运行一个重型扫描，Review 不占扫描名额</span>`;
  }

  // ---------- 任务 ----------
  function phaseText(t) {
    const s = t.scan || {};
    const fail = s.failed ? ` · ${s.failed} 项失败` : '';
    switch (s.status) {
      case 'queued': return s.discovered ? `排队中 · 已有 ${s.previewed} 项预览` : '等待前一个扫描结束';
      case 'running':
        if (s.phase === 'discover') return `发现素材 ${s.discovered}`;
        if (s.phase === 'preview') return `生成预览 ${s.previewed}/${s.discovered}${fail}`;
        if (s.phase === 'group') return '整理分组';
        return `AI 分析 ${s.analyzed}/${s.total_analysis}${fail}`;
      case 'paused': return `暂停于 ${pct(s.progress, 1)}% · 可继续 Review`;
      case 'completed': return `${s.discovered} 项素材 · 分析完成`;
      case 'partial': return `${s.analyzed} 项完成${fail} · 有失败、不可用能力或离线来源，详见运行历史`;
      case 'failed': return '扫描失败，详见运行历史';
      case 'stopped': return `已停止 · 保留 ${s.previewed} 项预览与 ${s.analyzed} 项分析`;
      case 'cancelled': return '已取消';
      case 'interrupted': return '服务重启时中断，将自动从未完成项继续';
      default: return '';
    }
  }
  function taskScanHTML(t) {
    const p = pct((t.scan || {}).progress || 0, 1);
    return `<div class="tc-label">扫描执行</div><div class="tc-line">${statusChip(t)}<span class="tc-pct">${p}%</span></div>${bar(p, 'bar-scan st-' + scanOf(t)[1])}<div class="tc-note">${esc(phaseText(t))}</div>`;
  }
  function taskReviewHTML(t) {
    const r = t.review || {};
    if (!r.total) return `<div class="tc-label">人工 Review</div><div class="tc-line"><span class="tc-muted">尚无可审素材</span></div>${bar(0, 'bar-review')}<div class="tc-note">扫描出预览后即可浏览标记</div>`;
    return `<div class="tc-label">人工 Review · ${esc(reviewLabel(t))}</div><div class="tc-line"><span class="tc-num"><b>${r.judged}</b> / ${r.total} 张已判断</span><span class="tc-pct">${pct(r.judged, r.total)}%</span></div>${bar(pct(r.judged, r.total), 'bar-review')}<div class="tc-note">${r.collections_done}/${r.collections_total} 个集合完成</div>`;
  }
  function taskActionsHTML(t) {
    const b = (act, label, icon, kind) => `<button type="button" class="btn sm ${kind || ''}" data-action="${act}" data-id="${esc(t.id)}" data-key="${act}-${esc(t.id)}">${ic(icon)}${label}</button>`;
    const st = (t.scan || {}).status;
    if (st === 'running' || st === 'queued') return b('task-pause', '暂停', 'pause') + b('task-stop', st === 'queued' ? '取消' : '停止', 'stop', 'ghost');
    if (st === 'paused') return b('task-resume', '继续', 'play', 'accent') + b('task-stop', '停止', 'stop', 'ghost');
    return `<a class="btn sm ghost" href="#/task/${esc(t.id)}/collections" data-key="cols-${esc(t.id)}">继续 Review${ic('right')}</a>`;
  }
  function mosaicHTML(t) {
    const all = S.cols.find(c => c.task_id === t.id && c.group_kind === 'all');
    const ids = all ? all.cover_ids.slice(0, 3) : [];
    let h = ids.map(id => `<div class="ph"><img src="${esc(mediaUrl(id, 'thumb'))}" alt="" loading="lazy"></div>`).join('');
    for (let i = ids.length; i < 3; i++) h += '<div class="ph ph-wait"></div>';
    return h;
  }
  function vTasks() {
    const showArch = ui.params.archived;
    const tasks = S.tasks.filter(t => !!t.archived === !!showArch).slice().reverse();
    return `<div class="page">
      <header class="page-head">
        <div><h1 tabindex="-1">任务${showArch ? ' · 已归档' : ''}</h1><p class="lede">每个任务是一次独立扫描：扫描结束即完成，人工 Review 进度单独计算，可随时回来继续。</p></div>
        <div class="head-actions"><a class="btn ghost" href="${showArch ? '#/tasks' : '#/tasks/archived'}">${showArch ? '返回任务' : '已归档'}</a><a class="btn primary" href="#/new">${ic('plus')}新建扫描任务</a></div>
      </header>
      <div class="queue-strip" data-bind="queue">${queueHTML()}</div>
      <div class="task-list" role="list">${tasks.map(t => `
        <article class="task-row" role="listitem">
          <div class="task-mosaic">${mosaicHTML(t)}</div>
          <div class="task-info">
            <h2 class="task-title"><a class="row-link" href="#/task/${esc(t.id)}/preview">${esc(t.title)}</a></h2>
            <p class="task-sub">${esc(t.subtitle || '—')}</p>
            <p class="task-meta">${t.sources.length} 个来源${t.sources.some(s => !s.online) ? ' <span class="note warn">有来源离线</span>' : ''} · ${esc(presetName(t.preset))} · ${esc((MODES[t.run_mode] || [t.run_mode])[0])} · 创建于 ${esc(fmtTime(t.created_at))} · ${(t.counts || {}).assets || 0} 个素材（${(t.counts || {}).files || 0} 个文件，${fmtBytes((t.counts || {}).bytes)}${(t.counts || {}).versions ? `；其中 ${t.counts.versions} 个是同一拍摄的其他版本，不单独审阅` : ''}）</p>
          </div>
          <div class="task-cell" data-bind="task-scan" data-id="${esc(t.id)}">${taskScanHTML(t)}</div>
          <div class="task-cell" data-bind="task-review" data-id="${esc(t.id)}">${taskReviewHTML(t)}</div>
          <div class="task-actions" data-bind="task-actions" data-id="${esc(t.id)}">${taskActionsHTML(t)}</div>
        </article>`).join('') || `<div class="empty">${showArch ? '没有已归档的任务。' : '还没有任务。点「新建扫描任务」选择来源目录。'}</div>`}
      </div>
    </div>`;
  }

  // ---------- 新建任务 ----------
  const ABS = /^(\/|[A-Za-z]:[\\/]|\\\\)/;
  function expandCaps(on) {
    const set = new Set(['meta', ...on]);
    let again = true;
    while (again) { again = false; S.caps.forEach(c => { if (set.has(c.id)) (c.requires || []).forEach(r => { if (!set.has(r)) { set.add(r); again = true; } }); }); }
    return S.caps.map(c => c.id).filter(id => set.has(id));
  }
  function defaultDraft() {
    const pid = S.settings.default_preset || (S.presets[0] && S.presets[0].id);
    const p = S.presets.find(x => x.id === pid);
    return { sources: [], title: '', subtitle: '', description: '', preset: pid, caps: expandCaps(p ? p.capabilities : []), run_mode: S.settings.run_mode || 'balanced' };
  }
  function pathChipsHTML(d) {
    if (!d.sources.length) return '<li class="path-empty">尚未添加目录</li>';
    return d.sources.map((p, i) => `<li class="path-chip">${ic('folder')}<span class="mono">${esc(p)}</span><button type="button" class="icon-btn xs" data-action="rm-path" data-i="${i}" aria-label="移除 ${esc(p)}">${ic('x')}</button></li>`).join('');
  }
  function capToggles(selected, name) {
    return `<div class="set-list">${S.caps.map(c => `<label class="switch-row"><span class="sw-text"><b>${esc(c.name)}</b><small>${esc(c.description)}${c.requires && c.requires.length ? ` · 需要：${esc(c.requires.map(capName).join('、'))}` : ''}${c.state === 'unavailable' ? ` · <span class="txt-fail">不可用：${esc(c.reason || '原因未知')}</span>` : ''}</small></span><input type="checkbox" role="switch" class="switch" name="${name}" value="${esc(c.id)}" ${selected.includes(c.id) ? 'checked' : ''} ${c.id === 'meta' ? 'disabled' : ''}></label>`).join('')}</div>`;
  }
  function vCreate() {
    const d = ui.draft || (ui.draft = defaultDraft());
    const field = (id, label, req, input, hint) => `<div class="field"><label for="f-${id}">${label}${req ? ' <span class="req">必填</span>' : ''}</label>${input}${hint ? `<p class="hint">${hint}</p>` : ''}<p class="field-err" id="err-${id}" aria-live="polite"></p></div>`;
    return `<div class="page"><div class="narrow">
      <a class="back" href="#/tasks">${ic('left')}任务</a>
      <header class="page-head"><div><h1 tabindex="-1">新建扫描任务</h1><p class="lede">选择来源目录并补充背景。扫描只读取原片并建立记录，不会复制、移动或改名。</p></div></header>
      <form id="create-form" class="form" novalidate>
        <section class="form-sec">
          <h2>来源目录</h2>
          <p class="hint">填写服务所在电脑上的绝对路径，可添加多张卡或多个目录。</p>
          <ul class="path-chips" id="path-chips" aria-label="已添加的来源目录">${pathChipsHTML(d)}</ul>
          <div class="path-add">
            <label class="sr-only" for="path-input">目录路径</label>
            <input id="path-input" class="input mono" placeholder="/Volumes/SD_CARD/DCIM" autocomplete="off" spellcheck="false" aria-describedby="err-sources">
            <button type="button" class="btn" data-action="add-path">${ic('plus')}添加目录</button>
          </div>
          <p class="field-err" id="err-sources" aria-live="polite"></p>
        </section>
        <section class="form-sec">
          <h2>背景信息</h2>
          ${field('title', '任务标题', true, `<input id="f-title" name="title" class="input" maxlength="80" value="${esc(d.title)}" aria-describedby="err-title">`)}
          ${field('subtitle', '副标题', false, `<input id="f-subtitle" name="subtitle" class="input" maxlength="120" value="${esc(d.subtitle)}" placeholder="地点 · 时间">`)}
          ${field('description', '描述', false, `<textarea id="f-description" name="description" class="input" rows="3" maxlength="2000">${esc(d.description)}</textarea>`, '描述只作为背景，不会被当作每张照片的精确地点或已确认物种。')}
        </section>
        <section class="form-sec">
          <h2>扫描预设与能力</h2>
          <div class="field"><label for="f-preset">预设</label><select id="f-preset" name="preset" class="input">${S.presets.map(p => `<option value="${esc(p.id)}" ${p.id === d.preset ? 'selected' : ''}>${esc(p.name)}${p.builtin ? '' : '（自定义）'}</option>`).join('')}</select></div>
          <div id="cap-toggles">${capToggles(d.caps, 'cap')}</div>
          <p class="hint">开启某能力会同时开启它依赖的能力。标为「不可用」的能力仍可选择，扫描时会如实记录为不可用，不会给出伪造结果。任务保存启动时的配置快照。</p>
        </section>
        <section class="form-sec">
          <h2>运行模式</h2>
          <div class="seg-radio" role="radiogroup" aria-label="运行模式">${Object.entries(MODES).map(([k, r]) => `<label><input type="radio" name="run_mode" value="${k}" ${d.run_mode === k ? 'checked' : ''}><span>${r[0]}<small>${r[1]}</small></span></label>`).join('')}</div>
          <p class="hint">运行模式只控制资源占用，不会关闭能力或降低分析质量。</p>
        </section>
        <p class="field-err" id="err-submit" aria-live="polite"></p>
        <footer class="form-foot"><a class="btn ghost" href="#/tasks">取消</a><button type="submit" class="btn primary">${ic('check')}创建并加入扫描队列</button></footer>
      </form>
    </div></div>`;
  }
  function showErr(field, msg) { const el = $('#err-' + field); if (el) el.textContent = msg || ''; }
  function addPath() {
    const inp = $('#path-input');
    const v = inp.value.trim();
    let err = '';
    if (!v) err = '请输入目录路径';
    else if (!ABS.test(v)) err = '请输入绝对路径，例如 /Volumes/SD_CARD/DCIM';
    else if (ui.draft.sources.includes(v)) err = '该路径已添加';
    showErr('sources', err);
    if (err) { inp.focus(); return false; }
    ui.draft.sources.push(v);
    inp.value = '';
    $('#path-chips').innerHTML = pathChipsHTML(ui.draft);
    inp.focus();
    return true;
  }
  async function submitCreate() {
    const inp = $('#path-input');
    if (inp.value.trim() && !addPath()) return;
    const d = ui.draft;
    showErr('sources', d.sources.length ? '' : '至少添加一个来源目录');
    showErr('title', d.title.trim() ? '' : '请填写任务标题');
    if (!d.sources.length) { inp.focus(); return; }
    if (!d.title.trim()) { $('#f-title').focus(); return; }
    try {
      const t = await api('POST', '/tasks', { title: d.title.trim(), subtitle: d.subtitle.trim(), description: d.description.trim(), sources: d.sources, preset: d.preset, capabilities: d.caps, run_mode: d.run_mode });
      ui.draft = null;
      toast(`已创建「${t.title}」并加入扫描队列`);
      await refreshTasks();
      location.hash = `#/task/${t.id}/preview`;
    } catch (e) { showErr('submit', errMsg(e)); }
  }

  // ---------- 任务详情 ----------
  function taskHeadHTML(t) {
    const p = pct((t.scan || {}).progress || 0, 1), r = t.review || {};
    return `<div class="th-status">${statusChip(t)}<span class="th-phase">${esc(phaseText(t))}</span></div>${bar(p, 'bar-scan st-' + scanOf(t)[1])}<div class="th-review">人工 ${esc(reviewLabel(t))} · 已判断 ${r.judged || 0}/${r.total || 0} 张 · 与扫描进度分开计算</div>`;
  }
  function phasesHTML(t) {
    const s = t.scan || {};
    const run = s.status === 'running';
    const cell = (label, v, n, extra, active) => `<div class="phase${run && active ? ' is-active' : ''}${n && v >= n ? ' is-done' : ''}"><div class="phase-top"><span>${label}</span><b>${v}<small> / ${n}</small></b></div>${bar(pct(v, n))}${extra || ''}</div>`;
    return cell('发现素材', s.discovered || 0, s.discovered || 0, '', s.phase === 'discover') + cell('生成预览', s.previewed || 0, s.discovered || 0, '', s.phase === 'preview') +
      cell('AI 分析', s.analyzed || 0, s.total_analysis || 0, s.failed ? `<span class="phase-fail">${ic('warn')}${s.failed} 项失败</span>` : '', s.phase === 'analyze') +
      `<div class="phase-note">${run ? '分析进行中也可以浏览和标记；新素材追加在末尾，不会打乱当前选择。' : '分析完成不等于 Review 完成。'}</div>`;
  }
  function taskMoreHTML(t) {
    const b = (act, label, icon) => `<button type="button" class="btn sm ghost" data-action="${act}" data-id="${esc(t.id)}">${icon ? ic(icon) : ''}${label}</button>`;
    return b('task-append', '追加来源', 'plus') + b('task-rescan', '重新分析…', 'retry') + b('task-retry', '重试失败项') + b('task-verify', '核对来源') + b(t.archived ? 'task-unarchive' : 'task-archive', t.archived ? '取消归档' : '归档');
  }
  async function vTask(p) {
    const t = await api('GET', `/tasks/${encodeURIComponent(p.id)}`);
    S.tasks = S.tasks.filter(x => x.id !== t.id).concat([t]);
    const tab = ['collections', 'runs'].includes(p.tab) ? p.tab : 'preview';
    const head = `<header class="task-head">
        <nav class="crumbs" aria-label="位置"><a href="#/tasks">任务</a><span aria-hidden="true">/</span><span>${esc(t.title)}</span></nav>
        <div class="th-row">
          <div class="th-title"><h1 tabindex="-1">${esc(t.title)}</h1><p class="th-sub">${esc(t.subtitle)}${t.subtitle ? ' · ' : ''}${esc(presetName(t.preset))} · ${esc((MODES[t.run_mode] || [t.run_mode])[0])} · ${esc(t.capabilities.map(capName).join('、'))}</p>
            <div class="th-sources">${t.sources.map(s => `<span class="src-chip${s.online ? '' : ' off'}" title="${s.online ? '在线' : '来源离线：记录与已有预览保留'}">${ic('folder')}<span class="mono">${esc(s.path)}</span>${s.online ? '' : '<b>离线</b>'}</span>`).join('')}</div>
            <div class="th-more">${taskMoreHTML(t)}</div></div>
          <div class="th-progress" data-bind="task-head" data-id="${esc(t.id)}">${taskHeadHTML(t)}</div>
          <div class="th-actions" data-bind="task-actions" data-id="${esc(t.id)}">${taskActionsHTML(t)}</div>
        </div>
        <nav class="tabs" aria-label="任务视图">
          <a href="#/task/${esc(t.id)}/preview" ${tab === 'preview' ? 'aria-current="page"' : ''}>Preview <span class="count">${(t.scan || {}).discovered || 0}</span></a>
          <a href="#/task/${esc(t.id)}/collections" ${tab === 'collections' ? 'aria-current="page"' : ''}>Collections <span class="count">${(t.review || {}).collections_total || 0}</span></a>
          <a href="#/task/${esc(t.id)}/runs" ${tab === 'runs' ? 'aria-current="page"' : ''}>运行历史 <span class="count">${(t.runs || []).length}</span></a>
          <a href="#/species/task/${esc(t.id)}">按物种看本次拍摄 →</a>
          <a href="#/import/${esc(t.id)}">完成与导入 →</a>
        </nav>
      </header>`;
    if (tab === 'collections') {
      ui.colScope = t.id;
      await refreshCols();
      return `<div class="wpage">${head}<div class="page-scroll">${colToolbarHTML(t.id)}<div id="col-area">${colAreaHTML()}</div></div></div>`;
    }
    if (tab === 'runs') return `<div class="wpage">${head}<div class="page-scroll"><div class="runs">${(t.runs || []).slice().reverse().map(runHTML).join('') || '<div class="empty">还没有运行。</div>'}</div></div></div>`;
    makeWs('preview', { task: t.id }, 'discovered', { taskId: t.id, start: prefs.get('last.' + t.id, null) });
    return `<div class="wpage">${head}<div class="phases" data-bind="task-phases" data-id="${esc(t.id)}">${phasesHTML(t)}</div>${wsHTML()}</div>`;
  }
  const KIND = { scan: '首次扫描', append: '追加来源', rescan: '重新分析', retry: '重试失败项' };
  const RUNST = { queued: '排队中', running: '运行中', paused: '已暂停', stopped: '已停止', completed: '完成', partial: '部分完成', failed: '失败', cancelled: '已取消', interrupted: '已中断' };
  function runHTML(r) {
    const s = r.summary;
    const cfg = r.config || {};
    const scope = r.scope || {};
    let body = '';
    if (s) {
      const fails = s.failed || [];
      body = `<dl class="meta run-meta">
        <dt>发现</dt><dd>${s.discovered} 个素材 · ${s.files} 个文件 · ${fmtBytes(s.bytes)}${s.by_media ? ` · ${esc(Object.entries(s.by_media).map(([k, v]) => `${k === 'video' ? '视频' : '照片'} ${v}`).join('，'))}` : ''}</dd>
        ${s.versions && (s.versions.same_capture || s.versions.export) ? `<dt>同一拍摄</dt><dd>${s.versions.same_capture} 个跨目录副本 · ${s.versions.export} 张导出图，归到原片下</dd>` : ''}
        <dt>预览</dt><dd>${s.previewed} 完成${s.preview_failed ? ` · <span class="txt-fail">${s.preview_failed} 失败</span>` : ''}</dd>
        <dt>分析</dt><dd>${esc(Object.entries(s.analyzed || {}).map(([k, v]) => `${capName(k)} ${v}`).join('，') || '—')}</dd>
        ${Object.keys(s.unavailable || {}).length ? `<dt>不可用</dt><dd>${esc(Object.entries(s.unavailable).map(([k, v]) => `${capName(k)}：${v}`).join('；'))}</dd>` : ''}
        ${(s.not_run || []).length ? `<dt>未运行</dt><dd>${esc(s.not_run.map(capName).join('、'))}</dd>` : ''}
        ${(s.offline_sources || []).length ? `<dt>离线来源</dt><dd class="mono">${esc(s.offline_sources.join('，'))}</dd>` : ''}
        ${s.missing ? `<dt>文件缺失</dt><dd>${esc(s.missing)} 个（来源在线但找不到文件）</dd>` : ''}
        ${s.unsupported ? `<dt>不支持</dt><dd>${esc(s.unsupported)} 个文件被跳过</dd>` : ''}
        <dt>用时</dt><dd>${s.elapsed_s != null ? esc(Math.round(s.elapsed_s)) + ' s' : '—'}</dd></dl>
        ${fails.length ? `<details class="run-fails"><summary>${fails.length} 个失败项</summary><ul>${fails.slice(0, 50).map(f => `<li><span class="mono">${esc(f.asset_id)}</span> · ${esc(capName(f.capability))} · ${esc(f.error)}</li>`).join('')}</ul></details>` : ''}`;
    } else body = `<p class="hint">${r.status === 'running' || r.status === 'queued' || r.status === 'paused' ? '运行未结束，汇总在结束时生成。' : '没有汇总。'}</p>`;
    return `<article class="run card"><h3>#${r.seq} ${esc(KIND[r.kind] || r.kind)} <span class="sub">${esc(RUNST[r.status] || r.status)} · 排队 ${esc(fmtTime(r.queued_at))}${r.finished_at ? ' · 结束 ' + esc(fmtTime(r.finished_at)) : ''}</span></h3>
      <p class="hint">能力：${esc((cfg.capabilities || []).map(capName).join('、') || '—')}${scope.asset_ids && scope.asset_ids.length ? ` · 限定 ${scope.asset_ids.length} 个素材` : ''}${scope.capabilities ? ` · 限定能力 ${esc(scope.capabilities.map(capName).join('、'))}` : ''}${(scope.sources || []).length ? ` · 来源 ${esc(scope.sources.join('，'))}` : ''}</p>
      ${r.error ? `<p class="ai-fail">${ic('warn')}${esc(r.error)}</p>` : ''}${body}</article>`;
  }

  // ---------- 工作区：网格 + 检视器 ----------
  const WSF = {
    preview: [['all', '全部'], ['waiting', '等待中'], ['analyzed', '已分析'], ['failed', '失败/不可用'], ['unjudged', '未判断']],
    review: [['all', '全部'], ['unjudged', '未判断'], ['ai-pick', 'AI 建议保留'], ['ai-reject', 'AI 建议舍弃'], ['pick', '已 Pick'], ['reject', '已 Reject']],
  };
  function passF(a, f) {
    switch (f) {
      case 'waiting': return a.preview === 'pending' || a.analysis === 'pending' || a.analysis === 'running';
      case 'analyzed': return a.analysis === 'done' || a.analysis === 'partial';
      case 'failed': return a.preview === 'failed' || a.analysis === 'failed' || a.analysis === 'unavailable';
      case 'unjudged': return !a.judged;
      case 'ai-pick': return !!a.ai && a.ai.verdict === 'pick';
      case 'ai-reject': return !!a.ai && a.ai.verdict === 'reject';
      case 'pick': return a.flag === 'pick';
      case 'reject': return a.flag === 'reject';
      default: return true;
    }
  }
  function makeWs(kind, query, order, extra) {
    const ws = Object.assign({ kind, query, order, filter: 'all', all: [], allSet: new Set(), ids: [], sel: new Set(), current: null, anchor: null, offset: 0, total: 0, loadedAll: false, cursor: '0', collapse: prefs.get('burst', '1') === '1', expanded: new Set(), burstRep: new Map(), loading: false }, extra);
    ui.ws = ws;
    return ws;
  }
  function visible(ws, a) {
    if (!passF(a, ws.filter)) return false;
    if (!ws.collapse || !a.burst || ws.expanded.has(a.burst.id)) return true;
    return ws.burstRep.get(a.burst.id) === a.id;
  }
  function addLoaded(ws, items) {
    const fresh = [];
    items.forEach(a => {
      if (a.updated_at > ws.cursor) ws.cursor = a.updated_at;
      if (ws.allSet.has(a.id)) return;
      ws.allSet.add(a.id); ws.all.push(a.id); fresh.push(a.id);
      if (a.burst) { const r = ws.burstRep.get(a.burst.id); if (!r || (A(r).burst || {}).rank > a.burst.rank && !ws.ids.includes(r)) ws.burstRep.set(a.burst.id, a.id); }
    });
    const vis = fresh.filter(id => visible(ws, A(id)));
    if (!vis.length) return;
    ws.ids.push(...vis);
    if (ui.ws === ws) {
      const grid = $('#grid');
      if (grid) { const e = $('.grid-empty', grid); if (e) e.remove(); grid.insertAdjacentHTML('beforeend', vis.map(id => tile(A(id), ws)).join('')); }
      if (!ws.current) setCurrent(ws.start && ws.allSet.has(ws.start) ? ws.start : ws.ids[0]);
      else applySel();
    }
  }
  async function loadMore(ws) {
    if (ws.loading || ws.loadedAll) return;
    ws.loading = true;
    try {
      const page = await api('GET', '/assets', null, Object.assign({}, ws.query, { order: ws.order, limit: 200, offset: ws.offset }));
      store(page.items);
      ws.offset += page.items.length;
      ws.total = page.total;
      ws.loadedAll = ws.offset >= page.total || !page.items.length;
      addLoaded(ws, page.items);
      if (ui.ws === ws) { patchBinds('ws-filters'); patchBinds('ws-count'); if (ws.start && !ws.allSet.has(ws.start) && !ws.loadedAll && ws.current === ws.ids[0]) { /* 起始项在后面的页里：保持第一项 */ } }
    } catch (e) { toast(errMsg(e)); } finally { ws.loading = false; }
    if (ui.ws === ws && !ws.loadedAll) { const s = $('#grid-sentinel'); if (s && s.getBoundingClientRect().top < window.innerHeight + 400) loadMore(ws); }
  }
  async function pollWs(ws) {
    const items = await apiAll(Object.assign({}, ws.query, { order: ws.order, changed_after: ws.cursor }));
    if (ui.ws !== ws || !items.length) return;
    store(items);
    const known = items.filter(a => ws.allSet.has(a.id)).map(a => a.id);
    known.forEach(id => S.details.delete(id));
    // 已载入的就地更新；全部载入后才追加新素材（否则留给分页按原顺序载入）
    if (ws.loadedAll) addLoaded(ws, items); else items.forEach(a => { if (a.updated_at > ws.cursor) ws.cursor = a.updated_at; });
    patchTiles(known);
    if (known.includes(ws.current)) renderInspector();
    patchBinds('ws-filters'); patchBinds('ws-count');
    renderLightbox();
  }
  function wsFilterChips() {
    const ws = ui.ws;
    if (!ws || !WSF[ws.kind]) return '';
    const all = ws.all.map(A);
    return `<div class="chips" role="group" aria-label="组内筛选">${WSF[ws.kind].map(([k, l]) => `<button type="button" class="fchip" data-action="ws-filter" data-f="${k}" data-key="wf-${k}" aria-pressed="${ws.filter === k}">${l}<span>${all.filter(a => passF(a, k)).length}</span></button>`).join('')}</div>`;
  }
  function wsCountHTML() {
    const ws = ui.ws;
    if (!ws) return '';
    return `<span class="hint">已载入 ${ws.all.length}${ws.loadedAll ? '' : ` / ${ws.total}`} 张${ws.ids.length !== ws.all.length ? ` · 显示 ${ws.ids.length} 张` : ''}</span>`;
  }
  function batchHTML() {
    const ws = ui.ws;
    if (!ws) return '';
    const n = ws.sel.size;
    const manual = ws.kind === 'review' && ui.col && ui.col.kind === 'manual';
    if (n < 2) return '<span class="hint">单击选择 · ⌘/Ctrl 或 Shift 多选 · 双击或空格放大 · P/X/U 标记，0–5 星级</span>';
    return `<span class="batch-n">已选 <b>${n}</b> 张 · 快捷键作用于全部已选</span>
      <button type="button" class="btn sm pick" data-action="batch" data-v="pick" data-key="b-pick">${ic('flag')}Pick</button>
      <button type="button" class="btn sm reject" data-action="batch" data-v="reject" data-key="b-reject">${ic('x')}Reject</button>
      <button type="button" class="btn sm ghost" data-action="batch" data-v="" data-key="b-none">清除 Flag</button>
      <label class="batch-stars"><span class="sr-only">批量星级</span><select class="input sm" data-action-change="batch-stars" aria-label="批量星级"><option value="">星级…</option>${[0, 1, 2, 3, 4, 5].map(i => `<option value="${i}">${i ? i + ' 星' : '清除星级'}</option>`).join('')}</select></label>
      <button type="button" class="btn sm ghost" data-action="compare" data-key="b-cmp" ${n === 2 ? '' : 'disabled title="选择 2 张时可并排比较"'}>${ic('compare')}比较</button>
      <button type="button" class="btn sm ghost" data-action="add-to-col" data-key="b-add">加入集合…</button>
      ${manual ? '<button type="button" class="btn sm ghost" data-action="split-col" data-key="b-split">拆分为新集合…</button><button type="button" class="btn sm ghost" data-action="remove-from-col" data-key="b-rm">移出本组</button>' : ''}
      <button type="button" class="btn sm ghost" data-action="clear-sel" data-key="b-clear">取消选择</button>`;
  }
  function wsHTML() {
    const ws = ui.ws;
    return `<div class="ws grid-${esc(prefs.get('grid', 'm'))}">
      <section class="ws-pane" aria-label="素材网格">
        <div class="ws-toolbar">${bindEl('ws-filters')}<div class="ws-tools">${bindEl('ws-count', '', 'span')}<button type="button" class="btn sm ghost" data-action="burst-collapse" aria-pressed="${ws.collapse}">连拍${ws.collapse ? '已折叠' : '已展开'}</button><button type="button" class="btn sm ghost" data-action="grid-size" aria-label="切换缩略图大小">${ic('grid')}</button>${bindEl('undo', '', 'span')}<button type="button" class="btn sm ghost" data-action="shortcuts">${ic('keyboard')}快捷键</button></div></div>
        <div class="grid-scroll" id="grid-scroll"><div class="grid" id="grid" role="list"><div class="grid-empty">${ws.kind === 'preview' ? '扫描开始后，素材会在这里逐步出现。' : '正在载入…'}</div></div><div id="grid-sentinel" aria-hidden="true"></div></div>
        <div class="batch-bar" data-bind="ws-batch">${batchHTML()}</div>
      </section>
      <aside class="inspector" id="inspector" aria-label="素材详情"></aside>
    </div>`;
  }
  function renderGrid() {
    const ws = ui.ws, grid = $('#grid');
    if (!grid) return;
    grid.innerHTML = ws.ids.length ? ws.ids.map(id => tile(A(id), ws)).join('') : `<div class="grid-empty">${ws.all.length ? '没有符合条件的素材。' : ws.kind === 'preview' ? '扫描开始后，素材会在这里逐步出现。' : '没有素材。'}</div>`;
    applySel();
  }
  function recompute() {
    const ws = ui.ws;
    ws.ids = ws.all.filter(id => visible(ws, A(id)));
    ws.sel = new Set(Array.from(ws.sel).filter(id => ws.ids.includes(id)));
    if (!ws.ids.includes(ws.current)) ws.current = ws.ids[0] || null;
    if (ws.current) ws.sel.add(ws.current);
    renderGrid(); patchBinds(); renderInspector();
  }
  function applySel() {
    const ws = ui.ws;
    if (!ws) return;
    $$('.tile-wrap').forEach(w => {
      const id = w.dataset.wrap, sel = ws.sel.has(id);
      w.classList.toggle('is-current', id === ws.current);
      w.classList.toggle('is-selected', sel);
      const c = $('.tile-check', w);
      if (c) c.setAttribute('aria-pressed', String(sel));
    });
    patchBinds('ws-batch');
  }

  // 检视器：大图层只在换图、预览状态、放大模式变化时重建
  function subjectBox(d) {
    const s = d && d.caps && d.caps.subject && d.caps.subject.data;
    const boxes = (s && s.boxes) || [];
    return boxes.slice().sort((x, y) => (y.score || 0) - (x.score || 0))[0] || null;
  }
  function stageEl(a, d) {
    if (a.media === 'video' && a.online) {
      return `<div class="ph ph-contain vid"><video controls preload="metadata" src="${esc(mediaUrl(a.id, 'original'))}"${a.preview === 'ready' ? ` poster="${esc(mediaUrl(a.id, 'large'))}"` : ''}></video><div class="vid-fail" hidden><p>${ic('warn')}无法在浏览器播放</p><button type="button" class="btn sm dark" data-action="reveal" data-id="${esc(a.id)}">在 Finder 中显示</button></div></div>`;
    }
    const box = ui.zoom && subjectBox(d);
    if (box && a.preview === 'ready') return `<div class="ph ph-contain zoom-wrap"><div class="zoom" data-zoom="${esc(a.id)}" role="img" aria-label="主体放大：${esc(a.name)}"></div></div>`;
    return photo(a, 'large', 'ph-contain');
  }
  function applyZoom(el, a, d) {
    const z = el && $('.zoom', el);
    if (!z) return;
    const [x0, y0, x1, y1] = subjectBox(d).xyxy;
    const m = 0.08, bx0 = Math.max(0, x0 - m), by0 = Math.max(0, y0 - m), bx1 = Math.min(1, x1 + m), by1 = Math.min(1, y1 + m);
    const bw = bx1 - bx0, bh = by1 - by0;
    z.style.backgroundImage = `url("${mediaUrl(a.id, 'large')}")`;
    z.style.aspectRatio = String(((a.width || 3) * bw) / ((a.height || 2) * bh));
    z.style.backgroundSize = `${100 / bw}% ${100 / bh}%`;
    z.style.backgroundPosition = `${bw < 1 ? (bx0 / (1 - bw)) * 100 : 0}% ${bh < 1 ? (by0 / (1 - bh)) * 100 : 0}%`;
  }
  function canvasBarHTML(a, d) {
    const ws = ui.ws, i = ws.ids.indexOf(ws.current);
    const box = subjectBox(d);
    return `<button type="button" class="icon-btn dark" data-action="prev" data-key="prev" aria-label="上一张" ${i <= 0 ? 'disabled' : ''}>${ic('left')}</button><span class="canvas-idx">${i + 1} / ${ws.ids.length}</span><button type="button" class="icon-btn dark" data-action="next" data-key="next" aria-label="下一张" ${i >= ws.ids.length - 1 ? 'disabled' : ''}>${ic('right')}</button>
      ${a.media === 'photo' ? `<button type="button" class="btn sm dark" data-action="zoom" data-key="zoom" aria-pressed="${ui.zoom}" ${box ? '' : 'disabled title="没有主体框（未启用主体检测、待运行或未识别）"'}>${ic('zoom')}主体放大</button>` : ''}
      <button type="button" class="btn sm dark" data-action="lightbox" data-key="lb">${ic('expand')}放大</button>`;
  }
  function capDataText(id, c) {
    const d = c.data;
    if (!d || c.state !== 'done') return '';
    if (id === 'content') return d.label_zh || d.label || '';
    if (id === 'species') return `${d.common || ''} ${d.taxon || ''}${d.p != null ? ` · ${Math.round(d.p * 100)}%` : ''}${d.uncertain ? ' · 不确定' : ''}`;
    if (id === 'quality') return (d.reasons_zh || []).join('、');
    if (id === 'aesthetic') return d.score != null ? `分数 ${fmtScore(d.score)}` : '';
    if (id === 'subject') return `${d.class || ''}${d.boxes ? ` · ${d.boxes.length} 个框` : ''}`;
    if (id === 'people') return d.count ? `${d.count} 张人脸` : '';
    if (id === 'meta') return '';
    return '';
  }
  function aiBodyHTML(a, d) {
    const ae = d && d.caps && d.caps.aesthetic;
    if (!a.ai) {
      const st = ae ? ae.state : null;
      if (st === 'not_enabled') return '<p class="hint">本任务未启用「美学与选择建议」，因此没有 AI Pick/Reject 建议。</p>';
      if (st === 'failed') return `<p class="ai-fail">${ic('warn')}分析失败：${esc(ae.error || '原因未知')}</p><button type="button" class="btn sm" data-action="retry-asset" data-id="${esc(a.id)}">${ic('retry')}重试此项</button>`;
      if (st === 'unavailable') return `<p class="ai-fail">${ic('warn')}能力不可用：${esc(ae.error || '原因未知')}</p>`;
      if (st === 'running') return '<p class="hint">正在分析…</p>';
      return '<p class="hint">尚无 AI 建议。可以先做人工判断，AI 结果到达后不会覆盖人工选择。</p>';
    }
    const v = a.ai.verdict;
    const hist = (d && d.ai_history) || [];
    return `<div class="ai-rec ai-${v === 'none' ? 'maybe' : esc(v)}"><span class="ai-label">AI ${esc(VERDICT[v] || v)}</span><span class="ai-score">${esc(fmtScore(a.ai.score))}</span></div>
      ${(a.ai.reasons || []).length ? `<ul class="reasons">${a.ai.reasons.map(r => `<li>${esc(r)}</li>`).join('')}</ul>` : '<p class="hint">无可靠理由（系统不编造理由）。</p>'}
      ${hist.length > 1 ? `<details><summary class="hint">历史建议 ${hist.length} 条</summary><ul class="hist">${hist.map(h => `<li>${esc(fmtTime(h.at))} · ${esc(VERDICT[h.verdict] || h.verdict)} · ${esc(fmtScore(h.score))} · <span class="mono">${esc(h.version || '')}</span></li>`).join('')}</ul></details>` : ''}`;
  }
  function fileState(f, d) {
    if (f.online) return pill('在线', 'ok');
    return d.meta && d.meta.missing ? pill('文件缺失', 'fail') : pill('来源离线', 'wait');
  }
  // 服务替人清空过的判断（内容变化、本组不留清 Pick）：说明原因并可整批恢复
  function restoreHTML(hist, meta) {
    const seen = new Set(), out = [];
    hist.slice().reverse().forEach(h => {
      if (!h.batch_id || seen.has(h.batch_id)) return;
      const changed = h.actor === 'system' && h.reason === 'content_changed';
      const zeroed = typeof h.reason === 'string' && h.reason.startsWith('zero:');
      if (!changed && !zeroed) return;
      seen.add(h.batch_id);
      out.push(`<div class="note ${changed ? 'warn' : 'info'} block restore-row"><span>${ic('warn')}${changed ? '内容已变化，旧判断已清空' : '「本组不留」清除了 Pick'}（${esc(fmtTime(h.at))}）</span><button type="button" class="btn sm" data-action="restore-batch" data-id="${esc(h.batch_id)}" data-label="${changed ? '恢复旧判断' : '恢复 Pick'}">${changed ? '恢复旧判断' : '恢复 Pick'}</button></div>`);
    });
    if (!out.length && meta.content_changed_at) out.push(`<p class="note warn block">${ic('warn')}内容已变化（${esc(fmtTime(meta.content_changed_at))}），旧判断见历史。</p>`);
    return out.join('');
  }
  function panelHTML(a, d) {
    const meta = (d && d.meta) || {};
    const cols = (d && d.collections) || [];
    const hist = (d && d.mark_history) || [];
    const FIELD = { flag: 'Flag', stars: '星级' };
    const capsHTML = d ? S.caps.map(c => { const x = d.caps[c.id] || { state: 'not_enabled' }; const [l, tone] = CAPSTATE[x.state] || [x.state, 'muted']; const extra = capDataText(c.id, x); return `<li><span>${esc(c.name)}${extra ? `<small>${esc(extra)}</small>` : ''}${x.error ? `<small class="txt-fail">${esc(x.error)}</small>` : ''}</span>${pill(l, tone)}</li>`; }).join('') : '<li class="hint">载入中…</li>';
    return `<div class="insp-title"><h2>${esc(a.name)}</h2><p class="mono">${esc(fmtTime(a.taken_at))}${a.camera ? ' · ' + esc(a.camera) : ''}</p></div>
      ${a.online ? '' : `<p class="note warn block">${ic('warn')}${meta.missing ? '文件缺失：来源在线但找不到原文件。' : '原片离线：只显示已有记录与预览，原片当前不可用。'}</p>`}
      ${restoreHTML(hist, meta)}
      <section class="card card-human"><h3>人工判断<span class="sub">随素材全局共享</span></h3>
        ${flagSeg(a)}
        <div class="star-row">${starsHTML(a)}<span class="hint">1–5 星，0 清除</span></div>
        <label class="field-inline"><span class="hint">理由（可选，随下一次标记保存）</span><input id="mark-reason" class="input sm" maxlength="200" autocomplete="off"></label>
        <label class="check-row"><input type="checkbox" data-pref="advance" ${prefs.get('advance', '0') === '1' ? 'checked' : ''}> 键盘标记后自动前进到下一张</label>
        ${hist.length ? `<details><summary class="hint">标记历史 ${hist.length} 条</summary><ul class="hist">${hist.slice().reverse().map(h => `<li>${esc(fmtTime(h.at))} · ${esc(FIELD[h.field] || h.field)} ${esc(h.old == null ? '无' : h.old)} → ${esc(h.new == null ? '无' : h.new)} · ${esc(h.actor)}${h.reason ? ' · ' + esc(h.reason === 'content_changed' ? '内容变化，系统清空' : h.reason) : ''}</li>`).join('')}</ul></details>` : '<p class="hint">尚无人工判断。浏览经过不计为已审。</p>'}
        ${cols.length ? `<div class="in-cols"><span class="hint">出现在 ${cols.length} 个集合，标记一致：</span>${cols.map(c => `<a class="col-ref" href="#/review/${esc(c.id)}">${esc(c.name)}</a>`).join('')}</div>` : ''}
      </section>
      ${subjectsHTML(a, d)}
      ${facesHTML(a, d)}
      <section class="card card-ai"><h3>AI 建议<span class="sub">机器结果 · 与人工判断分开保存</span></h3>${aiBodyHTML(a, d)}</section>
      <section class="card"><h3>能力状态</h3><ul class="cap-list">${capsHTML}</ul>${a.analysis === 'failed' ? `<button type="button" class="btn sm" data-action="retry-asset" data-id="${esc(a.id)}">${ic('retry')}重试此项</button>` : ''}</section>
      <section class="card"><h3>元数据与文件</h3><dl class="meta">
        <dt>拍摄时间</dt><dd>${esc(a.taken_at || '—')}</dd><dt>设备</dt><dd>${esc(a.camera || '—')}${meta.lens ? ' · ' + esc(meta.lens) : ''}</dd>
        <dt>尺寸</dt><dd>${a.width ? `${a.width} × ${a.height}` : '—'}${a.duration_s ? ` · ${esc(Math.round(a.duration_s))} s` : ''}</dd>
        <dt>位置</dt><dd>${d && d.lat != null ? `${esc(d.lat.toFixed(5))}, ${esc(d.lon.toFixed(5))}` : '无 GPS'}</dd>
        ${(d && d.tags && d.tags.length) ? `<dt>标签</dt><dd>${d.tags.map(t => `${esc(t.name)}<small>${t.source === 'user' ? '人工' : 'AI'}</small>`).join('，')}</dd>` : ''}
        </dl>
        <ul class="files">${d ? d.files.map(f => `<li><span class="mono">${esc(f.path)}</span><span>${esc(f.role)} · ${fmtBytes(f.size)} ${fileState(f, d)}</span></li>`).join('') : ''}</ul>
        ${a.online ? `<button type="button" class="btn sm ghost" data-action="reveal" data-id="${esc(a.id)}">${ic('folder')}在 Finder 中显示</button>` : ''}
        <p class="hint">${PREVIEW_NOTE}</p></section>
      ${versionsHTML(d)}`;
  }
  // 照片里的人脸：点“命名”给这张脸起名；名字记住后，所有任务里认出是此人的照片都归入同名分组
  function facesHTML(a, d) {
    const fs = d && d.caps && d.caps.people && d.caps.people.data && d.caps.people.data.faces;
    if (!fs || !fs.length) return '';
    const crop = f => {
      const [x0, y0, x1, y1] = f.box, w = Math.max(x1 - x0, 0.01), h = Math.max(y1 - y0, 0.01);
      return `background-image:url('${mediaUrl(a.id, 'large')}');background-size:${100 / w}% ${100 / h}%;background-position:${w < 1 ? (x0 / (1 - w)) * 100 : 0}% ${h < 1 ? (y0 / (1 - h)) * 100 : 0}%`;
    };
    const btn = (act, i, label, name) => `<button type="button" class="btn sm ghost" data-action="${act}" data-id="${esc(a.id)}" data-face="${i}"${name ? ` data-name="${esc(name)}"` : ''}>${label}</button>`;
    return `<section class="card"><h3>人物<span class="sub">命名后，所有任务里认出是此人的照片都归入同名分组</span></h3><ul class="faces">${fs.map((f, i) => `<li><span class="face" style="${esc(crop(f))}" role="img" aria-label="人脸 ${i + 1}"></span>
      <span>${f.person ? `<b>${esc(f.person.name)}</b><small class="hint">${f.person.confirmed ? '已确认' : '按已命名的脸认出'}</small>` : '<small class="hint">未命名</small>'}</span>
      <span class="face-acts">${f.person && !f.person.confirmed ? btn('name-face', i, '是此人', f.person.name) : ''}${btn('name-face', i, f.person ? '改名' : '命名')}${f.person && f.person.confirmed ? btn('unname-face', i, '取消') : ''}</span></li>`).join('')}</ul></section>`;
  }
  // 照片里的动物（主体）：每只一行，人工确认优先；确认或改名可同时作用于同一连拍（契约修订 4）
  function idText(x) {
    if (!x) return '未识别';
    if (x.level === 'species') return [x.zh, x.common, x.taxon].filter(Boolean).join(' · ');
    return x.taxon ? `${x.taxon}（${LEVEL_ZH[x.level]}）` : LEVEL_ZH[x.level] || x.level;
  }
  function subjectsHTML(a, d) {
    const ss = d && d.subjects;
    if (!ss || !ss.length) return '';
    const crop = s => {
      const [x0, y0, x1, y1] = s.xyxy, w = Math.max(x1 - x0, 0.01), h = Math.max(y1 - y0, 0.01);
      return `background-image:url('${mediaUrl(a.id, 'large')}');background-size:${100 / w}% ${100 / h}%;background-position:${w < 1 ? (x0 / (1 - w)) * 100 : 0}% ${h < 1 ? (y0 / (1 - h)) * 100 : 0}%`;
    };
    const btn = (act, s, label) => `<button type="button" class="btn sm ghost" data-action="${act}" data-id="${esc(a.id)}" data-subject="${esc(s.id)}">${label}</button>`;
    const burst = a.burst && a.burst.size > 1;
    return `<section class="card"><h3>物种<span class="sub">${esc(ID_NOTE)}</span></h3><ul class="faces subjects">${ss.map((s, i) => {
      const cur = s.current, ai = s.ai;
      const src = !cur ? '' : s.human ? '<small class="pill p-ok">人工确认</small>' : `<small class="hint">AI 候选${cur.p != null ? ' · ' + esc(fmtScore(cur.p)) : ''}</small>`;
      const aiDiff = s.human && ai && ai.concept !== cur.concept ? `<small class="hint">模型：${esc(idText(ai))}</small>` : '';
      const said = cur && cur.said ? `<small class="hint">当时记作 ${esc(cur.said.taxon)}（名录已更名）</small>` : '';
      return `<li><span class="face subj" style="${esc(crop(s))}" role="img" aria-label="动物 ${i + 1}"></span>
        <span><b>${esc(idText(cur))}</b>${src}${aiDiff}${said}</span>
        <span class="face-acts">${!s.human && ai && ai.level === 'species' ? btn('id-accept', s, '确认') : ''}${btn('id-change', s, '改为…')}${btn('id-unsure', s, '不确定')}</span></li>`;
    }).join('')}</ul>${burst ? `<label class="check-row"><input type="checkbox" data-pref="idburst" ${prefs.get('idburst', '1') === '1' ? 'checked' : ''}> 同一连拍（${a.burst.size} 张）里模型认成同一种的也一起改</label>` : ''}</section>`;
  }
  // 同一拍摄的其他版本：只在代表版本出现；“不是同一张”由人确认后永不再自动合并
  function versionsHTML(d) {
    if (!d) return '';
    const split = id => `<button type="button" class="btn sm ghost" data-action="split-version" data-id="${esc(id)}">不是同一张</button>`;
    if (d.original) return `<section class="card"><h3>同一拍摄<span class="sub">此素材不单独出现在分组与列表中</span></h3><p class="hint">是「${esc(d.original.name)}」的${esc(VERSION_WHY[d.original.reason] || '版本')}，分析与分组以它为准。</p>${split(d.id)}</section>`;
    if (!(d.versions || []).length) return '';
    return `<section class="card"><h3>同一拍摄的其他版本<span class="sub">${d.versions.length} 个 · 不重复出现在分组中</span></h3><ul class="versions">${d.versions.map(v => `<li><img src="${esc(mediaUrl(v.id, 'thumb'))}" alt="" loading="lazy"><span><b>${esc(v.name)}</b><small class="hint">${esc(VERSION_WHY[v.reason] || v.reason)}</small><small class="mono">${esc(v.path)}</small></span>${split(v.id)}</li>`).join('')}</ul></section>`;
  }
  async function ensureDetail(id) {
    if (S.details.has(id)) return S.details.get(id);
    try {
      const d = await api('GET', `/assets/${encodeURIComponent(id)}`);
      S.details.set(id, d);
      store([d]);
      return d;
    } catch (e) { return null; }
  }
  function renderInspector() {
    const el = $('#inspector'), ws = ui.ws;
    if (!el || !ws) return;
    const a = ws.current && A(ws.current);
    if (!a) {
      if (el.dataset.mode !== 'empty') { el.dataset.mode = 'empty'; el.innerHTML = `<div class="insp-empty">${ic('lib')}<p>选择一张素材，查看大图、AI 建议与人工判断。</p></div>`; }
      return;
    }
    if (el.dataset.mode !== 'full') {
      el.dataset.mode = 'full';
      el.innerHTML = '<div class="insp-canvas"><div class="insp-photo"></div><div class="canvas-bar"></div></div><div class="insp-scroll" id="insp-scroll"></div>';
    }
    const d = S.details.get(a.id);
    if (!d) ensureDetail(a.id).then(x => { if (x && ui.ws === ws && ws.current === a.id) renderInspector(); });
    const ph = $('.insp-photo', el), pk = `${a.id}|${sigMedia(a)}|${a.media}|${ui.zoom && !!subjectBox(d)}`;
    if (ph.dataset.pk !== pk) { ph.dataset.pk = pk; ph.innerHTML = stageEl(a, d); applyZoom(ph, a, d); }
    swap($('.canvas-bar', el), canvasBarHTML(a, d));
    const sc = $('#insp-scroll', el);
    const reason = $('#mark-reason', sc);
    const keepReason = reason && sc.dataset.cur === a.id ? reason.value : '';
    const keep = sc.dataset.cur === a.id ? sc.scrollTop : 0;
    swap(sc, panelHTML(a, d));
    const r2 = $('#mark-reason', sc);
    if (r2 && keepReason) r2.value = keepReason;
    sc.dataset.cur = a.id;
    sc.scrollTop = keep;
  }
  function renderLightbox() {
    const body = $('dialog.lightbox[open] .dlg-body');
    const ws = ui.ws, a = ws && ws.current && A(ws.current);
    if (!body || !a) return;
    if (!body.firstElementChild) body.innerHTML = '<div class="lb-stage"><div class="lb-photo"></div><div class="lb-navs"></div></div><div class="lb-bar"></div>';
    const i = ws.ids.indexOf(a.id);
    const d = S.details.get(a.id);
    const ph = $('.lb-photo', body), pk = `${a.id}|${sigMedia(a)}|${ui.zoom && !!subjectBox(d)}`;
    if (ph.dataset.pk !== pk) { ph.dataset.pk = pk; ph.innerHTML = stageEl(a, d); applyZoom(ph, a, d); }
    swap($('.lb-navs', body), `<button type="button" class="lb-nav prev" data-action="prev" data-key="lb-prev" aria-label="上一张" ${i <= 0 ? 'disabled' : ''}>${ic('left')}</button><button type="button" class="lb-nav next" data-action="next" data-key="lb-next" aria-label="下一张" ${i >= ws.ids.length - 1 ? 'disabled' : ''}>${ic('right')}</button>`);
    swap($('.lb-bar', body), `<div class="lb-info"><b>${esc(a.name)}</b><span>${i + 1} / ${ws.ids.length}</span><span class="hint">${PREVIEW_NOTE}</span></div><div class="lb-ai">${aiPill(a)}</div><div class="lb-human">${flagSeg(a)}${starsHTML(a)}</div>`);
  }
  function setCurrent(id, opts) {
    const ws = ui.ws;
    if (!ws || !id) return;
    const hadTileFocus = document.activeElement && document.activeElement.classList.contains('tile');
    ws.current = id;
    if (!(opts && opts.keepSel)) { ws.sel = new Set([id]); ws.anchor = id; }
    applySel();
    renderInspector();
    const t = $(`.tile[data-asset="${CSS.escape(id)}"]`);
    if (t) { t.scrollIntoView({ block: 'nearest' }); if (hadTileFocus) t.focus({ preventScroll: true }); }
    if (ws.taskId) prefs.set('last.' + ws.taskId, id);
    if (ws.colId) prefs.set('last.' + ws.colId, id);
    renderLightbox();
  }
  function gridCols() {
    const g = $('#grid'), t = $('.tile-wrap', g || document);
    return g && t ? Math.max(1, Math.round(g.clientWidth / (t.offsetWidth + 12))) : 1;
  }
  function move(delta) {
    const ws = ui.ws;
    if (!ws || !ws.ids.length) return;
    let i = ws.ids.indexOf(ws.current);
    i = i < 0 ? 0 : Math.max(0, Math.min(ws.ids.length - 1, i + delta));
    setCurrent(ws.ids[i]);
    if (i >= ws.ids.length - 3) loadMore(ws);
  }
  const selTargets = () => { const ws = ui.ws; return ws.sel.size > 1 ? Array.from(ws.sel) : ws.current ? [ws.current] : []; };

  // ---------- 人工标记与撤销 ----------
  function afterMarks(assets) {
    assets.forEach(x => { const a = A(x.id); if (a) Object.assign(a, { flag: x.flag, stars: x.stars, mark_version: x.version, judged: !!(x.flag || x.stars) }); S.details.delete(x.id); });
    patchTiles(assets.map(x => x.id)); renderInspector(); renderLightbox(); patchBinds();
    $$('dialog.compare[open] [data-cmp]').forEach(el => swap(el, cmpCapHTML(el.dataset.cmp)));
  }
  async function doMark(ids, patch, label, advance) {
    ids = ids.filter(id => A(id));
    if (!ids.length) return;
    const body = Object.assign({ asset_ids: ids, expect: {} }, patch);
    ids.forEach(id => { body.expect[id] = A(id).mark_version; });
    const reason = $('#mark-reason');
    if (reason && reason.value.trim()) body.reason = reason.value.trim();
    try {
      const r = await api('POST', '/marks', body);
      if (reason) reason.value = '';
      ui.undo.push({ batch: r.batch_id, label });
      afterMarks(r.assets);
      toast(`${label} · ${ids.length} 张`, { action: { label: '撤销', run: doUndo } });
      if (advance && prefs.get('advance', '0') === '1') move(1);
    } catch (e) {
      if (e.status === 409) { toast('有素材刚被其他客户端修改，已载入最新标记，请重新操作'); await Promise.all(ids.map(id => { S.details.delete(id); return ensureDetail(id); })); patchTiles(ids); renderInspector(); }
      else toast(errMsg(e));
    }
  }
  async function identify(el, claim, label) {
    const a = A(el.dataset.id);
    const scope = a && a.burst && a.burst.size > 1 && prefs.get('idburst', '1') === '1' ? 'burst' : 'subject';
    try {
      const r = await api('POST', '/identifications', { subject_ids: [el.dataset.subject], claim, scope });
      const n = r.changed.length, skip = r.skipped.filter(x => x.reason === 'human').length;
      S.details.clear();
      toast(`${label}${n > 1 ? `（${n} 只，含同一连拍）` : ''}${skip ? `；${skip} 张已有人工结论，未改` : ''}`, r.batch_id ? { action: { label: '撤销', run: async () => {
        try { await api('POST', '/identifications/undo', { batch_id: r.batch_id }); S.details.clear(); toast('已撤销'); renderInspector(); } catch (e) { toast(e.status === 409 ? '之后又被人工修改过，无法撤销' : errMsg(e)); }
      } } } : undefined);
      renderInspector();
    } catch (e) { toast(errMsg(e)); }
  }
  async function undoBatch(batch, label) {
    try {
      const r = await api('POST', '/marks/undo', { batch_id: batch });
      toast(`已撤销：${label}（${r.undone} 张）`);
      ui.undo = ui.undo.filter(u => u.batch !== batch);
      patchBinds('undo');
      await pollNow();
    } catch (e) { toast(e.status === 409 ? '这批素材之后又被修改过，无法撤销' : errMsg(e)); }
  }
  function doUndo() {
    const e = ui.undo[ui.undo.length - 1];
    if (!e) { toast('没有可撤销的操作'); return; }
    undoBatch(e.batch, e.label);
  }

  // ---------- Collections ----------
  async function refreshCols() { S.cols = await api('GET', '/collections', null, { deleted: '0' }); }
  function colState(c) {
    const st = c.review.state;
    if (st === 'completed') return ['已完成', 'ok'];
    if (st === 'zeroed') return ['本组不留', 'muted'];
    return c.judged ? ['审阅中', 'info'] : ['未开始', 'neutral'];
  }
  function colNotes(c) {
    const w = [];
    if (c.review.state === 'zeroed') w.push(c.picks ? `<span class="note warn">${ic('warn')}已标记本组不留，但含 ${c.picks} 张 Pick</span>` : '<span class="note muted">已明确零选</span>');
    else if (c.size && !c.picks) w.push(`<span class="note warn">${ic('warn')}尚无人工入选</span>`);
    if (c.low_score) w.push('<span class="note">AI 整组评分偏低 · 全部候选仍可审阅</span>');
    if (c.review.new_since) w.push(`<span class="note info">新增 ${c.review.new_since} 张待审</span>`);
    if (c.review.removed_since) w.push(`<span class="note info">完成后移出 ${c.review.removed_since} 张</span>`);
    if (c.review.changed_since) w.push(`<span class="note warn">${c.review.changed_since} 张内容已变化，旧判断见历史</span>`);
    return w.join('');
  }
  const colKindText = c => `${c.group_kind ? GROUP[c.group_kind] || c.group_kind : '自建'} · ${c.kind === 'smart' ? 'Smart' : 'Manual'}${c.origin === 'auto' ? ' · 自动建议' : ''}`;
  function montageHTML(c) {
    const ids = c.cover_ids.slice(0, 4);
    if (!ids.length) return '<div class="montage n1"><div class="ph ph-wait"><span class="ph-note">暂无成员</span></div></div>';
    return `<div class="montage n${ids.length}">${ids.map((id, i) => `<div class="ph${i ? '' : ' m-main'}"><img src="${esc(mediaUrl(id, 'thumb'))}" alt="" loading="lazy"><span class="ph-note ph-fallback">无预览</span></div>`).join('')}</div>${c.size > 4 ? `<span class="m-more">+${c.size - 4}</span>` : ''}`;
  }
  function colInfoHTML(c, showTask) {
    const [label, tone] = colState(c);
    const t = c.task_id && taskById(c.task_id);
    return `<div class="col-top"><h3><a class="block-link" href="#/review/${esc(c.id)}">${esc(c.name)}</a></h3>
        <button type="button" class="icon-btn" data-action="edit-col" data-id="${esc(c.id)}" aria-label="编辑「${esc(c.name)}」" title="改名与描述">${ic('edit')}</button></div>
      <p class="col-sub">${c.size} 张 · ${esc(colKindText(c))}${showTask && t ? ` · ${esc(t.title)}` : ''}</p>
      ${c.description ? `<p class="col-desc">${esc(c.description)}</p>` : ''}
      <div class="col-progress">${bar(pct(c.judged, c.size), 'bar-review')}<span>已判断 ${c.judged}/${c.size}</span></div>
      <div class="col-stats"><span class="chip c-${tone}">${label}</span><span class="stat"><b>${c.picks}</b> Pick</span><span class="stat"><b>${c.rejects}</b> Reject</span><span class="stat ai">AI 保留 ${c.ai_picks} · 舍弃 ${c.ai_rejects}</span></div>
      <div class="col-warn">${colNotes(c)}</div>`;
  }
  const COLF = [['all', '全部'], ['open', '未完成'], ['done', '已完成'], ['nopick', '尚无人工入选'], ['new', '新增待审']];
  const KINDF = [['', '全部类型'], ['person', '人物'], ['species', '物种'], ['animal', '动物'], ['scene', '场景'], ['other', '其他'], ['all', '全部素材'], ['user', '自建']];
  function passColF(c) {
    const k = ui.colKind;
    if (k === 'user' ? c.group_kind : k && c.group_kind !== k) return false;
    switch (ui.colFilter) {
      case 'open': return c.review.state === 'open';
      case 'done': return c.review.state !== 'open';
      case 'nopick': return c.size > 0 && !c.picks;
      case 'new': return c.review.new_since > 0 || c.review.changed_since > 0;
      default: return true;
    }
  }
  const scopeCols = () => (ui.view === 'task' ? S.cols.filter(c => c.task_id === ui.colScope) : S.cols);
  function colToolbarHTML(taskId) {
    const cols = scopeCols();
    const chips = (list, key, act) => `<div class="chips" role="group">${list.map(([k, l]) => `<button type="button" class="fchip" data-action="${act}" data-f="${k}" aria-pressed="${ui[key] === k}">${l}</button>`).join('')}</div>`;
    return `<div class="col-toolbar">${chips(COLF, 'colFilter', 'col-filter')}${chips(KINDF, 'colKind', 'col-kind')}
      <div class="ws-tools"><span class="hint">${cols.length} 个集合</span>
      <button type="button" class="btn sm ghost" data-action="new-col" data-id="${esc(taskId || '')}">${ic('plus')}新建集合</button>
      <button type="button" class="btn sm ghost" data-action="merge-cols">合并…</button>
      <button type="button" class="btn sm ghost" data-action="coverage" data-id="${esc(taskId || '')}">覆盖检查</button>
      <a class="btn sm ghost" href="#/collections/deleted">${ic('trash')}已删除</a></div></div>`;
  }
  // 主体（人物/物种/动物/场景/其他）是 block；带 GPS 的大组按地点细分，细分是块内入口。时间不切分分组
  function topLevel(cols) {
    const live = new Set(scopeCols().map(c => c.id));
    const rank = c => { const i = GROUP_ORDER.indexOf(c.group_kind || 'user'); return i < 0 ? GROUP_ORDER.length : i; };
    return cols.filter(c => !c.parent_id || !live.has(c.parent_id)).sort((a, b) => rank(a) - rank(b) || b.size - a.size);
  }
  function childrenHTML(c) {
    const kids = scopeCols().filter(k => k.parent_id === c.id);
    if (!kids.length) return '';
    return `<div class="col-kids"><span class="hint">按${kids[0].group_kind === 'place' ? '地点' : '日期（旧）'}细分</span>${kids.map(k => `<a class="kid-chip${k.review.state !== 'open' ? ' done' : ''}" href="#/review/${esc(k.id)}" title="${esc(k.name)}">${esc(k.label || k.name)}<b>${k.size}</b></a>`).join('')}</div>`;
  }
  function blocksHTML(cols, showTask) {
    return `<div class="col-grid">${topLevel(cols).map(c => `<article class="col-block"><div class="montage-wrap">${montageHTML(c)}</div><div class="col-info">${colInfoHTML(c, showTask)}${childrenHTML(c)}</div></article>`).join('')}</div>`;
  }
  function colAreaHTML() {
    const cols = scopeCols().filter(passColF);
    ui.colKey = JSON.stringify(scopeCols());
    if (ui.view === 'task') return cols.length ? blocksHTML(cols, false) : '<div class="empty">还没有集合。照片分析完成后按人物、物种、动物类型和场景分组；同一主体跨多少天都在一组，带 GPS 的大组再按地点细分。</div>';
    const groups = new Map();
    cols.forEach(c => { const k = c.task_id || ''; if (!groups.has(k)) groups.set(k, []); groups.get(k).push(c); });
    if (!groups.size) return '<div class="empty">没有符合条件的集合。</div>';
    return Array.from(groups.entries()).map(([tid, list]) => {
      const t = taskById(tid);
      return `<section class="col-section"><header class="cs-head"><h2>${t ? `<a href="#/task/${esc(t.id)}/collections">${esc(t.title)}</a>` : '跨任务集合'}</h2>${t ? statusChip(t) : ''}<span class="hint">${topLevel(list).length} 个集合</span></header>${blocksHTML(list, false)}</section>`;
    }).join('');
  }
  async function vCollections() {
    await refreshCols();
    return `<div class="page">
      <header class="page-head"><div><h1 tabindex="-1">Collections</h1><p class="lede">按人物、物种、动物类型、场景等主体组织的 Review 单位：同一主体不按时间切开，带 GPS 的大组按地点细分。人物分组可以命名，以后的扫描会直接归入。同一素材可出现在多个集合，人工标记全局一致；各集合完成进度独立。</p></div></header>
      ${colToolbarHTML('')}
      <div id="col-area">${colAreaHTML()}</div>
    </div>`;
  }
  async function vDeleted() {
    const cols = await api('GET', '/collections', null, { deleted: '1' });
    return `<div class="page"><a class="back" href="#/collections">${ic('left')}Collections</a>
      <header class="page-head"><div><h1 tabindex="-1">已删除的集合</h1><p class="lede">删除只去掉集合及成员关系；素材和全局 Flag/Star 不变，可随时恢复。</p></div></header>
      <div class="del-list">${cols.map(c => `<div class="del-row card"><div><b>${esc(c.name)}</b><p class="hint">${c.size} 张 · ${esc(colKindText(c))} · 删除于 ${esc(fmtTime(c.deleted_at))}</p></div><button type="button" class="btn sm" data-action="restore-col" data-id="${esc(c.id)}">恢复</button></div>`).join('') || '<div class="empty">没有已删除的集合。</div>'}</div></div>`;
  }

  // ---------- Review ----------
  function colHeadHTML(c) {
    const [label, tone] = colState(c);
    return `<span class="chip c-${tone}">${label}</span>
      <div class="rh-progress">${bar(pct(c.judged, c.size), 'bar-review')}<span>已判断 <b>${c.judged}</b>/${c.size}</span></div>
      <span class="stat"><b>${c.picks}</b> Pick</span><span class="stat"><b>${c.rejects}</b> Reject</span><span class="stat ai">AI 保留 ${c.ai_picks} · 舍弃 ${c.ai_rejects}</span><span class="stat muted">${c.review.unhandled} 张未逐张处理</span>${colNotes(c)}`;
  }
  function colActionsHTML(c) {
    const closed = c.review.state !== 'open';
    return `${closed ? '<button type="button" class="btn sm ghost" data-action="reopen-col" data-key="reopen">重新打开</button>' : `<button type="button" class="btn sm accent" data-action="complete-col" data-key="complete">${ic('check')}完成本组</button><button type="button" class="btn sm ghost" data-action="zero-col" data-key="zero">本组不留</button>`}
      <button type="button" class="btn sm ghost" data-action="export" data-key="export">${ic('download')}导出…</button>
      ${c.kind === 'smart' ? '<button type="button" class="btn sm ghost" data-action="save-manual" data-key="save-manual">另存为 Manual…</button>' : ''}
      <button type="button" class="btn sm ghost" data-action="delete-col" data-key="delete-col">${ic('trash')}删除</button>
      <button type="button" class="btn sm" data-action="next-col" data-key="next-col">下一组${ic('right')}</button>`;
  }
  async function vReview(p) {
    const c = await api('GET', `/collections/${encodeURIComponent(p.id)}`);
    ui.col = c;
    if (!S.cols.length) await refreshCols();
    const t = c.task_id && taskById(c.task_id);
    makeWs('review', { collection: c.id }, 'discovered', { colId: c.id, taskId: null, start: prefs.get('last.' + c.id, null) });
    return `<div class="wpage">
      <header class="rv-head">
        <nav class="crumbs" aria-label="位置"><a href="#/collections">Collections</a>${t ? `<span aria-hidden="true">/</span><a href="#/task/${esc(t.id)}/collections">${esc(t.title)}</a>` : ''}<span aria-hidden="true">/</span><span>Review</span></nav>
        <div class="rh-row">
          <div class="rh-title"><h1 tabindex="-1">${esc(c.name)}</h1><button type="button" class="icon-btn" data-action="edit-col" data-id="${esc(c.id)}" aria-label="编辑集合" title="改名与描述">${ic('edit')}</button><span class="hint">${esc(colKindText(c))}</span></div>
          <div class="rh-actions" data-bind="col-actions">${colActionsHTML(c)}</div>
        </div>
        ${c.description ? `<p class="col-desc">${esc(c.description)}</p>` : ''}
        ${c.kind === 'smart' ? `<p class="hint">Smart 规则：<span class="mono">${esc(JSON.stringify(c.rule))}</span> · 成员按规则实时求值</p>` : ''}
        <div class="rh-stats" data-bind="col-head">${colHeadHTML(c)}</div>
      </header>
      ${wsHTML()}
    </div>`;
  }
  async function refreshCol() {
    if (!ui.col) return;
    try { ui.col = await api('GET', `/collections/${encodeURIComponent(ui.col.id)}`); patchBinds('col-head'); patchBinds('col-actions'); patchBinds('ws-batch'); } catch (e) { /* 下次轮询再试 */ }
  }

  // ---------- 物种 ----------
  const speciesHref = k => `#/species/${encodeURIComponent(k)}`;
  function speciesCardHTML(k) {
    const when = k.first_taken_at === k.last_taken_at ? fmtTime(k.first_taken_at).slice(0, 10) : `${fmtTime(k.first_taken_at).slice(0, 10)} – ${fmtTime(k.last_taken_at).slice(0, 10)}`;
    return `<a class="sp-card" href="${esc(speciesHref(k.concept))}">
      <img src="${esc(mediaUrl(k.cover_id, 'thumb'))}" alt="" loading="lazy">
      <span class="sp-name"><b>${esc(k.zh || k.common || k.taxon)}</b>${k.zh && k.common ? `<small>${esc(k.common)}</small>` : ''}<i>${esc(k.taxon)}</i></span>
      <span class="sp-meta">${k.confirmed ? `已确认 ${k.confirmed}` : ''}${k.confirmed && k.candidates ? ' · ' : ''}${k.candidates ? `待确认 ${k.candidates}` : ''}${k.only_one ? ' · <b class="pill p-wait">仅 1 张</b>' : ''}</span>
      <span class="sp-meta mono">${esc(when)}</span></a>`;
  }
  async function vSpecies(p) {
    const lib = await api('GET', '/species', null, { task: p.task });
    const t = p.task && (taskById(p.task) || await api('GET', `/tasks/${encodeURIComponent(p.task)}`));
    const kind = ui.spKind || '';
    const pass = k => !kind || k.kind === kind;
    const grid = list => `<div class="sp-grid">${list.filter(pass).map(speciesCardHTML).join('') || '<p class="hint">没有。</p>'}</div>`;
    const un = lib.unresolved;
    return `<div class="page">
      <header class="page-head"><div>${t ? `<nav class="crumbs" aria-label="位置"><a href="#/task/${esc(t.id)}/preview">${esc(t.title)}</a><span aria-hidden="true">/</span><a href="#/species">全部物种</a></nav>` : ''}<h1 tabindex="-1">${t ? '本次拍摄的物种' : '物种'}</h1>
        <p class="lede">${t ? '本次' : '已'}拍到 <b>${lib.confirmed_total}</b> 种<span class="hint">（至少有一张照片经你确认的物种；只有模型候选的物种在「待确认」，不计入）</span></p></div>
        <div class="seg" role="group" aria-label="类别">${[['', '全部'], ['bird', '鸟类'], ['mammal', '哺乳动物'], ['other_animal', '其他动物']].map(([v, l]) => `<button type="button" class="seg-btn" data-action="sp-kind" data-kind="${v}" aria-pressed="${kind === v}">${l}</button>`).join('')}</div></header>
      <section><h2>已确认<span class="hint"> · ${lib.species.length} 种 · 每个物种一组，跨任务与日期累积，一张照片也单独成组</span></h2>${grid(lib.species)}</section>
      <section><h2>待确认<span class="hint"> · ${lib.to_confirm.length} 种 · 只有模型候选</span></h2>${grid(lib.to_confirm)}</section>
      <section><h2>待鉴定</h2><p class="hint">只识别到属或科：<a href="#/library" data-action="lib-preset" data-filter="id_state" data-value="unresolved">${un.genus_family + un.unconfirmed} 张</a>（属/科 ${un.genus_family} · 不确定 ${un.unconfirmed}）。可以照常保留、修图和导出，不需要先鉴定。</p></section>
    </div>`;
  }
  async function vSpeciesOne(p) {
    const lib = await api('GET', '/species', null, { hidden: '1' });
    const k = lib.species.concat(lib.to_confirm).find(x => x.concept === p.concept);
    if (!k) throw Object.assign(new Error('没有这个物种的照片'), { status: 404 });
    ui.sp = k;
    makeWs('query', { concept: k.concept }, 'time', {});
    return `<div class="wpage">
      <header class="rv-head">
        <nav class="crumbs" aria-label="位置"><a href="#/species">物种</a><span aria-hidden="true">/</span><span>${esc(k.common || k.taxon)}</span></nav>
        <div class="rh-row">
          <div class="rh-title"><h1 tabindex="-1">${esc(k.zh || k.common || k.taxon)}</h1>${k.zh && k.common ? `<span class="hint">${esc(k.common)}</span>` : ''}<i class="hint">${esc(k.taxon)}</i>${k.kind ? `<span class="hint">${esc(KIND_ZH[k.kind] || k.kind)}</span>` : ''}</div>
          <div class="rh-actions"><button type="button" class="btn sm ghost" data-action="sp-cover">设当前照片为封面</button><button type="button" class="btn sm ghost" data-action="sp-hide">${k.hidden ? '在物种页显示' : '在物种页隐藏'}</button></div>
        </div>
        <p class="hint">已确认 ${k.confirmed} 张 · 待确认 ${k.candidates} 张 · 首次 ${esc(fmtTime(k.first_taken_at))} · 最近 ${esc(fmtTime(k.last_taken_at))}。成员来自每张照片的物种结论：在右侧「物种」卡片确认或纠正。</p>
      </header>
      ${wsHTML()}
    </div>`;
  }

  // ---------- 全部素材 ----------
  const LIBF = [
    ['task', '任务', 'sel'], ['collection', '集合', 'sel'], ['media', '媒体', [['', '不限'], ['photo', '照片'], ['video', '视频']]],
    ['flag', '人工 Flag', [['', '不限'], ['pick', 'Pick'], ['reject', 'Reject'], ['none', '未标记']]],
    ['stars_min', '星级', [['', '不限'], ['1', '≥ 1 星'], ['3', '≥ 3 星'], ['5', '5 星']]],
    ['ai', 'AI 建议', [['', '不限'], ['pick', '建议保留'], ['reject', '建议舍弃'], ['none', '无建议']]],
    ['judged', '判断状态', [['', '不限'], ['yes', '已判断'], ['no', '未判断']]],
    ['online', '原片', [['', '不限'], ['1', '在线'], ['0', '离线']]],
    ['analysis', '分析状态', [['', '不限'], ['pending', '等待中'], ['done', '已分析'], ['failed', '失败'], ['unavailable', '不可用']]],
    ['has_gps', 'GPS', [['', '不限'], ['1', '有'], ['0', '无']]],
    ['id_state', '物种结论', [['', '不限'], ['confirmed', '已确认'], ['candidate', '模型候选'], ['unresolved', '待鉴定（属/科/不确定）']]],
    ['tag', '标签', 'sel'],
    ['order', '排序', [['time', '拍摄时间'], ['discovered', '发现顺序'], ['path', '文件名'], ['score', 'AI 分数']]],
  ];
  const libRule = () => { const r = {}; Object.entries(ui.lib).forEach(([k, v]) => { if (v !== '' && v != null && k !== 'order') r[k] = v; }); return r; };
  async function vLibrary() {
    if (!S.cols.length) await refreshCols();
    let tags = [];
    try { tags = await api('GET', '/tags'); } catch (e) { tags = []; }
    const f = ui.lib;
    const opts = k => (k === 'task' ? [['', '全部任务']].concat(S.tasks.map(t => [t.id, t.title])) : k === 'collection' ? [['', '全部集合']].concat(S.cols.map(c => [c.id, c.name])) : [['', '全部标签']].concat(tags.filter(t => t.active).map(t => [t.id, t.name])));
    const sel = ([name, label, o]) => `<label class="fsel"><span>${label}</span><select class="input sm" name="${name}">${(o === 'sel' ? opts(name) : o).map(([v, l]) => `<option value="${esc(v)}" ${String(f[name] || (name === 'order' ? 'time' : '')) === v ? 'selected' : ''}>${esc(l)}</option>`).join('')}</select></label>`;
    const txt = (name, label, type, ph) => `<label class="fsel"><span>${label}</span><input class="input sm" type="${type}" name="${name}" value="${esc(f[name] || '')}" placeholder="${esc(ph || '')}"></label>`;
    makeWs('query', libRule(), f.order || 'time', {});
    return `<div class="wpage">
      <header class="rv-head">
        <div class="rh-row"><div class="rh-title"><h1 tabindex="-1">全部素材</h1><span class="hint">跨任务组合筛选；结果可直接 Review、保存为 Smart Collection 或导出。</span></div>
          <div class="rh-actions"><button type="button" class="btn sm ghost" data-action="lib-clear">清除筛选</button><button type="button" class="btn sm ghost" data-action="save-smart">保存为 Smart Collection…</button><button type="button" class="btn sm ghost" data-action="export">${ic('download')}导出…</button></div></div>
        <form class="filters" id="lib-filters" aria-label="组合筛选">
          ${LIBF.map(sel).join('')}
          ${txt('date_from', '起始日期', 'date')}${txt('date_to', '结束日期', 'date')}
          ${txt('content', '内容', 'search', '场景大类或标签')}${txt('species', '物种', 'search', '学名或俗名')}${txt('q', '文件名', 'search', '包含…')}
        </form>
      </header>
      ${wsHTML()}
    </div>`;
  }

  // ---------- 管理 ----------
  async function vSettings() {
    const [caps, presets, settings, tags, storage] = await Promise.all([api('GET', '/capabilities'), api('GET', '/presets'), api('GET', '/settings'), api('GET', '/tags'), api('GET', '/storage')]);
    S.caps = caps; S.presets = presets; S.settings = settings;
    const tagName = id => { const t = tags.find(x => x.id === id); return t ? t.name : id; };
    const versions = settings.capability_versions || {};
    return `<div class="page"><div class="narrow wide">
      <header class="page-head"><div><h1 tabindex="-1">管理</h1><p class="lede">能力与版本、扫描预设、默认运行模式、标签，以及数据与缓存位置。</p></div></header>
      <section class="set-sec"><div class="sec-head"><h2>分析能力</h2><button type="button" class="btn sm" data-action="caps-check" id="caps-check">${ic('retry')}检查模型</button></div>
        <p class="hint">能力不直接绑定某个模型；每次运行记录所用版本。「检查模型」会试加载模型，可能需要一段时间。</p>
        <table class="tbl"><thead><tr><th>能力</th><th>版本</th><th>状态</th><th>说明</th></tr></thead><tbody>${caps.map(c => `<tr><td><b>${esc(c.name)}</b><small class="mono">${esc(c.id)}</small></td><td class="mono">${esc(c.version || versions[c.id] || '—')}</td><td>${pill({ available: '可用', unavailable: '不可用', unknown: '未检查' }[c.state] || c.state, c.state === 'available' ? 'ok' : c.state === 'unavailable' ? 'fail' : 'wait')}</td><td>${esc(c.reason || c.description)}</td></tr>`).join('')}</tbody></table></section>
      <section class="set-sec"><div class="sec-head"><h2>扫描预设</h2><button type="button" class="btn sm ghost" data-action="preset-new">${ic('plus')}新建预设</button></div>
        <div class="preset-table">${presets.map(p => `<div class="pt-row"><b>${esc(p.name)}${p.builtin ? ' <small class="hint">内建 · 只读</small>' : ''}</b><span class="pc-caps">${p.capabilities.map(c => `<span class="cap-chip">${esc(capName(c))}</span>`).join('')}</span><span class="pt-act">${p.builtin ? `<button type="button" class="btn sm ghost" data-action="preset-saveas" data-id="${esc(p.id)}">另存为…</button>` : `<button type="button" class="btn sm ghost" data-action="preset-edit" data-id="${esc(p.id)}">编辑</button><button type="button" class="btn sm danger-ghost" data-action="preset-delete" data-id="${esc(p.id)}">删除</button>`}</span></div>`).join('')}</div>
        <p class="hint">任务保存启动时的配置快照；修改预设不影响已创建任务。</p></section>
      <section class="set-sec"><h2>默认设置</h2>
        <div class="seg-radio" role="radiogroup" aria-label="默认运行模式">${Object.entries(MODES).map(([k, r]) => `<label><input type="radio" name="def-mode" value="${k}" ${settings.run_mode === k ? 'checked' : ''}><span>${r[0]}<small>${r[1]}</small></span></label>`).join('')}</div>
        <div class="field"><label for="def-preset">默认预设</label><select id="def-preset" class="input" name="def-preset">${presets.map(p => `<option value="${esc(p.id)}" ${p.id === settings.default_preset ? 'selected' : ''}>${esc(p.name)}</option>`).join('')}</select></div>
        <p class="hint">运行模式只控制资源占用，不关闭能力、不降低分析质量。</p></section>
      <section class="set-sec"><div class="sec-head"><h2>标签</h2><button type="button" class="btn sm ghost" data-action="tag-new">${ic('plus')}新建标签</button></div>
        <p class="hint">标签使用稳定身份；改名、别名、上级、合并、拆分、停用都保留历史，人工与 AI 来源分开保存。</p>
        <table class="tbl"><thead><tr><th>标签</th><th>别名</th><th>上级</th><th>素材</th><th>状态</th><th></th></tr></thead><tbody>${tags.map(t => `<tr${t.active ? '' : ' class="muted"'}><td><b>${esc(t.name)}</b><small class="mono">${esc(t.id)}</small></td><td>${esc((t.aliases || []).join('、') || '—')}</td><td>${t.parent_id ? esc(tagName(t.parent_id)) : '—'}</td><td>${esc(t.count)}</td><td>${t.merged_into ? `已合并到 ${esc(tagName(t.merged_into))}` : t.active ? '启用' : '已停用'}</td><td class="pt-act"><button type="button" class="btn sm ghost" data-action="tag-edit" data-id="${esc(t.id)}">编辑</button><button type="button" class="btn sm ghost" data-action="tag-merge" data-id="${esc(t.id)}" ${t.merged_into ? 'disabled' : ''}>合并到…</button><button type="button" class="btn sm ghost" data-action="tag-split" data-id="${esc(t.id)}">拆分…</button></td></tr>`).join('') || '<tr><td colspan="6" class="hint">还没有标签。</td></tr>'}</tbody></table></section>
      <section class="set-sec"><h2>数据与存储</h2>
        <dl class="store">
          <dt>数据目录</dt><dd class="mono">${esc(storage.data_dir)}</dd>
          <dt>整理记录</dt><dd><span class="mono">${esc(storage.db_path)}</span> · ${fmtBytes(storage.db_bytes)}</dd>
          <dt>预览缓存</dt><dd><span class="mono">${esc(storage.cache_dir)}</span> · ${fmtBytes(storage.cache_bytes)} · ${esc(storage.cache_files)} 个文件</dd>
          <dt>原片</dt><dd>保留原位置，Gardener 不移动、改名或删除；Reject 不触发任何文件操作。</dd>
        </dl>
        <div class="set-actions"><button type="button" class="btn sm danger-ghost" data-action="clear-cache">清理预览缓存…</button></div></section>
      ${S.tags = tags, ''}
    </div></div>`;
  }

  function notFound(msg, href, label) {
    return `<div class="page"><div class="empty big"><h1 tabindex="-1">${esc(msg)}</h1><a class="btn" href="${href}">${label}</a></div></div>`;
  }

  // ---------- 对话框 ----------
  function dialog(o) {
    const opener = document.activeElement;
    const openerKey = opener && opener.dataset ? opener.dataset.key : null;
    const d = document.createElement('dialog');
    d.className = 'dlg ' + (o.cls || '');
    d.setAttribute('aria-labelledby', 'dlg-title');
    d.innerHTML = `<form method="dialog" class="dlg-form">
      <header class="dlg-head"><h2 id="dlg-title">${esc(o.title)}</h2><button type="button" class="icon-btn" data-close aria-label="关闭">${ic('x')}</button></header>
      <div class="dlg-body">${o.body}</div>
      <p class="field-err dlg-err" aria-live="polite"></p>
      ${o.actions ? `<footer class="dlg-foot"><button type="button" class="btn ghost" data-close>${esc(o.cancel || '取消')}</button>${o.actions.map(a => `<button type="submit" class="btn ${a.kind || ''}" value="${esc(a.value)}">${esc(a.label)}</button>`).join('')}</footer>` : ''}
    </form>`;
    document.body.append(d);
    d.addEventListener('click', e => { if (e.target.closest('[data-close]') || e.target === d) d.close(); });
    d.querySelector('form').addEventListener('submit', async e => {
      e.preventDefault();
      const v = e.submitter && e.submitter.value;
      if (!o.onSubmit) { d.close(); return; }
      const btns = $$('.dlg-foot [type=submit]', d);
      btns.forEach(b => { b.disabled = true; });
      try {
        if ((await o.onSubmit(v, d)) !== false) d.close();
      } catch (err) { $('.dlg-err', d).textContent = errMsg(err); }
      btns.forEach(b => { b.disabled = false; });
    });
    d.addEventListener('close', () => {
      d.remove();
      if (o.onClose) o.onClose();
      const back = opener && document.contains(opener) ? opener : openerKey && $(`[data-key="${CSS.escape(openerKey)}"]`);
      if (back) back.focus({ preventScroll: true });
    });
    d.showModal();
    const f = d.querySelector(o.focus || '.dlg-body input, .dlg-body select, .dlg-foot [type=submit], .dlg-body button');
    if (f) f.focus();
    return d;
  }
  const confirmDialog = (title, body, label, kind, run) => dialog({ title, body, actions: [{ label, value: 'ok', kind: kind || 'primary' }], focus: kind === 'danger' ? '.dlg-foot [data-close]' : null, onSubmit: v => (v === 'ok' ? run() : undefined) });
  const val = (d, sel) => ($(sel, d).value || '').trim();

  function openLightbox() {
    if (!ui.ws || !ui.ws.current) return;
    dialog({ title: '放大查看', cls: 'lightbox', body: '' });
    renderLightbox();
  }
  function cmpCapHTML(id) {
    const a = A(id);
    return `<div class="cmp-meta"><b>${esc(a.name)}</b>${aiPill(a)}</div>${a.ai && (a.ai.reasons || []).length ? `<p class="hint">AI：${esc(a.ai.reasons.join('；'))}</p>` : ''}<div class="cmp-human">${flagSeg(a)}${starsHTML(a)}</div>`;
  }
  function openCompare() {
    const ws = ui.ws;
    if (!ws || ws.sel.size !== 2) { toast('请先选择 2 张（⌘/Ctrl 或 Shift 多选）后再比较'); return; }
    const ids = Array.from(ws.sel);
    dialog({ title: '并排比较', cls: 'compare', body: `<div class="cmp-grid">${ids.map(id => `<figure class="cmp-item">${photo(A(id), 'large', 'ph-contain')}<figcaption data-cmp="${esc(id)}">${cmpCapHTML(id)}</figcaption></figure>`).join('')}</div><p class="hint">可以两张都保留、都舍弃或都不标记。${PREVIEW_NOTE}</p>` });
  }
  function openEditCol(id) {
    const c = S.cols.find(x => x.id === id) || (ui.col && ui.col.id === id ? ui.col : null);
    if (!c) return;
    const person = c.group_kind === 'person' && c.origin === 'auto';
    const nameField = person
      ? `<div class="field"><label for="ec-name">这个人叫什么</label><input id="ec-name" class="input" maxlength="60" value="${esc(c.name)}" list="ec-people" autocomplete="off"><datalist id="ec-people"></datalist></div>`
      : `<div class="field"><label for="ec-name">名称</label><input id="ec-name" class="input" maxlength="80" value="${esc(c.name)}"></div>`;
    const d = dialog({
      title: person ? '给人物命名' : '编辑集合', focus: '#ec-name',
      body: `${nameField}
        <div class="field"><label for="ec-desc">描述</label><textarea id="ec-desc" class="input" rows="3" maxlength="1000">${esc(c.description)}</textarea></div>
        ${c.kind === 'smart' ? `<div class="field"><label for="ec-rule">规则（JSON，键同全部素材筛选）</label><textarea id="ec-rule" class="input mono" rows="3">${esc(JSON.stringify(c.rule || {}))}</textarea></div>` : ''}
        <p class="hint">${person ? '命名后，这一组的脸成为此人的参考：所有任务里认出是此人的照片都归入这个名字的分组，以后的扫描也会直接加进来。名字与已有的人相同，就合并为同一个人。' : '改名不影响成员和人工标记；之后重新分组也不会覆盖人工命名。'}</p>`,
      actions: [{ label: '保存', value: 'ok', kind: 'primary' }],
      onSubmit: async (v, dd) => {
        const name = val(dd, '#ec-name');
        if (!name) throw new Error('名称不能为空');
        const body = { name, description: val(dd, '#ec-desc'), version: c.version };
        if (c.kind === 'smart') { try { body.rule = JSON.parse(val(dd, '#ec-rule') || '{}'); } catch (e) { throw new Error('规则不是有效的 JSON'); } }
        let got;
        try { got = await api('PATCH', `/collections/${encodeURIComponent(c.id)}`, body); } catch (e) { if (e.status === 409) { await rerender(); throw new Error('集合已被其他客户端修改，已载入最新版本，请重新编辑'); } throw e; }
        S.details.clear();
        toast(got.id !== c.id ? `已并入「${got.name}」` : `已保存「${name}」`);
        if (got.id !== c.id && ui.col && ui.col.id === c.id) { location.hash = `#/review/${got.id}`; return; }
        await rerender();
      },
    });
    if (person) peopleOptions($('#ec-people', d));
  }
  async function peopleOptions(list) {
    try { list.innerHTML = (await api('GET', '/people')).map(p => `<option value="${esc(p.name)}"></option>`).join(''); } catch (e) { /* 只影响输入提示 */ }
  }
  function openComplete() {
    const c = ui.col;
    dialog({
      title: '完成本组？',
      body: `<p>本组 ${c.size} 张中，${c.judged} 张已判断，<b>${c.review.unhandled} 张未逐张处理</b>。</p><p class="hint">未处理的素材保持原状，<b>不会被自动 Reject</b>。之后若有新成员加入，会标记为「新增待审」，历史完成记录保留。</p>
        <div class="field"><label for="cp-note">备注（可选）</label><input id="cp-note" class="input" maxlength="200"></div>`,
      actions: [{ label: '完成本组', value: 'ok', kind: 'primary' }],
      onSubmit: async (v, d) => {
        const note = val(d, '#cp-note');
        await api('POST', `/collections/${encodeURIComponent(c.id)}/complete`, note ? { note } : {});
        await refreshCol(); await refreshCols();
        const next = nextCol();
        toast(`已完成「${c.name}」`, next ? { action: { label: '下一组', run: () => { location.hash = '#/review/' + next.id; } } } : {});
      },
    });
  }
  async function zero(c, resolution) {
    const r = await api('POST', `/collections/${encodeURIComponent(c.id)}/zero`, resolution ? { resolution } : {});
    await refreshCol(); await refreshCols();
    if (r.batch_id) toast(`已清除 Pick，「${c.name}」记为本组不留`, { action: { label: '撤销清除', run: () => undoBatch(r.batch_id, '本组不留清除的 Pick') } });
    else toast(`「${c.name}」已记为本组不留`);
    pollNow();
  }
  function openZero() {
    const c = ui.col;
    confirmDialog('本组不留？', `<p>将「${esc(c.name)}」记为明确零选。</p><p class="hint">不会 Reject、移动或删除任何素材；若本组含已 Pick 的素材，会先请你选择如何处理。</p>`, '确认本组不留', 'primary', async () => {
      try { await zero(c); } catch (e) {
        if (e.status !== 409 || !e.body.picks) throw e;
        setTimeout(() => openZeroConflict(c, e.body.picks), 0);
      }
    });
  }
  function openZeroConflict(c, picks) {
    const list = picks.map(p => { const a = A(p.id) || { id: p.id, name: p.id, preview: 'ready', online: true }; return `<li class="conf-item">${photo(a, 'thumb')}<span><b>${esc(a.name)}</b><span class="hint">${p.collections.length ? '也在：' + p.collections.map(x => esc(x.name)).join('、') : '仅在本组'}</span></span></li>`; }).join('');
    dialog({
      title: `本组含 ${picks.length} 张已 Pick 的素材`, cls: 'conflict',
      body: `<p class="note warn block">${ic('warn')}「本组不留」与现有 Pick 冲突。Pick 是全局判断，会同时影响这些素材所在的其他集合，因此不会被静默修改。</p><ul class="conf-list">${list}</ul><p class="hint">请选择处理方式（清除后可撤销）：</p>`,
      actions: [{ label: '保留 Pick，仅完成本组', value: 'keep_picks', kind: 'primary' }, { label: `清除这 ${picks.length} 张的 Pick 并记为不留`, value: 'clear_picks', kind: 'danger' }],
      onSubmit: v => (v ? zero(c, v) : undefined),
    });
  }
  function nextCol() {
    const c = ui.col;
    if (!c) return null;
    const list = S.cols.filter(x => x.task_id === c.task_id && x.size > 0);
    const i = list.findIndex(x => x.id === c.id);
    const after = list.slice(i + 1).concat(list.slice(0, Math.max(0, i)));
    return after.find(x => x.review.state === 'open') || null;
  }
  function openExport() {
    const ws = ui.ws;
    const isCol = ws && ws.kind === 'review' && ui.col;
    const sel = ws ? Array.from(ws.sel) : [];
    const source = isCol ? { collection_id: ui.col.id } : ws && ws.kind === 'preview' ? { rule: { task: ws.taskId } } : { rule: libRule() };
    const radio = (v, label, on, dis) => `<label class="radio-row"><input type="radio" name="scope" value="${v}" ${on ? 'checked' : ''} ${dis ? 'disabled' : ''}><span>${label}</span></label>`;
    const d = dialog({
      title: isCol ? `导出「${ui.col.name}」` : '导出筛选结果', cls: 'export',
      body: `<fieldset class="fs"><legend>范围</legend>${radio('picked', '已 Pick', true)}${radio('all', isCol ? '本组全部成员' : '全部结果')}${radio('selected', `当前选中（${sel.length} 张）`, false, !sel.length)}</fieldset>
        <div class="field"><label for="ex-dest">目标目录</label><input id="ex-dest" class="input mono" placeholder="默认：服务数据目录下的 exports/" autocomplete="off"></div>
        <label class="switch-row boxed"><span class="sw-text"><b>复制内容</b><small>默认关闭</small></span><input type="checkbox" role="switch" class="switch" id="copy-toggle"></label>
        <div id="copy-note" class="copy-note" aria-live="polite"></div>
        <div class="field" id="ex-conflict-f" hidden><label for="ex-conflict">同名冲突</label><select id="ex-conflict" class="input"><option value="rename">改名保留两份</option><option value="skip">跳过</option><option value="fail">报错</option></select></div>
        <p class="hint">${XML_NOTE}</p><div id="ex-report"></div>`,
      actions: [{ label: '导出', value: 'ok', kind: 'primary' }],
      onSubmit: async (v, dlg) => {
        if (v !== 'ok') return undefined;
        const scope = $('input[name=scope]:checked', dlg).value;
        const body = Object.assign({}, scope === 'selected' ? { asset_ids: sel } : source, { scope, copy: $('#copy-toggle', dlg).checked, conflict: $('#ex-conflict', dlg).value });
        const dest = val(dlg, '#ex-dest');
        if (dest) body.dest_dir = dest;
        const r = await api('POST', '/export', body);
        $('#ex-report', dlg).innerHTML = reportHTML(r);
        $('.dlg-foot [type=submit]', dlg).hidden = true;
        return false;
      },
    });
    const sync = () => {
      const on = $('#copy-toggle', d).checked;
      $('#ex-conflict-f', d).hidden = !on;
      $('#copy-note', d).innerHTML = on
        ? `<p class="note warn block">${ic('warn')}开启：除 XML 外，把所选素材的全部文件（视频整段）复制到目标目录的 files/ 下，复制后逐个做 sha256 校验；源文件不会被删除。</p>`
        : '<p class="note block">关闭：只写 <b>一个</b> XML 文件，引用原片位置，不复制任何文件。</p>';
    };
    $('#copy-toggle', d).addEventListener('change', sync);
    sync();
  }
  function reportHTML(r) {
    return `<section class="card report"><h3>导出完成${r.verified ? ' · 副本已校验' : ''}</h3><dl class="meta">
      <dt>XML</dt><dd class="mono">${esc(r.xml_path)}</dd><dt>素材</dt><dd>${esc(r.assets)}</dd>
      <dt>文件</dt><dd>复制 ${esc(r.copied)} · 相同复用 ${esc(r.identical)} · 改名 ${esc(r.renamed)} · 跳过 ${esc(r.skipped)} · 共 ${fmtBytes(r.bytes)}</dd></dl>
      ${(r.failed || []).length ? `<p class="ai-fail">${ic('warn')}${r.failed.length} 个文件失败：</p><ul class="hist">${r.failed.map(f => `<li class="mono">${esc(f.path)} · ${esc(f.error)}</li>`).join('')}</ul>` : ''}
      ${(r.notes || []).map(n => `<p class="hint">${esc(n)}</p>`).join('')}<p class="hint">${XML_NOTE}</p></section>`;
  }
  function openAddToCol() {
    const ids = selTargets();
    const manual = S.cols.filter(c => c.kind === 'manual' && (!ui.col || c.id !== ui.col.id));
    dialog({
      title: `把 ${ids.length} 张加入集合`,
      body: `<div class="field"><label for="ac-col">已有集合</label><select id="ac-col" class="input"><option value="">新建 Manual 集合…</option>${manual.map(c => `<option value="${esc(c.id)}">${esc(c.name)}</option>`).join('')}</select></div>
        <div class="field"><label for="ac-name">新集合名称</label><input id="ac-name" class="input" maxlength="80"></div><p class="hint">加入集合不改变 Flag/Star，不移动原片。</p>`,
      actions: [{ label: '加入', value: 'ok', kind: 'primary' }],
      onSubmit: async (v, d) => {
        const cid = val(d, '#ac-col');
        if (cid) await api('POST', `/collections/${encodeURIComponent(cid)}/members`, { add: ids });
        else {
          const name = val(d, '#ac-name');
          if (!name) throw new Error('请填写新集合名称');
          await api('POST', '/collections', { name, kind: 'manual', asset_ids: ids, task_id: (ui.ws && ui.ws.taskId) || (ui.col && ui.col.task_id) || undefined });
        }
        await refreshCols();
        toast(`已加入 ${ids.length} 张`);
        pollNow();
      },
    });
  }
  async function rerender() { const ws = ui.ws, keep = ws && { current: ws.current, sel: ws.sel }; await route(true); if (keep && ui.ws) ui.ws.start = keep.current; }

  function showShortcuts() {
    const row = (k, t) => `<tr><td>${k.split(' ').map(x => `<kbd>${x}</kbd>`).join(' ')}</td><td>${t}</td></tr>`;
    dialog({
      title: '键盘快捷键', cls: 'small',
      body: `<table class="kbd-table">${row('P', '标记 Pick')}${row('X', '标记 Reject')}${row('U', '清除 Flag')}${row('1 2 3 4 5', '设置星级')}${row('0', '清除星级')}${row('← →', '上一张 / 下一张')}${row('↑ ↓', '上一行 / 下一行')}${row('空格', '放大查看')}${row('C', '比较已选 2 张')}${row('⌘/Ctrl Z', '撤销')}${row('⌘/Ctrl A', '全选当前网格')}${row('Esc', '关闭对话框 / 取消多选')}</table>
        <p class="hint">在输入框中打字时快捷键不会触发。多选时标记作用于全部已选素材。</p>`,
    });
  }

  // ---------- Toast ----------
  function toast(msg, opt) {
    opt = opt || {};
    const box = $('#toasts');
    const el = document.createElement('div');
    el.className = 'toast';
    const span = document.createElement('span');
    span.textContent = msg;
    el.append(span);
    if (opt.action) {
      const b = document.createElement('button');
      b.type = 'button'; b.className = 'toast-act'; b.textContent = opt.action.label;
      b.addEventListener('click', () => { el.remove(); opt.action.run(); });
      el.append(b);
    }
    box.append(el);
    while (box.children.length > 3) box.firstChild.remove();
    setTimeout(() => el.classList.add('out'), 5200);
    setTimeout(() => el.remove(), 5600);
  }

  // ---------- 路由 ----------
  const VIEWS = { tasks: vTasks, create: vCreate, task: vTask, review: vReview, collections: vCollections, deleted: vDeleted, library: vLibrary, settings: vSettings, species: vSpecies, 'species-one': vSpeciesOne };
  const NAV_OF = { tasks: 'tasks', create: 'tasks', task: 'tasks', review: 'collections', collections: 'collections', deleted: 'collections', library: 'library', settings: 'settings', species: 'species', 'species-one': 'species' };
  const TITLES = { tasks: '任务', create: '新建扫描任务', task: '任务', review: 'Review', collections: 'Collections', deleted: '已删除的集合', library: '全部素材', settings: '管理', species: '物种', 'species-one': '物种' };
  function parse() {
    const parts = location.hash.replace(/^#\/?/, '').split('/').map(decodeURIComponent);
    switch (parts[0]) {
      case 'new': return ['create', {}];
      case 'task': return ['task', { id: parts[1], tab: parts[2] || 'preview' }];
      case 'review': return ['review', { id: parts[1] }];
      case 'collections': return parts[1] === 'deleted' ? ['deleted', {}] : ['collections', {}];
      case 'library': return ['library', {}];
      case 'species': return parts[1] === 'task' ? ['species', { task: parts[2] }] : parts[1] ? ['species-one', { concept: parts[1] }] : ['species', {}];
      case 'settings': return ['settings', {}];
      case 'import': return ['import', { task: parts[1], plan: parts[2] }];
      case 'tasks': return ['tasks', { archived: parts[1] === 'archived' }];
      default: return null;
    }
  }
  async function route(keepPos) {
    const p = parse();
    if (!p) { location.replace(prefs.get('route', '#/tasks')); return; }
    const [view, params] = p;
    const token = ++ui.token;
    $$('dialog[open]').forEach(d => d.close());
    const keep = keepPos === true && ui.ws ? ui.ws.current : null;
    ui.view = view; ui.params = params; ui.ws = null; ui.col = null;
    let html;
    try { html = await VIEWS[view](params); } catch (e) {
      html = e.status === 404 ? notFound('没有找到这个对象', '#/tasks', '返回任务') : `<div class="page"><div class="empty big"><h1 tabindex="-1">无法载入</h1><p>${esc(errMsg(e))}</p><button type="button" class="btn" data-action="reload">重试</button></div></div>`;
      ui.ws = null;
    }
    if (token !== ui.token) return;
    setHTML(main, html);
    main.scrollTop = 0;
    prefs.set('route', location.hash);
    $$('.nav a').forEach(a => { if (a.dataset.nav === NAV_OF[view]) a.setAttribute('aria-current', 'page'); else a.removeAttribute('aria-current'); });
    document.title = `${TITLES[view]} · Gardener`;
    const h = $('h1', main);
    if (h && keepPos !== true) h.focus({ preventScroll: true });
    if (ui.ws) {
      if (keep) ui.ws.start = keep;
      renderInspector();
      observeSentinel(ui.ws);
      await loadMore(ui.ws);
    }
  }
  function observeSentinel(ws) {
    const s = $('#grid-sentinel'), root = $('#grid-scroll');
    if (!s || !('IntersectionObserver' in window)) return;
    new IntersectionObserver(es => { if (es.some(e => e.isIntersecting) && ui.ws === ws) loadMore(ws); }, { root, rootMargin: '400px' }).observe(s);
  }

  // ---------- 轮询 ----------
  async function refreshTasks() { S.tasks = await api('GET', '/tasks', null, { archived: 'all' }); }
  let polling = false;
  async function pollNow() {
    if (polling) return;
    polling = true;
    try {
      const [tasks, health] = await Promise.all([api('GET', '/tasks', null, { archived: 'all' }), api('GET', '/health')]);
      const prev = new Map(S.tasks.map(t => [t.id, (t.scan || {}).status]));
      S.tasks = tasks; S.health = health;
      if (S.down) { S.down = false; }
      tasks.forEach(t => { const was = prev.get(t.id), now = (t.scan || {}).status; if (was && was !== now && ['completed', 'partial', 'failed', 'stopped'].includes(now)) toast(`「${t.title}」${SCAN[now][0]}`); });
      patchBinds();
      if (ui.ws) await pollWs(ui.ws);
      if (ui.view === 'collections' || (ui.view === 'task' && ui.params.tab === 'collections')) {
        await refreshCols();
        if (JSON.stringify(scopeCols()) !== ui.colKey && safeForStructure()) { const area = $('#col-area'); if (area) setHTML(area, colAreaHTML()); }
      }
      if (ui.view === 'review') await refreshCol();
    } catch (e) {
      if (e.status === 0) { S.down = true; patchBinds('svc'); }
    } finally { polling = false; }
  }

  // ---------- 动作 ----------
  async function taskCall(id, action, body, msg) {
    try {
      const r = await api('POST', `/tasks/${encodeURIComponent(id)}/${action}`, body || {});
      toast(msg);
      await pollNow();
      return r;
    } catch (e) { toast(errMsg(e)); return null; }
  }
  const ACTIONS = {
    reload: () => route(),
    'task-pause': el => taskCall(el.dataset.id, 'pause', null, '已暂停；当前块结束后释放扫描名额'),
    'task-resume': el => taskCall(el.dataset.id, 'resume', null, '已重新排队'),
    'task-stop': el => { const t = taskById(el.dataset.id); confirmDialog('停止扫描？', `<p>停止「${esc(t ? t.title : '')}」。已发现的素材、预览和分析结果都会保留，人工标记不受影响。</p><p class="hint">停止后如实显示为「已停止」，不会冒称完成。</p>`, '停止扫描', 'danger', () => taskCall(el.dataset.id, 'stop', null, '已停止，结果保留')); },
    'task-verify': async el => { const t = await taskCall(el.dataset.id, 'verify-sources', null, '已核对来源'); if (t && t.sources) { const off = t.sources.filter(s => !s.online).length; toast(off ? `${off} 个来源仍离线；记录和已有预览保留` : '全部来源在线'); route(true); } },
    'task-archive': el => taskCall(el.dataset.id, 'archive', null, '已归档；素材、集合与历史仍可查询').then(() => route(true)),
    'task-unarchive': el => taskCall(el.dataset.id, 'unarchive', null, '已取消归档').then(() => route(true)),
    'task-retry': el => taskCall(el.dataset.id, 'retry', {}, '失败项已加入重试运行'),
    'retry-asset': el => { const a = A(el.dataset.id); const tid = a && a.task_ids && a.task_ids[0]; if (tid) taskCall(tid, 'retry', { asset_ids: [a.id] }, '已为此项排队重试'); },
    'split-version': async el => {
      try {
        await api('POST', `/assets/${encodeURIComponent(el.dataset.id)}/split-version`);
        S.details.clear();
        toast('已分开：它从此单独出现，不再自动合并；在任务中「重试」可补做它的分析');
        renderInspector();
      } catch (e) { toast(errMsg(e)); }
    },
    'id-accept': el => identify(el, { accept: true }, '已确认'),
    'id-unsure': el => identify(el, { level: 'unconfirmed' }, '已标为不确定'),
    'id-change': el => {
      const d = dialog({
        title: '改为哪个物种', focus: '#idq',
        body: '<div class="field"><label for="idq">学名、英文名、中文名或旧名</label><input id="idq" class="input" maxlength="80" autocomplete="off"></div><ul class="id-hits" id="id-hits" aria-live="polite"></ul><p class="hint">中文名来自你放在数据目录的 names-zh.csv；没有的物种用学名或英文名查。</p>',
        actions: [],
      });
      let seq = 0;
      $('#idq', d).addEventListener('input', async e => {
        const q = e.target.value.trim(), my = ++seq;
        const hits = q ? await api('GET', '/names', null, { q, limit: 12 }).catch(() => []) : [];
        if (my !== seq) return;
        $('#id-hits', d).innerHTML = hits.map(h => `<li><button type="button" class="btn sm ghost" data-action="id-pick" data-id="${esc(el.dataset.id)}" data-subject="${esc(el.dataset.subject)}" data-concept="${esc(h.concept)}" data-taxon="${esc(h.scientific || '')}" data-common="${esc(h.common || '')}"><b>${esc(h.zh || h.common || h.scientific)}</b> <i>${esc(h.scientific)}</i>${h.photos ? ` <small class="hint">库中 ${h.photos} 张</small>` : ''}${h.matched !== h.scientific && h.matched !== h.common && h.matched !== h.zh ? ` <small class="hint">（${esc(h.matched)}）</small>` : ''}</button></li>`).join('') || (q ? '<li class="hint">没有找到</li>' : '');
      });
    },
    'id-pick': el => {
      const dd = el.closest('dialog'); if (dd) dd.close();
      identify(el, { level: 'species', concept: el.dataset.concept, taxon: el.dataset.taxon, common: el.dataset.common || null }, `已改为 ${el.dataset.common || el.dataset.taxon}`);
    },
    'sp-kind': el => { ui.spKind = el.dataset.kind; rerender(); },
    'lib-preset': el => { ui.lib = { [el.dataset.filter]: el.dataset.value }; location.hash = '#/library'; },
    'sp-cover': async () => {
      const ws = ui.ws, k = ui.sp;
      if (!ws || !ws.current || !k) { toast('先在网格里选一张照片'); return; }
      try { await api('PATCH', `/species/${encodeURIComponent(k.concept)}`, { cover_asset_id: ws.current }); toast('已设为封面'); } catch (e) { toast(errMsg(e)); }
    },
    'sp-hide': async () => {
      const k = ui.sp; if (!k) return;
      try { await api('PATCH', `/species/${encodeURIComponent(k.concept)}`, { hidden: !k.hidden }); toast(k.hidden ? '已在物种页显示' : '已在物种页隐藏（照片与结论不变）'); rerender(); } catch (e) { toast(errMsg(e)); }
    },
    'name-face': el => {
      const { id, face, name } = el.dataset;
      const save = async n => {
        await api('POST', `/assets/${encodeURIComponent(id)}/faces/${encodeURIComponent(face)}`, { name: n });
        S.details.clear();
        await refreshCols();
        toast(`已记住「${n}」：这个人的照片会归入同名分组`);
        renderInspector();
      };
      if (name) { save(name).catch(e => toast(errMsg(e))); return; }
      const d = dialog({
        title: '给这张脸命名', focus: '#nf-name',
        body: '<div class="field"><label for="nf-name">这个人叫什么</label><input id="nf-name" class="input" maxlength="60" list="nf-people" autocomplete="off"><datalist id="nf-people"></datalist></div><p class="hint">选已有的名字就是同一个人；新名字会新建一个人。以后的扫描会直接把认出的照片加进去。</p>',
        actions: [{ label: '保存', value: 'ok', kind: 'primary' }],
        onSubmit: async (v, dd) => { const n = val(dd, '#nf-name'); if (!n) throw new Error('名字不能为空'); await save(n); },
      });
      peopleOptions($('#nf-people', d));
    },
    'unname-face': async el => {
      try {
        await api('DELETE', `/assets/${encodeURIComponent(el.dataset.id)}/faces/${encodeURIComponent(el.dataset.face)}`);
        S.details.clear();
        await refreshCols();
        toast('已取消这张脸的命名');
        renderInspector();
      } catch (e) { toast(errMsg(e)); }
    },
    'task-append': el => dialog({
      title: '追加来源目录', focus: '#ap-paths',
      body: '<div class="field"><label for="ap-paths">目录（每行一个绝对路径）</label><textarea id="ap-paths" class="input mono" rows="3" placeholder="/Volumes/SD_CARD/DCIM"></textarea></div><p class="hint">追加会开始一次新运行，复用已有素材的结果，不改写过去的运行。</p>',
      actions: [{ label: '追加并排队', value: 'ok', kind: 'primary' }],
      onSubmit: async (v, d) => {
        const paths = val(d, '#ap-paths').split('\n').map(s => s.trim()).filter(Boolean);
        if (!paths.length) throw new Error('至少填写一个目录');
        const bad = paths.find(p => !ABS.test(p));
        if (bad) throw new Error(`不是绝对路径：${bad}`);
        await api('POST', `/tasks/${encodeURIComponent(el.dataset.id)}/sources`, { paths });
        toast('已追加来源并排队');
        await rerender();
      },
    }),
    'task-rescan': el => {
      const t = taskById(el.dataset.id);
      const sel = ui.ws && ui.ws.taskId === el.dataset.id ? Array.from(ui.ws.sel) : [];
      dialog({
        title: '重新分析',
        body: `<p class="hint">用于新版本小批试跑：可只选部分能力与当前选中的素材。新结果作为新一轮运行保存，旧结果与人工判断不被覆盖。</p>${capToggles(t ? t.capabilities : [], 'rs-cap')}
          <fieldset class="fs"><legend>素材范围</legend><label class="radio-row"><input type="radio" name="rs-scope" value="all" checked><span>本任务全部素材</span></label><label class="radio-row"><input type="radio" name="rs-scope" value="sel" ${sel.length ? '' : 'disabled'}><span>当前选中（${sel.length} 张）</span></label></fieldset>`,
        actions: [{ label: '排队重新分析', value: 'ok', kind: 'primary' }],
        onSubmit: async (v, d) => {
          const caps = $$('input[name=rs-cap]:checked', d).map(i => i.value);
          if (!caps.length) throw new Error('至少选择一个能力');
          const body = { capabilities: caps };
          if ($('input[name=rs-scope]:checked', d).value === 'sel') body.asset_ids = sel;
          await api('POST', `/tasks/${encodeURIComponent(el.dataset.id)}/rescan`, body);
          toast('已排队重新分析');
          pollNow();
        },
      });
    },
    'restore-batch': el => undoBatch(el.dataset.id, el.dataset.label).then(() => { S.details.clear(); renderInspector(); }),
    'add-path': () => addPath(),
    'rm-path': el => { ui.draft.sources.splice(Number(el.dataset.i), 1); $('#path-chips').innerHTML = pathChipsHTML(ui.draft); $('#path-input').focus(); },
    'toggle-sel': el => {
      const ws = ui.ws, id = el.dataset.id;
      if (ws.sel.has(id)) ws.sel.delete(id); else ws.sel.add(id);
      if (ws.sel.has(id)) ws.current = id;
      ws.anchor = id;
      applySel(); renderInspector();
    },
    'clear-sel': () => { const ws = ui.ws; ws.sel = new Set(ws.current ? [ws.current] : []); applySel(); },
    'ws-filter': el => { ui.ws.filter = el.dataset.f; recompute(); },
    burst: el => { const ws = ui.ws, b = el.dataset.id; if (ws.expanded.has(b)) ws.expanded.delete(b); else ws.expanded.add(b); recompute(); },
    'burst-collapse': () => { const ws = ui.ws; ws.collapse = !ws.collapse; prefs.set('burst', ws.collapse ? '1' : '0'); const b = $('[data-action="burst-collapse"]'); b.setAttribute('aria-pressed', String(ws.collapse)); b.textContent = ws.collapse ? '连拍已折叠' : '连拍已展开'; recompute(); },
    'grid-size': () => { const n = prefs.get('grid', 'm') === 'm' ? 'l' : 'm'; prefs.set('grid', n); const w = $('.ws'); w.classList.remove('grid-m', 'grid-l'); w.classList.add('grid-' + n); },
    prev: () => move(-1),
    next: () => move(1),
    lightbox: () => openLightbox(),
    zoom: () => { ui.zoom = !ui.zoom; renderInspector(); renderLightbox(); },
    compare: () => openCompare(),
    reveal: async el => { try { await api('POST', `/assets/${encodeURIComponent(el.dataset.id)}/reveal`); toast('已在 Finder 中显示'); } catch (e) { toast(errMsg(e)); } },
    flag: el => { const v = el.dataset.v || null; doMark([el.dataset.id], { flag: v }, v === 'pick' ? 'Pick' : v === 'reject' ? 'Reject' : '清除 Flag'); },
    star: el => { const id = el.dataset.id, v = Number(el.dataset.v); const n = A(id).stars === v ? 0 : v; doMark([id], { stars: n }, n ? `${n} 星` : '清除星级'); },
    batch: el => { const v = el.dataset.v || null; doMark(Array.from(ui.ws.sel), { flag: v }, `批量${v === 'pick' ? ' Pick' : v === 'reject' ? ' Reject' : '清除 Flag'}`); },
    undo: () => doUndo(),
    shortcuts: () => showShortcuts(),
    'edit-col': el => openEditCol(el.dataset.id),
    'complete-col': () => openComplete(),
    'zero-col': () => openZero(),
    'reopen-col': async () => { try { await api('POST', `/collections/${encodeURIComponent(ui.col.id)}/reopen`); toast(`已重新打开「${ui.col.name}」，人工标记不变`); await refreshCol(); await refreshCols(); } catch (e) { toast(errMsg(e)); } },
    'next-col': () => { const n = nextCol(); if (!n) toast('本任务没有其他未完成的集合'); else location.hash = '#/review/' + n.id; },
    'delete-col': () => { const c = ui.col; confirmDialog('删除集合？', `<p>删除「${esc(c.name)}」。只去掉集合和成员关系；素材与全局 Flag/Star 不变，可在「已删除」中恢复。</p>`, '删除集合', 'danger', async () => { await api('DELETE', `/collections/${encodeURIComponent(c.id)}`); toast(`已删除「${c.name}」`, { action: { label: '恢复', run: () => api('POST', `/collections/${encodeURIComponent(c.id)}/restore`).then(() => { location.hash = '#/review/' + c.id; }) } }); location.hash = c.task_id ? `#/task/${c.task_id}/collections` : '#/collections'; }); },
    'restore-col': async el => { try { await api('POST', `/collections/${encodeURIComponent(el.dataset.id)}/restore`); toast('已恢复'); route(true); } catch (e) { toast(errMsg(e)); } },
    'save-manual': () => dialog({
      title: '另存为 Manual 集合', focus: '#sm-name',
      body: `<div class="field"><label for="sm-name">名称</label><input id="sm-name" class="input" maxlength="80" value="${esc(ui.col.name)}（固定）"></div><p class="hint">按当前规则结果固定成员；之后规则变化不影响新集合。</p>`,
      actions: [{ label: '保存', value: 'ok', kind: 'primary' }],
      onSubmit: async (v, d) => { const c = await api('POST', `/collections/${encodeURIComponent(ui.col.id)}/save-as-manual`, { name: val(d, '#sm-name') }); toast('已另存'); location.hash = '#/review/' + c.id; },
    }),
    'split-col': () => { const ids = Array.from(ui.ws.sel); dialog({
      title: `把 ${ids.length} 张拆分为新集合`, focus: '#sp-name',
      body: '<div class="field"><label for="sp-name">新集合名称</label><input id="sp-name" class="input" maxlength="80"></div><p class="hint">这些素材移出本组、进入新的 Manual 集合；标记不变。</p>',
      actions: [{ label: '拆分', value: 'ok', kind: 'primary' }],
      onSubmit: async (v, d) => { const name = val(d, '#sp-name'); if (!name) throw new Error('请填写名称'); const c = await api('POST', `/collections/${encodeURIComponent(ui.col.id)}/split`, { asset_ids: ids, name }); toast(`已拆分为「${c.name}」`, { action: { label: '打开', run: () => { location.hash = '#/review/' + c.id; } } }); await rerender(); },
    }); },
    'remove-from-col': () => { const ids = Array.from(ui.ws.sel); confirmDialog('移出本组？', `<p>把 ${ids.length} 张移出「${esc(ui.col.name)}」。</p><p class="hint">不改变 Flag/Star，不移动原片；之后自动重组也不会把它们加回。</p>`, '移出', 'primary', async () => { await api('POST', `/collections/${encodeURIComponent(ui.col.id)}/members`, { remove: ids }); toast(`已移出 ${ids.length} 张`); await rerender(); }); },
    'add-to-col': () => openAddToCol(),
    export: () => openExport(),
    'col-filter': el => { ui.colFilter = el.dataset.f; route(true); },
    'col-kind': el => { ui.colKind = el.dataset.f; route(true); },
    'new-col': el => dialog({
      title: '新建 Manual 集合', focus: '#nc-name',
      body: '<div class="field"><label for="nc-name">名称</label><input id="nc-name" class="input" maxlength="80"></div><div class="field"><label for="nc-desc">描述</label><textarea id="nc-desc" class="input" rows="2"></textarea></div><p class="hint">之后在 Review 或全部素材中多选，用「加入集合…」添加成员。Smart Collection 从全部素材的筛选保存。</p>',
      actions: [{ label: '创建', value: 'ok', kind: 'primary' }],
      onSubmit: async (v, d) => { const name = val(d, '#nc-name'); if (!name) throw new Error('请填写名称'); await api('POST', '/collections', { name, description: val(d, '#nc-desc'), kind: 'manual', task_id: el.dataset.id || undefined }); toast(`已创建「${name}」`); await rerender(); },
    }),
    'merge-cols': () => { const cols = scopeCols(); dialog({
      title: '合并集合',
      body: `<fieldset class="fs merge-list"><legend>选择至少 2 个集合</legend>${cols.map(c => `<label class="radio-row"><input type="checkbox" name="mg" value="${esc(c.id)}"><span>${esc(c.name)}<small>${c.size} 张 · ${esc(colKindText(c))}</small></span></label>`).join('')}</fieldset><div class="field"><label for="mg-name">新集合名称</label><input id="mg-name" class="input" maxlength="80"></div><p class="hint">合并生成新的 Manual 集合，原集合软删除（可恢复）；标记不变。</p>`,
      actions: [{ label: '合并', value: 'ok', kind: 'primary' }],
      onSubmit: async (v, d) => { const ids = $$('input[name=mg]:checked', d).map(i => i.value); if (ids.length < 2) throw new Error('至少选择 2 个集合'); const c = await api('POST', `/collections/${encodeURIComponent(ids[0])}/merge`, { ids: ids.slice(1), name: val(d, '#mg-name') || undefined }); toast(`已合并为「${c.name}」`); await rerender(); },
    }); },
    coverage: async el => {
      try {
        const rows = await api('GET', '/coverage', null, { task: el.dataset.id || undefined });
        const ST = { covered: ['已覆盖', 'ok'], zeroed: ['明确不留', 'muted'], uncovered: ['未覆盖', 'fail'] };
        dialog({ title: '覆盖检查', cls: 'wide', body: `<p class="hint">只检查已扫描素材形成的事件/地点/场景/物种集合；没有拍到的地点系统无从得知。「未覆盖」= 尚无人工 Pick 且未明确不留，不强制每组保留一张。</p>
          <table class="tbl"><thead><tr><th>集合</th><th>类型</th><th>张数</th><th>Pick</th><th>状态</th></tr></thead><tbody>${rows.map(r => `<tr><td><a href="#/review/${esc(r.collection_id)}">${esc(r.name)}</a></td><td>${esc(GROUP[r.group_kind] || r.group_kind)}</td><td>${esc(r.size)}</td><td>${esc(r.picks)}</td><td>${pill((ST[r.state] || [r.state])[0], (ST[r.state] || [0, 'muted'])[1])}</td></tr>`).join('') || '<tr><td colspan="5" class="hint">还没有可检查的集合。</td></tr>'}</tbody></table>` });
      } catch (e) { toast(errMsg(e)); }
    },
    'lib-clear': () => { ui.lib = {}; route(true); },
    'save-smart': () => { const rule = libRule(); dialog({
      title: '保存为 Smart Collection', focus: '#ss-name',
      body: `<div class="field"><label for="ss-name">名称</label><input id="ss-name" class="input" maxlength="80"></div><p class="hint">规则：<span class="mono">${esc(JSON.stringify(rule))}</span>。成员按规则实时求值；完成审阅时记录快照，之后新增显示「新增待审」。</p>`,
      actions: [{ label: '保存', value: 'ok', kind: 'primary' }],
      onSubmit: async (v, d) => { const name = val(d, '#ss-name'); if (!name) throw new Error('请填写名称'); const c = await api('POST', '/collections', { name, kind: 'smart', rule }); await refreshCols(); toast(`已保存「${c.name}」`, { action: { label: '打开', run: () => { location.hash = '#/review/' + c.id; } } }); },
    }); },
    'caps-check': async el => { el.disabled = true; el.textContent = '检查中…（可能需要几分钟）'; try { S.caps = await api('POST', '/capabilities/check', {}); toast('模型检查完成'); route(true); } catch (e) { toast(errMsg(e)); el.disabled = false; el.textContent = '检查模型'; } },
    'preset-new': () => presetDialog(null, '新建预设'),
    'preset-saveas': el => presetDialog(S.presets.find(p => p.id === el.dataset.id), '另存为新预设', true),
    'preset-edit': el => presetDialog(S.presets.find(p => p.id === el.dataset.id), '编辑预设'),
    'preset-delete': el => { const p = S.presets.find(x => x.id === el.dataset.id); confirmDialog('删除预设？', `<p>删除「${esc(p.name)}」。已创建任务保存的是配置快照，不受影响。</p>`, '删除', 'danger', async () => { await api('DELETE', `/presets/${encodeURIComponent(p.id)}`); toast('已删除预设'); route(true); }); },
    'tag-new': () => tagDialog(null),
    'tag-edit': el => tagDialog(S.tags.find(t => t.id === el.dataset.id)),
    'tag-merge': el => { const t = S.tags.find(x => x.id === el.dataset.id); dialog({
      title: `合并「${t.name}」到…`,
      body: `<div class="field"><label for="tm-into">目标标签</label><select id="tm-into" class="input">${S.tags.filter(x => x.id !== t.id && x.active).map(x => `<option value="${esc(x.id)}">${esc(x.name)}</option>`).join('')}</select></div><p class="hint">合并保留历史；原标签停用并指向目标。</p>`,
      actions: [{ label: '合并', value: 'ok', kind: 'primary' }],
      onSubmit: async (v, d) => { await api('POST', `/tags/${encodeURIComponent(t.id)}/merge`, { into: val(d, '#tm-into') }); toast('已合并'); route(true); },
    }); },
    'tag-split': el => { const t = S.tags.find(x => x.id === el.dataset.id); dialog({
      title: `拆分「${t.name}」`, focus: '#ts-names',
      body: `<div class="field"><label for="ts-names">新子标签（每行一个）</label><textarea id="ts-names" class="input" rows="3"></textarea></div><label class="check-row"><input type="checkbox" id="ts-deact"> 拆分后停用原标签</label><p class="hint">新标签作为「${esc(t.name)}」的子标签；成员可在素材上再人工指定。拆分记入历史，人工与 AI 来源分开保留。</p>`,
      actions: [{ label: '拆分', value: 'ok', kind: 'primary' }],
      onSubmit: async (v, d) => { const names = val(d, '#ts-names').split('\n').map(s => s.trim()).filter(Boolean); if (!names.length) throw new Error('至少填写一个新标签'); await api('POST', `/tags/${encodeURIComponent(t.id)}/split`, { names, deactivate_source: $('#ts-deact', d).checked }); toast(`已拆分出 ${names.length} 个标签`); route(true); },
    }); },
    'clear-cache': () => confirmDialog('清理预览缓存？', '<p>只删除预览缓存文件以释放空间。</p><p class="hint">任务、素材、集合、人工标记与 AI 建议等整理记录全部保留；原片在线时，预览会在需要时重新生成，离线素材在原片恢复前将没有预览。</p>', '清理缓存', 'danger', async () => { const r = await api('POST', '/storage/clear-cache', {}); toast(`已清理 ${r.removed_files} 个文件，释放 ${fmtBytes(r.freed_bytes)}`); route(true); }),
    skip: () => { const h = $('h1', main); (h || main).focus(); },
  };
  // ---------- 完成与导入（契约修订 5D；PRD §06 正式导入、§09 Review 完成与导入确认） ----------
  // #/import/<task>：填写目标；#/import/<task>/<plan>：确认页（用途计数、文件与容量、重复、未完成项、导入 N 张）与结果。
  const IMPORT_USES = [
    ['edit', '选入修图', '复制入库；可交接 Lightroom'], ['record', '保留记录', '复制入库；补充物种档案'],
    ['pending', '待定', '默认一并复制；继续待整理'], ['reject', '淘汰', '不从新批次复制；来源不在此步自动删除'],
  ];
  const IMPORT_ST = { planned: '待确认', queued: '排队中', running: '导入中', completed: '导入完成', partial: '部分完成 · 有失败项', failed: '导入失败' };
  const imp = { task: null, plan: null };
  async function vImport(p) {
    const t = await api('GET', `/tasks/${encodeURIComponent(p.task)}`);
    imp.task = t;
    imp.plan = p.plan ? await api('GET', `/imports/${encodeURIComponent(p.plan)}`) : null;
    const last = imp.plan || (await api('GET', '/imports', null, { task: t.id }))[0] || null;
    return `<div class="page"><div class="narrow">
      <nav class="crumbs" aria-label="位置"><a href="#/tasks">任务</a><span aria-hidden="true">/</span><a href="#/task/${esc(t.id)}/collections">${esc(t.title)}</a><span aria-hidden="true">/</span><span>完成与导入</span></nav>
      <header class="page-head"><div><h1 tabindex="-1">完成与导入</h1><p class="lede">这次保留多少、准备修多少、还有多少待定、将复制哪些文件。导入只读取来源，复制后逐个校验字节与 sha256 再入库；成功导入后仍可继续 Review。</p></div></header>
      <div id="imp-body">${imp.plan ? importPlanHTML(imp.plan) : importFormHTML(last)}</div></div></div>`;
  }
  function importFormHTML(last) {
    return `<section class="form-sec">
      <div class="field"><label for="imp-dest">目标根目录</label><input id="imp-dest" class="input mono" autocomplete="off" placeholder="/Volumes/Photos/Wildlife" value="${esc(last ? last.dest_root : '')}"><p class="hint">其下新建 YYYY-MM-DD_地点 目录；跨日拍摄用日期范围。</p></div>
      <div class="field"><label for="imp-place">地点</label><input id="imp-place" class="input" autocomplete="off" placeholder="未填写则为“未标注地点”" value="${esc(last && last.place ? last.place : '')}"></div>
      <label class="switch-row boxed"><span class="sw-text"><b>待定照片一起导入</b><small>默认开启，避免尚未决定就失去卡上的素材</small></span><input type="checkbox" role="switch" class="switch" id="imp-pending-new" ${last && !last.include_pending ? '' : 'checked'}></label>
      <p class="field-err" id="imp-err" aria-live="polite"></p>
      <button type="button" class="btn primary" data-action="import-plan">${ic('check')}生成导入计划</button></section>`;
  }
  function importPlanHTML(p) {
    const u = p.uses || {}, nc = p.not_copied || {}, d = p.duplicates || {}, un = p.unfinished || {};
    const take = id => id === 'reject' ? false : id === 'pending' ? p.include_pending : true;
    const rows = IMPORT_USES.map(([id, label, how]) => `<tr><th scope="row">${label}</th><td>${u[id] || 0}</td><td>${id === 'pending' && !p.include_pending ? '本次不复制；仍仅在来源中' : how}</td><td>${take(id) ? '复制' : '—'}</td></tr>`).join('');
    const warn = s => `<p class="note warn block">${ic('warn')}${s}</p>`;
    const notes = [
      p.stale ? warn('生成计划后又有决定变化；请重新生成计划，再导入。') : '',
      !p.include_pending && nc.pending ? warn(`${nc.pending} 张待定照片不导入，它们仍仅在来源中。`) : '',
      un.offline ? warn(`${un.offline} 张有文件离线或缺失：导入时会列为失败，来源恢复后可重试。`) : '',
      un.scan_active ? warn('扫描仍在进行；新发现的素材不在本计划中，扫描结束后可再生成一次。') : '',
      un.analysis_pending ? `<p class="note block">${un.analysis_pending} 张分析尚未完成；不影响导入，物种可在入库后继续确认。</p>` : '',
      p.enough_space ? '' : warn(`目标磁盘空间不足：需要 ${fmtBytes(p.needed_bytes)}，可用 ${fmtBytes(p.free_bytes)}。`),
    ].join('');
    const busy = ['queued', 'running'].includes(p.status);
    const runnable = ['planned', 'partial', 'failed'].includes(p.status);
    const label = p.status === 'planned' ? `导入 ${p.assets} 张` : '继续导入未完成项';
    const dupList = (d.items || []).length ? `<details><summary>精确重复与已有副本（${d.items.length}）</summary><ul class="hist">${d.items.map(x => `<li class="mono">${esc(x.path)} → ${esc(x.existing)}</li>`).join('')}</ul></details>` : '';
    const renList = (p.renamed.items || []).length ? `<details><summary>同名改为批次后缀（${p.renamed.files} 个文件）</summary><ul class="hist">${p.renamed.items.map(x => `<li class="mono">${esc(x.path)} → ${esc(x.to)}</li>`).join('')}</ul></details>` : '';
    return `${notes}
      <section class="card"><h3>本次决定<span class="sub">照片数，不等于文件数</span></h3>
        <table class="imp-uses"><thead><tr><th>用途</th><th>照片</th><th>默认处理</th><th>本次</th></tr></thead><tbody>${rows}</tbody>
        <tfoot><tr><th scope="row">总计</th><td>${u.total || 0}</td><td colspan="2">导入 ${p.assets} 张${nc.versions ? ` · 同一拍摄的其他版本 ${nc.versions} 张不另复制` : ''}</td></tr></tfoot></table></section>
      <section class="card"><h3>文件与目标</h3><dl class="meta imp-meta">
        <dt>文件</dt><dd>${p.files} 个（RAW、同拍 JPEG 与 sidecar ${p.sidecars} 个一起复制）· ${fmtBytes(p.bytes)}</dd>
        <dt>目标</dt><dd class="mono">${esc(p.dest_dir)}</dd>
        <dt>容量</dt><dd>需复制 ${fmtBytes(p.needed_bytes)} · 可用 ${fmtBytes(p.free_bytes)}</dd>
        <dt>重复</dt><dd>目标已有相同文件 ${d.identical || 0} · 之前已导入 ${d.reuse || 0} · 同内容已在库中 ${d.duplicate || 0}（不再复制；连拍相似不算重复）</dd>
        <dt>同名</dt><dd>${p.renamed.files ? `${p.renamed.files} 个文件加批次后缀 _B${esc(p.seq)}，整组同名，不覆盖已有文件` : '无冲突'}</dd></dl>${dupList}${renList}</section>
      ${p.status === 'planned' ? `<label class="switch-row boxed"><span class="sw-text"><b>待定照片一起导入</b><small>关闭后待定照片仍仅在来源中</small></span><input type="checkbox" role="switch" class="switch" id="imp-pending" ${p.include_pending ? 'checked' : ''}></label>` : ''}
      ${p.status === 'planned' ? '' : importResultHTML(p)}
      <footer class="form-foot"><a class="btn ghost" href="#/task/${esc(p.task_id)}/collections">继续 Review</a>
        ${runnable ? `<button type="button" class="btn primary" data-action="import-run" data-id="${esc(p.id)}" ${p.enough_space ? '' : 'disabled'}>${ic('download')}${label}</button>` : ''}
        ${busy ? `<span class="note">${IMPORT_ST[p.status]}…</span>` : ''}</footer>`;
  }
  function importResultHTML(p) {
    const g = p.progress || {}, nc = p.not_copied || {};
    const fails = p.failed || [];
    return `<section class="card report"><h3>${esc(IMPORT_ST[p.status] || p.status)}<span class="sub">批次 ${esc(p.seq)}</span></h3>
      ${bar(pct(g.bytes_done, g.bytes_total), p.status === 'running' ? 'st-running' : '')}
      <dl class="meta imp-meta"><dt>入库</dt><dd>${g.assets_done}/${g.assets_total} 张 · ${g.files_done}/${g.files_total} 个文件已校验 · ${fmtBytes(g.bytes_done)}</dd>
        <dt>未复制</dt><dd>淘汰 ${nc.reject || 0} 张${nc.pending ? ` · 待定 ${nc.pending} 张` : ''}；来源未做任何改动</dd>
        ${p.interruptions ? `<dt>中断</dt><dd>${p.interruptions} 次，已从未完成项继续</dd>` : ''}</dl>
      ${fails.length ? `<p class="ai-fail">${ic('warn')}${g.files_failed} 个文件失败（部分完成，不是成功）：</p><ul class="hist">${fails.map(f => `<li class="mono">${esc(f.path)} · ${esc(f.error)}</li>`).join('')}</ul>` : ''}
      ${p.status === 'completed' ? '<p class="hint">下一步：继续 Review；待定照片改为选入修图时只更新用途，不移动已导入的文件。</p>' : ''}</section>`;
  }
  async function importReplan(task, body) {
    const p = await api('POST', `/tasks/${encodeURIComponent(task)}/import-plan`, body);
    location.hash = `#/import/${encodeURIComponent(task)}/${encodeURIComponent(p.id)}`;
  }
  Object.assign(VIEWS, { import: vImport });
  Object.assign(NAV_OF, { import: 'tasks' });
  Object.assign(TITLES, { import: '完成与导入' });
  Object.assign(ACTIONS, {
    'import-plan': async () => {
      const dest = $('#imp-dest').value.trim();
      if (!dest) { $('#imp-err').textContent = '请填写目标根目录（绝对路径）'; return; }
      try { await importReplan(imp.task.id, { dest_root: dest, place: $('#imp-place').value.trim(), include_pending: $('#imp-pending-new').checked }); } catch (e) { $('#imp-err').textContent = errMsg(e); }
    },
    'import-run': async el => {
      el.disabled = true;
      try { imp.plan = await api('POST', `/imports/${encodeURIComponent(el.dataset.id)}/run`); setHTML($('#imp-body'), importPlanHTML(imp.plan)); toast('已开始导入；可以继续 Review'); } catch (e) { toast(errMsg(e)); el.disabled = false; }
    },
  });
  document.addEventListener('change', async e => {
    if (e.target.id !== 'imp-pending' || !imp.plan) return;
    const p = imp.plan;
    try { await importReplan(p.task_id, { dest_root: p.dest_root, place: p.place || '', include_pending: e.target.checked }); } catch (err) { toast(errMsg(err)); }
  });
  setInterval(async () => {
    if (ui.view !== 'import' || !imp.plan || !['queued', 'running'].includes(imp.plan.status)) return;
    try { imp.plan = await api('GET', `/imports/${encodeURIComponent(imp.plan.id)}`); const box = $('#imp-body'); if (box) setHTML(box, importPlanHTML(imp.plan)); } catch (e) { /* 下次轮询再试 */ }
  }, 2000);
  // ---------- 完成与导入 · 结束 ----------
  function presetDialog(p, title, saveAs) {
    const sel = p ? p.capabilities : ['meta'];
    dialog({
      title, focus: '#ps-name',
      body: `<div class="field"><label for="ps-name">名称</label><input id="ps-name" class="input" maxlength="40" value="${esc(p ? (saveAs ? p.name + '（副本）' : p.name) : '')}"></div>${capToggles(expandCaps(sel), 'ps-cap')}`,
      actions: [{ label: '保存', value: 'ok', kind: 'primary' }],
      onSubmit: async (v, d) => {
        const name = val(d, '#ps-name');
        if (!name) throw new Error('请填写名称');
        const capabilities = expandCaps($$('input[name=ps-cap]:checked', d).map(i => i.value));
        if (p && !saveAs) await api('PUT', `/presets/${encodeURIComponent(p.id)}`, { name, capabilities });
        else await api('POST', '/presets', { name, capabilities });
        toast('预设已保存');
        route(true);
      },
    });
  }
  function tagDialog(t) {
    const others = S.tags.filter(x => !t || x.id !== t.id);
    dialog({
      title: t ? `编辑标签「${t.name}」` : '新建标签', focus: '#tg-name',
      body: `<div class="field"><label for="tg-name">名称</label><input id="tg-name" class="input" maxlength="60" value="${esc(t ? t.name : '')}"></div>
        ${t ? `<div class="field"><label for="tg-alias">别名（逗号分隔）</label><input id="tg-alias" class="input" value="${esc((t.aliases || []).join(', '))}"></div>` : ''}
        <div class="field"><label for="tg-parent">上级</label><select id="tg-parent" class="input"><option value="">无</option>${others.map(x => `<option value="${esc(x.id)}" ${t && t.parent_id === x.id ? 'selected' : ''}>${esc(x.name)}</option>`).join('')}</select></div>
        ${t ? `<label class="check-row"><input type="checkbox" id="tg-active" ${t.active ? 'checked' : ''}> 启用</label>` : ''}<p class="hint">修改保留历史。</p>`,
      actions: [{ label: '保存', value: 'ok', kind: 'primary' }],
      onSubmit: async (v, d) => {
        const name = val(d, '#tg-name');
        if (!name) throw new Error('请填写名称');
        const parent_id = val(d, '#tg-parent') || null;
        if (!t) await api('POST', '/tags', { name, parent_id });
        else await api('PATCH', `/tags/${encodeURIComponent(t.id)}`, { name, parent_id, aliases: val(d, '#tg-alias').split(/[,，]/).map(s => s.trim()).filter(Boolean), active: $('#tg-active', d).checked });
        toast('标签已保存');
        route(true);
      },
    });
  }

  document.addEventListener('click', e => {
    const act = e.target.closest('[data-action]');
    if (act) {
      if (act.disabled) return;
      const fn = ACTIONS[act.dataset.action];
      if (fn) { e.preventDefault(); fn(act, e); }
      return;
    }
    const tl = e.target.closest('.tile[data-asset]');
    if (!tl || !ui.ws) return;
    const id = tl.dataset.asset, ws = ui.ws;
    if (e.metaKey || e.ctrlKey) {
      if (ws.sel.has(id) && ws.sel.size > 1) ws.sel.delete(id); else ws.sel.add(id);
      ws.anchor = id;
      setCurrent(id, { keepSel: true });
    } else if (e.shiftKey && ws.anchor && ws.ids.includes(ws.anchor)) {
      const a = ws.ids.indexOf(ws.anchor), b = ws.ids.indexOf(id);
      ws.sel = new Set(ws.ids.slice(Math.min(a, b), Math.max(a, b) + 1));
      setCurrent(id, { keepSel: true });
    } else setCurrent(id);
  });
  document.addEventListener('dblclick', e => {
    const tl = e.target.closest('.tile[data-asset]');
    if (tl && ui.ws) { setCurrent(tl.dataset.asset); openLightbox(); }
  });

  document.addEventListener('keydown', e => {
    const tg = e.target;
    if (tg && tg.id === 'path-input' && e.key === 'Enter') { e.preventDefault(); addPath(); return; }
    if (isField(tg)) return;
    const open = $('dialog[open]');
    const lb = !!open && open.classList.contains('lightbox');
    if (open && !lb) return;
    const mod = e.metaKey || e.ctrlKey;
    const k = e.key;
    if (mod && k.toLowerCase() === 'z' && !e.shiftKey) { e.preventDefault(); doUndo(); return; }
    const ws = ui.ws;
    if (!ws) return;
    if (mod && k.toLowerCase() === 'a' && !lb) { e.preventDefault(); ws.sel = new Set(ws.ids); applySel(); return; }
    if (mod || e.altKey) return;
    const targets = lb ? (ws.current ? [ws.current] : []) : selTargets();
    const markKey = (patch, label) => { if (targets.length) { e.preventDefault(); doMark(targets, patch, label, targets.length === 1); } };
    if (k === 'ArrowRight') { e.preventDefault(); move(1); }
    else if (k === 'ArrowLeft') { e.preventDefault(); move(-1); }
    else if ((k === 'ArrowDown' || k === 'ArrowUp') && !lb) { e.preventDefault(); move(k === 'ArrowDown' ? gridCols() : -gridCols()); }
    else if (k === 'p' || k === 'P') markKey({ flag: 'pick' }, 'Pick');
    else if (k === 'x' || k === 'X') markKey({ flag: 'reject' }, 'Reject');
    else if (k === 'u' || k === 'U') markKey({ flag: null }, '清除 Flag');
    else if (/^[0-5]$/.test(k)) markKey({ stars: Number(k) }, k === '0' ? '清除星级' : `${k} 星`);
    else if (k === ' ' && !lb && (tg === document.body || tg.classList.contains('tile') || tg.tagName === 'H1' || tg === main)) { e.preventDefault(); openLightbox(); }
    else if ((k === 'c' || k === 'C') && !lb) openCompare();
    else if (k === 'Escape' && !lb && ws.sel.size > 1) { ws.sel = new Set(ws.current ? [ws.current] : []); applySel(); }
  });

  document.addEventListener('input', e => {
    const f = e.target.form;
    if (f && f.id === 'create-form' && ui.draft && ['title', 'subtitle', 'description'].includes(e.target.name)) { ui.draft[e.target.name] = e.target.value; showErr(e.target.name, ''); }
    if (e.target.id === 'path-input') showErr('sources', '');
  });
  document.addEventListener('change', async e => {
    const t = e.target;
    if (t.form && t.form.id === 'create-form' && ui.draft) {
      const d = ui.draft;
      if (t.name === 'preset') { d.preset = t.value; const p = S.presets.find(x => x.id === t.value); d.caps = expandCaps(p ? p.capabilities : []); $('#cap-toggles').innerHTML = capToggles(d.caps, 'cap'); }
      else if (t.name === 'cap') { d.caps = expandCaps($$('input[name=cap]:checked').map(i => i.value)); $('#cap-toggles').innerHTML = capToggles(d.caps, 'cap'); }
      else if (t.name === 'run_mode') d.run_mode = t.value;
    } else if (t.form && t.form.id === 'lib-filters') {
      ui.lib[t.name] = t.value;
      route(true);
    } else if (t.dataset.actionChange === 'batch-stars' && t.value !== '') {
      const n = Number(t.value);
      doMark(Array.from(ui.ws.sel), { stars: n }, n ? `批量 ${n} 星` : '批量清除星级');
    } else if (t.dataset.pref === 'advance' || t.dataset.pref === 'idburst') prefs.set(t.dataset.pref, t.checked ? '1' : '0');
    else if (t.name === 'def-mode' || t.name === 'def-preset') {
      try { S.settings = await api('PUT', '/settings', t.name === 'def-mode' ? { run_mode: t.value } : { default_preset: t.value }); toast('默认设置已保存'); } catch (err) { toast(errMsg(err)); }
    }
  });
  document.addEventListener('submit', e => {
    if (e.target.id === 'create-form') { e.preventDefault(); submitCreate(); }
    if (e.target.id === 'lib-filters') e.preventDefault();
  });

  // 图片载入失败：显示占位，不出现破图；视频无法播放：说明并提供在 Finder 中显示
  document.addEventListener('error', e => {
    const el = e.target;
    if (el && el.tagName === 'IMG') { const ph = el.closest('.ph'); if (ph) ph.classList.add('is-failed'); el.remove(); }
    if (el && el.tagName === 'VIDEO') { const box = el.closest('.vid'); el.remove(); if (box) $('.vid-fail', box).hidden = false; }
  }, true);

  window.addEventListener('hashchange', () => route());
  (async function boot() {
    try {
      const [caps, presets, settings] = await Promise.all([api('GET', '/capabilities'), api('GET', '/presets'), api('GET', '/settings')]);
      S.caps = caps; S.presets = presets; S.settings = settings;
      await refreshTasks();
      try { await refreshCols(); } catch (e) { S.cols = []; }
    } catch (e) { S.down = e.status === 0; }
    patchBinds();
    await route();
    pollNow();
    setInterval(pollNow, 2000);
  })();
})();
