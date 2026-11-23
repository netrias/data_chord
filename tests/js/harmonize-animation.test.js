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
    const scheduledFrames = new Map();
    let nextFrame = 0;
    let onResize;
    const canvas = win.document.querySelector('canvas');
    canvas.getContext = () => context;
    let width = canvasWidth;
    Object.defineProperty(canvas, 'clientWidth', { get: () => width });
    win.matchMedia = () => ({ matches: reducedMotion });
    win.requestAnimationFrame = (callback) => {
      scheduledFrames.set(++nextFrame, callback);
      return nextFrame;
    };
    win.cancelAnimationFrame = (id) => scheduledFrames.delete(id);
    win.ResizeObserver = class {
      constructor(callback) { onResize = callback; }
      observe() {}
      disconnect() { onResize = undefined; }
    };
    return {
      win,
      canvas,
      scheduledFrames,
      loadingState: win.document.getElementById('loadingState'),
      resizeCanvas: (nextWidth) => { width = nextWidth; onResize?.(); },
      playFrame: () => {
        const [id, callback] = scheduledFrames.entries().next().value;
        scheduledFrames.delete(id);
        callback(1000);
      },
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
    assert.equal(page.scheduledFrames.size, 1);
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
    assert.equal(page.scheduledFrames.size, 0);
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
    assert.equal(page.scheduledFrames.size, 0);
  });

  for (const { name, framesBeforeFailure } of [
    { name: 'the first frame', framesBeforeFailure: 0 },
    { name: 'a later frame', framesBeforeFailure: 1 },
  ]) {
    it(`unmarks the loading state and stops when ${name} cannot be drawn`, () => {
      // Given a 2D context whose drawing fails after a set number of good frames
      const drawFailure = new Error('context lost');
      const context = mockContext([]);
      let framesLeft = framesBeforeFailure;
      context.clearRect = () => {
        if (framesLeft === 0) throw drawFailure;
        framesLeft -= 1;
      };
      const page = loadingPage({ context });
      assert.equal(page.loadingState.dataset.animation, undefined);

      // When the animation runs up to the failing frame
      const runToFailure = () => {
        startHarmonizeAnimation(page.win);
        page.playFrame();
      };
      assert.throws(runToFailure, drawFailure);

      // Then the state is not marked, so the spinner is visible, and no further frame is scheduled
      assert.equal(page.loadingState.dataset.animation, undefined);
      assert.equal(page.scheduledFrames.size, 0);
    });
  }

  it('cancels hidden frames and resumes when the loading panel is shown again', () => {
    // Given a visible animation with one scheduled frame
    const calls = [];
    const page = loadingPage({ context: mockContext(calls) });
    startHarmonizeAnimation(page.win);
    const callsBeforeHide = calls.length;
    assert.equal(page.scheduledFrames.size, 1);

    // When the canvas disappears
    page.resizeCanvas(0);

    // Then it cancels the queued frame without drawing again
    assert.equal(page.scheduledFrames.size, 0);
    assert.equal(calls.length, callsBeforeHide);

    // When a retry shows the canvas again
    page.resizeCanvas(680);

    // Then it paints and queues exactly one frame
    assert.ok(calls.length > callsBeforeHide);
    assert.equal(page.scheduledFrames.size, 1);
  });

  it('restores the spinner and stops running when the canvas loses its context', () => {
    // Given a visible animation with one frame queued
    const page = loadingPage({ context: mockContext([]) });
    startHarmonizeAnimation(page.win);
    assert.equal(page.scheduledFrames.size, 1);

    // When the browser signals that the canvas context was lost
    page.canvas.dispatchEvent(new page.win.Event('contextlost'));

    // Then the spinner marker is restored and no frame remains queued
    assert.equal(page.loadingState.dataset.animation, undefined);
    assert.equal(page.scheduledFrames.size, 0);
  });
});
