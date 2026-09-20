"use client";

import { useCallback, useEffect, useState } from "react";

import { describePostBoxError } from "@/contexts/postbox-context";

/**
 * Fetch-with-dependencies, without writing state synchronously in an effect.
 *
 * The obvious version —
 *
 *     const load = useCallback(async () => { setLoading(true); ... }, [deps])
 *     useEffect(() => { void load(); }, [load])
 *
 * — sets state on the way into an effect, which React's `set-state-in-effect`
 * rule flags and which causes an extra render pass on every dependency change.
 *
 * Here `loading` is DERIVED: it means "the answer on screen is not the answer
 * to the question currently being asked". A key is computed during render from
 * the dependencies and a reload counter; whatever the effect settles is stored
 * with the key it was fetched for. When the two differ, the request is in
 * flight.
 *
 * The same shape is used by the Platform Console's `useListData`, for the same
 * reason.
 */
export function useAsyncData<T>(
  loader: () => Promise<T>,
  deps: React.DependencyList,
  fallbackMessage = "That could not be loaded.",
): {
  data: T | null;
  error: string | null;
  loading: boolean;
  reload: () => void;
} {
  const [nonce, setNonce] = useState(0);
  const key = `${JSON.stringify(deps)}::${nonce}`;

  const [settled, setSettled] = useState<{
    key: string;
    data: T | null;
    error: string | null;
  }>({ key: "", data: null, error: null });

  const reload = useCallback(() => setNonce((value) => value + 1), []);

  useEffect(() => {
    let cancelled = false;

    loader()
      .then((result) => {
        if (!cancelled) setSettled({ key, data: result, error: null });
      })
      .catch((caught) => {
        if (!cancelled) {
          setSettled({
            key,
            data: null,
            error: describePostBoxError(caught, fallbackMessage),
          });
        }
      });

    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);

  const loading = settled.key !== key;

  return {
    // The previous answer is withheld while a new one is in flight, so a list
    // never shows one folder's messages under another folder's heading.
    data: loading ? null : settled.data,
    error: loading ? null : settled.error,
    loading,
    reload,
  };
}
