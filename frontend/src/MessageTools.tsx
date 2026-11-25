import { NumberLinks } from "./CustomerNumber";
import { ChevronDown } from "lucide-react";
import { useState } from "react";
import {
  Alert,
  ActionIcon,
  Menu,
  Popover,
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

export function languageName(code: string): string {
  try {
    return new Intl.DisplayNames(["ru"], { type: "language" }).of(code) || code;
  } catch {
    return code;
  }
}

export function LinkedText({
  text,
  customerLinks,
}: {
  text: string;
  customerLinks?: boolean;
}) {
  const parts = text.split(/((?:https?:\/\/|www\.)[^\s<>]+)/giu);
  return (
    <>
      {parts.map((part, i) => {
        if (!/^(?:https?:\/\/|www\.)/iu.test(part))
          return customerLinks ? <NumberLinks key={i} text={part} /> : part;
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
  managerLanguage,
  onManagerChange,
  detectedLanguage,
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
  managerLanguage?: string;
  onManagerChange?: (language: string) => void;
  detectedLanguage?: string;
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
        comboboxProps={{ withinPortal: false }}
        label="Язык клиента"
        data={[
          { value: "auto", label: "Определять автоматически" },
          ...languages,
        ]}
        value={value.translation_client_language || "auto"}
        searchable
        disabled={disabled}
        onChange={(v) => v && onChange({ translation_client_language: v })}
      />
      {value.translation_client_language === "auto" && (
        <small>
          Авто:{" "}
          {detectedLanguage
            ? languageName(detectedLanguage)
            : "ещё не определён"}
        </small>
      )}
      {onManagerChange && (
        <Select
          comboboxProps={{ withinPortal: false }}
          label="Мой язык"
          data={languages}
          value={managerLanguage || "en"}
          searchable
          onChange={(value) => value && onManagerChange(value)}
        />
      )}
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
  const personal = useQuery({
    queryKey: ["manager-language"],
    queryFn: () => api<{ language: string }>("/api/me/language"),
  });
  const ownLanguage = personal.data?.language || "en";
  const value = customer.data;
  const clientLanguage =
    value?.translation_client_language === "auto"
      ? value.language
      : value?.translation_client_language;
  const incoming = value?.incoming_translation_auto
    ? !!clientLanguage && clientLanguage !== ownLanguage
    : !!value?.translate_incoming;
  const enabled = !!(incoming || value?.translate_outgoing);
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
  async function changePersonal(language: string) {
    setError("");
    try {
      const updated = await api<{ language: string }>(
        "/api/me/language",
        "PUT",
        { language },
      );
      query.setQueryData(["manager-language"], updated);
      for (const key of [
        "conversation",
        "conversations",
        "messages",
        "ai-draft",
      ])
        void query.invalidateQueries({ queryKey: [key] });
    } catch (error) {
      setError(error instanceof Error ? error.message : String(error));
    }
  }
  return (
    <Popover position="bottom-end" width={300} withinPortal shadow="md">
      <Popover.Target>
        <Button
          size="xs"
          variant="subtle"
          title="Входящие: язык клиента → язык менеджера. Исходящие: язык менеджера → язык клиента."
        >
          <span className="crm-translation-short">Перевод</span>
          <span className="crm-translation-full">
            {enabled
              ? `${clientLanguage ? languageName(clientLanguage) : "авто"} → ${languageName(ownLanguage)}`
              : "Перевод: выкл"}
          </span>
        </Button>
      </Popover.Target>
      <Popover.Dropdown>
        {error && <Alert color="red">{error}</Alert>}
        {value && (
          <TranslationFields
            value={{ ...value, translate_incoming: incoming }}
            managerLanguage={ownLanguage}
            onManagerChange={(language) => void changePersonal(language)}
            detectedLanguage={value.language}
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
      </Popover.Dropdown>
    </Popover>
  );
}

export function SendLanguageMenu({
  value,
  onChange,
  managerLanguage,
  clientLanguage,
  disabled = false,
}: {
  value: string | null;
  onChange: (language: string | null) => void;
  managerLanguage: string;
  clientLanguage?: string;
  disabled?: boolean;
}) {
  return (
    <Menu withinPortal={false} position="top-end">
      <Menu.Target>
        <ActionIcon
          variant="light"
          disabled={disabled}
          title="Язык одной отправки"
          aria-label="Язык отправки"
        >
          <ChevronDown size={14} />
        </ActionIcon>
      </Menu.Target>
      <Menu.Dropdown>
        <Menu.Item
          color={!value ? "teal" : undefined}
          onClick={() => onChange(null)}
        >
          По умолчанию
          {clientLanguage ? " · " + languageName(clientLanguage) : ""}
        </Menu.Item>
        <Menu.Item
          color={value === "original" ? "teal" : undefined}
          onClick={() => onChange("original")}
        >
          На языке менеджера, без перевода · {languageName(managerLanguage)}
        </Menu.Item>
        <Menu.Divider />
        {languages.map((language) => (
          <Menu.Item
            key={language.value}
            color={value === language.value ? "teal" : undefined}
            onClick={() => onChange(language.value)}
          >
            {languageName(language.value)}
          </Menu.Item>
        ))}
      </Menu.Dropdown>
    </Menu>
  );
}

export function MessageActions({
  message: m,
  user,
  onChanged,
  onReply,
}: {
  message: Message;
  user: User;
  onChanged: () => void;
  onReply: () => void;
}) {
  const [edit, setEdit] = useState(false),
    [text, setText] = useState("");
  const [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const [confirmAction, setConfirmAction] = useState<"hide" | "delete" | null>(
    null,
  );
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
      setConfirmAction(null);
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
      <Menu withinPortal position="bottom-end">
        <Menu.Target>
          <ActionIcon
            size={14}
            variant="subtle"
            className="crm-message-menu"
            aria-label="Действия с сообщением"
            title="Действия с сообщением"
          >
            <ChevronDown size={12} />
          </ActionIcon>
        </Menu.Target>
        <Menu.Dropdown>
          {!m.is_private_note &&
            !m.deleted &&
            m.external &&
            m.author_type !== "system" && (
              <Menu.Item onClick={onReply}>Ответить</Menu.Item>
            )}
          <Menu.Item
            disabled={!m.text}
            onClick={() =>
              void navigator.clipboard
                .writeText(
                  m.direction === "out" ? (m.delivery_text ?? m.text) : m.text,
                )
                .catch(() => setError("Не удалось скопировать текст"))
            }
          >
            Скопировать текст
          </Menu.Item>
          {writable && !m.attachment_url && !active && (
            <Menu.Item
              disabled={busy}
              onClick={() => {
                setText(m.text);
                setOperationId(crypto.randomUUID());
                setEdit(true);
              }}
            >
              Редактировать
            </Menu.Item>
          )}
          {writable && !active && (
            <Menu.Item
              disabled={busy}
              color="red"
              onClick={() => setConfirmAction("delete")}
            >
              {m.is_private_note
                ? "Удалить заметку"
                : unsent
                  ? "Отменить отправку"
                  : "Удалить у всех"}
            </Menu.Item>
          )}
          <Menu.Item disabled={busy} onClick={() => setConfirmAction("hide")}>
            Удалить у себя
          </Menu.Item>
        </Menu.Dropdown>
      </Menu>
      <Modal
        opened={confirmAction !== null}
        onClose={() => !busy && setConfirmAction(null)}
        title={
          confirmAction === "hide" ? "Удалить у себя?" : "Удалить сообщение?"
        }
        centered
      >
        <p>
          {confirmAction === "hide"
            ? "Сообщение останется у клиента и других сотрудников."
            : m.is_private_note
              ? "Внутренняя заметка будет удалена."
              : unsent
                ? "Отправка будет отменена."
                : "Сообщение будет удалено у вас и у клиента."}
        </p>
        {error && <Alert color="red">{error}</Alert>}
        <Button
          color="red"
          loading={busy}
          onClick={() => confirmAction && void change(confirmAction)}
        >
          Удалить
        </Button>
        <Button
          ml="sm"
          variant="default"
          disabled={busy}
          onClick={() => setConfirmAction(null)}
        >
          Отмена
        </Button>
      </Modal>
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
