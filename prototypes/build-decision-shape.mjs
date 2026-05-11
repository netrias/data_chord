// Build the decision-shape Stage 2 prototype as a single self-contained HTML
// file with the live payload + CDE catalog inlined as JS globals.
import { readFileSync, writeFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const payload = readFileSync(resolve(here, 'stage2_live_payload.json'), 'utf8');
const catalog = readFileSync(resolve(here, 'stage2_cde_catalog.json'), 'utf8');

const html = `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8" />
<title>Stage 2 — Decision-shape prototype</title>
<style>
  :root {
    --netrias-500: #73b306;
    --netrias-600: #5e9205;
    --netrias-700: #4a7304;
    --netrias-100: #e8f5cc;
    --netrias-50:  #f4fae6;

    --azure-500: #0673b3;
    --azure-700: #044770;
    --azure-50:  #e6f2fa;

    --magenta-500: #b30673;
    --magenta-50:  #fae6f2;

    --amber-500: #d97706;
    --amber-700: #92400e;
    --amber-50:  #fef3c7;

    --gray-900: #0f172a;
    --gray-800: #1e293b;
    --gray-700: #334155;
    --gray-600: #475569;
    --gray-500: #64748b;
    --gray-400: #94a3b8;
    --gray-300: #cbd5e1;
    --gray-200: #e2e8f0;
    --gray-100: #f1f5f9;
    --gray-50:  #f8fafc;
    --white: #fff;

    --shadow-sm: 0 1px 2px rgba(15, 23, 42, 0.04);
    --shadow-md: 0 4px 16px rgba(15, 23, 42, 0.08);
    --shadow-lg: 0 20px 60px rgba(15, 23, 42, 0.18);

    --radius-sm: 8px;
    --radius-md: 12px;
    --radius-lg: 16px;
    --radius-full: 999px;

    --font-family: "Inter", "Helvetica Neue", Arial, sans-serif;
  }

  * { box-sizing: border-box; }
  .hidden { display: none !important; }
  body {
    margin: 0;
    font-family: var(--font-family);
    background: radial-gradient(circle at top, #f5fbfc, var(--gray-50) 60%);
    color: var(--gray-900);
    padding: 24px 24px 80px;
  }
  .page { max-width: 1280px; margin: 0 auto; }

  .topbar {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 24px;
  }
  .crumb { font-size: 13px; color: var(--gray-500); letter-spacing: 0.04em; text-transform: uppercase; }
  .crumb b { color: var(--gray-900); }

  .progress-card {
    display: flex;
    flex-direction: column;
    align-items: flex-end;
    gap: 4px;
    background: var(--white);
    border: 1px solid var(--gray-200);
    border-radius: var(--radius-md);
    padding: 10px 16px;
    box-shadow: var(--shadow-sm);
  }
  .progress-headline { font-size: 16px; font-weight: 600; color: var(--gray-900); }
  .progress-headline b { color: var(--netrias-700); }
  .progress-sub { font-size: 12px; color: var(--gray-500); }

  .stage-card {
    background: var(--white);
    border: 1px solid var(--gray-200);
    border-radius: var(--radius-lg);
    box-shadow: var(--shadow-md);
    overflow: hidden;
  }

  .toolbar {
    display: flex;
    align-items: center;
    gap: 12px;
    padding: 16px 20px;
    border-bottom: 1px solid var(--gray-100);
    flex-wrap: wrap;
  }
  .chips {
    display: flex;
    gap: 6px;
    flex-wrap: wrap;
  }
  .chip {
    border: 1px solid var(--gray-200);
    background: var(--white);
    border-radius: var(--radius-full);
    padding: 6px 12px;
    font-size: 13px;
    color: var(--gray-700);
    cursor: pointer;
    display: inline-flex;
    align-items: center;
    gap: 6px;
    line-height: 1;
    transition: all 120ms ease;
  }
  .chip:hover { border-color: var(--gray-300); }
  .chip.active {
    background: var(--gray-900);
    border-color: var(--gray-900);
    color: var(--white);
  }
  .chip .ico {
    width: 16px; height: 16px; display: inline-flex; align-items: center; justify-content: center;
    font-size: 11px;
    border-radius: var(--radius-full);
  }
  .chip .count {
    background: var(--gray-100);
    padding: 1px 8px;
    border-radius: var(--radius-full);
    font-size: 12px;
    color: var(--gray-700);
    font-variant-numeric: tabular-nums;
  }
  .chip.active .count { background: rgba(255,255,255,0.18); color: var(--white); }
  .chip--auto .ico { background: var(--gray-100); color: var(--gray-500); }
  .chip--review .ico { background: var(--amber-50); color: var(--amber-700); }
  .chip--unmapped .ico { background: var(--gray-100); color: var(--gray-500); border: 1px dashed var(--gray-400); }
  .chip--decided .ico { background: var(--netrias-100); color: var(--netrias-700); }
  .chip--changed .ico { background: var(--azure-50); color: var(--azure-700); }

  .toolbar-spacer { flex: 1; }
  .search-input {
    border: 1px solid var(--gray-200);
    border-radius: var(--radius-full);
    padding: 6px 14px;
    font-size: 13px;
    width: 220px;
    outline: none;
  }
  .audit-btn {
    background: var(--gray-900);
    color: var(--white);
    border: none;
    border-radius: var(--radius-full);
    padding: 7px 14px;
    font-size: 13px;
    font-weight: 600;
    cursor: pointer;
  }
  .audit-btn:hover { background: var(--gray-800); }
  .audit-btn[disabled] { opacity: 0.4; cursor: not-allowed; }

  .list-head, .row {
    display: grid;
    grid-template-columns: 28px minmax(0, 1.2fr) minmax(0, 1.4fr) 110px 24px;
    align-items: center;
    gap: 16px;
    padding: 10px 20px;
  }
  .list-head {
    font-size: 11px;
    color: var(--gray-500);
    letter-spacing: 0.06em;
    text-transform: uppercase;
    border-bottom: 1px solid var(--gray-100);
    background: var(--gray-50);
  }
  .row {
    border-bottom: 1px solid var(--gray-100);
    cursor: pointer;
    transition: background 80ms ease;
  }
  .row:hover { background: var(--gray-50); }
  .row:last-child { border-bottom: none; }

  .row-status {
    width: 22px; height: 22px;
    border-radius: var(--radius-full);
    display: inline-flex; align-items: center; justify-content: center;
    font-size: 12px;
    font-weight: 600;
  }
  .row-col { font-weight: 600; color: var(--gray-900); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .row-col-sub { font-size: 11px; color: var(--gray-500); font-weight: 400; margin-top: 2px; }
  .row-target { color: var(--gray-700); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .row-target--placeholder { color: var(--gray-400); font-style: italic; }
  .row-target--none { color: var(--gray-400); }
  .row-fit { font-size: 12px; color: var(--gray-600); text-align: right; font-variant-numeric: tabular-nums; }
  .row-chev { color: var(--gray-300); font-size: 18px; text-align: right; }

  /* state-specific row treatments */
  .row--auto .row-status { background: var(--gray-100); color: var(--gray-400); border: 1px solid var(--gray-200); }
  .row--auto .row-col, .row--auto .row-target { color: var(--gray-500); }
  .row--auto .row-fit { color: var(--gray-400); }

  .row--review .row-status { background: var(--amber-50); color: var(--amber-700); border: 1px solid #fde68a; }
  .row--review .row-target { color: var(--gray-900); }

  .row--unmapped .row-status { background: var(--white); color: var(--gray-400); border: 1px dashed var(--gray-400); }

  .row--confirmed .row-status { background: var(--netrias-100); color: var(--netrias-700); border: 1px solid #b9e08a; }
  .row--confirmed .row-col, .row--confirmed .row-target { color: var(--gray-900); }

  .row--changed .row-status { background: var(--azure-50); color: var(--azure-700); border: 1px solid #b8def4; }
  .row--changed .row-target { color: var(--azure-700); font-weight: 600; }

  .row--skipped .row-status { background: var(--gray-100); color: var(--gray-500); border: 1px solid var(--gray-200); }
  .row--skipped .row-target { color: var(--gray-400); font-style: italic; }

  /* badges */
  .badge {
    display: inline-flex;
    align-items: center;
    gap: 4px;
    padding: 2px 8px;
    font-size: 10px;
    border-radius: var(--radius-full);
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    margin-left: 8px;
    vertical-align: middle;
  }
  .badge--auto { background: var(--gray-100); color: var(--gray-500); }
  .badge--passthrough { background: var(--magenta-50); color: var(--magenta-500); }
  .badge--audited { background: var(--netrias-100); color: var(--netrias-700); }

  /* takeover */
  .takeover {
    position: fixed;
    inset: 0;
    background: rgba(15, 23, 42, 0.45);
    display: flex;
    align-items: center;
    justify-content: center;
    z-index: 100;
  }
  .takeover.hidden { display: none; }
  .takeover-card {
    background: var(--white);
    width: min(900px, 92vw);
    max-height: 86vh;
    border-radius: var(--radius-lg);
    box-shadow: var(--shadow-lg);
    overflow: hidden;
    display: flex;
    flex-direction: column;
  }
  .tk-head {
    padding: 16px 20px;
    border-bottom: 1px solid var(--gray-100);
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 16px;
  }
  .tk-head-title { font-size: 17px; font-weight: 600; }
  .tk-head-sub { font-size: 12px; color: var(--gray-500); margin-top: 2px; }
  .tk-counter { font-size: 12px; color: var(--gray-500); }
  .tk-body {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 1px;
    background: var(--gray-100);
    flex: 1;
    overflow: hidden;
  }
  .tk-pane {
    background: var(--white);
    overflow: auto;
    padding: 16px 20px;
  }
  .tk-pane h4 { margin: 0 0 8px; font-size: 12px; color: var(--gray-500); letter-spacing: 0.06em; text-transform: uppercase; }
  .tk-target-name { font-size: 18px; font-weight: 600; color: var(--gray-900); }
  .tk-target-desc { font-size: 13px; color: var(--gray-600); margin-top: 8px; line-height: 1.5; }
  .tk-conform-pill {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 4px 10px;
    border-radius: var(--radius-full);
    font-size: 12px;
    background: var(--netrias-50);
    color: var(--netrias-700);
    margin-top: 12px;
  }
  .tk-conform-pill.bad { background: var(--amber-50); color: var(--amber-700); }
  .tk-conform-pill.neutral { background: var(--magenta-50); color: var(--magenta-500); }

  .sample-list { list-style: none; margin: 0; padding: 0; font-size: 13px; }
  .sample-list li {
    padding: 4px 0;
    border-bottom: 1px solid var(--gray-100);
    color: var(--gray-700);
    display: flex;
    align-items: center;
    gap: 8px;
  }
  .sample-list li.match { color: var(--netrias-700); }
  .sample-list li .ok { width: 14px; color: var(--netrias-500); }

  .tk-foot {
    padding: 14px 20px;
    border-top: 1px solid var(--gray-100);
    display: flex;
    align-items: center;
    gap: 8px;
    background: var(--gray-50);
  }
  .btn {
    border-radius: var(--radius-full);
    padding: 8px 14px;
    font-size: 13px;
    font-weight: 600;
    cursor: pointer;
    border: 1px solid transparent;
    transition: all 120ms ease;
  }
  .btn-primary { background: var(--netrias-500); color: var(--white); border-color: var(--netrias-500); }
  .btn-primary:hover { background: var(--netrias-600); border-color: var(--netrias-600); }
  .btn-secondary { background: var(--white); color: var(--gray-800); border-color: var(--gray-300); }
  .btn-secondary:hover { background: var(--gray-50); }
  .btn-ghost { background: transparent; color: var(--gray-600); border-color: transparent; }
  .btn-ghost:hover { color: var(--gray-900); }
  .tk-foot-spacer { flex: 1; }

  .picker {
    margin-top: 12px;
    border: 1px solid var(--gray-200);
    border-radius: var(--radius-md);
    overflow: hidden;
  }
  .picker-search {
    width: 100%;
    border: none;
    padding: 10px 12px;
    border-bottom: 1px solid var(--gray-100);
    font-size: 13px;
    outline: none;
  }
  .picker-list {
    max-height: 280px;
    overflow: auto;
  }
  .picker-opt {
    padding: 10px 12px;
    border-bottom: 1px solid var(--gray-100);
    cursor: pointer;
    display: flex;
    align-items: center;
    gap: 10px;
    font-size: 13px;
  }
  .picker-opt:hover { background: var(--gray-50); }
  .picker-opt.is-current { background: var(--netrias-50); }
  .picker-opt-name { font-weight: 600; color: var(--gray-900); }
  .picker-opt-desc { font-size: 11px; color: var(--gray-500); margin-top: 2px; line-height: 1.4; }

  .empty {
    padding: 60px 20px;
    text-align: center;
    color: var(--gray-500);
    font-size: 14px;
  }

  .legend {
    margin-top: 20px;
    background: var(--white);
    border: 1px solid var(--gray-200);
    border-radius: var(--radius-md);
    padding: 12px 16px;
    font-size: 12px;
    color: var(--gray-600);
    line-height: 1.6;
  }
  .legend b { color: var(--gray-800); }

  .audit-banner {
    background: var(--gray-900);
    color: var(--white);
    padding: 10px 20px;
    font-size: 13px;
    display: flex;
    align-items: center;
    gap: 12px;
  }
  .audit-banner .audit-progress { font-weight: 600; }
  .audit-banner .audit-exit {
    margin-left: auto;
    background: rgba(255,255,255,0.15);
    border: none;
    color: var(--white);
    padding: 4px 10px;
    font-size: 12px;
    border-radius: var(--radius-full);
    cursor: pointer;
  }
</style>
</head>
<body>
<div class="page">
  <div class="topbar">
    <div class="crumb">Stage 2 · <b>Map columns to standards</b> · prototype (decision-shape filters)</div>
    <div class="progress-card">
      <div class="progress-headline"><b id="progressDecided">0</b> of <span id="progressTotal">0</span> decisions made</div>
      <div class="progress-sub" id="progressSub"></div>
    </div>
  </div>

  <div class="stage-card">
    <div class="audit-banner hidden" id="auditBanner">
      <span>Auditing auto-matched columns</span>
      <span class="audit-progress" id="auditProgress"></span>
      <button class="audit-exit" id="auditExit">Exit audit</button>
    </div>
    <div class="toolbar">
      <div class="chips" id="chips"></div>
      <div class="toolbar-spacer"></div>
      <input class="search-input" id="search" placeholder="Filter columns…" type="search" />
      <button class="audit-btn" id="auditBtn">Audit auto-matches →</button>
    </div>
    <div class="list-head">
      <div></div>
      <div>Your column</div>
      <div>Target standard</div>
      <div style="text-align:right">Value fit</div>
      <div></div>
    </div>
    <div id="rows"></div>
    <div class="empty hidden" id="empty">No columns to display.</div>
  </div>

  <div class="legend">
    <b>Decision states.</b>
    <span class="badge badge--auto">auto</span> name matches the standard exactly — presumed correct, can be audited.
    <span class="badge" style="background:var(--amber-50);color:var(--amber-700)">review</span> AI suggested a target; the column header doesn't match the name — read and decide.
    <span class="badge" style="background:var(--white);color:var(--gray-500);border:1px dashed var(--gray-400)">unmapped</span> no AI suggestion; pick a target or skip.
    Once you confirm or change a target, the row becomes <span class="badge" style="background:var(--netrias-100);color:var(--netrias-700)">decided</span>.
  </div>
</div>

<div class="takeover hidden" id="takeover">
  <div class="takeover-card">
    <div class="tk-head">
      <div>
        <div class="tk-head-title" id="tkTitle"></div>
        <div class="tk-head-sub" id="tkSub"></div>
      </div>
      <div class="tk-counter" id="tkCounter"></div>
    </div>
    <div class="tk-body">
      <div class="tk-pane">
        <h4>Your column · sample values</h4>
        <ul class="sample-list" id="tkSamples"></ul>
      </div>
      <div class="tk-pane">
        <h4>Proposed target standard</h4>
        <div class="tk-target-name" id="tkTargetName"></div>
        <div class="tk-target-desc" id="tkTargetDesc"></div>
        <div id="tkConformWrap"></div>
        <div id="tkPickerWrap" class="hidden"></div>
      </div>
    </div>
    <div class="tk-foot">
      <button class="btn btn-primary" id="tkConfirm">✓ Looks right — confirm</button>
      <button class="btn btn-secondary" id="tkChange">Pick different target</button>
      <button class="btn btn-ghost" id="tkSkip">Skip column</button>
      <div class="tk-foot-spacer"></div>
      <button class="btn btn-ghost" id="tkPrev">← Prev</button>
      <button class="btn btn-ghost" id="tkNext">Next →</button>
      <button class="btn btn-ghost" id="tkClose">Close</button>
    </div>
  </div>
</div>

<script>
  const PAYLOAD = ${payload};
  const CATALOG = ${catalog};
  const CDE_BY_KEY = new Map(CATALOG.map(c => [c.cde_key, c]));
  const NO_MAP_DESC = 'No standard chosen. This column will be skipped during harmonization.';

  // ── Decision state ─────────────────────────────────────────────
  // decisions: colKey -> { kind: 'confirmed' | 'overridden' | 'no_mapping' | 'audited', target?: string }
  // 'audited' = user swept through audit and accepted the auto-matched target
  const state = {
    decisions: new Map(),
    filter: 'all',
    search: '',
    takeoverKey: null,
    auditMode: false,
    pickerOpen: false,
  };

  // ── Helpers ────────────────────────────────────────────────────
  const colKeyOf = (c) => c.column_key ?? c.column_name;
  const headerOf = (c) => c.header ?? c.column_name ?? '(unnamed)';
  const topSuggestion = (c) => (PAYLOAD.cde_targets?.[colKeyOf(c)] ?? [])[0] ?? null;
  const overlapRatio = (c) => {
    const r = PAYLOAD.column_summaries?.[colKeyOf(c)]?.value_overlap_ratio;
    return Number.isFinite(r) ? r : null;
  };

  // The current target for a column, considering user decisions.
  const effectiveTarget = (c) => {
    const d = state.decisions.get(colKeyOf(c));
    if (d?.kind === 'no_mapping') return null;
    if (d?.target) return d.target;
    const s = topSuggestion(c);
    return s?.target ?? null;
  };

  // Decision-shape state of the row — drives filter membership and visual.
  const stateOf = (c) => {
    const d = state.decisions.get(colKeyOf(c));
    if (d?.kind === 'no_mapping') return 'skipped';
    if (d?.kind === 'overridden') return 'changed';
    if (d?.kind === 'confirmed' || d?.kind === 'audited') return 'confirmed';
    const s = topSuggestion(c);
    if (!s) return 'unmapped';
    // sim 1.0 = byte-equal name match → presumed-correct ("auto")
    return s.similarity >= 0.999 ? 'auto' : 'review';
  };

  const isPassthrough = (target) => target ? CDE_BY_KEY.get(target)?.cde_type === 'passthrough' : false;

  // ── Counts ─────────────────────────────────────────────────────
  const countByState = () => {
    const counts = { all: 0, auto: 0, review: 0, unmapped: 0, decided: 0, changed: 0, skipped: 0 };
    for (const c of PAYLOAD.columns) {
      counts.all++;
      const s = stateOf(c);
      if (s === 'auto') counts.auto++;
      else if (s === 'review') counts.review++;
      else if (s === 'unmapped') counts.unmapped++;
      else if (s === 'confirmed') { counts.decided++; }
      else if (s === 'changed') { counts.decided++; counts.changed++; }
      else if (s === 'skipped') { counts.decided++; }
    }
    return counts;
  };

  // ── Filter logic ───────────────────────────────────────────────
  const passesFilter = (c) => {
    const s = stateOf(c);
    if (state.filter === 'all') return true;
    if (state.filter === 'decided') return s === 'confirmed' || s === 'changed' || s === 'skipped';
    return s === state.filter;
  };
  const passesSearch = (c) => {
    if (!state.search) return true;
    return headerOf(c).toLowerCase().includes(state.search);
  };
  const visibleColumns = () => PAYLOAD.columns.filter(c => passesFilter(c) && passesSearch(c));

  // ── Rendering: chips ───────────────────────────────────────────
  const renderChips = () => {
    const counts = countByState();
    const items = [
      { key: 'all', label: 'All', count: counts.all, ico: '·', cls: '' },
      { key: 'review', label: 'Needs review', count: counts.review, ico: '⚠', cls: 'chip--review' },
      { key: 'unmapped', label: 'Unmapped', count: counts.unmapped, ico: '○', cls: 'chip--unmapped' },
      { key: 'auto', label: 'Auto-matched', count: counts.auto, ico: '⚙', cls: 'chip--auto' },
      { key: 'decided', label: 'Decided', count: counts.decided, ico: '✓', cls: 'chip--decided' },
    ];
    document.getElementById('chips').innerHTML = items.map(it => \`
      <button class="chip \${it.cls} \${state.filter === it.key ? 'active' : ''}" data-key="\${it.key}">
        <span class="ico">\${it.ico}</span>
        <span>\${it.label}</span>
        <span class="count">\${it.count}</span>
      </button>
    \`).join('');
  };

  // ── Rendering: progress card ───────────────────────────────────
  const renderProgress = () => {
    const counts = countByState();
    const totalActionable = counts.all - counts.auto + (countAudited());
    document.getElementById('progressDecided').textContent = counts.decided;
    document.getElementById('progressTotal').textContent = totalActionable;
    document.getElementById('progressSub').textContent = counts.auto > 0
      ? \`+ \${counts.auto} auto-matched (audit any time)\`
      : 'all auto-matched columns audited';
  };
  const countAudited = () => Array.from(state.decisions.values()).filter(d => d.kind === 'audited').length;

  // ── Rendering: rows ────────────────────────────────────────────
  const renderRows = () => {
    const cols = visibleColumns();
    const empty = document.getElementById('empty');
    const rows = document.getElementById('rows');
    if (!cols.length) {
      rows.innerHTML = '';
      empty.classList.remove('hidden');
      return;
    }
    empty.classList.add('hidden');
    rows.innerHTML = cols.map(rowHtml).join('');
  };

  const STATUS_GLYPH = {
    auto: '⚙',
    review: '⚠',
    unmapped: '○',
    confirmed: '✓',
    changed: '✎',
    skipped: '—',
  };

  const rowHtml = (c) => {
    const s = stateOf(c);
    const target = effectiveTarget(c);
    const tgtMeta = target ? CDE_BY_KEY.get(target) : null;
    const ratio = overlapRatio(c);
    const passthrough = isPassthrough(target);

    let targetCell;
    const decision = state.decisions.get(colKeyOf(c));
    if (decision?.kind === 'no_mapping') {
      targetCell = '<span class="row-target row-target--none">No mapping (skipped)</span>';
    } else if (!target) {
      targetCell = '<span class="row-target row-target--placeholder">— pick a target —</span>';
    } else {
      const ptBadge = passthrough ? '<span class="badge badge--passthrough">pass-through</span>' : '';
      targetCell = \`<span class="row-target">\${escapeHtml(target)}\${ptBadge}</span>\`;
    }

    let fitCell;
    if (!target || decision?.kind === 'no_mapping') fitCell = '<span class="row-fit">—</span>';
    else if (passthrough) fitCell = '<span class="row-fit" title="No PV list to validate against">↪</span>';
    else if (ratio === null) fitCell = '<span class="row-fit">—</span>';
    else fitCell = \`<span class="row-fit">\${formatRatio(ratio)}</span>\`;

    return \`
      <div class="row row--\${s}" data-key="\${escapeAttr(colKeyOf(c))}">
        <span class="row-status">\${STATUS_GLYPH[s]}</span>
        <div class="row-col" title="\${escapeAttr(headerOf(c))}">\${escapeHtml(headerOf(c))}</div>
        \${targetCell}
        \${fitCell}
        <span class="row-chev">›</span>
      </div>
    \`;
  };

  // ── Takeover ───────────────────────────────────────────────────
  const openTakeover = (key) => {
    state.takeoverKey = key;
    state.pickerOpen = false;
    document.getElementById('takeover').classList.remove('hidden');
    renderTakeover();
  };
  const closeTakeover = () => {
    state.takeoverKey = null;
    state.pickerOpen = false;
    document.getElementById('takeover').classList.add('hidden');
    if (state.auditMode) exitAudit();
  };

  const auditableSet = () => PAYLOAD.columns.filter(c => stateOf(c) === 'auto').map(colKeyOf);

  const renderTakeover = () => {
    const key = state.takeoverKey;
    if (!key) return;
    const c = PAYLOAD.columns.find(x => colKeyOf(x) === key);
    if (!c) { closeTakeover(); return; }

    const list = state.auditMode ? auditableSet() : visibleColumns().map(colKeyOf);
    const idx = list.indexOf(key);
    document.getElementById('tkTitle').textContent = headerOf(c);
    const s = stateOf(c);
    document.getElementById('tkSub').textContent = describeState(s);
    document.getElementById('tkCounter').textContent = state.auditMode
      ? \`Audit · \${idx + 1} of \${list.length}\`
      : list.length ? \`\${idx + 1} of \${list.length}\` : '';

    if (state.auditMode) {
      document.getElementById('auditProgress').textContent = \`(\${countAudited()} audited)\`;
    }

    // Sample values list
    const target = effectiveTarget(c);
    const tgtMeta = target ? CDE_BY_KEY.get(target) : null;
    const passthrough = isPassthrough(target);
    const samples = (c.sample_values ?? []).slice(0, 30);
    document.getElementById('tkSamples').innerHTML = samples.map(v => \`
      <li><span class="ok"></span><span>\${escapeHtml(v)}</span></li>
    \`).join('');

    // Target pane
    document.getElementById('tkTargetName').textContent = target ?? '(none chosen)';
    document.getElementById('tkTargetDesc').textContent = target
      ? (tgtMeta?.description ?? '')
      : 'Pick a standard from the catalog, or skip this column.';

    const ratio = overlapRatio(c);
    let pillHtml = '';
    if (target) {
      if (passthrough) {
        pillHtml = '<div class="tk-conform-pill neutral">↪ Pass-through · no validation</div>';
      } else if (ratio !== null) {
        const pct = ratio;
        const cls = pct >= 0.5 ? '' : 'bad';
        pillHtml = \`<div class="tk-conform-pill \${cls}">\${pct >= 0.5 ? '✓' : '⚠'} \${formatRatio(pct)} of distinct values match target's PV list</div>\`;
      }
    }
    document.getElementById('tkConformWrap').innerHTML = pillHtml;

    // Picker
    const pickerWrap = document.getElementById('tkPickerWrap');
    if (state.pickerOpen) {
      pickerWrap.classList.remove('hidden');
      pickerWrap.innerHTML = pickerHtml(c);
      const search = document.getElementById('pickerSearch');
      search.addEventListener('input', (e) => {
        const list = document.getElementById('pickerList');
        list.innerHTML = pickerOptionsHtml(c, e.target.value);
      });
    } else {
      pickerWrap.classList.add('hidden');
    }

    // Buttons
    document.getElementById('tkPrev').disabled = idx <= 0;
    document.getElementById('tkNext').disabled = idx < 0 || idx >= list.length - 1;
    const confirmBtn = document.getElementById('tkConfirm');
    if (target) {
      confirmBtn.style.display = '';
      confirmBtn.textContent = state.auditMode
        ? '✓ Looks right — next →'
        : (s === 'confirmed' || s === 'changed') ? '✓ Already confirmed' : '✓ Looks right — confirm';
    } else {
      confirmBtn.style.display = 'none';
    }
  };

  const describeState = (s) => ({
    auto: 'Auto-matched on byte-equal name. Presumed correct — confirm to mark decided.',
    review: 'AI suggested a target. The column name doesn\\'t match exactly, so this needs your read.',
    unmapped: 'No AI suggestion. Pick a standard from the catalog or skip the column.',
    confirmed: 'Confirmed.',
    changed: 'You picked a different target.',
    skipped: 'Skipped (no mapping).',
  }[s] ?? '');

  const pickerHtml = (c) => \`
    <div class="picker">
      <input class="picker-search" id="pickerSearch" type="search" placeholder="Search standards by name or description…" />
      <div class="picker-list" id="pickerList">
        \${pickerOptionsHtml(c, '')}
      </div>
    </div>
  \`;

  const pickerOptionsHtml = (c, q) => {
    const lq = q.toLowerCase();
    const current = effectiveTarget(c);
    const matches = (cde) =>
      !lq ||
      cde.cde_key.toLowerCase().includes(lq) ||
      (cde.label ?? '').toLowerCase().includes(lq) ||
      (cde.description ?? '').toLowerCase().includes(lq);
    const opts = CATALOG.filter(matches).slice(0, 60);
    return opts.map(cde => \`
      <div class="picker-opt \${cde.cde_key === current ? 'is-current' : ''}" data-key="\${escapeAttr(cde.cde_key)}">
        <div style="flex:1;min-width:0">
          <div class="picker-opt-name">\${escapeHtml(cde.cde_key)}</div>
          <div class="picker-opt-desc">\${escapeHtml((cde.description ?? '').slice(0, 200))}</div>
        </div>
        \${cde.cde_type === 'passthrough' ? '<span class="badge badge--passthrough">pass-through</span>' : ''}
      </div>
    \`).join('') || '<div class="empty">No standards match.</div>';
  };

  // ── Decisions ──────────────────────────────────────────────────
  const confirmCurrent = () => {
    const key = state.takeoverKey;
    if (!key) return;
    const c = PAYLOAD.columns.find(x => colKeyOf(x) === key);
    if (!c) return;
    const target = effectiveTarget(c);
    if (!target) return;
    // Audit confirmations are recorded as 'audited' so they can be counted
    // distinctly in progress UI ("0 of 37 + N audited").
    const isAuto = stateOf(c) === 'auto';
    const kind = state.auditMode && isAuto ? 'audited' : (isAuto ? 'audited' : 'confirmed');
    // Capture position in the current filter BEFORE the decision changes the
    // row's state and removes it from that filter.
    const before = state.auditMode ? auditableSet() : visibleColumns().map(colKeyOf);
    const beforeIdx = before.indexOf(key);
    state.decisions.set(colKeyOf(c), { kind, target });
    rerender();
    advanceOrClose(beforeIdx);
  };
  const skipCurrent = () => {
    const key = state.takeoverKey;
    if (!key) return;
    const before = state.auditMode ? auditableSet() : visibleColumns().map(colKeyOf);
    const beforeIdx = before.indexOf(key);
    state.decisions.set(key, { kind: 'no_mapping' });
    rerender();
    advanceOrClose(beforeIdx);
  };
  const overrideCurrent = (newTarget) => {
    const key = state.takeoverKey;
    if (!key) return;
    state.decisions.set(key, { kind: 'overridden', target: newTarget });
    state.pickerOpen = false;
    rerender();
    renderTakeover();
  };

  const advanceOrClose = (beforeIdx) => {
    if (state.auditMode) {
      // After audit confirmation, the row leaves the auto pool. The next
      // row to audit is whatever is now at the same index (or the new last).
      const remaining = auditableSet();
      if (remaining.length === 0) { exitAudit(); closeTakeover(); return; }
      const i = Math.min(beforeIdx, remaining.length - 1);
      state.takeoverKey = remaining[i];
      state.pickerOpen = false;
      renderTakeover();
      return;
    }
    // Non-audit: the just-decided row has left the active filter, so the row
    // at the same index in the new visible list is the natural next target.
    const after = visibleColumns().map(colKeyOf);
    if (after.length === 0) { closeTakeover(); return; }
    const i = Math.min(beforeIdx, after.length - 1);
    state.takeoverKey = after[i];
    state.pickerOpen = false;
    renderTakeover();
  };

  // ── Audit mode ─────────────────────────────────────────────────
  const enterAudit = () => {
    const list = auditableSet();
    if (!list.length) return;
    state.auditMode = true;
    state.filter = 'auto';
    document.getElementById('auditBanner').classList.remove('hidden');
    document.getElementById('auditBtn').textContent = 'In audit ↓';
    document.getElementById('auditBtn').disabled = true;
    state.takeoverKey = list[0];
    rerender();
    openTakeover(list[0]);
  };
  const exitAudit = () => {
    state.auditMode = false;
    document.getElementById('auditBanner').classList.add('hidden');
    document.getElementById('auditBtn').textContent = 'Audit auto-matches →';
    document.getElementById('auditBtn').disabled = false;
    rerender();
  };

  // ── Wiring ─────────────────────────────────────────────────────
  const rerender = () => {
    renderChips();
    renderProgress();
    renderRows();
  };

  const formatRatio = (r) => {
    const pct = r * 100;
    return Number.isInteger(pct) ? pct + '%' : pct.toFixed(1) + '%';
  };
  function escapeHtml(s) {
    return String(s ?? '').replace(/[&<>]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' }[c]));
  }
  function escapeAttr(s) {
    return String(s ?? '').replace(/["'&<>]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  }

  document.getElementById('chips').addEventListener('click', (e) => {
    const btn = e.target.closest('.chip');
    if (!btn) return;
    state.filter = btn.dataset.key;
    rerender();
  });
  document.getElementById('search').addEventListener('input', (e) => {
    state.search = e.target.value.toLowerCase();
    renderRows();
  });
  document.getElementById('rows').addEventListener('click', (e) => {
    const row = e.target.closest('.row');
    if (!row) return;
    openTakeover(row.dataset.key);
  });
  document.getElementById('auditBtn').addEventListener('click', enterAudit);
  document.getElementById('auditExit').addEventListener('click', () => { exitAudit(); closeTakeover(); });

  document.getElementById('tkClose').addEventListener('click', closeTakeover);
  document.getElementById('tkConfirm').addEventListener('click', confirmCurrent);
  document.getElementById('tkSkip').addEventListener('click', skipCurrent);
  document.getElementById('tkChange').addEventListener('click', () => {
    state.pickerOpen = !state.pickerOpen;
    renderTakeover();
  });
  document.getElementById('tkPickerWrap').addEventListener('click', (e) => {
    const opt = e.target.closest('.picker-opt');
    if (!opt) return;
    overrideCurrent(opt.dataset.key);
  });
  document.getElementById('tkPrev').addEventListener('click', () => {
    const list = state.auditMode ? auditableSet() : visibleColumns().map(colKeyOf);
    const idx = list.indexOf(state.takeoverKey);
    if (idx > 0) { state.takeoverKey = list[idx - 1]; state.pickerOpen = false; renderTakeover(); }
  });
  document.getElementById('tkNext').addEventListener('click', () => {
    const list = state.auditMode ? auditableSet() : visibleColumns().map(colKeyOf);
    const idx = list.indexOf(state.takeoverKey);
    if (idx >= 0 && idx < list.length - 1) { state.takeoverKey = list[idx + 1]; state.pickerOpen = false; renderTakeover(); }
  });
  document.addEventListener('keydown', (e) => {
    if (!state.takeoverKey) return;
    if (state.pickerOpen) {
      if (e.key === 'Escape') { state.pickerOpen = false; renderTakeover(); }
      return;
    }
    if (e.key === 'Escape') closeTakeover();
    else if (e.key === 'ArrowLeft') document.getElementById('tkPrev').click();
    else if (e.key === 'ArrowRight') document.getElementById('tkNext').click();
    else if (e.key === 'Enter') confirmCurrent();
  });

  // Initial render
  rerender();
</script>
</body>
</html>
`;

writeFileSync(resolve(here, 'decision-shape-stage2.html'), html);
console.log('wrote decision-shape-stage2.html (' + html.length + ' bytes)');
