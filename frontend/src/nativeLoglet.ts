import type { LogSegment } from "./types";

export interface NativeLogletConfiguration {
  storageMembers: string[];
  sequencerNode: string;
  sequencerIncarnation: string;
}

export function nativeLogletConfiguration(
  segment: LogSegment,
): NativeLogletConfiguration | null {
  const parameters = segment.loglet.parameters;
  const members = parameters.storage_members;
  const sequencerNode = parameters.sequencer_node;
  const sequencerIncarnation = parameters.sequencer_incarnation;
  if (
    segment.loglet.kind !== "native" ||
    segment.loglet.version !== 1 ||
    !Array.isArray(members) ||
    !members.every((member): member is string => typeof member === "string") ||
    typeof sequencerNode !== "string" ||
    typeof sequencerIncarnation !== "string"
  ) {
    return null;
  }
  return {
    storageMembers: members,
    sequencerNode,
    sequencerIncarnation,
  };
}
