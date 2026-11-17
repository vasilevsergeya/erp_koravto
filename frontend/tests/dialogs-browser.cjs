const { chromium } = require("playwright"),
  assert = require("node:assert/strict"),
  fs = require("node:fs");
(async () => {
  const browser = await chromium.launch({
    ...(process.env.CHROMIUM_PATH
      ? { executablePath: process.env.CHROMIUM_PATH }
      : {}),
    headless: true,
    args: ["--no-sandbox"],
  });
  const page = await browser.newPage({
    viewport: { width: 1366, height: 768 },
    deviceScaleFactor: 1,
  });
  await page.addInitScript(() => {
    window.WebSocket = class {
      addEventListener() {}
      close() {}
      send() {}
    };
  });
  const errors = [];
  page.on("pageerror", (e) => errors.push(String(e)));
  const duplicateKeys = [];
  page.on("console", (message) => {
    if (message.text().includes("Encountered two children with the same key"))
      duplicateKeys.push(message.text());
  });
  const dialogs = Array.from({ length: 142 }, (_, i) => ({
    id: "dialog-" + i,
    name: "Клиент " + (i + 1),
    display_name: "Клиент " + (i + 1),
    phone: "+1202555" + String(i).padStart(4, "0"),
    customer_id: "customer-1",
    channel_id: "channel-1",
    state: "waiting_us",
    updated_at: new Date(Date.now() - i * 3600000).toISOString(),
    order_numbers: [],
    unread_count: 1,
    inbound_seq: 1,
    last_message: i % 4 ? "Уточните статус заказа." : "",
    last_has_attachment: i % 4 === 0,
    has_ai_draft: i % 3 === 0,
    needs_human: i % 9 === 0,
  }));
  const profile = {
    id: "customer-1",
    name: "Демонстрационный клиент",
    display_name: "Демонстрационный клиент",
    tags: [],
    conversation_count: 1,
    open_tasks: 0,
    version: 1,
    translation_client_language: "ar",
    translation_manager_language: "en",
    language: "ar",
    translate_incoming: true,
    translate_outgoing: true,
  };
  const msgs = [
    {
      id: "message-1",
      direction: "in",
      author_type: "client",
      text: "hello",
      kind: "text",
      status: "inbound",
      version: 1,
      external: true,
      created_at: new Date(Date.now() - 10000).toISOString(),
    },
    {
      id: "message-2",
      direction: "out",
      author_type: "user",
      author_name: "Менеджер",
      text: "Спасибо! Проверим информацию по вашему заказу.",
      kind: "text",
      status: "delivered",
      version: 1,
      external: true,
      created_at: new Date().toISOString(),
    },
  ];
  await page.route("**/api/**", async (r) => {
    const u = new URL(r.request().url()),
      p = u.pathname;
    let d = [];
    if (p === "/api/auth/me" || p === "/api/auth/refresh")
      d = {
        id: "user-test",
        email: "demo@example.test",
        role: "admin",
        csrf_token: "test",
        must_change_password: false,
      };
    else if (p === "/api/conversations/counts")
      d = {
        all: 142,
        waiting_us: 142,
        mine: 0,
        human: 16,
        ai_draft: 48,
        closed: 0,
      };
    else if (p === "/api/conversations")
      d = dialogs.slice(
        Number(u.searchParams.get("offset") || 0),
        Number(u.searchParams.get("offset") || 0) + 50,
      );
    else if (/\/conversations\/[^/]+$/.test(p))
      d = dialogs.find((x) => p.endsWith(x.id)) || dialogs[0];
    else if (p.endsWith("/messages")) d = msgs;
    else if (p.endsWith("/customer"))
      d = { status: "not_found", clients: [], stale: false, error: null };
    else if (p.startsWith("/api/customers/")) d = profile;
    else if (p.endsWith("/draft"))
      d = { item: null, configured: true, enabled: true };
    else if (p === "/api/channels")
      d = [
        {
          channelId: "channel-1",
          name: "WhatsApp",
          transport: "whatsapp",
          state: "active",
        },
      ];
    else if (p === "/api/tasks") d = { items: [], total: 0 };
    else if (p === "/api/notifications") d = { items: [], unread: 0 };
    else if (p === "/api/integrations/health") d = { warnings: [] };
    else if (p === "/api/me/notifications")
      d = {
        preferences: {
          email: false,
          telegram: false,
          browser: false,
          sound: false,
        },
        email: "demo@example.test",
        telegram_linked: false,
        smtp_configured: false,
        telegram_configured: false,
      };
    await r.fulfill({ json: d });
  });
  await page.route("**/health", (r) => r.fulfill({ json: { ok: true } }));
  await page.goto(process.env.CRM_TEST_BASE_URL || "http://127.0.0.1:5173/");
  await page.locator('[data-dialog-id="dialog-0"]').click();
  await page.locator(".crm-message").first().waitFor();
  // Switching both fresh and cached dialogs must unmount the previous assistant.
  for (const id of [
    "dialog-0",
    "dialog-1",
    "dialog-2",
    "dialog-0",
    "dialog-1",
    "dialog-0",
  ]) {
    await page.locator(`[data-dialog-id="${id}"]`).click();
    await page.waitForFunction(
      (name) =>
        document.querySelector(".crm-thread header b")?.textContent === name,
      dialogs.find((dialog) => dialog.id === id).display_name,
    );
    assert.equal(
      await page.locator(".crm-assistant-row .ai-draft").count(),
      1,
      `Only one assistant after opening ${id}`,
    );
    assert.equal(
      await page
        .locator(".crm-assistant-row .ai-draft-toggle")
        .getAttribute("aria-controls"),
      `ai-panel-${id}`,
    );
    assert.equal(
      await page
        .locator(".crm-assistant-row")
        .getByRole("button", { name: "Быстрые ответы", exact: true })
        .count(),
      1,
    );
    await page.keyboard.press("Alt+a");
    await page.getByRole("region", { name: "Панель AI-помощника" }).waitFor();
    assert.equal(
      await page.getByRole("region", { name: "Панель AI-помощника" }).count(),
      1,
    );
    await page.keyboard.press("Escape");
    await page
      .getByRole("region", { name: "Панель AI-помощника" })
      .waitFor({ state: "hidden" });
  }
  assert.deepEqual(duplicateKeys, []);
  const metrics = await page.evaluate(() => ({
    width: innerWidth,
    height: innerHeight,
    document: document.documentElement.scrollHeight,
    topbar: document.querySelector(".topbar").getBoundingClientRect().height,
    row: document.querySelector(".crm-dialog").getBoundingClientRect().height,
    message: document.querySelector(".crm-message").getBoundingClientRect()
      .height,
    list: document.querySelector(".crm-dialog-list").clientHeight,
    thread: document.querySelector(".crm-thread").clientHeight,
    messages: document.querySelector(".crm-messages").clientHeight,
    assistant: document
      .querySelector(".crm-assistant-row")
      .getBoundingClientRect().height,
    composer: document.querySelector(".crm-composer").getBoundingClientRect()
      .height,
  }));
  assert.equal(metrics.topbar, 40);
  assert.equal(metrics.row, 52);
  assert.ok(metrics.message <= 44);
  assert.equal(metrics.document, 768);
  assert.ok(metrics.list / 52 >= 10);
  assert.ok(metrics.assistant <= 36);
  assert.ok(metrics.composer <= 100);
  assert.ok(metrics.messages / metrics.thread >= 0.6);
  await page.keyboard.press("Alt+a");
  await page.getByRole("region", { name: "Панель AI-помощника" }).waitFor();
  const expandedHeight = await page
    .locator(".crm-messages")
    .evaluate((el) => el.clientHeight);
  assert.equal(
    expandedHeight,
    metrics.messages,
    "AI panel must overlay the timeline",
  );
  await page.keyboard.press("Escape");
  await page
    .getByRole("region", { name: "Панель AI-помощника" })
    .waitFor({ state: "hidden" });
  const editor = page.locator(".crm-editor textarea");
  await editor.fill(
    Array.from({ length: 12 }, (_, i) => `Line ${i + 1}`).join("\n"),
  );
  const inputSize = await editor.evaluate((el) => ({
    height: el.clientHeight,
    scroll: el.scrollHeight,
  }));
  assert.ok(
    inputSize.height <= 160,
    "Editor grows to at most seven text lines plus padding",
  );
  assert.ok(inputSize.scroll > inputSize.height);
  await editor.fill("");
  assert.ok(
    (await page
      .locator(".crm-composer")
      .evaluate((el) => el.getBoundingClientRect().height)) <= 100,
  );
  for (const count of [100, 142]) {
    await page
      .locator(".crm-dialog-list")
      .evaluate((el) => (el.scrollTop = el.scrollHeight));
    await page.waitForFunction(
      (expected) =>
        document.querySelectorAll("[data-dialog-id]").length >= expected,
      count,
    );
  }
  assert.equal(await page.locator("[data-dialog-id]").count(), 142);
  await page.locator(".crm-dialog-list").evaluate((el) => (el.scrollTop = 0));
  const output =
    process.env.CRM_TEST_OUTPUT ||
    require("node:path").resolve(__dirname, "../../docs/reports");
  fs.mkdirSync(output, { recursive: true });
  await page.screenshot({ path: output + "/dialogs_step2_1366.png" });
  await page.setViewportSize({ width: 1920, height: 1080 });
  await page.screenshot({ path: output + "/dialogs_step2_1920.png" });
  fs.writeFileSync(
    output + "/dialogs_step2_browser.json",
    JSON.stringify({ metrics, errors }, null, 2),
  );
  assert.deepEqual(errors, []);
  console.log(JSON.stringify(metrics));
  await browser.close();
})().catch((e) => {
  console.error(e);
  process.exit(1);
});
