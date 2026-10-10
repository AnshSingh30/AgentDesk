import { expect, test } from "@playwright/test";

/** Annotations + Service API key management on the seeded demo agent (needs `make seed`; no LLM calls). */
test("annotations and service API keys", async ({ page }) => {
  await page.goto("/login");
  await page.getByLabel("Email").fill("demo@example.com");
  await page.getByLabel("Password").fill("Demo-password-123!");
  await page.getByRole("button", { name: /sign in|log in/i }).click();
  await expect(page).toHaveURL(/\/o\/[^/]+/, { timeout: 30_000 });
  const org = page.url().match(/\/o\/([^/]+)/)![1];

  await page.goto(`/o/${org}/agents`);
  await page.getByRole("link", { name: "Northwind HR Buddy" }).first().click();
  await expect(page).toHaveURL(/\/agents\/[^/]+\//);
  const base = page.url().match(/(\/o\/[^/]+\/agents\/[^/]+)/)![1];

  // Annotations: add, see it listed, delete
  const question = `E2E question ${Date.now()}`;
  await page.goto(`${base}/annotations`);
  await page.getByLabel("Question").fill(question);
  await page.getByLabel("Answer").fill("E2E curated answer.");
  await page.getByRole("button", { name: "Add annotation" }).click();
  await expect(page.getByText(question)).toBeVisible();
  await expect(page.getByText("Used 0 times").first()).toBeVisible();
  const card = page
    .locator("div", { hasText: question })
    .filter({ has: page.getByLabel("Delete annotation") })
    .last();
  await card.getByLabel("Delete annotation").click();
  await page.getByRole("dialog").getByRole("button").last().click();
  await expect(page.getByText(question)).toHaveCount(0);

  // Service API: create a key (shown once), then revoke it
  await page.goto(`${base}/deploy`);
  await expect(page.getByRole("heading", { name: "Service API" })).toBeVisible();
  await page.getByLabel("Key name").fill("e2e-key");
  await page.getByRole("button", { name: "Create key" }).click();
  await expect(page.getByText(/won.t be shown again/)).toBeVisible();
  await expect(page.locator("code", { hasText: /^adk_/ })).toBeVisible();
  await page.getByRole("button", { name: "Revoke e2e-key" }).first().click();
  await page.getByRole("dialog").getByRole("button").last().click();
  await expect(page.getByText("API key revoked")).toBeVisible();
});
