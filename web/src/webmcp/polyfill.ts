/**
 * A clean, spec-shaped implementation of the WebMCP model context surface.
 *
 * Browsers that already ship `document.modelContext` (or the older
 * `navigator.modelContext`) are never touched — we only fill the gap so the
 * studio's tools are reachable today. The shape follows the WebMCP drafts:
 *
 *   modelContext.registerTool(tool, { signal })   -> Promise<void>
 *   modelContext.getTools()                       -> Promise<tool[]>
 *   modelContext.executeTool(name, args, {signal})-> Promise<ToolResult>
 *   modelContext.addEventListener('toolchange', e)
 *
 * `signal` is the unregister channel: aborting it removes the tool and fires
 * `toolchange`. Registering a name twice rejects.
 */

export type JsonSchema = {
  type?: string;
  title?: string;
  description?: string;
  properties?: Record<string, JsonSchema>;
  required?: string[];
  enum?: unknown[];
  items?: JsonSchema;
  minimum?: number;
  maximum?: number;
  default?: unknown;
  additionalProperties?: boolean | JsonSchema;
  oneOf?: JsonSchema[];
  anyOf?: JsonSchema[];
};

export interface ToolCallContext {
  signal?: AbortSignal;
}

export interface ToolDefinition {
  name: string;
  title?: string;
  description: string;
  inputSchema: JsonSchema;
  annotations?: Record<string, boolean>;
  execute: (args: Record<string, unknown>, ctx?: ToolCallContext) => unknown | Promise<unknown>;
}

export interface ToolResult {
  content: { type: 'text'; text: string }[];
  structuredContent?: unknown;
  isError: boolean;
}

export interface RegisterOptions {
  signal?: AbortSignal;
}

export interface ToolChangeDetail {
  type: 'registered' | 'unregistered';
  name: string;
  tools: string[];
}

/** The subset of the model context API this app relies on. */
export interface ModelContextLike extends EventTarget {
  registerTool(tool: ToolDefinition, options?: RegisterOptions): Promise<void>;
  getTools(): Promise<ToolDefinition[]>;
  executeTool(
    name: string,
    args?: Record<string, unknown>,
    options?: ToolCallContext,
  ): Promise<ToolResult>;
}

const NAME_RE = /^[a-z0-9][a-z0-9.-]*$/;

function assertTool(tool: ToolDefinition): void {
  if (!tool || typeof tool !== 'object') throw new TypeError('registerTool: a tool object is required');
  if (typeof tool.name !== 'string' || !tool.name) throw new TypeError('registerTool: tool.name must be a non-empty string');
  if (!NAME_RE.test(tool.name)) throw new TypeError(`registerTool: invalid tool name "${tool.name}" (use kebab-case)`);
  if (typeof tool.description !== 'string' || !tool.description) {
    throw new TypeError(`registerTool: tool "${tool.name}" needs a description`);
  }
  if (!tool.inputSchema || typeof tool.inputSchema !== 'object') {
    throw new TypeError(`registerTool: tool "${tool.name}" needs an inputSchema`);
  }
  if (typeof tool.execute !== 'function') {
    throw new TypeError(`registerTool: tool "${tool.name}" needs an execute function`);
  }
}

function textResult(value: unknown, isError = false): ToolResult {
  let text: string;
  if (typeof value === 'string') text = value;
  else {
    try {
      text = JSON.stringify(value ?? null);
    } catch {
      text = String(value);
    }
  }
  return { content: [{ type: 'text', text }], structuredContent: value, isError };
}

function makeEvent(detail: ToolChangeDetail): Event {
  return typeof CustomEvent === 'function'
    ? new CustomEvent<ToolChangeDetail>('toolchange', { detail })
    : new Event('toolchange');
}

interface Entry {
  tool: ToolDefinition;
  controller: AbortController;
}

export class ModelContext extends EventTarget implements ModelContextLike {
  private entries = new Map<string, Entry>();

  async registerTool(tool: ToolDefinition, options: RegisterOptions = {}): Promise<void> {
    assertTool(tool);
    if (this.entries.has(tool.name)) {
      throw new Error(`Tool "${tool.name}" is already registered`);
    }
    if (options.signal?.aborted) {
      throw new Error(`Tool "${tool.name}" was registered with an aborted signal`);
    }

    const controller = new AbortController();
    const entry: Entry = { tool: { ...tool }, controller };

    // Follow the caller's signal, and keep our own so dispose() can drop
    // everything the app registered without touching the caller's controller.
    if (options.signal) {
      options.signal.addEventListener('abort', () => this.drop(tool.name), { once: true });
    }

    this.entries.set(tool.name, entry);
    this.emit('registered', tool.name);
  }

  async unregisterTool(name: string): Promise<void> {
    this.drop(name);
  }

  async getTools(): Promise<ToolDefinition[]> {
    return [...this.entries.values()].map((e) => e.tool);
  }

  /** Local shortcut used by the studio; tools read `entries` synchronously. */
  listTools(): ToolDefinition[] {
    return [...this.entries.values()].map((e) => e.tool);
  }

  async executeTool(
    name: string,
    args: Record<string, unknown> = {},
    options: ToolCallContext = {},
  ): Promise<ToolResult> {
    const entry = this.entries.get(name);
    if (!entry) return textResult(`Tool not found: ${name}`, true);
    if (options.signal?.aborted) return textResult(`Tool call aborted: ${name}`, true);
    try {
      const value = await entry.tool.execute(args, { signal: options.signal ?? entry.controller.signal });
      return textResult(value);
    } catch (e) {
      return textResult(e instanceof Error ? e.message : String(e), true);
    }
  }

  /** Abort every registration made through this context. */
  dispose(): void {
    for (const name of [...this.entries.keys()]) this.drop(name);
  }

  private drop(name: string): void {
    const entry = this.entries.get(name);
    if (!entry) return;
    this.entries.delete(name);
    entry.controller.abort();
    this.emit('unregistered', name);
  }

  private emit(type: ToolChangeDetail['type'], name: string): void {
    this.dispatchEvent(makeEvent({ type, name, tools: [...this.entries.keys()] }));
  }
}

/* --------------------------------------------------------------- install --- */

export type Surface = 'document' | 'navigator' | 'polyfill';

interface AnyGlobals {
  document?: { modelContext?: ModelContextLike } & Record<string, unknown>;
  navigator?: { modelContext?: ModelContextLike } & Record<string, unknown>;
}

function globals(): AnyGlobals {
  const g = globalThis as unknown as AnyGlobals;
  return g;
}

const hasRegister = (ctx: unknown): ctx is ModelContextLike =>
  !!ctx && typeof (ctx as ModelContextLike).registerTool === 'function';

/** Which model context wins: document first, then navigator, then nothing. */
export function detectSurface(): { surface: Surface; ctx?: ModelContextLike } {
  const g = globals();
  const doc = g.document?.modelContext;
  if (hasRegister(doc)) {
    // Our own installed polyfill answers as the polyfill, not as a native API.
    return { surface: doc instanceof ModelContext ? 'polyfill' : 'document', ctx: doc };
  }
  const nav = g.navigator?.modelContext;
  if (hasRegister(nav)) {
    return { surface: nav instanceof ModelContext ? 'polyfill' : 'navigator', ctx: nav };
  }
  return { surface: 'polyfill' };
}

let singleton: ModelContext | null = null;

/** The context tools are registered into — native if present, else ours. */
export function getModelContext(): ModelContextLike {
  const detected = detectSurface();
  if (detected.ctx) return detected.ctx;
  if (!singleton) singleton = new ModelContext();
  return singleton;
}

export function createModelContext(): ModelContext {
  return new ModelContext();
}

/**
 * Install the polyfill only when no native API exists. Returns the surface the
 * studio ended up on, so the status bar can name it honestly.
 */
export function installModelContext(): Surface {
  const detected = detectSurface();
  if (detected.surface !== 'polyfill') return detected.surface;

  const g = globals();
  if (!g.document) return 'polyfill'; // non-DOM environment (tests): nothing to patch
  g.document.modelContext = getModelContext();
  return 'polyfill';
}

/** Test helper: forget the singleton so each test starts clean. */
export function resetModelContextForTests(): void {
  singleton?.dispose();
  singleton = null;
  const g = globals();
  if (g.document) delete g.document.modelContext;
  if (g.navigator) delete g.navigator.modelContext;
}
