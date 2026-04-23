import type React from 'react';
import { useState } from 'react';
import {
  Play,
  Plus,
  RefreshCw,
  TrendingUp,
  AlertTriangle,
  Info,
} from 'lucide-react';
import {
  Badge,
  Button,
  DataTable,
  EmptyState,
  IconButton,
  Input,
  Skeleton,
  Tabs,
  toast,
  type ColumnDef,
} from '../components/ui';
import {
  ChangeCell,
  MASlopeCell,
  PriceCell,
  Sparkline,
  StatBar,
} from '../components/data';

interface SampleRow {
  ticker: string;
  last: number;
  chgPct: number;
  volRatio: number;
  ma3: 'up' | 'flat' | 'down';
  ma5: 'up' | 'flat' | 'down';
  ma13: 'up' | 'flat' | 'down';
  sparkline: number[];
}

const SAMPLE: SampleRow[] = [
  { ticker: 'NVDA', last: 138.45, chgPct: 1.82, volRatio: 2.1, ma3: 'up', ma5: 'up', ma13: 'flat', sparkline: [10, 12, 11, 14, 13, 15, 17, 16, 18, 19] },
  { ticker: 'AAPL', last: 225.31, chgPct: -0.12, volRatio: 1.2, ma3: 'up', ma5: 'up', ma13: 'up', sparkline: [15, 14, 16, 15, 17, 16, 15, 14, 13, 15] },
  { ticker: 'META', last: 604.22, chgPct: -0.44, volRatio: 0.9, ma3: 'flat', ma5: 'up', ma13: 'up', sparkline: [20, 19, 18, 19, 17, 16, 18, 17, 16, 17] },
];

const columns: ColumnDef<SampleRow, unknown>[] = [
  { accessorKey: 'ticker', header: 'Ticker', size: 80 },
  {
    accessorKey: 'last',
    header: 'Last',
    size: 80,
    meta: { align: 'right' },
    cell: ({ getValue }) => <PriceCell value={getValue() as number} />,
  },
  {
    accessorKey: 'chgPct',
    header: 'Chg%',
    size: 80,
    meta: { align: 'right' },
    cell: ({ getValue }) => <ChangeCell value={getValue() as number} mode="percent" />,
  },
  {
    accessorKey: 'volRatio',
    header: 'Vol/Avg',
    size: 80,
    meta: { align: 'right' },
    cell: ({ getValue }) => <PriceCell value={getValue() as number} decimals={1} />,
  },
  {
    id: 'ma',
    header: 'MA 3/5/13',
    size: 80,
    cell: ({ row }) => (
      <MASlopeCell ma3={row.original.ma3} ma5={row.original.ma5} ma13={row.original.ma13} />
    ),
  },
  {
    id: 'sparkline',
    header: '30d',
    size: 100,
    cell: ({ row }) => <Sparkline data={row.original.sparkline} colorize />,
  },
];

const DesignLabPage: React.FC = () => {
  const [tab, setTab] = useState('primitives');
  const [pillDemo, setPillDemo] = useState('one');
  const [query, setQuery] = useState('');

  return (
    <div className="mx-auto max-w-6xl p-8">
      <div className="text-label uppercase text-text-3">Internal</div>
      <h1 className="text-h1 text-text-1">Design lab</h1>
      <p className="mt-1 text-body-sm text-text-3">
        Every primitive rendered once. Delete this route in Step 8 cleanup.
      </p>

      <div className="mt-6">
        <Tabs
          value={tab}
          onChange={setTab}
          items={[
            { value: 'primitives', label: 'Primitives' },
            { value: 'data', label: 'Data cells' },
            { value: 'table', label: 'Data table', count: SAMPLE.length },
            { value: 'states', label: 'States' },
          ]}
        />
      </div>

      {tab === 'primitives' && (
        <section className="mt-6 space-y-6">
          <Block title="Buttons">
            <div className="flex flex-wrap items-center gap-3">
              <Button variant="primary" iconLeft={Play}>Run analysis</Button>
              <Button variant="secondary">Cancel</Button>
              <Button variant="ghost" iconLeft={RefreshCw}>Refresh</Button>
              <Button variant="danger">Delete</Button>
              <Button variant="primary" size="sm">Small</Button>
              <Button variant="primary" loading>Loading</Button>
              <Button variant="primary" disabled>Disabled</Button>
              <IconButton icon={Plus} aria-label="Add" variant="secondary" />
              <IconButton icon={Plus} aria-label="Add" variant="ghost" />
            </div>
          </Block>

          <Block title="Inputs">
            <div className="flex flex-col gap-3 max-w-md">
              <Input value={query} onChange={setQuery} placeholder="Type something…" />
              <Input value={query} onChange={setQuery} placeholder="Small" size="sm" />
              <Input value="disabled" onChange={() => {}} disabled />
            </div>
          </Block>

          <Block title="Badges">
            <div className="flex flex-wrap items-center gap-2">
              <Badge variant="bullish">BUY · 76</Badge>
              <Badge variant="neutral">WATCH · 48</Badge>
              <Badge variant="bearish">SELL · 25</Badge>
              <Badge variant="warn">CAUTION</Badge>
              <Badge variant="accent">NEW</Badge>
              <Badge variant="bullish" size="md">LONG</Badge>
            </div>
          </Block>

          <Block title="Tabs (pills)">
            <Tabs
              value={pillDemo}
              onChange={setPillDemo}
              variant="pills"
              items={[
                { value: 'one', label: 'One' },
                { value: 'two', label: 'Two' },
                { value: 'three', label: 'Three' },
                { value: 'four', label: 'Four' },
              ]}
            />
          </Block>

          <Block title="Toast">
            <div className="flex gap-3">
              <Button variant="secondary" onClick={() => toast.success('Analysis complete')}>
                Success
              </Button>
              <Button variant="secondary" onClick={() => toast.error('Failed to fetch data')}>
                Error
              </Button>
              <Button variant="secondary" onClick={() => toast.info('New regime score: +42')}>
                Info
              </Button>
            </div>
          </Block>
        </section>
      )}

      {tab === 'data' && (
        <section className="mt-6 space-y-6">
          <Block title="PriceCell & ChangeCell">
            <div className="flex flex-wrap items-center gap-6 font-mono">
              <PriceCell value={708.72} />
              <PriceCell value={0.0012} decimals={4} />
              <PriceCell value={null} />
              <ChangeCell value={1.82} mode="percent" />
              <ChangeCell value={-0.12} mode="percent" />
              <ChangeCell value={-3.48} mode="absolute" showArrow />
              <ChangeCell value={0} mode="percent" />
            </div>
          </Block>

          <Block title="Sparkline">
            <div className="flex items-center gap-4">
              <Sparkline data={[1, 2, 3, 4, 3, 2, 4, 5, 6, 5]} />
              <Sparkline data={[6, 5, 4, 3, 4, 3, 2, 3, 2, 1]} colorize />
              <Sparkline data={[1, 2, 3, 4, 3, 2, 4, 5, 6, 5]} colorize showDot />
            </div>
          </Block>

          <Block title="StatBar">
            <StatBar
              items={[
                { label: 'SPY', value: '708.72', delta: '\u22120.12%', deltaPositive: false, sub: 'MA20 669.62' },
                { label: 'VIX', value: '18.87', delta: '\u22122.30%', deltaPositive: true, sub: '5d \u22124.1%' },
                { label: 'Breadth', value: '62%', sub: '>MA20' },
                { label: 'Premkt', value: '+0.08%', deltaPositive: true, sub: 'SPY fut' },
                { label: 'Updated', value: '09:10', sub: 'v1 \u00b7 ET' },
              ]}
            />
          </Block>

          <Block title="MASlopeCell">
            <div className="flex gap-4">
              <MASlopeCell ma3="up" ma5="up" ma13="up" />
              <MASlopeCell ma3="up" ma5="up" ma13="flat" />
              <MASlopeCell ma3="flat" ma5="down" ma13="down" />
              <MASlopeCell ma3="down" ma5="down" ma13="down" />
            </div>
          </Block>
        </section>
      )}

      {tab === 'table' && (
        <section className="mt-6">
          <DataTable
            data={SAMPLE}
            columns={columns}
            density="regular"
            stickyHeader
            getRowId={(r) => r.ticker}
            onRowClick={(r) => toast.info(`Open ${r.ticker}`)}
          />
        </section>
      )}

      {tab === 'states' && (
        <section className="mt-6 space-y-6">
          <Block title="Skeletons">
            <div className="space-y-2 max-w-md">
              <Skeleton width="100%" height={20} />
              <Skeleton width="60%" height={16} />
              <Skeleton width={120} height={80} variant="rect" />
            </div>
          </Block>

          <Block title="Empty state">
            <EmptyState
              icon={TrendingUp}
              title="No breakout signals in scope."
              description="Adjust filters or wait for the next scan."
              action={{ label: 'Rescan', onClick: () => toast.info('Rescan queued') }}
            />
          </Block>

          <Block title="Inline error">
            <div className="max-w-md rounded-ds-sm border-l-2 border-l-down-strong bg-down-subtle px-3 py-2">
              <div className="flex items-center gap-2 text-body text-down-strong">
                <AlertTriangle size={14} strokeWidth={1.5} />
                Failed to load watchlist data
              </div>
              <div className="mt-1 text-body-sm text-text-2">
                yfinance returned 429. Try again in 5 min.
              </div>
            </div>
          </Block>

          <Block title="Info banner">
            <div className="inline-flex items-center gap-2 rounded-ds-sm border border-[color:var(--accent-subtle-border)] bg-[color:var(--accent-subtle-bg)] px-3 py-2 text-body-sm text-accent">
              <Info size={14} strokeWidth={1.5} />
              New regime score: +42
            </div>
          </Block>
        </section>
      )}
    </div>
  );
};

const Block: React.FC<{ title: string; children: React.ReactNode }> = ({ title, children }) => (
  <div>
    <div className="mb-2 text-label uppercase text-text-3">{title}</div>
    {children}
  </div>
);

export default DesignLabPage;
