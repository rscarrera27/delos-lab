export type KvValue = string | number;

export interface LogSegment {
  segment_id: string;
  virtual_start: number;
  virtual_stop: number | null;
  loglet: {
    kind: string;
    version: number;
    parameters: Record<string, unknown>;
  };
}

export interface VersionedLogChain {
  version: number;
  chain: { segments: LogSegment[] } | null;
}

export interface MetaStoreNodeState {
  node_id: string;
  state: {
    last_applied: number;
    state_machine: VersionedLogChain;
  };
}

export interface DatabaseNodeState {
  node_id: string;
  process: {
    status: "online";
    incarnation_id: string;
  };
  application: {
    applied_position: number | null;
    values: Record<string, KvValue>;
    request_count: number;
  };
  virtual_log: {
    chain_version: number | null;
    cached_chain: VersionedLogChain | null;
    active_segment: string | null;
    active_virtual_start: number | null;
    known_virtual_tail: number | null;
  };
  native_loglet_client: NativeLogletClientState | null;
  sequencer: NativeSequencerState | null;
  log_server: { segments: LogServerState[] };
}

export interface NativeLogletClientState {
  segment_id: string;
  known_tail: number;
  last_check_tail: {
    tail: number;
    sealed: boolean;
  } | null;
}

export interface NativeSequencerState {
  segment_id: string;
  known_tail: number;
}

export interface LogServerState {
  segment_id: string;
  local_tail: number;
  trimmed_prefix: number;
  known_tail: number;
  sealed: boolean;
}

export interface LogEntry {
  segment_id: string;
  position: number;
  command_id: string;
  payload: string;
}

export type ServiceName = "meta" | "db";
export type ProcessLifecycle = "running" | "paused" | "exited" | "removed";

export interface ServiceObservation {
  name: ServiceName;
  configured: number;
  running: number;
  reachable: number;
}

export interface NodeObservation {
  node_id: string;
  service: ServiceName;
  lifecycle: ProcessLifecycle;
  reachable: boolean;
  metastore: MetaStoreNodeState | null;
  database: DatabaseNodeState | null;
  error: string | null;
  observed_at: number;
}

export interface DeploymentSnapshot {
  services: Record<ServiceName, ServiceObservation>;
  nodes: Record<string, NodeObservation>;
  collected_at: number;
}

export interface KvResult {
  code?: "APPLIED" | "NOT_FOUND" | "CAS_MISMATCH" | "TYPE_MISMATCH";
  key?: string;
  value: KvValue | null;
}

export interface RequestIdentity {
  clientId: string;
  requestId: string;
}

export type ProcessAction = "resume" | "pause" | "kill";
