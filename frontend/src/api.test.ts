import { createLabApi } from "./api";

const response = (body: unknown, status = 200) =>
  new Response(status === 204 ? null : JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });

describe("LabApi", () => {
  it("loads the deployment snapshot", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValueOnce(response({ nodes: {} }));
    const api = createLabApi(fetcher);

    await expect(api.deployment()).resolves.toMatchObject({ nodes: {} });

    expect(fetcher).toHaveBeenCalledWith("/api/deployment", expect.any(Object));
  });

  it("sends a stable KV command identity to the selected DB", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(response({ value: "blue" }));
    const api = createLabApi(fetcher);

    await api.kvPut("node/full", "colour", "blue", {
      clientId: "browser-a",
      requestId: "request-7",
    });

    expect(fetcher).toHaveBeenCalledWith(
      "/api/nodes/node%2Ffull/kv/colour",
      expect.objectContaining({
        method: "PUT",
        body: JSON.stringify({
          client_id: "browser-a",
          request_id: "request-7",
          value: "blue",
        }),
      }),
    );
  });

  it("encodes node lifecycle actions", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValueOnce(response(null, 204));
    const api = createLabApi(fetcher);

    await api.nodeAction("db/2", "kill");

    expect(fetcher).toHaveBeenCalledWith(
      "/api/nodes/db%2F2/kill",
      expect.objectContaining({ method: "POST" }),
    );
  });

  it("resets the lab runtime through the controller", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValueOnce(response(null, 204));
    const api = createLabApi(fetcher);

    await api.resetCluster();

    expect(fetcher).toHaveBeenCalledWith(
      "/api/cluster/reset",
      expect.objectContaining({ method: "POST" }),
    );
  });

  it("requests a new database node through the controller", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValueOnce(response({ node_id: "db-4" }, 201));
    const api = createLabApi(fetcher);

    await expect(api.addDatabaseNode()).resolves.toEqual({ node_id: "db-4" });
    expect(fetcher).toHaveBeenCalledWith(
      "/api/database-nodes",
      expect.objectContaining({ method: "POST" }),
    );
  });

  it("surfaces structured HTTP failures", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(
      response({ code: "UNAVAILABLE", message: "quorum unavailable" }, 503),
    );
    const api = createLabApi(fetcher);

    await expect(api.kvGet("db-1", "missing")).rejects.toEqual(
      expect.objectContaining({
        name: "ApiError",
        status: 503,
        code: "UNAVAILABLE",
        detail: "quorum unavailable",
        message: "quorum unavailable",
      }),
    );
  });

  it("loads a bounded segment entry range from a colocated LogServer", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(response([]));
    const api = createLabApi(fetcher);

    await api.logEntries("node/full", "segment/2", 3, 20);

    expect(fetcher).toHaveBeenCalledWith(
      "/api/nodes/node%2Ffull/segments/segment%2F2/entries?start=3&limit=20",
      expect.any(Object),
    );
  });
});
