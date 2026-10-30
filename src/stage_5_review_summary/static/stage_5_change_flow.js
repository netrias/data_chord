/**
 * Stage 5 change details: a collapsed summary line that opens into a by-column flow chart.
 * Bands count unique values; the three outcomes partition each column's unique values.
 */

const _SVG_NS = 'http://www.w3.org/2000/svg';

export const MIN_BAND = 3;
export const MAX_SCALE = 4;
// Total band height for the whole chart; tiny datasets stop growing at MAX_SCALE px per value.
const _BAND_BUDGET = 280;
const _NODE_WIDTH = 10;
// The left node column sits at a share of the width, kept between a minimum and maximum x.
const _LEFT_X_SHARE = 0.3;
const _LEFT_X_MIN = 210;
const _LEFT_X_MAX = 270;
// The outcome labels take a share of the width on the right, kept between a minimum and maximum.
const _RIGHT_LABEL_SHARE = 0.18;
const _RIGHT_LABEL_MIN = 140;
const _RIGHT_LABEL_MAX = 170;
const _ROW_MIN_LEFT = 22;
const _ROW_MIN_RIGHT = 50;
const _GAP_LEFT = 8;
const _GAP_RIGHT = 14;
const _HEAD = 22;
const _DRAW_DELAY_MS = 120;
const _DRAW_DURATION_MS = 850;
const _RESIZE_DELAY_MS = 80;
const _TOP_COLUMNS_IN_CARD = 4;

const _OUTCOMES = [
  { id: 'ai', field: 'ai_changes', title: 'Changed by AI', label: 'changed by AI', short: 'by AI', color: '--flow-ai' },
  { id: 'you', field: 'manual_changes', title: 'Changed by you', label: 'changed by you', short: 'by you', color: '--flow-you' },
  { id: 'same', field: 'unchanged', title: 'Unchanged', label: 'unchanged', short: 'unchanged', color: '--flow-same' },
];

const _safeCount = (value) => {
  const count = Number(value);
  return Number.isFinite(count) && count >= 0 ? Math.trunc(count) : 0;
};

const _format = (count) => count.toLocaleString();
const _percent = (part, whole) => `${whole ? Math.round((part / whole) * 100) : 0}%`;
const _clamp = (value, min, max) => Math.min(max, Math.max(min, value));

/** Totals for the closed summary line; rows changed come from row counts, the rest from unique values. */
export const summarizeChanges = (columns) => {
  const totals = { ai: 0, you: 0, same: 0, rowsChanged: 0 };
  for (const column of Array.isArray(columns) ? columns : []) {
    for (const outcome of _OUTCOMES) totals[outcome.id] += _safeCount(column?.[outcome.field]);
    totals.rowsChanged += _safeCount(column?.changed_rows);
  }
  return totals;
};

/** The three outcomes partition a column's unique values; columns with none are left out. */
const _chartColumns = (columns) => (Array.isArray(columns) ? columns : [])
  .map((column) => {
    const ai = _safeCount(column?.ai_changes);
    const you = _safeCount(column?.manual_changes);
    const same = _safeCount(column?.unchanged);
    return {
      name: String(column?.column ?? 'Unnamed column'),
      ai,
      you,
      same,
      total: ai + you + same,
      changed: ai + you,
    };
  })
  .filter((column) => column.total > 0)
  .sort((left, right) => right.changed - left.changed || right.total - left.total);

const _placeNodes = (nodes, x, gap, rowMin, top) => {
  let y = top;
  for (const node of nodes) {
    node.h = Math.max(node.bands.reduce((sum, band) => sum + band.t, 0), MIN_BAND);
    node.slot = Math.max(node.h, rowMin);
    node.x = x;
    node.y = y + (node.slot - node.h) / 2;
    y += node.slot + gap;
  }
  return y - gap;
};

/**
 * Pure geometry for a two-column flow: columns on the left, outcomes on the right.
 * Band thickness is unique values times one shared scale, never below MIN_BAND.
 * Bands are created in row order and outcome order, which is already the top-to-bottom
 * order of the node at the other end, so stacking in creation order keeps crossings low.
 */
export const layoutFlow = (columns, width) => {
  const chartColumns = _chartColumns(columns);
  const total = chartColumns.reduce((sum, column) => sum + column.total, 0);
  const scale = total ? Math.min(MAX_SCALE, _BAND_BUDGET / total) : 0;
  const leftX = _clamp(width * _LEFT_X_SHARE, _LEFT_X_MIN, _LEFT_X_MAX);
  const rightX = width - _clamp(width * _RIGHT_LABEL_SHARE, _RIGHT_LABEL_MIN, _RIGHT_LABEL_MAX) - _NODE_WIDTH;

  // Geometry fields are filled by _placeNodes; el is set when the chart is drawn.
  const left = chartColumns.map((column) => ({ column, bands: [], x: 0, y: 0, h: 0, slot: 0, el: null }));
  const right = _OUTCOMES.map((outcome) => ({
    outcome,
    value: chartColumns.reduce((sum, column) => sum + column[outcome.id], 0),
    bands: [],
    x: 0,
    y: 0,
    h: 0,
    slot: 0,
    el: null,
  }));
  const bands = [];
  for (const source of left) {
    for (const target of right) {
      const value = source.column[target.outcome.id];
      if (value === 0) continue;
      const band = { source, target, value, t: Math.max(MIN_BAND, value * scale), y0: 0, y1: 0, el: null };
      source.bands.push(band);
      target.bands.push(band);
      bands.push(band);
    }
  }

  const leftEnd = _placeNodes(left, leftX, _GAP_LEFT, _ROW_MIN_LEFT, _HEAD);
  const rightHeight = right.reduce(
    (sum, node) => sum + Math.max(node.bands.reduce((bandSum, band) => bandSum + band.t, 0), _ROW_MIN_RIGHT),
    0,
  ) + _GAP_RIGHT * (right.length - 1);
  const rightTop = _HEAD + Math.max(0, (leftEnd - _HEAD - rightHeight) / 2);
  const rightEnd = _placeNodes(right, rightX, _GAP_RIGHT, _ROW_MIN_RIGHT, rightTop);

  for (const node of left) {
    let y = node.y;
    for (const band of node.bands) {
      band.y0 = y;
      y += band.t;
    }
  }
  for (const node of right) {
    let y = node.y;
    for (const band of node.bands) {
      band.y1 = y;
      y += band.t;
    }
  }

  return {
    width,
    height: Math.ceil(Math.max(leftEnd, rightEnd) + 6),
    scale,
    leftX,
    rightX,
    total,
    left,
    right,
    bands,
  };
};

/* ---------- DOM helpers ---------- */

const _html = (tag, className, text) => {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text != null) element.textContent = text;
  return element;
};

const _svg = (tag, attributes, parent, text) => {
  const element = document.createElementNS(_SVG_NS, tag);
  for (const [name, value] of Object.entries(attributes)) element.setAttribute(name, String(value));
  if (text != null) element.textContent = text;
  parent?.appendChild(element);
  return element;
};

const _swatch = (color) => {
  const swatch = document.createElement('i');
  swatch.className = 'change-flow__swatch';
  swatch.style.background = `var(${color})`;
  return swatch;
};

/** Shortens an SVG label to the longest prefix that fits; the full name stays in the card and aria-label. */
const _fitText = (text, maxWidth) => {
  const full = text.textContent ?? '';
  if (text.getComputedTextLength() <= maxWidth) return;
  let low = 0;
  let high = full.length - 1;
  while (low < high) {
    const middle = Math.ceil((low + high) / 2);
    text.textContent = `${full.slice(0, middle)}…`;
    if (text.getComputedTextLength() <= maxWidth) low = middle;
    else high = middle - 1;
  }
  text.textContent = `${full.slice(0, Math.max(low, 1))}…`;
};

/* ---------- Hover card content ---------- */

const _cardRow = (list, color, label, count) => {
  const term = _html('dt');
  term.append(_swatch(color), label);
  list.append(term, _html('dd', '', _format(count)));
};

const _columnCard = (column) => {
  const list = _html('dl');
  for (const outcome of _OUTCOMES) _cardRow(list, outcome.color, outcome.title, column[outcome.id]);
  const bar = _html('div', 'change-flow-card__bar');
  for (const outcome of _OUTCOMES.slice(0, 2)) {
    const part = _html('span');
    part.style.width = `${(column[outcome.id] / column.total) * 100}%`;
    part.style.background = `var(${outcome.color})`;
    bar.appendChild(part);
  }
  return [
    _html('h3', '', column.name),
    list,
    bar,
    _html('p', '', `${_percent(column.changed, column.total)} of ${_format(column.total)} unique values changed`),
  ];
};

const _outcomeCard = (node, layout) => {
  const { outcome, value } = node;
  const list = _html('dl');
  const top = node.bands
    .map((band) => band.source.column)
    .sort((a, b) => b[outcome.id] - a[outcome.id])
    .slice(0, _TOP_COLUMNS_IN_CARD);
  for (const column of top) _cardRow(list, outcome.color, column.name, column[outcome.id]);
  return [
    _html('h3', '', `${_format(value)} ${outcome.label}`),
    list,
    _html('p', '', `${_percent(value, layout.total)} of all ${_format(layout.total)} unique values`),
  ];
};

const _bandCard = (band) => {
  const { column } = band.source;
  const { outcome } = band.target;
  const list = _html('dl');
  _cardRow(list, outcome.color, outcome.title, band.value);
  return [
    _html('h3', '', column.name),
    list,
    _html('p', '', `${_percent(band.value, column.total)} of this column's ${_format(column.total)} unique values`),
  ];
};

/* ---------- Mounted chart ---------- */

const _buildDisclosure = (columns) => {
  const totals = summarizeChanges(columns);
  const toggle = _html('button', 'change-flow__toggle');
  toggle.type = 'button';
  toggle.setAttribute('aria-expanded', 'false');
  toggle.setAttribute('aria-controls', 'changeFlowBody');

  const chevron = _svg('svg', { class: 'change-flow__chevron', viewBox: '0 0 20 20', 'aria-hidden': 'true' });
  _svg('path', {
    d: 'M7.5 5l5 5-5 5',
    fill: 'none',
    stroke: 'currentColor',
    'stroke-width': 2,
    'stroke-linecap': 'round',
    'stroke-linejoin': 'round',
  }, chevron);

  const line = _html('span', 'change-flow__summary');
  for (const outcome of _OUTCOMES) {
    const item = _html('span');
    item.dataset.changeTotal = outcome.id;
    item.append(_swatch(outcome.color), _html('b', '', _format(totals[outcome.id])), ` ${outcome.short}`);
    line.appendChild(item);
  }
  const rows = _html('span', 'change-flow__rows', `· ${_format(totals.rowsChanged)} rows changed`);
  rows.dataset.changeTotal = 'rows';
  line.appendChild(rows);

  toggle.append(chevron, _html('span', 'change-flow__title', 'Change details'), line);
  return toggle;
};

/** Plain text keys; pointer hover highlights an outcome, but they take no focus because they have no action. */
const _buildLegend = () => {
  const legend = _html('div', 'change-flow__legend');
  for (const outcome of _OUTCOMES) {
    const key = _html('span');
    key.dataset.outcome = outcome.id;
    key.append(_swatch(outcome.color), outcome.title);
    legend.appendChild(key);
  }
  return legend;
};

const _bandPath = (band, x0, x1) => {
  const c1 = x0 + (x1 - x0) * 0.45;
  const c2 = x1 - (x1 - x0) * 0.45;
  return `M${x0},${band.y0} C${c1},${band.y0} ${c2},${band.y1} ${x1},${band.y1}`
    + ` L${x1},${band.y1 + band.t} C${c2},${band.y1 + band.t} ${c1},${band.y0 + band.t} ${x0},${band.y0 + band.t} Z`;
};

/** Renders the disclosure into rootEl. Closed on every load; the chart draws in each time it opens. */
export const mountChangeFlow = (rootEl, columns) => {
  if (!rootEl) return;
  const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)');

  const toggle = _buildDisclosure(columns);
  const body = _html('div', 'change-flow__body');
  body.id = 'changeFlowBody';
  const inner = _html('div', 'change-flow__inner');
  inner.inert = true;
  const content = _html('div', 'change-flow__content');
  const legend = _buildLegend();
  const wrap = _html('div', 'change-flow__chart');
  const svg = _svg('svg', { class: 'change-flow__svg', role: 'group', 'aria-label': 'Unique values by column and outcome' }, wrap);
  const empty = _html('p', 'change-flow__empty hidden', 'No column values to show.');
  const foot = _html('p', 'change-flow__foot', 'Band height is the number of unique values. Thin bands are drawn at least 3 px tall.');
  content.append(legend, wrap, empty, foot);
  inner.appendChild(content);
  body.appendChild(inner);

  // Fixed to the viewport, outside the chart box, so it never adds a scrollbar to any box.
  const card = _html('div', 'change-flow-card');
  card.setAttribute('aria-hidden', 'true');
  rootEl.replaceChildren(toggle, body, card);

  let layout = null;
  // A keyboard-focused node the card is pinned to; null while the card follows the pointer.
  let cardAnchor = null;
  // The draw-in is timed from the open, so a redraw mid-reveal resumes it instead of snapping.
  let revealStart = -Infinity;
  let revealFrame = 0;
  let lastWidth = -1;
  const isOpen = () => toggle.getAttribute('aria-expanded') === 'true';

  const placeCard = (x, y) => {
    const maxX = window.innerWidth - card.offsetWidth - 8;
    const maxY = window.innerHeight - card.offsetHeight - 8;
    card.style.left = `${Math.max(8, Math.min(x, maxX))}px`;
    card.style.top = `${Math.max(8, Math.min(y, maxY))}px`;
  };
  const followPointer = (event) => placeCard(event.clientX + 16, event.clientY + 14);
  const placeAtAnchor = (anchor) => {
    const box = anchor.getBoundingClientRect();
    placeCard(box.right + 8, box.top);
  };

  const clearFocus = () => {
    cardAnchor = null;
    svg.classList.remove('is-focused');
    for (const element of svg.querySelectorAll('.is-on')) element.classList.remove('is-on');
    card.classList.remove('is-shown');
  };

  const focusOn = (cardContent, bands, nodes, event, anchor) => {
    svg.classList.add('is-focused');
    for (const element of svg.querySelectorAll('.is-on')) element.classList.remove('is-on');
    for (const band of bands) band.el.classList.add('is-on');
    for (const node of nodes) node.el.classList.add('is-on');
    card.replaceChildren(...cardContent);
    card.classList.add('is-shown');
    cardAnchor = event ? null : anchor;
    if (event) followPointer(event);
    else placeAtAnchor(anchor);
  };

  // Scrolling moves the chart under a resting pointer, so a pointer card hides; a focus card follows its node.
  const handleScroll = () => {
    if (cardAnchor && cardAnchor === document.activeElement) placeAtAnchor(cardAnchor);
    else clearFocus();
  };

  const wirePointer = (element, cardContent, bands, nodes) => {
    element.addEventListener('pointerenter', (event) => focusOn(cardContent(), bands, nodes, event));
    element.addEventListener('pointermove', followPointer);
    element.addEventListener('pointerleave', clearFocus);
  };
  // Separate from pointer wiring: Chromium makes any SVG element with focus listeners a Tab stop.
  const wireNode = (element, cardContent, bands, nodes) => {
    wirePointer(element, cardContent, bands, nodes);
    element.addEventListener('focus', () => focusOn(cardContent(), bands, nodes, null, element));
    element.addEventListener('blur', clearFocus);
  };

  const draw = () => {
    clearFocus();
    cancelAnimationFrame(revealFrame);
    lastWidth = wrap.clientWidth;
    const minWidth = parseFloat(getComputedStyle(svg).minWidth) || 0;
    const width = Math.max(minWidth, Math.round(wrap.clientWidth));
    layout = layoutFlow(columns, width);
    svg.replaceChildren();
    const hasColumns = layout.left.length > 0;
    wrap.classList.toggle('hidden', !hasColumns);
    legend.classList.toggle('hidden', !hasColumns);
    empty.classList.toggle('hidden', hasColumns);
    if (!hasColumns) return;

    svg.setAttribute('viewBox', `0 0 ${width} ${layout.height}`);
    svg.setAttribute('height', String(layout.height));
    const defs = _svg('defs', {}, svg);
    const revealRect = _svg('rect', { x: 0, y: 0, width, height: layout.height }, _svg('clipPath', { id: 'changeFlowReveal' }, defs));

    _svg('text', { x: 0, y: 11, class: 'change-flow__head' }, svg, 'COLUMN');
    _svg('text', { x: layout.leftX - 14, y: 11, 'text-anchor': 'end', class: 'change-flow__head' }, svg, 'CHANGED / UNIQUE');
    _svg('text', { x: layout.rightX + _NODE_WIDTH + 12, y: 11, class: 'change-flow__head' }, svg, 'OUTCOME');

    // Every band runs between the same two x positions, so one gradient per outcome serves them all.
    const x0 = layout.leftX + _NODE_WIDTH;
    const x1 = layout.rightX;
    for (const outcome of _OUTCOMES) {
      const muted = outcome.id === 'same';
      const gradient = _svg('linearGradient', {
        id: `changeFlowBand-${outcome.id}`, gradientUnits: 'userSpaceOnUse', x1: x0, x2: x1, y1: 0, y2: 0,
      }, defs);
      // Bands fade from a neutral source tone into the outcome color.
      const from = _svg('stop', { offset: '0%', 'stop-opacity': muted ? 0.9 : 0.35 }, gradient);
      from.style.stopColor = muted ? 'var(--gray-200)' : 'var(--flow-source)';
      const to = _svg('stop', { offset: '100%', 'stop-opacity': muted ? 0.75 : 0.88 }, gradient);
      to.style.stopColor = `var(${outcome.color})`;
    }

    const bandGroup = _svg('g', { 'clip-path': 'url(#changeFlowReveal)' }, svg);
    const nodeGroup = _svg('g', {}, svg);

    for (const band of layout.bands) {
      const outcomeId = band.target.outcome.id;
      band.el = _svg('path', {
        d: _bandPath(band, x0, x1), class: 'change-flow__band', fill: `url(#changeFlowBand-${outcomeId})`,
      }, bandGroup);
      band.el.dataset.outcome = outcomeId;
      wirePointer(band.el, () => _bandCard(band), [band], [band.source, band.target]);
    }

    // Left rows: name, then "changed / unique" right-aligned against a small stacked node.
    for (const [index, node] of layout.left.entries()) {
      const { column } = node;
      const group = _svg('g', {
        class: 'change-flow__node',
        tabindex: 0,
        role: 'img',
        'aria-label': `${column.name}: ${column.changed} of ${column.total} unique values changed, ${column.ai} by AI, ${column.you} by you`,
      }, nodeGroup);
      group.dataset.flowColumn = String(index);
      const centerY = node.y + node.h / 2;
      _svg('rect', {
        x: -2, y: centerY - node.slot / 2 - 3, width: node.x + _NODE_WIDTH + 6, height: node.slot + 6, rx: 6, class: 'change-flow__ring',
      }, group);
      _svg('rect', { x: node.x, y: node.y, width: _NODE_WIDTH, height: node.h, rx: 3 }, _svg('clipPath', { id: `changeFlowNode${index}` }, defs));
      const stack = _svg('g', { class: 'change-flow__stack', 'clip-path': `url(#changeFlowNode${index})` }, group);
      for (const band of node.bands) {
        const segment = _svg('rect', { x: node.x, y: band.y0, width: _NODE_WIDTH, height: band.t }, stack);
        segment.style.fill = `var(${band.target.outcome.color})`;
      }
      const label = _svg('g', { class: 'change-flow__label' }, group);
      const name = _svg('text', { x: 4, y: centerY, 'dominant-baseline': 'central', class: 'change-flow__name' }, label, column.name);
      const counts = _svg('text', { x: node.x - 14, y: centerY, 'dominant-baseline': 'central', 'text-anchor': 'end' }, label);
      _svg('tspan', { class: 'change-flow__count' }, counts, _format(column.changed));
      _svg('tspan', { class: 'change-flow__of' }, counts, ` / ${_format(column.total)}`);
      _fitText(name, node.x - 14 - counts.getComputedTextLength() - 12);
      node.el = group;
      wireNode(group, () => _columnCard(column), node.bands, [node, ...node.bands.map((band) => band.target)]);
    }

    // Right nodes: big number with a caption under it.
    for (const node of layout.right) {
      const { outcome, value } = node;
      const group = _svg('g', {
        class: 'change-flow__node',
        tabindex: 0,
        role: 'img',
        'aria-label': `${_format(value)} unique values ${outcome.label}`,
      }, nodeGroup);
      group.dataset.flowOutcome = outcome.id;
      const centerY = node.y + node.h / 2;
      const textX = node.x + _NODE_WIDTH + 12;
      _svg('rect', {
        x: node.x - 4, y: centerY - node.slot / 2 - 3, width: Math.min(_RIGHT_LABEL_MAX, width - node.x + 4), height: node.slot + 6, rx: 6, class: 'change-flow__ring',
      }, group);
      const bar = _svg('rect', { x: node.x, y: node.y, width: _NODE_WIDTH, height: node.h, rx: 3, class: 'change-flow__stack' }, group);
      bar.style.fill = `var(${outcome.color})`;
      const label = _svg('g', { class: 'change-flow__label' }, group);
      _svg('text', {
        x: textX, y: centerY - 9, 'dominant-baseline': 'central', class: `change-flow__big${outcome.id === 'same' ? ' is-muted' : ''}`,
      }, label, _format(value));
      _svg('text', { x: textX, y: centerY + 12, 'dominant-baseline': 'central', class: 'change-flow__caption' }, label, `${outcome.label} · ${_percent(value, layout.total)}`);
      node.el = group;
      wireNode(group, () => _outcomeCard(node, layout), node.bands, [node, ...node.bands.map((band) => band.source)]);
    }

    // Grows the band clip from the left nodes to the full width; skipped once the reveal has finished.
    const revealFrom = layout.leftX;
    const stepReveal = () => {
      const progress = _clamp((performance.now() - revealStart) / _DRAW_DURATION_MS, 0, 1);
      revealRect.setAttribute('width', String(revealFrom + (width - revealFrom) * (1 - (1 - progress) ** 3)));
      if (progress < 1) revealFrame = requestAnimationFrame(stepReveal);
    };
    stepReveal();
  };

  for (const key of legend.querySelectorAll('[data-outcome]')) {
    key.addEventListener('pointerenter', () => {
      const node = layout?.right.find((outcome) => outcome.outcome.id === key.dataset.outcome);
      if (!node?.el) return;
      focusOn(_outcomeCard(node, layout), node.bands, [node, ...node.bands.map((band) => band.source)], null, node.el);
    });
    key.addEventListener('pointerleave', clearFocus);
  }

  toggle.addEventListener('click', () => {
    const open = !isOpen();
    rootEl.classList.toggle('is-open', open);
    toggle.setAttribute('aria-expanded', String(open));
    inner.inert = !open;
    if (!open) {
      clearFocus();
      return;
    }
    revealStart = reducedMotion.matches ? -Infinity : performance.now() + _DRAW_DELAY_MS;
    draw();
  });
  window.addEventListener('scroll', handleScroll, { passive: true });
  wrap.addEventListener('scroll', handleScroll, { passive: true });

  // Redraw only when an open chart's width changes, so height changes never cause a redraw loop.
  let timer = null;
  new ResizeObserver(() => {
    if (!isOpen() || wrap.clientWidth === lastWidth) return;
    clearTimeout(timer);
    timer = setTimeout(draw, _RESIZE_DELAY_MS);
  }).observe(wrap);
};
