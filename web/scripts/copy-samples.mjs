#!/usr/bin/env node
/**
 * Copy the demo-mode sample clips out of docs/proof into web/public/samples.
 *
 * Only the generic proof renders already in this repository. They are build
 * inputs, not tracked assets: run by `predev` / `prebuild`, and the directory is
 * gitignored. A missing proof directory is a warning, never a failed build.
 */
import { copyFile, mkdir } from 'node:fs/promises';
import { existsSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const REPO = path.resolve(HERE, '..', '..');
const SOURCE = path.join(REPO, 'docs', 'proof');
const TARGET = path.join(HERE, '..', 'public', 'samples');

const FILES = [
  'wan_camera_draft.mp4',
  'wan_i2v_draft.mp4',
  'wan_t2v_draft.mp4',
  'ltx_t2v_draft.mp4',
  'keyframe_draft.png',
];

if (!existsSync(SOURCE)) {
  console.warn(`[samples] no ${SOURCE} — demo mode will show empty media frames`);
  process.exit(0);
}

await mkdir(TARGET, { recursive: true });

let copied = 0;
for (const file of FILES) {
  const from = path.join(SOURCE, file);
  if (!existsSync(from)) {
    console.warn(`[samples] missing ${file} — skipped`);
    continue;
  }
  await copyFile(from, path.join(TARGET, file));
  copied += 1;
}

console.log(`[samples] ${copied}/${FILES.length} proof clips in web/public/samples`);
