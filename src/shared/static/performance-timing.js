/**
 * Lightweight browser-side timing capture for performance journeys.
 *
 * Timings are intentionally stored on window so Playwright and future agent
 * tools can read the same user-perceived marks without scraping console logs.
 */

const STORE_KEY = '__dataChordPerf';
const MARK_PREFIX = 'data-chord:';

const _createStore = () => ({
  marks: [],
  measures: [],
});

const _store = () => {
  if (!window[STORE_KEY]) {
    window[STORE_KEY] = _createStore();
  }
  return window[STORE_KEY];
};

const _findLatestMark = (name) => {
  const marks = _store().marks;
  for (let index = marks.length - 1; index >= 0; index -= 1) {
    if (marks[index].name === name) {
      return marks[index];
    }
  }
  return null;
};

const _markForBrowserTimeline = (name) => {
  try {
    performance.mark(`${MARK_PREFIX}${name}`);
  } catch {
    // Browser performance marks are best-effort; the in-memory store is enough
    // for the perf journey.
  }
};

const _measureForBrowserTimeline = (name, startName, endName) => {
  try {
    performance.measure(
      `${MARK_PREFIX}${name}`,
      `${MARK_PREFIX}${startName}`,
      `${MARK_PREFIX}${endName}`,
    );
  } catch {
    // Some browsers throw if a mark was missed. The explicit store below is the
    // canonical report used by tests and scripts.
  }
};

export const markTiming = (name, detail = {}) => {
  const mark = {
    name,
    at: performance.now(),
    epoch_ms: Date.now(),
    detail,
  };
  _store().marks.push(mark);
  _markForBrowserTimeline(name);
  return mark;
};

export const measureTiming = (name, startName, endName, detail = {}) => {
  const start = _findLatestMark(startName);
  const end = _findLatestMark(endName);
  if (!start || !end) {
    return null;
  }

  const measure = {
    name,
    start: startName,
    end: endName,
    duration_ms: end.at - start.at,
    detail,
  };
  _store().measures.push(measure);
  _measureForBrowserTimeline(name, startName, endName);
  return measure;
};

export const markAfterPaint = (name, detail = {}) => {
  return new Promise((resolve) => {
    // Two animation frames puts the mark after layout and paint, which is closer
    // to what a user sees than timing only the JavaScript render call.
    requestAnimationFrame(() => {
      requestAnimationFrame(() => {
        resolve(markTiming(name, detail));
      });
    });
  });
};

export const exposeTimingHelpers = () => {
  const store = _store();
  store.getReport = () => ({
    marks: [...store.marks],
    measures: [...store.measures],
  });
  store.reset = () => {
    store.marks.length = 0;
    store.measures.length = 0;
  };
  return store;
};

exposeTimingHelpers();
