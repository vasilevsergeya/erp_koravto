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
    const profile = {
      name: "Alex Morgan",
      FirstName: "Alex",
      LastName: "Morgan",
      NickName: "AM",
      Company: "Example Auto Parts",
      country: "Australia",
      email: buyer,
      emails: [buyer.toUpperCase(), "orders@example.test"],
      phones: ["+61 2 5550 0100", "+61 491 570 006"],
      Default_address_address1: "25 Example Street",
      Default_address_address2: "Unit 4",
      Default_address_address3: "Building B",
      Default_address_city: "Sydney",
      Default_address_country: "Australia",
      Default_address_zip: "0200",
      Default_address_province_code: "NSW",
      Ценообразование: "Опт",
      Incoterms: "FCA",
      realizations: ["SHOP-1258"],
      orders: [
        {
          number: "SHOP-1258",
          number_1c: "00ЮН-026437",
          sales_channel_number: "WEB-2",
          amount: 248.5,
          shipping_number: "001234567890",
          project: "Интернет-магазин",
        },
      ],
    };
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
        json = {
          status: "ok",
          clients:
            url.searchParams.get("email") === buyer
              ? [profile]
              : [
                  { name: "Legacy", realizations: ["0001"] },
                  { name: "Other", realizations: [] },
                ],
        };
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
    const details = page.locator(".onec-customer-details");
    await details.getByText("Example Auto Parts").waitFor();
    const visible = await details.innerText();
    for (const value of [
      "Alex Morgan",
      "AM",
      "Alex",
      "Morgan",
      "Australia",
      buyer,
      "orders@example.test",
      ...profile.phones,
      "25 Example Street",
      "Unit 4",
      "Building B",
      "Sydney",
      "NSW",
      "0200",
      "Опт",
      "FCA",
      "SHOP-1258",
      "WEB-2",
      "00ЮН-026437",
      "248,50",
      "001234567890",
      "Интернет-магазин",
    ])
      assert.ok(visible.includes(value), value);
    assert.equal(visible.toLowerCase().split(buyer).length, 2);
    assert.equal(await page.locator(".crm-onec-candidates").count(), 0);
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
    await page.locator(".crm-onec-candidates summary").click();
    await details.nth(0).getByText("0001").waitFor();
    assert.equal(await details.count(), 2);
    assert.equal(await details.locator(".onec-profile-group").count(), 0);
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
