import type { LabApi } from "../api";
import type { DeploymentSnapshot, ProcessAction, ServiceName } from "../types";
import { NodeId } from "./NodeId";
import { StatusIndicator } from "./StatusIndicator";
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
import {
  Table,
  TableBody,
  TableCell,
  TableFooter,
  TableHead,
  TableHeader,
  TableRow,
} from "./ui/table";

const observationAge = (observedAt: number, collectedAt: number) => {
  const milliseconds = Math.max(0, Math.round((collectedAt - observedAt) * 1000));
  return milliseconds < 1000 ? `${milliseconds}ms 전` : `${(milliseconds / 1000).toFixed(1)}s 전`;
};

export function ProcessTable({
  deployment,
  service,
  api,
  run,
  pending,
}: {
  deployment: DeploymentSnapshot;
  service: ServiceName;
  api: LabApi;
  run: (name: string, operation: () => Promise<unknown>) => Promise<void>;
  pending: string | null;
}) {
  const nodes = Object.values(deployment.nodes)
    .filter((node) => node.service === service && node.lifecycle !== "removed")
    .sort((left, right) => left.node_id.localeCompare(right.node_id));
  const serviceLabel = service === "db" ? "Database" : "MetaStore";

  return (
    <CollapsibleCard size="sm">
      <CardHeader className="border-b">
        <CardTitle><h3>Process</h3></CardTitle>
        <CardAction><CardCollapseButton label={`${serviceLabel} Process`} /></CardAction>
      </CardHeader>
      <CardContent className="p-0">
        <Table aria-label={`${serviceLabel} processes`}>
          <TableHeader>
            <TableRow>
              <TableHead>Process</TableHead>
              <TableHead>Lifecycle</TableHead>
              <TableHead>Reachability</TableHead>
              <TableHead>Observed</TableHead>
              <TableHead className="text-right">Actions</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {nodes.map((node) => {
              const action = (name: ProcessAction) =>
                void run(`${name}-${node.node_id}`, () =>
                  api.nodeAction(node.node_id, name),
                ).catch(() => undefined);
              return (
                <TableRow key={node.node_id}>
                  <TableCell>
                    <div className="grid min-w-36 gap-1">
                      <NodeId id={node.node_id} className="text-xs font-semibold" />
                      {node.error && (
                        <small className="max-w-72 truncate text-destructive" title={node.error}>
                          {node.error}
                        </small>
                      )}
                    </div>
                  </TableCell>
                  <TableCell>
                    <Badge variant="outline">{node.lifecycle}</Badge>
                  </TableCell>
                  <TableCell>
                    <StatusIndicator
                      label={node.reachable ? "Reachable" : "Unreachable"}
                      tone={node.reachable ? "healthy" : "danger"}
                    />
                  </TableCell>
                  <TableCell className="font-mono text-xs">
                    {observationAge(node.observed_at, deployment.collected_at)}
                  </TableCell>
                  <TableCell>
                    <div className="flex justify-end gap-1">
                      <Button
                        aria-label={`Resume process ${node.node_id}`}
                        size="xs"
                        variant="outline"
                        disabled={pending !== null || node.lifecycle === "running"}
                        onClick={() => action("resume")}
                      >
                        Resume
                      </Button>
                      <Button
                        aria-label={`Pause process ${node.node_id}`}
                        size="xs"
                        variant="outline"
                        disabled={pending !== null || node.lifecycle !== "running"}
                        onClick={() => action("pause")}
                      >
                        Pause
                      </Button>
                      {service === "db" && (
                        <Button
                          aria-label={`Kill process ${node.node_id}`}
                          size="xs"
                          variant="destructive"
                          disabled={pending !== null}
                          onClick={() => action("kill")}
                        >
                          Kill
                        </Button>
                      )}
                    </div>
                  </TableCell>
                </TableRow>
              );
            })}
          </TableBody>
          {service === "db" && (
            <TableFooter>
              <TableRow>
                <TableCell colSpan={5} className="text-right">
                  <Button
                    aria-label="Add database process"
                    size="sm"
                    variant="outline"
                    disabled={pending !== null}
                    onClick={() =>
                      void run("add-database-process", () => api.addDatabaseNode()).catch(
                        () => undefined,
                      )
                    }
                  >
                    Add database process
                  </Button>
                </TableCell>
              </TableRow>
            </TableFooter>
          )}
        </Table>
      </CardContent>
    </CollapsibleCard>
  );
}
