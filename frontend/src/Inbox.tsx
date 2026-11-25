import { Counterparties } from "./Counterparties";
import { CustomerAvatar } from "./CustomerAvatar";
import { CustomerFields, CustomerMarks } from "./CustomerMarks";
import { ProfileNote } from "./ProfileNote";
import { useMediaQuery } from "@mantine/hooks";
import { CustomerNumber } from "./CustomerNumber";
import { useDialogColumns } from "./DialogColumns";
import { memberOptions, type Member } from "./team";
import {
  Fragment,
  type ReactNode,
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import {
  useInfiniteQuery,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import {
  Alert,
  Badge,
  Button,
  Checkbox,
  FileButton,
  ActionIcon,
  Menu,
  Loader,
  Modal,
  Drawer,
  Popover,
  Select,
  Textarea,
  TextInput,
} from "@mantine/core";
import {
  Paperclip,
  Plus,
  SlidersHorizontal,
  PanelRightClose,
  PanelRightOpen,
  StickyNote,
  ListTodo,
  CheckCheck,
  Send,
  X,
} from "lucide-react";
import { useTranslation } from "react-i18next";
import {
  api,
  uploadFile,
  type Conversation,
  type Message,
  type User,
} from "./api";
import "./inbox.css";
import {
  MessageActions,
  LinkedText,
  TranslationPreferences,
  languageName,
  SendLanguageMenu,
} from "./MessageTools";
import { ConversationTasks, createTask, openTask } from "./Tasks";
import { CustomerSidebar } from "./Customers";
import { QuickReplyPicker } from "./QuickReplies";
import { DraftPanel } from "./AI";
import { DialogSummary, useSummaryLanguage } from "./DialogSummary";
import { loadDraft, saveDraft, removeDraft } from "./inbox-drafts";
import { taskLabel } from "./task-i18n";
import { OneCCandidates, OneCCustomerDetails, type OneCClient } from "./OneCCustomerDetails";

type Channel = {
  channelId: string;
  name: string;
  state: string;
  transport: string;
};
type OneCCustomer = {
  status: string;
  clients: OneCClient[];
  candidates?: OneCClient[];
  phone_country?: string;
  checked_at: string | null;
  stale: boolean;
  error: string | null;
};
function DetailField({
  label,
  value,
  children,
  disabled = false,
}: {
  label: string;
  value: string;
  children: ReactNode;
  disabled?: boolean;
}) {
  return (
    <div className="crm-detail-row">
      <span>{label}:</span>
      <Popover position="bottom-end" width={240} shadow="md">
        <Popover.Target>
          <button className="crm-inline-value" disabled={disabled}>
            {value || "—"}
          </button>
        </Popover.Target>
        <Popover.Dropdown>{children}</Popover.Dropdown>
      </Popover>
    </div>
  );
}
const regionName = (code?: string) => {
  if (!code) return "—";
  try {
    return new Intl.DisplayNames(["ru"], { type: "region" }).of(code) || code;
  } catch {
    return code;
  }
};
const time = (value: string) =>
  new Intl.DateTimeFormat("ru-RU", {
    dateStyle: "short",
    timeStyle: "short",
    timeZone: "Asia/Seoul",
  }).format(new Date(value));
const dayKey = (value: string) =>
  new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Seoul" }).format(
    new Date(value),
  );
const clockTime = (value: string) =>
  new Intl.DateTimeFormat("ru-RU", {
    hour: "2-digit",
    minute: "2-digit",
    timeZone: "Asia/Seoul",
  }).format(new Date(value));
const shortTime = (value: string) =>
  dayKey(value) === dayKey(new Date().toISOString())
    ? clockTime(value)
    : new Intl.DateTimeFormat("ru-RU", {
        day: "2-digit",
        month: "2-digit",
        timeZone: "Asia/Seoul",
      }).format(new Date(value));
const dayLabel = (value: string) =>
  dayKey(value) === dayKey(new Date().toISOString())
    ? "Сегодня"
    : new Intl.DateTimeFormat("ru-RU", {
        day: "numeric",
        month: "long",
        timeZone: "Asia/Seoul",
      }).format(new Date(value));
const errorText = (error: unknown) =>
  error instanceof Error ? error.message : String(error);

export function Inbox({ user }: { user: User }) {
  const { t } = useTranslation("inbox");
  const query = useQueryClient();
  const personalLanguage = useQuery({
    queryKey: ["manager-language"],
    queryFn: () => api<{ language: string }>("/api/me/language"),
  });
  const ownLanguage =
    personalLanguage.data?.language || user.manager_language || "en";
  const [selected, setSelected] = useState<string | null>(() =>
    new URLSearchParams(location.search).has("client")
      ? null
      : sessionStorage.getItem("crm-open-conversation") ||
        new URLSearchParams(location.search).get("conversation") ||
        sessionStorage.getItem(`crm-active-conversation:${user.id}`),
  );
  const narrow = useMediaQuery("(max-width:1099px)");
  const mobile = useMediaQuery("(max-width:699px)");
  const [noteOpened, setNoteOpened] = useState(0);
  const [noteHint, setNoteHint] = useState(false);
  const [keyboardHeight, setKeyboardHeight] = useState<number>();
  useEffect(() => {
    if (!mobile || !window.visualViewport) return;
    const viewport = window.visualViewport;
    const resize = () => setKeyboardHeight(viewport.height);
    resize();
    viewport.addEventListener("resize", resize);
    return () => viewport.removeEventListener("resize", resize);
  }, [mobile]);
  const columns = useDialogColumns(user.id);
  const [group, setGroup] = useState(
    () => localStorage.getItem(`crm-customer-filter:${user.id}`) || "all",
  );
  const [sort, setSort] = useState(() =>
    localStorage.getItem(`crm-dialog-sort:${user.id}`) === "oldest"
      ? "oldest"
      : "newest",
  );
  const [filter, setFilter] = useState(() => {
    const saved = localStorage.getItem(`crm-dialog-filter:${user.id}`);
    return saved && ["waiting_us", "mine", "ai_draft", "closed"].includes(saved)
      ? saved
      : "all";
  });
  const [panelOpen, setPanelOpen] = useState(() => {
    const saved = localStorage.getItem(`crm-customer-panel:${user.id}`);
    return saved === null ? window.innerWidth >= 1100 : saved === "true";
  });
  const list = useRef<HTMLDivElement>(null);
  const listEnd = useRef<HTMLDivElement>(null);
  const initialScroll = useRef<string | null>(null);
  const stickToBottom = useRef(true);
  const loadingOlder = useRef(false);
  const selectedRef = useRef(selected);
  selectedRef.current = selected;
  const [search, setSearch] = useState(
    () => new URLSearchParams(location.search).get("client") || "",
  );
  const [searchQuery, setSearchQuery] = useState(search);
  const initialDraft = useRef(loadDraft(user.id, selected));
  const [message, setMessage] = useState(initialDraft.current?.text || "");
  const [editingDraft, setEditingDraft] = useState<string | null>(
    initialDraft.current?.aiDraftId || null,
  );
  const [sourceLanguage, setSourceLanguage] = useState(
    initialDraft.current?.sourceLanguage || ownLanguage,
  );
  const [sendLanguage, setSendLanguage] = useState<string | null>(
    initialDraft.current?.targetLanguage || null,
  );
  const [sendError, setSendError] = useState(false);
  useEffect(() => {
    if (!message && !editingDraft) setSourceLanguage(ownLanguage);
  }, [message, editingDraft, ownLanguage]);
  const [file, setFile] = useState<File | null>(
    initialDraft.current?.file || null,
  );
  const [attachment, setAttachment] = useState<string | null>(
    initialDraft.current?.attachment || null,
  );
  const [note, setNote] = useState(initialDraft.current?.note || false);
  const [originals, setOriginals] = useState<Record<string, boolean>>({});
  const [quoted, setQuoted] = useState<Message | null>(
    initialDraft.current?.quoted || null,
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [sendId, setSendId] = useState<string>(
    () => initialDraft.current?.sendId || crypto.randomUUID(),
  );
  const [older, setOlder] = useState<Message[]>([]);
  const [hasOlder, setHasOlder] = useState(true);
  const [newOpen, setNewOpen] = useState(false);
  const [phone, setPhone] = useState("");
  const [name, setName] = useState("");
  const [country, setCountry] = useState(
    () => localStorage.getItem(`crm-dialog-country:${user.id}`) || "RU",
  );
  const [channel, setChannel] = useState<string | null>(null);
  const [firstText, setFirstText] = useState("");
  const [newId, setNewId] = useState<string>(() => crypto.randomUUID());
  useEffect(() => {
    const open = () => {
      const raw = sessionStorage.getItem("crm-new-conversation");
      if (raw) {
        try {
          const value = JSON.parse(raw);
          setPhone(value.phone || "");
          setName(value.name || "");
          setNewOpen(true);
          setNewId(crypto.randomUUID());
          setFirstText("");
        } catch {
          /* ignore invalid local state */
        }
        sessionStorage.removeItem("crm-new-conversation");
      }
    };
    open();
    window.addEventListener("crm:new-conversation", open);
    return () => window.removeEventListener("crm:new-conversation", open);
  }, []);
  const [order, setOrder] = useState("");
  const [cardOpen, setCardOpen] = useState(false);
  const [refreshingCustomer, setRefreshingCustomer] = useState(false);
  const [customerError, setCustomerError] = useState("");
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
        aiDraftId?: string | null;
        sourceLanguage?: string;
        targetLanguage?: string | null;
      }
    >
  >({});
  const canWrite = user.role === "admin" || user.role === "manager";
  const summaryPreference = useSummaryLanguage(user.id, canWrite, ownLanguage);
  useEffect(() => {
    if (selected)
      saveDraft(user.id, selected, {
        text: message,
        file,
        attachment,
        note,
        quoted,
        sendId,
        aiDraftId: editingDraft,
        sourceLanguage,
        targetLanguage: sendLanguage,
      });
  }, [
    user.id,
    selected,
    message,
    file,
    attachment,
    note,
    quoted,
    sendId,
    editingDraft,
    sourceLanguage,
    sendLanguage,
  ]);
  useEffect(() => {
    const timer = setTimeout(() => setSearchQuery(search), 250);
    return () => clearTimeout(timer);
  }, [search]);
  const params = new URLSearchParams({
    q: searchQuery,
    sort,
    group,
  });
  if (filter === "waiting_us" || filter === "closed")
    params.set("state", filter);
  if (filter === "mine") params.set("assignee", "me");
  if (filter === "ai_draft") params.set("ai_draft", "true");
  const listKey = params.toString();
  const conversations = useInfiniteQuery({
    queryKey: ["conversations", listKey],
    initialPageParam: 0,
    queryFn: ({ pageParam }) =>
      api<Conversation[]>(
        "/api/conversations?" + listKey + "&offset=" + pageParam,
      ),
    getNextPageParam: (last, pages) =>
      last.length === 50 ? pages.length * 50 : undefined,
    refetchInterval: 5000,
  });
  const dialogRows = useMemo(
    () =>
      Array.from(
        new Map(
          (conversations.data?.pages.flat() || []).map((c) => [c.id, c]),
        ).values(),
      ),
    [conversations.data],
  );
  const previousList = useRef({ key: listKey, rows: dialogRows });
  useLayoutEffect(() => {
    const previous = previousList.current;
    previousList.current = { key: listKey, rows: dialogRows };
    const el = list.current;
    if (!el) return;
    if (previous.key !== listKey) {
      el.scrollTop = 0;
      return;
    }
    // All dialog wrappers have the same height. Appending a page leaves the
    // existing rows in place, even if the user kept scrolling during the fetch.
    const height =
      el.querySelector<HTMLElement>(".crm-dialog-wrap")?.offsetHeight;
    if (!height || el.scrollTop <= 0) return;
    const first = Math.floor(el.scrollTop / height);
    if (previous.rows[first]?.id === dialogRows[first]?.id) return;

    // A live update can move a visible dialog to the top. Anchor on the
    // visible row that moved least, retaining its offset within the viewport.
    const positions = new Map(dialogRows.map((row, index) => [row.id, index]));
    const end = Math.min(
      previous.rows.length,
      Math.ceil((el.scrollTop + el.clientHeight) / height),
    );
    let shift: number | undefined;
    for (let i = first; i < end; i++) {
      const index = positions.get(previous.rows[i].id);
      if (
        index !== undefined &&
        (shift === undefined || Math.abs(index - i) < Math.abs(shift))
      )
        shift = index - i;
    }
    if (shift) el.scrollTop += shift * height;
  }, [listKey, dialogRows]);

  const { hasNextPage, isFetching, isError, fetchNextPage } = conversations;
  useEffect(() => {
    const root = list.current,
      tail = listEnd.current;
    if (!root || !tail || !hasNextPage || isFetching || isError) return;
    // Observing again after a refresh also checks a stationary scrollbar.
    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting && root.clientHeight > 0)
          void fetchNextPage({ cancelRefetch: false });
      },
      { root, rootMargin: "0px 0px 520px 0px" },
    );
    observer.observe(tail);
    return () => observer.disconnect();
  }, [
    listKey,
    hasNextPage,
    isFetching,
    isError,
    fetchNextPage,
    conversations.data,
  ]);
  const counters = useQuery({
    queryKey: ["conversation-counts", user.id, group],
    queryFn: () =>
      api<Record<string, number>>("/api/conversations/counts?group=" + group),
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
  const customer = useQuery({
    queryKey: ["onec-customer", selected],
    queryFn: () =>
      api<OneCCustomer>("/api/conversations/" + selected + "/customer"),
    enabled: !!selected,
    staleTime: 30000,
    refetchInterval: 60000,
    retry: false,
  });
  async function refreshCustomer() {
    if (!selected) return;
    const target = selected;
    setRefreshingCustomer(true);
    setCustomerError("");
    try {
      const result = await api<OneCCustomer>(
        "/api/conversations/" + target + "/customer/refresh",
        "POST",
      );
      query.setQueryData(["onec-customer", target], result);
      void query.invalidateQueries({ queryKey: ["conversation", target] });
      void query.invalidateQueries({ queryKey: ["conversations"] });
    } catch (e) {
      setCustomerError(errorText(e));
    } finally {
      setRefreshingCustomer(false);
    }
  }
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
    if (selected && current.data?.task_updated)
      void api("/api/conversations/" + selected + "/read", "POST")
        .then(() =>
          query.invalidateQueries({ queryKey: ["conversation", selected] }),
        )
        .catch(() => {});
  }, [selected, current.data?.task_updated, query]);
  useLayoutEffect(() => {
    if (stickToBottom.current && pane.current)
      pane.current.scrollTop = pane.current.scrollHeight;
  }, [message, file, quoted, note]);
  const lastId = messages.data?.at(-1)?.id;
  useEffect(() => {
    const element = pane.current;
    if (element && messages.data && initialScroll.current !== selected) {
      element.scrollTop = element.scrollHeight;
      initialScroll.current = selected;
    } else if (
      element &&
      element.scrollHeight - element.scrollTop - element.clientHeight < 350
    )
      end.current?.scrollIntoView({ block: "end" });
  }, [lastId, selected, messages.data]);
  const choose = useCallback(
    (id: string | null) => {
      if (selected)
        drafts.current[selected] = {
          text: message,
          file,
          attachment,
          note,
          quoted,
          sendId,
          aiDraftId: editingDraft,
          sourceLanguage,
          targetLanguage: sendLanguage,
        };
      const draft = id
        ? drafts.current[id] || loadDraft(user.id, id)
        : undefined;
      setNoteOpened(0);
      setNoteHint(false);
      initialScroll.current = null;
      stickToBottom.current = true;
      setSelected(id);
      setOlder([]);
      setHasOlder(true);
      setMessage(draft?.text || "");
      setEditingDraft(draft?.aiDraftId || null);
      setSourceLanguage(draft?.sourceLanguage || ownLanguage);
      setSendLanguage(draft?.targetLanguage || null);
      setSendError(false);
      setFile(draft?.file || null);
      setAttachment(draft?.attachment || null);
      setQuoted(draft?.quoted || null);
      setNote(draft?.note || false);
      setSendId(draft?.sendId || crypto.randomUUID());
      setError("");
      setCustomerError("");
    },
    [
      selected,
      message,
      file,
      attachment,
      note,
      quoted,
      sendId,
      user.id,
      editingDraft,
      sourceLanguage,
      sendLanguage,
      ownLanguage,
    ],
  );
  useEffect(() => {
    sessionStorage.removeItem("crm-open-conversation");
    const show = (event: Event) =>
      choose((event as CustomEvent<string>).detail);
    window.addEventListener("crm:conversation", show);
    return () => window.removeEventListener("crm:conversation", show);
  }, [choose]);
  const clientLink = useRef(new URLSearchParams(location.search).get("client"));
  useEffect(() => {
    const target = clientLink.current;
    const first = conversations.data?.pages[0]?.[0];
    if (target && searchQuery === target && first) {
      clientLink.current = null;
      choose(first.id);
    }
  }, [choose, conversations.data, searchQuery]);
  function attachFile(value: File | null) {
    if (value && value.size > 10 * 1024 * 1024) {
      setError(t("fileLimit"));
      return;
    }
    setFile(value);
    setAttachment(null);
  }
  async function send() {
    if (!selected || busy) return;
    setBusy(true);
    setError("");
    setSendError(false);
    const target = selected;
    try {
      let attachmentId = attachment;
      if (file && !attachmentId) {
        attachmentId = (await uploadFile(file)).id;
        if (selectedRef.current === target) setAttachment(attachmentId);
        else if (drafts.current[target]) {
          drafts.current[target] = {
            ...drafts.current[target],
            attachment: attachmentId,
          };
          saveDraft(user.id, target, drafts.current[target]);
        }
      }
      const language =
        sendLanguage ||
        (editingDraft
          ? current.data?.translation?.translation_client_language || ""
          : current.data?.translation?.translate_outgoing
            ? current.data.translation.translation_client_language
            : "original");
      if (!note && !language)
        throw new Error(
          "Язык клиента ещё не определён. Выберите язык отправки.",
        );
      if (editingDraft && !note) {
        await api("/api/drafts/" + editingDraft + "/send-edited", "POST", {
          final_text: message,
          source_language: sourceLanguage,
          target_language: language,
          attachment_id: attachmentId,
          quoted_message_id: quoted?.id || null,
        });
      } else {
        await api("/api/conversations/" + target + "/messages", "POST", {
          client_id: sendId,
          text: message,
          attachment_id: attachmentId,
          quoted_message_id: quoted?.id || null,
          is_private_note: note,
          source_language: sourceLanguage,
          target_language: language || "original",
        });
      }
      delete drafts.current[target];
      removeDraft(user.id, target);
      if (selectedRef.current === target) {
        setMessage("");
        setFile(null);
        setAttachment(null);
        setQuoted(null);
        setSendId(crypto.randomUUID());
        setEditingDraft(null);
        setSendLanguage(null);
        setSourceLanguage(ownLanguage);
      }
      void query.invalidateQueries({ queryKey: ["ai-draft", target] });
      refresh();
      setTimeout(() => {
        if (selectedRef.current === target)
          end.current?.scrollIntoView({ block: "end" });
      }, 150);
    } catch (e) {
      if (selectedRef.current === target) {
        setError(errorText(e));
        setSendError(true);
      }
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
    if (!before || !selected || loadingOlder.current) return;
    loadingOlder.current = true;
    const target = selected;
    const el = pane.current,
      height = el?.scrollHeight || 0,
      top = el?.scrollTop || 0;
    try {
      const rows = await api<Message[]>(
        "/api/conversations/" + selected + "/messages?before=" + before,
      );
      if (selectedRef.current !== target) return;
      setOlder((previous) => [...rows, ...previous]);
      setHasOlder(rows.length === 50);
      requestAnimationFrame(() => {
        if (el && selectedRef.current === target)
          el.scrollTop = top + el.scrollHeight - height;
      });
    } catch (e) {
      if (selectedRef.current === target) setError(errorText(e));
    } finally {
      loadingOlder.current = false;
    }
  }
  const oldMessages = useQuery({
    queryKey: ["messages", selected, "older", older.map((m) => m.id).join(",")],
    enabled: !!selected && older.length > 0,
    refetchInterval: 5000,
    queryFn: async () => {
      const rows: Message[] = [];
      for (let i = 0; i < older.length; i += 100)
        rows.push(
          ...(await api<Message[]>(
            "/api/conversations/" +
              selected +
              "/messages?ids=" +
              older
                .slice(i, i + 100)
                .map((m) => m.id)
                .join(","),
          )),
        );
      return rows;
    },
  });
  const allMessages = Array.from(
    new Map(
      [...(oldMessages.data || older), ...(messages.data || [])].map((m) => [
        m.id,
        m,
      ]),
    ).values(),
  ).sort(
    (a, b) =>
      a.created_at.localeCompare(b.created_at) || a.id.localeCompare(b.id),
  );
  const c = current.data;
  const onec = customer.data;
  const displayName = c?.display_name || c?.name || c?.phone;
  function openCard() {
    if (narrow) {
      if (!cardOpen)
        history.pushState({ ...history.state, crmCard: selected }, "");
      setCardOpen(true);
    } else {
      setPanelOpen(true);
      localStorage.setItem(`crm-customer-panel:${user.id}`, "true");
    }
  }
  function closeCard() {
    setNoteHint(false);
    setCardOpen(false);
    if (history.state?.crmCard) history.back();
  }
  useEffect(() => {
    const onBack = () => setCardOpen(false);
    window.addEventListener("popstate", onBack);
    return () => window.removeEventListener("popstate", onBack);
  }, []);
  const dialogTools = c && (
    <>
      {canWrite && (
        <DialogSummary
          key={c.id}
          conversationId={c.id}
          preference={summaryPreference}
        />
      )}
      {c.customer_id && (
        <TranslationPreferences
          key={c.customer_id}
          customerId={c.customer_id}
          canWrite={canWrite}
          original={!!originals[c.id]}
          onOriginal={(value) => setOriginals((p) => ({ ...p, [c.id]: value }))}
        />
      )}
    </>
  );
  const customerCard = (
    <div
      className="crm-customer-content"
      onPointerDown={(e) => {
        if (
          noteHint &&
          !(e.target as HTMLElement).closest(".crm-note-touch-tip")
        )
          setNoteHint(false);
      }}
    >
      {c?.customer_id && (
        <div className="crm-card-identity">
          <CustomerSidebar id={c.customer_id} compact />
          <CustomerNumber value={c.client_number} />
        </div>
      )}
      <DetailField
        label={t("chatName")}
        value={c?.name || ""}
        disabled={!canWrite}
      >
        <TextInput
          aria-label={t("chatName")}
          key={c?.id + ":" + c?.name}
          defaultValue={c?.name || ""}
          onBlur={(e) => {
            if (e.currentTarget.value !== c?.name)
              void change({ name: e.currentTarget.value });
          }}
        />
      </DetailField>
      <div className="crm-detail-row">
        <span>Телефон:</span>
        <a href={"tel:" + c?.phone}>{c?.phone || "—"}</a>
      </div>
      {onec?.clients.length === 1 && (
        <div className="crm-detail-row">
          <span>{t("onecOrders:email")}:</span>
          <b dir="auto" title={onec.clients[0].email}>
            {onec.clients[0].email || "—"}
          </b>
        </div>
      )}
      <div
        className="crm-detail-row"
        title="Регион телефонного номера, не место проживания клиента"
      >
        <span>Страна номера:</span>
        <b>{regionName(onec?.phone_country)}</b>
      </div>
      {c && <CustomerFields c={c} />}
      <DetailField
        label={t("assignee")}
        value={
          team.data?.find((u) => u.id === c?.assignee_id)?.name || "Не назначен"
        }
        disabled={!canWrite}
      >
        <Select
          aria-label={t("assignee")}
          value={c?.assignee_id || null}
          clearable
          comboboxProps={{ withinPortal: false }}
          data={memberOptions(team.data?.filter((u) => u.role !== "staff"))}
          onChange={(value) => void change({ assignee_id: value })}
        />
      </DetailField>
      {noteHint && c?.note_preview && (
        <button
          className="crm-note-touch-tip"
          title="Закрыть подсказку"
          onClick={() => setNoteHint(false)}
        >
          {c.note_preview} ×
        </button>
      )}
      {c?.customer_id && (
        <ProfileNote
          key={c.customer_id}
          id={c.customer_id}
          admin={user.role === "admin"}
          expanded={!!mobile}
          openRequest={noteOpened}
        />
      )}
      {mobile && <div className="crm-mobile-tools">{dialogTools}</div>}
      <section className="crm-onec" aria-label={t("onecCustomer")}>
        <div
          className="crm-detail-heading"
          title={
            onec?.checked_at
              ? t("onecChecked") + " " + time(onec.checked_at)
              : undefined
          }
        >
          <b>
            {onec?.status === "not_found"
              ? "Заказы · 1С: не найдены"
              : "Заказы · 1С"}
          </b>
          {canWrite && (
            <ActionIcon
              size="sm"
              variant="subtle"
              title={t("onecRefresh")}
              aria-label={t("onecRefresh")}
              loading={refreshingCustomer || customer.isFetching}
              onClick={() => void refreshCustomer()}
            >
              ↻
            </ActionIcon>
          )}
        </div>
        {customer.isLoading && (
          <p role="status">
            <Loader size="xs" /> {t("onecLoading")}
          </p>
        )}
        {(customer.error || customerError) && (
          <Alert color="orange">
            {customerError || errorText(customer.error)}
          </Alert>
        )}
        {onec && (
          <>
            {!["matched", "multiple", "not_found"].includes(onec.status) && (
              <p className="crm-muted" role="status">
                {t("onec_" + onec.status)}
              </p>
            )}
            {onec.error && onec.status === "unavailable" && (
              <p className="crm-muted">
                {t("onecError_" + onec.error, {
                  defaultValue: t("onec_unavailable"),
                })}
              </p>
            )}
            <OneCCandidates candidates={onec.candidates} compact />
            {onec.clients.map((client, index) => (
              <OneCCustomerDetails
                key={index}
                client={client}
                compact
                showEmail={false}
              />
            ))}
            {onec.stale && <p className="crm-onec-stale">{t("onecStale")}</p>}
            {onec.checked_at && onec.status !== "not_found" && (
              <small className="crm-muted">
                {t("onecChecked")} {time(onec.checked_at)}
              </small>
            )}
          </>
        )}
        {c?.customer_id && (
          <Counterparties
            key={c.customer_id}
            id={c.customer_id}
            write={canWrite}
          />
        )}
      </section>
      <div className="crm-detail-heading">
        <span>Заказы вручную: {c?.order_numbers.length || "нет"}</span>
        {canWrite && (
          <Popover position="bottom-end" width={230} shadow="md">
            <Popover.Target>
              <ActionIcon
                variant="subtle"
                size="sm"
                title={t("linkOrder")}
                aria-label={t("linkOrder")}
              >
                <Plus size={14} />
              </ActionIcon>
            </Popover.Target>
            <Popover.Dropdown>
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
            </Popover.Dropdown>
          </Popover>
        )}
      </div>
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
      {c && <ConversationTasks conversation={c} />}
      {canWrite && c?.state === "closed" && (
        <Button
          mt="sm"
          fullWidth
          variant="default"
          onClick={() => void change({ state: "waiting_us" })}
        >
          {t("reopen")}
        </Button>
      )}
    </div>
  );
  return (
    <div
      ref={columns.root}
      style={columns.style}
      className={
        "crm-inbox " +
        (selected ? "has-selection " : "") +
        (!panelOpen ? "panel-collapsed" : "")
      }
    >
      <aside className="crm-dialogs">
        {columns.handle("list")}
        <div className="crm-toolbar">
          <TextInput
            aria-label={t("search")}
            placeholder={"Номер, " + t("search").toLowerCase()}
            title={"Номер клиента, " + t("search").toLowerCase()}
            size="xs"
            value={search}
            onChange={(e) => setSearch(e.currentTarget.value)}
          />
          <Menu position="bottom-start" withinPortal>
            <Menu.Target>
              <Button
                size="xs"
                variant={filter === "all" ? "subtle" : "light"}
                title={sort === "oldest" ? "Сначала старые" : "Фильтр"}
                leftSection={<SlidersHorizontal size={13} />}
              >
                {filter === "all"
                  ? "Фильтр"
                  : filter === "ai_draft"
                    ? "AI"
                    : t(filter)}{" "}
                · {counters.data?.[filter] ?? 0}
                {sort === "oldest" && " ↑"}
              </Button>
            </Menu.Target>
            <Menu.Dropdown className="crm-filter-menu">
              {["all", "waiting_us", "mine", "ai_draft", "closed"].map((f) => (
                <Menu.Item
                  key={f}
                  color={filter === f ? "teal" : undefined}
                  rightSection={`${counters.data?.[f] ?? 0}${filter === f ? " ✓" : ""}`}
                  onClick={() => {
                    setFilter(f);
                    localStorage.setItem(`crm-dialog-filter:${user.id}`, f);
                  }}
                >
                  {f === "ai_draft" ? t("v1:aiDraft") : t(f)}
                </Menu.Item>
              ))}
              <Menu.Divider />
              {(["newest", "oldest"] as const).map((value) => (
                <Menu.Item
                  key={value}
                  color={sort === value ? "teal" : undefined}
                  rightSection={sort === value ? "✓" : undefined}
                  onClick={() => {
                    setSort(value);
                    localStorage.setItem(`crm-dialog-sort:${user.id}`, value);
                  }}
                >
                  {value === "newest" ? "Сначала новые" : "Сначала старые"}
                </Menu.Item>
              ))}
              <Menu.Divider />
              <Menu.Label>Клиенты</Menu.Label>
              {Object.entries({
                all: "Все",
                regular: "Постоянные",
                partners: "Партнёры (B2B)",
              }).map(([value, label]) => (
                <Menu.Item
                  key={value}
                  color={group === value ? "teal" : undefined}
                  rightSection={group === value ? "✓" : undefined}
                  onClick={() => {
                    setGroup(value);
                    localStorage.setItem(
                      `crm-customer-filter:${user.id}`,
                      value,
                    );
                  }}
                >
                  {label}
                </Menu.Item>
              ))}
            </Menu.Dropdown>
          </Menu>
          {canWrite && (
            <ActionIcon
              title={t("newDialog")}
              aria-label={t("newDialog")}
              onClick={() => {
                setError("");
                setNewOpen(true);
              }}
            >
              <Plus size={17} />
            </ActionIcon>
          )}
        </div>
        {conversations.isLoading && <Loader size="sm" m="md" />}
        {conversations.error && !conversations.isFetchNextPageError && (
          <Alert color="red">
            {errorText(conversations.error)}
            <Button size="xs" onClick={() => void conversations.refetch()}>
              Повторить
            </Button>
          </Alert>
        )}
        <div className="crm-dialog-list" ref={list}>
          {!dialogRows.length && !conversations.isLoading && (
            <p className="crm-empty">{t("noDialogs")}</p>
          )}
          {dialogRows.map((item) => (
            <div className="crm-dialog-wrap" key={item.id}>
              <button
                disabled={busy}
                data-dialog-id={item.id}
                className={`crm-dialog ${item.id === selected ? "selected" : ""} ${item.state}`}
                onClick={() => choose(item.id)}
                title={team.data?.find((u) => u.id === item.assignee_id)?.name}
              >
                <CustomerAvatar c={item} />
                <div className="crm-dialog-line">
                  <b>{item.display_name || item.name || item.phone}</b>
                  {item.last_has_attachment && (
                    <Paperclip size={12} aria-label={t("attachment")} />
                  )}
                  <time title={time(item.updated_at)}>
                    {shortTime(item.updated_at)}
                  </time>
                </div>
                <div className="crm-dialog-line">
                  <p>{item.last_message || " "}</p>
                  {item.has_ai_draft && (
                    <Badge size="xs" color="violet" title={t("v1:aiDraft")}>
                      AI
                    </Badge>
                  )}
                  {item.task_updated && (
                    <Badge
                      size="xs"
                      color="orange"
                      title={taskLabel("taskUpdate")}
                    >
                      •
                    </Badge>
                  )}
                  {item.unread_count > 0 && (
                    <Badge size="xs">{item.unread_count}</Badge>
                  )}
                </div>
              </button>
              <span className="crm-row-marks">
                <CustomerMarks c={item} compact />
              </span>
            </div>
          ))}
          {hasNextPage && (
            <div className="crm-list-end" ref={listEnd} aria-hidden="true" />
          )}
          {conversations.isFetchNextPageError && (
            <Alert color="red">
              {errorText(conversations.error)}
              <Button
                size="xs"
                onClick={() => void fetchNextPage({ cancelRefetch: false })}
              >
                {t("retry")}
              </Button>
            </Alert>
          )}
          {conversations.isFetchingNextPage && (
            <div className="crm-list-loading" role="status">
              <Loader size="xs" />
            </div>
          )}
        </div>
      </aside>
      {!selected ? (
        <div className="crm-empty crm-select-prompt">
          <h3>{t("selectDialog")}</h3>
          <p>{t("selectHint")}</p>
        </div>
      ) : (
        <>
          <section
            className="crm-thread"
            onDragOver={(event) => {
              if (
                canWrite &&
                !busy &&
                event.dataTransfer.types.includes("Files")
              )
                event.preventDefault();
            }}
            onDrop={(event) => {
              if (canWrite && !busy && event.dataTransfer.files.length) {
                event.preventDefault();
                attachFile(event.dataTransfer.files[0]);
              }
            }}
          >
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
              {c && <CustomerAvatar c={c} />}
              <div>
                <b title={displayName}>{displayName || t("loading")}</b>
                <small>{c?.phone}</small>
                <CustomerNumber value={c?.client_number} />
              </div>
              {c && <CustomerMarks c={c} />}
              {c && <Badge variant="light">{t(c.state)}</Badge>}
              {c?.note_preview && (
                <button
                  className="crm-note-icon"
                  title={c.note_preview}
                  aria-label="Заметка о клиенте"
                  onClick={() => {
                    setNoteOpened((v) => v + 1);
                    setNoteHint(window.matchMedia("(hover:none)").matches);
                    openCard();
                  }}
                >
                  <StickyNote size={15} />
                </button>
              )}
              {!mobile && dialogTools}
              <ActionIcon
                variant="subtle"
                aria-label={
                  !narrow && panelOpen
                    ? "Свернуть карточку"
                    : "Открыть карточку"
                }
                title={
                  !narrow && panelOpen
                    ? "Свернуть карточку"
                    : "Открыть карточку"
                }
                onClick={() => {
                  if (narrow) {
                    openCard();
                    return;
                  }
                  setPanelOpen(!panelOpen);
                  localStorage.setItem(
                    `crm-customer-panel:${user.id}`,
                    String(!panelOpen),
                  );
                }}
              >
                {!narrow && panelOpen ? (
                  <PanelRightClose size={18} />
                ) : (
                  <PanelRightOpen size={18} />
                )}
              </ActionIcon>
            </header>
            {channels.data?.find(
              (ch) => ch.channelId === c?.channel_id && ch.state !== "active",
            ) && <Alert color="orange">{t("inactiveChannel")}</Alert>}
            {error && !newOpen && (
              <Alert color="red" withCloseButton onClose={() => setError("")}>
                {error}
                {sendError && (
                  <Button
                    size="xs"
                    variant="subtle"
                    loading={busy}
                    onClick={() => void send()}
                  >
                    Повторить
                  </Button>
                )}
              </Alert>
            )}
            {messages.error && (
              <Alert color="red">{errorText(messages.error)}</Alert>
            )}
            {current.error && (
              <Alert color="red">{errorText(current.error)}</Alert>
            )}
            <div
              className="crm-messages"
              ref={pane}
              onScroll={(e) => {
                const el = e.currentTarget;
                stickToBottom.current =
                  el.scrollHeight - el.scrollTop - el.clientHeight < 80;
                if (
                  e.currentTarget.scrollTop < 100 &&
                  initialScroll.current === selected &&
                  hasOlder &&
                  (messages.data?.length || 0) >= 50
                )
                  void loadOlder();
              }}
            >
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
              {allMessages.map((m, index) => (
                <Fragment key={m.id}>
                  {(index === 0 ||
                    dayKey(m.created_at) !==
                      dayKey(allMessages[index - 1].created_at)) && (
                    <div className="crm-date-separator">
                      {dayLabel(m.created_at)}
                    </div>
                  )}
                  <article
                    className={
                      "crm-message " +
                      (index > 0 &&
                      allMessages[index - 1].author_id === m.author_id &&
                      allMessages[index - 1].direction === m.direction &&
                      allMessages[index - 1].author_type === m.author_type
                        ? "same-author "
                        : "") +
                      (m.author_type === "system"
                        ? "system"
                        : m.is_private_note
                          ? "private"
                          : m.direction === "out"
                            ? "out"
                            : "in")
                    }
                    dir="auto"
                    key={m.id}
                  >
                    {m.quoted_text && (
                      <blockquote dir="auto">
                        <LinkedText text={m.quoted_text} />
                      </blockquote>
                    )}
                    {m.content?.map_url && (
                      <a
                        href={m.content.map_url}
                        target="_blank"
                        rel="noreferrer"
                      >
                        {t("v1:map")}
                      </a>
                    )}
                    {m.kind === "vcard" && (
                      <div>
                        <b>{m.content?.name || t("v1:contact")}</b>
                        {m.content?.phones?.map((p) => (
                          <p key={p}>{p}</p>
                        ))}
                      </div>
                    )}
                    {m.deleted && <p>Сообщение удалено</p>}
                    {m.task_id && (
                      <a
                        href={"/tasks/T-" + m.task_number}
                        onClick={(e) => {
                          e.preventDefault();
                          openTask(m.task_id!);
                        }}
                      >
                        Открыть задачу T-{m.task_number}
                      </a>
                    )}
                    {m.text &&
                      (m.kind !== "vcard" || !m.content?.phones?.length) && (
                        <div className="crm-message-text">
                          <LinkedText
                            customerLinks={m.is_private_note}
                            text={
                              m.direction === "out"
                                ? (m.delivery_text ?? m.text)
                                : m.text
                            }
                          />
                        </div>
                      )}
                    {!originals[selected] &&
                      m.reading_translation?.state === "ready" &&
                      m.reading_translation.text &&
                      m.reading_translation.text !==
                        (m.direction === "out"
                          ? (m.delivery_text ?? m.text)
                          : m.text) && (
                        <div className="crm-manager-version" dir="auto">
                          <LinkedText text={m.reading_translation.text} />
                        </div>
                      )}
                    {!originals[selected] &&
                      m.reading_translation &&
                      m.reading_translation.state !== "ready" &&
                      !m.deleted && (
                        <div className="crm-translation-status">
                          {m.reading_translation.state === "error"
                            ? "Перевод недоступен. Показан оригинал."
                            : "Перевод готовится…"}
                          {m.reading_translation.state === "error" &&
                            canWrite && (
                              <button
                                onClick={() =>
                                  void api(
                                    "/api/messages/" +
                                      m.id +
                                      "/translation/retry",
                                    "POST",
                                  )
                                    .then(refresh)
                                    .catch((e) => setError(errorText(e)))
                                }
                              >
                                Повторить перевод
                              </button>
                            )}
                        </div>
                      )}
                    {!!m.history?.length && (
                      <details>
                        <summary>{t("v1:previousVersions")}</summary>
                        {m.history.map((v, i) => (
                          <p key={i}>
                            <LinkedText text={v.body} />
                          </p>
                        ))}
                      </details>
                    )}
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
                          <audio
                            controls
                            preload="none"
                            src={m.attachment_url}
                          />
                        ) : m.attachment_mime?.startsWith("video/") ? (
                          <video
                            controls
                            preload="none"
                            src={m.attachment_url}
                          />
                        ) : null}
                        {(!m.attachment_mime?.startsWith("image/") ||
                          m.attachment_mime === "image/svg+xml") && (
                          <a
                            href={m.attachment_url}
                            target="_blank"
                            rel="noreferrer"
                          >
                            <Paperclip size={12} />{" "}
                            {m.attachment_name || t("attachment")}
                          </a>
                        )}
                      </div>
                    )}
                    {m.kind === "missing_call" && <p>{t("missedCall")}</p>}
                    {m.error_text && (
                      <p className="crm-message-error">{m.error_text}</p>
                    )}
                    <footer dir="ltr">
                      {(m.direction === "out" ||
                        m.is_private_note ||
                        m.author_type === "system") && (
                        <span
                          className="crm-message-author"
                          title={m.author_name || undefined}
                        >
                          {m.is_private_note ? t("privateNote") + " · " : ""}
                          {m.author_name ? (
                            <bdi>{m.author_name}</bdi>
                          ) : m.author_type === "system" ? (
                            taskLabel("system")
                          ) : (
                            t(m.author_type)
                          )}
                        </span>
                      )}
                      {m.edited && <span>{t("edited")}</span>}
                      <time title={time(m.created_at)}>
                        {clockTime(m.created_at)}
                      </time>
                      {m.direction === "out" && (
                        <span>
                          {m.status === "translating"
                            ? "Ожидает перевода"
                            : t("status_" + m.status, {
                                defaultValue: m.status,
                              })}
                        </span>
                      )}
                      {canWrite &&
                        ["error", "uncertain"].includes(m.status) &&
                        m.author_type === "user" && (
                          <button onClick={() => void retry(m)}>
                            {t("retry")}
                          </button>
                        )}
                      {canWrite && (
                        <MessageActions
                          message={m}
                          user={user}
                          onChanged={refresh}
                          onReply={() => setQuoted(m)}
                        />
                      )}
                    </footer>
                  </article>
                </Fragment>
              ))}
              <div ref={end} />
            </div>
            <div className="crm-assistant-row">
              {/* Sibling keys must be distinct, including for the same dialog. */}
              <DraftPanel
                key={"draft:" + selected}
                conversationId={selected}
                canWrite={canWrite}
                isAdmin={user.role === "admin"}
                managerLanguage={ownLanguage}
                clientLanguage={c?.translation?.translation_client_language}
                onEdit={(id, text, source, target) => {
                  if (selectedRef.current !== selected) return;
                  setEditingDraft(id);
                  setMessage(text);
                  setSourceLanguage(source);
                  setSendLanguage(target);
                  setNote(false);
                  setSendId(crypto.randomUUID());
                  requestAnimationFrame(() =>
                    document
                      .querySelector<HTMLTextAreaElement>(
                        ".crm-editor textarea",
                      )
                      ?.focus(),
                  );
                }}
              />
              {canWrite && c && (
                <QuickReplyPicker
                  key={"quick-replies:" + c.id}
                  conversation={c}
                  text={message}
                  onInsert={setMessage}
                />
              )}
            </div>
            {canWrite ? (
              <div className={"crm-composer " + (note ? "is-note" : "")}>
                {editingDraft && (
                  <div className="crm-file-preview">
                    <span>
                      Правка AI-черновика · {languageName(sourceLanguage)}
                    </span>
                    <ActionIcon
                      size={18}
                      variant="subtle"
                      title="Продолжить как обычный ответ"
                      aria-label="Отменить связь с AI-черновиком"
                      onClick={() => setEditingDraft(null)}
                    >
                      <X size={13} />
                    </ActionIcon>
                  </div>
                )}
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
                {file && (
                  <div className="crm-file-preview">
                    <Paperclip size={12} />
                    <span>{file.name}</span>
                    <ActionIcon
                      size={18}
                      variant="subtle"
                      aria-label="Убрать файл"
                      disabled={busy}
                      onClick={() => attachFile(null)}
                    >
                      <X size={13} />
                    </ActionIcon>
                  </div>
                )}
                <div className="crm-editor">
                  <Textarea
                    aria-label={t("message")}
                    placeholder={note ? t("noteHint") : t("messageHint")}
                    value={message}
                    onChange={(event) => setMessage(event.currentTarget.value)}
                    autosize
                    minRows={1}
                    maxRows={7}
                    maxLength={10000}
                    dir="auto"
                    disabled={busy}
                    onPaste={(event) => {
                      const image = Array.from(event.clipboardData.items)
                        .find(
                          (item) =>
                            item.kind === "file" &&
                            item.type.startsWith("image/"),
                        )
                        ?.getAsFile();
                      if (image) {
                        event.preventDefault();
                        attachFile(image);
                      }
                    }}
                    onKeyDown={(event) => {
                      if (
                        (event.ctrlKey || event.metaKey) &&
                        event.key === "Enter"
                      ) {
                        event.preventDefault();
                        void send();
                      }
                    }}
                  />
                  <FileButton onChange={attachFile}>
                    {(props) => (
                      <ActionIcon
                        {...props}
                        className="crm-attach-button"
                        variant="subtle"
                        disabled={busy}
                        title={t("composeHint")}
                        aria-label={t("attach")}
                      >
                        <Paperclip size={18} />
                      </ActionIcon>
                    )}
                  </FileButton>
                </div>
                <div className="crm-compose-actions">
                  <Checkbox
                    size="xs"
                    title={t("privateNote")}
                    aria-label={t("privateNote")}
                    label={
                      <>
                        <StickyNote size={15} />
                        <span className="crm-action-label">
                          {t("privateNote")}
                        </span>
                      </>
                    }
                    checked={note}
                    onChange={(event) => setNote(event.currentTarget.checked)}
                    disabled={busy}
                  />
                  {c && c.inbound_seq > 0 && (
                    <Button
                      size="xs"
                      variant="subtle"
                      color={c.unread_count ? "teal" : "orange"}
                      title={
                        c.unread_count ? "Не требует ответа" : "Требует ответа"
                      }
                      aria-label={
                        c.unread_count ? "Не требует ответа" : "Требует ответа"
                      }
                      disabled={busy}
                      onClick={() =>
                        void api(
                          "/api/conversations/" + c.id + "/response-state",
                          "POST",
                          {
                            required: !c.unread_count,
                            inbound_seq: c.inbound_seq,
                          },
                        )
                          .then(refresh)
                          .catch((e) => {
                            setError(errorText(e));
                            refresh();
                          })
                      }
                    >
                      <CheckCheck size={15} />
                      <span className="crm-action-label">
                        {c.unread_count
                          ? "Не требует ответа"
                          : "Требует ответа"}
                      </span>
                    </Button>
                  )}
                  {c && (
                    <Button
                      size="xs"
                      variant="subtle"
                      title="Создать задачу"
                      aria-label="Создать задачу"
                      onClick={() => createTask(c)}
                    >
                      <ListTodo size={15} />
                      <span className="crm-action-label">Создать задачу</span>
                    </Button>
                  )}
                  <div className="crm-send-group">
                    <Button
                      className="crm-send"
                      size="xs"
                      loading={busy}
                      title={t("composeHint")}
                      disabled={!message.trim() && !file}
                      onClick={() => void send()}
                    >
                      <Send size={14} />
                      <span>
                        {note ? t("saveNote") : t("send")}
                        {!note &&
                        (sendLanguage ||
                          editingDraft ||
                          c?.translation?.translate_outgoing)
                          ? " · " +
                            languageName(
                              sendLanguage === "original"
                                ? sourceLanguage
                                : sendLanguage ||
                                    c?.translation
                                      ?.translation_client_language ||
                                    sourceLanguage,
                            )
                          : ""}
                      </span>
                    </Button>
                    {!note && (
                      <SendLanguageMenu
                        value={sendLanguage}
                        onChange={setSendLanguage}
                        managerLanguage={sourceLanguage}
                        clientLanguage={
                          editingDraft || c?.translation?.translate_outgoing
                            ? c?.translation?.translation_client_language
                            : sourceLanguage
                        }
                        disabled={busy}
                      />
                    )}
                  </div>
                </div>
              </div>
            ) : (
              <p className="crm-empty">{t("readOnly")}</p>
            )}
          </section>
          {!narrow && panelOpen && (
            <aside className="crm-customer">
              {columns.handle("panel")}
              {customerCard}
            </aside>
          )}
          <Drawer
            opened={!!narrow && cardOpen}
            onClose={closeCard}
            position="right"
            styles={
              mobile && keyboardHeight
                ? {
                    content: {
                      height: keyboardHeight,
                      maxHeight: keyboardHeight,
                    },
                  }
                : undefined
            }
            size={mobile ? "100%" : 320}
            title={t("customer")}
            className="crm-client-drawer"
            closeButtonProps={{ "aria-label": "Закрыть карточку" }}
          >
            {customerCard}
          </Drawer>
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
        {!phone.trim().startsWith("+") && (
          <>
            {" "}
            <Select
              mt="sm"
              label={t("country")}
              value={country}
              onChange={(value) => {
                const next = value || "RU";
                setCountry(next);
                localStorage.setItem(`crm-dialog-country:${user.id}`, next);
              }}
              data={[
                { value: "RU", label: "Россия +7" },
                { value: "KR", label: "대한민국 +82" },
                { value: "US", label: "United States +1" },
                { value: "GB", label: "United Kingdom +44" },
                { value: "AE", label: "الإمارات +971" },
              ]}
            />
            <small className="crm-muted">{t("countryHint")}</small>
          </>
        )}
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
