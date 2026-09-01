import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import type { LabApi } from "./api";
import { App } from "./App";
import { labDeployment } from "./test/fixtures";

const api = () =>
  ({
    deployment: vi.fn().mockResolvedValue(labDeployment),
    resetCluster: vi.fn().mockResolvedValue(undefined),
  }) as unknown as LabApi;

describe("App topology workspace", () => {
  it("renders a compact shell without explanatory slogans", async () => {
    render(<App api={api()} pollInterval={60_000} />);

    expect(await screen.findByRole("heading", { name: "Topology" })).toBeInTheDocument();
    expect(screen.getByText("Delos Lab")).toBeInTheDocument();
    expect(screen.getByText("6/6 reachable")).toBeInTheDocument();
    expect(screen.queryByText("분리된 합의, 수렴된 데이터 경로")).not.toBeInTheDocument();
    expect(screen.queryByText("Virtual Consensus Lab")).not.toBeInTheDocument();
    const tabList = screen.getByRole("tablist", { name: "실험실 작업공간" });
    expect(tabList).toHaveClass("grid", "grid-cols-3", "w-full");
    expect(tabList.parentElement).not.toHaveClass("overflow-x-auto");
  });

  it("resets the lab after confirmation and keeps tab semantics accessible", async () => {
    const labApi = api();
    const user = userEvent.setup();
    vi.spyOn(window, "confirm").mockReturnValue(true);
    render(<App api={labApi} pollInterval={60_000} />);
    await screen.findByRole("heading", { name: "Topology" });

    expect(screen.queryByRole("button", { name: "전체 중지" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "클러스터 재시작" })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "초기화" }));
    expect(window.confirm).toHaveBeenCalledWith("모든 데이터와 프로세스 상태를 초기화할까요?");
    await waitFor(() => expect(labApi.resetCluster).toHaveBeenCalledOnce());

    const logTab = screen.getByRole("tab", { name: "Virtual Log" });
    await user.click(logTab);
    expect(logTab).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("tabpanel")).toHaveAccessibleName("Virtual Log");

    await user.keyboard("{ArrowRight}");
    const kvTab = screen.getByRole("tab", { name: "KV Console" });
    expect(kvTab).toHaveFocus();
    expect(kvTab).toHaveAttribute("aria-selected", "true");
    expect(screen.queryByRole("tab", { name: "Faults" })).not.toBeInTheDocument();
    expect(screen.queryByRole("tab", { name: "Timeline" })).not.toBeInTheDocument();
  });
});
