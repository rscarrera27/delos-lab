import { expect, test } from "@playwright/test";

test("the local lab exposes three focused workspaces", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByText(/\d+\/\d+ reachable/)).toBeVisible();
  await expect(page.getByRole("tab")).toHaveCount(3);
  await expect(page.getByRole("tab", { name: "Topology" })).toBeVisible();
  await expect(page.getByRole("tab", { name: "Virtual Log" })).toBeVisible();
  await expect(page.getByRole("tab", { name: "KV Console" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Database", level: 2 })).toBeVisible();
  await expect(page.getByRole("heading", { name: "MetaStore", level: 2 })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Application", level: 3 })).toBeVisible();
  await expect(page.getByRole("heading", { name: "NativeLoglet", level: 3 })).toBeVisible();
  await expect(page.getByRole("heading", { name: "MetaStore", level: 3 })).toBeVisible();
  await expect(page.getByRole("table", { name: "Database processes" })).toBeVisible();
  await expect(page.getByRole("table", { name: "MetaStore processes" })).toBeVisible();
});

test("the browser writes through a selected DB and observes the VirtualLog", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByText(/\d+\/\d+ reachable/)).toBeVisible();

  await page.getByRole("tab", { name: "KV Console" }).click();
  await page.getByRole("combobox", { name: "연산" }).click();
  await page.getByRole("option", { name: "PUT" }).click();
  await page.getByLabel("키").fill("browser-e2e");
  await page.getByLabel("값").fill("virtual-consensus");
  await page.getByRole("button", { name: "명령 실행" }).click();
  await expect(page.getByText("APPLIED", { exact: true })).toBeVisible();
  await expect(page.locator(".result-value")).toHaveText("virtual-consensus");

  await page.getByRole("combobox", { name: "연산" }).click();
  await page.getByRole("option", { name: "GET" }).click();
  await page.getByRole("button", { name: "명령 실행" }).click();
  await expect(page.locator(".result-value")).toHaveText("virtual-consensus");

  await page.getByRole("tab", { name: "Virtual Log" }).click();
  await expect(page.getByRole("heading", { name: "Virtual Log" })).toBeVisible();
  await expect(page.getByText(/Chain v[1-9]\d*/)).toBeVisible();
  await expect(page.getByText("Segments", { exact: true })).toBeVisible();
  await expect(page.getByRole("table", { name: "Observed physical entries" })).toBeVisible();
  await expect(page.getByText("PUT", { exact: true }).first()).toBeVisible();
});

test("the browser adds a database node that joins the next NativeLoglet segment", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByText(/\d+\/\d+ reachable/)).toBeVisible();

  const processTable = page.getByRole("table", { name: "Database processes" });
  const processIds = processTable.locator('tbody span[title^="db-"]');
  const before = new Set(await processIds.evaluateAll((nodes) => nodes.map((node) => node.title)));
  await page.getByRole("button", { name: "Add database process" }).click();

  await expect(page.getByText("7/7 reachable")).toBeVisible();
  await expect(processIds).toHaveCount(before.size + 1);
  const after = await processIds.evaluateAll((nodes) => nodes.map((node) => node.title));
  const addedId = after.find((nodeId) => !before.has(nodeId));
  expect(addedId).toMatch(/^db-[a-z0-9]{5}$/);

  await page.getByRole("tab", { name: "Virtual Log" }).click();
  const activeSegment = page.getByRole("button", {
    name: new RegExp(`members.*${addedId}.*Open`),
  });
  await expect(activeSegment).toBeVisible();
});
