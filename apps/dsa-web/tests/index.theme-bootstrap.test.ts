// @vitest-environment node

import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

describe('index.html theme bootstrap', () => {
  it('preloads the dark-only design before React mounts', () => {
    const indexHtml = readFileSync(resolve(__dirname, '..', 'index.html'), 'utf8');

    expect(indexHtml).toContain("document.documentElement.classList.add('dark');");
    expect(indexHtml).toContain("document.documentElement.style.colorScheme = 'dark';");
    expect(indexHtml).not.toContain("localStorage.getItem('theme')");
  });
});
