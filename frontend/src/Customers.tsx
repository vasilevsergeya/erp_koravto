import { useEffect, useRef, useState } from "react";
import {
  Alert,
  Badge,
  Button,
  Checkbox,
  Loader,
  Modal,
  Select,
  Table,
  TagsInput,
  Textarea,
  TextInput,
} from "@mantine/core";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { api, type User } from "./api";
import { CustomerPicker } from "./CustomerPicker";
import { createTask as newTask, openTask } from "./Tasks";
import {
  openCustomer,
  openConversation,
  newCustomerChat,
  type CustomerLite,
  type CustomersResult,
} from "./customer-api";
import "./customer-i18n";
import "./customers.css";

type CustomerDetail = CustomerLite & {
  redirected_from?: string | null;
  conversations: {
    id: string;
    name: string;
    phone: string;
    channel_id: string;
    state: string;
    updated_at: string;
    phone_verified: boolean;
    order_numbers: string[];
    onec: {
      status: string;
      stale: boolean;
      clients: { name: string; realizations: string[] }[];
    };
  }[];
  tasks: {
    id: string;
    code: string;
    title: string;
    assignee_name: string;
    status: { name: string; color: string };
  }[];
  notes: { id: string; text: string; author: string; created_at: string }[];
  notes_more: boolean;
  events: {
    id: string;
    action: string;
    actor: string;
    created_at: string;
    details: Record<string, unknown>;
  }[];
};
const err = (e: unknown) => (e instanceof Error ? e.message : String(e));
const time = (v: string | null) =>
  v
    ? new Intl.DateTimeFormat("ru-RU", {
        timeZone: "Asia/Seoul",
        dateStyle: "short",
        timeStyle: "short",
      }).format(new Date(v))
    : "—";
function refresh(query: ReturnType<typeof useQueryClient>) {
  for (const key of [
    "customers",
    "customer",
    "conversations",
    "conversation",
    "tasks",
    "task",
    "messages",
  ])
    void query.invalidateQueries({ queryKey: [key] });
}
const segments = ["retail", "small_wholesale", "wholesale"];

function ProfileForm({
  customer,
  onDone,
  onCancel,
}: {
  customer?: CustomerLite;
  onDone: (c: CustomerDetail) => void;
  onCancel: () => void;
}) {
  const { t } = useTranslation("customers");
  const [name, setName] = useState(customer?.name || "");
  const [phone, setPhone] = useState("");
  const [phoneCountry, setPhoneCountry] = useState("RU");
  const [language, setLanguage] = useState(customer?.language || "");
  const [country, setCountry] = useState(customer?.country || "");
  const [segment, setSegment] = useState<string | null>(
    customer?.segment || null,
  );
  const [tags, setTags] = useState(customer?.tags || []);
  const [orders, setOrders] = useState(customer?.order_numbers || []);
  const [owner, setOwner] = useState(customer?.default_owner_id || null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const id = useRef(crypto.randomUUID());
  const revision = useRef(customer?.version);
  const team = useQuery({
    queryKey: ["team"],
    queryFn: () =>
      api<{ id: string; name: string; role: string }[]>("/api/team"),
  });
  async function save() {
    setBusy(true);
    setError("");
    try {
      const c = await api<CustomerDetail>(
        customer ? "/api/customers/" + customer.id : "/api/customers",
        customer ? "PATCH" : "POST",
        {
          name,
          language,
          country,
          segment: segment || "",
          tags,
          order_numbers: orders,
          default_owner_id: owner,
          ...(customer
            ? { version: revision.current }
            : { client_id: id.current, phone, phone_country: phoneCountry }),
        },
      );
      onDone(c);
    } catch (e) {
      setError(err(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <form
      className="customer-form"
      onSubmit={(e) => {
        e.preventDefault();
        void save();
      }}
    >
      {error && <Alert color="red">{error}</Alert>}
      <TextInput
        label={t("name")}
        value={name}
        onChange={(e) => setName(e.currentTarget.value)}
        maxLength={200}
      />
      {!customer && (
        <>
          <TextInput
            label={t("phone")}
            value={phone}
            onChange={(e) => setPhone(e.currentTarget.value)}
            placeholder="+7…"
          />
          <TextInput
            label={t("phoneCountry")}
            value={phoneCountry}
            maxLength={2}
            onChange={(e) =>
              setPhoneCountry(e.currentTarget.value.toUpperCase())
            }
          />
        </>
      )}
      <div className="customer-form-grid">
        <TextInput
          label={t("language")}
          value={language}
          onChange={(e) => setLanguage(e.currentTarget.value)}
          maxLength={20}
        />
        <TextInput
          label={t("country")}
          value={country}
          onChange={(e) => setCountry(e.currentTarget.value.toUpperCase())}
          maxLength={2}
        />
        <Select
          label={t("segment")}
          clearable
          value={segment}
          onChange={setSegment}
          data={segments.map((value) => ({ value, label: t(value) }))}
        />
        <Select
          label={t("owner")}
          clearable
          value={owner}
          onChange={setOwner}
          data={(team.data || [])
            .filter((u) => u.role !== "staff")
            .map((u) => ({ value: u.id, label: u.name }))}
        />
      </div>
      <TagsInput label={t("tags")} value={tags} onChange={setTags} />
      <TagsInput
        label={t("manualOrders")}
        value={orders}
        onChange={setOrders}
      />
      <div className="customer-actions">
        <Button variant="subtle" onClick={onCancel}>
          {t("cancel")}
        </Button>
        <Button type="submit" loading={busy}>
          {t("save")}
        </Button>
      </div>
    </form>
  );
}

function CustomerView({
  id,
  user,
  onBack,
  onOpen,
}: {
  id: string;
  user: User;
  onBack: () => void;
  onOpen: (id: string) => void;
}) {
  const { t } = useTranslation("customers");
  const query = useQueryClient();
  const [editing, setEditing] = useState(false);
  const [action, setAction] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [phone, setPhone] = useState("");
  const [phoneCountry, setPhoneCountry] = useState("RU");
  const [target, setTarget] = useState<string | null>(null);
  const [detach, setDetach] = useState<{ id: string; value: string } | null>(
    null,
  );
  const [newName, setNewName] = useState("");
  const [note, setNote] = useState("");
  const noteId = useRef(crypto.randomUUID());
  const data = useQuery({
    queryKey: ["customer", id],
    queryFn: () => api<CustomerDetail>("/api/customers/" + id),
    refetchInterval: editing || action ? false : 15000,
  });
  const targetData = useQuery({
    queryKey: ["customer", target],
    queryFn: () => api<CustomerDetail>("/api/customers/" + target),
    enabled: !!target,
  });
  const c = data.data;
  const write = user.role !== "staff";
  async function mutate(path: string, body?: unknown) {
    if (!c) return;
    setBusy(true);
    setError("");
    try {
      const result = await api<CustomerDetail>(
        "/api/customers/" + c.id + path,
        "POST",
        body,
      );
      refresh(query);
      setAction("");
      setDetach(null);
      if (path === "/notes") {
        setNote("");
        noteId.current = crypto.randomUUID();
      }
      if (result.id !== id) onOpen(result.id);
      else query.setQueryData(["customer", id], result);
    } catch (e) {
      setError(err(e));
    } finally {
      setBusy(false);
    }
  }
  if (data.isLoading) return <Loader />;
  if (data.error)
    return (
      <Alert color="red">
        {err(data.error)}
        <Button onClick={() => void data.refetch()}>{t("next")}</Button>
      </Alert>
    );
  if (!c) return null;
  const allOrders = Array.from(
    new Set([
      ...c.order_numbers,
      ...c.conversations.flatMap((x) => x.order_numbers),
    ]),
  );
  return (
    <div className="customer-page">
      <div className="customer-actions">
        <Button variant="subtle" onClick={onBack}>
          ← {t("back")}
        </Button>
        <Button
          variant="light"
          onClick={() => newTask(undefined, { id: c.id, name: c.display_name })}
        >
          {t("task")}
        </Button>
        {write && (
          <>
            <Button onClick={() => setEditing(true)}>{t("edit")}</Button>
            <Button
              variant="default"
              onClick={() => {
                setAction("merge");
                setTarget(null);
                setError("");
              }}
            >
              {t("merge")}
            </Button>
          </>
        )}
      </div>
      {error && !action && <Alert color="red">{error}</Alert>}
      {c.redirected_from && <Alert>{t("mergedNotice")}</Alert>}
      <div className="customer-heading">
        <h2 dir="auto">{c.display_name}</h2>
        {c.segment && <Badge>{t(c.segment)}</Badge>}
        {c.tags.map((tag) => (
          <Badge key={tag} variant="light">
            {tag}
          </Badge>
        ))}
      </div>
      <div className="customer-columns">
        <div>
          <section className="surface customer-section">
            <h3>{t("profile")}</h3>
            <dl>
              <dt>{t("language")}</dt>
              <dd>{c.language || t("unassigned")}</dd>
              <dt>{t("country")}</dt>
              <dd>{c.country || t("unassigned")}</dd>
              <dt>{t("firstContact")}</dt>
              <dd>{time(c.first_contact)}</dd>
              <dt>{t("lastContact")}</dt>
              <dd>{time(c.last_contact)}</dd>
            </dl>
            <h4>{t("phone")}</h4>
            {!c.phones.length && <p className="muted">{t("noPhone")}</p>}
            {c.phones.map((p) => (
              <div className="customer-phone" key={p.id}>
                <b>{p.value}</b>
                {write && (
                  <div className="customer-actions">
                    <Button
                      variant="light"
                      size="xs"
                      onClick={() => newCustomerChat(p.value, c.display_name)}
                    >
                      {t("newChat")}
                    </Button>
                    <Button
                      variant="subtle"
                      size="xs"
                      color="gray"
                      onClick={() => {
                        setDetach(p);
                        setNewName(c.name);
                        setAction("detach");
                        setError("");
                      }}
                    >
                      {t("detach")}
                    </Button>
                  </div>
                )}
              </div>
            ))}
            {write && (
              <Button
                variant="light"
                onClick={() => {
                  setAction("phone");
                  setPhone("");
                  setError("");
                }}
              >
                {t("addPhone")}
              </Button>
            )}
          </section>
          <section className="surface customer-section">
            <h3>{t("orders")}</h3>
            <div className="customer-tags">
              {allOrders.map((o) => (
                <Badge variant="light" key={o}>
                  {o}
                </Badge>
              ))}
            </div>
            <p className="muted">{t("onecHint")}</p>
            {c.conversations.map((chat) => (
              <div key={chat.id} className="customer-order-source">
                <b>{chat.phone}</b>
                <small>
                  {t(chat.onec.status)}
                  {chat.onec.stale ? " · " + t("stale") : ""}
                </small>
                {chat.onec.clients.map((cl, i) => (
                  <div key={i}>
                    <strong dir="auto">{cl.name}</strong>
                    <div className="customer-tags">
                      {cl.realizations.map((o, j) => (
                        <Badge variant="outline" key={j}>
                          {o}
                        </Badge>
                      ))}
                    </div>
                  </div>
                ))}
              </div>
            ))}
            {write && !!c.conversations.length && (
              <Button
                variant="light"
                loading={busy}
                onClick={() => void mutate("/onec/refresh")}
              >
                {t("refreshOnec")}
              </Button>
            )}
          </section>
        </div>
        <div>
          <section className="surface customer-section">
            <h3>
              {t("conversations")} · {c.conversation_count}
            </h3>
            {!c.conversations.length && <p>{t("noChats")}</p>}
            {c.conversations.map((chat) => (
              <button
                className="customer-chat"
                key={chat.id}
                onClick={() => openConversation(chat.id)}
              >
                <span>
                  <b dir="auto">{chat.name}</b>
                  <small>
                    {chat.phone}
                    {!chat.phone_verified ? " · " + t("unverified") : ""}
                  </small>
                </span>
                <span>
                  <Badge variant="light">{t(chat.state)}</Badge>
                  <small>{time(chat.updated_at)}</small>
                </span>
              </button>
            ))}
          </section>
          <section className="surface customer-section">
            <h3>
              {t("tasks")} · {c.open_tasks}
            </h3>
            {!c.tasks.length && <p>{t("noTasks")}</p>}
            {c.tasks.map((task) => (
              <button
                className="customer-chat"
                key={task.id}
                onClick={() => openTask(task.code)}
              >
                <span>
                  <b>
                    {task.code} · {task.title}
                  </b>
                  <small>{task.assignee_name}</small>
                </span>
                <Badge color={task.status.color}>{task.status.name}</Badge>
              </button>
            ))}
          </section>
          <section className="surface customer-section">
            <h3>{t("notes")}</h3>
            {write && (
              <div className="customer-form">
                <Textarea
                  label={t("note")}
                  minRows={3}
                  autosize
                  value={note}
                  onChange={(e) => setNote(e.currentTarget.value)}
                  maxLength={20000}
                />
                <Button
                  loading={busy}
                  disabled={!note.trim()}
                  onClick={() =>
                    void mutate("/notes", {
                      client_id: noteId.current,
                      text: note,
                    })
                  }
                >
                  {t("addNote")}
                </Button>
              </div>
            )}
            {!c.notes.length && <p className="muted">{t("noNotes")}</p>}
            {c.notes.map((n) => (
              <article className="customer-note" key={n.id}>
                <small>
                  {n.author} · {time(n.created_at)}
                </small>
                <p dir="auto">{n.text}</p>
              </article>
            ))}
            {c.notes_more && <p>{t("moreNotes")}</p>}
          </section>
          <details className="surface customer-section">
            <summary>{t("history")}</summary>
            {c.events.map((e) => (
              <p key={e.id}>
                {t(e.action)} · {e.actor}
                <small className="customer-date">{time(e.created_at)}</small>
              </p>
            ))}
          </details>
        </div>
      </div>
      <Modal
        opened={editing}
        onClose={() => setEditing(false)}
        title={t("edit")}
        size="lg"
      >
        <ProfileForm
          customer={c}
          onCancel={() => setEditing(false)}
          onDone={(next) => {
            setEditing(false);
            query.setQueryData(["customer", id], next);
            refresh(query);
          }}
        />
      </Modal>
      <Modal
        opened={!!action}
        onClose={() => {
          if (!busy) setAction("");
        }}
        title={t(
          action === "phone"
            ? "addPhone"
            : action === "detach"
              ? "detach"
              : "merge",
        )}
        size="lg"
      >
        {error && (
          <Alert color="red" mb="md">
            {error}
          </Alert>
        )}
        {action === "phone" && (
          <form
            className="customer-form"
            onSubmit={(e) => {
              e.preventDefault();
              void mutate("/identities", {
                version: c.version,
                phone,
                country: phoneCountry,
              });
            }}
          >
            <TextInput
              label={t("phone")}
              value={phone}
              onChange={(e) => setPhone(e.currentTarget.value)}
              required
            />
            <TextInput
              label={t("phoneCountry")}
              value={phoneCountry}
              onChange={(e) =>
                setPhoneCountry(e.currentTarget.value.toUpperCase())
              }
              maxLength={2}
            />
            <Button type="submit" loading={busy}>
              {t("addPhone")}
            </Button>
          </form>
        )}
        {action === "merge" && (
          <div className="customer-form">
            <p>{t("mergeHelp")}</p>
            <CustomerPicker
              value={target}
              onChange={setTarget}
              exclude={c.id}
              label={t("mergeTarget")}
            />
            {targetData.data && (
              <Alert>
                <b>{targetData.data.display_name}</b>
                <p>
                  {t("conversations")}: {targetData.data.conversation_count} ·{" "}
                  {t("tasks")}: {targetData.data.open_tasks}
                </p>
              </Alert>
            )}
            <Button
              color="orange"
              loading={busy}
              disabled={!targetData.data || targetData.data.id === c.id}
              onClick={() =>
                void mutate("/merge", {
                  version: c.version,
                  into_id: targetData.data?.id,
                  into_version: targetData.data?.version,
                  confirm: true,
                })
              }
            >
              {t("mergeConfirm")}
            </Button>
          </div>
        )}
        {action === "detach" && detach && (
          <div className="customer-form">
            <b>{detach.value}</b>
            <p>{t("detachHelp")}</p>
            <TextInput
              label={t("newName")}
              value={newName}
              onChange={(e) => setNewName(e.currentTarget.value)}
            />
            <Button
              color="orange"
              loading={busy}
              onClick={() =>
                void mutate("/identities/" + detach.id + "/detach", {
                  version: c.version,
                  name: newName,
                  confirm: true,
                })
              }
            >
              {t("detachConfirm")}
            </Button>
          </div>
        )}
      </Modal>
    </div>
  );
}

export function CustomerSidebar({ id }: { id: string }) {
  const { t } = useTranslation("customers");
  const data = useQuery({
    queryKey: ["customer", id],
    queryFn: () => api<CustomerDetail>("/api/customers/" + id),
    refetchInterval: 15000,
  });
  return (
    <section className="customer-sidebar">
      {data.error && <Alert color="red">{err(data.error)}</Alert>}
      <h3 dir="auto">{data.data?.display_name || t("profile")}</h3>
      <div className="customer-tags">
        {data.data?.tags.map((tag) => (
          <Badge key={tag} variant="light">
            {tag}
          </Badge>
        ))}
      </div>
      <p>
        {t("conversations")}: {data.data?.conversation_count ?? "…"} ·{" "}
        {t("tasks")}: {data.data?.open_tasks ?? "…"}
      </p>
      <Button variant="light" fullWidth onClick={() => openCustomer(id)}>
        {t("open")}
      </Button>
    </section>
  );
}

export function CustomersPage({ user }: { user: User }) {
  const { t } = useTranslation("customers");
  const query = useQueryClient();
  const initial = () =>
    location.pathname.match(/^\/customers\/([^/]+)$/)?.[1] ||
    sessionStorage.getItem("crm-open-customer");
  const [selected, setSelected] = useState<string | null>(initial);
  const [create, setCreate] = useState(false);
  const [text, setText] = useState("");
  const [search, setSearch] = useState("");
  const [segment, setSegment] = useState<string | null>(null);
  const [country, setCountry] = useState("");
  const [tag, setTag] = useState("");
  const [hasOpen, setHasOpen] = useState(false);
  const [page, setPage] = useState(0);
  useEffect(() => {
    sessionStorage.removeItem("crm-open-customer");
    const open = (e: Event) => {
      setSelected((e as CustomEvent<string>).detail);
      sessionStorage.removeItem("crm-open-customer");
    };
    const pop = () => setSelected(initial());
    window.addEventListener("crm:customer", open);
    window.addEventListener("popstate", pop);
    return () => {
      window.removeEventListener("crm:customer", open);
      window.removeEventListener("popstate", pop);
    };
  }, []);
  useEffect(() => {
    const timer = setTimeout(() => {
      setSearch(text);
      setPage(0);
    }, 250);
    return () => clearTimeout(timer);
  }, [text]);
  const filter = new URLSearchParams({
    q: search,
    segment: segment || "",
    country,
    tag,
    has_open: String(hasOpen),
  }).toString();
  const list = useQuery({
    queryKey: ["customers", filter, page],
    queryFn: () =>
      api<CustomersResult>("/api/customers?" + filter + "&offset=" + page * 50),
    enabled: !selected,
    refetchInterval: 15000,
  });
  function show(id: string) {
    openCustomer(id);
    setSelected(id);
  }
  if (selected)
    return (
      <CustomerView
        key={selected}
        id={selected}
        user={user}
        onOpen={show}
        onBack={() => {
          history.pushState({}, "", "/customers");
          setSelected(null);
          sessionStorage.removeItem("crm-open-customer");
        }}
      />
    );
  return (
    <div className="customer-page">
      <div className="customer-actions">
        {user.role !== "staff" && (
          <>
            <Button onClick={() => setCreate(true)}>{t("create")}</Button>
            <Button
              component="a"
              href={"/api/customers/export?" + filter}
              variant="default"
            >
              {t("export")}
            </Button>
          </>
        )}
      </div>
      <section className="surface customer-filters">
        <TextInput
          aria-label={t("search")}
          placeholder={t("search")}
          value={text}
          onChange={(e) => setText(e.currentTarget.value)}
        />
        <Select
          aria-label={t("segment")}
          placeholder={t("segment")}
          value={segment}
          clearable
          onChange={(v) => {
            setSegment(v);
            setPage(0);
          }}
          data={segments.map((value) => ({ value, label: t(value) }))}
        />
        <TextInput
          aria-label={t("country")}
          placeholder={t("country")}
          value={country}
          maxLength={2}
          onChange={(e) => {
            setCountry(e.currentTarget.value.toUpperCase());
            setPage(0);
          }}
        />
        <TextInput
          aria-label={t("tags")}
          placeholder={t("tags")}
          value={tag}
          onChange={(e) => {
            setTag(e.currentTarget.value);
            setPage(0);
          }}
        />
        <Checkbox
          label={t("openOnly")}
          checked={hasOpen}
          onChange={(e) => {
            setHasOpen(e.currentTarget.checked);
            setPage(0);
          }}
        />
      </section>
      {list.isLoading && <Loader />}
      {list.error && <Alert color="red">{err(list.error)}</Alert>}
      <section className="surface customer-section">
        <div className="customer-table">
          <Table striped highlightOnHover>
            <Table.Thead>
              <Table.Tr>
                {[
                  "name",
                  "phone",
                  "segment",
                  "country",
                  "conversations",
                  "tasks",
                ].map((k) => (
                  <Table.Th key={k}>{t(k)}</Table.Th>
                ))}
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {list.data?.items.map((c) => (
                <Table.Tr key={c.id}>
                  <Table.Td>
                    <button
                      className="customer-name"
                      onClick={() => show(c.id)}
                    >
                      {c.display_name}
                    </button>
                    <div className="customer-tags">
                      {c.tags.map((tag) => (
                        <Badge key={tag} size="xs" variant="light">
                          {tag}
                        </Badge>
                      ))}
                    </div>
                  </Table.Td>
                  <Table.Td>
                    {c.phones.map((p) => (
                      <div key={p.id}>{p.value}</div>
                    ))}
                  </Table.Td>
                  <Table.Td>{c.segment ? t(c.segment) : "—"}</Table.Td>
                  <Table.Td>{c.country || "—"}</Table.Td>
                  <Table.Td>{c.conversation_count}</Table.Td>
                  <Table.Td>{c.open_tasks}</Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        </div>
        {!list.isLoading && !list.data?.items.length && <p>{t("empty")}</p>}
        <div className="customer-pagination">
          <span>
            {t("total")}: {list.data?.total ?? 0}
          </span>
          <Button
            variant="light"
            disabled={!page}
            onClick={() => setPage((p) => p - 1)}
          >
            {t("previous")}
          </Button>
          <Button
            variant="light"
            disabled={(page + 1) * 50 >= (list.data?.total || 0)}
            onClick={() => setPage((p) => p + 1)}
          >
            {t("next")}
          </Button>
        </div>
      </section>
      <Modal
        opened={create}
        onClose={() => setCreate(false)}
        title={t("create")}
        size="lg"
      >
        <ProfileForm
          onCancel={() => setCreate(false)}
          onDone={(c) => {
            setCreate(false);
            refresh(query);
            show(c.id);
          }}
        />
      </Modal>
    </div>
  );
}
