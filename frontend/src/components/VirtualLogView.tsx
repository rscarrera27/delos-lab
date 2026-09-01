import { useEffect, useMemo, useState } from "react";

import type { LabApi } from "../api";
import { nativeLogletConfiguration } from "../nativeLoglet";
import { latestObservedChain } from "../observations";
import type { DatabaseNodeState, DeploymentSnapshot, LogEntry, LogSegment } from "../types";
import { MetricValue } from "./MetricValue";
import { NodeId } from "./NodeId";
import { Alert, AlertDescription } from "./ui/alert";
import { Badge } from "./ui/badge";
import { Button } from "./ui/button";
import {
  CardAction,
  CardCollapseButton,
  CardContent,
  CardHeader,
  CardTitle,
  CollapsibleCard,
} from "./ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "./ui/table";

interface DecodedOperation {
  kind: string;
  key: string;
  value: string;
}

function decodeOperation(payload: string): DecodedOperation | null {
  try {
    const envelope = JSON.parse(payload) as {
      operation?: {
        kind?: unknown;
        key?: unknown;
        value?: unknown;
        expected?: unknown;
        delta?: unknown;
      };
    };
    const operation = envelope.operation;
    if (!operation || typeof operation.kind !== "string" || typeof operation.key !== "string") {
      return null;
    }
    const detail =
      operation.kind === "increment"
        ? operation.delta
        : operation.kind === "delete"
          ? null
          : operation.value;
    return {
      kind: operation.kind,
      key: operation.key,
      value: detail === null || detail === undefined ? "—" : String(detail),
    };
  } catch {
    return null;
  }
}

function localLimit(segment: LogSegment, databaseStates: DatabaseNodeState[]): number {
  return Math.max(
    0,
    ...databaseStates.map((state) =>
      state.log_server.segments.find((copy) => copy.segment_id === segment.segment_id)
        ?.local_tail ?? 0,
    ),
  );
}

export function VirtualLogView({ deployment, api }: { deployment: DeploymentSnapshot; api: LabApi }) {
  const latestObserved = latestObservedChain(deployment);
  const segments = useMemo(
    () => latestObserved?.chain?.segments ?? [],
    [latestObserved],
  );
  const databaseProcesses = Object.values(deployment.nodes).filter(
    (node) => node.service === "db",
  );
  const databaseStates = databaseProcesses
    .map((node) => node.database as DatabaseNodeState | null)
    .filter((state): state is DatabaseNodeState => state !== null);
  const [selectedSegmentId, setSelectedSegmentId] = useState<string | null>(null);
  const [entries, setEntries] = useState<LogEntry[]>([]);
  const [entryError, setEntryError] = useState<string | null>(null);
  const [loadingEntries, setLoadingEntries] = useState(false);
  const [entryCoverage, setEntryCoverage] = useState<{
    responses: number;
    configured: number;
  } | null>(null);
  const selectedSegment =
    segments.find((segment) => segment.segment_id === selectedSegmentId) ?? segments.at(-1) ?? null;
  const selectedNative = useMemo(
    () => (selectedSegment ? nativeLogletConfiguration(selectedSegment) : null),
    [selectedSegment],
  );

  useEffect(() => {
    if (segments.length > 0 && selectedSegmentId === null) {
      setSelectedSegmentId(segments.at(-1)?.segment_id ?? null);
    }
  }, [segments, selectedSegmentId]);

  useEffect(() => {
    if (selectedSegment === null || selectedNative === null) {
      setEntries([]);
      setEntryCoverage(null);
      return;
    }
    let cancelled = false;
    const limit = Math.min(200, localLimit(selectedSegment, databaseStates));
    const start = Math.max(0, localLimit(selectedSegment, databaseStates) - limit);
    const load = async () => {
      setLoadingEntries(true);
      setEntryError(null);
      setEntryCoverage(null);
      const observations = await Promise.all(selectedNative.storageMembers.map(async (member) => {
        const node = deployment.nodes[member];
        if (!node?.reachable) return { entries: [] as LogEntry[], error: null, responded: false };
        try {
          const result = await api.logEntries(member, selectedSegment.segment_id, start, 200);
          return { entries: result, error: null, responded: true };
        } catch (cause) {
          return { entries: [] as LogEntry[], error: cause, responded: false };
        }
      }));
      if (!cancelled) {
        const responses = observations.filter((observation) => observation.responded).length;
        const lastError = observations
          .map((observation) => observation.error)
          .filter((error) => error !== null)
          .at(-1);
        setEntries(
          observations
            .flatMap((observation) => observation.entries)
            .sort((left, right) => left.position - right.position),
        );
        setEntryCoverage({ responses, configured: selectedNative.storageMembers.length });
        if (responses === 0) {
          setEntryError(
            lastError instanceof Error ? lastError.message : "로그 엔트리를 읽지 못했습니다.",
          );
        }
        setLoadingEntries(false);
      }
    };
    void load();
    return () => {
      cancelled = true;
    };
  }, [api, selectedNative, selectedSegment, deployment, deployment.collected_at]);

  const rows = [...new Set(entries.map((entry) => entry.position))].map((position) => {
    const copies = entries.filter((entry) => entry.position === position);
    const variants = new Set(
      copies.map((entry) => `${entry.command_id}\u0000${entry.payload}`),
    );
    return { position, entry: copies[0], copies: copies.length, conflict: variants.size > 1 };
  });

  return (
    <div className="space-y-4 py-5">
      <div className="flex items-center justify-between gap-4">
        <h1 className="text-lg font-semibold tracking-tight">Virtual Log</h1>
        <Badge variant="outline">
          {latestObserved?.chain ? `Chain v${latestObserved.version}` : "No LogChain"}
        </Badge>
      </div>

      <CollapsibleCard size="sm">
        <CardHeader className="border-b">
          <CardTitle>Segments</CardTitle>
          <CardAction className="flex items-center gap-2">
            <span className="text-xs text-muted-foreground">{segments.length}</span>
            <CardCollapseButton label="Segments" />
          </CardAction>
        </CardHeader>
        <CardContent className="space-y-2">
          {segments.length === 0 ? (
            <p className="p-3 text-sm text-muted-foreground">아직 부트스트랩된 LogChain이 없습니다.</p>
          ) : (
            segments.map((segment, index) => {
              const native = nativeLogletConfiguration(segment);
              const open = index === segments.length - 1;
              const selected = segment.segment_id === selectedSegment?.segment_id;
              return (
                <Button
                  key={segment.segment_id}
                  variant={selected ? "secondary" : "outline"}
                  className="grid h-auto w-full grid-cols-[minmax(0,1fr)_auto_auto] items-center gap-4 px-3 py-2 text-left"
                  onClick={() => setSelectedSegmentId(segment.segment_id)}
                >
                  <span className="min-w-0">
                    <strong className="block truncate font-mono text-xs">{segment.segment_id}</strong>
                    <span className="mt-1 block font-mono text-xs text-muted-foreground">
                      [{segment.virtual_start}, {segment.virtual_stop ?? "∞"})
                    </span>
                    <span className="mt-1 flex min-w-0 items-center gap-1 overflow-hidden text-xs text-muted-foreground">
                      <span>members</span>
                      {(native?.storageMembers ?? []).map((member, memberIndex) => (
                        <span key={member} className="flex min-w-0 items-center gap-1">
                          {memberIndex > 0 && <span aria-hidden="true">·</span>}
                          <NodeId id={member} className="text-xs" />
                        </span>
                      ))}
                    </span>
                    <span className="mt-1 block truncate font-mono text-xs text-muted-foreground">
                      {segment.loglet.kind}@{segment.loglet.version}
                      {native ? ` · inc ${native.sequencerIncarnation}` : ""}
                    </span>
                  </span>
                  {native ? (
                    <NodeId id={native.sequencerNode} className="text-xs" />
                  ) : (
                    <span className="font-mono text-xs">{segment.loglet.kind}</span>
                  )}
                  <Badge variant={open ? "default" : "outline"}>{open ? "Open" : "Sealed"}</Badge>
                </Button>
              );
            })
          )}
        </CardContent>
      </CollapsibleCard>

      <CollapsibleCard size="sm">
        <CardHeader className="border-b">
          <CardTitle>Observed physical entries</CardTitle>
          <CardAction className="flex items-center gap-2">
            <span className="font-mono text-xs text-muted-foreground">
              {selectedSegment?.segment_id ?? "—"} · {loadingEntries ? "loading" : `${rows.length} entries`}
              {entryCoverage ? ` · ${entryCoverage.responses}/${entryCoverage.configured} LogServers` : ""}
            </span>
            <CardCollapseButton label="Observed physical entries" />
          </CardAction>
        </CardHeader>
        <CardContent className="p-0">
          {entryError && (
            <Alert variant="destructive" className="mx-3">
              <AlertDescription>{entryError}</AlertDescription>
            </Alert>
          )}
          {!loadingEntries && entryCoverage !== null && entryCoverage.responses < entryCoverage.configured && (
            <Alert className="mx-3">
              <AlertDescription>
                부분 관찰: LogServer {entryCoverage.responses}/{entryCoverage.configured} 응답.
              </AlertDescription>
            </Alert>
          )}
          <div className="max-h-96 overflow-auto">
          <Table aria-label="Observed physical entries">
            <TableHeader className="sticky top-0 bg-card">
              <TableRow>
                <TableHead>Position</TableHead>
                <TableHead>Command</TableHead>
                <TableHead>Operation</TableHead>
                <TableHead className="text-right">Copies</TableHead>
                <TableHead>Key</TableHead>
                <TableHead>Value</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.length === 0 ? (
                <TableRow>
                  <TableCell colSpan={6} className="py-8 text-center text-muted-foreground">
                    관찰된 물리 엔트리가 없습니다.
                  </TableCell>
                </TableRow>
              ) : (
                rows.map(({ position, entry, copies, conflict }) => {
                  const operation = decodeOperation(entry.payload);
                  const outsideChain =
                    selectedSegment?.virtual_stop !== null &&
                    selectedSegment?.virtual_stop !== undefined &&
                    position >= selectedSegment.virtual_stop - selectedSegment.virtual_start;
                  const operationLabel = outsideChain
                    ? "OUTSIDE CHAIN"
                    : conflict
                      ? "CONFLICT"
                      : operation?.kind.toUpperCase() ?? "RAW";
                  return (
                    <TableRow key={position}>
                      <TableCell className="font-mono text-xs">
                        {outsideChain ? "—" : `V${(selectedSegment?.virtual_start ?? 0) + position}`} · {selectedSegment?.segment_id}:{position}
                      </TableCell>
                      <TableCell className="max-w-52 truncate font-mono text-xs">
                        {entry.command_id}
                      </TableCell>
                      <TableCell>
                        <Badge variant="outline">{operationLabel}</Badge>
                      </TableCell>
                      <TableCell className="text-right"><MetricValue name="copies" value={copies} /></TableCell>
                      <TableCell className="font-mono text-xs">{operation?.key ?? "—"}</TableCell>
                      <TableCell className="max-w-72 truncate font-mono text-xs" title={entry.payload}>
                        {operation?.value ?? entry.payload}
                      </TableCell>
                    </TableRow>
                  );
                })
              )}
            </TableBody>
          </Table>
          </div>
        </CardContent>
      </CollapsibleCard>
    </div>
  );
}
