// @vitest-environment node

import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

describe('font loading', () => {
  it('lets Vite bundle Fontsource assets without stale public font URLs', () => {
    const main = readFileSync(resolve(__dirname, '..', 'src', 'main.tsx'), 'utf8');
    const indexCss = readFileSync(resolve(__dirname, '..', 'src', 'index.css'), 'utf8');
    const globalsCss = readFileSync(
      resolve(__dirname, '..', 'src', 'styles', 'globals.css'),
      'utf8',
    );
    const tokensCss = readFileSync(
      resolve(__dirname, '..', 'src', 'styles', 'tokens.css'),
      'utf8',
    );

    expect(main).toContain("import '@fontsource/geist-sans/latin-400.css'");
    expect(main).toContain("import '@fontsource/geist-mono/latin-400.css'");
    expect(indexCss).not.toContain('@import "@fontsource/');
    expect(globalsCss).not.toContain('/fonts/');
    expect(tokensCss).toContain('--font-sans: "Geist Sans"');
  });
});
