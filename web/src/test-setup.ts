/**
 * jsdom under Node 25: Node ships its own `localStorage` global, which shadows
 * jsdom's Storage and is an inert `{}` when no `--localstorage-file` was given.
 * Tests that exercise the connection choice need a working Storage, so install
 * a small in-memory one when the ambient global is not usable.
 */
class MemoryStorage implements Storage {
  private data = new Map<string, string>();

  get length(): number {
    return this.data.size;
  }

  clear(): void {
    this.data.clear();
  }

  getItem(key: string): string | null {
    return this.data.has(key) ? (this.data.get(key) as string) : null;
  }

  key(index: number): string | null {
    return Array.from(this.data.keys())[index] ?? null;
  }

  removeItem(key: string): void {
    this.data.delete(key);
  }

  setItem(key: string, value: string): void {
    this.data.set(String(key), String(value));
  }
}

const ambient = (globalThis as { localStorage?: Storage }).localStorage;
const usable = !!ambient && typeof ambient.getItem === 'function' && typeof ambient.setItem === 'function';

if (!usable) {
  const store = new MemoryStorage();
  try {
    Object.defineProperty(globalThis, 'localStorage', {
      value: store,
      configurable: true,
      writable: true,
    });
  } catch {
    (globalThis as { localStorage?: Storage }).localStorage = store;
  }
}
