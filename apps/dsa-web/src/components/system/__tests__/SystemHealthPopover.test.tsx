import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
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

    expect(await screen.findByText('健康状态读取失败')).toBeInTheDocument();
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
});
