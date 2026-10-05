"use client";

import { MESSAGES } from "@/lib/failures";
import {
  Check,
  CircleX,
  Info,
  LoaderCircle,
  TriangleAlert,
  X,
  type LucideIcon,
} from "lucide-react";
import {
  useEffect,
  type ReactNode,
  type SelectHTMLAttributes,
  type InputHTMLAttributes,
} from "react";

/**
 * Every screen in the product is built from this file.
 *
 * Which is the point: the teacher's pages were redesigned and the nineteen
 * administrative screens were not, and they still look like one product,
 * because the redesign happened here and their call sites never changed. No
 * prop below was removed or narrowed for that reason - `Badge` still answers
 * to "slate" and "violet", `Column` gained only optional fields.
 *
 * Colour comes from the tokens in `globals.css` and never from a literal, so
 * "what does a blocker look like" has one answer and the accent can be
 * changed in a line.
 */

/** The vocabulary the whole app judges things in. */
export type Tone = "neutral" | "ok" | "caution" | "blocker" | "info" | "accent";

const TONE_ICON: Record<"ok" | "caution" | "blocker" | "info", LucideIcon> = {
  ok: Check,
  caution: TriangleAlert,
  blocker: CircleX,
  info: Info,
};

/* --------------------------------------------------------------- primitives */

export function Button({
  children,
  variant = "primary",
  size = "md",
  className = "",
  ...props
}: {
  children: ReactNode;
  variant?: "primary" | "secondary" | "danger" | "ghost";
  /** `lg` is the 44px touch target a phone needs for a primary action; `sm`
   *  keeps a row of table actions from growing. */
  size?: "sm" | "md" | "lg";
} & React.ButtonHTMLAttributes<HTMLButtonElement>) {
  const styles = {
    primary: "bg-accent text-accent-ink hover:bg-accent-hover disabled:bg-ink-faint",
    secondary:
      "bg-surface text-ink-soft border border-line-strong hover:bg-surface-hover disabled:opacity-50",
    danger: "bg-surface text-blocker border border-blocker-line hover:bg-blocker-surface disabled:opacity-50",
    ghost: "text-ink-muted hover:text-ink hover:bg-surface-hover disabled:opacity-50",
  }[variant];

  const sizes = {
    sm: "h-8 px-2.5 text-xs",
    md: "h-9 px-3 text-sm",
    lg: "h-11 px-4 text-sm",
  }[size];

  return (
    <button
      {...props}
      className={`inline-flex items-center justify-center gap-1.5 rounded-md font-medium transition-colors disabled:cursor-not-allowed ${sizes} ${styles} ${className}`}
    >
      {children}
    </button>
  );
}

export function Input({
  label,
  hint,
  trailing,
  className = "",
  ...props
}: {
  label?: string;
  hint?: string;
  /** A control inside the right edge of the field, such as show-password. */
  trailing?: ReactNode;
} & InputHTMLAttributes<HTMLInputElement>) {
  const field = (
    <input
      {...props}
      className={`h-9 w-full rounded-md border border-line-strong bg-surface px-3 text-sm text-ink outline-none placeholder:text-ink-faint focus:border-accent ${className}`}
    />
  );
  return (
    <label className="block">
      {label && (
        <span className="mb-1 block text-xs font-medium text-ink-soft">{label}</span>
      )}
      {trailing ? (
        <span className="relative block">
          {field}
          <span className="absolute inset-y-0 right-0 flex items-center">{trailing}</span>
        </span>
      ) : (
        field
      )}
      {hint && <span className="mt-1 block text-xs text-ink-faint">{hint}</span>}
    </label>
  );
}

export function Select({
  label,
  hint,
  children,
  className = "",
  ...props
}: {
  label?: string;
  hint?: string;
  children: ReactNode;
} & SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <label className="block">
      {label && (
        <span className="mb-1 block text-xs font-medium text-ink-soft">{label}</span>
      )}
      <select
        {...props}
        className={`h-9 w-full rounded-md border border-line-strong bg-surface px-3 text-sm text-ink outline-none focus:border-accent ${className}`}
      >
        {children}
      </select>
      {hint && <span className="mt-1 block text-xs text-ink-faint">{hint}</span>}
    </label>
  );
}

export function Badge({
  children,
  tone = "slate",
}: {
  children: ReactNode;
  // violet marks a subject that is taught as both a lecture and a
  // practical - two obligations, scheduled separately, in different rooms.
  // The colour names are the older vocabulary and still answer, so sixteen
  // files did not have to change; new code should say what it means instead.
  tone?:
    | "slate"
    | "blue"
    | "amber"
    | "green"
    | "red"
    | "violet"
    | "neutral"
    | "info"
    | "caution"
    | "ok"
    | "blocker"
    | "accent";
}) {
  const tones = {
    slate: "bg-surface-hover text-ink-soft",
    neutral: "bg-surface-hover text-ink-soft",
    blue: "bg-info-surface text-info",
    info: "bg-info-surface text-info",
    amber: "bg-caution-surface text-caution",
    caution: "bg-caution-surface text-caution",
    green: "bg-ok-surface text-ok",
    ok: "bg-ok-surface text-ok",
    red: "bg-blocker-surface text-blocker",
    blocker: "bg-blocker-surface text-blocker",
    accent: "bg-accent-surface text-accent",
    violet: "bg-violet-50 text-violet-700",
  }[tone];
  return (
    <span
      className={`inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-xs font-medium ${tones}`}
    >
      {children}
    </span>
  );
}

/** A badge that says its state in words and marks it with an icon, so the
 *  meaning survives a reader who cannot see the colour. */
export function StatusBadge({
  tone,
  children,
  icon,
}: {
  tone: Tone;
  children: ReactNode;
  icon?: LucideIcon;
}) {
  const fallback =
    tone === "neutral" || tone === "accent" ? undefined : TONE_ICON[tone];
  const Icon = icon ?? fallback;
  return (
    <Badge tone={tone}>
      {Icon && <Icon aria-hidden="true" size={12} strokeWidth={2.5} />}
      {children}
    </Badge>
  );
}

/* ------------------------------------------------------------ page feedback */

/**
 * One message about one thing.
 *
 * `role` follows the tone rather than the caller: a blocker interrupts a
 * screen reader, everything else waits its turn. That mapping is the whole
 * reason this component exists - it is what stops a passing server error from
 * being announced, and drawn, as though the data itself were wrong.
 */
export function Alert({
  tone,
  title,
  children,
  action,
  live,
  onDismiss,
}: {
  tone: "ok" | "caution" | "blocker" | "info";
  title: ReactNode;
  children?: ReactNode;
  action?: ReactNode;
  live?: "alert" | "status" | "off";
  onDismiss?: () => void;
}) {
  const styles = {
    ok: "border-ok-line bg-ok-surface text-ok",
    caution: "border-caution-line bg-caution-surface text-caution",
    blocker: "border-blocker-line bg-blocker-surface text-blocker",
    info: "border-info-line bg-info-surface text-info",
  }[tone];
  const Icon = TONE_ICON[tone];
  const role = live ?? (tone === "blocker" ? "alert" : "status");

  return (
    <div
      role={role === "off" ? undefined : role}
      className={`flex items-start gap-2.5 rounded-md border px-3 py-2.5 text-sm ${styles}`}
    >
      <Icon aria-hidden="true" size={16} className="mt-0.5 shrink-0" />
      <div className="min-w-0 flex-1">
        <div className="font-medium">{title}</div>
        {children && <div className="mt-1 text-ink-soft">{children}</div>}
        {action && <div className="mt-2 flex flex-wrap gap-2">{action}</div>}
      </div>
      {onDismiss && (
        <button
          onClick={onDismiss}
          aria-label="Dismiss"
          className="shrink-0 opacity-60 hover:opacity-100"
        >
          <X aria-hidden="true" size={16} />
        </button>
      )}
    </div>
  );
}

const NOTICED = new Set<string>([MESSAGES.unreachable, MESSAGES.server, MESSAGES.session]);

export function ErrorBanner({
  error,
  onDismiss,
}: {
  error: string | null;
  onDismiss?: () => void;
}) {
  // Connection, server and session trouble is already in the notice panel;
  // saying it twice on one screen reads as two problems.
  if (!error || NOTICED.has(error)) return null;
  return (
    <div className="mb-4">
      <Alert tone="blocker" title={error} onDismiss={onDismiss} />
    </div>
  );
}

export function EmptyState({
  children,
  title,
  icon: Icon,
  action,
  variant = "table",
}: {
  children?: ReactNode;
  title?: string;
  icon?: LucideIcon;
  action?: ReactNode;
  variant?: "page" | "table" | "panel";
}) {
  const pad = { page: "px-6 py-16", table: "px-4 py-10", panel: "px-4 py-6" }[variant];
  return (
    <div
      className={`rounded-md border border-dashed border-line-strong text-center text-sm text-ink-muted ${pad}`}
    >
      {Icon && <Icon aria-hidden="true" size={24} className="mx-auto mb-3 text-ink-faint" />}
      {title && <div className="mb-1 font-medium text-ink">{title}</div>}
      {children}
      {action && <div className="mt-4 flex justify-center gap-2">{action}</div>}
    </div>
  );
}

/** A whole region that could not load. */
export function ErrorState({
  title,
  detail,
  onRetry,
  retryLabel = "Try again",
}: {
  title: string;
  detail?: ReactNode;
  onRetry?: () => void;
  retryLabel?: string;
}) {
  return (
    <Alert
      tone="blocker"
      title={title}
      action={
        onRetry && (
          <Button variant="secondary" size="sm" onClick={onRetry}>
            {retryLabel}
          </Button>
        )
      }
    >
      {detail}
    </Alert>
  );
}

/** A region that is loading, said out loud. */
export function LoadingState({ label, rows = 3 }: { label: string; rows?: number }) {
  return (
    <div role="status" aria-live="polite">
      <span className="sr-only">{label}</span>
      <Skeleton rows={rows} />
    </div>
  );
}

export function PageHeader({
  title,
  description,
  actions,
}: {
  title: string;
  description?: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <div className="mb-6 flex flex-wrap items-start justify-between gap-4">
      <div className="min-w-0">
        <h1 className="text-xl font-semibold tracking-tight text-ink sm:text-2xl">
          {title}
        </h1>
        {description && <p className="mt-1 text-sm text-ink-muted">{description}</p>}
      </div>
      {actions && <div className="flex shrink-0 flex-wrap gap-2">{actions}</div>}
    </div>
  );
}

/** A heading inside a page. `PageHeader` owns the h1; this is everything under it. */
export function SectionHeader({
  title,
  description,
  actions,
  level = 2,
}: {
  title: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
  level?: 2 | 3;
}) {
  const Heading = level === 2 ? "h2" : "h3";
  return (
    <div className="mb-3 flex flex-wrap items-end justify-between gap-3">
      <div className="min-w-0">
        <Heading className="text-sm font-semibold text-ink">{title}</Heading>
        {description && <p className="mt-0.5 text-xs text-ink-muted">{description}</p>}
      </div>
      {actions && <div className="flex shrink-0 flex-wrap gap-2">{actions}</div>}
    </div>
  );
}

/* ------------------------------------------------------------------ numbers */

/**
 * One number with its name.
 *
 * `role="group"` with the label as its accessible name is what lets a test -
 * and a screen reader - ask for "Classes" and get the figure, instead of
 * hunting for the string "36" somewhere on the page.
 */
export function Metric({
  label,
  value,
  hint,
  tone = "neutral",
}: {
  label: string;
  value: ReactNode;
  hint?: string;
  tone?: "neutral" | "ok" | "blocker";
}) {
  const colour = { neutral: "text-ink", ok: "text-ok", blocker: "text-blocker" }[tone];
  return (
    <div role="group" aria-label={label} className="min-w-0">
      <div className={`text-2xl font-semibold tabular-nums tracking-tight ${colour}`}>
        {value}
      </div>
      <div className="mt-0.5 truncate text-xs font-medium text-ink-soft">{label}</div>
      {hint && <div className="truncate text-[11px] text-ink-faint">{hint}</div>}
    </div>
  );
}

/** Metrics in a row, separated by space rather than boxed in cards. */
export function MetricRow({
  children,
  columns = 4,
}: {
  children: ReactNode;
  columns?: 2 | 3 | 4 | 5 | 6 | 7;
}) {
  const cols = {
    2: "grid-cols-2",
    3: "grid-cols-2 sm:grid-cols-3",
    4: "grid-cols-2 sm:grid-cols-4",
    5: "grid-cols-2 sm:grid-cols-3 lg:grid-cols-5",
    6: "grid-cols-2 sm:grid-cols-3 lg:grid-cols-6",
    7: "grid-cols-2 sm:grid-cols-4 lg:grid-cols-7",
  }[columns];
  return <div className={`grid gap-x-4 gap-y-5 ${cols}`}>{children}</div>;
}

/* -------------------------------------------------------------------- table */

export interface Column<T> {
  /** Usually a string. A node when the header is interactive - a sort
   *  control, say - in which case give the column a `key` as well. */
  header: ReactNode;
  cell: (row: T) => ReactNode;
  className?: string;
  /** Stable identity for React. Defaults to the header when it is text. */
  key?: string;
  /** Pin this column to the left edge while the rest scrolls. At most one. */
  sticky?: boolean;
  align?: "left" | "right";
}

export function DataTable<T extends { id: number }>({
  rows,
  columns,
  empty,
  minWidth,
  scroll = "auto",
}: {
  rows: T[];
  columns: Column<T>[];
  empty: ReactNode;
  /** Below this width the table scrolls instead of crushing its columns. */
  minWidth?: string;
  /** `bleed` lets the scroll region reach the screen edge on a phone. */
  scroll?: "auto" | "bleed";
}) {
  if (rows.length === 0) return <EmptyState>{empty}</EmptyState>;

  // `border-separate` matters: with the default collapsed borders the browser
  // hands border painting to the table and a pinned cell loses its edge while
  // scrolling, so the pinned column's right edge is an inset shadow instead.
  const pinned = (c: Column<T>, head: boolean) =>
    c.sticky
      ? `sticky left-0 ${
          head ? "z-20 bg-surface-sunken" : "z-10 bg-surface"
        } shadow-[inset_-1px_0_0_var(--color-line)]`
      : "";

  return (
    <div
      data-scroll-region
      className={`relative overflow-x-auto overscroll-x-contain ${
        scroll === "bleed"
          ? "-mx-4 sm:mx-0 sm:rounded-md sm:border sm:border-line"
          : "rounded-md border border-line"
      }`}
    >
      <table
        className="w-full border-separate border-spacing-0 text-sm"
        style={minWidth ? { minWidth } : undefined}
      >
        <thead className="text-left text-xs uppercase tracking-wide text-ink-muted">
          <tr>
            {columns.map((c, i) => (
              <th
                key={c.key ?? (typeof c.header === "string" ? c.header : i)}
                scope="col"
                className={`border-b border-line bg-surface-sunken px-3 py-2 font-medium ${
                  c.align === "right" ? "text-right" : ""
                } ${pinned(c, true)} ${c.className ?? ""}`}
              >
                {c.header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.id} className="group">
              {columns.map((c, i) => (
                <td
                  key={c.key ?? (typeof c.header === "string" ? c.header : i)}
                  className={`border-b border-line px-3 py-2 align-middle group-hover:bg-surface-sunken ${
                    c.align === "right" ? "text-right" : ""
                  } ${pinned(c, false)} ${c.className ?? ""}`}
                >
                  {c.cell(row)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/* ------------------------------------------------------------------- layout */

export function Card({
  title,
  actions,
  children,
  variant = "outline",
}: {
  title?: string;
  actions?: ReactNode;
  children: ReactNode;
  /** `plain` groups without drawing a box - the way to keep a page from
   *  turning into a wall of cards. */
  variant?: "outline" | "plain";
}) {
  const box = variant === "outline" ? "rounded-md border border-line bg-surface p-4" : "";
  return (
    <div className={box}>
      {(title || actions) && (
        <div className="mb-3 flex items-center justify-between gap-3">
          {title && <h2 className="text-sm font-semibold text-ink">{title}</h2>}
          {actions}
        </div>
      )}
      {children}
    </div>
  );
}

/** A right-hand detail panel. Used for class details + manual edits, where a
 *  full page navigation would lose the timetable context behind it. */
export function Drawer({
  open,
  onClose,
  title,
  subtitle,
  children,
}: {
  open: boolean;
  onClose: () => void;
  title: ReactNode;
  subtitle?: ReactNode;
  children: ReactNode;
}) {
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 flex justify-end">
      <button aria-label="Close panel" onClick={onClose} className="fade-enter flex-1 bg-ink/20" />
      <div
        role="dialog"
        aria-modal="true"
        className="drawer-enter flex h-full w-full max-w-md flex-col overflow-y-auto border-l border-line bg-surface shadow-xl"
      >
        <div className="flex items-start justify-between gap-3 border-b border-line px-5 py-4">
          <div>
            <div className="text-base font-semibold text-ink">{title}</div>
            {subtitle && <div className="mt-0.5 text-sm text-ink-muted">{subtitle}</div>}
          </div>
          <button
            onClick={onClose}
            aria-label="Close"
            className="rounded p-1 text-ink-faint hover:bg-surface-hover hover:text-ink-soft"
          >
            <X aria-hidden="true" size={16} />
          </button>
        </div>
        <div className="flex-1 px-5 py-4">{children}</div>
      </div>
    </div>
  );
}

/** Loading placeholder that keeps layout height stable. */
export function Skeleton({ rows = 3 }: { rows?: number }) {
  return (
    <div className="space-y-2" aria-busy="true" aria-live="polite">
      {Array.from({ length: rows }).map((_, i) => (
        <div key={i} className="h-8 animate-pulse rounded bg-surface-hover" />
      ))}
    </div>
  );
}

/**
 * What the machine is doing, one line per step.
 *
 * Every step here is a real event - a request that resolved, a poll that came
 * back terminal. There is no timer pretending to be progress, because a
 * progress bar that is not measuring anything is a lie told politely.
 */
export function StageChecklist({
  stages,
  caption,
}: {
  stages: {
    id: string;
    label: string;
    status: "pending" | "active" | "done" | "failed";
  }[];
  caption?: string;
}) {
  return (
    <ol className="space-y-2">
      {stages.map((s) => {
        const mark =
          s.status === "done" ? (
            <Check aria-hidden="true" size={14} className="text-ok" />
          ) : s.status === "active" ? (
            <LoaderCircle
              aria-hidden="true"
              size={14}
              className="animate-spin text-accent"
            />
          ) : s.status === "failed" ? (
            <CircleX aria-hidden="true" size={14} className="text-blocker" />
          ) : (
            <span className="h-1.5 w-1.5 rounded-full bg-ink-faint" aria-hidden="true" />
          );
        const text =
          s.status === "pending"
            ? "text-ink-faint"
            : s.status === "active"
              ? "font-medium text-ink"
              : "text-ink-soft";
        const said = {
          done: " - done",
          active: " - in progress",
          failed: " - failed",
          pending: " - waiting",
        }[s.status];
        return (
          <li
            key={s.id}
            aria-current={s.status === "active" ? "step" : undefined}
            className={`flex items-center gap-2.5 text-sm ${text}`}
          >
            <span className="flex h-4 w-4 items-center justify-center">{mark}</span>
            {s.label}
            <span className="sr-only">{said}</span>
          </li>
        );
      })}
      {caption && <li className="pt-1 text-xs text-ink-faint">{caption}</li>}
    </ol>
  );
}

export function Stat({
  label,
  value,
  hint,
  href,
}: {
  label: string;
  value: ReactNode;
  hint?: string;
  href?: string;
}) {
  const inner = (
    <>
      <div className="text-2xl font-semibold tabular-nums text-ink">{value}</div>
      <div className="text-xs font-medium text-ink-soft">{label}</div>
      {hint && <div className="mt-0.5 text-[11px] text-ink-faint">{hint}</div>}
    </>
  );
  const className =
    "block rounded-md border border-line bg-surface px-3 py-3 transition-colors";
  if (!href) return <div className={className}>{inner}</div>;
  return (
    <a href={href} className={`${className} hover:border-line-strong`}>
      {inner}
    </a>
  );
}

/** Prominent occupied/free/inactive banner for the room and faculty detail
 *  pages - the spec asks it to be "immediately obvious", so this is a full-
 *  width colored block, not a small badge easy to miss. */
export function StatusBanner({
  state,
  detail,
}: {
  state: "occupied" | "free" | "inactive" | "unknown";
  detail: string;
}) {
  const styles = {
    occupied: "border-caution-line bg-caution-surface text-caution",
    free: "border-ok-line bg-ok-surface text-ok",
    inactive: "border-line bg-surface-hover text-ink-soft",
    unknown: "border-line bg-surface-sunken text-ink-muted",
  }[state];
  const label = {
    occupied: "Occupied right now",
    free: "Available right now",
    inactive: "Inactive",
    unknown: "Status unknown",
  }[state];
  const dot = {
    occupied: "bg-caution",
    free: "bg-ok",
    inactive: "bg-ink-faint",
    unknown: "bg-line-strong",
  }[state];

  return (
    <div className={`flex items-center gap-3 rounded-md border px-4 py-3 ${styles}`}>
      <span className={`h-2.5 w-2.5 shrink-0 rounded-full ${dot}`} />
      <div>
        <div className="text-sm font-semibold">{label}</div>
        <div className="text-xs opacity-80">{detail}</div>
      </div>
    </div>
  );
}

/** Two-column key/value list for detail panels. */
export function DetailList({ items }: { items: [string, ReactNode][] }) {
  return (
    <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-2 text-sm">
      {items.map(([k, v]) => (
        <div key={k} className="contents">
          <dt className="text-ink-muted">{k}</dt>
          <dd className="text-ink">{v}</dd>
        </div>
      ))}
    </dl>
  );
}
