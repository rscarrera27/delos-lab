import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import type { LabApi } from "../api";
import { labDeployment } from "../test/fixtures";
import { TopologyView } from "./TopologyView";


describe("TopologyView", () => {
  it("groups collapsible component and process cards by Database and MetaStore", async () => {
    const user = userEvent.setup();
    render(<TopologyView deployment={labDeployment} api={{} as LabApi} run={vi.fn()} pending={null} />);

    expect(screen.getByRole("heading", { name: "Database", level: 2 })).toBeVisible();
    expect(screen.getByRole("heading", { name: "MetaStore", level: 2 })).toBeVisible();
    expect(screen.getByRole("heading", { name: "Application", level: 3 })).toBeVisible();
    expect(screen.getByRole("heading", { name: "NativeLoglet", level: 3 })).toBeVisible();
    expect(screen.getByRole("heading", { name: "MetaStore", level: 3 })).toBeVisible();
    expect(screen.getAllByRole("heading", { name: "Process", level: 3 })).toHaveLength(2);
    expect(screen.getByRole("table", { name: "Application" })).toBeVisible();
    expect(screen.getByLabelText("NativeLoglet sequencer")).toBeVisible();
    expect(screen.getByRole("table", { name: "NativeLoglet LogServers" })).toBeVisible();
    expect(screen.getByRole("table", { name: "MetaStore" })).toBeVisible();
    expect(screen.getByRole("table", { name: "Database processes" })).toBeVisible();
    expect(screen.getByRole("table", { name: "MetaStore processes" })).toBeVisible();
    expect(screen.queryByRole("table", { name: "Paxos slots" })).not.toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: /접기$/ })).toHaveLength(5);

    await user.click(screen.getByRole("button", { name: "Application 접기" }));

    expect(screen.getByRole("heading", { name: "Application", level: 3 })).toBeVisible();
    expect(screen.getByRole("table", { name: "Application", hidden: true })).not.toBeVisible();
    expect(screen.getByRole("button", { name: "Application 펼치기" })).toHaveAttribute(
      "aria-expanded",
      "false",
    );
  });

  it("allows only pause and resume for MetaStore processes", async () => {
    const user = userEvent.setup();
    const api = { nodeAction: vi.fn().mockResolvedValue(undefined) } as unknown as LabApi;
    const run = vi.fn(async (_name: string, operation: () => Promise<unknown>) => {
      await operation();
    });
    render(<TopologyView deployment={labDeployment} api={api} run={run} pending={null} />);

    expect(screen.queryByRole("button", { name: "Kill process meta-1" })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Pause process meta-1" }));

    expect(api.nodeAction).toHaveBeenCalledWith("meta-1", "pause");
  });

  it("shortens long node IDs without changing lifecycle targets", async () => {
    const user = userEvent.setup();
    const longId = "633eb7690c14f995f38ca368888cdbfa539c452e3f4d9ab489c7af4f02d01abb";
    const node = {
      ...labDeployment.nodes["db-1"],
      node_id: longId,
    };
    const { "db-1": _removed, ...otherProcesses } = labDeployment.nodes;
    const deployment = {
      ...labDeployment,
      nodes: { ...otherProcesses, [longId]: node },
    };
    const api = { nodeAction: vi.fn().mockResolvedValue(undefined) } as unknown as LabApi;
    const run = vi.fn(async (_name: string, operation: () => Promise<unknown>) => {
      await operation();
    });

    render(<TopologyView deployment={deployment} api={api} run={run} pending={null} />);

    expect(screen.getAllByText("633eb7690c14…").length).toBeGreaterThan(0);
    await user.click(screen.getByRole("button", { name: `Kill process ${longId}` }));
    expect(api.nodeAction).toHaveBeenCalledWith(longId, "kill");
  });

  it("renders each logical component and process inventory as an independent card", () => {
    render(
      <TopologyView
        deployment={labDeployment}
        api={{} as LabApi}
        run={vi.fn()}
        pending={null}
      />,
    );

    const application = screen.getByRole("heading", { name: "Application", level: 3 });
    const nativeLoglet = screen.getByRole("heading", { name: "NativeLoglet", level: 3 });
    const metaStore = screen.getByRole("heading", { name: "MetaStore", level: 3 });
    const processes = screen.getAllByRole("heading", { name: "Process", level: 3 });
    const applicationCard = application.closest("[data-slot=card]");
    const nativeLogletCard = nativeLoglet.closest("[data-slot=card]");
    const metaStoreCard = metaStore.closest("[data-slot=card]");
    const processCards = processes.map((heading) => heading.closest("[data-slot=card]"));
    expect(applicationCard).not.toBe(nativeLogletCard);
    expect(nativeLogletCard).not.toBe(metaStoreCard);
    expect(metaStoreCard).not.toBe(applicationCard);
    expect(processCards[0]).not.toBe(processCards[1]);
    expect(processCards).not.toContain(applicationCard);
    expect(processCards).not.toContain(nativeLogletCard);
    expect(processCards).not.toContain(metaStoreCard);
  });
});
