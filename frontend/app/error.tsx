"use client"; // Error boundaries must be Client Components

import Link from "next/link";

/**
 * Root error boundary.
 *
 * Without this file a thrown render error takes the whole application down to
 * Next's default screen, which says nothing useful and offers no way back. The
 * risk grows with the dataset: a malformed row or an unexpected null in one
 * table should cost the user that page, not their session.
 *
 * `retry()` rather than `reset()` deliberately - this Next version documents
 * `retry` as the one to reach for, because it re-fetches the boundary's
 * children before re-rendering them. `reset` only re-renders, which for a page
 * whose data was the problem just reproduces the same error.
 *
 * The message is shown because hiding it helps nobody. Errors from Server
 * Components arrive already redacted to a generic string plus a digest, so
 * showing it here cannot leak server detail.
 */
export default function Error({
  error,
  retry,
}: {
  error: Error & { digest?: string };
  retry: () => void;
}) {
  return (
    <div className="mx-auto max-w-lg py-16">
      <div className="rounded-md border border-blocker-line bg-blocker-surface p-6">
        <h1 className="text-base font-semibold text-blocker">
          Something went wrong on this page
        </h1>
        <p className="mt-2 text-sm text-ink-soft">
          The rest of the application is unaffected. Try again, and if it keeps
          happening the reference below identifies it in the server logs.
        </p>
        <p className="mt-3 break-words rounded border border-blocker-line bg-surface px-3 py-2 font-mono text-xs text-blocker">
          {error.message || "Unknown error"}
          {error.digest && (
            <span className="mt-1 block text-ink-muted">ref: {error.digest}</span>
          )}
        </p>
        <div className="mt-4 flex gap-2">
          <button
            onClick={() => retry()}
            className="inline-flex h-9 items-center rounded-md bg-accent px-3 text-sm font-medium text-accent-ink transition-colors hover:bg-accent-hover"
          >
            Try again
          </button>
          <Link
            href="/"
            className="inline-flex h-9 items-center rounded-md border border-line-strong bg-surface px-3 text-sm font-medium text-ink-soft transition-colors hover:bg-surface-hover"
          >
            Back to dashboard
          </Link>
        </div>
      </div>
    </div>
  );
}
