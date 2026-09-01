import type { DeploymentSnapshot, VersionedLogChain } from "./types";

export function latestObservedChain(deployment: DeploymentSnapshot): VersionedLogChain | null {
  return (
    Object.values(deployment.nodes)
      .filter((node) => node.service === "meta" && node.reachable && node.metastore !== null)
      .map((node) => node.metastore!.state.state_machine)
      .sort((left, right) => right.version - left.version)[0] ?? null
  );
}
