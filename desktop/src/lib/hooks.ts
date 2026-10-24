import { useCallback, useEffect, useRef, useState } from "react";

import { onEvent } from "./api";
import type { BusEvent } from "./types";

export interface Loadable<T> {
  data: T | null;
  error: string | null;
  loading: boolean;
  reload: () => void;
  setData: (data: T | null) => void;
}

/** Load data on mount and whenever `deps` change. Stale responses are discarded. */
export function useLoad<T>(loader: () => Promise<T>, deps: unknown[] = []): Loadable<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const generation = useRef(0);
  const loaderRef = useRef(loader);
  loaderRef.current = loader;

  const run = useCallback(() => {
    const current = ++generation.current;
    setLoading(true);
    loaderRef
      .current()
      .then((value) => {
        if (current !== generation.current) return;
        setData(value);
        setError(null);
      })
      .catch((err: unknown) => {
        if (current !== generation.current) return;
        setError(errorMessage(err));
      })
      .finally(() => {
        if (current === generation.current) setLoading(false);
      });
  }, []);

  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(run, deps);

  return { data, error, loading, reload: run, setData };
}

/** React to core events whose topic starts with `prefix`. */
export function useBus(prefix: string, handler: (event: BusEvent) => void): void {
  const ref = useRef(handler);
  ref.current = handler;
  useEffect(
    () =>
      onEvent((event) => {
        if (event.topic.startsWith(prefix)) ref.current(event);
      }),
    [prefix],
  );
}

export function useInterval(callback: () => void, ms: number | null): void {
  const ref = useRef(callback);
  ref.current = callback;
  useEffect(() => {
    if (ms === null) return;
    const id = window.setInterval(() => ref.current(), ms);
    return () => window.clearInterval(id);
  }, [ms]);
}

export function useDebounced<T>(value: T, ms: number): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const id = window.setTimeout(() => setDebounced(value), ms);
    return () => window.clearTimeout(id);
  }, [value, ms]);
  return debounced;
}

export function errorMessage(err: unknown): string {
  if (err instanceof Error) return err.message;
  // The shell's plugins (opener, dialog) reject with a plain string: it says what went wrong.
  if (typeof err === "string" && err.trim()) return err;
  return "Something went wrong";
}
