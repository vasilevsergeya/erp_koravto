import { useLayoutEffect, useRef, useState, type FormEvent } from "react";
import {
  useInfiniteQuery,
  useQueries,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import {
  ArrowLeft,
  BookOpen,
  ChevronRight,
  Inbox,
  Mail,
  Paperclip,
  RefreshCw,
  Search,
  UserRound,
  UserRoundCheck,
} from "lucide-react";
import { api } from "./api";
import { clockTime, dayKey, dayLabel, shortTime } from "./dialog-time";
import { LinkedText } from "./MessageTools";
import {
  OneCCandidates,
  OneCCustomerDetails,
  type OneCClient,
} from "./OneCCustomerDetails";
import "./mail.css";

type Contact = { name: string; email: string };
type Summary = {
  email: string;
  name: string;
  threadId: string;
  subject: string;
  snippet: string;
  at: number;
  unread: boolean;
};
type Letter = {
  id: string;
  subject: string;
  sender: Contact | null;
  recipients: Contact[];
  outgoing: boolean;
  at: number;
  text: string;
  attachments: string[];
};
type HistoryPage = { messages: Letter[]; next: string | null };
type MailPage = {
  mailbox: string | null;
  dialogs: Summary[];
  match_ticket?: string;
  next: string | null;
};
const display = (c?: Contact | null) =>
  c?.name || c?.email || "Неизвестный отправитель";

function Retry({ error, retry }: { error: Error; retry: () => void }) {
  return (
    <div className="mail-feedback" role="alert">
      <p>{error.message}</p>
      <button onClick={retry}>Повторить</button>
    </div>
  );
}

function CustomerPanel({
  email,
  mailbox,
  threadId,
}: {
  email: string;
  mailbox: string;
  threadId: string;
}) {
  const queryClient = useQueryClient();
  const customer = useQuery({
    queryKey: ["mail", "customer", mailbox, email, threadId],
    enabled: !!email,
    staleTime: 15 * 60_000,
    gcTime: 0,
    retry: false,
    queryFn: async () => {
      const result = await api<{ status: string; clients: OneCClient[] }>(
        "/api/mail/threads/" +
          encodeURIComponent(threadId) +
          "/customer?email=" +
          encodeURIComponent(email),
      );
      if (result.status === "ok")
        void queryClient.invalidateQueries({
          queryKey: ["mail", "customer-matches", mailbox],
        });
      return result;
    },
  });
  return (
    <>
      <div className="mail-pane-title">
        <UserRound size={16} />
        <h3>Клиент и заказы</h3>
      </div>
      <div className="mail-client-body">
        <div className="mail-contact">
          <Mail size={15} />
          <span>{email}</span>
        </div>
        <div className="mail-onec-heading">
          <strong>Данные из 1С</strong>
          <button
            className="mail-icon"
            title="Обновить данные 1С"
            aria-label="Обновить данные 1С"
            disabled={customer.isFetching}
            onClick={() => void customer.refetch()}
          >
            <RefreshCw size={14} />
          </button>
        </div>
        {customer.isLoading && (
          <p role="status" className="mail-muted">
            Ищем клиента по email…
          </p>
        )}
        {customer.error ? (
          <Retry error={customer.error} retry={() => void customer.refetch()} />
        ) : customer.data?.status === "not_configured" ? (
          <p className="mail-muted">Подключение к 1С ещё не настроено.</p>
        ) : customer.data?.clients.length === 1 ? (
          <OneCCustomerDetails compact client={customer.data.clients[0]} />
        ) : customer.data?.clients.length ? (
          <OneCCandidates compact candidates={customer.data.clients} />
        ) : (
          customer.data && (
            <p className="mail-muted">Клиент с этим email в 1С не найден.</p>
          )
        )}
      </div>
    </>
  );
}

export function MailPage({
  admin,
  openSettings,
}: {
  admin: boolean;
  openSettings: () => void;
}) {
  const [input, setInput] = useState("");
  const [search, setSearch] = useState("");
  const [selected, select] = useState("");
  const [pane, setPane] = useState("list");
  const pages = useInfiniteQuery({
    queryKey: ["mail", "dialogs", search],
    initialPageParam: "",
    gcTime: 0,
    staleTime: 30_000,
    retry: false,
    queryFn: ({ pageParam }) =>
      api<MailPage>(
        "/api/mail/dialogs?" +
          new URLSearchParams({ page: pageParam, q: search }),
      ),
    getNextPageParam: (last) => last.next ?? undefined,
  });
  const mailbox = pages.data?.pages[0].mailbox;
  const queryClient = useQueryClient();
  const tickets = [
    ...new Set(
      (pages.data?.pages ?? []).flatMap((p) =>
        p.match_ticket ? [p.match_ticket] : [],
      ),
    ),
  ];
  const matchPages = useQueries({
    queries: tickets.map((ticket) => ({
      queryKey: ["mail", "customer-matches", mailbox, ticket],
      queryFn: () =>
        api<{
          matches: Record<string, "email" | "phone">;
          onec_emails?: string[];
          pending: boolean;
        }>("/api/mail/dialogs/customer-matches", "POST", { ticket }),
      refetchInterval: (query: { state: { data?: { pending: boolean } } }) =>
        query.state.data?.pending ? 3_000 : 60_000,
      staleTime: 2_000,
      gcTime: 0,
      retry: false,
    })),
  });
  const matches = new Map<string, "email" | "phone">();
  for (const page of matchPages)
    for (const [email, reason] of Object.entries(page.data?.matches ?? {}))
      if (reason === "email" || !matches.has(email)) matches.set(email, reason);
  const onecEmails = new Set(
    matchPages.flatMap((page) => page.data?.onec_emails ?? []),
  );
  const byEmail = new Map<string, Summary>();
  for (const page of pages.data?.pages ?? [])
    for (const row of page.dialogs) {
      const previous = byEmail.get(row.email);
      if (!previous || row.at > previous.at) byEmail.set(row.email, row);
    }
  const rows = [...byEmail.values()].sort((a, b) => b.at - a.at);
  const active = byEmail.get(selected) || rows[0];
  const history = useInfiniteQuery({
    queryKey: ["mail", "history", mailbox, active?.email, active?.at],
    enabled:
      !!mailbox && !!active && (!pages.isError || pages.isFetchNextPageError),
    initialPageParam: "",
    staleTime: 30_000,
    gcTime: 0,
    retry: false,
    queryFn: ({ pageParam }) =>
      api<HistoryPage>(
        "/api/mail/dialogs/messages?" +
          new URLSearchParams({ email: active!.email, page: pageParam }),
      ),
    getNextPageParam: (last) => last.next ?? undefined,
  });
  const letters = new Map<string, Letter>();
  for (const page of history.data?.pages ?? [])
    for (const message of page.messages) letters.set(message.id, message);
  const ordered = [...letters.values()].sort(
    (a, b) => a.at - b.at || a.id.localeCompare(b.id),
  );
  const scroller = useRef<HTMLDivElement>(null);
  const scrollState = useRef({ email: "", oldest: "", height: 0 });
  useLayoutEffect(() => {
    const box = scroller.current;
    if (!box?.clientHeight || !active || !history.data) return;
    const oldest = ordered[0]?.id ?? "";
    const previous = scrollState.current;
    if (previous.email !== active.email) box.scrollTop = box.scrollHeight;
    else if (previous.oldest && previous.oldest !== oldest)
      box.scrollTop += box.scrollHeight - previous.height;
    scrollState.current = {
      email: active.email,
      oldest,
      height: box.scrollHeight,
    };
  });
  function submit(event: FormEvent) {
    event.preventDefault();
    select("");
    setSearch(input.trim());
    setPane("list");
  }
  function refresh() {
    void pages.refetch();
    void queryClient.invalidateQueries({
      queryKey: ["mail", "customer-matches"],
    });
    if (active) void history.refetch();
  }
  return (
    <section className="mail-page" aria-label="Почта Gmail">
      <header className="mail-top">
        <div>
          <h1>Почта</h1>
          {mailbox && <span className="mail-account">{mailbox}</span>}
        </div>
        <div>
          <span className="mail-readonly">
            <BookOpen size={14} />
            Только чтение
          </span>
          <button
            className="mail-icon"
            title="Обновить почту"
            aria-label="Обновить почту"
            onClick={refresh}
            disabled={pages.isFetching}
          >
            <RefreshCw size={16} />
          </button>
        </div>
      </header>
      {pages.isLoading ? (
        <div className="mail-empty" role="status">
          Загружаем переписку Gmail…
        </div>
      ) : pages.error && !pages.isFetchNextPageError ? (
        <div className="mail-empty">
          <Retry error={pages.error} retry={refresh} />
          {admin && <button onClick={openSettings}>Настройки Gmail</button>}
        </div>
      ) : !mailbox ? (
        <div className="mail-empty">
          <Mail size={28} />
          <h2>Подключите Gmail</h2>
          <p>Здесь появятся письма, карточки клиентов и их заказы из 1С.</p>
          {admin ? (
            <button onClick={openSettings}>Настроить подключение</button>
          ) : (
            <p>Подключение настраивает администратор ERP.</p>
          )}
        </div>
      ) : (
        <div className={"mail-grid show-" + pane}>
          <section className="mail-list" aria-label="Список переписки">
            <div className="mail-pane-title">
              <Inbox size={16} />
              <h2>Переписка</h2>
            </div>
            <form className="mail-search" onSubmit={submit}>
              <input
                type="search"
                placeholder="Тема, email или текст"
                aria-label="Поиск писем"
                value={input}
                maxLength={200}
                onChange={(e) => {
                  setInput(e.target.value);
                  if (!e.target.value) setSearch("");
                }}
              />
              <button className="mail-icon" aria-label="Найти письма">
                <Search size={15} />
              </button>
            </form>
            <div className="mail-rows">
              {rows.map((row) => (
                <button
                  key={row.email}
                  aria-pressed={row.email === active?.email}
                  className="mail-row"
                  onClick={() => {
                    select(row.email);
                    setPane("thread");
                  }}
                >
                  <span className="mail-row-meta">
                    <span>{row.name || row.email}</span>
                    <time>{shortTime(row.at)}</time>
                  </span>
                  {row.name && (
                    <span className="mail-row-address">{row.email}</span>
                  )}
                  {matches.has(row.email.toLowerCase()) && (
                    <span className="mail-customer-match">
                      <UserRoundCheck size={13} aria-hidden="true" />
                      {matches.get(row.email.toLowerCase()) === "email"
                        ? "Клиент ERP · email"
                        : "Клиент ERP · телефон из 1С"}
                    </span>
                  )}
                  {onecEmails.has(row.email.toLowerCase()) && (
                    <span className="mail-customer-match">
                      <UserRoundCheck size={13} aria-hidden="true" />
                      Клиент ERP · email из 1С
                    </span>
                  )}
                  <strong>
                    {row.unread && <i aria-label="Непрочитанное" />}
                    {row.subject || "Без темы"}
                  </strong>
                  <span className="mail-snippet">{row.snippet}</span>
                </button>
              ))}
              {!rows.length && (
                <p className="mail-feedback">
                  {search ? "По запросу ничего не найдено." : "Писем пока нет."}
                </p>
              )}
              {pages.isFetchNextPageError && (
                <Retry
                  error={pages.error!}
                  retry={() => void pages.fetchNextPage()}
                />
              )}
              {pages.hasNextPage && !pages.isFetchNextPageError && (
                <button
                  className="mail-more"
                  disabled={pages.isFetching}
                  onClick={() => void pages.fetchNextPage()}
                >
                  {pages.isFetchingNextPage ? "Загрузка…" : "Загрузить ещё"}
                </button>
              )}
            </div>
          </section>
          <section className="mail-thread" aria-label="Письма собеседника">
            <div className="mail-mobile-tools">
              <button onClick={() => setPane("list")}>
                <ArrowLeft size={14} />
                Переписка
              </button>
              <button onClick={() => setPane("client")}>
                Клиент и заказы
                <ChevronRight size={14} />
              </button>
            </div>
            {active ? (
              <>
                <div className="mail-subject">
                  <h2 dir="auto">{active.name || active.email}</h2>
                  {active.name && <small>{active.email}</small>}
                </div>
                <div className="mail-letters" key={active.email} ref={scroller}>
                  {history.isLoading && (
                    <p role="status" className="mail-muted">
                      Загружаем письма…
                    </p>
                  )}
                  {history.error && (
                    <Retry
                      error={history.error}
                      retry={() =>
                        void (history.isFetchNextPageError
                          ? history.fetchNextPage()
                          : history.refetch())
                      }
                    />
                  )}
                  {history.hasNextPage && !history.isFetchNextPageError && (
                    <button
                      className="mail-more"
                      disabled={history.isFetching}
                      onClick={() => void history.fetchNextPage()}
                    >
                      {history.isFetchingNextPage
                        ? "Загрузка…"
                        : "Показать более ранние письма"}
                    </button>
                  )}
                  {history.data && !ordered.length && !history.hasNextPage && (
                    <p className="mail-muted">
                      Писем с этим адресом больше нет.
                    </p>
                  )}
                  {ordered.map((m, i, messages) => (
                    <div key={m.id}>
                      {(i === 0 ||
                        dayKey(messages[i - 1].at) !== dayKey(m.at)) && (
                        <div className="crm-date-separator">
                          {dayLabel(m.at)}
                        </div>
                      )}
                      <article
                        className={
                          "mail-letter" + (m.outgoing ? " outgoing" : "")
                        }
                      >
                        <div className="mail-letter-heading">
                          <strong dir="auto">{display(m.sender)}</strong>
                          <time
                            dateTime={new Date(m.at).toISOString()}
                            title={dayLabel(m.at) + " · Сеул"}
                          >
                            {clockTime(m.at)}
                          </time>
                        </div>
                        <div className="mail-addresses">
                          {m.sender?.email}
                          {m.recipients.length > 0 && (
                            <>
                              {" "}
                              → {m.recipients.map((c) => c.email).join(", ")}
                            </>
                          )}
                        </div>
                        <strong className="mail-letter-subject" dir="auto">
                          {m.subject || "Без темы"}
                        </strong>
                        <div className="mail-text" dir="auto">
                          <LinkedText text={m.text} />
                        </div>
                        {m.attachments.map((name, index) => (
                          <span className="mail-file" key={index}>
                            <Paperclip size={13} />
                            {name}
                          </span>
                        ))}
                      </article>
                    </div>
                  ))}
                </div>
              </>
            ) : (
              <div className="mail-empty">Выберите собеседника</div>
            )}
          </section>
          <aside className="mail-client">
            <div className="mail-mobile-tools">
              <button onClick={() => setPane("thread")}>
                <ArrowLeft size={14} />К письмам
              </button>
            </div>
            {active && mailbox ? (
              <CustomerPanel
                key={active.email}
                email={active.email}
                mailbox={mailbox}
                threadId={active.threadId}
              />
            ) : (
              <div className="mail-pane-title">
                <UserRound size={16} />
                <h3>Клиент и заказы</h3>
              </div>
            )}
          </aside>
        </div>
      )}
    </section>
  );
}
