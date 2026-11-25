import { useState, type FormEvent } from "react";
import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
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
} from "lucide-react";
import { api } from "./api";
import { LinkedText } from "./MessageTools";
import {
  OneCCandidates,
  OneCCustomerDetails,
  type OneCClient,
} from "./OneCCustomerDetails";
import "./mail.css";

type Contact = { name: string; email: string };
type Summary = {
  id: string;
  subject: string;
  contacts: Contact[];
  snippet: string;
  at: number;
  unread: boolean;
  count: number;
};
type Letter = {
  id: string;
  sender: Contact | null;
  recipients: Contact[];
  outgoing: boolean;
  at: number;
  text: string;
  attachments: string[];
};
type Thread = Summary & { messages: Letter[] };
type MailPage = {
  mailbox: string | null;
  threads: Summary[];
  next: string | null;
};
const clock = new Intl.DateTimeFormat("ru", {
  timeZone: "Asia/Seoul",
  hour: "2-digit",
  minute: "2-digit",
});
const date = new Intl.DateTimeFormat("ru", {
  timeZone: "Asia/Seoul",
  day: "numeric",
  month: "short",
  year: "numeric",
});
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
  contacts,
  threadId,
}: {
  contacts: Contact[];
  threadId: string;
}) {
  const [chosen, choose] = useState("");
  const email =
    contacts.length === 1
      ? contacts[0].email
      : contacts.find((c) => c.email === chosen)?.email || "";
  const customer = useQuery({
    queryKey: ["mail", "customer", threadId, email],
    enabled: !!email,
    staleTime: 15 * 60_000,
    gcTime: 0,
    retry: false,
    queryFn: () =>
      api<{ status: string; clients: OneCClient[] }>(
        "/api/mail/threads/" +
          encodeURIComponent(threadId) +
          "/customer?email=" +
          encodeURIComponent(email),
      ),
  });
  return (
    <>
      <div className="mail-pane-title">
        <UserRound size={16} />
        <h3>Клиент и заказы</h3>
      </div>
      <div className="mail-client-body">
        {contacts.length > 1 && (
          <label>
            Участник переписки
            <select value={email} onChange={(e) => choose(e.target.value)}>
              <option value="">Выберите email для поиска в 1С</option>
              {contacts.map((c) => (
                <option key={c.email} value={c.email}>
                  {c.email}
                </option>
              ))}
            </select>
          </label>
        )}
        {contacts.length === 1 && (
          <div className="mail-contact">
            <Mail size={15} />
            <span>{email}</span>
          </div>
        )}
        {!contacts.length && (
          <p className="mail-muted">В переписке нет адреса клиента.</p>
        )}
        {email && (
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
        )}
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
    queryKey: ["mail", "threads", search],
    initialPageParam: "",
    gcTime: 0,
    staleTime: 30_000,
    retry: false,
    queryFn: ({ pageParam }) =>
      api<MailPage>(
        "/api/mail/threads?" +
          new URLSearchParams({ page: pageParam, q: search }),
      ),
    getNextPageParam: (last) => last.next ?? undefined,
  });
  const mailbox = pages.data?.pages[0].mailbox;
  const byId = new Map<string, Summary>();
  for (const page of pages.data?.pages ?? [])
    for (const row of page.threads)
      if (!byId.has(row.id)) byId.set(row.id, row);
  const rows = [...byId.values()];
  const active = byId.get(selected) || rows[0];
  const thread = useQuery({
    queryKey: ["mail", "thread", mailbox, active?.id, active?.at],
    enabled:
      !!mailbox && !!active && (!pages.isError || pages.isFetchNextPageError),
    staleTime: 30_000,
    gcTime: 0,
    retry: false,
    queryFn: () =>
      api<Thread>("/api/mail/threads/" + encodeURIComponent(active!.id)),
  });
  function submit(event: FormEvent) {
    event.preventDefault();
    select("");
    setSearch(input.trim());
    setPane("list");
  }
  function refresh() {
    void pages.refetch();
    if (active) void thread.refetch();
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
                  key={row.id}
                  aria-pressed={row.id === active?.id}
                  className="mail-row"
                  onClick={() => {
                    select(row.id);
                    setPane("thread");
                  }}
                >
                  <span className="mail-row-meta">
                    <span>
                      {row.contacts.length ? display(row.contacts[0]) : mailbox}
                    </span>
                    <time>
                      {date.format(row.at).replace(/\s*\d{4}\s*г\.?$/, "")}
                    </time>
                  </span>
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
          <section className="mail-thread" aria-label="Цепочка писем">
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
                  <h2 dir="auto">{active.subject || "Без темы"}</h2>
                  <small>Писем в цепочке · {active.count}</small>
                </div>
                <div className="mail-letters" key={active.id}>
                  {thread.isLoading && (
                    <p role="status" className="mail-muted">
                      Загружаем письма…
                    </p>
                  )}
                  {thread.error ? (
                    <Retry
                      error={thread.error}
                      retry={() => void thread.refetch()}
                    />
                  ) : (
                    thread.data?.messages.map((m, i, messages) => (
                      <div key={m.id}>
                        {(i === 0 ||
                          date.format(messages[i - 1].at) !==
                            date.format(m.at)) && (
                          <div className="mail-day">{date.format(m.at)}</div>
                        )}
                        <article
                          className={
                            "mail-letter" + (m.outgoing ? " outgoing" : "")
                          }
                        >
                          <div className="mail-letter-heading">
                            <strong dir="auto">{display(m.sender)}</strong>
                            <time title={date.format(m.at) + " · Сеул"}>
                              {clock.format(m.at)}
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
                    ))
                  )}
                </div>
              </>
            ) : (
              <div className="mail-empty">Выберите цепочку писем</div>
            )}
          </section>
          <aside className="mail-client">
            <div className="mail-mobile-tools">
              <button onClick={() => setPane("thread")}>
                <ArrowLeft size={14} />К письмам
              </button>
            </div>
            {active && thread.data && !thread.error ? (
              <CustomerPanel
                key={thread.data.id}
                threadId={thread.data.id}
                contacts={thread.data.contacts}
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
