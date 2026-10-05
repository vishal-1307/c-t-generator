"use client";

import { LoaderCircle } from "lucide-react";
import { useState, type ReactNode } from "react";
import { ApiError, fetchFile } from "@/lib/api";
import { useNotices } from "@/lib/notices";

/**
 * A link to a file the API makes.
 *
 * Still a real link - it can be copied, and opens in a new tab - but a click
 * fetches the file and saves it, so a file that cannot be made is reported
 * here, in a notice, instead of sending the tab to a page of JSON.
 */
export function DownloadLink({
  href,
  className,
  children,
}: {
  href: string;
  className?: string;
  children: ReactNode;
}) {
  const { notify } = useNotices();
  const [busy, setBusy] = useState(false);

  async function save(event: React.MouseEvent<HTMLAnchorElement>) {
    // Let the browser handle "open in new tab" and friends as a link.
    if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) {
      return;
    }
    event.preventDefault();
    if (busy) return;
    setBusy(true);
    try {
      const { blob, filename } = await fetchFile(href);
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = filename ?? "timetable";
      document.body.appendChild(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (err) {
      // Connection and server trouble already raised their own notice.
      const status = err instanceof ApiError ? err.status : 0;
      if (status !== 0 && status < 500 && status !== 401) {
        notify({
          tone: "error",
          key: `download:${href}`,
          title: "Could not download the file",
          detail: err instanceof Error ? err.message : String(err),
        });
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <a href={href} onClick={save} aria-busy={busy || undefined} className={className}>
      {busy && <LoaderCircle aria-hidden="true" size={14} className="animate-spin" />}
      {children}
    </a>
  );
}
