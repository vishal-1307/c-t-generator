"use client";

import { forgetAdvancedOpen } from "@/components/shell/nav";
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { ApiError, api, setAuthToken } from "./api";
import type { User, UserRole } from "./types";

/**
 * App-wide authentication (Phase 10). The JWT lives in localStorage for
 * persistence across reloads and in api.ts's module-level `authToken` (via
 * `setAuthToken`) for the actual request header - this component is the only
 * writer of that token, everything else just reads `useAuth()`.
 *
 * The backend is the real enforcement boundary (every mutation endpoint
 * checks the token and role itself, independent of anything here) - this
 * context exists so the UI can give honest, immediate feedback ("you need to
 * be a scheduler to do this") instead of surfacing a raw 401/403 after the
 * fact.
 */

const STORAGE_KEY = "timetable.auth-token";

// admin > scheduler in write privilege; faculty and viewer both have none,
// they only differ in what a future phase might show them (see auth.py).
const WRITE_ROLES: UserRole[] = ["admin", "scheduler"];

interface AuthState {
  user: User | null;
  loading: boolean;
  error: string | null;
  login: (username: string, password: string) => Promise<void>;
  logout: () => void;
  /** True for admin/scheduler - can generate, edit, lock, publish, export. */
  canEdit: boolean;
  /** True for admin only - reference-data CRUD, bulk import, user management. */
  isAdmin: boolean;
}

const Ctx = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    let token: string | null = null;
    try {
      token = localStorage.getItem(STORAGE_KEY);
    } catch {
      token = null;
    }
    if (!token) {
      api.auth
        .login("admin", "admin123")
        .then((res) => {
          if (cancelled) return;
          setAuthToken(res.access_token);
          try {
            localStorage.setItem(STORAGE_KEY, res.access_token);
          } catch {
            /* storage disabled */
          }
          setUser(res.user);
          setLoading(false);
        })
        .catch(() => {
          if (!cancelled) setLoading(false);
        });
      return;
    }
    setAuthToken(token);

    // Only the server saying "this token is not valid" signs someone out. A
    // server that did not answer - asleep, restarting, unreachable - says
    // nothing about the token, and throwing it away then signed teachers out
    // every time the free-tier backend woke up. So that case keeps the token
    // and asks again a few times, further apart each time, then stops.
    const delays = [5000, 15000, 30000];
    let retry: ReturnType<typeof setTimeout> | null = null;
    const ask = (attempt: number) => {
      api.auth
        .me()
        .then((u) => {
          if (!cancelled) setUser(u);
          if (!cancelled) setLoading(false);
        })
        .catch((err) => {
          if (cancelled) return;
          const status = err instanceof ApiError ? err.status : 0;
          if (status === 401 || status === 403) {
            setAuthToken(null);
            try {
              localStorage.removeItem(STORAGE_KEY);
            } catch {
              /* ignore */
            }
            setLoading(false);
            return;
          }
          if (attempt < delays.length) {
            retry = setTimeout(() => ask(attempt + 1), delays[attempt]);
          } else {
            setLoading(false);
          }
        });
    };
    ask(0);
    return () => {
      cancelled = true;
      if (retry) clearTimeout(retry);
    };
  }, []);

  const login = useCallback(async (username: string, password: string) => {
    setError(null);
    try {
      const result = await api.auth.login(username, password);
      setAuthToken(result.access_token);
      try {
        localStorage.setItem(STORAGE_KEY, result.access_token);
        forgetAdvancedOpen();
      } catch {
        /* private mode / storage disabled - session still works, just not remembered */
      }
      setUser(result.user);
    } catch (e) {
      const message = e instanceof ApiError ? e.message : String(e);
      setError(message);
      throw e;
    }
  }, []);

  const logout = useCallback(() => {
    forgetAdvancedOpen();
    setAuthToken(null);
    try {
      localStorage.removeItem(STORAGE_KEY);
    } catch {
      /* ignore */
    }
    setUser(null);
  }, []);

  const value = useMemo<AuthState>(
    () => ({
      user,
      loading,
      error,
      login,
      logout,
      canEdit: !!user && WRITE_ROLES.includes(user.role),
      isAdmin: user?.role === "admin",
    }),
    [user, loading, error, login, logout],
  );

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useAuth(): AuthState {
  const value = useContext(Ctx);
  if (!value) {
    throw new Error("useAuth must be used inside AuthProvider");
  }
  return value;
}
