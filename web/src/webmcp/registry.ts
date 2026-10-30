/**
 * Registers the studio tools into whichever model context exists and keeps the
 * activity feed honest: every call — agent or human — lands there exactly once.
 */
import { asActor, getActor, errText, setFeedMode, useStudio, type StudioApi } from '../store';
import { buildToolDefinitions, CONTRACT_TOOL_NAMES } from '../tools';
import type { Actor } from '../types';
import type { CameraVocab } from '../cameraVocab';
import {
  detectSurface,
  getModelContext,
  installModelContext,
  type ModelContextLike,
  type Surface,
  type ToolDefinition,
} from './polyfill';

export const SURFACE_LABEL: Record<Surface, string> = {
  document: 'native · document.modelContext',
  navigator: 'native · navigator.modelContext',
  polyfill: 'polyfill · document.modelContext',
};

export interface StudioRegistry {
  surface: Surface;
  surfaceLabel: string;
  tools(): ToolDefinition[];
  names(): string[];
  call(name: string, args?: Record<string, unknown>, actor?: Actor): Promise<unknown>;
  dispose(): void;
}

/**
 * What the feed may record: a key a caller smuggled into the arguments is never
 * echoed, only the fact that one was passed. No tool schema admits an `api_key`
 * — this is the second line, so a stray argument cannot leak a secret into the
 * feed, the console, or a screenshot of it.
 */
function feedArgs(args: Record<string, unknown>): Record<string, unknown> {
  if (!args || !('api_key' in args)) return args;
  return { ...args, api_key: args.api_key ? '(not logged)' : undefined };
}

function withFeed(def: ToolDefinition, store: StudioApi): ToolDefinition {
  return {
    ...def,
    execute: async (args: Record<string, unknown> = {}, ctx) => {
      const actor = getActor();
      // The store actions log themselves for a human click; during a tool call
      // this wrapper owns the single feed row, named after the tool.
      setFeedMode('manual');
      const logged = feedArgs(args);
      try {
        const result = await def.execute(args, ctx);
        store.getState().pushFeed({ tool: def.name, args: logged, result, status: 'ok', actor });
        return result;
      } catch (e) {
        store.getState().pushFeed({ tool: def.name, args: logged, result: errText(e), status: 'error', actor });
        throw e;
      } finally {
        setFeedMode('auto');
      }
    },
  };
}

export interface RegistryOptions {
  vocab: CameraVocab;
  /** Injectable for tests; defaults to the live studio store. */
  store?: StudioApi;
  /** Provide a context explicitly (tests) instead of installing/finding one. */
  context?: ModelContextLike;
  /** Publish window.cinemaStudio (default true). */
  expose?: boolean;
}

export async function createStudioRegistry(options: RegistryOptions): Promise<StudioRegistry> {
  const store = options.store ?? useStudio;
  const surface: Surface = options.context ? detectSurface().surface : installModelContext();
  const context = options.context ?? getModelContext();

  const definitions = buildToolDefinitions(options.vocab, store).map((def) => withFeed(def, store));
  const byName = new Map(definitions.map((def) => [def.name, def]));
  const controllers: AbortController[] = [];
  const registered: string[] = [];

  for (const def of definitions) {
    const controller = new AbortController();
    controllers.push(controller);
    try {
      await context.registerTool(def, { signal: controller.signal });
      registered.push(def.name);
    } catch (e) {
      // A name already taken (a previous mount, or a native context refusing
      // one): `call()` still works, so the studio degrades one tool at a time
      // instead of losing the whole surface.
      console.warn(`[webmcp] could not register "${def.name}":`, e);
    }
  }

  store.getState().setWebmcp({
    surface: SURFACE_LABEL[surface],
    tools: registered.length,
  });

  const registry: StudioRegistry = {
    surface,
    surfaceLabel: SURFACE_LABEL[surface],
    tools: () => definitions,
    names: () => definitions.map((d) => d.name),
    async call(name, args = {}, actor = 'you') {
      const def = byName.get(name);
      if (!def) throw new Error(`Unknown tool "${name}" (have: ${definitions.map((d) => d.name).join(', ')})`);
      return asActor(actor, () => def.execute(args, {}));
    },
    dispose() {
      for (const controller of controllers) controller.abort();
    },
  };

  if (options.expose !== false) exposeGlobals(registry);
  return registry;
}

/**
 * The demo/test handle: `window.cinemaStudio.tools()` lists the surface,
 * `window.cinemaStudio.call('set-camera', {move:'orbit_left'})` directs the shoot.
 */
export function exposeGlobals(registry: StudioRegistry, target: unknown = globalThis): void {
  const w = target as { cinemaStudio?: unknown; window?: unknown };
  const host = (w.window ?? target) as { cinemaStudio?: unknown };
  host.cinemaStudio = {
    tools: () =>
      registry.tools().map((tool) => ({
        name: tool.name,
        title: tool.title,
        description: tool.description,
        inputSchema: tool.inputSchema,
      })),
    names: () => registry.names(),
    surface: registry.surfaceLabel,
    call: (name: string, args?: Record<string, unknown>, actor?: Actor) => registry.call(name, args, actor),
  };
}

/** The contract list, re-exported so tests and the status bar share one source. */
export const toolContract = CONTRACT_TOOL_NAMES;
