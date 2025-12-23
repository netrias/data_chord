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

/* Stage config objects injected into window by server templates */
interface Window {
  stageOneUploadConfig?: Record<string, unknown>;
  stageTwoConfig?: Record<string, unknown>;
  stageThreeConfig?: Record<string, unknown>;
  stageFourConfig?: Record<string, unknown>;
  stageFiveConfig?: Record<string, unknown>;
}
