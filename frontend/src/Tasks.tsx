import Markdown from "react-markdown";
import { useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Alert,
  Badge,
  Button,
  Checkbox,
  Drawer,
  FileInput,
  Loader,
  Modal,
  MultiSelect,
  NumberInput,
  Select,
  Table,
  TagsInput,
  Textarea,
  TextInput,
} from "@mantine/core";
import { Bell, Plus, Settings2 } from "lucide-react";
import { useTranslation } from "react-i18next";
import { api, uploadFile, type Conversation, type User } from "./api";
import { taskLabel } from "./task-i18n";
import "./tasks.css";

type Status = {
  id: string;
  name: string;
  color: string;
  terminal: boolean;
  position: number;
};
type Board = {
  id: string;
  name: string;
  position: number;
  archived: boolean;
  statuses: Status[];
};
type Kind = { id: string; name: string; active: boolean; position: number };
type Member = { id: string; name: string; role: string };
type Task = {
  id: string;
  code: string;
  number: number;
  version: number;
  title: string;
  description: string;
  board_id: string;
  board_name: string;
  status_id: string;
  status: Status;
  type_id: string;
  type_name: string;
  priority: string;
  author_id: string;
  author_name: string;
  assignee_id: string | null;
  assignee_name: string;
  due_at: string | null;
  conversation_id: string | null;
  customer_name: string;
  order_no: string;
  part_no: string;
  quantity: number | null;
  labels: string[];
  watchers: string[];
  attachment_ids: string[];
  created_at: string;
  updated_at: string;
  completed_at: string | null;
  overdue: boolean;
};
type Detail = Task & {
  comments: {
    id: string;
    author_name: string;
    text: string;
    created_at: string;
  }[];
  history: {
    id: string;
    user_name: string;
    changes: Record<string, [unknown, unknown]>;
    created_at: string;
  }[];
  attachments: { id: string; name: string; url: string; size: number }[];
};
type TaskPage = { items: Task[]; total: number };
type TaskEvent = { key?: string; conversation?: Conversation };
type Notice = {
  id: string;
  read_at: string | null;
  created_at: string;
  payload: { number: number; title: string; text: string };
};
const errorText = (e: unknown) => (e instanceof Error ? e.message : String(e));
const options = (items: { id: string; name: string }[] | undefined) =>
  (items || []).map((x) => ({ value: x.id, label: x.name }));
export const openTask = (key: string) =>
  window.dispatchEvent(
    new CustomEvent<TaskEvent>("crm:task", { detail: { key } }),
  );
const createTask = (conversation?: Conversation) =>
  window.dispatchEvent(
    new CustomEvent<TaskEvent>("crm:task", { detail: { conversation } }),
  );
const currentTask = () =>
  /^\/tasks\/(T-\d+)$/.exec(location.pathname)?.[1] || null;
function useCatalogs() {
  return {
    boards: useQuery({
      queryKey: ["boards"],
      queryFn: () => api<Board[]>("/api/boards"),
    }),
    types: useQuery({
      queryKey: ["task-types"],
      queryFn: () => api<Kind[]>("/api/task-types"),
    }),
    team: useQuery({
      queryKey: ["team"],
      queryFn: () => api<Member[]>("/api/team"),
    }),
    config: useQuery({
      queryKey: ["tasks-config"],
      queryFn: () => api<{ timezone: string }>("/api/tasks-config"),
    }),
  };
}
const dateText = (v: string | null, zone = "Asia/Seoul") =>
  v
    ? new Intl.DateTimeFormat("ru-RU", {
        timeZone: zone,
        dateStyle: "short",
        timeStyle: "short",
      }).format(new Date(v))
    : "—";
function wallTime(value: string, zone: string) {
  const parts = new Intl.DateTimeFormat("sv-SE", {
    timeZone: zone,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  }).formatToParts(new Date(value));
  const p = Object.fromEntries(parts.map((x) => [x.type, x.value]));
  return `${p.year}-${p.month}-${p.day}T${p.hour}:${p.minute}`;
}
function toIso(value: string, zone: string) {
  const target = Date.parse(value + ":00Z");
  let guess = target;
  for (let i = 0; i < 3; i++)
    guess +=
      target -
      Date.parse(wallTime(new Date(guess).toISOString(), zone) + ":00Z");
  if (wallTime(new Date(guess).toISOString(), zone) !== value)
    throw new Error(taskLabel("dateError"));
  return new Date(guess).toISOString();
}
function refresh(query: ReturnType<typeof useQueryClient>) {
  for (const key of [
    "tasks",
    "task",
    "task-report",
    "notifications",
    "conversations",
    "conversation",
    "messages",
    "conversation-counts",
  ])
    void query.invalidateQueries({ queryKey: [key] });
}

function TaskForm({
  task,
  conversation,
  onDone,
  onCancel,
}: {
  task?: Detail;
  conversation?: Conversation;
  onDone: (task: Task) => void;
  onCancel: () => void;
}) {
  const { t } = useTranslation("tasks");
  const { boards, types, team, config } = useCatalogs();
  const zone = config.data?.timezone || "Asia/Seoul";
  const [title, setTitle] = useState(task?.title || "");
  const [description, setDescription] = useState(task?.description || "");
  const [boardId, setBoard] = useState(task?.board_id || "");
  const [statusId, setStatus] = useState(task?.status_id || "");
  const [typeId, setType] = useState(task?.type_id || "");
  const [priority, setPriority] = useState(task?.priority || "normal");
  const [assignee, setAssignee] = useState(task?.assignee_id || null);
  const [due, setDue] = useState(
    task?.due_at ? wallTime(task.due_at, zone) : "",
  );
  const [order, setOrder] = useState(
    task?.order_no || conversation?.order_numbers[0] || "",
  );
  const [part, setPart] = useState(task?.part_no || "");
  const [qty, setQty] = useState<number | string>(task?.quantity || "");
  const [labels, setLabels] = useState(task?.labels || []);
  const [watchers, setWatchers] = useState(task?.watchers || []);
  const [attachments, setAttachments] = useState(task?.attachments || []);
  const [file, setFile] = useState<File | null>(null);
  const [completion, setCompletion] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const clientId = useRef(crypto.randomUUID());
  const board =
    boards.data?.find((b) => b.id === boardId) ||
    boards.data?.find((b) => !b.archived);
  const kind = typeId || types.data?.find((k) => k.active)?.id;
  const status = statusId || board?.statuses[0]?.id;
  async function save() {
    setBusy(true);
    setError("");
    try {
      let files = attachments;
      if (file) {
        if (file.size > 10 * 1024 * 1024) throw new Error(t("fileLimit"));
        const result = await uploadFile(file);
        files = [
          ...files,
          { id: result.id, name: result.filename, url: "", size: result.size },
        ];
        setAttachments(files);
        setFile(null);
      }
      const body = {
        title,
        description,
        board_id: board?.id,
        status_id: status,
        type_id: kind,
        priority,
        assignee_id: assignee,
        due_at: due ? toIso(due, zone) : null,
        conversation_id: task?.conversation_id || conversation?.id || null,
        order_no: order,
        part_no: part,
        quantity: qty ? Number(qty) : null,
        labels,
        watchers,
        attachment_ids: files.map((a) => a.id),
      };
      const result = await api<Task>(
        task ? "/api/tasks/" + task.id : "/api/tasks",
        task ? "PATCH" : "POST",
        task
          ? { ...body, version: task.version, completion_comment: completion }
          : { ...body, client_id: clientId.current },
      );
      onDone(result);
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  }
  if (boards.isLoading || types.isLoading || team.isLoading || config.isLoading)
    return <Loader />;
  const failed = boards.error || types.error || team.error || config.error;
  if (failed) return <Alert color="red">{errorText(failed)}</Alert>;
  return (
    <form
      className="task-form"
      onSubmit={(e) => {
        e.preventDefault();
        void save();
      }}
    >
      {error && <Alert color="red">{error}</Alert>}
      <TextInput
        label={t("title")}
        value={title}
        onChange={(e) => setTitle(e.currentTarget.value)}
        required
        maxLength={250}
      />
      <Textarea
        label={t("description")}
        description={t("markdown")}
        value={description}
        onChange={(e) => setDescription(e.currentTarget.value)}
        minRows={4}
        autosize
        maxLength={20000}
      />
      <div className="task-form-grid">
        <Select
          label={t("board")}
          required
          data={options(
            boards.data?.filter((b) => !b.archived || b.id === task?.board_id),
          )}
          value={board?.id || null}
          onChange={(v) => {
            setBoard(v || "");
            setStatus("");
          }}
        />
        <Select
          label={t("status")}
          data={options(board?.statuses)}
          value={status || null}
          onChange={(v) => setStatus(v || "")}
        />
        <Select
          label={t("type")}
          required
          data={options(
            types.data?.filter((k) => k.active || k.id === task?.type_id),
          )}
          value={kind || null}
          onChange={(v) => setType(v || "")}
        />
        <Select
          label={t("priority")}
          data={["low", "normal", "high", "urgent"].map((value) => ({
            value,
            label: t(value),
          }))}
          value={priority}
          onChange={(v) => setPriority(v || "normal")}
        />
        <Select
          label={t("assignee")}
          data={options(team.data)}
          value={assignee}
          onChange={setAssignee}
          searchable
          clearable
          placeholder={t("unassigned")}
        />
        <TextInput
          type="datetime-local"
          label={t("due") + " · " + zone}
          value={due}
          onChange={(e) => setDue(e.currentTarget.value)}
        />
      </div>
      <MultiSelect
        label={t("watchers")}
        data={options(team.data)}
        value={watchers}
        onChange={setWatchers}
        searchable
      />
      <TagsInput label={t("labels")} value={labels} onChange={setLabels} />
      <div className="task-form-grid">
        <TextInput
          label={t("order")}
          value={order}
          onChange={(e) => setOrder(e.currentTarget.value)}
          maxLength={100}
        />
        <TextInput
          label={t("part")}
          value={part}
          onChange={(e) => setPart(e.currentTarget.value)}
          maxLength={100}
        />
        <NumberInput
          label={t("qty")}
          min={1}
          max={1000000}
          allowDecimal={false}
          value={qty}
          onChange={setQty}
        />
      </div>
      {(task?.customer_name || conversation) && (
        <p className="muted">
          {t("linked")}:{" "}
          {task?.customer_name ||
            conversation?.display_name ||
            conversation?.name ||
            conversation?.phone}
        </p>
      )}
      <div className="task-file-list">
        {attachments.map((a) => (
          <div key={a.id}>
            {a.name}
            <Button
              variant="subtle"
              size="xs"
              onClick={() =>
                setAttachments((x) => x.filter((f) => f.id !== a.id))
              }
            >
              {t("remove")}
            </Button>
          </div>
        ))}
      </div>
      <FileInput
        label={t("attach")}
        value={file}
        onChange={setFile}
        clearable
      />
      {task &&
        board?.statuses.find((s) => s.id === status)?.terminal &&
        !task.completed_at && (
          <Textarea
            label={t("completionComment")}
            value={completion}
            onChange={(e) => setCompletion(e.currentTarget.value)}
          />
        )}
      <div className="task-actions">
        <Button variant="default" onClick={onCancel} disabled={busy}>
          {t("cancel")}
        </Button>
        <Button
          type="submit"
          loading={busy}
          disabled={!title.trim() || !board || !kind}
        >
          {t("save")}
        </Button>
      </div>
    </form>
  );
}

export function TaskDialogs() {
  const { t } = useTranslation("tasks");
  const query = useQueryClient();
  const [key, setKey] = useState<string | null>(currentTask);
  const [creating, setCreating] = useState(false);
  const [conversation, setConversation] = useState<Conversation>();
  const [editing, setEditing] = useState(false);
  const [comment, setComment] = useState("");
  const [mentions, setMentions] = useState<string[]>([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const previousPath = useRef("/");
  const catalogs = useCatalogs();
  const zone = catalogs.config.data?.timezone || "Asia/Seoul";
  const detail = useQuery({
    queryKey: ["task", key],
    queryFn: () => api<Detail>("/api/tasks/" + key),
    enabled: !!key,
    refetchInterval: editing ? false : 15000,
  });
  useEffect(() => {
    const handler = (event: Event) => {
      const d = (event as CustomEvent<TaskEvent>).detail;
      previousPath.current = location.pathname.startsWith("/tasks/")
        ? "/"
        : location.pathname;
      setEditing(false);
      setError("");
      setComment("");
      setMentions([]);
      setKey(d.key || null);
      setCreating(!d.key);
      setConversation(d.conversation);
      if (d.key) history.pushState({}, "", "/tasks/" + d.key);
    };
    const pop = () => {
      setKey(currentTask());
      setCreating(false);
      setEditing(false);
    };
    window.addEventListener("crm:task", handler);
    window.addEventListener("popstate", pop);
    return () => {
      window.removeEventListener("crm:task", handler);
      window.removeEventListener("popstate", pop);
    };
  }, []);
  function close() {
    setKey(null);
    setCreating(false);
    setEditing(false);
    history.replaceState({}, "", previousPath.current);
  }
  function saved(task: Task) {
    setCreating(false);
    setEditing(false);
    setKey(task.code);
    history.replaceState({}, "", "/tasks/" + task.code);
    refresh(query);
  }
  async function addComment() {
    setBusy(true);
    setError("");
    try {
      await api("/api/tasks/" + key + "/comments", "POST", {
        text: comment,
        mentions,
      });
      setComment("");
      setMentions([]);
      refresh(query);
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  }
  const task = detail.data;
  const showValue = (field: string, v: unknown): string => {
    if (v === null || v === undefined || v === "") return "—";
    if (Array.isArray(v)) return v.map((x) => showValue(field, x)).join(", ");
    if (field === "board_id")
      return catalogs.boards.data?.find((x) => x.id === v)?.name || String(v);
    if (field === "status_id")
      return (
        catalogs.boards.data?.flatMap((x) => x.statuses).find((x) => x.id === v)
          ?.name || String(v)
      );
    if (field === "type_id")
      return catalogs.types.data?.find((x) => x.id === v)?.name || String(v);
    if (["assignee_id", "watchers"].includes(field))
      return catalogs.team.data?.find((x) => x.id === v)?.name || String(v);
    if (field === "priority") return t(String(v));
    if (field === "due_at") return dateText(String(v), zone);
    if (field === "attachment_ids") return t("files");
    return String(v);
  };
  return (
    <>
      <Modal opened={creating} onClose={close} title={t("create")} size="lg">
        <TaskForm conversation={conversation} onDone={saved} onCancel={close} />
      </Modal>
      <Drawer
        opened={!!key}
        onClose={close}
        position="right"
        size="lg"
        title={key || t("details")}
      >
        {detail.isLoading && <Loader />}
        {detail.error && <Alert color="red">{errorText(detail.error)}</Alert>}
        {task &&
          (editing ? (
            <TaskForm
              key={task.id}
              task={task}
              onDone={saved}
              onCancel={() => setEditing(false)}
            />
          ) : (
            <div className="task-detail">
              <div className="task-detail-top">
                <Badge color={task.status.color}>{task.status.name}</Badge>
                <Badge color={task.priority === "urgent" ? "red" : "gray"}>
                  {t(task.priority)}
                </Badge>
                <Button variant="light" onClick={() => setEditing(true)}>
                  {t("edit")}
                </Button>
              </div>
              <h2>{task.title}</h2>
              <div className="task-description" dir="auto">
                <Markdown skipHtml>{task.description || "—"}</Markdown>
              </div>
              <dl className="task-facts">
                {[
                  [t("board"), task.board_name],
                  [t("type"), task.type_name],
                  [t("author"), task.author_name],
                  [t("assignee"), task.assignee_name],
                  [t("due"), dateText(task.due_at, zone)],
                  [t("createdAt"), dateText(task.created_at, zone)],
                  [t("completedAt"), dateText(task.completed_at, zone)],
                  [t("order"), task.order_no],
                  [t("part"), task.part_no],
                  [t("qty"), task.quantity || "—"],
                ].map(([label, value]) => (
                  <div key={String(label)}>
                    <dt>{label}</dt>
                    <dd
                      className={
                        label === t("due") && task.overdue ? "task-overdue" : ""
                      }
                    >
                      {value || "—"}
                    </dd>
                  </div>
                ))}
              </dl>
              {!!task.watchers.length && (
                <p>
                  {t("watchers")}:{" "}
                  {task.watchers
                    .map((id) => showValue("watchers", id))
                    .join(", ")}
                </p>
              )}
              <div className="task-tags">
                {task.labels.map((label) => (
                  <Badge key={label} variant="light">
                    {label}
                  </Badge>
                ))}
              </div>
              {task.conversation_id && (
                <Button
                  variant="subtle"
                  onClick={() => {
                    const id = task.conversation_id;
                    sessionStorage.setItem("crm-open-conversation", id!);
                    close();
                    window.dispatchEvent(
                      new CustomEvent("crm:conversation", { detail: id }),
                    );
                  }}
                >
                  {t("chat")} · {task.customer_name}
                </Button>
              )}
              <div className="task-file-list">
                {task.attachments.map((a) => (
                  <a key={a.id} href={a.url} target="_blank" rel="noreferrer">
                    {a.name} · {Math.ceil(a.size / 1024)} КБ
                  </a>
                ))}
              </div>
              <h3>{t("comments")}</h3>
              {task.comments.map((c) => (
                <article className="task-comment" key={c.id}>
                  <b>{c.author_name}</b>
                  <small>{dateText(c.created_at, zone)}</small>
                  <div dir="auto">{c.text}</div>
                </article>
              ))}
              {error && <Alert color="red">{error}</Alert>}
              <Textarea
                label={t("comment")}
                value={comment}
                onChange={(e) => setComment(e.currentTarget.value)}
                autosize
                minRows={3}
              />
              <MultiSelect
                label={t("mention")}
                data={options(catalogs.team.data)}
                value={mentions}
                onChange={setMentions}
                searchable
              />
              <Button
                onClick={() => void addComment()}
                loading={busy}
                disabled={!comment.trim()}
              >
                {t("addComment")}
              </Button>
              <details>
                <summary>
                  {t("history")} · {task.history.length}
                </summary>
                {task.history.map((h) => (
                  <article className="task-history" key={h.id}>
                    <small>
                      {dateText(h.created_at, zone)} · {h.user_name}
                    </small>
                    {Object.entries(h.changes).map(([field, values]) => (
                      <p key={field}>
                        {t(
                          (
                            {
                              board_id: "board",
                              status_id: "status",
                              type_id: "type",
                              assignee_id: "assignee",
                              due_at: "due",
                              order_no: "order",
                              part_no: "part",
                              quantity: "qty",
                              attachment_ids: "files",
                              conversation_id: "linked",
                            } as Record<string, string>
                          )[field] || field,
                        )}
                        : {showValue(field, values[0])} →{" "}
                        {showValue(field, values[1])}
                      </p>
                    ))}
                  </article>
                ))}
              </details>
            </div>
          ))}
      </Drawer>
    </>
  );
}

export function ConversationTasks({
  conversation,
}: {
  conversation: Conversation;
}) {
  const { t } = useTranslation("tasks");
  const tasks = useQuery({
    queryKey: ["tasks", "conversation", conversation.id],
    queryFn: () =>
      api<TaskPage>(
        "/api/tasks?conversation_id=" + conversation.id + "&view=open",
      ),
    refetchInterval: 20000,
  });
  return (
    <section className="conversation-tasks">
      <h4>
        {t("tasks")} <Badge size="sm">{tasks.data?.total || 0}</Badge>
      </h4>
      <Button
        variant="light"
        size="xs"
        leftSection={<Plus size={14} />}
        onClick={() => createTask(conversation)}
      >
        {t("createLinked")}
      </Button>
      {tasks.error && <Alert color="red">{errorText(tasks.error)}</Alert>}
      {tasks.data?.items.map((task) => (
        <button
          className="task-link"
          key={task.id}
          onClick={() => openTask(task.code)}
        >
          <b>{task.code}</b> {task.title}
          <small>
            {task.status.name} · {task.assignee_name}
          </small>
        </button>
      ))}
    </section>
  );
}

export function NotificationBell() {
  const { t } = useTranslation("tasks");
  const query = useQueryClient();
  const [opened, setOpened] = useState(false);
  const [page, setPage] = useState(0);
  const [error, setError] = useState("");
  const notices = useQuery({
    queryKey: ["notifications", page],
    queryFn: () =>
      api<{ unread: number; items: Notice[] }>(
        "/api/notifications?offset=" + page * 50,
      ),
    refetchInterval: 15000,
  });
  async function read(ids: string[]) {
    try {
      await api("/api/notifications/read", "POST", { ids });
      void query.invalidateQueries({ queryKey: ["notifications"] });
    } catch (e) {
      setError(errorText(e));
    }
  }
  return (
    <>
      <Button
        variant="subtle"
        leftSection={<Bell size={18} />}
        onClick={() => setOpened(true)}
        aria-label={t("notifications")}
      >
        {notices.data?.unread || 0}
      </Button>
      <Drawer
        position="right"
        opened={opened}
        onClose={() => setOpened(false)}
        title={t("notifications")}
        size="md"
      >
        <p className="muted">{t("inApp")}</p>
        {(error || notices.error) && (
          <Alert color="red">{error || errorText(notices.error)}</Alert>
        )}
        {!!notices.data?.items.length && (
          <Button
            variant="subtle"
            size="xs"
            onClick={() => void read(notices.data!.items.map((n) => n.id))}
          >
            {t("markRead")}
          </Button>
        )}
        {!notices.data?.items.length && <p>{t("noNotifications")}</p>}
        {notices.data?.items.map((n) => (
          <button
            key={n.id}
            className={"task-notice " + (!n.read_at ? "unread" : "")}
            onClick={() => {
              void read([n.id]);
              setOpened(false);
              openTask("T-" + n.payload.number);
            }}
          >
            <small>{n.payload.text}</small>
            <b>
              T-{n.payload.number} · {n.payload.title}
            </b>
            <small>{dateText(n.created_at)}</small>
          </button>
        ))}
        <div className="task-actions">
          <Button
            variant="subtle"
            disabled={!page}
            onClick={() => setPage((p) => p - 1)}
          >
            {t("prev")}
          </Button>
          <Button
            variant="subtle"
            disabled={(notices.data?.items.length || 0) < 50}
            onClick={() => setPage((p) => p + 1)}
          >
            {t("next")}
          </Button>
        </div>
      </Drawer>
    </>
  );
}

type ReportRow = {
  author_id: string;
  assignee_id: string | null;
  author_name: string;
  assignee_name: string;
  total: number;
  open: number;
  completed: number;
  overdue: number;
  hours: number | null;
};
function AssignmentReport({
  user,
  onDrill,
}: {
  user: User;
  onDrill: (filters: Record<string, string>) => void;
}) {
  const { t } = useTranslation("tasks");
  const { boards, types, config } = useCatalogs();
  const zone = config.data?.timezone || "Asia/Seoul";
  const today = wallTime(new Date().toISOString(), zone).slice(0, 10);
  const [start, setStart] = useState(today.slice(0, 8) + "01");
  const [end, setEnd] = useState(today);
  const [board, setBoard] = useState<string | null>(null);
  const [kind, setKind] = useState<string | null>(null);
  const nextDay = end
    ? new Date(Date.parse(end + "T00:00:00Z") + 86400000)
        .toISOString()
        .slice(0, 10)
    : "";
  const valid = !!start && !!end && end >= start;
  const params = new URLSearchParams(
    valid
      ? {
          start: toIso(start + "T00:00", zone),
          end: toIso(nextDay + "T00:00", zone),
          ...(board ? { board_id: board } : {}),
          ...(kind ? { type_id: kind } : {}),
        }
      : {},
  );
  const report = useQuery({
    queryKey: ["task-report", params.toString()],
    queryFn: () =>
      api<{ rows: ReportRow[] }>("/api/reports/assignments?" + params),
    enabled: valid,
  });
  return (
    <section className="surface task-report">
      <p>{t("reportHint")}</p>
      <p className="muted">
        {t(user.role === "admin" ? "reportAll" : "reportMine")}
      </p>
      <div className="task-filters">
        <TextInput
          type="date"
          label={t("periodFrom")}
          value={start}
          onChange={(e) => setStart(e.currentTarget.value)}
        />
        <TextInput
          type="date"
          label={t("periodTo")}
          value={end}
          onChange={(e) => setEnd(e.currentTarget.value)}
        />
        <Select
          label={t("board")}
          data={options(boards.data)}
          value={board}
          onChange={setBoard}
          clearable
        />
        <Select
          label={t("type")}
          data={options(types.data)}
          value={kind}
          onChange={setKind}
          clearable
        />
        <Button
          variant="light"
          disabled={!valid}
          component="a"
          href={"/api/reports/assignments?" + params + "&format=csv"}
        >
          {t("export")}
        </Button>
      </div>
      {!valid && <Alert color="orange">{t("dateError")}</Alert>}
      {report.error && <Alert color="red">{errorText(report.error)}</Alert>}
      {report.isLoading && <Loader />}
      <div className="task-table">
        <Table highlightOnHover>
          <Table.Thead>
            <Table.Tr>
              {[
                "filterAuthor",
                "filterAssignee",
                "total",
                "open",
                "completed",
                "overdueCount",
                "hours",
              ].map((k) => (
                <Table.Th key={k}>{t(k)}</Table.Th>
              ))}
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {report.data?.rows.map((r) => (
              <Table.Tr
                key={r.author_id + ":" + r.assignee_id}
                onClick={() =>
                  onDrill({
                    author_id: r.author_id,
                    assignee_id: r.assignee_id || "unassigned",
                    view: "all",
                    created_from: params.get("start")!,
                    created_to: params.get("end")!,
                    ...(board ? { board_id: board } : {}),
                    ...(kind ? { type_id: kind } : {}),
                    ...(user.role !== "admin" ? { related_me: "true" } : {}),
                  })
                }
                tabIndex={0}
                onKeyDown={(e) => {
                  if (e.key === "Enter") e.currentTarget.click();
                }}
              >
                <Table.Td>{r.author_name}</Table.Td>
                <Table.Td>{r.assignee_name}</Table.Td>
                <Table.Td>{r.total}</Table.Td>
                <Table.Td>{r.open}</Table.Td>
                <Table.Td>{r.completed}</Table.Td>
                <Table.Td className={r.overdue ? "task-overdue" : ""}>
                  {r.overdue}
                </Table.Td>
                <Table.Td>{r.hours ?? "—"}</Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      </div>
      {report.data?.rows.length === 0 && <p>{t("empty")}</p>}
    </section>
  );
}

export function TasksPage({ user }: { user: User }) {
  const { t } = useTranslation("tasks");
  const query = useQueryClient();
  const { boards, types, team, config } = useCatalogs();
  const [mode, setMode] = useState("kanban");
  const [filters, setFilters] = useState<Record<string, string>>({
    view: "open",
  });
  const [page, setPage] = useState(0);
  const [search, setSearch] = useState("");
  const [error, setError] = useState("");
  const [settings, setSettings] = useState(false);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    const timer = setTimeout(() => {
      setFilters((f) => ({ ...f, q: search }));
      setPage(0);
    }, 300);
    return () => clearTimeout(timer);
  }, [search]);
  const board =
    filters.board_id || boards.data?.find((b) => !b.archived)?.id || "";
  const params = new URLSearchParams({
    ...Object.fromEntries(Object.entries(filters).filter(([, v]) => v)),
    ...(mode === "kanban" ? { board_id: board } : {}),
    offset: String(page * 100),
  });
  const tasks = useQuery({
    queryKey: ["tasks", params.toString()],
    queryFn: () => api<TaskPage>("/api/tasks?" + params),
    refetchInterval: 15000,
    enabled: mode !== "report",
  });
  const change = (key: string, value: string | null) => {
    setFilters((f) => ({ ...f, [key]: value || "" }));
    setPage(0);
  };
  async function move(task: Task, status: string) {
    setError("");
    setBusy(true);
    try {
      await api("/api/tasks/" + task.id, "PATCH", {
        version: task.version,
        status_id: status,
      });
      refresh(query);
    } catch (e) {
      setError(errorText(e));
      void query.invalidateQueries({ queryKey: ["tasks"] });
    } finally {
      setBusy(false);
    }
  }
  const selectedBoard = boards.data?.find((b) => b.id === board);
  const zone = config.data?.timezone || "Asia/Seoul";
  return (
    <div className="tasks-page">
      <div className="task-toolbar">
        <div className="task-tabs">
          {["kanban", "list", "report"].map((m) => (
            <Button
              key={m}
              variant={mode === m ? "filled" : "default"}
              onClick={() => {
                setMode(m);
                setPage(0);
              }}
            >
              {t(m)}
            </Button>
          ))}
        </div>
        <div className="task-actions">
          {user.role === "admin" && (
            <Button
              variant="default"
              leftSection={<Settings2 size={16} />}
              onClick={() => setSettings(true)}
            >
              {t("settings")}
            </Button>
          )}
          <Button leftSection={<Plus size={16} />} onClick={() => createTask()}>
            {t("create")}
          </Button>
        </div>
      </div>
      {mode === "report" ? (
        <AssignmentReport
          user={user}
          onDrill={(f) => {
            setFilters(f);
            setMode("list");
            setPage(0);
          }}
        />
      ) : (
        <>
          <div className="task-filters surface">
            <TextInput
              className="task-search"
              placeholder={t("search")}
              value={search}
              onChange={(e) => setSearch(e.currentTarget.value)}
              aria-label={t("search")}
            />
            <Select
              aria-label={t("board")}
              placeholder={t("board")}
              data={options(boards.data)}
              value={mode === "kanban" ? board : filters.board_id || null}
              onChange={(v) => change("board_id", v)}
              clearable={mode !== "kanban"}
            />
            <Select
              aria-label={t("status")}
              placeholder={t("status")}
              data={options(
                boards.data
                  ?.filter(
                    (b) => !filters.board_id || b.id === filters.board_id,
                  )
                  .flatMap((b) => b.statuses),
              )}
              value={filters.status_id || null}
              onChange={(v) => change("status_id", v)}
              clearable
            />
            <Select
              aria-label={t("all")}
              data={["open", "all", "mine", "created", "overdue"].map(
                (value) => ({ value, label: t(value) }),
              )}
              value={filters.view || "open"}
              onChange={(v) => change("view", v)}
            />
            <Select
              aria-label={t("type")}
              placeholder={t("type")}
              data={options(types.data)}
              value={filters.type_id || null}
              onChange={(v) => change("type_id", v)}
              clearable
            />
            <Select
              aria-label={t("author")}
              placeholder={t("author")}
              data={options(team.data)}
              value={filters.author_id || null}
              onChange={(v) => change("author_id", v)}
              clearable
              searchable
            />
            <Select
              aria-label={t("assignee")}
              placeholder={t("assignee")}
              data={[
                { value: "unassigned", label: t("unassigned") },
                ...options(team.data),
              ]}
              value={filters.assignee_id || null}
              onChange={(v) => change("assignee_id", v)}
              clearable
              searchable
            />
            <TextInput
              placeholder={t("labelFilter")}
              aria-label={t("labelFilter")}
              value={filters.label || ""}
              onChange={(e) => change("label", e.currentTarget.value)}
            />
            <Select
              aria-label={t("sort")}
              data={["updated", "due", "created", "number"].map((value) => ({
                value,
                label: t(value),
              }))}
              value={filters.sort || "updated"}
              onChange={(v) => change("sort", v)}
            />
            <Button
              variant="subtle"
              onClick={() => {
                setFilters({ view: "open" });
                setSearch("");
                setPage(0);
              }}
            >
              {t("reset")}
            </Button>
            <Button
              variant="light"
              component="a"
              href={"/api/tasks/export?" + params}
            >
              {t("export")}
            </Button>
          </div>
          {(error || tasks.error || boards.error) && (
            <Alert color="red">
              {error || errorText(tasks.error || boards.error)}
            </Alert>
          )}
          {tasks.isLoading && <Loader />}
          {mode === "kanban" ? (
            <div className={"task-kanban " + (busy ? "busy" : "")}>
              {selectedBoard?.statuses.map((status) => (
                <section
                  className="task-column"
                  key={status.id}
                  onDragOver={(e) => {
                    e.preventDefault();
                    e.dataTransfer.dropEffect = "move";
                  }}
                  onDrop={(e) => {
                    e.preventDefault();
                    const task = tasks.data?.items.find(
                      (x) => x.id === e.dataTransfer.getData("text/plain"),
                    );
                    if (task && !busy && task.status_id !== status.id)
                      void move(task, status.id);
                  }}
                >
                  <h3>
                    <i style={{ background: status.color }} />
                    {status.name}
                    <span>
                      {tasks.data?.items.filter(
                        (x) => x.status_id === status.id,
                      ).length || 0}
                    </span>
                  </h3>
                  {tasks.data?.items
                    .filter((x) => x.status_id === status.id)
                    .map((task) => (
                      <article
                        key={task.id}
                        className={
                          "task-card " + (task.overdue ? "overdue" : "")
                        }
                        draggable={!busy}
                        onDragStart={(e) =>
                          e.dataTransfer.setData("text/plain", task.id)
                        }
                      >
                        <button onClick={() => openTask(task.code)}>
                          <small>
                            {task.code} · {task.type_name}
                          </small>
                          <strong>{task.title}</strong>
                          {task.customer_name && (
                            <span>↗ {task.customer_name}</span>
                          )}
                          <span>{task.assignee_name}</span>
                          <span className={task.overdue ? "task-overdue" : ""}>
                            {dateText(task.due_at, zone)}
                          </span>
                        </button>
                        <Select
                          size="xs"
                          aria-label={t("status") + " " + task.code}
                          data={options(selectedBoard.statuses)}
                          value={task.status_id}
                          disabled={busy}
                          onChange={(value) => {
                            if (value) void move(task, value);
                          }}
                        />
                      </article>
                    ))}
                </section>
              ))}
            </div>
          ) : (
            <div className="surface task-table">
              <Table highlightOnHover>
                <Table.Thead>
                  <Table.Tr>
                    {[
                      "number",
                      "title",
                      "status",
                      "assignee",
                      "due",
                      "customer",
                    ].map((k) => (
                      <Table.Th key={k}>{t(k)}</Table.Th>
                    ))}
                  </Table.Tr>
                </Table.Thead>
                <Table.Tbody>
                  {tasks.data?.items.map((task) => (
                    <Table.Tr
                      key={task.id}
                      onClick={() => openTask(task.code)}
                      tabIndex={0}
                      onKeyDown={(e) => {
                        if (e.key === "Enter") openTask(task.code);
                      }}
                    >
                      <Table.Td>{task.code}</Table.Td>
                      <Table.Td>
                        <b>{task.title}</b>
                        <small>
                          {task.board_name} · {task.type_name}
                        </small>
                      </Table.Td>
                      <Table.Td>
                        <Badge color={task.status.color}>
                          {task.status.name}
                        </Badge>
                      </Table.Td>
                      <Table.Td>{task.assignee_name}</Table.Td>
                      <Table.Td className={task.overdue ? "task-overdue" : ""}>
                        {dateText(task.due_at, zone)}
                      </Table.Td>
                      <Table.Td>{task.customer_name || "—"}</Table.Td>
                    </Table.Tr>
                  ))}
                </Table.Tbody>
              </Table>
            </div>
          )}
          {tasks.data?.total === 0 && (
            <div className="task-empty">{t("empty")}</div>
          )}
          <div className="task-footer">
            <small>
              {t("total")}: {tasks.data?.total || 0}
              {(tasks.data?.total || 0) > 100 && " · " + t("listLimit")}
            </small>
            <div className="task-actions">
              <Button
                variant="default"
                disabled={!page}
                onClick={() => setPage((p) => p - 1)}
              >
                {t("prev")}
              </Button>
              <span>
                {t("page")} {page + 1}
              </span>
              <Button
                variant="default"
                disabled={(page + 1) * 100 >= (tasks.data?.total || 0)}
                onClick={() => setPage((p) => p + 1)}
              >
                {t("next")}
              </Button>
            </div>
          </div>
        </>
      )}
      <Modal
        opened={settings}
        onClose={() => setSettings(false)}
        title={t("settings")}
        size="xl"
      >
        <BoardSettings />
      </Modal>
    </div>
  );
}

function BoardSettings() {
  const { t } = useTranslation("tasks");
  const query = useQueryClient();
  const { boards, types } = useCatalogs();
  const [boardId, setBoardId] = useState<string | null>(null);
  const [newBoard, setNewBoard] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const board = boards.data?.find((b) => b.id === boardId) || boards.data?.[0];
  async function run(path: string, method: string, body?: unknown) {
    setBusy(true);
    setError("");
    try {
      await api(path, method, body);
      for (const key of ["boards", "task-types"])
        await query.invalidateQueries({ queryKey: [key] });
      refresh(query);
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="task-form">
      <p className="muted">{t("tasksSettingsHint")}</p>
      {error && <Alert color="red">{error}</Alert>}
      <Select
        label={t("board")}
        data={options(boards.data)}
        value={board?.id || null}
        onChange={setBoardId}
      />
      {board && (
        <>
          <BoardEditor
            key={board.id + board.name + board.archived + board.position}
            board={board}
            busy={busy}
            run={run}
          />
          {board.statuses.map((status) => (
            <StatusEditor
              key={
                status.id +
                status.name +
                status.position +
                status.color +
                status.terminal
              }
              status={status}
              board={board}
              busy={busy}
              run={run}
            />
          ))}
          <StatusEditor
            key={board.id + "new" + board.statuses.length}
            board={board}
            busy={busy}
            run={run}
          />
        </>
      )}
      <div className="task-actions">
        <TextInput
          label={t("boardName")}
          value={newBoard}
          onChange={(e) => setNewBoard(e.currentTarget.value)}
        />
        <Button
          disabled={busy || !newBoard.trim()}
          onClick={() =>
            void run("/api/boards", "POST", {
              name: newBoard,
              position: boards.data?.length || 0,
            })
          }
        >
          {t("addBoard")}
        </Button>
      </div>
      <h3>{t("types")}</h3>
      {types.data?.map((kind) => (
        <TypeEditor
          key={kind.id + kind.name + kind.active}
          kind={kind}
          busy={busy}
          run={run}
        />
      ))}
      <TypeEditor key={"new" + types.data?.length} busy={busy} run={run} />
    </div>
  );
}
type Runner = (path: string, method: string, body?: unknown) => Promise<void>;
function BoardEditor({
  board,
  busy,
  run,
}: {
  board: Board;
  busy: boolean;
  run: Runner;
}) {
  const { t } = useTranslation("tasks");
  const [name, setName] = useState(board.name);
  const [position, setPosition] = useState<number | string>(board.position);
  const [archived, setArchived] = useState(board.archived);
  return (
    <div className="task-settings-row">
      <TextInput
        label={t("boardName")}
        value={name}
        onChange={(e) => setName(e.currentTarget.value)}
      />
      <NumberInput
        label={t("position")}
        value={position}
        min={0}
        onChange={setPosition}
      />
      <Checkbox
        label={t("archived")}
        checked={archived}
        onChange={(e) => setArchived(e.currentTarget.checked)}
      />
      <Button
        disabled={busy || !name.trim()}
        onClick={() =>
          void run("/api/boards/" + board.id, "PUT", {
            name,
            position: Number(position),
            archived,
          })
        }
      >
        {t("save")}
      </Button>
    </div>
  );
}
function StatusEditor({
  board,
  status,
  busy,
  run,
}: {
  board: Board;
  status?: Status;
  busy: boolean;
  run: Runner;
}) {
  const { t } = useTranslation("tasks");
  const [name, setName] = useState(status?.name || "");
  const [color, setColor] = useState(status?.color || "blue");
  const [terminal, setTerminal] = useState(status?.terminal || false);
  const [position, setPosition] = useState<number | string>(
    status?.position ?? board.statuses.length,
  );
  const [removing, setRemoving] = useState(false);
  const [moveTo, setMoveTo] = useState<string | null>(null);
  const path = "/api/boards/" + board.id + "/statuses";
  return (
    <div className="task-status-editor">
      <div className="task-settings-row">
        <TextInput
          label={t("statusName")}
          value={name}
          onChange={(e) => setName(e.currentTarget.value)}
        />
        <Select
          label={t("color")}
          data={["blue", "green", "orange", "red", "gray", "violet", "teal"]}
          value={color}
          onChange={(v) => setColor(v || "blue")}
        />
        <NumberInput
          label={t("position")}
          value={position}
          min={0}
          onChange={setPosition}
        />
        <Checkbox
          label={t("terminal")}
          checked={terminal}
          onChange={(e) => setTerminal(e.currentTarget.checked)}
        />
        <Button
          disabled={busy || !name.trim()}
          onClick={() =>
            void run(
              path + (status ? "/" + status.id : ""),
              status ? "PUT" : "POST",
              { name, color, terminal, position: Number(position) },
            )
          }
        >
          {t(status ? "save" : "addStatus")}
        </Button>
        {status && (
          <Button
            variant="subtle"
            color="red"
            disabled={busy || board.statuses.length < 2}
            onClick={() => setRemoving(!removing)}
          >
            {t("remove")}
          </Button>
        )}
      </div>
      {removing && (
        <Alert color="orange">
          <p>{t("removeHint")}</p>
          <Select
            label={t("moveTo")}
            data={options(board.statuses.filter((s) => s.id !== status?.id))}
            value={moveTo}
            onChange={setMoveTo}
          />
          <Button
            mt="sm"
            color="red"
            disabled={busy || !moveTo}
            onClick={() =>
              void run(path + "/" + status?.id + "?move_to=" + moveTo, "DELETE")
            }
          >
            {t("confirm")}
          </Button>
        </Alert>
      )}
    </div>
  );
}
function TypeEditor({
  kind,
  busy,
  run,
}: {
  kind?: Kind;
  busy: boolean;
  run: Runner;
}) {
  const { t } = useTranslation("tasks");
  const [name, setName] = useState(kind?.name || "");
  const [active, setActive] = useState(kind?.active ?? true);
  return (
    <div className="task-settings-row">
      <TextInput
        label={t("name")}
        value={name}
        onChange={(e) => setName(e.currentTarget.value)}
      />
      <Checkbox
        label={t("active")}
        checked={active}
        onChange={(e) => setActive(e.currentTarget.checked)}
      />
      <Button
        disabled={busy || !name.trim()}
        onClick={() =>
          void run(
            "/api/task-types" + (kind ? "?type_id=" + kind.id : ""),
            "POST",
            { name, active, position: kind?.position || 0 },
          )
        }
      >
        {t(kind ? "save" : "addType")}
      </Button>
    </div>
  );
}
