import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import type { LabApi } from "../api";
import { labDeployment } from "../test/fixtures";
import { ProcessTable } from "./ProcessTable";

describe("ProcessTable", () => {
  it("separates Database and MetaStore process inventories", () => {
    const props = {
      deployment: labDeployment,
      api: {} as LabApi,
      run: vi.fn(),
      pending: null,
    };
    render(
      <>
        <ProcessTable {...props} service="db" />
        <ProcessTable {...props} service="meta" />
      </>,
    );

    const databases = screen.getByRole("table", { name: "Database processes" });
    const metas = screen.getByRole("table", { name: "MetaStore processes" });
    expect(within(databases).getAllByRole("row")).toHaveLength(5);
    expect(within(databases).getByText("db-1")).toBeVisible();
    expect(within(databases).queryByText("meta-1")).not.toBeInTheDocument();
    expect(within(metas).getAllByRole("row")).toHaveLength(4);
    expect(within(metas).getByText("meta-1")).toBeVisible();
    expect(within(metas).queryByText("db-1")).not.toBeInTheDocument();
    expect(within(databases).getAllByRole("button", { name: /Kill process/ })).toHaveLength(3);
    expect(within(metas).queryByRole("button", { name: /Kill process/ })).not.toBeInTheDocument();
  });

  it("uses the full node ID for lifecycle actions", async () => {
    const user = userEvent.setup();
    const fullProcessId = "633eb7690c14f995f38ca368888cdbfa539c452e3f4d9ab489c7af4f02d01abb";
    const node = {
      ...labDeployment.nodes["db-1"],
      node_id: fullProcessId,
    };
    const { "db-1": _removed, ...others } = labDeployment.nodes;
    const deployment = {
      ...labDeployment,
      nodes: { ...others, [fullProcessId]: node },
    };
    const api = { nodeAction: vi.fn().mockResolvedValue(undefined) } as unknown as LabApi;
    const run = vi.fn(async (_name: string, operation: () => Promise<unknown>) => {
      await operation();
    });

    render(
      <ProcessTable
        deployment={deployment}
        service="db"
        api={api}
        run={run}
        pending={null}
      />,
    );

    expect(screen.getByText("633eb7690c14…")).toBeVisible();
    const kill = screen.getByRole("button", { name: `Kill process ${fullProcessId}` });
    expect(kill).toHaveAttribute("data-variant", "destructive");
    await user.click(kill);
    expect(api.nodeAction).toHaveBeenCalledWith(fullProcessId, "kill");
  });

  it("keeps an unavailable node manageable and shows its error", () => {
    const deployment = structuredClone(labDeployment);
    deployment.nodes["db-1"].lifecycle = "exited";
    deployment.nodes["db-1"].reachable = false;
    deployment.nodes["db-1"].error = "connection refused";

    render(
      <ProcessTable
        deployment={deployment}
        service="db"
        api={{} as LabApi}
        run={vi.fn()}
        pending={null}
      />,
    );

    expect(screen.getByText("exited")).toBeVisible();
    expect(screen.getByText("Unreachable")).toBeVisible();
    expect(screen.getByText("connection refused")).toBeVisible();
    expect(screen.getByRole("button", { name: "Resume process db-1" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Pause process db-1" })).toBeDisabled();
  });

  it("adds a replacement database process from the table footer", async () => {
    const user = userEvent.setup();
    const api = { addDatabaseNode: vi.fn().mockResolvedValue({ node_id: "db-rand0" }) } as unknown as LabApi;
    const run = vi.fn(async (_name: string, operation: () => Promise<unknown>) => {
      await operation();
    });
    render(
      <ProcessTable
        deployment={labDeployment}
        service="db"
        api={api}
        run={run}
        pending={null}
      />,
    );

    await user.click(screen.getByRole("button", { name: "Add database process" }));

    expect(run).toHaveBeenCalledWith("add-database-process", expect.any(Function));
    expect(api.addDatabaseNode).toHaveBeenCalledOnce();
  });

  it("hides a killed process from the process inventory", () => {
    const deployment = structuredClone(labDeployment);
    deployment.nodes["meta-2"].lifecycle = "removed";

    render(
      <ProcessTable
        deployment={deployment}
        service="meta"
        api={{} as LabApi}
        run={vi.fn()}
        pending={null}
      />,
    );

    expect(screen.queryByText("meta-2")).not.toBeInTheDocument();
    expect(screen.getByText("meta-1")).toBeVisible();
  });
});
