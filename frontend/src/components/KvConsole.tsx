import { type ComponentProps, type FormEvent, useMemo, useState } from "react";

import type { LabApi } from "../api";
import type { DeploymentSnapshot, KvResult, KvValue } from "../types";
import { LabeledSelect } from "./LabeledSelect";
import { NodeId, shortNodeId } from "./NodeId";
import { Alert, AlertDescription } from "./ui/alert";
import { Button } from "./ui/button";
import {
  CardAction,
  CardCollapseButton,
  CardContent,
  CardHeader,
  CardTitle,
  CollapsibleCard,
} from "./ui/card";
import { Input } from "./ui/input";
import { Label } from "./ui/label";

type Operation = "get" | "put" | "delete" | "cas" | "increment";
let requestCounter = 0;
const nextRequestId = () => `web-${Date.now().toString(36)}-${++requestCounter}`;
const valueOf = (raw: string): KvValue => (/^-?\d+$/.test(raw.trim()) ? Number(raw) : raw);

function Field({
  id,
  label,
  className,
  ...props
}: { id: string; label: string; className?: string } & ComponentProps<typeof Input>) {
  return (
    <div className={`grid gap-2 ${className ?? ""}`}>
      <Label htmlFor={id}>{label}</Label>
      <Input id={id} {...props} />
    </div>
  );
}

export function KvConsole({
  deployment,
  api,
  onApplied,
}: {
  deployment: DeploymentSnapshot;
  api: LabApi;
  onApplied: () => Promise<unknown> | void;
}) {
  const databases = useMemo(
    () => Object.values(deployment.nodes).filter((node) => node.service === "db"),
    [deployment],
  );
  const [target, setTarget] = useState(databases[0]?.node_id ?? "db-1");
  const [operation, setOperation] = useState<Operation>("get");
  const [key, setKey] = useState("");
  const [value, setValue] = useState("");
  const [expected, setExpected] = useState("");
  const [delta, setDelta] = useState("1");
  const [clientId, setClientId] = useState("delos-browser");
  const [requestId, setRequestId] = useState(nextRequestId);
  const [pending, setPending] = useState(false);
  const [result, setResult] = useState<KvResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    const node = deployment.nodes[target];
    if (!node || key.trim() === "") return;
    setPending(true);
    setError(null);
    const identity = { clientId, requestId };
    try {
      let response: KvResult;
      if (operation === "get") response = await api.kvGet(node.node_id, key);
      else if (operation === "put") response = await api.kvPut(node.node_id, key, valueOf(value), identity);
      else if (operation === "delete") response = await api.kvDelete(node.node_id, key, identity);
      else if (operation === "cas") {
        response = await api.kvCompareAndSet(
          node.node_id,
          key,
          expected === "" ? null : valueOf(expected),
          valueOf(value),
          identity,
        );
      } else response = await api.kvIncrement(node.node_id, key, Number(delta), identity);
      setResult(response);
      if (operation !== "get") {
        await onApplied();
        setRequestId(nextRequestId());
      }
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "KV 명령이 실패했습니다.");
    } finally {
      setPending(false);
    }
  };

  return (
    <div className="space-y-4 py-5">
      <h1 className="text-lg font-semibold tracking-tight">KV Console</h1>
      <div className="grid gap-4 lg:grid-cols-[minmax(0,1.2fr)_minmax(18rem,.8fr)]">
        <CollapsibleCard size="sm">
          <CardHeader className="border-b">
            <CardTitle>Command</CardTitle>
            <CardAction><CardCollapseButton label="Command" /></CardAction>
          </CardHeader>
          <CardContent>
            <form className="space-y-4" onSubmit={submit}>
              <div className="grid gap-4 sm:grid-cols-2">
                <LabeledSelect
                  label="대상 DB"
                  value={target}
                  onValueChange={setTarget}
                  options={databases.map((node) => ({
                    value: node.node_id,
                    label: `${shortNodeId(node.node_id)} · ${node.reachable ? "reachable" : "unreachable"}`,
                  }))}
                />
                <LabeledSelect
                  label="연산"
                  value={operation}
                  onValueChange={(next) => {
                    setOperation(next as Operation);
                    setResult(null);
                  }}
                  options={[
                    { value: "get", label: "GET" },
                    { value: "put", label: "PUT" },
                    { value: "delete", label: "DELETE" },
                    { value: "cas", label: "COMPARE & SET" },
                    { value: "increment", label: "INCREMENT" },
                  ]}
                />
                <Field
                  id="client-id"
                  label="Client ID"
                  required
                  value={clientId}
                  onChange={(event) => setClientId(event.target.value)}
                />
                <Field
                  id="request-id"
                  label="Request ID"
                  required
                  value={requestId}
                  onChange={(event) => setRequestId(event.target.value)}
                />
                <Field
                  id="kv-key"
                  label="키"
                  className="sm:col-span-2"
                  required
                  value={key}
                  onChange={(event) => setKey(event.target.value)}
                  placeholder="예: counter"
                />
                {(operation === "put" || operation === "cas") && (
                  <Field
                    id="kv-value"
                    label={operation === "cas" ? "새 값" : "값"}
                    className={operation === "put" ? "sm:col-span-2" : undefined}
                    required
                    value={value}
                    onChange={(event) => setValue(event.target.value)}
                    placeholder="문자열 또는 정수"
                  />
                )}
                {operation === "cas" && (
                  <Field
                    id="kv-expected"
                    label="기대 값"
                    value={expected}
                    onChange={(event) => setExpected(event.target.value)}
                    placeholder="비우면 null"
                  />
                )}
                {operation === "increment" && (
                  <Field
                    id="kv-delta"
                    label="증가량"
                    className="sm:col-span-2"
                    required
                    type="number"
                    value={delta}
                    onChange={(event) => setDelta(event.target.value)}
                  />
                )}
              </div>
              <Button className="w-full" disabled={pending || !deployment.nodes[target]?.reachable}>
                {pending ? "로그에 기록 중…" : "명령 실행"}
              </Button>
              {error && (
                <Alert variant="destructive">
                  <AlertDescription>{error}</AlertDescription>
                </Alert>
              )}
            </form>
          </CardContent>
        </CollapsibleCard>

        <CollapsibleCard size="sm">
          <CardHeader className="border-b">
            <CardTitle>Response</CardTitle>
            <CardAction><CardCollapseButton label="Response" /></CardAction>
          </CardHeader>
          <CardContent className="grid min-h-52 place-items-center text-center">
            {result ? (
              <div>
                <span className="font-mono text-xs text-muted-foreground">{result.code ?? "READ"}</span>
                <strong className="result-value my-3 block max-w-xs [overflow-wrap:anywhere] font-mono text-3xl">
                  {result.value === null ? "null" : String(result.value)}
                </strong>
                <small className="flex justify-center gap-1 text-muted-foreground">
                  <span>from</span>
                  <NodeId id={target} className="text-xs" />
                </small>
              </div>
            ) : (
              <span className="text-sm text-muted-foreground">명령의 결과가 여기에 나타납니다.</span>
            )}
          </CardContent>
        </CollapsibleCard>
      </div>

      <CollapsibleCard size="sm">
        <CardHeader className="border-b">
          <CardTitle>Process snapshots</CardTitle>
          <CardAction><CardCollapseButton label="Process snapshots" /></CardAction>
        </CardHeader>
        <CardContent className="grid gap-3 md:grid-cols-3">
          {databases.map((node) => {
            const state = node.database;
            return (
              <article key={node.node_id} className="min-w-0 rounded-md border bg-muted/30 p-3">
                <NodeId id={node.node_id} className="text-xs font-semibold" />
                <small className="ml-2 font-mono text-xs text-muted-foreground">
                  applied {state?.application.applied_position ?? "—"}
                </small>
                <pre className="mt-3 min-h-20 overflow-auto border-t pt-3 text-xs">
                  {JSON.stringify(state?.application.values ?? {}, null, 2)}
                </pre>
              </article>
            );
          })}
        </CardContent>
      </CollapsibleCard>
    </div>
  );
}
