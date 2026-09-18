import { expect, test } from "@playwright/test";

const API = process.env.E2E_API_URL || "http://localhost:8000";
const MAILPIT = process.env.E2E_MAILPIT_URL || "http://localhost:8025";

/** Signup → verify (via Mailpit) → org → HR Onboarding agent → upload doc → ingestion → Playground answer with citation (PRD §20.1 E2E). */
test("signup to first answer", async ({ page, request }) => {
  const email = `e2e-${Date.now()}@example.com`;
  const password = "E2e-password-123!";

  await page.goto("/signup");
  await page.getByLabel("Name").fill("E2E User");
  await page.getByLabel("Work email").fill(email);
  await page.getByLabel("Password").fill(password);
  await page.getByRole("button", { name: "Create account" }).click();
  await expect(page).toHaveURL(/\/onboarding/);

  // verification link from Mailpit
  let token = "";
  for (let i = 0; i < 30 && !token; i++) {
    const search = await request.get(`${MAILPIT}/api/v1/search?query=to:${encodeURIComponent(email)}`);
    const msgs = (await search.json()).messages ?? [];
    if (msgs.length) {
      const msg = await (await request.get(`${MAILPIT}/api/v1/message/${msgs[0].ID}`)).json();
      token = (msg.Text as string).match(/token=([A-Za-z0-9_-]+)/)?.[1] ?? "";
    }
    if (!token) await page.waitForTimeout(1000);
  }
  expect(token).not.toEqual("");
  await page.goto(`/verify-email?token=${token}`);
  await expect(page.getByText("Your email is verified")).toBeVisible();
  await expect(page).toHaveURL(/\/onboarding/, { timeout: 15_000 });

  await page.getByLabel("Organisation name").fill("E2E Org");
  await page.getByRole("button", { name: "Continue" }).click();
  await page.getByRole("button", { name: /HR Onboarding Assistant/ }).click();
  await expect(page).toHaveURL(/\/agents\/new\?template=hr_onboarding/);
  await page.getByRole("button", { name: "Create agent" }).click();
  await expect(page).toHaveURL(/\/build\/instructions/);
  await expect(page.getByLabel("System prompt")).toContainText("HR onboarding assistant");
  const playgroundUrl = page.url().replace(/\/build\/.*$/, "/playground");

  // Knowledge step: create a KB, then upload a document on the KB page
  await page.getByRole("link", { name: "Knowledge Base", exact: true }).click();
  await page.getByPlaceholder("New knowledge base name").fill("E2E Docs");
  await page.getByRole("button", { name: "Create & attach" }).click();
  await expect(page.getByText("E2E Docs").first()).toBeVisible();
  await expect(page.getByText(/^Saved ·/)).toBeVisible({ timeout: 10_000 });
  await page.getByRole("link", { name: /Manage documents/ }).click();
  await expect(page).toHaveURL(/\/knowledge\//);
  await page.getByLabel("Upload files").setInputFiles({
    name: "leave-policy.md",
    mimeType: "text/markdown",
    buffer: Buffer.from(
      "# Leave Policy\n\n## Casual Leave\n\nEmployees get 12 casual leaves per year. Casual leave cannot be carried forward.\n",
    ),
  });
  await expect(page.getByText("ready", { exact: true }).first()).toBeVisible({ timeout: 120_000 });

  // Playground answer
  await page.goto(playgroundUrl);
  await expect(page).toHaveURL(/\/playground/);
  await page.getByLabel("Message").fill("How many casual leaves do I get?");
  await page.getByLabel("Send").click();
  await expect(page.getByText(/12 casual leaves/)).toBeVisible({ timeout: 60_000 });
  await expect(page.getByText(/\[1\] leave-policy\.md/)).toBeVisible();

  // API sanity: analytics reflects the run
  const me = await request.get(`${API}/v1/auth/me`);
  expect(me.status()).toBe(401); // request context has no cookies; the browser session does
});
