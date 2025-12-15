// """Coordinate the stage three metric widgets."""

import renderColumnBreakdownWidget from './column_breakdown_viz.js';

export class StageThreeMetricsDashboard {
  constructor(grid) {
    // "why: retain DOM handle for the metrics grid so render cycles stay predictable."
    this.grid = grid;
  }

  render(dataset) {
    // "why: orchestrate metric modules only when the dataset is ready."
    if (!this.grid) {
      return;
    }
    if (!dataset) {
      this.hide();
      return;
    }
    this.grid.innerHTML = '';

    if (dataset.columnBreakdown?.length) {
      renderColumnBreakdownWidget({
        container: this.grid,
        columns: dataset.columnBreakdown,
      });
    }

    this.grid.classList.remove('hidden');
  }

  hide() {
    // "why: clear stale visualizations while another run processes."
    if (this.grid) {
      this.grid.innerHTML = '';
      this.grid.classList.add('hidden');
    }
  }

  static initFromDom() {
    // "why: simplify bootstrapping from the main stage three script."
    const grid = document.querySelector('[data-metrics-grid]');
    if (!grid) {
      return null;
    }
    return new StageThreeMetricsDashboard(grid);
  }
}

export default StageThreeMetricsDashboard;
