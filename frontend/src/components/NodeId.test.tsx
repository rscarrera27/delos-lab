import { render, screen } from "@testing-library/react";

import { NodeId, shortNodeId } from "./NodeId";

describe("NodeId", () => {
  it("keeps short development identifiers unchanged", () => {
    expect(shortNodeId("db-1")).toBe("db-1");
  });

  it("shows twelve characters and preserves the full node identifier", () => {
    const id = "633eb7690c14f995f38ca368888cdbfa539c452e3f4d9ab489c7af4f02d01abb";
    render(<NodeId id={id} />);

    expect(screen.getByText("633eb7690c14…")).toHaveAttribute("title", id);
    expect(screen.queryByText(id)).not.toBeInTheDocument();
  });
});
