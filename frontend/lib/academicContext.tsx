"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { api } from "./api";
import type { AcademicContext } from "./types";

/**
 * The app-wide "which semester am I working on?" selection.
 *
 * Sections, timetable runs, validation and assignments are all scoped to an
 * AcademicContext on the backend, so almost every page needs the same answer.
 * Keeping it here means one fetch and one selector in the shell rather than a
 * context dropdown repeated on eight pages.
 *
 * Faculty, subjects, rooms and time slots are deliberately NOT scoped - they
 * are institution-wide in the data model, and pretending otherwise in the UI
 * would misrepresent the schema.
 */

const STORAGE_KEY = "timetable.academic-context";

interface ContextState {
  contexts: AcademicContext[];
  current: AcademicContext | null;
  currentId: number | null;
  setCurrentId: (id: number | null) => void;
  refresh: () => Promise<void>;
  loading: boolean;
  error: string | null;
}

const Ctx = createContext<ContextState | null>(null);

export function AcademicContextProvider({ children }: { children: ReactNode }) {
  const [contexts, setContexts] = useState<AcademicContext[]>([]);
  const [currentId, setCurrentIdState] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const setCurrentId = useCallback((id: number | null) => {
    setCurrentIdState(id);
    try {
      if (id === null) localStorage.removeItem(STORAGE_KEY);
      else localStorage.setItem(STORAGE_KEY, String(id));
    } catch {
      // Private mode / storage disabled - the selection still works for this
      // session, it just will not be remembered.
    }
  }, []);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const list = await api.academicContexts.list();
      setContexts(list);
      setError(null);

      let remembered: number | null = null;
      try {
        const raw = localStorage.getItem(STORAGE_KEY);
        remembered = raw ? Number(raw) : null;
      } catch {
        remembered = null;
      }
      const stillExists = list.some((c) => c.id === remembered);
      // Otherwise the newest dataset - each upload is one, and the most
      // recent upload is what anyone opening the site means by "the data".
      const newest = list.reduce<number | null>((max, c) => (max === null || c.id > max ? c.id : max), null);
      setCurrentIdState(stillExists ? remembered : newest);
    } catch (e) {
      setContexts([]);
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void refresh();
  }, [refresh]);

  const value = useMemo<ContextState>(
    () => ({
      contexts,
      current: contexts.find((c) => c.id === currentId) ?? null,
      currentId,
      setCurrentId,
      refresh,
      loading,
      error,
    }),
    [contexts, currentId, setCurrentId, refresh, loading, error],
  );

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useAcademicContext(): ContextState {
  const value = useContext(Ctx);
  if (!value) {
    throw new Error("useAcademicContext must be used inside AcademicContextProvider");
  }
  return value;
}
