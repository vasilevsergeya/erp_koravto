import { useState } from "react";
import { Popover } from "@mantine/core";
import { useQueryClient } from "@tanstack/react-query";
import { api, type Conversation } from "./api";

export const kinds = { retail: "Розничный", partner: "Партнёр (B2B)" };
export function orders(count: number) {
  const word = new Intl.PluralRules("ru").select(count);
  return (
    count +
    " " +
    (word === "one" ? "заказ" : word === "few" ? "заказа" : "заказов")
  );
}
export function CustomerMarks({
  c,
  compact = false,
}: {
  c: Conversation;
  compact?: boolean;
}) {
  const n = c.order_count;
  const partner = c.customer_type === "partner";
  return (
    <>
      {n != null && n >= 2 && !(compact && partner) && (
        <Mark label="★" tip={"Постоянный клиент: " + orders(n)} />
      )}
      {partner && <Mark label="B2B" tip="Партнёр (B2B), оптовые заказы" />}
      {!compact && n === 1 && (
        <Mark label="1 заказ" tip="Новый клиент: 1 заказ" />
      )}
    </>
  );
}
function Mark({ label, tip }: { label: string; tip: string }) {
  const [open, setOpen] = useState(false);
  return (
    <Popover
      opened={open}
      onChange={setOpen}
      withArrow
      shadow="sm"
      withinPortal
    >
      <Popover.Target>
        <button
          className={"crm-mark" + (label === "1 заказ" ? " neutral" : "")}
          title={tip}
          aria-label={tip}
          onClick={(e) => {
            e.stopPropagation();
            setOpen(!open);
          }}
        >
          {label}
        </button>
      </Popover.Target>
      <Popover.Dropdown>{tip}</Popover.Dropdown>
    </Popover>
  );
}
export function CustomerFields({ c }: { c: Conversation }) {
  const query = useQueryClient(),
    [error, setError] = useState("");
  const n = c.order_count;
  return (
    <>
      <div className="crm-detail-row">
        <span>Статус:</span>
        <b>
          {n == null
            ? "Нет данных · —"
            : (n === 0 ? "Лид" : n === 1 ? "Новый" : "Постоянный") +
              " · " +
              orders(n)}
        </b>
      </div>
      {c.customer_id && (
        <label className="crm-detail-row">
          <span>Тип:</span>
          <select
            aria-label="Тип клиента"
            value={c.customer_type || "retail"}
            onChange={async (e) => {
              try {
                const res = await api<{ customer_type: "retail" | "partner" }>(
                  "/api/customers/" + c.customer_id + "/type",
                  "PUT",
                  { customer_type: e.target.value },
                );
                query.setQueryData(
                  ["conversation", c.id],
                  (old?: Conversation) => old && { ...old, ...res },
                );
                for (const key of [
                  "conversations",
                  "conversation-counts",
                  "customer",
                  "customers",
                ])
                  void query.invalidateQueries({ queryKey: [key] });
                setError("");
              } catch (err) {
                setError(String(err));
              }
            }}
          >
            {Object.entries(kinds).map(([key, val]) => (
              <option key={key} value={key}>
                {val}
              </option>
            ))}
          </select>
        </label>
      )}
      {error && <small role="alert">{error}</small>}
    </>
  );
}
