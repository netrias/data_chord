const config = window.stageFiveConfig ?? {};
const stageThreePayloadKey = config.stageThreePayloadKey ?? 'stage3HarmonizePayload';
const summaryEndpoint = config.summaryEndpoint ?? '/stage-5/summary';

const progressSteps = document.querySelectorAll('.progress-tracker [data-stage]');
const summaryMetricGrid = document.getElementById('summaryMetricGrid');
const changeTableBody = document.getElementById('changeTableBody');
const changeTableHeader = document.querySelector('.change-table-header');
const insightTags = document.getElementById('insightTags');
const downloadResults = document.getElementById('downloadResults');
const startNewButton = document.getElementById('startNewButton');
const changeModal = document.getElementById('changeModal');
const changeModalTitle = document.getElementById('changeModalTitle');
const changeModalBody = document.getElementById('changeModalBody');
const closeChangeModal = document.getElementById('closeChangeModal');
const summaryError = document.getElementById('summaryError');
const filterChangesToggle = document.getElementById('filterChangesToggle');

const SORT_KEYS = ['column', 'ai_changes', 'manual_changes', 'reviewed'];

const state = {
  summary: null,
  fileId: null,
  manualColumns: [],
  sortKey: null,
  sortAscending: true,
  filterChangesOnly: false,
};

const setActiveStage = (stage) => {
  const order = ['upload', 'mapping', 'harmonize', 'review', 'export'];
  const targetIndex = order.indexOf(stage);
  progressSteps.forEach((step) => {
    const stepStage = step.dataset.stage;
    const stepIndex = order.indexOf(stepStage);
    const isActive = stepStage === stage;
    const isComplete = stepIndex >= 0 && stepIndex < targetIndex;
    step.classList.toggle('active', isActive);
    step.classList.toggle('complete', isComplete);
  });
};

const safeJsonParse = (raw) => {
  try {
    return JSON.parse(raw);
  } catch (error) {
    console.warn('Unable to parse session payload', error);
    return null;
  }
};

const readFromSession = (key) => {
  try {
    const raw = sessionStorage.getItem(key);
    return raw ? safeJsonParse(raw) : null;
  } catch (error) {
    console.warn('Unable to read session storage', error);
    return null;
  }
};

const loadSourceContext = () => {
  const stored = readFromSession(stageThreePayloadKey);
  const fileId = stored?.request?.file_id;
  if (!fileId) {
    return null;
  }
  const manualColumns = Object.keys(stored?.request?.manual_overrides ?? {});
  return { fileId, manualColumns };
};

const showSummaryError = (message) => {
  if (!summaryError) {
    window.alert(message);
    return;
  }
  summaryError.textContent = message;
  summaryError.classList.remove('hidden');
};

const renderSummaryMetrics = () => {
  if (!state.summary || !summaryMetricGrid) {
    return;
  }
  const cards = [
    {
      label: 'Total rows',
      value: state.summary.total_rows.toLocaleString(),
      hint: `${state.summary.columns_reviewed} columns reviewed`,
    },
    {
      label: 'AI adjustments',
      value: state.summary.ai_changes,
      delta:
        state.summary.total_rows > 0
          ? `${Math.round((state.summary.ai_changes / state.summary.total_rows) * 100)}% of rows`
          : '—',
      tone: 'up',
      action: { label: 'View adjusted values', type: 'ai' },
    },
    {
      label: 'Manual overrides',
      value: state.summary.manual_changes,
      delta:
        state.summary.total_rows > 0
          ? `${Math.round((state.summary.manual_changes / state.summary.total_rows) * 100)}% curated`
          : '—',
      tone: 'down',
      action: { label: 'View manual overrides', type: 'manual' },
    },
  ];

  summaryMetricGrid.innerHTML = cards
    .map((card) => {
      const delta = card.delta ? `<span class="metric-delta ${card.tone ?? ''}">${card.delta}</span>` : '';
      const hint = card.hint ? `<p class="metric-hint">${card.hint}</p>` : '';
      const action = card.action
        ? `<button class="metric-link" data-change-link="${card.action.type}">${card.action.label}</button>`
        : '';
      return `
        <article class="metric-card">
          <h4>${card.label}</h4>
          <strong>${card.value}</strong>
          ${delta}
          ${hint}
          ${action}
        </article>
      `;
    })
    .join('');
};

const getReviewedPercent = (entry) => {
  const touched = entry.ai_changes + entry.manual_changes;
  return state.summary?.total_rows ? (touched / state.summary.total_rows) * 100 : 0;
};

const sortColumnSummaries = (summaries) => {
  if (!state.sortKey) {
    return summaries;
  }
  const sorted = [...summaries];
  sorted.sort((a, b) => {
    let aVal, bVal;
    if (state.sortKey === 'column') {
      aVal = a.column.toLowerCase();
      bVal = b.column.toLowerCase();
    } else if (state.sortKey === 'reviewed') {
      aVal = getReviewedPercent(a);
      bVal = getReviewedPercent(b);
    } else {
      aVal = a[state.sortKey];
      bVal = b[state.sortKey];
    }
    if (aVal < bVal) return state.sortAscending ? -1 : 1;
    if (aVal > bVal) return state.sortAscending ? 1 : -1;
    return 0;
  });
  return sorted;
};

const filterColumnSummaries = (summaries) => {
  if (!state.filterChangesOnly) {
    return summaries;
  }
  return summaries.filter((entry) => entry.ai_changes > 0 || entry.manual_changes > 0);
};

const renderChangeTable = () => {
  if (!state.summary || !changeTableBody) {
    return;
  }
  const filtered = filterColumnSummaries(state.summary.column_summaries);
  const sorted = sortColumnSummaries(filtered);
  const rows = sorted
    .map((entry) => {
      const reviewedPercent = getReviewedPercent(entry);
      const reviewed = state.summary.total_rows ? `${Math.round(reviewedPercent)}%` : '—';
      return `
        <div class="change-table-row" role="row">
          <span>${entry.column}</span>
          <span>${entry.ai_changes}</span>
          <span>${entry.manual_changes}</span>
          <span>${reviewed}</span>
        </div>
      `;
    })
    .join('');
  changeTableBody.innerHTML = rows || '<div class="change-table-row" role="row">No changes detected.</div>';
  renderTableHeader();
};

const renderTableHeader = () => {
  if (!changeTableHeader) {
    return;
  }
  const headers = [
    { key: 'column', label: 'Column' },
    { key: 'ai_changes', label: 'AI updates' },
    { key: 'manual_changes', label: 'Manual overrides' },
    { key: 'reviewed', label: 'Reviewed rows' },
  ];
  changeTableHeader.innerHTML = headers
    .map((header) => {
      const isActive = state.sortKey === header.key;
      const arrow = isActive ? (state.sortAscending ? ' ▲' : ' ▼') : '';
      return `<span class="sortable-header${isActive ? ' active' : ''}" data-sort-key="${header.key}">${header.label}${arrow}</span>`;
    })
    .join('');
};

const renderInsightTags = () => {
  if (!state.summary || !insightTags) {
    return;
  }
  const tags = [
    `${state.summary.ai_changes} AI adjustments`,
    `${state.summary.manual_changes} manual overrides`,
    `${state.summary.columns_reviewed} columns reviewed`,
  ];
  insightTags.innerHTML = tags.map((tag) => `<span class="insight-tag">${tag}</span>`).join('');
};

const openChangeModal = (type) => {
  if (!state.summary || !changeModal || !changeModalBody || !changeModalTitle) {
    return;
  }
  const entries = type === 'ai' ? state.summary.ai_examples : state.summary.manual_examples;
  changeModalTitle.textContent = type === 'ai' ? 'AI adjusted values' : 'Manual overrides';
  if (!entries.length) {
    changeModalBody.innerHTML = '<p>No changes recorded.</p>';
  } else {
    changeModalBody.innerHTML = entries
      .map(
        (entry) => `
          <article class="modal-item">
            <h4>${entry.column} · Row ${entry.row_index}</h4>
            <dl>
              <dt>Original</dt>
              <dd>${entry.original ?? '—'}</dd>
              <dt>Updated</dt>
              <dd>${entry.harmonized ?? '—'}</dd>
            </dl>
          </article>
        `,
      )
      .join('');
  }
  changeModal.classList.remove('hidden');
};

const attachStageEvents = () => {
  document.querySelectorAll('.progress-tracker .step[data-url]').forEach((step) => {
    step.addEventListener('click', () => {
      const target = step.dataset.url;
      if (target) {
        window.location.assign(target);
      }
    });
  });
  document.querySelectorAll('[data-nav-target]').forEach((button) => {
    button.addEventListener('click', () => {
      const target = button.dataset.navTarget;
      if (target) {
        window.location.assign(target);
      }
    });
  });
};

const handleSortClick = (sortKey) => {
  if (state.sortKey === sortKey) {
    state.sortAscending = !state.sortAscending;
  } else {
    state.sortKey = sortKey;
    state.sortAscending = true;
  }
  renderChangeTable();
};

const handleFilterToggle = () => {
  state.filterChangesOnly = !state.filterChangesOnly;
  if (filterChangesToggle) {
    filterChangesToggle.textContent = state.filterChangesOnly ? 'Show all columns' : 'Show changed only';
    filterChangesToggle.classList.toggle('active', state.filterChangesOnly);
  }
  renderChangeTable();
};

const attachStageFiveEvents = () => {
  if (downloadResults) {
    downloadResults.addEventListener('click', () => {
      window.alert('Download will be available in a future release.');
    });
  }
  if (startNewButton) {
    startNewButton.addEventListener('click', () => {
      if (config.stageOneUrl) {
        window.location.assign(config.stageOneUrl);
      }
    });
  }
  summaryMetricGrid?.addEventListener('click', (event) => {
    const target = event.target;
    if (target.matches('[data-change-link]')) {
      openChangeModal(target.getAttribute('data-change-link'));
    }
  });
  closeChangeModal?.addEventListener('click', () => changeModal?.classList.add('hidden'));
  changeModal?.addEventListener('click', (event) => {
    if (event.target === changeModal) {
      changeModal.classList.add('hidden');
    }
  });
  changeTableHeader?.addEventListener('click', (event) => {
    const target = event.target;
    if (target.matches('[data-sort-key]')) {
      handleSortClick(target.getAttribute('data-sort-key'));
    }
  });
  filterChangesToggle?.addEventListener('click', handleFilterToggle);
};

const fetchSummary = async () => {
  const context = loadSourceContext();
  if (!context) {
    showSummaryError('Unable to locate harmonization context. Please rerun Stage 4.');
    return;
  }
  state.fileId = context.fileId;
  state.manualColumns = context.manualColumns;
  try {
    const response = await fetch(summaryEndpoint, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ file_id: context.fileId, manual_columns: context.manualColumns }),
    });
    if (!response.ok) {
      const detail = await response.text();
      throw new Error(detail || 'Unable to load harmonized results.');
    }
    state.summary = await response.json();
    summaryError?.classList.add('hidden');
    renderSummaryMetrics();
    renderChangeTable();
    renderInsightTags();
  } catch (error) {
    showSummaryError(error.message || 'Unable to load harmonized results.');
  }
};

const init = () => {
  setActiveStage('export');
  attachStageEvents();
  attachStageFiveEvents();
  fetchSummary();
};

init();
