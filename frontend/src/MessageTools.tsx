import { useState } from "react";
import {
  Alert,
  Button,
  Checkbox,
  Modal,
  Select,
  Stack,
  Textarea,
} from "@mantine/core";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, type Message, type User } from "./api";
import type { CustomerLite } from "./customer-api";

export const languages = [
  ["ru", "Русский"],
  ["en", "English"],
  ["ko", "한국어"],
  ["de", "Deutsch"],
  ["fr", "Français"],
  ["es", "Español"],
  ["it", "Italiano"],
  ["pt", "Português"],
  ["ar", "العربية"],
  ["zh", "中文"],
  ["ja", "日本語"],
  ["tr", "Türkçe"],
  ["pl", "Polski"],
  ["uk", "Українська"],
  ["he", "עברית"],
  ["id", "Bahasa Indonesia"],
  ["vi", "Tiếng Việt"],
  ["th", "ไทย"],
  ["hi", "हिन्दी"],
  ["nl", "Nederlands"],
].map(([value, label]) => ({ value, label }));

export function LinkedText({ text }: { text: string }) {
  const parts = text.split(/((?:https?:\/\/|www\.)[^\s<>]+)/giu);
  return (
    <>
      {parts.map((part, i) => {
        if (!/^(?:https?:\/\/|www\.)/iu.test(part)) return part;
        const value = part.replace(/[.,!?;:)}\]»]+$/u, "");
        const suffix = part.slice(value.length);
        try {
          const url = new URL(
            value.startsWith("www.") ? "https://" + value : value,
          );
          if (!["http:", "https:"].includes(url.protocol)) return part;
          return (
            <span key={i}>
              <a href={url.href} target="_blank" rel="noopener noreferrer">
                {value}
              </a>
              {suffix}
            </span>
          );
        } catch {
          return part;
        }
      })}
    </>
  );
}

export function TranslationFields({
  value,
  onChange,
  disabled = false,
}: {
  value: Pick<
    CustomerLite,
    | "translate_incoming"
    | "translate_outgoing"
    | "translation_client_language"
    | "translation_manager_language"
  >;
  onChange: (fields: Partial<CustomerLite>) => void;
  disabled?: boolean;
}) {
  return (
    <Stack gap="xs" className="crm-translation-fields">
      <Checkbox
        label="Переводить входящие сообщения"
        checked={!!value.translate_incoming}
        disabled={disabled}
        onChange={(e) =>
          onChange({ translate_incoming: e.currentTarget.checked })
        }
      />
      <Checkbox
        label="Переводить исходящие сообщения"
        checked={!!value.translate_outgoing}
        disabled={disabled}
        onChange={(e) =>
          onChange({ translate_outgoing: e.currentTarget.checked })
        }
      />
      <Select
        label="Язык клиента"
        data={languages}
        value={value.translation_client_language || "en"}
        searchable
        disabled={disabled}
        onChange={(v) => v && onChange({ translation_client_language: v })}
      />
      <Select
        label="Язык менеджера"
        data={languages}
        value={value.translation_manager_language || "ru"}
        searchable
        disabled={disabled}
        onChange={(v) => v && onChange({ translation_manager_language: v })}
      />
      <small>
        Входящие: язык клиента → язык менеджера. Исходящие: язык менеджера →
        язык клиента.
      </small>
    </Stack>
  );
}

export function TranslationPreferences({
  customerId,
  canWrite,
  original,
  onOriginal,
}: {
  customerId: string;
  canWrite: boolean;
  original: boolean;
  onOriginal: (value: boolean) => void;
}) {
  const query = useQueryClient();
  const customer = useQuery({
    queryKey: ["customer", customerId],
    queryFn: () => api<CustomerLite>("/api/customers/" + customerId),
    refetchInterval: 10000,
  });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const value = customer.data;
  const enabled = !!(value?.translate_incoming || value?.translate_outgoing);
  async function change(fields: Partial<CustomerLite>) {
    if (!value) return;
    setBusy(true);
    setError("");
    try {
      const updated = await api<CustomerLite>(
        "/api/customers/" + customerId,
        "PATCH",
        { ...fields, version: value.version },
      );
      query.setQueryData(["customer", customerId], updated);
      for (const key of [
        "conversation",
        "conversations",
        "messages",
        "customers",
      ])
        void query.invalidateQueries({ queryKey: [key] });
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="crm-translation-preferences">
      <h3>Перевод диалога</h3>
      {error && <Alert color="red">{error}</Alert>}
      {value && (
        <TranslationFields
          value={value}
          onChange={(fields) => void change(fields)}
          disabled={!canWrite || busy}
        />
      )}
      <Checkbox
        mt="sm"
        label="Показать на языке оригинала"
        checked={!enabled || original}
        disabled={!enabled}
        onChange={(e) => onOriginal(e.currentTarget.checked)}
      />
    </section>
  );
}

export function MessageActions({
  message: m,
  user,
  onChanged,
}: {
  message: Message;
  user: User;
  onChanged: () => void;
}) {
  const [edit, setEdit] = useState(false),
    [text, setText] = useState("");
  const [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const [operationId, setOperationId] = useState(() => crypto.randomUUID());
  const owned = m.author_id === user.id || user.role === "admin";
  const active =
    !!m.action &&
    ["queued", "processing", "uncertain"].includes(m.action.state);
  const unsent = ["queued", "translating"].includes(m.status);
  const writable =
    owned &&
    m.direction === "out" &&
    m.author_type !== "system" &&
    !m.deleted &&
    (m.is_private_note || m.external || unsent || m.status === "error");
  async function change(action: "edit" | "delete" | "hide") {
    if (busy) return;
    if (
      action === "delete" &&
      !window.confirm(
        m.is_private_note
          ? "Удалить внутреннюю заметку?"
          : unsent
            ? "Отменить отправку сообщения?"
            : "Удалить сообщение у себя и у клиента?",
      )
    )
      return;
    if (
      action === "hide" &&
      !window.confirm(
        "Скрыть сообщение только для вас? У клиента и других сотрудников оно останется.",
      )
    )
      return;
    setBusy(true);
    setError("");
    try {
      await api("/api/messages/" + m.id + "/actions", "POST", {
        client_id: operationId,
        version: m.version,
        action,
        text: action === "edit" ? text : "",
      });
      setEdit(false);
      setOperationId(crypto.randomUUID());
      onChanged();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }
  async function retryAction() {
    if (
      !m.action ||
      !window.confirm("Проверьте результат в WhatsApp. Повторить операцию?")
    )
      return;
    try {
      await api("/api/message-actions/" + m.action.id + "/retry", "POST", {
        confirm: true,
      });
      onChanged();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }
  return (
    <>
      <div className="crm-message-tools">
        {writable && !m.attachment_url && !active && (
          <button
            disabled={busy}
            onClick={() => {
              setText(m.text);
              setOperationId(crypto.randomUUID());
              setEdit(true);
            }}
          >
            Редактировать
          </button>
        )}
        {writable && !active && (
          <button disabled={busy} onClick={() => void change("delete")}>
            {m.is_private_note
              ? "Удалить заметку"
              : unsent
                ? "Отменить отправку"
                : "Удалить у всех"}
          </button>
        )}
        <button disabled={busy} onClick={() => void change("hide")}>
          Удалить у себя
        </button>
      </div>
      {m.action && ["queued", "processing"].includes(m.action.state) && (
        <small>Изменение выполняется…</small>
      )}
      {m.action && ["error", "uncertain"].includes(m.action.state) && (
        <div className="crm-message-error">
          {m.action.error || "Изменение не подтверждено"}
          {owned && (
            <button onClick={() => void retryAction()}>
              Повторить после проверки
            </button>
          )}
        </div>
      )}
      {error && <Alert color="red">{error}</Alert>}
      <Modal
        opened={edit}
        onClose={() => !busy && setEdit(false)}
        title={
          m.is_private_note
            ? "Редактировать заметку"
            : "Редактировать сообщение"
        }
        closeButtonProps={{ "aria-label": "Закрыть редактирование" }}
      >
        <Textarea
          label="Текст оригинала"
          value={text}
          onChange={(e) => {
            setText(e.currentTarget.value);
            setOperationId(crypto.randomUUID());
          }}
          minRows={4}
          autosize
          maxLength={10000}
          disabled={busy}
        />
        {error && (
          <Alert color="red" mt="sm">
            {error}
          </Alert>
        )}
        <Button
          mt="md"
          loading={busy}
          disabled={!text.trim()}
          onClick={() => void change("edit")}
        >
          Сохранить изменения
        </Button>
      </Modal>
    </>
  );
}
