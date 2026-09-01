import type { LabApi } from "../api";
import type { DeploymentSnapshot } from "../types";
import { ProcessTable } from "./ProcessTable";
import { DatabaseComponents, MetaStoreComponent } from "./TopologyComponents";

export function TopologyView({
  deployment,
  api,
  run,
  pending,
}: {
  deployment: DeploymentSnapshot;
  api: LabApi;
  run: (name: string, operation: () => Promise<unknown>) => Promise<void>;
  pending: string | null;
}) {
  return (
    <div className="space-y-6 py-5">
      <h1 className="text-lg font-semibold tracking-tight">Topology</h1>

      <section aria-labelledby="database-heading" className="space-y-4">
        <h2 id="database-heading" className="text-base font-semibold tracking-tight">
          Database
        </h2>
        <DatabaseComponents deployment={deployment} />
        <ProcessTable
          deployment={deployment}
          service="db"
          api={api}
          run={run}
          pending={pending}
        />
      </section>

      <section aria-labelledby="metastore-heading" className="space-y-4">
        <h2 id="metastore-heading" className="text-base font-semibold tracking-tight">
          MetaStore
        </h2>
        <MetaStoreComponent deployment={deployment} />
        <ProcessTable
          deployment={deployment}
          service="meta"
          api={api}
          run={run}
          pending={pending}
        />
      </section>
    </div>
  );
}
