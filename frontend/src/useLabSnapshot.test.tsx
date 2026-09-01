import { act, renderHook, waitFor } from "@testing-library/react";

import type { LabApi } from "./api";
import type { DeploymentSnapshot } from "./types";
import { useLabSnapshot } from "./useLabSnapshot";

const deployment = (running: boolean): DeploymentSnapshot => ({
  services: {
    meta: { name: "meta", configured: 3, running: 0, reachable: 0 },
    db: { name: "db", configured: 3, running: running ? 1 : 0, reachable: running ? 1 : 0 },
  },
  nodes: {
    "db-1": {
      node_id: "db-1",
      service: "db",
      lifecycle: running ? "running" : "exited",
      reachable: running,
      metastore: null,
      database: null,
      error: null,
      observed_at: 1,
    },
  },
  collected_at: 1,
});

const nodeTopology = (
  appliedPosition: number,
  values: Record<string, string | number>,
): DeploymentSnapshot => {
  const snapshot = deployment(true);
  snapshot.nodes["db-1"].database = {
    node_id: "db-1",
    process: { status: "online", incarnation_id: "inc-1" },
    application: { applied_position: appliedPosition, values, request_count: 0 },
    virtual_log: {
      chain_version: null,
      cached_chain: null,
      active_segment: null,
      active_virtual_start: null,
      known_virtual_tail: null,
    },
    native_loglet_client: null,
    sequencer: null,
    log_server: { segments: [] },
  };
  return snapshot;
};

function fakeApi(): LabApi {
  return {
    deployment: vi.fn().mockResolvedValue(deployment(true)),
  } as unknown as LabApi;
}

describe("useLabSnapshot", () => {
  it("polls the deployment snapshot", async () => {
    vi.useFakeTimers();
    const api = fakeApi();
    const { result } = renderHook(() => useLabSnapshot(api, 1_000));

    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(result.current.deployment).toEqual(deployment(true));

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1_000);
    });

    expect(api.deployment).toHaveBeenCalledTimes(2);
  });

  it("replaces node snapshots on each topology poll", async () => {
    vi.useFakeTimers();
    const api = fakeApi();
    vi.mocked(api.deployment)
      .mockResolvedValueOnce(nodeTopology(2, { removed: "old" }))
      .mockResolvedValue(nodeTopology(4, {}));
    const { result } = renderHook(() => useLabSnapshot(api, 1_000));

    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(result.current.deployment?.nodes["db-1"].database).toMatchObject({
      application: { applied_position: 2, values: { removed: "old" } },
    });

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1_000);
    });
    expect(result.current.deployment?.nodes["db-1"].database).toMatchObject({
      application: { applied_position: 4, values: {} },
    });
  });

  it("keeps the last good topology while reporting a refresh error", async () => {
    const api = fakeApi();
    const topologyMock = vi.mocked(api.deployment);
    const { result } = renderHook(() => useLabSnapshot(api, 60_000));
    await waitFor(() => expect(result.current.deployment).toEqual(deployment(true)));

    topologyMock.mockRejectedValueOnce(new Error("controller offline"));
    await act(async () => result.current.refresh());

    expect(result.current.deployment).toEqual(deployment(true));
    expect(result.current.error).toBe("controller offline");
  });

  it("refreshes after a mutation and exposes pending operation", async () => {
    let finish!: () => void;
    const api = fakeApi();
    const mutation = vi.fn(
      () => new Promise<void>((resolve) => {
        finish = resolve;
      }),
    );
    const { result } = renderHook(() => useLabSnapshot(api, 60_000));
    await waitFor(() => expect(result.current.loading).toBe(false));

    let task!: Promise<void>;
    act(() => {
      task = result.current.run("restart-db-1", mutation);
    });
    expect(result.current.pending).toBe("restart-db-1");

    await act(async () => {
      finish();
      await task;
    });
    expect(result.current.pending).toBeNull();
    expect(api.deployment).toHaveBeenCalledTimes(2);
  });

  it("fetches again when a mutation overlaps an older poll", async () => {
    let resolveOld!: (snapshot: DeploymentSnapshot) => void;
    const oldPoll = new Promise<DeploymentSnapshot>((resolve) => {
      resolveOld = resolve;
    });
    const api = {
      deployment: vi.fn().mockReturnValueOnce(oldPoll).mockResolvedValue(deployment(true)),
    } as unknown as LabApi;
    const { result } = renderHook(() => useLabSnapshot(api, 60_000));

    let mutation!: Promise<void>;
    act(() => {
      mutation = result.current.run("start-db-1", async () => undefined);
    });
    await act(async () => {
      resolveOld(deployment(false));
      await mutation;
    });

    expect(api.deployment).toHaveBeenCalledTimes(2);
    expect(result.current.deployment).toEqual(deployment(true));
  });
});
