/**
 * Drives the Stage 2 guided prototypes.
 *
 * Two entrypoints share rendering + state primitives:
 *   - initConcierge()    : one-at-a-time, no escape during walkthrough.
 *   - initWalkthrough()  : same card, with a queue rail and jumping.
 */

import {
  SAMPLE_FILE, CDE_CATALOG, COLUMNS, COUNTS, RISK_LABEL, RISK_TONE,
} from './sample_payload.js';

/**
 * Inject the shared progress-tracker partial. The tracker is fixed chrome
 * across the app; we preserve it verbatim so the guided flow inherits the
 * same step navigation users already know.
 */
async function mountProgressTracker() {
  const host = document.getElementById('tracker-mount');
  if (!host) return;
  try {
    const res = await fetch('./shared/_progress_tracker.html');
    host.outerHTML = await res.text();
  } catch (e) {
    // Prototype only — if the partial can't load, surface it but don't crash.
    console.warn('progress-tracker partial failed to load', e);
  }
}

const STATUS = {
  PENDING:    'pending',
  CONFIRMED:  'confirmed',
  MANUAL:     'manual',     // user picked a different CDE
  SKIPPED:    'skipped',
  NO_MAPPING: 'no_mapping',
  AUTO:       'auto',       // auto-confirmed by triage
  EMPTY:      'empty',      // empty column, skipped
  FLAGGED:    'flagged',    // marked for follow-up (walkthrough only)
};

// Value-first sections.
//
// `label` is the short, terse tab name. `explainer` is the plain-English
// sentence shown when this section is active — the goal is that a first-time
// user reads it and understands exactly what this category means.
//
// Language mirrors Stage 2's existing concepts:
//   - "Harmonize values"     → PV-type CDE, values get rewritten
//   - "Already match"        → PV-type CDE, values already conform
//   - "Pass-through"         → non-PV CDE, mapped but values not changed
//   - "Empty"                → no data
const VF_SECTIONS = [
  {
    id: 'rewrite', label: 'Harmonize values', tone: 'magenta',
    explainer: 'Your column is mapped to a standard with strict permissible values. Some of your values don’t match yet — confirming these columns will rewrite them to match.',
  },
  {
    id: 'match', label: 'Already match', tone: 'success',
    explainer: 'Your column is mapped to a standard and every value already matches its permissible values. Nothing will change — just confirm.',
  },
  {
    id: 'passthrough', label: 'Pass-through', tone: 'info',
    explainer: 'Your column is mapped to a standard that doesn’t enumerate values (free text, IDs, dates). The mapping is recorded but your values stay exactly as they are.',
  },
  {
    id: 'empty', label: 'Empty', tone: 'neutral',
    explainer: 'These columns have no data. They won’t be included in the harmonized output.',
  },
];

// In-memory state. Each column gets a decision; review queue is the subset
// the user is being walked through. Walkthrough variant adds a flag set.
const state = {
  decisions: new Map(),       // column key → { status, cde, renamed_to }
  flagged: new Set(),
  currentIdx: 0,              // (linear modes) index into the review queue
  mode: 'concierge',          // 'concierge' | 'walkthrough' | 'value_first'
  reviewQueue: [],
  showSafeInQueue: false,     // walkthrough: include auto-safe in queue?
  // Value-first specific. Section-locked: navigation stays within the
  // section the user picked; reassigning a column previews a migration
  // and only commits on confirm.
  vfCurrentSection: 'rewrite',
  vfCurrentColumnKey: null,
  vfPending: null,            // { columnKey, fromSection, toSection, cde }
  vfPickerOpenFor: null,
};

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
})[c]);

const pct = (v) => v == null ? '—' : `${Math.round(v * 100)}%`;
const tier = (v) => v == null ? 'mid' : v >= 0.9 ? 'good' : v >= 0.5 ? 'mid' : 'low';


/* ────────────────────────────────────────────────────────────
   Initial state
   ──────────────────────────────────────────────────────────── */
function seedDecisions() {
  state.decisions.clear();
  state.flagged.clear();
  for (const col of COLUMNS) {
    if (col.classification === 'auto_safe') {
      state.decisions.set(col.key, { status: STATUS.AUTO, cde: col.suggested_cde });
    } else if (col.classification === 'empty') {
      state.decisions.set(col.key, { status: STATUS.EMPTY, cde: null });
    } else {
      state.decisions.set(col.key, { status: STATUS.PENDING, cde: col.suggested_cde });
    }
  }
}

function buildReviewQueue() {
  state.reviewQueue = COLUMNS
    .filter(c => c.classification === 'needs_review')
    .map(c => c.key);
}

function currentColumn() {
  if (state.mode === 'value_first') {
    return COLUMNS.find(c => c.key === state.vfCurrentColumnKey) ?? null;
  }
  return COLUMNS.find(c => c.key === state.reviewQueue[state.currentIdx]) ?? null;
}

/**
 * Compute the actual value-overlap of a column against any candidate CDE.
 * The seed payload only stores the precomputed overlap against the AI rec,
 * so for any other CDE we have to recompute from the column's distinct
 * values vs the CDE's PV set.
 */
function effectiveOverlap(col, cdeInfo) {
  if (!cdeInfo || cdeInfo.type !== 'pv' || !cdeInfo.pvs) return null;
  const total = col.distinct_values.length;
  if (total === 0) return null;
  const pvSet = new Set(cdeInfo.pvs);
  const matched = col.distinct_values.filter(v => pvSet.has(v.value)).length;
  return matched / total;
}

/**
 * Classify a column into one of the four value-first sections.
 *
 * Section membership is *live*: it follows the column's current decision,
 * not the AI's original recommendation. Reassigning a column's target CDE
 * in the picker can move it from one section to another.
 */
function vfSection(col) {
  if (col.classification === 'empty') return 'empty';
  const decision = state.decisions.get(col.key);
  if (decision?.status === STATUS.NO_MAPPING) return 'empty';
  const cdeKey = decision?.cde ?? col.suggested_cde;
  if (!cdeKey) return 'rewrite';
  const cde = CDE_CATALOG[cdeKey];
  if (!cde) return 'rewrite';
  if (cde.type === 'passthrough') return 'passthrough';
  const overlap = (cdeKey === col.suggested_cde) ? col.overlap : effectiveOverlap(col, cde);
  return (overlap != null && overlap >= 1.0) ? 'match' : 'rewrite';
}

/**
 * The section a column *would* land in based solely on the AI rec.
 * Used to detect "moved" columns: if vfSection(col) !== vfSectionOriginal(col)
 * the user has reassigned this column out of its AI-recommended bucket.
 */
function vfSectionOriginal(col) {
  if (col.classification === 'empty') return 'empty';
  const cde = CDE_CATALOG[col.suggested_cde];
  if (!cde) return 'rewrite';
  if (cde.type === 'passthrough') return 'passthrough';
  return (col.overlap != null && col.overlap >= 1.0) ? 'match' : 'rewrite';
}

function isMoved(col) {
  return vfSection(col) !== vfSectionOriginal(col);
}

/** Section membership grouped by current decision — recomputed each render. */
function vfGroupsNow() {
  const groups = { rewrite: [], match: [], passthrough: [], empty: [] };
  for (const col of COLUMNS) groups[vfSection(col)].push(col);
  // Sort: rewrite by overlap asc; others alpha. We sort each render so
  // newly-moved columns slot into a stable position within their group.
  groups.rewrite.sort((a, b) => {
    const ao = effectiveOverlapFor(a) ?? 0;
    const bo = effectiveOverlapFor(b) ?? 0;
    return ao - bo;
  });
  groups.match.sort((a, b) => a.key.localeCompare(b.key));
  groups.passthrough.sort((a, b) => a.key.localeCompare(b.key));
  groups.empty.sort((a, b) => a.key.localeCompare(b.key));
  return groups;
}

function effectiveOverlapFor(col) {
  const decision = state.decisions.get(col.key);
  const cdeKey = decision?.cde ?? col.suggested_cde;
  if (!cdeKey) return null;
  const cde = CDE_CATALOG[cdeKey];
  if (!cde) return null;
  return cdeKey === col.suggested_cde ? col.overlap : effectiveOverlap(col, cde);
}

/**
 * Build the initial review queue using the AI-rec sections. Queue order
 * stays stable across reassignments — the chip groups update, but
 * Prev/Next stays linear so users don't get yanked around.
 */
function buildValueFirstQueue() {
  const groups = { rewrite: [], match: [], passthrough: [], empty: [] };
  for (const col of COLUMNS) groups[vfSectionOriginal(col)].push(col);
  groups.rewrite.sort((a, b) => (a.overlap ?? 0) - (b.overlap ?? 0));
  groups.match.sort((a, b) => a.key.localeCompare(b.key));
  groups.passthrough.sort((a, b) => a.key.localeCompare(b.key));
  groups.empty.sort((a, b) => a.key.localeCompare(b.key));
  state.reviewQueue = [
    ...groups.rewrite.map(c => c.key),
    ...groups.match.map(c => c.key),
    ...groups.passthrough.map(c => c.key),
    ...groups.empty.map(c => c.key),
  ];
}


/* ────────────────────────────────────────────────────────────
   Concierge entrypoint
   ──────────────────────────────────────────────────────────── */
export async function initConcierge() {
  state.mode = 'concierge';
  seedDecisions();
  buildReviewQueue();

  await mountProgressTracker();

  $('#welcome-filename').textContent = SAMPLE_FILE.file_name;
  $('#welcome-standard').textContent = SAMPLE_FILE.target_standard;
  $('#count-auto').textContent = COUNTS.auto_safe;
  $('#count-empty').textContent = COUNTS.empty;
  $('#count-review').textContent = COUNTS.needs_review;

  document.body.addEventListener('click', onConciergeClick);
  document.addEventListener('keydown', onConciergeKey);
}

function onConciergeClick(e) {
  const action = e.target.closest('[data-action]')?.dataset.action;
  if (!action) return;

  switch (action) {
    case 'start':
      enterWalkthrough();
      break;
    case 'trust-everything':
      // Auto-confirm every reviewable column without inspection.
      for (const key of state.reviewQueue) {
        const col = COLUMNS.find(c => c.key === key);
        state.decisions.set(key, { status: STATUS.AUTO, cde: col.suggested_cde });
      }
      state.reviewQueue = [];
      showDone();
      break;
    case 'audit-safe':
      openAuditDialog('safe');
      break;
    case 'audit-empty':
      openAuditDialog('empty');
      break;
    case 'close-audit':
      $('#audit-dialog').classList.add('hidden');
      break;

    case 'exit-walkthrough':
      // Back to welcome — preserves any decisions already made.
      showScreen('welcome');
      break;
    case 'prev':         navigate(-1); break;
    case 'skip':         skipCurrent(); break;
    case 'confirm':      confirmCurrent(); break;
    case 'change-cde':   openPicker('change'); break;
    case 'pick-no-map':  setCurrentDecision(STATUS.NO_MAPPING, null); navigate(1); break;
    case 'rename':       toggleRename(); break;
    case 'close-picker': $('#picker-dialog').classList.add('hidden'); break;

    case 'harmonize':
      alert('(Prototype) Would advance to Stage 3 with the captured decisions.');
      break;
    case 'restart':
      seedDecisions();
      buildReviewQueue();
      state.currentIdx = 0;
      showScreen('welcome');
      break;

    default:
      if (action.startsWith('alt-pick:')) {
        const cde = action.slice('alt-pick:'.length);
        setCurrentDecision(STATUS.MANUAL, cde);
        renderCard();
      }
      if (action.startsWith('picker-pick:')) {
        const cde = action.slice('picker-pick:'.length);
        setCurrentDecision(STATUS.MANUAL, cde);
        $('#picker-dialog').classList.add('hidden');
        renderCard();
      }
  }
}

function onConciergeKey(e) {
  if (!isWalkthroughVisible()) return;
  // Don't hijack typing inside inputs.
  if (e.target.matches('input, textarea')) return;

  switch (e.key) {
    case 'Enter':      e.preventDefault(); confirmCurrent(); break;
    case 'ArrowLeft':  navigate(-1); break;
    case 'ArrowRight': navigate(1); break;
    case 's': case 'S': skipCurrent(); break;
    case 'c': case 'C': openPicker('change'); break;
    case 'r': case 'R': toggleRename(); break;
  }
}

const isWalkthroughVisible = () => !$('#screen-walkthrough')?.classList.contains('hidden');


/* ────────────────────────────────────────────────────────────
   Walkthrough entrypoint (prototype 2)
   ──────────────────────────────────────────────────────────── */
export async function initWalkthrough() {
  state.mode = 'walkthrough';
  seedDecisions();
  buildReviewQueue();

  await mountProgressTracker();

  renderRail();
  renderCard();
  document.body.addEventListener('click', onWalkthroughClick);
  document.addEventListener('keydown', onConciergeKey);
}

function onWalkthroughClick(e) {
  const action = e.target.closest('[data-action]')?.dataset.action;
  if (!action) return;

  switch (action) {
    case 'prev': navigate(-1); break;
    case 'next': navigate(1); break;
    case 'skip': skipCurrent(); break;
    case 'confirm': confirmCurrent(); break;
    case 'change-cde': openPicker('change'); break;
    case 'pick-no-map': setCurrentDecision(STATUS.NO_MAPPING, null); navigate(1); break;
    case 'rename': toggleRename(); break;
    case 'flag': toggleFlag(); break;
    case 'close-picker': $('#picker-dialog').classList.add('hidden'); break;

    case 'queue-jump': {
      const key = e.target.closest('[data-key]')?.dataset.key;
      if (key) jumpTo(key);
      break;
    }

    case 'toggle-safe': {
      state.showSafeInQueue = !state.showSafeInQueue;
      buildReviewQueueForWalkthrough();
      renderRail();
      renderCard();
      break;
    }

    case 'bulk-confirm-safe':
      // Bulk-trust all currently auto-safe columns explicitly.
      for (const col of COLUMNS) {
        if (col.classification === 'auto_safe') {
          state.decisions.set(col.key, { status: STATUS.CONFIRMED, cde: col.suggested_cde });
        }
      }
      renderRail();
      renderCard();
      break;

    case 'harmonize':
      alert('(Prototype) Would advance to Stage 3.');
      break;

    default:
      if (action.startsWith('alt-pick:')) {
        const cde = action.slice('alt-pick:'.length);
        setCurrentDecision(STATUS.MANUAL, cde);
        renderRail();
        renderCard();
      }
      if (action.startsWith('picker-pick:')) {
        const cde = action.slice('picker-pick:'.length);
        setCurrentDecision(STATUS.MANUAL, cde);
        $('#picker-dialog').classList.add('hidden');
        renderRail();
        renderCard();
      }
  }
}

/* ────────────────────────────────────────────────────────────
   Value-first entrypoint (prototype 3)
   ──────────────────────────────────────────────────────────── */
export async function initValueFirst() {
  state.mode = 'value_first';
  seedDecisions();
  buildValueFirstQueue();

  // Start in the rewrite section on its first column (worst-overlap first).
  const groups = vfGroupsNow();
  state.vfCurrentSection = 'rewrite';
  state.vfCurrentColumnKey = groups.rewrite[0]?.key ?? COLUMNS[0]?.key ?? null;

  await mountProgressTracker();

  renderVfTrack();
  renderVfCard();

  document.body.addEventListener('click', onValueFirstClick);
  document.addEventListener('keydown', onValueFirstKey);
}

function onValueFirstClick(e) {
  const action = e.target.closest('[data-action]')?.dataset.action;

  // Click-outside closes the picker dropdown.
  if (state.vfPickerOpenFor && !e.target.closest('.vf-picker-wrap')) {
    state.vfPickerOpenFor = null;
    renderVfCard();
  }
  if (!action) return;

  switch (action) {
    case 'prev': vfNavigate(-1); break;
    case 'confirm': vfConfirmCurrent(); break;

    case 'toggle-picker': {
      const col = currentColumn();
      state.vfPickerOpenFor = state.vfPickerOpenFor === col.key ? null : col.key;
      renderVfCard();
      break;
    }

    case 'jump-section': {
      const sectionId = e.target.closest('[data-section]')?.dataset.section;
      if (sectionId) vfJumpToSection(sectionId);
      break;
    }
    case 'jump-column': {
      const key = e.target.closest('[data-key]')?.dataset.key;
      if (key) vfJumpToColumn(key);
      break;
    }

    default:
      if (action.startsWith('picker-pick:')) {
        const col = currentColumn();
        if (!col) break;
        const cde = action.slice('picker-pick:'.length);
        const fromSection = vfSection(col);

        // Stage the change as a pending preview. The decision isn't committed
        // to state.decisions yet — vfSection still classifies the column
        // where it lives, but vfChipHtml renders a ghost in destination.
        const toSection = sectionForCde(col, cde);
        state.vfPending = { columnKey: col.key, fromSection, toSection, cde };
        state.vfPickerOpenFor = null;
        renderVfTrack();
        renderVfCard();
      }
  }
}

/** Section a column WOULD land in if assigned to `cdeKey`, without committing. */
function sectionForCde(col, cdeKey) {
  if (cdeKey === '__none__') return 'empty';
  const cde = CDE_CATALOG[cdeKey];
  if (!cde) return 'rewrite';
  if (cde.type === 'passthrough') return 'passthrough';
  const overlap = effectiveOverlap(col, cde);
  return (overlap != null && overlap >= 1.0) ? 'match' : 'rewrite';
}

function onValueFirstKey(e) {
  if (e.target.matches('input, textarea')) return;
  switch (e.key) {
    case 'Enter':      e.preventDefault(); vfConfirmCurrent(); break;
    case 'ArrowLeft':  vfNavigate(-1); break;
    case 'ArrowRight': vfNavigate(1); break;
    case 's': case 'S': vfSkipCurrent(); break;
    case 'c': case 'C': openPicker(); break;
    case 'r': case 'R': toggleRename(); renderVfCard(); break;
  }
}

/**
 * Confirm the current column's mapping. If a picker change is pending,
 * commit it (the chip migrates to its new section). Either way, the user
 * advances to the next column *in their current section* — they do not
 * follow a migrated column out of the section they're working in.
 */
function vfConfirmCurrent() {
  const col = currentColumn();
  if (!col) return;

  // Snapshot the section view before any state mutation.
  const sectionId = state.vfCurrentSection;
  const sectionCols = vfGroupsNow()[sectionId] ?? [];
  const idx = sectionCols.findIndex(c => c.key === col.key);

  // Commit the pending picker change (if any) or just confirm the current rec.
  if (state.vfPending && state.vfPending.columnKey === col.key) {
    const { cde } = state.vfPending;
    if (cde === '__none__') {
      state.decisions.set(col.key, { status: STATUS.NO_MAPPING, cde: null });
    } else {
      state.decisions.set(col.key, { status: STATUS.MANUAL, cde });
    }
    state.vfPending = null;
  } else {
    const decision = state.decisions.get(col.key) ?? {};
    const status = decision.status === STATUS.MANUAL ? STATUS.MANUAL : STATUS.CONFIRMED;
    state.decisions.set(col.key, { status, cde: decision.cde ?? col.suggested_cde });
  }

  // Advance to the next column in the user's section (not the migrated column's
  // new section). If the migration emptied the next slot, fall back to the
  // section's first remaining unreviewed column.
  vfAdvanceWithinSection(sectionId, idx);
}

function vfAdvanceWithinSection(sectionId, oldIdx) {
  const groups = vfGroupsNow();
  const sectionCols = groups[sectionId] ?? [];
  // The "next" position depends on whether the current column is still in
  // this section (no migration) or left (migration). Either way, oldIdx is
  // the slot the user just finished; the next slot is at oldIdx (if the
  // column migrated out, the list shifted) or oldIdx + 1 (if it stayed).
  const stillHere = sectionCols.some(c => c.key === state.vfCurrentColumnKey);
  const nextIdx = stillHere ? oldIdx + 1 : oldIdx;
  const next = sectionCols[nextIdx];
  if (next) {
    state.vfCurrentColumnKey = next.key;
  } else {
    // End of section. Park on the last column so the user sees the state,
    // then they can pick another section.
    const last = sectionCols[sectionCols.length - 1];
    if (last) state.vfCurrentColumnKey = last.key;
  }
  renderVfTrack();
  renderVfCard();
}

function vfNavigate(delta) {
  const sectionCols = vfGroupsNow()[state.vfCurrentSection] ?? [];
  const idx = sectionCols.findIndex(c => c.key === state.vfCurrentColumnKey);
  const next = idx + delta;
  if (next < 0 || next >= sectionCols.length) return;
  state.vfCurrentColumnKey = sectionCols[next].key;
  state.vfPending = null;
  renderVfTrack();
  renderVfCard();
}

function vfJumpToSection(sectionId) {
  state.vfCurrentSection = sectionId;
  const groups = vfGroupsNow();
  state.vfCurrentColumnKey = groups[sectionId]?.[0]?.key ?? state.vfCurrentColumnKey;
  state.vfPending = null;
  renderVfTrack();
  renderVfCard();
}

function vfJumpToColumn(columnKey) {
  const col = COLUMNS.find(c => c.key === columnKey);
  if (!col) return;
  state.vfCurrentSection = vfSection(col);
  state.vfCurrentColumnKey = columnKey;
  state.vfPending = null;
  renderVfTrack();
  renderVfCard();
}


/* ────────────────────────────────────────────────────────────
   Value-first: tabs + active-section context

   The strip is one bordered panel containing:
     - a row of section tabs (short terse labels)
     - an active-section context block: plain-English explainer of
       what the category means + chips for the section's columns
   ──────────────────────────────────────────────────────────── */
/**
 * Render all four sections side-by-side, each with its chip list visible.
 * The user's current section is highlighted with the explainer text; other
 * sections show just header + chips for at-a-glance navigation.
 *
 * Pending migrations get rendered as a dashed ghost chip in the destination
 * section while the originating chip stays visible (faded) in its old home.
 */
function renderVfTrack() {
  const stripEl = $('#vf-strip');
  if (!stripEl) return;

  const groups = vfGroupsNow();
  const currentCol = currentColumn();
  const activeSectionId = state.vfCurrentSection;
  const pending = state.vfPending;

  stripEl.innerHTML = VF_SECTIONS.map(section => {
    const cols = groups[section.id] ?? [];
    const reviewed = cols.filter(isReviewed).length;
    const isActive = section.id === activeSectionId;

    // A pending column displays a ghost in its destination section.
    const showGhost = pending && pending.toSection === section.id && pending.toSection !== pending.fromSection;
    const ghostCol = showGhost ? COLUMNS.find(c => c.key === pending.columnKey) : null;

    return `
      <section class="vf-section vf-section--${section.tone} ${isActive ? 'vf-section--active' : ''}">
        <header class="vf-section-head" data-action="jump-section" data-section="${section.id}">
          <span class="vf-section-icon">${sectionIcon(section.id)}</span>
          <span class="vf-section-label">${esc(section.label)}</span>
          <span class="vf-section-count">${reviewed}/${cols.length}</span>
        </header>
        ${isActive ? `<p class="vf-section-explainer">${esc(section.explainer)}</p>` : ''}
        <div class="vf-section-chips">
          ${cols.map(c => vfChipHtml(c, currentCol, pending)).join('')}
          ${ghostCol ? vfGhostChipHtml(ghostCol, pending) : ''}
        </div>
      </section>
    `;
  }).join('');
}

function isReviewed(col) {
  const d = state.decisions.get(col.key);
  return d?.status && d.status !== STATUS.PENDING;
}

function vfChipHtml(col, currentCol, pending) {
  const d = state.decisions.get(col.key) ?? {};
  const isCurrent = currentCol?.key === col.key;
  const done = d.status === STATUS.CONFIRMED || d.status === STATUS.MANUAL || d.status === STATUS.NO_MAPPING;
  const skipped = d.status === STATUS.SKIPPED;
  // The originating chip of a pending move stays in its current section
  // but appears faded to indicate "about to leave."
  const isLeaving = pending && pending.columnKey === col.key && pending.fromSection !== pending.toSection;

  let cls = 'vf-chip';
  if (isCurrent) cls += ' vf-chip--current';
  if (done) cls += ' vf-chip--done';
  if (skipped) cls += ' vf-chip--skipped';
  if (isLeaving) cls += ' vf-chip--leaving';

  const glyph = done ? '✓' : skipped ? '↷' : isCurrent ? '▶' : '○';
  const overlap = effectiveOverlapFor(col);
  const overlapMeta = overlap == null ? '' : `<span class="vf-chip-meta">${pct(overlap)}</span>`;

  return `
    <button class="${cls}" data-action="jump-column" data-key="${esc(col.key)}"
            title="${esc(col.key)}${overlap != null ? ` — ${pct(overlap)} match` : ''}">
      <span class="vf-chip-glyph">${glyph}</span>
      <span class="vf-chip-name">${esc(col.key)}</span>
      ${overlapMeta}
    </button>
  `;
}

/**
 * Dashed-outline ghost chip shown in the destination section while a
 * picker change is staged. It indicates where the column will land if
 * the user confirms. Cleared if they pick something else or navigate away.
 */
function vfGhostChipHtml(col, pending) {
  const cde = CDE_CATALOG[pending.cde];
  const overlap = pending.cde === '__none__' ? null : effectiveOverlap(col, cde);
  const overlapMeta = overlap == null ? '' : `<span class="vf-chip-meta">${pct(overlap)}</span>`;
  return `
    <span class="vf-chip vf-chip--ghost" title="${esc(col.key)} — pending migration, confirm to commit">
      <span class="vf-chip-glyph">↪</span>
      <span class="vf-chip-name">${esc(col.key)}</span>
      ${overlapMeta}
    </span>
  `;
}

function sectionIcon(id) {
  return { rewrite: '✎', match: '✓', passthrough: '→', empty: '—' }[id] ?? '·';
}


/* ────────────────────────────────────────────────────────────
   Value-first: popup-style card

   Mirrors the existing Stage 2 takeover dialog: a single white card
   with a thin head row (column name + rename + counter + nav) and
   a calm two-pane body. Value-match shows up as a single status
   line in the right-pane head, not as a heavy hero block.
   ──────────────────────────────────────────────────────────── */
function renderVfCard() {
  const col = currentColumn();
  const host = $('#vf-stage');
  if (!col || !host) return;

  const decision = state.decisions.get(col.key);
  // Picker change is previewed in the card *immediately* — status line and
  // PV list update right away, so users can see what the new mapping looks
  // like before deciding to confirm.
  const pending = state.vfPending?.columnKey === col.key ? state.vfPending : null;
  const cdeKey = pending ? pending.cde : (decision?.cde ?? col.suggested_cde);
  const effectiveCdeKey = cdeKey === '__none__' ? null : cdeKey;
  const cdeInfo = effectiveCdeKey ? CDE_CATALOG[effectiveCdeKey] : null;
  const sectionId = pending ? pending.toSection : vfSection(col);
  const isManual = !!pending || decision?.status === STATUS.MANUAL;

  // Position within the user's current section, not the global queue.
  const sectionCols = vfGroupsNow()[state.vfCurrentSection] ?? [];
  const idx = Math.max(0, sectionCols.findIndex(c => c.key === col.key));
  const queueLen = sectionCols.length;

  // Rewrite cards get a warning Next button (the only confirmation that
  // mutates data). Match cards get a netrias-tinted header to echo
  // Stage 4's pv-conformant treatment.
  const nextCls = sectionId === 'rewrite' ? 'vf-takeover-btn vf-takeover-btn--warn' : 'vf-takeover-btn vf-takeover-btn--primary';
  const nextLabel = sectionId === 'rewrite' ? 'Confirm rewrite →' : 'Next →';
  const headCls = sectionId === 'match' ? 'vf-takeover-head vf-takeover-head--conformant' : 'vf-takeover-head';

  // While a picker change is staged, show a small banner explaining what
  // confirming will do: migrate to a different section and advance to the
  // next column in the user's current section.
  const movedHint = pending && pending.fromSection !== pending.toSection
    ? `<div class="vf-moved-hint">↪ Confirming sends this column to <strong>${esc(VF_SECTIONS.find(s => s.id === pending.toSection).label)}</strong>. You'll continue in <strong>${esc(VF_SECTIONS.find(s => s.id === state.vfCurrentSection).label)}</strong>.</div>`
    : '';

  host.innerHTML = `
    <article class="vf-takeover">
      <header class="${headCls}">
        <div class="vf-takeover-head-left">
          <h2 class="vf-takeover-name" title="${esc(col.key)}">${esc(col.key)}</h2>
        </div>
        <div class="vf-takeover-head-right">
          <span class="vf-takeover-counter">${idx + 1} of ${queueLen}</span>
          <button class="vf-takeover-btn" data-action="prev" ${idx <= 0 ? 'disabled' : ''}>← Prev</button>
          <button class="${nextCls}" data-action="confirm">${nextLabel}</button>
        </div>
      </header>
      ${movedHint}

      <div class="vf-takeover-body">
        ${vfSourcePaneHtml(col, cdeInfo)}
        ${vfTargetPaneHtml(col, cdeKey, cdeInfo, sectionId, isManual)}
      </div>
    </article>
  `;
}

function vfSourcePaneHtml(col, cdeInfo) {
  if (col.distinct_values.length === 0) {
    return `
      <section class="vf-pane vf-pane--source">
        <div class="vf-pane-head">
          <span class="vf-pane-title">Your column</span>
          <span class="vf-pane-meta">0 values</span>
        </div>
        <div class="vf-pane-empty">No values in this column.</div>
      </section>
    `;
  }

  const pvSet = (cdeInfo?.type === 'pv' && Array.isArray(cdeInfo.pvs)) ? new Set(cdeInfo.pvs) : null;
  const matched = pvSet
    ? col.distinct_values.filter(v => pvSet.has(v.value)).length
    : null;
  const summary = pvSet
    ? `${matched} of ${col.distinct_values.length} match`
    : `${col.value_count.toLocaleString()} values · ${col.distinct_count.toLocaleString()} distinct`;

  return `
    <section class="vf-pane vf-pane--source">
      <div class="vf-pane-head">
        <span class="vf-pane-title">Your column</span>
        <span class="vf-pane-meta">${summary}</span>
      </div>
      <div class="vf-pane-list">
        <div class="vf-pane-list-head">
          <span class="vf-pane-list-head-cell">Value</span>
          <span class="vf-pane-list-head-cell vf-pane-list-head-cell--num">Count</span>
        </div>
        <ul>
          ${col.distinct_values.map(v => vfValueRowHtml(v, pvSet)).join('')}
        </ul>
      </div>
    </section>
  `;
}

function vfValueRowHtml(v, pvSet) {
  const isMatch = pvSet ? pvSet.has(v.value) : null;
  const cls = isMatch === true ? 'vf-value-row--match' : isMatch === false ? 'vf-value-row--miss' : '';
  const glyph = isMatch === true ? '✓' : isMatch === false ? '✗' : '';
  return `
    <li class="vf-value-row ${cls}">
      <span class="vf-value-glyph">${glyph}</span>
      <span class="vf-value-text" title="${esc(v.value)}">${esc(v.value)}</span>
      <span class="vf-value-count">${v.count.toLocaleString()}</span>
    </li>
  `;
}

function vfTargetPaneHtml(col, cdeKey, cdeInfo, sectionId, isManual) {
  if (sectionId === 'empty' || !cdeKey) {
    return `
      <section class="vf-pane vf-pane--target">
        <div class="vf-pane-head">
          <span class="vf-pane-title">Target standard</span>
          <span class="vf-pane-meta">${sectionId === 'empty' ? 'No values to map' : 'No mapping'}</span>
        </div>
        <div class="vf-pane-empty vf-pane-empty--soft">
          ${sectionId === 'empty'
            ? 'This column has no values, so nothing will be harmonized. Reviewed for awareness only.'
            : 'This column will be skipped. Pick a target if you want it harmonized.'}
        </div>
      </section>
    `;
  }

  const status = vfStatusLineHtml(col, cdeInfo, sectionId);

  return `
    <section class="vf-pane vf-pane--target">
      <div class="vf-pane-head">
        <span class="vf-pane-title">Target standard</span>
        ${status}
      </div>
      ${vfPickerHtml(col, cdeInfo, isManual, sectionId)}
      ${vfTargetBodyHtml(col, cdeInfo, sectionId)}
    </section>
  `;
}

/**
 * Single-line value-match status, shown in the right pane header so it sits
 * exactly where the real takeover puts its status indicator.
 */
function vfStatusLineHtml(col, cdeInfo, sectionId) {
  if (sectionId === 'passthrough') {
    return `<span class="vf-status vf-status--info">→ Pass-through · values unchanged</span>`;
  }
  const p = pct(col.overlap);
  const t = tier(col.overlap);
  if (sectionId === 'match') {
    // Mirrors Stage 4 legend: ✓ green for conformant.
    return `<span class="vf-status vf-status--good"><span class="vf-status-glyph">✓</span> ${p} match · no rewrites</span>`;
  }
  // rewrite — ⚠ amber for non-conformant (Stage 4 legend style)
  const total = col.distinct_values.length;
  const matched = col.overlap == null ? 0 : Math.round(col.overlap * total);
  const rewriteN = Math.max(0, total - matched);
  return `<span class="vf-status vf-status--${t === 'low' ? 'bad' : 'warn'}"><span class="vf-status-glyph">⚠</span> ${p} match · ${rewriteN} of ${total} will be rewritten</span>`;
}

/**
 * Picker button styled like Stage 2's .cde-picker. Click toggles a
 * dropdown beneath it (vfPickerDropdownHtml). The dropdown mocks the
 * real shape: AI rec section, divider, "other standards" section, search.
 */
function vfPickerHtml(col, cdeInfo, isManual, sectionId) {
  const typeBadge = cdeInfo.type === 'passthrough'
    ? `<span class="vf-type-badge">Pass-through</span>`
    : '';
  const aiBadge = !isManual ? `<span class="vf-ai-badge">✦ AI rec</span>` : '';

  const colKey = col.key;
  const isOpen = state.vfPickerOpenFor === colKey;
  return `
    <div class="vf-picker-wrap">
      <button class="vf-picker ${isOpen ? 'vf-picker--open' : ''}" data-action="toggle-picker">
        <span class="vf-picker-name">${esc(cdeInfo.label)}</span>
        ${typeBadge}
        ${aiBadge}
        <span class="vf-picker-caret">▾</span>
      </button>
      ${isOpen ? vfPickerDropdownHtml(col, cdeInfo) : ''}
    </div>
  `;
}

function vfPickerDropdownHtml(col, cdeInfo) {
  // AI recs: the column's listed alternatives (plus the suggested CDE itself,
  // if it's not already in alternatives — keeps the picker honest).
  const aiKeys = new Set();
  (col.alternatives ?? []).forEach(a => aiKeys.add(a.cde));
  if (col.suggested_cde) aiKeys.add(col.suggested_cde);
  const aiOptions = [...aiKeys]
    .map(k => ({ key: k, info: CDE_CATALOG[k], overlap: col.alternatives?.find(a => a.cde === k)?.overlap
                                                  ?? (k === col.suggested_cde ? col.overlap : null) }))
    .filter(o => o.info);

  // "Other standards": everything else in the catalog, alphabetical. Mocked
  // — no real match scores against this column for items the AI didn't rank.
  const otherOptions = Object.entries(CDE_CATALOG)
    .filter(([k]) => !aiKeys.has(k))
    .map(([key, info]) => ({ key, info, overlap: null }))
    .sort((a, b) => a.info.label.localeCompare(b.info.label));

  return `
    <div class="vf-dropdown" id="vf-picker-dropdown">
      <div class="vf-dd-search">
        <input type="search" placeholder="Filter standards by name or description…" autocomplete="off" />
      </div>
      <div class="vf-dd-list">
        <div class="vf-dd-section">
          <div class="vf-dd-section-label">✦ AI recommendations</div>
          ${aiOptions.map(o => vfDdItemHtml(o, 'ai', col)).join('')}
          ${vfDdItemHtml({ key: '__none__', info: { label: 'No Mapping', description: 'Skip this column. Values will not be harmonized.' }, overlap: null }, 'none', col)}
        </div>
        <div class="vf-dd-divider"></div>
        <div class="vf-dd-section">
          <div class="vf-dd-section-label">Other standards</div>
          ${otherOptions.slice(0, 12).map(o => vfDdItemHtml(o, 'alt', col)).join('')}
          ${otherOptions.length > 12 ? `<div class="vf-dd-more">+ ${otherOptions.length - 12} more — type to filter</div>` : ''}
        </div>
      </div>
    </div>
  `;
}

function vfDdItemHtml(opt, kind, col) {
  const matchPct = opt.overlap != null
    ? `<span class="vf-dd-pct">${pct(opt.overlap)}</span>`
    : '';
  const tag = kind === 'ai' ? `<span class="vf-dd-tag vf-dd-tag--ai">✦ AI</span>`
            : kind === 'none' ? `<span class="vf-dd-tag vf-dd-tag--none">no map</span>`
            : '';
  return `
    <button class="vf-dd-opt" data-action="picker-pick:${esc(opt.key)}">
      <div class="vf-dd-opt-main">
        <span class="vf-dd-opt-name">${esc(opt.info.label)}</span>
        ${tag}
      </div>
      <div class="vf-dd-opt-meta">
        <span class="vf-dd-opt-desc">${esc(opt.info.description ?? '')}</span>
        ${matchPct}
      </div>
    </button>
  `;
}

function vfTargetBodyHtml(col, cdeInfo, sectionId) {
  if (sectionId === 'passthrough') {
    return `
      <div class="vf-target-body vf-target-body--centered">
        <div class="vf-target-illu">→</div>
        <div class="vf-target-illu-title">Pass-through</div>
        <div class="vf-target-illu-sub">No permissible value list. Values are written as-is.</div>
      </div>
    `;
  }

  // PV-type. Risk note (when it adds info beyond the status line) + PV grid + alts.
  return `
    <div class="vf-target-body">
      ${col.risk_reason ? `<div class="vf-target-note vf-target-note--${tier(col.overlap) === 'low' ? 'bad' : 'warn'}">${esc(col.risk_reason)}</div>` : ''}
      ${vfPvListHtml(col, cdeInfo)}
      ${vfAltsHtml(col)}
    </div>
  `;
}

function vfPvListHtml(col, cdeInfo) {
  if (!cdeInfo?.pvs?.length) return '';
  const userValues = new Set(col.distinct_values.map(v => v.value));
  return `
    <div class="vf-target-pvs">
      <div class="vf-target-pvs-head">
        <span class="vf-pane-title">Permissible values</span>
        <span class="vf-pane-meta">${cdeInfo.pvs.length}</span>
      </div>
      <div class="vf-target-pvs-grid">
        ${cdeInfo.pvs.map(pv => {
          const used = userValues.has(pv);
          return `
            <div class="vf-target-pv ${used ? 'vf-target-pv--used' : ''}">
              <span class="vf-target-pv-glyph">${used ? '✓' : ''}</span>
              <span>${esc(pv)}</span>
            </div>
          `;
        }).join('')}
      </div>
    </div>
  `;
}

function vfAltsHtml(col) {
  const decision = state.decisions.get(currentColumn()?.key) ?? {};
  const cdeKey = decision.cde ?? currentColumn()?.suggested_cde;
  const alts = (currentColumn()?.alternatives ?? []).filter(a => a.cde !== cdeKey);
  if (alts.length === 0) return '';
  return `
    <div class="vf-target-alts">
      <div class="vf-pane-title">Other candidates</div>
      ${alts.map(a => `
        <button class="vf-alt-row" data-action="alt-pick:${esc(a.cde)}">
          <span class="vf-alt-row-name">${esc(a.label)}</span>
          <span class="vf-alt-row-pct">${pct(a.overlap)} match</span>
          <span class="vf-alt-row-cta">Use →</span>
        </button>
      `).join('')}
    </div>
  `;
}


/* ────────────────────────────────────────────────────────────
   Sign-off + overview dialogs
   ──────────────────────────────────────────────────────────── */
function openSignoff() {
  const groups = state.vfGroups ?? { rewrite: [], match: [], passthrough: [], empty: [] };
  const sectionStats = VF_SECTIONS.map(section => {
    const cols = groups[section.id] ?? [];
    const reviewed = cols.filter(c => {
      const d = state.decisions.get(c.key);
      return d?.status && d.status !== STATUS.PENDING;
    }).length;
    return { section, total: cols.length, reviewed, cols };
  });

  $('#signoff-body').innerHTML = `
    <p class="welcome-sub" style="margin-bottom: var(--space-5);">
      You'll harmonize <strong>${COLUMNS.length - groups.empty.length}</strong> columns. Anything not reviewed will keep the AI's pick.
    </p>
    ${sectionStats.map(({ section, total, reviewed, cols }) => `
      <div class="signoff-section signoff-section--${section.tone}">
        <div class="signoff-section-row">
          <span class="signoff-section-icon">${sectionIcon(section.id)}</span>
          <div class="signoff-section-text">
            <div class="signoff-section-title">${esc(section.label)}</div>
            <div class="signoff-section-sub">${reviewed} of ${total} reviewed</div>
          </div>
        </div>
        ${reviewed < total ? `
          <div class="signoff-section-unreviewed">
            Unreviewed: ${cols.filter(c => {
              const d = state.decisions.get(c.key);
              return !d?.status || d.status === STATUS.PENDING;
            }).slice(0, 6).map(c => `<code>${esc(c.key)}</code>`).join(', ')}${cols.filter(c => {
              const d = state.decisions.get(c.key);
              return !d?.status || d.status === STATUS.PENDING;
            }).length > 6 ? '…' : ''}
          </div>
        ` : ''}
      </div>
    `).join('')}
  `;
  $('#signoff-dialog').classList.remove('hidden');
}

function finalizeSignoff() {
  // Mark everything still pending as auto-confirmed (the user trusts the AI rec).
  for (const col of COLUMNS) {
    const d = state.decisions.get(col.key);
    if (!d?.status || d.status === STATUS.PENDING) {
      state.decisions.set(col.key, { status: STATUS.AUTO, cde: col.suggested_cde });
    }
  }
  alert('(Prototype) Would advance to Stage 3 with the captured decisions.');
  $('#signoff-dialog').classList.add('hidden');
}

function openOverview() {
  const groups = state.vfGroups ?? { rewrite: [], match: [], passthrough: [], empty: [] };
  $('#overview-body').innerHTML = VF_SECTIONS.map(section => {
    const cols = groups[section.id] ?? [];
    if (cols.length === 0) return '';
    return `
      <div class="overview-section overview-section--${section.tone}">
        <div class="overview-section-head">
          <span class="signoff-section-icon">${sectionIcon(section.id)}</span>
          ${esc(section.label)} <span class="overview-section-count">${cols.length}</span>
        </div>
        ${cols.map(col => {
          const d = state.decisions.get(col.key) ?? {};
          const isCurrent = state.reviewQueue[state.currentIdx] === col.key;
          const statusGlyph = d.status === STATUS.CONFIRMED || d.status === STATUS.MANUAL || d.status === STATUS.NO_MAPPING ? '✓'
                            : d.status === STATUS.SKIPPED ? '↷'
                            : '○';
          return `
            <div class="overview-row ${isCurrent ? 'overview-row--current' : ''}" data-action="jump-column" data-key="${esc(col.key)}">
              <span class="overview-row-glyph">${statusGlyph}</span>
              <span class="overview-row-name">${esc(col.key)}</span>
              <span class="overview-row-meta">${col.overlap != null ? pct(col.overlap) : ''}</span>
            </div>
          `;
        }).join('')}
      </div>
    `;
  }).join('');
  $('#overview-dialog').classList.remove('hidden');
}


function buildReviewQueueForWalkthrough() {
  state.reviewQueue = COLUMNS
    .filter(c => {
      if (c.classification === 'needs_review') return true;
      if (state.showSafeInQueue && c.classification === 'auto_safe') return true;
      return false;
    })
    .map(c => c.key);

  if (state.currentIdx >= state.reviewQueue.length) state.currentIdx = 0;
}


/* ────────────────────────────────────────────────────────────
   Decision actions (shared)
   ──────────────────────────────────────────────────────────── */
function setCurrentDecision(status, cde) {
  const col = currentColumn();
  if (!col) return;
  const prev = state.decisions.get(col.key) ?? {};
  state.decisions.set(col.key, { ...prev, status, cde });
}

function confirmCurrent() {
  const col = currentColumn();
  if (!col) return;
  const decision = state.decisions.get(col.key);
  // If user hasn't manually changed it, treat as a confirm of the AI rec.
  const status = decision?.status === STATUS.MANUAL ? STATUS.MANUAL : STATUS.CONFIRMED;
  state.decisions.set(col.key, { status, cde: decision?.cde ?? col.suggested_cde });
  navigate(1);
}

function skipCurrent() {
  const col = currentColumn();
  if (!col) return;
  state.decisions.set(col.key, {
    ...state.decisions.get(col.key),
    status: STATUS.SKIPPED,
  });
  navigate(1);
}

function toggleRename() {
  const col = currentColumn();
  if (!col) return;
  const prev = state.decisions.get(col.key) ?? {};
  const suggestedCde = prev.cde ?? col.suggested_cde;
  const renamed_to = prev.renamed_to ? null : suggestedCde;
  state.decisions.set(col.key, { ...prev, renamed_to });
  if (state.mode === 'walkthrough') renderRail();
  renderCard();
}

function toggleFlag() {
  const col = currentColumn();
  if (!col) return;
  if (state.flagged.has(col.key)) state.flagged.delete(col.key);
  else state.flagged.add(col.key);
  renderRail();
  renderCard();
}

function navigate(delta) {
  const next = state.currentIdx + delta;
  if (next < 0) return;
  if (next >= state.reviewQueue.length) {
    // Finished the queue.
    if (state.mode === 'concierge') showDone();
    else {
      // Walkthrough: bounce back to the first incomplete item, or stay.
      const incomplete = state.reviewQueue.findIndex(k =>
        state.decisions.get(k)?.status === STATUS.PENDING);
      if (incomplete >= 0) {
        state.currentIdx = incomplete;
        renderRail();
        renderCard();
      } else {
        // All review items resolved.
        if (confirm('All columns reviewed. Continue to Harmonize?')) {
          alert('(Prototype) Would advance to Stage 3.');
        }
      }
    }
    return;
  }
  state.currentIdx = next;
  if (state.mode === 'walkthrough') renderRail();
  renderCard();
}

function jumpTo(key) {
  const idx = state.reviewQueue.indexOf(key);
  if (idx < 0) return;
  state.currentIdx = idx;
  renderRail();
  renderCard();
}


/* ────────────────────────────────────────────────────────────
   Screen transitions
   ──────────────────────────────────────────────────────────── */
function showScreen(name) {
  for (const id of ['welcome', 'walkthrough', 'done']) {
    const el = $(`#screen-${id}`);
    if (!el) continue;
    el.classList.toggle('hidden', id !== name);
  }
}

function enterWalkthrough() {
  if (state.reviewQueue.length === 0) {
    showDone();
    return;
  }
  state.currentIdx = 0;
  showScreen('walkthrough');
  renderCard();
}

function showDone() {
  $('#done-reviewed-count').textContent =
    [...state.decisions.values()].filter(d =>
      d.status === STATUS.CONFIRMED ||
      d.status === STATUS.MANUAL ||
      d.status === STATUS.NO_MAPPING ||
      d.status === STATUS.SKIPPED
    ).length;
  $('#done-auto-count').textContent =
    [...state.decisions.values()].filter(d => d.status === STATUS.AUTO).length;
  $('#done-empty-count').textContent =
    [...state.decisions.values()].filter(d => d.status === STATUS.EMPTY).length;

  $('#done-summary').innerHTML = COLUMNS.map(col => doneRowHtml(col)).join('');
  showScreen('done');
}

function doneRowHtml(col) {
  const d = state.decisions.get(col.key) ?? {};
  const display = d.renamed_to ?? d.cde ?? '—';
  let tag, mark, tagClass;
  switch (d.status) {
    case STATUS.CONFIRMED: mark = '✓'; tag = 'AI'; tagClass = 'ai'; break;
    case STATUS.MANUAL:    mark = '✓'; tag = 'Manual'; tagClass = 'manual'; break;
    case STATUS.AUTO:      mark = '✓'; tag = 'AI'; tagClass = 'ai'; break;
    case STATUS.NO_MAPPING:mark = '∅'; tag = 'Skipped'; tagClass = 'skip'; break;
    case STATUS.SKIPPED:   mark = '?'; tag = 'For later'; tagClass = 'skip'; break;
    case STATUS.EMPTY:     mark = '—'; tag = 'Empty'; tagClass = 'empty'; break;
    default:               mark = '·'; tag = 'Pending'; tagClass = 'skip';
  }
  return `
    <div class="done-row">
      <span class="done-row-mark done-row-mark--${tagClass === 'ai' ? 'ok' : tagClass}">${mark}</span>
      <span class="done-row-col">${esc(col.key)}</span>
      <span><span class="done-row-arrow">→</span> <span class="done-row-cde">${esc(display)}</span></span>
      <span class="done-row-tag done-row-tag--${tagClass}">${tag}</span>
    </div>
  `;
}


/* ────────────────────────────────────────────────────────────
   Audit dialog (concierge — show auto-confirmed or empty)
   ──────────────────────────────────────────────────────────── */
function openAuditDialog(which) {
  const title = which === 'safe' ? 'Auto-confirmed columns' : 'Empty columns';
  const list = COLUMNS.filter(c =>
    which === 'safe' ? c.classification === 'auto_safe' : c.classification === 'empty');

  $('#audit-title').textContent = `${title} (${list.length})`;
  $('#audit-body').innerHTML = list.map(col => `
    <div class="done-row">
      <span class="done-row-mark done-row-mark--ok">${which === 'safe' ? '✓' : '—'}</span>
      <span class="done-row-col">${esc(col.key)}</span>
      <span><span class="done-row-arrow">→</span> <span class="done-row-cde">${esc(col.suggested_cde ?? '—')}</span></span>
      <span class="done-row-tag done-row-tag--ai">${which === 'safe' ? 'AI' : 'Empty'}</span>
    </div>
  `).join('') || `<div class="pane-empty">No ${title.toLowerCase()}.</div>`;

  $('#audit-dialog').classList.remove('hidden');
}


/* ────────────────────────────────────────────────────────────
   Picker dialog (change CDE)
   ──────────────────────────────────────────────────────────── */
function openPicker() {
  const search = $('#picker-search');
  search.value = '';
  search.oninput = () => renderPicker(search.value);
  renderPicker('');
  $('#picker-dialog').classList.remove('hidden');
  search.focus();
}

function renderPicker(query) {
  const col = currentColumn();
  const q = query.trim().toLowerCase();
  const entries = Object.entries(CDE_CATALOG)
    .filter(([key, info]) =>
      !q ||
      key.toLowerCase().includes(q) ||
      info.label.toLowerCase().includes(q) ||
      info.description.toLowerCase().includes(q))
    .slice(0, 50);

  // Estimate overlap for non-listed CDEs as 0; real app would precompute.
  const overlapFor = (key) => {
    const alt = col?.alternatives?.find(a => a.cde === key);
    if (alt) return alt.overlap;
    if (key === col?.suggested_cde) return col?.overlap;
    return null;
  };

  $('#picker-body').innerHTML = entries.map(([key, info]) => {
    const ov = overlapFor(key);
    const ovPct = ov == null ? '' : `<span class="picker-row-pct">${pct(ov)} match</span>`;
    return `
      <div class="picker-row" data-action="picker-pick:${esc(key)}">
        <div>
          <div class="picker-row-name">${esc(info.label)}</div>
          <div class="picker-row-desc">${esc(info.description)}</div>
        </div>
        ${ovPct}
        <span class="alt-card-swap">Select →</span>
      </div>
    `;
  }).join('');
}


/* ────────────────────────────────────────────────────────────
   Card rendering (the per-column UI)
   ──────────────────────────────────────────────────────────── */
function renderCard() {
  const col = currentColumn();
  const host = $('#card-host');
  if (!col || !host) return;

  const decision = state.decisions.get(col.key);
  const cdeKey = decision?.cde ?? col.suggested_cde;
  const cdeInfo = cdeKey ? CDE_CATALOG[cdeKey] : null;
  const isPassthrough = cdeInfo?.type === 'passthrough';
  const isManual = decision?.status === STATUS.MANUAL;
  const isFlagged = state.flagged.has(col.key);

  // Top-bar pips and counter (concierge only — walkthrough hides these via CSS).
  renderPips();
  renderCounter();
  renderConfirmButton(col, cdeInfo);

  host.innerHTML = `
    <article class="guided-cardpane">
      ${headHtml(col, cdeInfo, isFlagged)}
      ${riskBannerHtml(col)}
      <div class="card-body">
        ${yourValuesPaneHtml(col, cdeInfo, cdeKey)}
        ${targetPaneHtml(col, cdeKey, cdeInfo, isPassthrough, isManual)}
      </div>
    </article>
  `;
}

function renderPips() {
  const pipsEl = $('#walk-pips');
  if (!pipsEl) return;
  pipsEl.innerHTML = state.reviewQueue.map((key, i) => {
    const d = state.decisions.get(key);
    let cls = '';
    if (i === state.currentIdx) cls = ' walk-pip--current';
    else if (d?.status === STATUS.CONFIRMED || d?.status === STATUS.MANUAL || d?.status === STATUS.NO_MAPPING) cls = ' walk-pip--done';
    else if (d?.status === STATUS.SKIPPED) cls = ' walk-pip--skipped';
    return `<span class="walk-pip${cls}"></span>`;
  }).join('');
}

function renderCounter() {
  const counterEl = $('#walk-counter-text');
  if (!counterEl) return;
  counterEl.textContent = `Reviewing ${state.currentIdx + 1} of ${state.reviewQueue.length}`;
  const auto = $('#walk-meta-auto');
  const empty = $('#walk-meta-empty');
  if (auto) auto.textContent = `${COUNTS.auto_safe} auto-confirmed`;
  if (empty) empty.textContent = `${COUNTS.empty} empty`;
}

function renderConfirmButton(col, cdeInfo) {
  const btn = $('#btn-confirm');
  const text = $('#btn-confirm-text');
  if (!btn || !text) return;

  const isRewrite = col.risk === 'case_mismatch' || (col.overlap != null && col.overlap < 1 && cdeInfo?.type === 'pv');
  if (isRewrite) {
    btn.classList.add('btn-confirm-warning');
    text.textContent = 'Confirm rewrite & next';
  } else {
    btn.classList.remove('btn-confirm-warning');
    text.textContent = 'Confirm & next';
  }
}

function headHtml(col, cdeInfo, isFlagged) {
  const decision = state.decisions.get(col.key) ?? {};
  const renamed = decision.renamed_to;
  const displayName = renamed ?? col.key;
  const renameClass = renamed ? 'card-rename-btn card-rename-active' : 'card-rename-btn';
  const renameLabel = renamed ? `→ ${esc(renamed)}` : 'Rename column';
  const riskLabel = col.risk ? RISK_LABEL[col.risk] : 'High match — quick confirm';
  const riskTone = col.risk ? RISK_TONE[col.risk] : 'success';
  const flagBtn = state.mode === 'walkthrough' ? `
    <button class="ghost-btn ghost-btn--sm" data-action="flag" title="Mark for follow-up">
      ${isFlagged ? '🚩 Flagged' : '🚩 Flag'}
    </button>` : '';

  return `
    <header class="card-head">
      <div class="card-head-titles">
        <div class="card-source-label">Your column</div>
        <div class="card-source-name">
          <h2>${esc(displayName)}</h2>
          <button class="${renameClass}" data-action="rename">${renamed ? esc(renameLabel) : renameLabel}</button>
        </div>
        <div class="card-meta">
          <span>${col.value_count.toLocaleString()} values</span>
          <span class="card-meta-sep">·</span>
          <span>${col.distinct_count.toLocaleString()} distinct</span>
        </div>
      </div>
      <div style="display:flex; gap:var(--space-3); align-items:center;">
        ${flagBtn}
        <span class="risk-badge risk-badge--${riskTone}">${esc(riskLabel)}</span>
      </div>
    </header>
  `;
}

function riskBannerHtml(col) {
  if (!col.risk) return '';
  const tone = RISK_TONE[col.risk];
  return `
    <div class="risk-banner risk-banner--${tone}">
      <span class="risk-banner-ico">${tone === 'warning' ? '⚠' : tone === 'info' ? 'ⓘ' : '!'}</span>
      <span>${esc(col.risk_reason)}</span>
    </div>
  `;
}

function yourValuesPaneHtml(col, cdeInfo, cdeKey) {
  const pvSet = (cdeInfo?.type === 'pv' && Array.isArray(cdeInfo.pvs))
    ? new Set(cdeInfo.pvs) : null;

  if (col.distinct_values.length === 0) {
    return `
      <section class="pane">
        <div class="pane-head">
          <span class="pane-title">Your values</span>
          <span class="pane-meta">No values</span>
        </div>
        <div class="pane-empty">This column has no non-null values.</div>
      </section>
    `;
  }

  const rowHtml = (v) => {
    const cls = pvSet ? (pvSet.has(v.value) ? 'value-row--match' : 'value-row--miss') : '';
    const markCls = pvSet ? (pvSet.has(v.value) ? 'value-mark--match' : 'value-mark--miss') : 'value-mark--neutral';
    const markGlyph = pvSet ? (pvSet.has(v.value) ? '✓' : '✗') : '·';
    return `
      <div class="value-row ${cls}">
        <span class="value-mark ${markCls}">${markGlyph}</span>
        <span class="value-text" title="${esc(v.value)}">${esc(v.value)}</span>
        <span class="value-count">${v.count.toLocaleString()}</span>
      </div>
    `;
  };

  const matched = pvSet
    ? col.distinct_values.filter(v => pvSet.has(v.value)).length
    : null;
  const meta = pvSet
    ? `${matched} of ${col.distinct_values.length} match the standard`
    : 'Free-text — passes through';

  return `
    <section class="pane">
      <div class="pane-head">
        <span class="pane-title">Your values</span>
        <span class="pane-meta">${meta}</span>
      </div>
      <div class="pane-body">
        ${col.distinct_values.map(rowHtml).join('')}
      </div>
    </section>
  `;
}

function targetPaneHtml(col, cdeKey, cdeInfo, isPassthrough, isManual) {
  if (!cdeKey || cdeKey === null) {
    // No mapping selected.
    return `
      <section class="pane">
        <div class="pane-head"><span class="pane-title">Target standard</span></div>
        <div class="target-card target-card--magenta">
          <div class="target-card-name">No mapping</div>
          <div class="target-card-desc">This column will be skipped. Values won't be harmonized.</div>
          <div class="target-card-actions">
            <button class="ghost-btn" data-action="change-cde">Pick a standard…</button>
          </div>
        </div>
      </section>
    `;
  }

  const overlapTier = tier(col.overlap);
  const cardCls = col.risk === 'low_overlap' || col.risk === 'wrong_mapping' ? 'target-card target-card--warning'
                : col.risk === 'case_mismatch' || col.risk === 'ambiguous' ? 'target-card target-card--magenta'
                : 'target-card';

  const aiBadge = isManual
    ? `<span class="target-card-ai target-card-ai--manual">✓ Your pick</span>`
    : `<span class="target-card-ai">✦ AI suggestion</span>`;

  const overlapBlock = isPassthrough
    ? `<div class="passthrough-card">
         <span class="passthrough-ico">→</span>
         <span>Free-text standard. Your values pass through unchanged — no PV checking.</span>
       </div>`
    : overlapMeterHtml(col);

  const altsHtml = (col.alternatives ?? [])
    .filter(a => a.cde !== cdeKey)
    .map(a => `
      <div class="alt-card" data-action="alt-pick:${esc(a.cde)}">
        <div class="alt-card-name">${esc(a.label)}</div>
        <div class="alt-card-pct">${pct(a.overlap)} match</div>
        <div class="alt-card-swap">Use instead →</div>
      </div>
    `).join('');

  const altsSection = (altsHtml || col.alternatives?.length === 0)
    ? `<div class="alternatives">
         <div class="alternatives-label">Other candidates</div>
         ${altsHtml}
         <div class="no-mapping-card" data-action="pick-no-map">
           <div>
             <div class="alt-card-name">Skip this column (no mapping)</div>
             <div class="alt-card-pct" style="color:var(--gray-500);font-weight:500;font-size:0.82rem;">Don't include in the submission.</div>
           </div>
           <span class="alt-card-swap">Skip →</span>
         </div>
       </div>`
    : '';

  const pvsListHtml = isPassthrough ? '' : pvListHtml(col, cdeInfo);

  return `
    <section class="pane">
      <div class="pane-head">
        <span class="pane-title">Target standard</span>
        <span class="pane-meta">${esc(SAMPLE_FILE.target_standard)}</span>
      </div>
      <div class="${cardCls}">
        ${aiBadge}
        <div class="target-card-name">${esc(cdeInfo?.label ?? cdeKey)}</div>
        <div class="target-card-desc">${esc(cdeInfo?.description ?? '')}</div>
        ${overlapBlock}
        <div class="target-card-actions">
          <button class="ghost-btn" data-action="change-cde">Change…</button>
        </div>
      </div>
      ${pvsListHtml}
      ${altsSection}
    </section>
  `;
}

function overlapMeterHtml(col) {
  const t = tier(col.overlap);
  const p = pct(col.overlap);
  const matched = col.distinct_values.length > 0 && col.overlap != null
    ? Math.round(col.overlap * col.distinct_values.length) : 0;
  const total = col.distinct_values.length;
  const rewriteN = total - matched;
  const note = col.overlap == null
    ? ''
    : t === 'good'
      ? `<strong>${matched}</strong> of your <strong>${total}</strong> distinct values already match the standard.`
      : `<strong>${matched}</strong> of <strong>${total}</strong> match the standard. The other <strong>${rewriteN}</strong> would be rewritten to fit.`;

  return `
    <div class="overlap-meter">
      <div class="overlap-label">
        <span>Value match</span>
        <span class="overlap-pct overlap-pct--${t}">${p}</span>
      </div>
      <div class="overlap-bar">
        <div class="overlap-bar-fill overlap-bar-fill--${t}" style="width: ${Math.max(2, (col.overlap ?? 0) * 100)}%;"></div>
      </div>
      ${note ? `<div class="overlap-explainer">${note}</div>` : ''}
    </div>
  `;
}

function pvListHtml(col, cdeInfo) {
  if (!cdeInfo?.pvs?.length) return '';
  const userValues = new Set(col.distinct_values.map(v => v.value));
  // PVs that don't appear in user values get a faded look so user can see
  // what they're committing to.
  return `
    <div class="pv-list">
      <div class="pv-list-head">Permissible values</div>
      ${cdeInfo.pvs.slice(0, 12).map(pv => {
        const used = userValues.has(pv);
        return `
          <div class="pv-row ${used ? 'pv-row--used' : ''}">
            <span class="value-mark ${used ? 'value-mark--match' : 'value-mark--neutral'}">${used ? '✓' : '·'}</span>
            <span class="value-text">${esc(pv)}</span>
          </div>
        `;
      }).join('')}
      ${cdeInfo.pvs.length > 12 ? `<div class="pv-list-head" style="text-align:center;">… and ${cdeInfo.pvs.length - 12} more</div>` : ''}
    </div>
  `;
}


/* ────────────────────────────────────────────────────────────
   Queue rail (walkthrough only)
   ──────────────────────────────────────────────────────────── */
function renderRail() {
  const railEl = $('#queue-rail');
  if (!railEl) return;

  const counts = COUNTS;
  const reviewed = COLUMNS.filter(c => c.classification === 'needs_review').filter(c => {
    const d = state.decisions.get(c.key);
    return d?.status === STATUS.CONFIRMED || d?.status === STATUS.MANUAL || d?.status === STATUS.NO_MAPPING;
  }).length;
  const totalReview = counts.needs_review;
  const progressPct = totalReview ? Math.round((reviewed / totalReview) * 100) : 100;

  railEl.innerHTML = `
    <header class="queue-head">
      <div class="queue-title">Mapping review</div>
      <div class="queue-sub">${reviewed} of ${totalReview} reviewed</div>
      <div class="queue-progress-mini"><div class="queue-progress-mini-fill" style="width:${progressPct}%"></div></div>
    </header>

    <label class="queue-toggle">
      <input type="checkbox" data-action="toggle-safe" ${state.showSafeInQueue ? 'checked' : ''} />
      Include auto-confirmed columns in queue
    </label>

    <div class="queue-body">
      ${queueSectionHtml('Needs your review', 'needs_review', counts.needs_review)}
      ${state.showSafeInQueue
        ? queueSectionHtml('Auto-confirmed', 'auto_safe', counts.auto_safe)
        : compactSectionHtml('Auto-confirmed', counts.auto_safe, 'ok')}
      ${compactSectionHtml('Empty columns', counts.empty, 'empty')}
    </div>

    <div class="queue-foot">
      ${reviewed === totalReview
        ? `<button class="netrias-btn" data-action="harmonize">Harmonize → ${counts.total - counts.empty} columns</button>`
        : `<button class="ghost-btn" data-action="bulk-confirm-safe">Confirm all ${counts.auto_safe} auto-safe</button>`}
    </div>
  `;
}

function queueSectionHtml(title, classification, total) {
  const items = COLUMNS.filter(c => c.classification === classification);
  return `
    <div class="queue-section-title">
      ${title}
      <span class="queue-section-count">${total}</span>
    </div>
    ${items.map(c => queueItemHtml(c)).join('')}
  `;
}

function compactSectionHtml(title, total, kind) {
  // Items still listed but collapsed/dimmed — preserves "I can see it's there"
  // without the cognitive load of an expanded list.
  return `
    <div class="queue-section-title">
      ${title}
      <span class="queue-section-count">${total} ${kind === 'ok' ? '✓' : '—'}</span>
    </div>
  `;
}

function queueItemHtml(col) {
  const decision = state.decisions.get(col.key) ?? {};
  const isCurrent = currentColumn()?.key === col.key;
  const isFlagged = state.flagged.has(col.key);

  let cls = 'queue-item';
  if (isCurrent) cls += ' queue-item--current';
  if (isFlagged) cls += ' queue-item--flagged';
  if (decision.status === STATUS.CONFIRMED || decision.status === STATUS.MANUAL || decision.status === STATUS.NO_MAPPING) cls += ' queue-item--done';
  else if (decision.status === STATUS.SKIPPED) cls += ' queue-item--skipped';
  else if (decision.status === STATUS.AUTO)    cls += ' queue-item--done';
  else if (decision.status === STATUS.EMPTY)   cls += ' queue-item--empty';
  else cls += ' queue-item--review';

  const overlapBadge = col.overlap != null
    ? `<span class="queue-item-meta">${pct(col.overlap)}</span>`
    : col.classification === 'empty'
      ? `<span class="queue-item-meta">—</span>`
      : `<span class="queue-item-meta">free</span>`;

  return `
    <div class="${cls}" data-action="queue-jump" data-key="${esc(col.key)}">
      <span class="queue-item-dot"></span>
      <span class="queue-item-name">${esc(col.key)}</span>
      ${overlapBadge}
    </div>
  `;
}
