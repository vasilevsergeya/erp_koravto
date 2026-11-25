import { useTranslation } from "react-i18next";
import i18n from "./i18n";
import "./onec-orders.css";

export type OneCOrder = {
  number: string;
  amount: number;
  shipping_number: string;
  project: string;
};

export type OneCClient = {
  name: string;
  country?: string;
  matched_phone?: string;
  realizations: string[];
  orders?: OneCOrder[];
};

i18n.addResourceBundle("ru", "onecOrders", {
  possibleMatch: "Возможное совпадение",
  country: "Страна из 1С",
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
  orders: "Orders",
  orderNumber: "Order",
  amount: "Amount",
  salesChannel: "Sales channel",
  shippingNumber: "Tracking number",
  noOrders: "No orders found in 1C",
  numbersOnly: "Order details have not been received from 1C yet",
});

export function OneCCustomerDetails({
  client,
  compact = false,
}: {
  client: OneCClient;
  compact?: boolean;
}) {
  const { t, i18n } = useTranslation("onecOrders");
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
      <dl className="onec-country">
        <dt>{t("country")}</dt>
        <dd dir="auto">{client.country || "—"}</dd>
      </dl>
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
                  <span>{t("orderNumber")}</span>
                  <b dir="auto">{order.number}</b>
                </div>
                {"amount" in order && (
                  <dl className="onec-order-fields">
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
      <summary>{t("possibleMatch")} · {candidates.length}</summary>
      {candidates.map((client, index) => (
        <OneCCustomerDetails key={index} client={client} compact={compact} />
      ))}
    </details>
  );
}
