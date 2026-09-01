import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import type { LabApi } from "../api";
import { labDeployment } from "../test/fixtures";
import { KvConsole } from "./KvConsole";

const longNodeId = "633eb7690c14f995f38ca368888cdbfa539c452e3f4d9ab489c7af4f02d01abb";

const deploymentWithLongNodeId = () => {
  const deployment = structuredClone(labDeployment);
  const { "db-1": dbOne, ...otherProcesses } = deployment.nodes;
  dbOne.node_id = longNodeId;
  return {
    ...deployment,
    nodes: { [longNodeId]: dbOne, ...otherProcesses },
  };
};

describe("KvConsole", () => {
  it("submits a PUT directly to the selected DB and shows the result", async () => {
    const user = userEvent.setup();
    const api = {
      kvPut: vi.fn().mockResolvedValue({ code: "APPLIED", value: "green" }),
    } as unknown as LabApi;
    const onApplied = vi.fn().mockResolvedValue(undefined);
    render(<KvConsole deployment={labDeployment} api={api} onApplied={onApplied} />);

    expect(screen.getByRole("heading", { name: "KV Console" })).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: /접기$/ })).toHaveLength(3);
    expect(screen.queryByText("전순서 로그 위의 KV")).not.toBeInTheDocument();
    await user.click(screen.getByRole("combobox", { name: "연산" }));
    await user.click(screen.getByRole("option", { name: "PUT" }));
    await user.click(screen.getByRole("combobox", { name: "대상 DB" }));
    await user.click(screen.getByRole("option", { name: /db-2/ }));
    await user.clear(screen.getByLabelText("Client ID"));
    await user.type(screen.getByLabelText("Client ID"), "student-a");
    await user.clear(screen.getByLabelText("Request ID"));
    await user.type(screen.getByLabelText("Request ID"), "retry-42");
    await user.type(screen.getByLabelText("키"), "colour");
    await user.type(screen.getByLabelText("값"), "green");
    await user.click(screen.getByRole("button", { name: "명령 실행" }));

    await waitFor(() => expect(api.kvPut).toHaveBeenCalledWith(
      "db-2",
      "colour",
      "green",
      { clientId: "student-a", requestId: "retry-42" },
    ));
    expect(await screen.findByText("APPLIED")).toBeInTheDocument();
    expect(screen.getByText("green", { selector: ".result-value" })).toBeInTheDocument();
    expect(onApplied).toHaveBeenCalled();
  });

  it("uses null as a blank compare-and-set expectation", async () => {
    const user = userEvent.setup();
    const api = {
      kvCompareAndSet: vi.fn().mockResolvedValue({ code: "APPLIED", value: 7 }),
    } as unknown as LabApi;
    render(<KvConsole deployment={labDeployment} api={api} onApplied={vi.fn()} />);

    await user.click(screen.getByRole("combobox", { name: "연산" }));
    await user.click(screen.getByRole("option", { name: "COMPARE & SET" }));
    await user.type(screen.getByLabelText("키"), "count");
    await user.type(screen.getByLabelText("새 값"), "7");
    await user.click(screen.getByRole("button", { name: "명령 실행" }));

    await waitFor(() => expect(api.kvCompareAndSet).toHaveBeenCalledWith(
      expect.any(String), "count", null, 7, expect.any(Object),
    ));
  });

  it("shortens long node IDs while targeting the full node", async () => {
    const user = userEvent.setup();
    const deployment = deploymentWithLongNodeId();
    const api = { kvGet: vi.fn().mockResolvedValue({ value: "blue" }) } as unknown as LabApi;

    render(<KvConsole deployment={deployment} api={api} onApplied={vi.fn()} />);

    await user.click(screen.getByRole("combobox", { name: "대상 DB" }));
    await user.click(screen.getByRole("option", { name: /633eb7690c14…/ }));
    await user.type(screen.getByLabelText("키"), "colour");
    await user.click(screen.getByRole("button", { name: "명령 실행" }));

    await waitFor(() => expect(api.kvGet).toHaveBeenCalledWith(longNodeId, "colour"));
    expect(screen.getAllByTitle(longNodeId).length).toBeGreaterThan(1);
    expect(screen.queryByText(longNodeId, { exact: true })).not.toBeInTheDocument();
  });
});
