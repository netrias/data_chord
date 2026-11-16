/** Start-up contract of the Stage 3 loading animation: when the spinner is replaced, and when it is not. */

import { describe, it, afterEach } from 'node:test';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';

import { startHarmonizeAnimation } from '../../src/stage_3_harmonize/static/harmonize-animation.js';

/* why: jsdom has no canvas drawing, so a mock 2D context records the calls made on it. */
const mockContext = (calls) => new Proxy({}, {
  get: (target, name) => target[name] ?? ((...args) => {
    calls.push(name);
    return name === 'createLinearGradient' ? { addColorStop() {} } : undefined;
  }),
});

describe('Stage 3 harmonize animation', () => {
  let dom;

  /** Build the Stage 3 loading state in a window that has the browser features jsdom lacks. */
  const loadingPage = ({ context, reducedMotion = false, canvasWidth = 680 }) => {
    dom = new JSDOM(`<!DOCTYPE html><html><body>
      <div id="loadingState" class="loading-state">
        <div class="loading-spinner loading-spinner--fallback"></div>
        <canvas class="harmonize-animation"></canvas>
      </div>
    </body></html>`);
    const win = dom.window;
    const scheduledFrames = [];
    const canvas = win.document.querySelector('canvas');
    canvas.getContext = () => context;
    let width = canvasWidth;
    Object.defineProperty(canvas, 'clientWidth', { get: () => width });
    win.matchMedia = () => ({ matches: reducedMotion });
    win.requestAnimationFrame = (callback) => scheduledFrames.push(callback);
    return {
      win,
      scheduledFrames,
      loadingState: win.document.getElementById('loadingState'),
      hideCanvas: () => { width = 0; },
    };
  };

  afterEach(() => {
    dom.window.close();
  });

  it('marks the loading state as running and draws on the canvas when it starts', () => {
    // Given the Stage 3 loading state with a working canvas, not yet marked and not drawn on
    const calls = [];
    const page = loadingPage({ context: mockContext(calls) });
    assert.equal(page.loadingState.dataset.animation, undefined);
    assert.deepEqual(calls, []);

    // When the animation starts
    startHarmonizeAnimation(page.win);

    // Then the state the CSS keys on is set, a frame is drawn, and the next frame is scheduled
    assert.equal(page.loadingState.dataset.animation, 'running');
    assert.ok(calls.includes('stroke') && calls.includes('fill'), `expected drawing calls, got ${[...new Set(calls)]}`);
    assert.equal(page.scheduledFrames.length, 1);
  });

  it('draws one still frame and schedules no further frame when reduced motion is preferred', () => {
    // Given the loading state in a browser that prefers reduced motion
    const calls = [];
    const page = loadingPage({ context: mockContext(calls), reducedMotion: true });
    assert.deepEqual(calls, []);

    // When the animation starts
    startHarmonizeAnimation(page.win);

    // Then exactly one frame is drawn and nothing is scheduled
    assert.equal(calls.filter((name) => name === 'clearRect').length, 1);
    assert.ok(calls.includes('stroke') && calls.includes('fill'), `expected drawing calls, got ${[...new Set(calls)]}`);
    assert.equal(page.scheduledFrames.length, 0);
    assert.equal(page.loadingState.dataset.animation, 'running');
  });

  it('leaves the loading state unmarked when no 2D context is available', () => {
    // Given a canvas that cannot provide a 2D context
    const page = loadingPage({ context: null });
    assert.equal(page.loadingState.dataset.animation, undefined);

    // When the animation starts
    startHarmonizeAnimation(page.win);

    // Then the state is not marked, so the spinner stays visible, and no frame is scheduled
    assert.equal(page.loadingState.dataset.animation, undefined);
    assert.equal(page.scheduledFrames.length, 0);
  });

  it('leaves the loading state unmarked when the first frame cannot be drawn', () => {
    // Given a 2D context whose drawing fails
    const drawFailure = new Error('context lost');
    const context = mockContext([]);
    context.clearRect = () => { throw drawFailure; };
    const page = loadingPage({ context });
    assert.equal(page.loadingState.dataset.animation, undefined);

    // When the animation starts
    assert.throws(() => startHarmonizeAnimation(page.win), drawFailure);

    // Then the state is not marked, so the spinner stays visible, and no frame is scheduled
    assert.equal(page.loadingState.dataset.animation, undefined);
    assert.equal(page.scheduledFrames.length, 0);
  });

  it('draws nothing while the canvas is not rendered, and keeps the loop alive', () => {
    // Given a running animation whose canvas has since been hidden
    const calls = [];
    const page = loadingPage({ context: mockContext(calls) });
    startHarmonizeAnimation(page.win);
    page.hideCanvas();
    const callsBeforeFrame = calls.length;
    assert.ok(callsBeforeFrame > 0);

    // When the next animation frame runs
    page.scheduledFrames[0](1000);

    // Then nothing is drawn, and a later frame is still scheduled for when the canvas returns
    assert.equal(calls.length, callsBeforeFrame);
    assert.equal(page.scheduledFrames.length, 2);
  });
});
