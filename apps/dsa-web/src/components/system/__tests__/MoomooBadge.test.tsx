import { render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import apiClient from '../../../api';
import { MoomooBadge } from '../MoomooBadge';

vi.mock('../../../api', () => ({
  default: {
    get: vi.fn(),
  },
}));

const getMock = vi.mocked(apiClient.get);

afterEach(() => {
  vi.clearAllMocks();
});

describe('MoomooBadge', () => {
  it('labels a reachable OpenD endpoint as read-only, never as live trading', async () => {
    getMock.mockResolvedValue({
      data: {
        enabled: true,
        sdk_installed: true,
        connected: true,
        host: '127.0.0.1',
        port: 11111,
        trd_env: 'LIVE',
        sdk_version: 'test',
        probe_level: 'tcp',
        read_only: true,
      },
    });

    const { container } = render(<MoomooBadge />);

    const badge = await screen.findByText('Moomoo 可达 · 只读');
    expect(badge).toBeInTheDocument();
    expect(badge).toHaveAttribute(
      'aria-label',
      expect.stringContaining('项目只读，不解锁或下单'),
    );
    expect(container).not.toHaveTextContent('MOOMOO LIVE');
  });

  it('keeps the offline state explicit', async () => {
    getMock.mockResolvedValue({
      data: {
        enabled: true,
        sdk_installed: true,
        connected: false,
        host: '127.0.0.1',
        port: 11111,
        trd_env: 'LIVE',
        message: 'OpenD daemon not reachable',
      },
    });

    render(<MoomooBadge />);

    expect(await screen.findByText('Moomoo offline')).toBeInTheDocument();
  });
});
