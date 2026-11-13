import { useEffect, useState } from "react";
import { Select } from "@mantine/core";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { api } from "./api";
import type { CustomerLite, CustomersResult } from "./customer-api";
import "./customer-i18n";
export function CustomerPicker({
  value,
  onChange,
  disabled = false,
  label,
  exclude,
}: {
  value: string | null;
  onChange: (value: string | null) => void;
  disabled?: boolean;
  label?: string;
  exclude?: string;
}) {
  const { t } = useTranslation("customers");
  const [text, setText] = useState("");
  const [q, setQ] = useState("");
  useEffect(() => {
    const timer = setTimeout(() => setQ(text), 250);
    return () => clearTimeout(timer);
  }, [text]);
  const list = useQuery({
    queryKey: ["customers", "picker", q],
    queryFn: () =>
      api<CustomersResult>(
        "/api/customers?limit=50&q=" + encodeURIComponent(q),
      ),
  });
  const selected = useQuery({
    queryKey: ["customer", value],
    queryFn: () => api<CustomerLite>("/api/customers/" + value),
    enabled: !!value,
  });
  const all = new Map((list.data?.items || []).map((c) => [c.id, c]));
  if (selected.data) all.set(selected.data.id, selected.data);
  return (
    <Select
      label={label || t("choose")}
      searchable
      clearable
      searchValue={text}
      onSearchChange={setText}
      disabled={disabled}
      value={value}
      onChange={onChange}
      error={list.error?.message || selected.error?.message}
      data={Array.from(all.values())
        .filter((c) => c.id !== exclude)
        .map((c) => ({
          value: c.id,
          label:
            c.display_name + (c.phones[0] ? " · " + c.phones[0].value : ""),
        }))}
    />
  );
}
