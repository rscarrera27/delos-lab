import type { ReactNode } from "react";

import { nativeLogletConfiguration } from "../nativeLoglet";
import { latestObservedChain } from "../observations";
import type { DeploymentSnapshot } from "../types";
import { MetricValue } from "./MetricValue";
import { NodeId } from "./NodeId";
import { Badge } from "./ui/badge";
import {
  CardAction,
  CardCollapseButton,
  CardContent,
  CardHeader,
  CardTitle,
  CollapsibleCard,
} from "./ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "./ui/table";

function DescriptionList({ items }: { items: Array<{ label: string; value: ReactNode }> }) {
  return (
    <dl className="grid min-w-36 gap-1.5 py-1 text-xs">
      {items.map(({ label, value }) => (
        <div key={label} className="flex items-baseline justify-between gap-4">
          <dt className="text-muted-foreground">{label}</dt>
          <dd className="font-mono text-right">{value}</dd>
        </div>
      ))}
    </dl>
  );
}

function ComponentIdentity({ nodeId }: { nodeId: string }) {
  return <NodeId id={nodeId} className="text-xs font-semibold" />;
}

export function DatabaseComponents({ deployment }: { deployment: DeploymentSnapshot }) {
  const databases = Object.values(deployment.nodes)
    .filter((node) => node.service === "db")
    .sort((left, right) => left.node_id.localeCompare(right.node_id));
  const chain = latestObservedChain(deployment);
  const active = chain?.chain?.segments.at(-1);
  const native = active ? nativeLogletConfiguration(active) : null;
  const sequencerNode = native
    ? databases.find((node) => node.node_id === native.sequencerNode)
    : undefined;
  const observedSequencer = sequencerNode?.database?.sequencer;
  const sequencerRuntime =
    observedSequencer?.segment_id === active?.segment_id ? observedSequencer : null;
  const sequencerState = !sequencerNode?.reachable || sequencerNode.database === null
    ? "unavailable"
    : sequencerNode.database.process.incarnation_id === native?.sequencerIncarnation
      ? "active"
      : "incarnation-mismatch";
  const logServerRows = (native?.storageMembers ?? databases.map((node) => node.node_id)).map(
    (processId) => {
    const node = databases.find((candidate) => candidate.node_id === processId);
    const server = node?.database?.log_server.segments.find(
      (candidate) => candidate.segment_id === active?.segment_id,
    );
    return {
      processId,
      node,
      server,
      segment: active?.segment_id ?? null,
    };
  });

  return (
    <>
      <CollapsibleCard size="sm">
        <CardHeader className="border-b">
          <CardTitle><h3>Application</h3></CardTitle>
          <CardAction><CardCollapseButton label="Application" /></CardAction>
        </CardHeader>
        <CardContent className="p-0">
          <Table aria-label="Application">
            <TableHeader>
              <TableRow>
                <TableHead>Process</TableHead>
                <TableHead>Application</TableHead>
                <TableHead>VirtualLog client</TableHead>
                <TableHead>NativeLoglet client</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {databases.map((node) => {
                const state = node.database;
                const application = state?.application;
                const virtualLog = state?.virtual_log;
                const client = state?.native_loglet_client;
                const replayLag =
                  application?.applied_position != null && virtualLog?.known_virtual_tail != null
                    ? Math.max(
                        0,
                        virtualLog.known_virtual_tail - (application.applied_position + 1),
                      )
                    : null;
                return (
                  <TableRow key={node.node_id}>
                    <TableCell className="align-top">
                      <ComponentIdentity nodeId={node.node_id} />
                    </TableCell>
                    <TableCell className="align-top">
                      <DescriptionList items={[
                        { label: "Applied", value: application?.applied_position == null ? "—" : <MetricValue name="applied" value={`V${application.applied_position}`} /> },
                        { label: "Replay lag", value: replayLag ?? "—" },
                        { label: "KV keys", value: application ? Object.keys(application.values).length : "—" },
                        { label: "Requests", value: application?.request_count ?? "—" },
                      ]} />
                    </TableCell>
                    <TableCell className="align-top">
                      <DescriptionList items={[
                        { label: "Chain cache", value: virtualLog?.chain_version == null ? "—" : `v${virtualLog.chain_version}` },
                        { label: "Active", value: virtualLog?.active_segment ?? "—" },
                      ]} />
                    </TableCell>
                    <TableCell className="align-top">
                      <DescriptionList items={[
                        { label: "Segment", value: client?.segment_id ?? "—" },
                        { label: "knownTail", value: client ? `${client.segment_id}:${client.known_tail}` : "—" },
                        { label: "checkTail", value: client?.last_check_tail ? `${client.last_check_tail.tail} · ${client.last_check_tail.sealed ? "sealed" : "open"}` : "Not called" },
                      ]} />
                    </TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        </CardContent>
      </CollapsibleCard>

      <CollapsibleCard size="sm">
        <CardHeader className="border-b">
          <CardTitle><h3>NativeLoglet</h3></CardTitle>
          <CardAction><CardCollapseButton label="NativeLoglet" /></CardAction>
        </CardHeader>
        <CardContent className="grid gap-3">
          <section
            aria-labelledby="sequencer-heading"
            className="overflow-hidden rounded-lg border bg-background/40"
          >
            <div className="border-b bg-muted/50 px-3 py-2.5">
              <h4 id="sequencer-heading" className="text-xs font-semibold">Latest Segment Sequencer</h4>
            </div>
            <dl
              aria-label="NativeLoglet sequencer"
              className="grid gap-px bg-border sm:grid-cols-2 xl:grid-cols-5"
            >
              {[
                { label: "Segment", value: active?.segment_id ?? "—" },
                { label: "Process", value: native ? <ComponentIdentity nodeId={native.sequencerNode} /> : "—" },
                { label: "Incarnation", value: native?.sequencerIncarnation ?? "—" },
                { label: "knownTail", value: <MetricValue name="sequencer-known" value={sequencerRuntime?.known_tail ?? "—"} /> },
                { label: "Status", value: !native ? "—" : (
                  <Badge
                    variant={sequencerState === "active" ? "default" : sequencerState === "unavailable" ? "destructive" : "outline"}
                  >
                    {sequencerState === "active" ? "Active" : sequencerState === "unavailable" ? "Unavailable" : "Incarnation mismatch"}
                  </Badge>
                ) },
              ].map(({ label, value }) => (
                <div key={label} className="min-w-0 bg-background px-3 py-3">
                  <dt className="text-xs text-muted-foreground">{label}</dt>
                  <dd className="mt-1 truncate font-mono text-xs" title={typeof value === "string" ? value : undefined}>{value}</dd>
                </div>
              ))}
            </dl>
          </section>

          <section
            aria-labelledby="logservers-heading"
            className="overflow-hidden rounded-lg border bg-background/40"
          >
            <div className="border-b bg-muted/50 px-3 py-2.5">
              <h4 id="logservers-heading" className="text-xs font-semibold">LogServers</h4>
            </div>
            <Table aria-label="NativeLoglet LogServers">
              <TableHeader>
                <TableRow>
                  <TableHead>Process</TableHead>
                  <TableHead>Segment</TableHead>
                  <TableHead className="text-right">Local tail</TableHead>
                  <TableHead className="text-right">Trimmed below</TableHead>
                  <TableHead className="text-right">knownTail</TableHead>
                  <TableHead>Seal</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {logServerRows.map(({ processId, server, segment }) => (
                  <TableRow key={processId}>
                    <TableCell><ComponentIdentity nodeId={processId} /></TableCell>
                    <TableCell className="font-mono text-xs">{segment ?? "—"}</TableCell>
                    <TableCell className="text-right"><MetricValue name="local-tail" value={server?.local_tail ?? "—"} /></TableCell>
                    <TableCell className="text-right"><MetricValue name="trimmed-prefix" value={server?.trimmed_prefix ?? "—"} /></TableCell>
                    <TableCell className="text-right"><MetricValue name="server-known" value={server?.known_tail ?? "—"} /></TableCell>
                    <TableCell><Badge variant="outline">{server ? (server.sealed ? "Sealed" : "Open") : "—"}</Badge></TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </section>
        </CardContent>
      </CollapsibleCard>
    </>
  );
}

export function MetaStoreComponent({ deployment }: { deployment: DeploymentSnapshot }) {
  const metas = Object.values(deployment.nodes)
    .filter((node) => node.service === "meta")
    .sort((left, right) => left.node_id.localeCompare(right.node_id));

  return (
    <CollapsibleCard size="sm">
      <CardHeader className="border-b">
        <CardTitle><h3>MetaStore</h3></CardTitle>
        <CardAction><CardCollapseButton label="MetaStore" /></CardAction>
      </CardHeader>
      <CardContent className="p-0">
        <Table aria-label="MetaStore">
          <TableHeader>
            <TableRow>
              <TableHead>Process</TableHead>
              <TableHead className="text-right">Last applied</TableHead>
              <TableHead className="text-right">Chain version</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {metas.map((node) => {
              const state = node.metastore;
              return (
                <TableRow key={node.node_id}>
                  <TableCell><ComponentIdentity nodeId={node.node_id} /></TableCell>
                  <TableCell className="text-right"><MetricValue name="last-applied" value={state?.state.last_applied ?? "—"} /></TableCell>
                  <TableCell className="text-right"><MetricValue name="chain-version" value={state ? `v${state.state.state_machine.version}` : "—"} /></TableCell>
                </TableRow>
              );
            })}
          </TableBody>
        </Table>
      </CardContent>
    </CollapsibleCard>
  );
}
