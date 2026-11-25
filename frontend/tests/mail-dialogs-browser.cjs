const assert = require("node:assert/strict");
const { chromium } = require("playwright");

(async () => {
  const browser = await chromium.launch({
    executablePath: process.env.CHROMIUM_PATH,
    args: ["--no-sandbox"],
  });
  try {
    const page = await browser.newPage({
      viewport: { width: 1366, height: 768 },
    });
    await page.addInitScript(() => {
      window.WebSocket = class {
        addEventListener() {}
        close() {}
      };
    });
    const errors = [];
    page.on("pageerror", (e) => errors.push(e.message));
    const buyer = "buyer@example.test",
      box = "sales@example.test",
      second = "second@example.test";
    const dates = [
      "2026-01-01T15:00:00Z",
      "2026-01-02T14:59:00Z",
      "2026-01-02T15:00:00Z",
    ];
    const letters = dates.map((at, i) => ({
      id: String(i),
      subject: "Тема " + i,
      at: Date.parse(at),
      text: "Письмо " + i,
      outgoing: i === 2,
      sender: { email: i === 2 ? box : buyer },
      recipients: [{ email: i === 2 ? buyer : box }],
      attachments: [],
    }));
    const row = {
      email: buyer,
      name: "Покупатель",
      subject: "Тема 2",
      at: letters[2].at,
      snippet: "Письмо",
      unread: true,
      threadId: "abc",
    };
    const lookups = [];
    await page.route("**/api/**", async (route) => {
      const url = new URL(route.request().url());
      const p = url.pathname;
      let json = [];
      if (p === "/api/auth/me" || p === "/api/auth/refresh")
        json = {
          id: "test",
          email: box,
          role: "admin",
          csrf_token: "test",
          must_change_password: false,
        };
      else if (p === "/api/mail/dialogs")
        json = url.searchParams.get("page")
          ? {
              mailbox: box,
              dialogs: [
                { ...row, at: letters[0].at },
                { ...row, email: second, name: "", at: 1 },
              ],
              next: null,
            }
          : { mailbox: box, dialogs: [row], next: "p2" };
      else if (p === "/api/mail/dialogs/messages")
        json =
          url.searchParams.get("email") === second
            ? { messages: [], next: null }
            : url.searchParams.get("page")
              ? { messages: letters.slice(0, 2), next: null }
              : { messages: letters.slice(1), next: "older" };
      else if (p.endsWith("/customer")) {
        lookups.push(url.searchParams.get("email"));
        json = { status: "ok", clients: [] };
      } else if (p === "/api/tasks") json = { items: [], total: 0 };
      else if (p === "/api/notifications") json = { items: [], unread: 0 };
      else if (p === "/api/integrations/health") json = { warnings: [] };
      await route.fulfill({ json });
    });
    await page.goto(
      (process.env.CRM_TEST_BASE_URL || "http://127.0.0.1:5173").replace(
        /\/$/,
        "",
      ) + "/mail",
    );
    const cards = page.locator(".mail-letter");
    await cards.nth(1).waitFor();
    await page
      .getByRole("button", { name: "Показать более ранние письма" })
      .click();
    await cards.nth(2).waitFor();
    assert.deepEqual(
      await page.locator(".mail-letter-subject").allTextContents(),
      ["Тема 0", "Тема 1", "Тема 2"],
    );
    assert.deepEqual(
      await page.locator(".mail-letters .crm-date-separator").allTextContents(),
      ["2 января", "3 января"],
    );
    assert.deepEqual(await cards.locator("time").allTextContents(), [
      "00:00",
      "23:59",
      "00:00",
    ]);
    const incoming = await cards.nth(1).boundingBox(),
      outgoing = await cards.nth(2).boundingBox();
    assert.ok(
      incoming.x > outgoing.x + 30 &&
        incoming.x + incoming.width > outgoing.x + outgoing.width,
    );
    await page.getByRole("button", { name: "Загрузить ещё" }).click();
    await page.getByRole("button", { name: new RegExp(second) }).click();
    await page.getByText("Писем с этим адресом больше нет.").waitFor();
    assert.equal(await page.locator(".mail-row").count(), 2);
    assert.deepEqual(lookups, [buyer, second]);
    assert.deepEqual(errors, []);
    console.log(
      "Mail address grouping, 1C selection, chronology, Seoul midnight and bubble alignment passed.",
    );
  } finally {
    await browser.close();
  }
})().catch((e) => {
  console.error(e);
  process.exitCode = 1;
});
