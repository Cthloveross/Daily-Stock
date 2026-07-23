import { describe, expect, it } from 'vitest';
import { parseTradingViewWatchlist } from '../tradingViewWatchlist';

describe('parseTradingViewWatchlist', () => {
  it('parses exchange-qualified symbols, sections and duplicates', () => {
    expect(
      parseTradingViewWatchlist(
        '\uFEFF###MEGA CAP,NASDAQ:AAPL,NYSE:TSLA\nNASDAQ:NVDA,NASDAQ:AAPL',
      ),
    ).toEqual({
      symbols: ['AAPL', 'TSLA', 'NVDA'],
      ignored: [],
    });
  });

  it('keeps supported class tickers and reports unsupported assets', () => {
    expect(
      parseTradingViewWatchlist('NYSE:BRK.B,CME_MINI:ES1!,BINANCE:BTCUSDT,NASDAQ:GOOGL'),
    ).toEqual({
      symbols: ['BRK.B', 'GOOGL'],
      ignored: ['CME_MINI:ES1!', 'BINANCE:BTCUSDT'],
    });
  });
});
