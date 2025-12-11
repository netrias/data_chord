// """Coordinate the stage three metric widgets."""

import renderTotalItemsWidget from './total_items_viz.js';
import renderChangeSplitWidget from './change_split_viz.js';
import renderChangeTypesWidget from './change_types_viz.js';
import renderConfidenceBucketsWidget from './confidence_buckets_viz.js';

const renderers = [
  {
    id: 'totalItems',
    render: (grid, dataset) =>
      renderTotalItemsWidget({
        container: grid,
        totalItems: dataset.totalItems,
        pillLabel: dataset.totalItemsPill ?? 'Harmonized rows',
        noteText: dataset.totalItemsNote,
      }),
  },
  {
    id: 'changeSplit',
    render: (grid, dataset) =>
      renderChangeSplitWidget({
        container: grid,
        changed: dataset.changedItems,
        unchanged: dataset.unchangedItems,
        noteText: dataset.changeSplitNote,
      }),
  },
  {
    id: 'changeTypes',
    render: (grid, dataset) =>
      renderChangeTypesWidget({
        container: grid,
        changeTypes: dataset.changeTypes,
        noteText: dataset.changeTypesNote,
      }),
  },
  {
    id: 'confidence',
    render: (grid, dataset) =>
      renderConfidenceBucketsWidget({
        container: grid,
        buckets: dataset.confidenceBuckets,
        isMocked: dataset.isConfidenceMocked,
        noteText: dataset.confidenceNote,
      }),
  },
];

export class StageThreeMetricsDashboard {
  constructor(root) {
    // "why: retain DOM handles so render cycles stay predictable."
    this.root = root;
    this.grid = root?.querySelector('[data-metrics-grid]') ?? null;
  }

  render(dataset) {
    // "why: orchestrate metric modules only when the dataset is ready."
    if (!this.root || !this.grid) {
      return;
    }
    if (!dataset) {
      this.hide();
      return;
    }
    this.grid.innerHTML = '';
    renderers.forEach((entry) => {
      entry.render(this.grid, dataset);
    });
    this.root.classList.remove('hidden');
  }

  hide() {
    // "why: clear stale visualizations while another run processes."
    if (this.grid) {
      this.grid.innerHTML = '';
    }
    if (this.root) {
      this.root.classList.add('hidden');
    }
  }

  static initFromDom() {
    // "why: simplify bootstrapping from the main stage three script."
    const container = document.getElementById('stageThreeMetrics');
    if (!container) {
      return null;
    }
    return new StageThreeMetricsDashboard(container);
  }
}

export default StageThreeMetricsDashboard;
