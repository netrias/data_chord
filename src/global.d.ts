/**
 * Global type declarations for browser JavaScript.
 * These declarations suppress false positives from TypeScript checking.
 */

/* Module path aliases resolved at runtime by the web server */
declare module '/assets/shared/step-instruction-ui.js' {
  export function initStepInstruction(instructionKey: string): void;
  export function setActiveStage(stageKey: string): void;
  export function initNavigationEvents(): void;
  export function advanceMaxReachedStage(stageKey: string): void;
}

declare module '/assets/shared/storage-keys.js' {
  export function isValidFileId(fileId: string | null): boolean;
  export const SessionKey: Record<string, string>;
}

declare module '/assets/shared/combobox.js' {
  export function createCombobox(config: unknown): HTMLDivElement;
}

declare module '/assets/shared/row-state.js' {
  export function createRowState(): unknown;
}

type StageConfig = Record<string, any>;

interface ClusterizeOptions {
  rows?: string[];
  scrollId?: string;
  contentId?: string;
  callbacks?: Record<string, Function>;
  [key: string]: any;
}

declare class Clusterize {
  constructor(options: ClusterizeOptions);
  update(rows: string[]): void;
  destroy(clean?: boolean): void;
}

interface Element {
  checked?: boolean;
  dataset: DOMStringMap;
  disabled?: boolean;
  files?: FileList | null;
  focus?: () => void;
  showModal?: () => void;
  close?: () => void;
  style: CSSStyleDeclaration;
  tabIndex: number;
  tagName?: string;
  value?: any;
  destroy?: () => void;
  setValue?: (value: string) => void;
}

interface EventTarget {
  checked?: boolean;
  closest?: (selectors: string) => Element | null;
  files?: FileList | null;
  tagName?: string;
  value?: any;
}

interface HTMLDivElement {
  destroy?: () => void;
  setValue?: (value: string) => void;
}

/* Stage config objects injected into window by server templates */
interface Window {
  stageOneUploadConfig?: StageConfig;
  stageTwoConfig?: StageConfig;
  stageThreeConfig?: StageConfig;
  stageFourConfig?: StageConfig;
  stageFiveConfig?: StageConfig;
}
