"use client";

import { CircleAlert, Info, WifiOff, X } from "lucide-react";
import Link from "next/link";
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { onApiTrouble } from "./api";
import type { FailureKind } from "./failures";

/**
 * Notices: what went wrong with a request, said once, the same way, on every
 * page.
 *
 * Page-local messages stay where they are - a row that cannot be imported
 * belongs next to the file, and a failed Generate next to its button. What
 * comes here is what no single page owns: the server asleep or restarting, the
 * server failing, the sign-in expiring, a download that did not arrive. The
 * API client reports the first three by itself; `notify` is for the rest.
 *
 * An error stays until it is dismissed, because a message that disappears
 * while someone is reading it is worse than none. Information goes by itself.
 */

export type NoticeTone = "error" | "info";

export type Notice = {
  id: number;
  tone: NoticeTone;
  title: string;
  detail?: string;
  /** What the kind of trouble is, so repeats of one problem collapse into one. */
  key?: string;
  action?: { label: string; href: string };
};

export type NoticeInput = Omit<Notice, "id">;

type NoticesState = {
  notices: Notice[];
  notify: (notice: NoticeInput) => void;
  dismiss: (id: number) => void;
};

const Ctx = createContext<NoticesState | null>(null);

/** How long an informational notice stays. Errors stay until dismissed. */
export const INFO_MS = 6000;

const TITLES: Record<Exclude<FailureKind, "request" | "busy">, string> = {
  unreachable: "Can't reach the server",
  server: "The server had a problem",
  session: "Signed out",
};

export function NoticesProvider({ children }: { children: ReactNode }) {
  const [notices, setNotices] = useState<Notice[]>([]);
  const next = useRef(1);
  const timers = useRef(new Map<number, ReturnType<typeof setTimeout>>());

  const dismiss = useCallback((id: number) => {
    setNotices((all) => all.filter((n) => n.id !== id));
    const timer = timers.current.get(id);
    if (timer) clearTimeout(timer);
    timers.current.delete(id);
  }, []);

  const notify = useCallback(
    (input: NoticeInput) => {
      const id = next.current++;
      setNotices((all) => {
        // The same trouble again replaces the old notice rather than stacking:
        // six failed polls of a sleeping server are one problem.
        const kept = input.key ? all.filter((n) => n.key !== input.key) : all;
        return [...kept, { ...input, id }].slice(-4);
      });
      if (input.tone === "info") {
        timers.current.set(
          id,
          setTimeout(() => dismiss(id), INFO_MS),
        );
      }
    },
    [dismiss],
  );

  useEffect(
    () =>
      onApiTrouble((failure) => {
        if (failure.kind === "request" || failure.kind === "busy") return;
        notify({
          tone: "error",
          key: failure.kind,
          title: TITLES[failure.kind],
          detail: failure.message,
          action: failure.kind === "session" ? { label: "Sign in", href: "/login" } : undefined,
        });
      }),
    [notify],
  );

  useEffect(() => {
    const all = timers.current;
    return () => {
      for (const timer of all.values()) clearTimeout(timer);
    };
  }, []);

  const value = useMemo(() => ({ notices, notify, dismiss }), [notices, notify, dismiss]);
  return (
    <Ctx.Provider value={value}>
      {children}
      <NoticePanel notices={notices} onDismiss={dismiss} />
    </Ctx.Provider>
  );
}

export function useNotices(): Pick<NoticesState, "notify" | "dismiss"> {
  const value = useContext(Ctx);
  // Outside the provider (a unit test rendering one component) notices go
  // nowhere rather than throwing: they are never the only way something is said.
  return value ?? NO_NOTICES;
}

const NO_NOTICES = { notify: () => {}, dismiss: () => {} };

/**
 * The panel: top right on a computer, above the bottom bar on a phone.
 *
 * Focus is never moved to it - someone typing should not lose their place -
 * and an error is announced assertively, information politely.
 */
export function NoticePanel({
  notices,
  onDismiss,
}: {
  notices: Notice[];
  onDismiss: (id: number) => void;
}) {
  return (
    <div
      aria-label="Notifications"
      className="pointer-events-none fixed inset-x-3 bottom-[calc(var(--spacing-bottomnav)+0.75rem)] z-50 flex flex-col gap-2 md:inset-x-auto md:bottom-auto md:right-4 md:top-[calc(var(--spacing-topbar)+0.75rem)] md:w-96"
    >
      {notices.map((n) => {
        const error = n.tone === "error";
        const Icon = !error ? Info : n.key === "unreachable" ? WifiOff : CircleAlert;
        return (
          <div
            key={n.id}
            role={error ? "alert" : "status"}
            className={`notice-enter pointer-events-auto flex items-start gap-3 rounded-md border bg-surface p-3 text-sm shadow-lg ${
              error ? "border-blocker-line" : "border-line"
            }`}
          >
            <Icon
              aria-hidden="true"
              size={18}
              className={`mt-0.5 shrink-0 ${error ? "text-blocker" : "text-info"}`}
            />
            <div className="min-w-0 flex-1">
              <p className="font-medium text-ink">{n.title}</p>
              {n.detail && <p className="mt-0.5 text-ink-soft">{n.detail}</p>}
              {n.action && (
                <Link
                  href={n.action.href}
                  onClick={() => onDismiss(n.id)}
                  className="mt-2 inline-flex font-medium text-accent underline-offset-2 hover:underline"
                >
                  {n.action.label}
                </Link>
              )}
            </div>
            <button
              type="button"
              onClick={() => onDismiss(n.id)}
              aria-label="Dismiss notification"
              className="-m-1 flex h-8 w-8 shrink-0 items-center justify-center rounded-md text-ink-muted hover:bg-surface-hover hover:text-ink"
            >
              <X aria-hidden="true" size={16} />
            </button>
          </div>
        );
      })}
    </div>
  );
}
