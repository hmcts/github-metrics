'use client';

import clsx from 'clsx';
import { ChevronDown, ChevronUp } from 'lucide-react';
import type { Direction } from '@/lib/sort';

/**
 * A sortable column header: a real `<button>` inside the `<th>`, not a click handler on the cell.
 *
 * The predecessor made the whole `<th>` clickable, which is unreachable by keyboard and announces as
 * a plain column header. `aria-sort` stays on the `<th>` — it is a property of the column, and a
 * screen reader looks for it on the header cell, not on the control inside — while the button is
 * what takes focus and the Enter key.
 *
 * The chevron is decoration: `aria-sort` already states the direction, so announcing the icon too
 * would say it twice.
 */
export function SortHeader({
  label,
  active,
  direction,
  onSort,
  numeric,
}: {
  label: string;
  active: boolean;
  direction: Direction;
  onSort: () => void;
  /** Right-align, as every numeric column on the site is. */
  numeric?: boolean;
}) {
  const Chevron = direction === 'ascending' ? ChevronUp : ChevronDown;
  return (
    <th
      scope="col"
      aria-sort={active ? direction : 'none'}
      className={clsx('py-2 font-medium', numeric ? 'text-right' : 'text-left')}
    >
      <button
        type="button"
        onClick={onSort}
        className={clsx(
          'inline-flex items-center gap-1 rounded transition-colors hover:text-slate-200',
          'focus:outline-none focus-visible:ring-1 focus-visible:ring-indigo-500',
          active ? 'text-slate-200' : null,
        )}
      >
        {label}
        {active ? <Chevron className="w-3 h-3" aria-hidden="true" /> : null}
      </button>
    </th>
  );
}
