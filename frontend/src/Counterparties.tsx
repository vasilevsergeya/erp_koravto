import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "./api";

type Account = { id: string | null; name: string; phone: string };
export function Counterparties({ id, write }: { id: string; write: boolean }) {
  const [open, setOpen] = useState(false),
    [phone, setPhone] = useState("");
  const [found, setFound] = useState<Account[] | null>(null),
    [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const query = useQueryClient(),
    url = "/api/customers/" + id + "/counterparties";
  const accounts = useQuery({
    queryKey: ["counterparties", id],
    enabled: open,
    queryFn: () => api<Account[]>(url),
  });
  async function search() {
    setBusy(true);
    setError("");
    setFound(null);
    try {
      setFound(
        await api<Account[]>(
          url + "/search?phone=" + encodeURIComponent(phone),
        ),
      );
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }
  async function link(a: Account, linked: boolean) {
    setBusy(true);
    setError("");
    try {
      await api(url, "PUT", { account_id: a.id, phone: a.phone, linked });
      for (const k of [
        "counterparties",
        "conversation",
        "conversations",
        "conversation-counts",
      ])
        void query.invalidateQueries({ queryKey: [k] });
      setFound(null);
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <details onToggle={(e) => setOpen(e.currentTarget.open)}>
      <summary>Контрагенты 1С</summary>
      {open && (
        <>
          {accounts.data?.map((a) => (
            <p key={a.id}>
              {a.name}{" "}
              {write && (
                <button disabled={busy} onClick={() => void link(a, false)}>
                  Отвязать
                </button>
              )}
            </p>
          ))}
          {write && (
            <>
              <label>
                Телефон для поиска
                <input
                  type="tel"
                  value={phone}
                  onChange={(e) => {
                    setPhone(e.target.value);
                    setFound(null);
                  }}
                />
              </label>
              <button
                disabled={busy || !phone.trim()}
                onClick={() => void search()}
              >
                Найти
              </button>
              {found?.map((a, i) => (
                <p key={a.id || i}>
                  {a.name}{" "}
                  {a.id ? (
                    <button disabled={busy} onClick={() => void link(a, true)}>
                      Привязать
                    </button>
                  ) : (
                    "В ответе 1С нет идентификатора контрагента"
                  )}
                </p>
              ))}
              {found?.length === 0 && <p>Контрагенты не найдены</p>}
            </>
          )}
          {(error || accounts.error) && (
            <small role="alert">{error || String(accounts.error)}</small>
          )}
        </>
      )}
    </details>
  );
}
