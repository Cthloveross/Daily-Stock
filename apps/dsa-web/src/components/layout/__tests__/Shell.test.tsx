import { render } from '@testing-library/react';
import { MemoryRouter, Routes, Route } from 'react-router-dom';
import { beforeAll, describe, expect, it, vi } from 'vitest';
import { Shell } from '../Shell';

vi.mock('../../../contexts/AuthContext', () => ({
  useAuth: () => ({
    authEnabled: false,
    logout: vi.fn(),
  }),
}));

vi.mock('../../../stores/regimeStore', () => ({
  useRegimeStore: (selector: (state: { today: null }) => unknown) =>
    selector({ today: null }),
}));

beforeAll(() => {
  Object.defineProperty(window, 'matchMedia', {
    writable: true,
    value: vi.fn().mockImplementation((query: string) => ({
      matches: query === '(prefers-color-scheme: dark)',
      media: query,
      onchange: null,
      addListener: vi.fn(),
      removeListener: vi.fn(),
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })),
  });
});

describe('Shell', () => {
  it('renders navigation and topbar', () => {
    const { container } = render(
      <MemoryRouter initialEntries={['/regime']}>
        <Routes>
          <Route element={<Shell />}>
            <Route path="/regime" element={<div>page content</div>} />
          </Route>
        </Routes>
      </MemoryRouter>,
    );
    expect(container).toBeTruthy();
  });
});
