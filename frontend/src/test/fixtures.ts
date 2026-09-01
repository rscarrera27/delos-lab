import type {
  DatabaseNodeState,
  DeploymentSnapshot,
  MetaStoreNodeState,
  NodeObservation,
} from "../types";

const chain = {
  version: 2,
  chain: {
    segments: [
      {
        segment_id: "segment-1",
        virtual_start: 0,
        virtual_stop: 4,
        loglet: {
          kind: "native",
          version: 1,
          parameters: {
            storage_members: ["db-1", "db-2", "db-3"],
            sequencer_node: "db-1",
            sequencer_incarnation: "inc-1",
          },
        },
      },
      {
        segment_id: "segment-2",
        virtual_start: 4,
        virtual_stop: null,
        loglet: {
          kind: "native",
          version: 1,
          parameters: {
            storage_members: ["db-1", "db-2", "db-3"],
            sequencer_node: "db-2",
            sequencer_incarnation: "inc-2",
          },
        },
      },
    ],
  },
};

const node = (
  nodeId: string,
  state: MetaStoreNodeState | DatabaseNodeState,
): NodeObservation => ({
  node_id: nodeId,
  service: nodeId.startsWith("meta") ? "meta" : "db",
  lifecycle: "running",
  reachable: true,
  metastore: nodeId.startsWith("meta") ? state as MetaStoreNodeState : null,
  database: nodeId.startsWith("meta") ? null : state as DatabaseNodeState,
  error: null,
  observed_at: 1_787_999_999.75,
});

const nodes: Record<string, NodeObservation> = {
    "meta-1": node("meta-1", {
      node_id: "meta-1",
      state: {
        last_applied: 2,
        state_machine: chain,
      },
    }),
    "meta-2": node("meta-2", {
      node_id: "meta-2",
      state: {
        last_applied: 1,
        state_machine: { ...chain, version: 1 },
      },
    }),
    "meta-3": node("meta-3", {
      node_id: "meta-3",
      state: {
        last_applied: 1,
        state_machine: { ...chain, version: 1 },
      },
    }),
    "db-1": node("db-1", {
      node_id: "db-1",
      process: { status: "online", incarnation_id: "inc-1" },
      application: { applied_position: 3, values: { colour: "blue" }, request_count: 1 },
      virtual_log: {
        chain_version: 1,
        cached_chain: { ...chain, version: 1 },
        active_segment: "segment-2",
        active_virtual_start: 4,
        known_virtual_tail: 8,
      },
      native_loglet_client: {
        segment_id: "segment-2",
        known_tail: 4,
        last_check_tail: { tail: 4, sealed: false },
      },
      sequencer: null,
      log_server: {
        segments: [
          { segment_id: "segment-1", local_tail: 4, trimmed_prefix: 0, known_tail: 4, sealed: true },
          { segment_id: "segment-2", local_tail: 4, trimmed_prefix: 0, known_tail: 3, sealed: false },
        ],
      },
    }),
    "db-2": node("db-2", {
      node_id: "db-2",
      process: { status: "online", incarnation_id: "inc-2" },
      application: { applied_position: 4, values: { colour: "blue" }, request_count: 1 },
      virtual_log: {
        chain_version: 2,
        cached_chain: chain,
        active_segment: "segment-2",
        active_virtual_start: 4,
        known_virtual_tail: 9,
      },
      native_loglet_client: {
        segment_id: "segment-2",
        known_tail: 5,
        last_check_tail: { tail: 5, sealed: false },
      },
      sequencer: { segment_id: "segment-2", known_tail: 5 },
      log_server: {
        segments: [
          { segment_id: "segment-1", local_tail: 4, trimmed_prefix: 0, known_tail: 4, sealed: true },
          { segment_id: "segment-2", local_tail: 5, trimmed_prefix: 0, known_tail: 4, sealed: false },
        ],
      },
    }),
    "db-3": node("db-3", {
      node_id: "db-3",
      process: { status: "online", incarnation_id: "inc-3" },
      application: { applied_position: 4, values: { colour: "blue" }, request_count: 1 },
      virtual_log: {
        chain_version: 2,
        cached_chain: chain,
        active_segment: "segment-2",
        active_virtual_start: 4,
        known_virtual_tail: 9,
      },
      native_loglet_client: {
        segment_id: "segment-2",
        known_tail: 5,
        last_check_tail: { tail: 5, sealed: true },
      },
      sequencer: null,
      log_server: {
        segments: [
          { segment_id: "segment-1", local_tail: 4, trimmed_prefix: 0, known_tail: 4, sealed: true },
          { segment_id: "segment-2", local_tail: 5, trimmed_prefix: 0, known_tail: 5, sealed: true },
        ],
      },
    }),
};

export const labDeployment: DeploymentSnapshot = {
  services: {
    meta: { name: "meta", configured: 3, running: 3, reachable: 3 },
    db: { name: "db", configured: 3, running: 3, reachable: 3 },
  },
  nodes,
  collected_at: 1_788_000_000,
};
