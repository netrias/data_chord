/**
 * Global type declarations for browser JavaScript.
 * These declarations suppress false positives from TypeScript checking.
 */

/* Module path aliases resolved at runtime by the web server */
declare module '/assets/shared/step-instruction-ui.js' {
  export function initStepInstruction(instructionKey: string): void;
  export function setActiveStage(stageKey: string, hasCompletedResults?: boolean): void;
  export function initNavigationEvents(): void;
  export function advanceMaxReachedStage(stageKey: string): void;
  export function resetMaxReachedStage(): void;
  export function renderFileContext(): void;
}

declare module '/assets/shared/file-session.js' {
  interface FileSession {
    file_id: string;
    original_name: string;
    uploaded_at?: string;
    human_size?: string;
    tabular_format?: string | null;
    sheet_names?: string[];
    sheet_previews?: Record<string, unknown>;
    selected_sheet?: string | null;
    dataset_shape?: { sheet_name: string | null; rows: number; columns: number | null };
  }
  export function readCurrentFileSession(): FileSession | null;
  export function readFileSession(fileId: string | null | undefined): FileSession | null;
  export function saveFileSession(uploadResponse: Record<string, unknown>, fileName: string): FileSession;
  export function updateFileSession(fileId: string, fields: Partial<FileSession>, options?: { discardStaleOnFailure?: boolean }): void;
  export function clearFileSession(): void;
  export function selectedSheetShape(session: FileSession | null, sheetName?: string | null): { rows: number; columns: number } | null;
  export function formatCount(count: number): string;
}

declare module '/assets/shared/storage-keys.js' {
  export function isValidFileId(fileId: string | null): boolean;
  export const SessionKey: Record<string, string>;
}

type StageConfig = Record<string, any>;

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
