import { beforeEach, describe, expect, it, vi } from 'vitest';
import {
  detectSurface,
  installModelContext,
  ModelContext,
  resetModelContextForTests,
  type ToolDefinition,
} from './polyfill';

const makeTool = (name: string, execute: ToolDefinition['execute'] = () => ({ ok: true })): ToolDefinition => ({
  name,
  title: name,
  description: `does ${name}`,
  inputSchema: { type: 'object', properties: {} },
  execute,
});

describe('ModelContext polyfill', () => {
  let ctx: ModelContext;

  beforeEach(() => {
    resetModelContextForTests();
    ctx = new ModelContext();
  });

  it('registers a tool and lists it', async () => {
    await ctx.registerTool(makeTool('set-camera'));
    const tools = await ctx.getTools();
    expect(tools.map((t) => t.name)).toEqual(['set-camera']);
  });

  it('rejects a duplicate name and keeps the first registration', async () => {
    await ctx.registerTool(makeTool('render-shot'));
    await expect(ctx.registerTool(makeTool('render-shot'))).rejects.toThrow(/already registered/);
    expect(await ctx.getTools()).toHaveLength(1);
  });

  it('unregisters when the registration signal aborts', async () => {
    const changes: string[] = [];
    ctx.addEventListener('toolchange', (event) => {
      const detail = (event as CustomEvent<{ type: string; name: string }>).detail;
      changes.push(`${detail.type}:${detail.name}`);
    });

    const controller = new AbortController();
    await ctx.registerTool(makeTool('grade-shot'), { signal: controller.signal });
    expect(changes).toEqual(['registered:grade-shot']);

    controller.abort();
    expect(await ctx.getTools()).toHaveLength(0);
    expect(changes).toEqual(['registered:grade-shot', 'unregistered:grade-shot']);
  });

  it('lets a name be registered again after its signal aborted', async () => {
    const first = new AbortController();
    await ctx.registerTool(makeTool('cut-sequence'), { signal: first.signal });
    first.abort();
    await expect(ctx.registerTool(makeTool('cut-sequence'))).resolves.toBeUndefined();
    expect(await ctx.getTools()).toHaveLength(1);
  });

  it('refuses a signal that is already aborted', async () => {
    const controller = new AbortController();
    controller.abort();
    await expect(ctx.registerTool(makeTool('plan-shot'), { signal: controller.signal })).rejects.toThrow(/aborted/);
    expect(await ctx.getTools()).toHaveLength(0);
  });

  it('executes a tool and wraps the result', async () => {
    await ctx.registerTool(makeTool('list-shots', () => ({ shots: [] })));
    const result = await ctx.executeTool('list-shots', {});
    expect(result.isError).toBe(false);
    expect(result.structuredContent).toEqual({ shots: [] });
    expect(result.content[0]).toEqual({ type: 'text', text: '{"shots":[]}' });
  });

  it('returns error results for unknown tools and thrown errors', async () => {
    const missing = await ctx.executeTool('does-not-exist');
    expect(missing.isError).toBe(true);

    await ctx.registerTool(
      makeTool('boom', () => {
        throw new Error('kaboom');
      }),
    );
    const failed = await ctx.executeTool('boom');
    expect(failed.isError).toBe(true);
    expect(failed.content[0].text).toBe('kaboom');
  });

  it('validates the tool shape on registration', async () => {
    await expect(ctx.registerTool(makeTool('Bad Name'))).rejects.toThrow(/kebab-case/);
    await expect(
      ctx.registerTool({ ...makeTool('no-exec'), execute: undefined as unknown as ToolDefinition['execute'] }),
    ).rejects.toThrow(/execute/);
    await expect(ctx.registerTool({ ...makeTool('no-schema'), inputSchema: undefined as never })).rejects.toThrow(
      /inputSchema/,
    );
  });

  it('dispose drops everything it registered', async () => {
    await ctx.registerTool(makeTool('list-camera-moves'));
    await ctx.registerTool(makeTool('get-shot'));
    ctx.dispose();
    expect(await ctx.getTools()).toHaveLength(0);
  });
});

describe('installModelContext', () => {
  beforeEach(() => {
    resetModelContextForTests();
  });

  it('installs the polyfill when no native API exists', () => {
    expect(detectSurface().surface).toBe('polyfill');
    expect(installModelContext()).toBe('polyfill');
    const installed = (document as unknown as { modelContext?: ModelContext }).modelContext;
    expect(typeof installed?.registerTool).toBe('function');
    expect(typeof installed?.executeTool).toBe('function');
  });

  it('never replaces a native document.modelContext', () => {
    const native = {
      registerTool: vi.fn(),
      getTools: vi.fn(),
      executeTool: vi.fn(),
      addEventListener: vi.fn(),
    } as unknown as ModelContext;
    (document as unknown as { modelContext?: ModelContext }).modelContext = native;

    expect(detectSurface().surface).toBe('document');
    expect(installModelContext()).toBe('document');
    expect((document as unknown as { modelContext?: ModelContext }).modelContext).toBe(native);
  });

  it('prefers document over navigator, and navigator over the polyfill', () => {
    const navCtx = {
      registerTool: vi.fn(),
      getTools: vi.fn(),
      executeTool: vi.fn(),
    } as unknown as ModelContext;
    Object.defineProperty(navigator, 'modelContext', { value: navCtx, configurable: true });

    // navigator only
    expect(detectSurface()).toEqual({ surface: 'navigator', ctx: navCtx });
    expect(installModelContext()).toBe('navigator');

    // document wins once it exists
    const docCtx = {
      registerTool: vi.fn(),
      getTools: vi.fn(),
      executeTool: vi.fn(),
    } as unknown as ModelContext;
    (document as unknown as { modelContext?: ModelContext }).modelContext = docCtx;
    expect(detectSurface()).toEqual({ surface: 'document', ctx: docCtx });
  });
});
