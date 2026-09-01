import { useCallback, useEffect, useRef, useState } from "react";

import type { LabApi } from "./api";
import type { DeploymentSnapshot } from "./types";

const errorMessage = (error: unknown) =>
  error instanceof Error ? error.message : "알 수 없는 관찰 오류가 발생했습니다.";

export function useLabSnapshot(api: LabApi, intervalMs = 1_000) {
  const [deployment, setDeployment] = useState<DeploymentSnapshot | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState<string | null>(null);
  const inFlight = useRef<Promise<void> | null>(null);
  const failed = useRef(false);

  const refresh = useCallback((): Promise<void> => {
    if (inFlight.current !== null) return inFlight.current;
    const task = (async () => {
      try {
        const nextDeployment = await api.deployment();
        setDeployment(nextDeployment);
        setError(null);
        failed.current = false;
      } catch (cause) {
        setError(errorMessage(cause));
        failed.current = true;
      } finally {
        setLoading(false);
        inFlight.current = null;
      }
    })();
    inFlight.current = task;
    return task;
  }, [api]);

  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const tick = async () => {
      await refresh();
      if (!cancelled) timer = setTimeout(tick, failed.current ? 5_000 : intervalMs);
    };
    void tick();
    const onVisibility = () => {
      if (document.visibilityState === "visible") void refresh();
    };
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      cancelled = true;
      if (timer !== undefined) clearTimeout(timer);
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [intervalMs, refresh]);

  const run = useCallback(
    async (operation: string, mutation: () => Promise<unknown>): Promise<void> => {
      setPending(operation);
      setError(null);
      try {
        await mutation();
        const olderPoll = inFlight.current;
        if (olderPoll !== null) await olderPoll;
        await refresh();
      } catch (cause) {
        setError(errorMessage(cause));
        throw cause;
      } finally {
        setPending(null);
      }
    },
    [refresh],
  );

  return { deployment, loading, error, pending, refresh, run };
}

export type LabSnapshot = ReturnType<typeof useLabSnapshot>;
