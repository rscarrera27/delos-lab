import { render, screen } from "@testing-library/react";

import type { LabApi } from "../api";
import { labDeployment } from "../test/fixtures";
import type { DatabaseNodeState, MetaStoreNodeState } from "../types";
import { VirtualLogView } from "./VirtualLogView";

const longNodeId = "633eb7690c14f995f38ca368888cdbfa539c452e3f4d9ab489c7af4f02d01abb";

const deploymentWithLongNodeId = () => {
  const deployment = structuredClone(labDeployment);
  const dbOne = deployment.nodes["db-1"];
  delete deployment.nodes["db-1"];
  dbOne.node_id = longNodeId;
  (dbOne.database as DatabaseNodeState).node_id = longNodeId;
  deployment.nodes[longNodeId] = dbOne;

  for (const node of Object.values(deployment.nodes)) {
    const chain = node.service === "meta"
      ? (node.metastore as MetaStoreNodeState).state.state_machine
      : (node.database as DatabaseNodeState).virtual_log.cached_chain;
    for (const segment of chain?.chain?.segments ?? []) {
      const members = segment.loglet.parameters.storage_members as string[];
      segment.loglet.parameters.storage_members = members.map((id) =>
        id === "db-1" ? longNodeId : id,
      );
      if (segment.loglet.parameters.sequencer_node === "db-1") {
        segment.loglet.parameters.sequencer_node = longNodeId;
      }
    }
  }
  return deployment;
};

const committedEntry = {
  segment_id: "segment-2",
  position: 0,
  command_id: "browser/1",
  payload: JSON.stringify({
    schema_version: 1,
    client_id: "browser",
    request_id: "1",
    operation: { kind: "put", key: "colour", value: "blue" },
  }),
};

const opaqueEntry = {
  segment_id: "segment-2",
  position: 1,
  command_id: "legacy/2",
  payload: "opaque-payload",
};

const api = {
  logEntries: vi
    .fn()
    .mockResolvedValueOnce([])
    .mockResolvedValueOnce([committedEntry, opaqueEntry])
    .mockResolvedValue([]),
} as unknown as LabApi;

describe("VirtualLogView", () => {
  it("renders abstract chain metadata and decoded log contents", async () => {
    render(<VirtualLogView deployment={labDeployment} api={api} />);

    expect(screen.getByRole("heading", { name: "Virtual Log" })).toBeInTheDocument();
    expect(screen.getByText("Chain v2")).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: /접기$/ })).toHaveLength(2);
    expect(screen.getByText("[0, 4)")).toBeInTheDocument();
    expect(screen.getByText("[4, ∞)")).toBeInTheDocument();
    expect(screen.queryByRole("table", { name: "Application cursors" })).not.toBeInTheDocument();
    expect(screen.queryByRole("table", { name: "LogServer cursors" })).not.toBeInTheDocument();
    expect(screen.getByText("Segments")).toBeVisible();
    expect(await screen.findByRole("table", { name: "Observed physical entries" })).toBeInTheDocument();
    expect(await screen.findByText("PUT")).toBeInTheDocument();
    expect(screen.getByText("colour")).toBeInTheDocument();
    expect(screen.getByText("blue")).toBeInTheDocument();
    expect(screen.getByText("legacy/2")).toBeInTheDocument();
    expect(screen.getByText("RAW")).toBeInTheDocument();
    expect(screen.getByText("opaque-payload")).toBeInTheDocument();
    expect(screen.getByText("V4 · segment-2:0")).toBeInTheDocument();
    const memberLists = screen.getAllByText("members").map((label) => label.parentElement);
    expect(memberLists).toHaveLength(2);
    for (const memberList of memberLists) {
      expect(memberList).toHaveTextContent("membersdb-1·db-2·db-3");
    }
    expect(screen.getByText("native@1 · inc inc-2")).toBeInTheDocument();
    expect(api.logEntries).toHaveBeenNthCalledWith(
      1,
      "db-1",
      "segment-2",
      0,
      200,
    );
    expect(api.logEntries).toHaveBeenCalledTimes(3);
  });

  it("reports partial physical observation without inferring holes", async () => {
    const partialApi = {
      logEntries: vi
        .fn()
        .mockRejectedValueOnce(new Error("db-1 unavailable"))
        .mockResolvedValueOnce([])
        .mockRejectedValueOnce(new Error("db-3 unavailable")),
    } as unknown as LabApi;

    render(<VirtualLogView deployment={labDeployment} api={partialApi} />);

    expect(await screen.findByText(/부분 관찰/)).toBeInTheDocument();
    expect(screen.queryByText("HOLE")).not.toBeInTheDocument();
    expect(screen.queryByText("UNKNOWN")).not.toBeInTheDocument();
  });

  it("shortens long node IDs in abstract segment metadata", () => {
    const emptyApi = {
      logEntries: vi.fn(() => new Promise<never>(() => undefined)),
    } as unknown as LabApi;

    render(<VirtualLogView deployment={deploymentWithLongNodeId()} api={emptyApi} />);

    expect(screen.getAllByText("633eb7690c14…").length).toBeGreaterThan(0);
    expect(screen.queryByText(longNodeId, { exact: true })).not.toBeInTheDocument();
  });
});
