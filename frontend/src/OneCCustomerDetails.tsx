import { useTranslation } from "react-i18next";
import i18n from "./i18n";
import "./onec-orders.css";

export type OneCOrder = {
  number: string;
  number_1c?: string;
  sales_channel_number?: string;
  amount: number;
  shipping_number: string;
  project: string;
};

export type OneCClient = {
  name: string;
  country?: string;
  email?: string;
  emails?: string[];
  phones?: string[];
  matched_email?: string;
  matched_phone?: string;
  Company?: string;
  NickName?: string;
  FirstName?: string;
  LastName?: string;
  Ценообразование?: string;
  Incoterms?: string;
  Default_address_address1?: string;
  Default_address_address2?: string;
  Default_address_address3?: string;
  Default_address_city?: string;
  Default_address_country?: string;
  Default_address_zip?: string;
  Default_address_province_code?: string;
  realizations: string[];
  orders?: OneCOrder[];
};

i18n.addResourceBundle("ru", "onecOrders", {
  possibleMatch: "Возможное совпадение",
  country: "Страна из 1С",
  profile: "Сведения о клиенте",
  firstName: "Имя",
  lastName: "Фамилия",
  nickname: "Никнейм",
  company: "Компания",
  contacts: "Контакты",
  email: "Основной email",
  emails: "Электронная почта",
  otherEmails: "Другие email",
  phones: "Телефоны",
  address: "Адрес по умолчанию",
  street: "Адрес",
  city: "Город",
  region: "Штат / регион",
  postalCode: "Почтовый индекс",
  addressCountry: "Страна",
  terms: "Коммерческие условия",
  pricing: "Ценообразование",
  incoterms: "Условия поставки (Incoterms)",
  channelOrderNumber: "Номер в канале продаж",
  onecOrderNumber: "Номер в 1С",
  orders: "Заказы",
  orderNumber: "Заказ",
  amount: "Сумма",
  salesChannel: "Канал продаж",
  shippingNumber: "Трек-номер",
  noOrders: "Реализации в 1С не найдены",
  numbersOnly: "Подробности заказов ещё не получены из 1С",
});
i18n.addResourceBundle("en", "onecOrders", {
  possibleMatch: "Possible match",
  country: "Country in 1C",
  profile: "Customer information",
  firstName: "First name",
  lastName: "Last name",
  nickname: "Nickname",
  company: "Company",
  contacts: "Contacts",
  email: "Primary email",
  emails: "Email addresses",
  otherEmails: "Other email addresses",
  phones: "Phone numbers",
  address: "Default address",
  street: "Address",
  city: "City",
  region: "State / province",
  postalCode: "Postal code",
  addressCountry: "Country",
  terms: "Commercial terms",
  pricing: "Pricing",
  incoterms: "Delivery terms (Incoterms)",
  channelOrderNumber: "Sales channel order number",
  onecOrderNumber: "1C order number",
  orders: "Orders",
  orderNumber: "Order",
  amount: "Amount",
  salesChannel: "Sales channel",
  shippingNumber: "Tracking number",
  noOrders: "No orders found in 1C",
  numbersOnly: "Order details have not been received from 1C yet",
});

function ProfileGroup({
  title,
  fields,
}: {
  title: string;
  fields: [string, string | undefined][];
}) {
  const populated = fields.filter(([, value]) => value?.trim());
  if (!populated.length) return null;
  return (
    <section className="onec-profile-group">
      <h4>{title}</h4>
      <dl>
        {populated.map(([label, value]) => (
          <div key={label}>
            <dt>{label}</dt>
            <dd dir="auto">{value}</dd>
          </div>
        ))}
      </dl>
    </section>
  );
}

function contactLines(
  values: (string | undefined)[],
  keyFor: (value: string) => string,
  omit?: string,
) {
  const seen = new Set(omit ? [keyFor(omit)] : []);
  const lines: string[] = [];
  for (const raw of values) {
    const value = raw?.trim();
    if (!value) continue;
    const key = keyFor(value);
    if (!seen.has(key)) {
      seen.add(key);
      lines.push(value);
    }
  }
  return lines.join("\n");
}

export function OneCCustomerDetails({
  client,
  compact = false,
  showPrimaryEmail = true,
}: {
  client: OneCClient;
  compact?: boolean;
  showPrimaryEmail?: boolean;
}) {
  const { t, i18n } = useTranslation("onecOrders");
  const primaryEmail = client.email?.trim();
  const emails = contactLines(
    [...(client.emails ?? []), client.matched_email],
    (email) => email.toLowerCase(),
    primaryEmail,
  );
  const phones = contactLines(
    [...(client.phones ?? []), client.matched_phone],
    (phone) => phone.replace(/[ ().-]/g, ""),
  );
  const street = [
    client.Default_address_address1,
    client.Default_address_address2,
    client.Default_address_address3,
  ]
    .map((line) => line?.trim())
    .filter(Boolean)
    .join("\n");
  const orders: (OneCOrder | Pick<OneCOrder, "number">)[] =
    client.orders ?? client.realizations.map((number) => ({ number }));
  const amountFormat = new Intl.NumberFormat(i18n.resolvedLanguage || "ru", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });

  return (
    <section className={`onec-customer-details${compact ? " is-compact" : ""}`}>
      <strong className="onec-customer-name" dir="auto">
        {client.name}
      </strong>
      <div className="onec-profile-groups">
        <ProfileGroup
          title={t("profile")}
          fields={[
            [t("company"), client.Company],
            [t("firstName"), client.FirstName],
            [t("lastName"), client.LastName],
            [t("nickname"), client.NickName],
            [t("country"), client.country],
          ]}
        />
        <ProfileGroup
          title={t("contacts")}
          fields={[
            [t("email"), showPrimaryEmail ? primaryEmail : undefined],
            [t(primaryEmail ? "otherEmails" : "emails"), emails],
            [t("phones"), phones],
          ]}
        />
        <ProfileGroup
          title={t("address")}
          fields={[
            [t("street"), street],
            [t("city"), client.Default_address_city],
            [t("region"), client.Default_address_province_code],
            [t("postalCode"), client.Default_address_zip],
            [t("addressCountry"), client.Default_address_country],
          ]}
        />
        <ProfileGroup
          title={t("terms")}
          fields={[
            [t("pricing"), client.Ценообразование],
            [t("incoterms"), client.Incoterms],
          ]}
        />
      </div>
      <p className="onec-order-count">
        {t("orders")} · {orders.length}
      </p>
      {orders.length ? (
        <>
          {!client.orders && (
            <p className="onec-order-hint">{t("numbersOnly")}</p>
          )}
          <ol className="onec-order-list" aria-label={t("orders")}>
            {orders.map((order, index) => (
              <li className="onec-order-card" key={index}>
                <div className="onec-order-heading">
                  <span>
                    {t(
                      "amount" in order &&
                        (!order.sales_channel_number ||
                          order.sales_channel_number === order.number)
                        ? "channelOrderNumber"
                        : "orderNumber",
                    )}
                  </span>
                  <b dir="auto">{order.number}</b>
                </div>
                {"amount" in order && (
                  <dl className="onec-order-fields">
                    {order.sales_channel_number &&
                      order.sales_channel_number !== order.number && (
                        <div>
                          <dt>{t("channelOrderNumber")}</dt>
                          <dd dir="auto">{order.sales_channel_number}</dd>
                        </div>
                      )}
                    <div>
                      <dt>{t("onecOrderNumber")}</dt>
                      <dd dir="auto">{order.number_1c || "—"}</dd>
                    </div>
                    <div>
                      <dt>{t("amount")}</dt>
                      <dd className="onec-order-amount">
                        {amountFormat.format(order.amount)}
                      </dd>
                    </div>
                    <div>
                      <dt>{t("salesChannel")}</dt>
                      <dd dir="auto">{order.project || "—"}</dd>
                    </div>
                    <div>
                      <dt>{t("shippingNumber")}</dt>
                      <dd className="onec-tracking-number" dir="auto">
                        {order.shipping_number || "—"}
                      </dd>
                    </div>
                  </dl>
                )}
              </li>
            ))}
          </ol>
        </>
      ) : (
        <p className="onec-order-hint">{t("noOrders")}</p>
      )}
    </section>
  );
}

export function OneCCandidates({
  candidates,
  compact = false,
}: {
  candidates?: OneCClient[];
  compact?: boolean;
}) {
  const { t } = useTranslation("onecOrders");
  if (!candidates || candidates.length < 2) return null;
  return (
    <details className="crm-onec-candidates">
      <summary>
        {t("possibleMatch")} · {candidates.length}
      </summary>
      {candidates.map((client, index) => (
        <OneCCustomerDetails key={index} client={client} compact={compact} />
      ))}
    </details>
  );
}
