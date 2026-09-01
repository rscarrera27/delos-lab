import "@testing-library/jest-dom/vitest";
import { afterEach, vi } from "vitest";

// Radix primitives rely on browser pointer-capture and scrolling APIs that
// jsdom does not implement. Keep the test environment aligned with browsers.
Object.defineProperties(HTMLElement.prototype, {
  hasPointerCapture: {
    configurable: true,
    value: () => false,
  },
  setPointerCapture: {
    configurable: true,
    value: () => undefined,
  },
  releasePointerCapture: {
    configurable: true,
    value: () => undefined,
  },
  scrollIntoView: {
    configurable: true,
    value: () => undefined,
  },
});

class TestResizeObserver implements ResizeObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
}

vi.stubGlobal("ResizeObserver", TestResizeObserver);

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});
