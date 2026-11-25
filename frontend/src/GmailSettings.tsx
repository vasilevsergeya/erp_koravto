import { useState, type FormEvent } from "react";
import { Alert, Button, PasswordInput, TextInput } from "@mantine/core";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Mail } from "lucide-react";
import { api } from "./api";

type Connection = { email: string; mode: "" | "imap" | "oauth" };
const path = "/api/settings/gmail/connection";
const key = ["settings", "gmail-connection"];

export function GmailSettings() {
  const query = useQueryClient();
  const connection = useQuery({
    queryKey: key,
    queryFn: () => api<Connection>(path),
  });
  const [editEmail, setEmail] = useState<string | null>(null);
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const email = editEmail ?? connection.data?.email ?? "";

  async function change(disconnect: boolean) {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      let saved: Connection;
      if (disconnect) {
        await api("/api/settings/gmail.credentials", "PUT", { value: "" });
        saved = { email: "", mode: "" };
      } else {
        saved = await api<Connection>(path, "PUT", {
          email: email.trim(),
          password,
        });
      }
      query.setQueryData(key, saved);
      query.removeQueries({ queryKey: ["mail"] });
      void query.invalidateQueries({ queryKey: ["settings"], exact: true });
      setPassword("");
      setEmail(null);
      setNotice(
        disconnect ? "Ящик отключён." : "Подключение проверено и сохранено.",
      );
    } catch (e) {
      setError(
        e instanceof Error ? e.message : "Не удалось сохранить подключение.",
      );
    } finally {
      setBusy(false);
    }
  }
  function submit(event: FormEvent) {
    event.preventDefault();
    void change(false);
  }
  return (
    <section className="surface settings-card" id="gmail">
      <div className="card-heading">
        <span className="integration-icon">
          <Mail />
        </span>
        <div>
          <h3>Почта Gmail</h3>
          <p>Чтение входящих и отправленных писем через IMAP.</p>
        </div>
      </div>
      {connection.data?.mode && (
        <p>
          {connection.data.mode === "imap"
            ? "Подключён ящик " + connection.data.email
            : "Сейчас используется подключение OAuth."}
        </p>
      )}
      <form onSubmit={submit}>
        <TextInput
          mb="xs"
          type="email"
          label="Адрес Gmail или Google Workspace"
          required
          autoComplete="off"
          maxLength={254}
          disabled={busy}
          value={email}
          onChange={(e) => setEmail(e.currentTarget.value)}
        />
        <PasswordInput
          mb="xs"
          label="Пароль приложения Google"
          description="16 символов. Можно вставить с пробелами между группами."
          autoComplete="new-password"
          maxLength={80}
          required
          disabled={busy}
          value={password}
          onChange={(e) => setPassword(e.currentTarget.value)}
        />
        <Button
          type="submit"
          variant="light"
          loading={busy}
          disabled={!email.trim() || !password.trim() || !connection.data}
        >
          Проверить и сохранить
        </Button>
        {connection.data?.mode && (
          <Button
            type="button"
            variant="subtle"
            color="gray"
            disabled={busy}
            onClick={() => void change(true)}
          >
            Отключить ящик
          </Button>
        )}
      </form>
      {notice && <p role="status">{notice}</p>}
      {(error || connection.error) && (
        <Alert color="red" role="alert">
          {error || connection.error?.message}
        </Alert>
      )}
    </section>
  );
}
