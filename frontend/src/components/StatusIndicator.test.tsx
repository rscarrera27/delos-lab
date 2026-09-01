import { render, screen } from "@testing-library/react";

import { StatusIndicator } from "./StatusIndicator";

describe("StatusIndicator", () => {
  it("renders a compact accessible status dot without visible label text", () => {
    const { container } = render(
      <StatusIndicator compact label="running · reachable" tone="healthy" />,
    );

    expect(screen.getByRole("img", { name: "running · reachable" })).toHaveAttribute(
      "title",
      "running · reachable",
    );
    expect(container.querySelector(".bg-green-500")).toBeInTheDocument();
    expect(screen.queryByText("running · reachable")).not.toBeInTheDocument();
  });

  it("uses the danger tone for an unavailable node", () => {
    const { container } = render(
      <StatusIndicator compact label="exited · unreachable" tone="danger" />,
    );

    expect(container.querySelector(".bg-red-500")).toBeInTheDocument();
  });
});
