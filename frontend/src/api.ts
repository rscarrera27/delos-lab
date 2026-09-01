import type {
  DeploymentSnapshot,
  LogEntry,
  KvResult,
  KvValue,
  ProcessAction,
  RequestIdentity,
} from "./types";

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    message: string,
    public readonly body: unknown,
    public readonly code: string | null,
    public readonly detail: unknown,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

type Fetcher = typeof fetch;

async function request<T>(fetcher: Fetcher, url: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  if (init.body !== undefined) headers.set("content-type", "application/json");
  const response = await fetcher(url, { ...init, headers });
  const contentType = response.headers.get("content-type") ?? "";
  const body: unknown =
    response.status !== 204 && contentType.includes("application/json")
      ? await response.json()
      : null;
  if (!response.ok) {
    const details = body as { code?: string; message?: string; detail?: unknown } | null;
    const detail = details?.detail ?? details?.message ?? null;
    const code = details?.code ?? null;
    throw new ApiError(
      response.status,
      typeof detail === "string"
        ? detail
        : code ?? `${response.status} ${response.statusText}`,
      body,
      code,
      detail,
    );
  }
  return body as T;
}

const encoded = (value: string) => encodeURIComponent(value);
const identityBody = ({ clientId, requestId }: RequestIdentity) => ({
  client_id: clientId,
  request_id: requestId,
});

export function createLabApi(fetcher: Fetcher = fetch) {
  return {
    deployment: () => request<DeploymentSnapshot>(fetcher, "/api/deployment"),
    resetCluster: () => request<void>(fetcher, "/api/cluster/reset", { method: "POST" }),
    addDatabaseNode: () =>
      request<{ node_id: string }>(fetcher, "/api/database-nodes", { method: "POST" }),
    nodeAction: (nodeId: string, action: ProcessAction) =>
      request<void>(fetcher, `/api/nodes/${encoded(nodeId)}/${action}`, { method: "POST" }),
    kvGet: (nodeId: string, key: string) =>
      request<KvResult>(fetcher, `/api/nodes/${encoded(nodeId)}/kv/${encoded(key)}`),
    kvPut: (nodeId: string, key: string, value: KvValue, identity: RequestIdentity) =>
      request<KvResult>(fetcher, `/api/nodes/${encoded(nodeId)}/kv/${encoded(key)}`, {
        method: "PUT",
        body: JSON.stringify({ ...identityBody(identity), value }),
      }),
    kvDelete: (nodeId: string, key: string, identity: RequestIdentity) =>
      request<KvResult>(fetcher, `/api/nodes/${encoded(nodeId)}/kv/${encoded(key)}`, {
        method: "DELETE",
        body: JSON.stringify(identityBody(identity)),
      }),
    kvCompareAndSet: (
      nodeId: string,
      key: string,
      expected: KvValue | null,
      value: KvValue,
      identity: RequestIdentity,
    ) =>
      request<KvResult>(fetcher, `/api/nodes/${encoded(nodeId)}/kv/${encoded(key)}/compare-and-set`, {
        method: "POST",
        body: JSON.stringify({ ...identityBody(identity), expected, value }),
      }),
    kvIncrement: (
      nodeId: string,
      key: string,
      delta: number,
      identity: RequestIdentity,
    ) =>
      request<KvResult>(fetcher, `/api/nodes/${encoded(nodeId)}/kv/${encoded(key)}/increment`, {
        method: "POST",
        body: JSON.stringify({ ...identityBody(identity), delta }),
      }),
    logEntries: (nodeId: string, segmentId: string, start = 0, limit = 200) =>
      request<LogEntry[]>(
        fetcher,
        `/api/nodes/${encoded(nodeId)}/segments/${encoded(segmentId)}/entries?start=${start}&limit=${limit}`,
      ),
  };
}

export type LabApi = ReturnType<typeof createLabApi>;
export const labApi = createLabApi();
