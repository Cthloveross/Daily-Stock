import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { SystemHealthPopover } from '../SystemHealthPopover';

const apiMocks = vi.hoisted(() => ({ get: vi.fn() }));

vi.mock('../../../api', () => ({ default: { get: apiMocks.get } }));

function healthResponse() {
  return {
    data: {
      schema_version: 'system-health-layers/1.0',
      generated_at: '2026-07-31T14:25:00+00:00',
      overall: 'degraded',
      layers: [
        { layer: 'api_process', state: 'ok', detail: 'API 进程存活' },
        { layer: 'opend_tcp', state: 'down', detail: '127.0.0.1:11111 TCP 不可达' },
        { layer: 'premarket_publication', state: 'degraded', detail: '尚无任何官方盘前发布' },
        { layer: 'future_layer_v2', state: 'ok', detail: '未知层原样展示' },
      ],
    },
  };
}

describe('SystemHealthPopover', () => {
  beforeEach(() => {
    apiMocks.get.mockReset().mockResolvedValue(healthResponse());
  });
  afterEach(() => { vi.useRealTimers(); });

  it('fetches layers only when opened and renders each state', async () => {
    render(<SystemHealthPopover />);

    expect(apiMocks.get).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: '系统健康分层' }));

    expect(await screen.findByText('API 进程')).toBeInTheDocument();
    expect(apiMocks.get).toHaveBeenCalledWith(
      '/api/v1/system/health-layers',
      { timeout: 10_000 },
    );
    expect(screen.getByText('OpenD 端口')).toBeInTheDocument();
    expect(screen.getByText('不可用')).toBeInTheDocument();
    expect(screen.getByText('尚无任何官方盘前发布')).toBeInTheDocument();
    // 后端新增未知层时按原名展示，不因缺少映射而崩溃或隐藏。
    expect(screen.getByText('future_layer_v2')).toBeInTheDocument();
  });

  it('shows a bounded error message when the endpoint fails', async () => {
    apiMocks.get.mockRejectedValue(new Error('boom'));
    render(<SystemHealthPopover />);

    fireEvent.click(screen.getByRole('button', { name: '系统健康分层' }));

    expect(await screen.findByText('健康状态读取失败，当前状态未知')).toBeInTheDocument();
  });

  it('closes when clicking outside', async () => {
    render(<SystemHealthPopover />);
    fireEvent.click(screen.getByRole('button', { name: '系统健康分层' }));
    await screen.findByText('API 进程');

    fireEvent.pointerDown(document.body);

    await waitFor(() => {
      expect(screen.queryByText('API 进程')).not.toBeInTheDocument();
    });
  });

  it('refreshes only while open and removes stale health after a failed refresh', async () => {
    vi.useFakeTimers();
    render(<SystemHealthPopover />);
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: '系统健康分层' }));
    });
    expect(screen.getByText('API 进程')).toBeInTheDocument();
    expect(screen.getByText(/检查于/)).toBeInTheDocument();
    apiMocks.get.mockRejectedValueOnce(new Error('offline'));

    await act(async () => { await vi.advanceTimersByTimeAsync(30_000); });

    expect(apiMocks.get).toHaveBeenCalledTimes(2);
    expect(screen.getByText('健康状态读取失败，当前状态未知')).toBeInTheDocument();
    expect(screen.queryByText('API 进程')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '系统健康分层' }));
    await act(async () => { await vi.advanceTimersByTimeAsync(60_000); });
    expect(apiMocks.get).toHaveBeenCalledTimes(2);
  });

  it('shows an ended shadow window without calling it a calibration pass', async () => {
    const response = healthResponse();
    response.data.layers.push({
      layer: 'intraday_p0_2m_shadow', state: 'ok',
      detail: '本轮观察窗口已结束；结束不表示已通过校准',
    });
    apiMocks.get.mockResolvedValue(response);
    render(<SystemHealthPopover />);
    fireEvent.click(screen.getByRole('button', { name: '系统健康分层' }));
    expect(await screen.findByText('20 日前瞻验证')).toBeInTheDocument();
    expect(screen.getByText('本轮观察窗口已结束；结束不表示已通过校准')).toBeInTheDocument();
  });
});
