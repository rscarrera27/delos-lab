import { render, screen, within } from "@testing-library/react";

import { labDeployment } from "../test/fixtures";
import { DatabaseComponents, MetaStoreComponent } from "./TopologyComponents";

describe("TopologyComponents", () => {
  it("keeps Application, NativeLoglet, and MetaStore in independent cards", () => {
    render(
      <>
        <DatabaseComponents deployment={labDeployment} />
        <MetaStoreComponent deployment={labDeployment} />
      </>,
    );

    expect(screen.getByRole("heading", { name: "Application", level: 3 })).toBeVisible();
    expect(screen.getByRole("heading", { name: "NativeLoglet", level: 3 })).toBeVisible();
    expect(screen.getByRole("heading", { name: "MetaStore", level: 3 })).toBeVisible();

    const application = screen.getByRole("table", { name: "Application" });
    expect(within(application).getAllByText("Replay lag")).toHaveLength(3);
    expect(within(application).getAllByText("checkTail")).toHaveLength(3);
    expect(within(application).getByRole("columnheader", { name: "Process" })).toBeVisible();
    expect(within(application).queryByRole("columnheader", { name: /Status/ })).not.toBeInTheDocument();

    const metaStore = screen.getByRole("table", { name: "MetaStore" });
    expect(within(metaStore).getByRole("columnheader", { name: "Process" })).toBeVisible();
    expect(within(metaStore).getByRole("columnheader", { name: "Last applied" })).toBeVisible();
    expect(within(metaStore).getByRole("columnheader", { name: "Chain version" })).toBeVisible();
    expect(screen.queryByRole("table", { name: "Paxos slots" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Restart process/ })).not.toBeInTheDocument();
  });

  it("separates the sequencer identity from LogServer state", () => {
    render(<DatabaseComponents deployment={labDeployment} />);

    const sequencer = screen.getByLabelText("NativeLoglet sequencer");
    const logServers = screen.getByRole("table", { name: "NativeLoglet LogServers" });
    expect(screen.getByRole("heading", { name: "Latest Segment Sequencer", level: 4 })).toBeVisible();
    expect(within(sequencer).getByText("Segment")).toBeVisible();
    expect(within(sequencer).getByText("Process")).toBeVisible();
    expect(within(sequencer).getByText("knownTail")).toBeVisible();
    expect(within(sequencer).getByText("5")).toBeVisible();
    expect(within(sequencer).getByText("Active")).toBeVisible();
    expect(within(logServers).getByRole("columnheader", { name: "Process" })).toBeVisible();
    expect(within(logServers).queryByRole("columnheader", { name: "Sequencer" })).not.toBeInTheDocument();
    expect(within(logServers).queryByRole("columnheader", { name: "Virtual tail" })).not.toBeInTheDocument();
    expect(within(logServers).getAllByRole("row")).toHaveLength(4);
    expect(within(logServers).getAllByText("segment-2")).toHaveLength(3);
    expect(within(logServers).queryByText("segment-1")).not.toBeInTheDocument();
  });

  it("shows a sequencer incarnation mismatch without calling it a stale role", () => {
    const configured = labDeployment.nodes["db-2"];
    const database = configured.database;
    if (database === null) {
      throw new Error("fixture db-2 must be a database node");
    }
    const restarted = {
      ...labDeployment,
      nodes: {
        ...labDeployment.nodes,
        "db-2": {
          ...configured,
          database: {
            ...database,
            process: { ...database.process, incarnation_id: "inc-restarted" },
          },
        },
      },
    };

    render(<DatabaseComponents deployment={restarted} />);

    const sequencer = screen.getByLabelText("NativeLoglet sequencer");
    expect(within(sequencer).getByText("Incarnation mismatch")).toBeVisible();
    expect(within(sequencer).queryByText("Stale incarnation")).not.toBeInTheDocument();
  });

  it("shows configured LogServer membership before local segment state exists", () => {
    const configured = labDeployment.nodes["db-3"];
    const database = configured.database;
    if (database === null) {
      throw new Error("fixture db-3 must be a database node");
    }
    const emptyLocalSegment = {
      ...labDeployment,
      nodes: {
        ...labDeployment.nodes,
        "db-3": {
          ...configured,
          database: {
            ...database,
            log_server: { segments: [] },
          },
        },
      },
    };

    render(<DatabaseComponents deployment={emptyLocalSegment} />);

    const logServers = screen.getByRole("table", { name: "NativeLoglet LogServers" });
    expect(within(logServers).getAllByText("segment-2")).toHaveLength(3);
  });
});
