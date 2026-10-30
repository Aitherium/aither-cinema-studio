import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import App from './App';
import { useStudio } from './store';
import { createStudioRegistry } from './webmcp/registry';
import './styles.css';

const root = document.getElementById('root');
if (root) {
  createRoot(root).render(
    <StrictMode>
      <App />
    </StrictMode>,
  );
}

/**
 * Load the vocabulary, install the WebMCP surface, register the tools and
 * publish window.cinemaStudio. Nothing here blocks the first paint.
 */
async function bootStudio(): Promise<void> {
  const store = useStudio;
  try {
    await store.getState().bootstrap();
  } catch {
    /* offline: the bundled vocabulary still builds every schema */
  }
  try {
    await createStudioRegistry({ vocab: store.getState().vocab });
  } catch (e) {
    console.error('[webmcp] tool registration failed', e);
    store.getState().setWebmcp({ surface: 'unavailable', tools: 0 });
  }
}

void bootStudio();
