import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import {
  CardAction,
  CardCollapseButton,
  CardContent,
  CardHeader,
  CardTitle,
  CollapsibleCard,
} from "@/components/ui/card";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";

test("shadcn primitives expose accessible alert, button, and tab semantics", () => {
  render(
    <>
      <Alert>
        <AlertTitle>관찰 경고</AlertTitle>
        <AlertDescription>연결 실패</AlertDescription>
      </Alert>
      <Button>초기화</Button>
      <Tabs defaultValue="topology">
        <TabsList aria-label="실험실 작업공간">
          <TabsTrigger value="topology">Topology</TabsTrigger>
          <TabsTrigger value="log">Virtual Log</TabsTrigger>
        </TabsList>
        <TabsContent value="topology">Topology panel</TabsContent>
        <TabsContent value="log">Log panel</TabsContent>
      </Tabs>
    </>,
  );

  expect(screen.getByRole("alert")).toHaveTextContent("연결 실패");
  expect(screen.getByRole("button", { name: "초기화" })).toBeEnabled();
  expect(screen.getByRole("tab", { name: "Topology" })).toHaveAttribute("aria-selected", "true");
  expect(screen.getByRole("tabpanel")).toHaveAccessibleName("Topology");
});

test("collapsible cards preserve their content while hiding it accessibly", async () => {
  const user = userEvent.setup();
  render(
    <CollapsibleCard>
      <CardHeader className="border-b">
        <CardTitle>Example</CardTitle>
        <CardAction><CardCollapseButton label="Example" /></CardAction>
      </CardHeader>
      <CardContent>Preserved value</CardContent>
    </CollapsibleCard>,
  );

  const collapse = screen.getByRole("button", { name: "Example 접기" });
  const content = screen.getByText("Preserved value");
  expect(collapse).toHaveAttribute("aria-expanded", "true");
  expect(content).toBeVisible();

  await user.click(collapse);

  expect(screen.getByRole("button", { name: "Example 펼치기" })).toHaveAttribute(
    "aria-expanded",
    "false",
  );
  expect(content).not.toBeVisible();
  expect(content).toBeInTheDocument();
});
