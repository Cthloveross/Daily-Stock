import { act, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import apiClient from '../../../api';
import { MoomooBadge } from '../MoomooBadge';
import {
  __resetMoomooStatusMonitorForTests,
  MOOMOO_STATUS_STORAGE_KEY,
} from '../moomooStatusMonitor';

vi.mock('../../../api', () => ({
  default: {
    get: vi.fn(),
  },
}));

const getMock = vi.mocked(apiClient.get);
const navigatorLocksDescriptor = Object.getOwnPropertyDescriptor(navigator, 'locks');

afterEach(() => {
  __resetMoomooStatusMonitorForTests();
  vi.clearAllMocks();
  vi.useRealTimers();
  Object.defineProperty(document, 'visibilityState', {
    configurable: true,
    value: 'visible',
  });
  if (navigatorLocksDescriptor) {
    Object.defineProperty(navigator, 'locks', navigatorLocksDescriptor);
  } else {
    Reflect.deleteProperty(navigator, 'locks');
  }
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

  it('shares one request and one polling clock across duplicate subscribers', async () => {
    vi.useFakeTimers();
    getMock.mockResolvedValue({
      data: {
        enabled: true,
        sdk_installed: true,
        connected: true,
        host: '127.0.0.1',
        port: 11111,
        trd_env: 'LIVE',
        read_only: true,
      },
    });

    render(
      <>
        <MoomooBadge />
        <MoomooBadge />
      </>,
    );

    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(getMock).toHaveBeenCalledTimes(1);
    expect(screen.getAllByText('Moomoo 可达 · 只读')).toHaveLength(2);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(30_000);
    });
    expect(getMock).toHaveBeenCalledTimes(1);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(30_000);
    });
    expect(getMock).toHaveBeenCalledTimes(2);
  });

  it('reuses a fresh status published by another tab without probing again', async () => {
    const now = Date.now();
    window.localStorage.setItem(MOOMOO_STATUS_STORAGE_KEY, JSON.stringify({
      version: 1,
      checkedAt: now,
      nextProbeAt: now + 60_000,
      failureCount: 0,
      outcome: 'success',
      status: {
        enabled: true,
        sdkInstalled: true,
        connected: true,
        host: '127.0.0.1',
        port: 11111,
        trdEnv: 'LIVE',
        readOnly: true,
      },
    }));

    render(<MoomooBadge />);

    expect(await screen.findByText('Moomoo 可达 · 只读')).toBeInTheDocument();
    expect(getMock).not.toHaveBeenCalled();
  });

  it('waits when another tab owns the browser lock and adopts its published result', async () => {
    const lockRequest = vi.fn(async (
      _name: string,
      _options: unknown,
      callback: (lock: null) => unknown,
    ) => callback(null));
    Object.defineProperty(navigator, 'locks', {
      configurable: true,
      value: { request: lockRequest },
    });

    render(<MoomooBadge />);
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(lockRequest).toHaveBeenCalledTimes(1);
    expect(getMock).not.toHaveBeenCalled();

    const now = Date.now();
    const sharedRaw = JSON.stringify({
      version: 1,
      checkedAt: now,
      nextProbeAt: now + 60_000,
      failureCount: 0,
      outcome: 'success',
      status: {
        enabled: true,
        sdkInstalled: true,
        connected: true,
        host: '127.0.0.1',
        port: 11111,
        trdEnv: 'LIVE',
        readOnly: true,
      },
    });
    window.localStorage.setItem(MOOMOO_STATUS_STORAGE_KEY, sharedRaw);
    act(() => {
      window.dispatchEvent(new StorageEvent('storage', {
        key: MOOMOO_STATUS_STORAGE_KEY,
        newValue: sharedRaw,
      }));
    });

    expect(await screen.findByText('Moomoo 可达 · 只读')).toBeInTheDocument();
    expect(getMock).not.toHaveBeenCalled();
  });

  it('never presents an expired shared success as a live status', async () => {
    const now = Date.now();
    window.localStorage.setItem(MOOMOO_STATUS_STORAGE_KEY, JSON.stringify({
      version: 1,
      checkedAt: now - 90_000,
      nextProbeAt: now - 30_000,
      failureCount: 0,
      outcome: 'success',
      status: {
        enabled: true,
        sdkInstalled: true,
        connected: true,
        host: '127.0.0.1',
        port: 11111,
        trdEnv: 'LIVE',
        readOnly: true,
      },
    }));
    getMock.mockRejectedValue(new Error('server unavailable'));

    render(<MoomooBadge />);

    expect(await screen.findByText('Moomoo 状态未知')).toBeInTheDocument();
    expect(screen.queryByText('Moomoo 可达 · 只读')).not.toBeInTheDocument();
    expect(getMock).toHaveBeenCalledTimes(1);
  });

  it('does not poll in a hidden tab and refreshes as soon as it becomes visible', async () => {
    Object.defineProperty(document, 'visibilityState', {
      configurable: true,
      value: 'hidden',
    });
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
    await act(async () => {
      await Promise.resolve();
    });
    expect(getMock).not.toHaveBeenCalled();

    Object.defineProperty(document, 'visibilityState', {
      configurable: true,
      value: 'visible',
    });
    act(() => {
      document.dispatchEvent(new Event('visibilitychange'));
    });

    expect(await screen.findByText('Moomoo offline')).toBeInTheDocument();
    expect(getMock).toHaveBeenCalledTimes(1);
  });

  it('shows an explicit unknown state and retries immediately when connectivity returns', async () => {
    getMock
      .mockRejectedValueOnce(new Error('server unavailable'))
      .mockResolvedValueOnce({
        data: {
          enabled: true,
          sdk_installed: true,
          connected: true,
          host: '127.0.0.1',
          port: 11111,
          trd_env: 'LIVE',
          read_only: true,
        },
      });

    render(<MoomooBadge />);

    expect(await screen.findByText('Moomoo 状态未知')).toBeInTheDocument();

    act(() => {
      window.dispatchEvent(new Event('online'));
    });

    expect(await screen.findByText('Moomoo 可达 · 只读')).toBeInTheDocument();
    expect(getMock).toHaveBeenCalledTimes(2);
  });

  it('backs off repeated failures instead of producing a fixed noisy loop', async () => {
    vi.useFakeTimers();
    getMock.mockRejectedValue(new Error('server unavailable'));

    render(<MoomooBadge />);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(screen.getByText('Moomoo 状态未知')).toBeInTheDocument();
    expect(getMock).toHaveBeenCalledTimes(1);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(14_999);
    });
    expect(getMock).toHaveBeenCalledTimes(1);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1);
    });
    expect(getMock).toHaveBeenCalledTimes(2);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(29_999);
    });
    expect(getMock).toHaveBeenCalledTimes(2);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1);
    });
    expect(getMock).toHaveBeenCalledTimes(3);
  });
});
