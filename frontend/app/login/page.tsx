"use client";

import { CalendarDays, Eye, EyeOff, LoaderCircle, RefreshCw } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { ApiError, api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { Alert, Button, Input } from "@/components/ui";

/**
 * Signing in is the one page with no navigation around it (see `AppShell`):
 * a rail full of places you cannot go yet is not orientation, it is noise.
 *
 * It is also where a sleeping server is first felt. A free-tier backend that
 * has been idle takes up to a minute to answer its first request, and a
 * button that says "Signing in…" for fifty seconds looks broken. So the page
 * wakes the server as it opens, says plainly when it is waiting on one, and
 * never reports a server that did not answer as a wrong password.
 */
const SLOW_AFTER_MS = 3000;

type Trouble =
  | { kind: "credentials"; message: string }
  | { kind: "unreachable" }
  | { kind: "server"; message: string };

export default function LoginPage() {
  const { login, user } = useAuth();
  const router = useRouter();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [trouble, setTrouble] = useState<Trouble | null>(null);
  const [busy, setBusy] = useState(false);
  const [slow, setSlow] = useState(false);
  const slowTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  // One request, as the page opens, so the server is already waking while
  // someone types. Not a keep-alive: it is never repeated.
  useEffect(() => {
    api.health().catch(() => {
      /* the sign-in itself will say if the server is unreachable */
    });
    return () => {
      if (slowTimer.current) clearTimeout(slowTimer.current);
    };
  }, []);

  useEffect(() => {
    if (user) router.replace("/");
  }, [user, router]);

  if (user) return null;

  async function submit(e?: React.FormEvent) {
    e?.preventDefault();
    setBusy(true);
    setSlow(false);
    setTrouble(null);
    slowTimer.current = setTimeout(() => setSlow(true), SLOW_AFTER_MS);
    try {
      await login(username, password);
      router.replace("/");
    } catch (err) {
      const status = err instanceof ApiError ? err.status : 0;
      // No answer, or the host answering for a server that is still starting.
      if (status === 0 || status === 502 || status === 503 || status === 504) {
        setTrouble({ kind: "unreachable" });
      } else if (status === 401 || status === 403 || status === 400 || status === 422 || status === 429) {
        setTrouble({ kind: "credentials", message: err instanceof Error ? err.message : "Sign-in failed." });
      } else {
        setTrouble({ kind: "server", message: "The server had a problem signing you in. Try again in a moment." });
      }
    } finally {
      if (slowTimer.current) clearTimeout(slowTimer.current);
      slowTimer.current = null;
      setBusy(false);
      setSlow(false);
    }
  }

  return (
    <div className="mx-auto flex min-h-dvh max-w-sm flex-col justify-center px-4 py-12">
      <div className="mb-8 text-center">
        <span className="mx-auto mb-4 flex h-11 w-11 items-center justify-center rounded-md bg-accent-surface text-accent">
          <CalendarDays aria-hidden="true" size={22} />
        </span>
        <h1 className="text-xl font-semibold tracking-tight text-ink">
          College Timetable Generator
        </h1>
        <p className="mt-1 text-sm text-ink-muted">
          Sign in to generate or edit the timetable.
        </p>
      </div>

      <form onSubmit={submit} className="space-y-4 rounded-md border border-line bg-surface p-5">
        {trouble?.kind === "credentials" && <Alert tone="blocker" title={trouble.message} />}
        {trouble?.kind === "server" && <Alert tone="caution" title={trouble.message} />}
        {trouble?.kind === "unreachable" && (
          <Alert
            tone="caution"
            title="Couldn't reach the server."
            action={
              <Button type="button" variant="secondary" size="sm" onClick={() => void submit()}>
                <RefreshCw aria-hidden="true" size={14} />
                Try again
              </Button>
            }
          >
            It may still be starting up - on first use this can take up to a minute. Your
            username and password were not rejected.
          </Alert>
        )}

        <Input
          label="Username"
          value={username}
          onChange={(e) => setUsername(e.target.value)}
          autoFocus
          required
          autoComplete="username"
        />
        <Input
          label="Password"
          type={showPassword ? "text" : "password"}
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          required
          autoComplete="current-password"
          className="pr-11"
          trailing={
            <button
              type="button"
              onClick={() => setShowPassword((v) => !v)}
              aria-label={showPassword ? "Hide password" : "Show password"}
              aria-pressed={showPassword}
              className="flex h-9 w-10 items-center justify-center rounded-md text-ink-muted hover:text-ink"
            >
              {showPassword ? (
                <EyeOff aria-hidden="true" size={16} />
              ) : (
                <Eye aria-hidden="true" size={16} />
              )}
            </button>
          }
        />
        <Button type="submit" size="lg" disabled={busy} className="w-full">
          {busy && <LoaderCircle aria-hidden="true" size={16} className="animate-spin" />}
          {busy ? (slow ? "Connecting to server…" : "Signing in…") : "Sign in"}
        </Button>

        {busy && slow && (
          <p role="status" className="text-center text-xs text-ink-muted">
            The server is waking up. This can take up to a minute the first time - there is no
            need to press Sign in again.
          </p>
        )}
      </form>

      <p className="mt-6 text-center text-xs text-ink-faint">
        Reading a timetable needs no account — sign in only to generate, edit,
        import data or publish.
      </p>
    </div>
  );
}
