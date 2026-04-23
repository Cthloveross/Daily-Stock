import type React from 'react';
import { useEffect, useState } from 'react';
import {
  flexRender,
  getCoreRowModel,
  getSortedRowModel,
  useReactTable,
  type ColumnDef,
  type SortingState,
  type Row,
} from '@tanstack/react-table';
import { ChevronDown, ChevronUp } from 'lucide-react';
import { cn } from '../../utils/cn';
import { EmptyState } from './EmptyState';
import { Skeleton } from './Skeleton';

export type { ColumnDef } from '@tanstack/react-table';

export interface DataTableProps<T> {
  data: T[];
  columns: ColumnDef<T, unknown>[];
  density?: 'compact' | 'regular';
  selection?: 'none' | 'single' | 'multiple';
  onRowClick?: (row: T) => void;
  sortable?: boolean;
  stickyHeader?: boolean;
  emptyState?: React.ReactNode;
  loading?: boolean;
  className?: string;
  getRowId?: (row: T, index: number) => string;
}

export function DataTable<T>({
  data,
  columns,
  density = 'regular',
  onRowClick,
  sortable = true,
  stickyHeader,
  emptyState,
  loading,
  className,
  getRowId,
}: DataTableProps<T>) {
  const [sorting, setSorting] = useState<SortingState>([]);
  const [selected, setSelected] = useState<string | null>(null);

  useEffect(() => {
    setSelected(null);
  }, [data]);

  const table = useReactTable({
    data,
    columns,
    state: { sorting },
    onSortingChange: setSorting,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: sortable ? getSortedRowModel() : undefined,
    getRowId: getRowId ? (row, idx) => getRowId(row, idx) : undefined,
  });

  const rowH = density === 'compact' ? 'h-7' : 'h-9';
  const headerH = density === 'compact' ? 'h-7' : 'h-8';
  const textSize = density === 'compact' ? 'text-mono-xs' : 'text-mono-sm';

  const rows = table.getRowModel().rows;

  const handleRowClick = (row: Row<T>) => {
    setSelected(row.id);
    onRowClick?.(row.original);
  };

  if (loading) {
    return (
      <div className={cn('flex flex-col gap-2 p-2', className)}>
        {Array.from({ length: 8 }).map((_, i) => (
          <Skeleton key={i} height={24} width="100%" />
        ))}
      </div>
    );
  }

  if (rows.length === 0) {
    return (
      <div className={className}>
        {emptyState ?? <EmptyState title="No data." size="md" />}
      </div>
    );
  }

  return (
    <div className={cn('w-full overflow-x-auto', className)}>
      <table className="w-full border-collapse font-sans">
        <thead
          className={cn(stickyHeader && 'sticky top-0 z-sticky bg-bg-1')}
        >
          {table.getHeaderGroups().map((hg) => (
            <tr key={hg.id} className={cn('border-b border-subtle', headerH)}>
              {hg.headers.map((h) => {
                const canSort = sortable && h.column.getCanSort();
                const sorted = h.column.getIsSorted();
                const align = (h.column.columnDef.meta as { align?: string } | undefined)?.align;
                return (
                  <th
                    key={h.id}
                    style={{ width: h.getSize() !== 150 ? h.getSize() : undefined }}
                    onClick={canSort ? h.column.getToggleSortingHandler() : undefined}
                    className={cn(
                      'px-3 first:pl-4 last:pr-4 text-label uppercase text-text-3',
                      align === 'right' && 'text-right',
                      align === 'center' && 'text-center',
                      !align && 'text-left',
                      canSort && 'cursor-pointer select-none',
                    )}
                  >
                    <span
                      className={cn(
                        'inline-flex items-center gap-1',
                        align === 'right' && 'justify-end',
                      )}
                    >
                      {flexRender(h.column.columnDef.header, h.getContext())}
                      {sorted === 'asc' && <ChevronUp size={10} className="text-accent" />}
                      {sorted === 'desc' && <ChevronDown size={10} className="text-accent" />}
                    </span>
                  </th>
                );
              })}
            </tr>
          ))}
        </thead>
        <tbody>
          {rows.map((row) => {
            const isSelected = selected === row.id;
            return (
              <tr
                key={row.id}
                onClick={() => handleRowClick(row)}
                className={cn(
                  rowH,
                  'border-b border-subtle last:border-b-0 transition-colors',
                  onRowClick && 'cursor-pointer hover:bg-bg-2',
                  isSelected && 'bg-[color:var(--accent-subtle-bg)] border-l-2 border-l-[color:var(--accent)]',
                )}
              >
                {row.getVisibleCells().map((cell) => {
                  const align = (cell.column.columnDef.meta as { align?: string } | undefined)?.align;
                  return (
                    <td
                      key={cell.id}
                      className={cn(
                        'px-3 first:pl-4 last:pr-4 text-text-1',
                        textSize,
                        align === 'right' && 'text-right',
                        align === 'center' && 'text-center',
                      )}
                    >
                      {flexRender(cell.column.columnDef.cell, cell.getContext())}
                    </td>
                  );
                })}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

export default DataTable;
