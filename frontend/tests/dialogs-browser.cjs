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
    client_number: "C-00123",
    phone_country: "US",
    customer_type: "partner",
    order_count: 2,
    note_preview: "Доставка",
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
  let profileNote = {
    text: "Доставка",
    version: 1,
    author: "Тест",
    updated_at: null,
  };
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
  const photoUrl = await page.evaluate(() => {
    const canvas = document.createElement("canvas");
    canvas.width = 1600;
    canvas.height = 800;
    return canvas.toDataURL("image/png");
  });
  const photos = ["archive", "recent"].map((id) => ({
    id,
    name: id + ".png",
    url: photoUrl,
  }));
  const summaryRequests = [];
  const summaryLanguages = new Map();
  let managerId = "user-test";
  await page.route("**/api/**", async (r) => {
    const u = new URL(r.request().url()),
      p = u.pathname;
    let d = [];
    if (p === "/api/auth/me" || p === "/api/auth/refresh")
      d = {
        id: managerId,
        email: "demo@example.test",
        role: "admin",
        csrf_token: "test",
        must_change_password: false,
      };
    else if (p === "/api/me/language") d = { language: "en" };
    else if (p === "/api/me/summary-language") {
      if (r.request().method() === "PUT")
        summaryLanguages.set(managerId, r.request().postDataJSON().language);
      d = { language: summaryLanguages.get(managerId) || null };
    } else if (p === "/api/conversations/counts")
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
    else if (p === "/api/conversations/dialog-2/images") d = photos;
    else if (p === "/api/conversations/dialog-2/messages")
      d = [
        {
          ...msgs[0],
          id: photos[1].id,
          attachment_url: photoUrl,
          attachment_name: photos[1].name,
          attachment_mime: "image/png",
        },
      ];
    else if (p.endsWith("/messages")) d = msgs;
    else if (p.endsWith("/summary")) {
      summaryRequests.push([
        p,
        r.request().method(),
        r.request().postDataJSON(),
      ]);
      d = {
        summary: "Клиент уточняет срок доставки.",
        task: "Проверить дату отправки.",
      };
    } else if (p.endsWith("/customer"))
      d = { status: "not_found", clients: [], stale: false, error: null };
    else if (p.endsWith("/profile-note")) {
      if (r.request().method() === "PUT") {
        const payload = r.request().postDataJSON();
        if (payload.version !== profileNote.version)
          return r.fulfill({
            status: 409,
            json: { detail: "Конфликт версий" },
          });
        profileNote = {
          ...profileNote,
          text: payload.text,
          version: profileNote.version + 1,
        };
      }
      d = profileNote;
    } else if (
      p.endsWith("/profile-note/history") ||
      p.endsWith("/counterparties")
    )
      d = [];
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
  await page.route(
    (url) => url.pathname === "/health",
    (r) => r.fulfill({ json: { ok: true } }),
  );
  await page.goto(process.env.CRM_TEST_BASE_URL || "http://127.0.0.1:5173/");
  await page.locator('[data-dialog-id="dialog-2"]').click();
  const viewer = page.getByRole("dialog", { name: "Изображения диалога" });
  const thumbnail = page.getByRole("button", { name: "recent.png" });
  for (const width of [1366, 390]) {
    await page.setViewportSize({ width, height: 768 });
    await thumbnail.click();
    await viewer.getByText("2 / 2").waitFor();
    assert.ok(
      await viewer
        .getByRole("button", { name: "Следующее изображение" })
        .isDisabled(),
    );
    await viewer
      .getByRole("button", { name: "Предыдущее изображение" })
      .click();
    await viewer.getByRole("img", { name: "archive.png" }).waitFor();
    assert.ok(
      await viewer
        .getByRole("button", { name: "Предыдущее изображение" })
        .isDisabled(),
    );
    const photo = viewer.locator(".gallery-frame img");
    await page.waitForFunction(
      () => document.querySelector(".gallery-frame img")?.clientWidth > 0,
    );
    await viewer.getByRole("button", { name: "Повернуть вправо" }).click();
    const rotated = await photo.boundingBox();
    assert.ok(rotated.height > rotated.width);
    await viewer.getByRole("button", { name: "Увеличить" }).click();
    assert.ok((await photo.boundingBox()).height > rotated.height);
    await viewer.getByRole("button", { name: "Уменьшить" }).click();
    assert.equal((await photo.boundingBox()).height, rotated.height);
    await viewer.getByRole("button", { name: "Повернуть влево" }).click();
    await viewer.getByRole("button", { name: "Увеличить" }).click();
    await page.keyboard.press("ArrowRight");
    await viewer.getByText("2 / 2").waitFor();
    assert.equal(await viewer.locator(".gallery-scale").innerText(), "100%");
    await page.keyboard.press("Escape");
    await viewer.waitFor({ state: "hidden" });
    assert.equal(
      await thumbnail.evaluate((el) => el === document.activeElement),
      true,
    );
  }
  await page.setViewportSize({ width: 1366, height: 768 });
  await page.locator('[data-dialog-id="dialog-0"]').click();
  await page.locator(".crm-message").first().waitFor();
  await page.getByRole("button", { name: "AI-сводка", exact: true }).click();
  const summary = page.getByRole("dialog", { name: "AI-сводка диалога" });
  const summaryLanguage = summary.getByLabel("Язык сводки");
  assert.equal(await summaryLanguage.inputValue(), "English");
  await summaryLanguage.click();
  await page.getByRole("option", { name: "Русский" }).click();
  await summary.getByRole("button", { name: "Сформировать" }).click();
  await summary.getByText("Проверить дату отправки.").waitFor();
  assert.deepEqual(summaryRequests, [
    ["/api/conversations/dialog-0/summary", "POST", { language: "ru" }],
  ]);
  assert.deepEqual(await summary.locator('[lang="ru"] p').allTextContents(), [
    "Клиент уточняет срок доставки.",
    "Задача",
    "Проверить дату отправки.",
  ]);
  await page.keyboard.press("Escape");
  await page.locator('[data-dialog-id="dialog-1"]').click();
  await page.getByRole("button", { name: "AI-сводка", exact: true }).click();
  await summary.getByRole("button", { name: "Сформировать" }).waitFor();
  assert.equal(await summaryLanguage.inputValue(), "Русский");
  assert.equal(await summary.getByText("Проверить дату отправки.").count(), 0);
  await summaryLanguage.click();
  await page.getByRole("option", { name: "Deutsch" }).click();
  await page.keyboard.press("Escape");
  await page.locator('[data-dialog-id="dialog-0"]').click();
  await page.getByRole("button", { name: "AI-сводка", exact: true }).click();
  assert.equal(await summaryLanguage.inputValue(), "Deutsch");
  assert.equal(summaryRequests.length, 1);
  await page.keyboard.press("Escape");

  for (const [id, expected] of [
    ["another-manager", "English"],
    ["user-test", "Deutsch"],
  ]) {
    managerId = id;
    await page.reload();
    await page.locator('[data-dialog-id="dialog-1"]').click();
    await page.getByRole("button", { name: "AI-сводка", exact: true }).click();
    await page.waitForFunction(
      (value) =>
        document.querySelector('[role="dialog"] input')?.value === value,
      expected,
    );
    await page.keyboard.press("Escape");
  }
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
  const list = page.locator(".crm-dialog-list");
  const topDialog = (el) => {
    const { top, left } = el.getBoundingClientRect();
    const row = document
      .elementFromPoint(left + el.clientWidth / 2, top + 1)
      .closest("[data-dialog-id]");
    return [row.dataset.dialogId, row.getBoundingClientRect().top - top];
  };
  const waitForRows = (count) =>
    page.waitForFunction(
      (n) => document.querySelectorAll("[data-dialog-id]").length === n,
      count,
      { timeout: 10000 },
    );
  // Scroll past a partially visible first row before reaching the next page.
  await list.evaluate(
    (el) =>
      new Promise((resolve) => {
        el.addEventListener("scroll", () => resolve(), { once: true });
        el.scrollTop = 20;
      }),
  );
  const scrollNearEnd = () =>
    list.evaluate((el) => {
      el.scrollTop = el.scrollHeight - el.clientHeight - 137;
    });
  const pageAt = (offset) => (url) =>
    url.pathname === "/api/conversations" &&
    Number(url.searchParams.get("offset")) === offset;
  const holdOffset = async (offset) => {
    const started = page.waitForRequest((request) =>
      pageAt(offset)(new URL(request.url())),
    );
    const release = Promise.withResolvers();
    await page.route(
      pageAt(offset),
      async (route) => {
        await release.promise;
        await route.fallback();
      },
      { times: 1 },
    );
    return { started, release: release.resolve };
  };
  const nextPage = await holdOffset(50);
  await scrollNearEnd();
  await nextPage.started;
  // Keep moving while the response is in flight.
  await list.evaluate((el) => {
    el.scrollTop += 67;
  });
  const onFirstPage = await list.evaluate(topDialog);
  nextPage.release();
  await waitForRows(100);
  assert.deepEqual(await list.evaluate(topDialog), onFirstPage);

  // Reach the next boundary during a background refresh. No further scroll
  // event is sent after the refresh completes.
  const refresh = await holdOffset(0);
  await page.evaluate(() =>
    document.dispatchEvent(new Event("visibilitychange")),
  );
  await refresh.started;
  await scrollNearEnd();
  const onSecondPage = await list.evaluate(topDialog);
  refresh.release();
  await waitForRows(142);
  assert.deepEqual(await list.evaluate(topDialog), onSecondPage);
  // A new message moves a different dialog ahead of the visible rows.
  const moved = dialogs.pop();
  dialogs.unshift(moved);
  await page.evaluate(() =>
    document.dispatchEvent(new Event("visibilitychange")),
  );
  await page.waitForFunction(
    (id) => document.querySelector("[data-dialog-id]").dataset.dialogId === id,
    moved.id,
  );
  assert.deepEqual(await list.evaluate(topDialog), onSecondPage);
  dialogs.push(dialogs.shift());

  // A new search starts at the top. Retrying a failed page keeps its offset.
  const search = page.locator(".crm-toolbar input");
  await search.fill("Клиент");
  await waitForRows(50);
  assert.equal(await list.evaluate((el) => el.scrollTop), 0);
  await page.route(
    pageAt(50),
    (route) =>
      route.fulfill({ status: 503, json: { detail: "Ошибка подгрузки" } }),
    { times: 2 },
  );
  await scrollNearEnd();
  await list.getByRole("button", { name: "Повторить" }).click();
  await waitForRows(100);
  await search.fill("");
  await waitForRows(142);
  assert.equal(await list.evaluate((el) => el.scrollTop), 0);
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
  await page.setViewportSize({ width: 1366, height: 768 });
  const width = (selector) =>
    page
      .locator(selector)
      .evaluate((el) => Math.round(el.getBoundingClientRect().width));
  for (const [column, handle, delta, expected, base] of [
    [".crm-dialogs", ".crm-resize-list", 65, 345, 280],
    [".crm-customer", ".crm-resize-panel", -43, 293, 250],
  ]) {
    const rect = await page.locator(handle).boundingBox();
    await page.mouse.move(rect.x + 3, rect.y + 55);
    await page.mouse.down();
    await page.mouse.move(rect.x + 3 + delta, rect.y + 55, { steps: 4 });
    await page.mouse.up();
    assert.equal(await width(column), expected);
    await page.reload();
    await page.locator("[data-dialog-id]").first().click();
    assert.equal(await width(column), expected);
    await page.locator(handle).dblclick();
    assert.equal(await width(column), base);
  }
  await page.getByTitle("Фильтр", { exact: true }).click();
  await page.getByRole("menuitem", { name: /Сначала старые/ }).click();
  await page.reload();
  await page.getByTitle("Сначала старые", { exact: true }).waitFor();
  await page.locator("[data-dialog-id]").first().click();
  const edit = page.locator(".crm-profile-note textarea:visible");
  await page
    .getByRole("button", { name: "Заметка о клиенте", exact: true })
    .click();
  await edit.fill("Мой текст");
  profileNote = { ...profileNote, text: "Версия коллеги", version: 2 };
  await edit.press("Control+Enter");
  await page.getByText("Версия коллеги", { exact: true }).waitFor();
  assert.equal(await edit.inputValue(), "Мой текст");
  await page
    .getByRole("button", { name: "Принять сохранённую версию" })
    .click();
  assert.equal(profileNote.text, "Версия коллеги");
  await page.setViewportSize({ width: 390, height: 844 });
  await page
    .getByRole("button", { name: "Открыть карточку", exact: true })
    .click();
  await edit.fill("Проверка");
  await edit.press("Control+Enter");
  await page.waitForFunction(
    () => !document.querySelector(".crm-client-drawer textarea")?.disabled,
  );
  assert.equal(profileNote.text, "Проверка");
  await page.goBack();
  await page.getByRole("dialog").waitFor({ state: "hidden" });
  assert.equal(
    await page.locator(".crm-thread-header").evaluate((el) => el.offsetHeight),
    44,
  );
  assert.deepEqual(errors, []);
  await browser.close();
})().catch((e) => {
  console.error(e);
  process.exit(1);
});
