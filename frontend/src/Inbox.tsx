import { useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Alert,
  Badge,
  Button,
  Checkbox,
  FileInput,
  Loader,
  Modal,
  Select,
  Textarea,
  TextInput,
} from "@mantine/core";
import { useTranslation } from "react-i18next";
import {
  api,
  uploadFile,
  type Conversation,
  type Message,
  type User,
} from "./api";
import "./inbox.css";

type Channel = {
  channelId: string;
  name: string;
  state: string;
  transport: string;
};
type Member = { id: string; name: string; role: string };
const time = (value: string) =>
  new Intl.DateTimeFormat("ru-RU", {
    dateStyle: "short",
    timeStyle: "short",
    timeZone: "Asia/Seoul",
  }).format(new Date(value));
const errorText = (error: unknown) =>
  error instanceof Error ? error.message : String(error);

export function Inbox({ user }: { user: User }) {
  const { t } = useTranslation("inbox");
  const query = useQueryClient();
  const [selected, setSelected] = useState<string | null>(null);
  const [filter, setFilter] = useState("all");
  const [search, setSearch] = useState("");
  const [searchQuery, setSearchQuery] = useState("");
  const [page, setPage] = useState(0);
  const [message, setMessage] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [attachment, setAttachment] = useState<string | null>(null);
  const [note, setNote] = useState(false);
  const [quoted, setQuoted] = useState<Message | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [sendId, setSendId] = useState<string>(() => crypto.randomUUID());
  const [older, setOlder] = useState<Message[]>([]);
  const [hasOlder, setHasOlder] = useState(true);
  const [newOpen, setNewOpen] = useState(false);
  const [phone, setPhone] = useState("");
  const [name, setName] = useState("");
  const [country, setCountry] = useState("RU");
  const [channel, setChannel] = useState<string | null>(null);
  const [firstText, setFirstText] = useState("");
  const [newId, setNewId] = useState<string>(() => crypto.randomUUID());
  const [order, setOrder] = useState("");
  const [cardOpen, setCardOpen] = useState(false);
  const end = useRef<HTMLDivElement>(null);
  const pane = useRef<HTMLDivElement>(null);
  const drafts = useRef<
    Record<
      string,
      {
        text: string;
        file: File | null;
        attachment: string | null;
        note: boolean;
        quoted: Message | null;
        sendId: string;
      }
    >
  >({});
  const canWrite = user.role === "admin" || user.role === "manager";
  useEffect(() => {
    const timer = setTimeout(() => {
      setSearchQuery(search);
      setPage(0);
    }, 250);
    return () => clearTimeout(timer);
  }, [search]);
  const params = new URLSearchParams({
    offset: String(page * 50),
    q: searchQuery,
  });
  if (filter === "waiting_us" || filter === "closed")
    params.set("state", filter);
  if (filter === "mine") params.set("assignee", "me");
  if (filter === "human") params.set("needs_human", "true");
  const conversations = useQuery({
    queryKey: ["conversations", params.toString()],
    queryFn: () => api<Conversation[]>("/api/conversations?" + params),
    refetchInterval: 5000,
  });
  const counters = useQuery({
    queryKey: ["conversation-counts"],
    queryFn: () => api<Record<string, number>>("/api/conversations/counts"),
    refetchInterval: 5000,
  });
  const current = useQuery({
    queryKey: ["conversation", selected],
    queryFn: () => api<Conversation>("/api/conversations/" + selected),
    enabled: !!selected,
    refetchInterval: 5000,
  });
  const messages = useQuery({
    queryKey: ["messages", selected],
    queryFn: () =>
      api<Message[]>("/api/conversations/" + selected + "/messages"),
    enabled: !!selected,
    refetchInterval: 5000,
  });
  const channels = useQuery({
    queryKey: ["channels"],
    queryFn: () => api<Channel[]>("/api/channels"),
    staleTime: 300000,
    retry: false,
  });
  const team = useQuery({
    queryKey: ["team"],
    queryFn: () => api<Member[]>("/api/team"),
  });
  const refresh = () => {
    for (const key of [
      "conversations",
      "conversation",
      "messages",
      "conversation-counts",
    ])
      void query.invalidateQueries({ queryKey: [key] });
  };
  useEffect(() => {
    if (selected && current.data?.unread_count)
      void api("/api/conversations/" + selected + "/read", "POST")
        .then(() => {
          void query.invalidateQueries({ queryKey: ["conversation-counts"] });
        })
        .catch(() => {});
  }, [selected, current.data?.unread_count, query]);
  const lastId = messages.data?.at(-1)?.id;
  useEffect(() => {
    const element = pane.current;
    if (
      element &&
      element.scrollHeight - element.scrollTop - element.clientHeight < 350
    )
      end.current?.scrollIntoView({ block: "end" });
  }, [lastId]);
  function choose(id: string | null) {
    if (selected)
      drafts.current[selected] = {
        text: message,
        file,
        attachment,
        note,
        quoted,
        sendId,
      };
    const draft = id ? drafts.current[id] : undefined;
    setSelected(id);
    setOlder([]);
    setHasOlder(true);
    setMessage(draft?.text || "");
    setFile(draft?.file || null);
    setAttachment(draft?.attachment || null);
    setQuoted(draft?.quoted || null);
    setNote(draft?.note || false);
    setSendId(draft?.sendId || crypto.randomUUID());
    setError("");
  }
  async function send() {
    if (!selected || busy) return;
    setBusy(true);
    setError("");
    const target = selected;
    try {
      let attachmentId = attachment;
      if (file && !attachmentId) {
        attachmentId = (await uploadFile(file)).id;
        setAttachment(attachmentId);
      }
      await api("/api/conversations/" + target + "/messages", "POST", {
        client_id: sendId,
        text: message,
        attachment_id: attachmentId,
        quoted_message_id: quoted?.id || null,
        is_private_note: note,
      });
      delete drafts.current[target];
      setMessage("");
      setFile(null);
      setAttachment(null);
      setQuoted(null);
      setSendId(crypto.randomUUID());
      refresh();
      setTimeout(() => end.current?.scrollIntoView({ block: "end" }), 150);
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  }
  async function create() {
    setBusy(true);
    setError("");
    try {
      const c = await api<Conversation>("/api/conversations", "POST", {
        client_id: newId,
        phone,
        country,
        name,
        channel_id: channel || "",
        text: firstText,
      });
      setNewOpen(false);
      setPhone("");
      setName("");
      setFirstText("");
      setNewId(crypto.randomUUID());
      choose(c.id);
      refresh();
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  }
  async function change(values: Record<string, unknown>) {
    if (!selected) return;
    try {
      await api("/api/conversations/" + selected, "PATCH", values);
      refresh();
    } catch (e) {
      setError(errorText(e));
    }
  }
  async function retry(m: Message) {
    let confirm = false;
    if (m.status === "uncertain") {
      confirm = window.confirm(t("retryWarning"));
      if (!confirm) return;
    }
    try {
      await api("/api/messages/" + m.id + "/retry", "POST", {
        confirm_duplicate_risk: confirm,
      });
      refresh();
    } catch (e) {
      setError(errorText(e));
    }
  }
  async function loadOlder() {
    const before = older[0]?.id || messages.data?.[0]?.id;
    if (!before || !selected) return;
    try {
      const rows = await api<Message[]>(
        "/api/conversations/" + selected + "/messages?before=" + before,
      );
      setOlder((previous) => [...rows, ...previous]);
      setHasOlder(rows.length === 50);
    } catch (e) {
      setError(errorText(e));
    }
  }
  const allMessages = Array.from(
    new Map(
      [...older, ...(messages.data || [])].map((m) => [m.id, m]),
    ).values(),
  );
  const c = current.data;
  const customerCard = (
    <div className="crm-customer-content">
      <h3>{t("customer")}</h3>
      <TextInput
        label={t("name")}
        key={c?.id + ":" + c?.name}
        defaultValue={c?.name || ""}
        disabled={!canWrite}
        onBlur={(e) => {
          if (e.currentTarget.value !== c?.name)
            void change({ name: e.currentTarget.value });
        }}
      />
      <p>{c?.phone}</p>
      <Select
        label={t("assignee")}
        value={c?.assignee_id || null}
        clearable
        disabled={!canWrite}
        data={(team.data || [])
          .filter((u) => u.role !== "staff")
          .map((u) => ({ value: u.id, label: u.name }))}
        onChange={(value) => void change({ assignee_id: value })}
      />
      <Checkbox
        mt="md"
        label={t("human")}
        checked={!!c?.needs_human}
        disabled={!canWrite}
        onChange={(e) => void change({ needs_human: e.currentTarget.checked })}
      />

      <h4>{t("orders")}</h4>
      <p className="crm-muted">{t("onecOffline")}</p>
      {c?.order_numbers.map((number) => (
        <div className="crm-order" key={number}>
          <span>{number}</span>
          {canWrite && (
            <button
              aria-label={t("unlinkOrder")}
              onClick={() =>
                void change({
                  order_numbers: c.order_numbers.filter((n) => n !== number),
                })
              }
            >
              ×
            </button>
          )}
        </div>
      ))}
      {canWrite && (
        <>
          <TextInput
            aria-label={t("orderNumber")}
            placeholder={t("orderNumber")}
            value={order}
            onChange={(e) => setOrder(e.currentTarget.value)}
            maxLength={100}
          />
          <Button
            mt="xs"
            size="xs"
            variant="light"
            disabled={!order.trim()}
            onClick={() => {
              void change({
                order_numbers: [
                  ...new Set([...(c?.order_numbers || []), order.trim()]),
                ],
              });
              setOrder("");
            }}
          >
            {t("linkOrder")}
          </Button>
          <Button
            mt="xl"
            fullWidth
            variant="default"
            onClick={() =>
              void change({
                state: c?.state === "closed" ? "waiting_us" : "closed",
              })
            }
          >
            {t(c?.state === "closed" ? "reopen" : "close")}
          </Button>
        </>
      )}
    </div>
  );
  return (
    <div className={"crm-inbox " + (selected ? "has-selection" : "")}>
      <aside className="crm-dialogs">
        <div className="crm-toolbar">
          <b>{t("dialogs")}</b>
          {canWrite && (
            <Button
              size="xs"
              onClick={() => {
                setError("");
                setNewOpen(true);
              }}
            >
              + {t("newDialog")}
            </Button>
          )}
        </div>
        <TextInput
          aria-label={t("search")}
          placeholder={t("search")}
          value={search}
          onChange={(e) => setSearch(e.currentTarget.value)}
          m="sm"
        />
        <div className="crm-filters">
          {["all", "waiting_us", "mine", "human", "closed"].map((f) => (
            <button
              className={filter === f ? "active" : ""}
              key={f}
              onClick={() => {
                setFilter(f);
                setPage(0);
              }}
            >
              {t(f)} <small>{counters.data?.[f] ?? 0}</small>
            </button>
          ))}
        </div>
        {conversations.isLoading && <Loader size="sm" m="md" />}
        {conversations.error && (
          <Alert color="red">{errorText(conversations.error)}</Alert>
        )}
        <div className="crm-dialog-list">
          {conversations.data?.length === 0 && (
            <p className="crm-empty">{t("noDialogs")}</p>
          )}
          {conversations.data?.map((item) => (
            <button
              disabled={busy}
              key={item.id}
              className={
                "crm-dialog " + (item.id === selected ? "selected" : "")
              }
              onClick={() => choose(item.id)}
            >
              <div>
                <b>{item.name || item.phone}</b>
                <small>{time(item.updated_at)}</small>
              </div>
              <p>{item.last_message || t("attachment")}</p>
              <div>
                <span>{t(item.state)}</span>
                {item.unread_count > 0 && (
                  <Badge size="xs">{item.unread_count}</Badge>
                )}
                {item.needs_human && (
                  <Badge color="orange" size="xs">
                    {t("human")}
                  </Badge>
                )}
              </div>
            </button>
          ))}
        </div>
        <div className="crm-pagination">
          <Button
            variant="subtle"
            size="xs"
            disabled={page === 0}
            onClick={() => setPage((p) => p - 1)}
          >
            ←
          </Button>
          <small>{page + 1}</small>
          <Button
            variant="subtle"
            size="xs"
            disabled={(conversations.data?.length || 0) < 50}
            onClick={() => setPage((p) => p + 1)}
          >
            →
          </Button>
        </div>
      </aside>
      {!selected ? (
        <div className="crm-empty crm-select-prompt">
          <h3>{t("selectDialog")}</h3>
          <p>{t("selectHint")}</p>
        </div>
      ) : (
        <>
          <section className="crm-thread">
            <header className="crm-thread-header">
              <Button
                className="crm-back"
                variant="subtle"
                size="xs"
                disabled={busy}
                onClick={() => choose(null)}
              >
                ← {t("dialogs")}
              </Button>
              <div>
                <b>{c?.name || c?.phone || t("loading")}</b>
                <small>{c?.phone}</small>
              </div>
              <Button
                className="crm-open-card"
                size="xs"
                variant="subtle"
                onClick={() => setCardOpen(true)}
              >
                {t("customer")}
              </Button>
              {c && <Badge variant="light">{t(c.state)}</Badge>}
            </header>
            {channels.data?.find(
              (ch) => ch.channelId === c?.channel_id && ch.state !== "active",
            ) && <Alert color="orange">{t("inactiveChannel")}</Alert>}
            {error && !newOpen && (
              <Alert color="red" withCloseButton onClose={() => setError("")}>
                {error}
              </Alert>
            )}
            {messages.error && (
              <Alert color="red">{errorText(messages.error)}</Alert>
            )}
            {current.error && (
              <Alert color="red">{errorText(current.error)}</Alert>
            )}
            <div className="crm-messages" ref={pane}>
              {hasOlder &&
                ((messages.data?.length || 0) >= 50 || older.length > 0) && (
                  <Button
                    fullWidth
                    variant="subtle"
                    size="xs"
                    onClick={() => void loadOlder()}
                  >
                    {t("loadOlder")}
                  </Button>
                )}
              {allMessages.map((m) => (
                <article
                  className={
                    "crm-message " +
                    (m.is_private_note
                      ? "private"
                      : m.direction === "out"
                        ? "out"
                        : "in")
                  }
                  dir="auto"
                  key={m.id}
                >
                  <div className="crm-message-author">
                    {m.is_private_note
                      ? t("privateNote")
                      : m.author_name || t(m.author_type)}
                    {m.edited && " · " + t("edited")}
                    {m.deleted && " · " + t("deleted")}
                  </div>
                  {m.quoted_text && (
                    <blockquote dir="auto">{m.quoted_text}</blockquote>
                  )}
                  {m.text && <div className="crm-message-text">{m.text}</div>}
                  {m.attachment_url && (
                    <div className="crm-attachment">
                      {m.attachment_mime?.startsWith("image/") &&
                      m.attachment_mime !== "image/svg+xml" ? (
                        <a
                          href={m.attachment_url}
                          target="_blank"
                          rel="noreferrer"
                        >
                          <img
                            loading="lazy"
                            src={m.attachment_url}
                            alt={m.attachment_name || t("attachment")}
                          />
                        </a>
                      ) : m.attachment_mime?.startsWith("audio/") ? (
                        <audio controls preload="none" src={m.attachment_url} />
                      ) : m.attachment_mime?.startsWith("video/") ? (
                        <video controls preload="none" src={m.attachment_url} />
                      ) : null}
                      <a
                        href={m.attachment_url}
                        target="_blank"
                        rel="noreferrer"
                      >
                        {m.attachment_name || t("attachment")}
                      </a>
                    </div>
                  )}
                  {m.kind === "missing_call" && <p>{t("missedCall")}</p>}
                  {m.error_text && (
                    <p className="crm-message-error">{m.error_text}</p>
                  )}
                  <footer>
                    <time>{time(m.created_at)}</time>
                    <span>
                      {t("status_" + m.status, { defaultValue: m.status })}
                    </span>
                    {canWrite &&
                      !m.is_private_note &&
                      m.author_type !== "system" && (
                        <button onClick={() => setQuoted(m)}>
                          {t("quote")}
                        </button>
                      )}
                    {canWrite &&
                      ["error", "uncertain"].includes(m.status) &&
                      m.author_type === "user" && (
                        <button onClick={() => void retry(m)}>
                          {t("retry")}
                        </button>
                      )}
                  </footer>
                </article>
              ))}
              <div ref={end} />
            </div>
            {canWrite ? (
              <div className="crm-composer">
                {quoted && (
                  <div className="crm-quote-preview">
                    <span>{quoted.text.slice(0, 150) || t("attachment")}</span>
                    <button
                      aria-label={t("cancelQuote")}
                      onClick={() => setQuoted(null)}
                    >
                      ×
                    </button>
                  </div>
                )}
                <Textarea
                  aria-label={t("message")}
                  placeholder={note ? t("noteHint") : t("messageHint")}
                  value={message}
                  onChange={(e) => setMessage(e.currentTarget.value)}
                  autosize
                  minRows={2}
                  maxRows={6}
                  maxLength={10000}
                  dir="auto"
                  disabled={busy}
                  onKeyDown={(e) => {
                    if ((e.ctrlKey || e.metaKey) && e.key === "Enter") {
                      e.preventDefault();
                      void send();
                    }
                  }}
                />
                <div className="crm-compose-actions">
                  <FileInput
                    aria-label={t("attach")}
                    placeholder={t("attach")}
                    value={file}
                    onChange={(value) => {
                      if (value && value.size > 10 * 1024 * 1024) {
                        setError(t("fileLimit"));
                        return;
                      }
                      setFile(value);
                      setAttachment(null);
                    }}
                    clearable
                    disabled={busy}
                    size="xs"
                  />
                  <Checkbox
                    size="xs"
                    label={t("privateNote")}
                    checked={note}
                    onChange={(e) => setNote(e.currentTarget.checked)}
                    disabled={busy}
                  />
                  <Button
                    loading={busy}
                    disabled={!message.trim() && !file}
                    onClick={() => void send()}
                  >
                    {note ? t("saveNote") : t("send")}
                  </Button>
                </div>
                <small className="crm-compose-hint">{t("composeHint")}</small>
              </div>
            ) : (
              <p className="crm-empty">{t("readOnly")}</p>
            )}
          </section>
          <aside className="crm-customer">{customerCard}</aside>
          <Modal
            opened={cardOpen}
            onClose={() => setCardOpen(false)}
            title={t("customer")}
            centered
          >
            {customerCard}
          </Modal>
        </>
      )}
      <Modal
        opened={newOpen}
        onClose={() => {
          if (!busy) setNewOpen(false);
        }}
        title={t("newDialog")}
        centered
      >
        {error && (
          <Alert color="red" mb="md">
            {error}
          </Alert>
        )}
        {channels.error && (
          <Alert color="orange" mb="md">
            {errorText(channels.error)}
          </Alert>
        )}
        <TextInput
          label={t("phone")}
          placeholder="+7 999 123-45-67"
          value={phone}
          onChange={(e) => setPhone(e.currentTarget.value)}
          required
        />
        <Select
          mt="sm"
          label={t("country")}
          value={country}
          onChange={(v) => setCountry(v || "RU")}
          data={[
            { value: "RU", label: "Россия +7" },
            { value: "KR", label: "대한민국 +82" },
            { value: "US", label: "United States +1" },
            { value: "GB", label: "United Kingdom +44" },
            { value: "AE", label: "الإمارات +971" },
          ]}
        />
        <small className="crm-muted">{t("countryHint")}</small>
        <TextInput
          mt="sm"
          label={t("name")}
          value={name}
          onChange={(e) => setName(e.currentTarget.value)}
          maxLength={200}
        />
        <Select
          mt="sm"
          label={t("channel")}
          value={
            channel ||
            (channels.data?.filter((c) => c.transport === "whatsapp").length ===
            1
              ? channels.data.find((c) => c.transport === "whatsapp")?.channelId
              : null)
          }
          onChange={setChannel}
          data={(channels.data || [])
            .filter((c) => c.transport === "whatsapp")
            .map((c) => ({
              value: c.channelId,
              label: (c.name || c.channelId) + " · " + c.state,
              disabled: c.state !== "active",
            }))}
        />
        <Textarea
          mt="sm"
          label={t("firstMessage")}
          value={firstText}
          onChange={(e) => setFirstText(e.currentTarget.value)}
          minRows={3}
          maxLength={10000}
          dir="auto"
        />
        <Button
          mt="lg"
          fullWidth
          loading={busy}
          disabled={!phone.trim() || !firstText.trim() || !!channels.error}
          onClick={() => void create()}
        >
          {t("startConversation")}
        </Button>
      </Modal>
    </div>
  );
}
