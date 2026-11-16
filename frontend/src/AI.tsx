import { useEffect, useRef, useState, type ReactNode } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Alert,
  Badge,
  Button,
  Checkbox,
  Loader,
  NumberInput,
  Popover,
  Select,
  Table,
  TagsInput,
  Textarea,
  TextInput,
} from "@mantine/core";
import { useTranslation } from "react-i18next";
import "./v1-i18n";
import { api } from "./api";
import "./ai.css";
import { SendLanguageMenu, languageName } from "./MessageTools";

const fail = (e: unknown) => (e instanceof Error ? e.message : String(e));
const intents: Record<string, string> = {
  greeting: "Приветствие",
  order_status: "Статус заказа",
  availability_price: "Наличие и цена",
  shipping: "Доставка",
  payment: "Оплата",
  return_complaint: "Возврат и претензия",
  other: "Другое",
};
const decisions: Record<string, string> = {
  pending: "Ожидает решения",
  sent: "Отправлен без правок",
  edited: "Отправлен с правками",
  rejected: "Отклонён",
  superseded: "Устарел",
  auto_sent: "Автоответ поставлен в очередь",
};
const states: Record<string, string> = {
  queued: "Готовится",
  generating: "Готовится",
  ready: "Черновик готов",
  unavailable: "AI недоступен",
  not_configured: "Ключ OpenAI не настроен",
  disabled: "AI выключен",
  off: "Выключен для этого вопроса",
  escalated: "Нужен ответ менеджера",
};
const reasons: Record<string, string> = {
  length: "Проверьте длину ответа",
  language: "Проверьте язык ответа",
  forbidden_phrase: "Обнаружена запрещённая фраза",
  exact_date: "Указана точная дата",
  no_source_facts: "Нет подтверждённых данных из 1С",
  shipping_not_configured: "Условия доставки ещё не настроены",
  human_required: "Требуется менеджер",
  price_not_configured: "Коэффициент цены не задан",
  unknown_price: "Не задан тариф модели",
  monthly_budget: "Достигнут месячный лимит",
  new_message: "Пришло новое сообщение",
  manual_reply: "Сотрудник уже ответил",
  message_changed: "Входящее изменилось",
  timeout: "Превышено время ожидания",
  interrupted: "Задание прервано",
  greeting_name_mismatch: "Обращение по чужому имени убрано; проверьте ответ",
  manager_translation_failed: "Перевод для менеджера недоступен",
  generation_failed: "Не удалось подготовить ответ",
  policy_or_message_changed: "Условия отправки изменились",
};
type Draft = {
  id: string;
  state: string;
  decision: string;
  intent: string;
  language: string;
  body: string;
  final_text: string;
  manager_text: string | null;
  manager_language: string;
  reason: string;
  policy: { passed?: boolean; reasons?: string[] };
  auto_at: string | null;
  created_at: string;
  tool_calls: { name: string; result: unknown }[];
};
function refresh(query: ReturnType<typeof useQueryClient>) {
  for (const key of [
    "ai-draft",
    "ai-report",
    "conversations",
    "conversation",
    "messages",
    "notifications",
  ])
    void query.invalidateQueries({ queryKey: [key] });
}

function sourceSummary(name: string, result: unknown): string {
  const value =
    result && typeof result === "object"
      ? (result as Record<string, unknown>)
      : {};
  const labels: Record<string, string> = {
    get_stock: "Остатки",
    get_orders: "Заказы клиента",
    get_order: "Заказ",
    get_customer_context: "Данные клиента",
    quote_price: "Расчёт цены",
    search_examples: "Примеры ответов",
    get_policies: "Правила компании",
  };
  const label = labels[name] || "Источник";
  if (value.status === "source_not_connected") {
    const numbers = Array.isArray(value.known_order_numbers)
      ? value.known_order_numbers.filter(
          (v): v is string => typeof v === "string",
        )
      : [];
    return name === "get_stock"
      ? "Остатки: источник 1С не подключён"
      : numbers.length
        ? `${label}: известны номера ${numbers.join(", ")}; подробности недоступны`
        : `${label}: не найдены; источник подробностей не подключён`;
  }
  if (value.status === "ok") return `${label}: данные получены`;
  return `${label}: данные недоступны`;
}

export function DraftPanel({
  conversationId,
  canWrite,
  isAdmin = false,
  managerLanguage,
  clientLanguage,
  onEdit,
}: {
  conversationId: string;
  canWrite: boolean;
  isAdmin?: boolean;
  managerLanguage: string;
  clientLanguage?: string;
  onEdit: (
    id: string,
    text: string,
    source: string,
    target: string | null,
  ) => void;
}) {
  const { t } = useTranslation("v1");
  const query = useQueryClient();
  const data = useQuery({
    queryKey: ["ai-draft", conversationId],
    queryFn: () =>
      api<{ item: Draft | null; configured: boolean; enabled: boolean }>(
        "/api/conversations/" + conversationId + "/draft",
      ),
    refetchInterval: 4000,
  });
  const [opened, setOpened] = useState(false),
    [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const [rejecting, setRejecting] = useState(false),
    [category, setCategory] = useState<string | null>(null),
    [reason, setReason] = useState("");
  const [sendLanguage, setSendLanguage] = useState<string | null>(null),
    [clientExpanded, setClientExpanded] = useState(false);
  const panelRef = useRef<HTMLElement>(null);
  useEffect(() => {
    const outside = (event: PointerEvent) => {
      if (
        event.target instanceof Node &&
        !panelRef.current?.contains(event.target)
      )
        setOpened(false);
    };
    const key = (event: KeyboardEvent) => {
      if (event.altKey && event.code === "KeyA") {
        event.preventDefault();
        setOpened((value) => !value);
      } else if (event.key === "Escape") setOpened(false);
    };
    document.addEventListener("pointerdown", outside);
    window.addEventListener("keydown", key);
    return () => {
      document.removeEventListener("pointerdown", outside);
      window.removeEventListener("keydown", key);
    };
  }, []);
  const d = data.data?.item,
    pending = d?.decision === "pending";
  const readable = !!d?.manager_text && d.manager_language === managerLanguage;
  const target =
    sendLanguage === "original"
      ? managerLanguage
      : sendLanguage || clientLanguage || d?.language || "";
  const state = data.isLoading
    ? "Готовится…"
    : !pending
      ? "Черновика нет"
      : states[d.state] || d.state;
  const warnings = [
    ...new Set(
      [...(d?.policy.reasons || []), d?.reason || ""]
        .filter(Boolean)
        .map((value) => reasons[value] || value),
    ),
  ];
  async function act(path: string, body?: unknown) {
    setBusy(true);
    setError("");
    try {
      await api(path, "POST", body);
      refresh(query);
      if (/\/(send|send-edited|reject)$/.test(path)) {
        setOpened(false);
        setSendLanguage(null);
      }
      return true;
    } catch (error) {
      setError(fail(error));
      return false;
    } finally {
      setBusy(false);
    }
  }
  const regenerate = () =>
    void act("/api/conversations/" + conversationId + "/draft/regenerate");
  return (
    <section
      ref={panelRef}
      className="ai-draft ai-draft-compact"
      aria-label="AI-черновик"
    >
      <button
        className="ai-draft-toggle"
        type="button"
        aria-expanded={opened}
        aria-controls={"ai-panel-" + conversationId}
        title="AI-помощник · Alt+A"
        onClick={() => setOpened(!opened)}
      >
        <b>AI-помощник</b>
        <span className={"ai-line-state " + (pending ? d.state : "empty")}>
          {state}
        </span>
        <span className="ai-line-preview" dir="auto">
          {pending
            ? readable
              ? d.manager_text
              : "Перевод для менеджера готовится…"
            : ""}
        </span>
        {!pending && canWrite && data.data?.configured && data.data.enabled && (
          <span className="ai-prepare-hint">Подготовить</span>
        )}
      </button>
      {opened && (
        <div
          id={"ai-panel-" + conversationId}
          className="ai-draft-panel"
          role="region"
          aria-label="Панель AI-помощника"
        >
          <div className="ai-heading">
            <b>AI-помощник</b>
            <Badge variant="light">{state}</Badge>
            {d && (
              <small>
                {intents[d.intent]} · {languageName(d.language || "en")}
              </small>
            )}
          </div>
          {(error || data.error) && (
            <Alert color="red">{error || fail(data.error)}</Alert>
          )}
          {d?.auto_at && pending && (
            <Alert color="orange">
              Автоответ ожидает отправки. «Изменить» останавливает
              автоматическую отправку.
            </Alert>
          )}
          {d?.body && pending && (
            <>
              {warnings.map((label) => (
                <small className="ai-warning" key={label}>
                  {label}
                </small>
              ))}
              {readable ? (
                <div className="ai-draft-body" dir="auto">
                  {d.manager_text}
                </div>
              ) : (
                <Alert color="orange">
                  Не удалось получить версию на вашем языке. Черновик не
                  отправлен.{" "}
                  <Button
                    size="xs"
                    variant="subtle"
                    disabled={busy}
                    onClick={() =>
                      void act("/api/drafts/" + d.id + "/reading/retry")
                    }
                  >
                    Повторить перевод
                  </Button>
                </Alert>
              )}
              {d.language !== managerLanguage && (
                <div className="ai-client-version">
                  <button
                    type="button"
                    aria-expanded={clientExpanded}
                    onClick={() => setClientExpanded(!clientExpanded)}
                  >
                    Клиенту · {languageName(d.language)}{" "}
                    {clientExpanded ? "▴" : "▾"}
                  </button>
                  <p dir="auto" className={clientExpanded ? "" : "is-clamped"}>
                    {d.body}
                  </p>
                </div>
              )}
            </>
          )}
          {canWrite &&
            d &&
            pending &&
            ["ready", "escalated"].includes(d.state) && (
              <div className="ai-actions">
                <div className="crm-send-group">
                  <Button
                    size="xs"
                    loading={busy}
                    disabled={!readable || !target}
                    title={"Отправить · " + languageName(target || "en")}
                    onClick={() =>
                      void act("/api/drafts/" + d.id + "/send", {
                        source_language: managerLanguage,
                        target_language: sendLanguage || target,
                      })
                    }
                  >
                    Отправить · {languageName(target || "en")}
                  </Button>
                  <SendLanguageMenu
                    value={sendLanguage}
                    onChange={setSendLanguage}
                    managerLanguage={managerLanguage}
                    clientLanguage={clientLanguage || d.language}
                    disabled={busy}
                  />
                </div>
                <Button
                  size="xs"
                  variant="light"
                  disabled={busy || !readable}
                  onClick={async () => {
                    if (await act("/api/drafts/" + d.id + "/pause")) {
                      onEdit(
                        d.id,
                        d.manager_text!,
                        d.manager_language,
                        sendLanguage,
                      );
                      setOpened(false);
                    }
                  }}
                >
                  Изменить
                </Button>
                <Popover
                  opened={rejecting}
                  onChange={setRejecting}
                  withinPortal={false}
                  position="top"
                  width={260}
                >
                  <Popover.Target>
                    <Button
                      size="xs"
                      variant="subtle"
                      color="gray"
                      disabled={busy}
                      onClick={async () => {
                        if (await act("/api/drafts/" + d.id + "/pause"))
                          setRejecting(!rejecting);
                      }}
                    >
                      Отклонить
                    </Button>
                  </Popover.Target>
                  <Popover.Dropdown>
                    <Select
                      comboboxProps={{ withinPortal: false }}
                      label={t("rejectReason")}
                      value={category}
                      onChange={setCategory}
                      data={["incorrect", "irrelevant", "tone", "other"].map(
                        (value) => ({ value, label: t(value) }),
                      )}
                    />
                    <TextInput
                      mt="xs"
                      label={t("comment")}
                      value={reason}
                      onChange={(event) => setReason(event.currentTarget.value)}
                      maxLength={1000}
                    />
                    <Button
                      mt="xs"
                      size="xs"
                      loading={busy}
                      disabled={
                        !category || (category === "other" && !reason.trim())
                      }
                      onClick={async () => {
                        if (
                          await act("/api/drafts/" + d.id + "/reject", {
                            category,
                            reason,
                          })
                        ) {
                          setRejecting(false);
                          setReason("");
                        }
                      }}
                    >
                      Отклонить
                    </Button>
                  </Popover.Dropdown>
                </Popover>
                <Button
                  size="xs"
                  variant="subtle"
                  title="Подготовить заново"
                  disabled={busy}
                  onClick={regenerate}
                >
                  Заново
                </Button>
              </div>
            )}
          {!!d?.tool_calls.length && (
            <details>
              <summary>{t("sources")}</summary>
              {d.tool_calls.map((call, index) => (
                <div key={index}>
                  <p>{sourceSummary(call.name, call.result)}</p>
                  {isAdmin && (
                    <details>
                      <summary>Подробности</summary>
                      <pre
                        style={{
                          whiteSpace: "pre-wrap",
                          overflowWrap: "anywhere",
                          fontSize: 12,
                        }}
                      >
                        {JSON.stringify(call.result, null, 2)}
                      </pre>
                    </details>
                  )}
                </div>
              ))}
            </details>
          )}
          {canWrite &&
            data.data?.configured &&
            data.data.enabled &&
            (!pending || !d?.body) && (
              <Button
                size="xs"
                variant="subtle"
                disabled={
                  busy || d?.state === "generating" || d?.state === "queued"
                }
                onClick={regenerate}
              >
                Подготовить
              </Button>
            )}
        </div>
      )}
    </section>
  );
}

type Mode = "off" | "draft" | "auto";
type Config = {
  enabled: boolean;
  model: string;
  classifier: string;
  embedding_model: string;
  monthly_limit: number;
  timezone: string;
  hours: Record<string, string[]>;
  holidays: string[];
  modes: Record<string, Mode[]>;
  greeting_auto: boolean;
  delay_min: number;
  delay_max: number;
  daily_limit: number;
  coefficient: number | null;
  rounding: string;
  keywords: string[];
  human_tags: string[];
  duty_user_id: string | null;
  style: string;
  forbidden: string[];
  signature: string;
  policies: Record<string, string>;
  rates: Record<string, number[]>;
};
type ConfigResult = {
  config: Config;
  version: string;
  configured: boolean;
  month_cost: number;
};
export function AISettings({ keyControl }: { keyControl: ReactNode }) {
  const data = useQuery({
    queryKey: ["ai-settings"],
    queryFn: () => api<ConfigResult>("/api/ai/settings"),
  });
  if (data.error) return <Alert color="red">{fail(data.error)}</Alert>;
  return (
    <section className="surface settings-card">
      <h3>AI · черновики и автоответы</h3>
      <p className="muted">
        По умолчанию ответы проверяет менеджер. Данные 1С сейчас содержат имена
        и номера реализаций; статусы, остатки и цены ещё не подключены.
      </p>
      {keyControl}
      {data.data ? (
        <ConfigForm key={data.data.version} initial={data.data} />
      ) : (
        <Loader />
      )}
      <PromptSettings />
    </section>
  );
}
function ConfigForm({ initial }: { initial: ConfigResult }) {
  const query = useQueryClient();
  const [c, set] = useState(initial.config),
    [busy, setBusy] = useState(""),
    [feedback, setFeedback] = useState("");
  const team = useQuery({
    queryKey: ["team"],
    queryFn: () =>
      api<{ id: string; name: string; role: string }[]>("/api/team"),
  });
  const change = <K extends keyof Config>(key: K, value: Config[K]) =>
    set((x) => ({ ...x, [key]: value }));
  async function save() {
    setBusy("save");
    setFeedback("");
    try {
      await api("/api/ai/settings", "PUT", {
        config: c,
        version: initial.version,
      });
      await query.invalidateQueries({ queryKey: ["ai-settings"] });
      setFeedback("Сохранено");
    } catch (e) {
      setFeedback(fail(e));
    } finally {
      setBusy("");
    }
  }
  async function check() {
    setBusy("check");
    setFeedback("");
    try {
      await api("/api/ai/check", "POST");
      setFeedback("Модели и эмбеддинги доступны");
    } catch (e) {
      setFeedback(fail(e));
    } finally {
      setBusy("");
    }
  }
  return (
    <div className="ai-config">
      <Checkbox
        label="Готовить AI-черновики для новых входящих"
        checked={c.enabled}
        onChange={(e) => change("enabled", e.currentTarget.checked)}
      />
      <div className="ai-grid">
        {(["model", "classifier", "embedding_model"] as const).map((key, i) => (
          <TextInput
            key={key}
            label={
              [
                "Основная модель",
                "Модель классификатора",
                "Модель эмбеддингов",
              ][i]
            }
            value={c[key]}
            onChange={(e) => change(key, e.currentTarget.value)}
          />
        ))}
        <NumberInput
          label="Месячный лимит автоответов, USD"
          description="Лимит расходов: после достижения останутся только черновики"
          value={c.monthly_limit}
          min={0}
          onChange={(v) => change("monthly_limit", Number(v))}
        />
        <TextInput
          label="Часовой пояс"
          value={c.timezone}
          onChange={(e) => change("timezone", e.currentTarget.value)}
        />
        <Select
          label="Дежурный менеджер"
          clearable
          value={c.duty_user_id}
          data={(team.data || [])
            .filter((u) => u.role !== "staff")
            .map((u) => ({ value: u.id, label: u.name }))}
          onChange={(v) => change("duty_user_id", v)}
        />
      </div>
      <p>
        Расход в этом месяце: ${initial.month_cost.toFixed(4)} ·{" "}
        {initial.configured ? "Ключ задан" : "Ключ не задан"}
      </p>
      <div className="ai-table">
        <Table>
          <Table.Thead>
            <Table.Tr>
              <Table.Th>Тип вопроса</Table.Th>
              <Table.Th>В рабочее время</Table.Th>
              <Table.Th>Вне рабочего времени</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {Object.entries(intents).map(([key, label]) => (
              <Table.Tr key={key}>
                <Table.Td>{label}</Table.Td>
                {[0, 1].map((index) => (
                  <Table.Td key={index}>
                    <Select
                      aria-label={
                        label +
                        (index ? " вне рабочего времени" : " в рабочее время")
                      }
                      value={c.modes[key][index]}
                      data={[
                        { value: "off", label: "Выключено" },
                        { value: "draft", label: "Черновик" },
                        ...(["payment", "return_complaint"].includes(key)
                          ? []
                          : [{ value: "auto", label: "Автоответ" }]),
                      ]}
                      onChange={(v) => {
                        if (
                          v === "auto" &&
                          !window.confirm(
                            "Разрешить автоматические ответы этого типа после проверки правил?",
                          )
                        )
                          return;
                        const modes = { ...c.modes, [key]: [...c.modes[key]] };
                        modes[key][index] = (v || "draft") as Mode;
                        change("modes", modes);
                      }}
                    />
                  </Table.Td>
                ))}
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      </div>
      <Checkbox
        label="Разрешить автоответ на приветствие"
        checked={c.greeting_auto}
        onChange={(e) => change("greeting_auto", e.currentTarget.checked)}
      />
      <div className="ai-grid">
        <NumberInput
          label="Минимальная пауза, секунд"
          value={c.delay_min}
          min={5}
          max={120}
          onChange={(v) => change("delay_min", Number(v))}
        />
        <NumberInput
          label="Максимальная пауза, секунд"
          value={c.delay_max}
          min={c.delay_min}
          max={120}
          onChange={(v) => change("delay_max", Number(v))}
        />
        <NumberInput
          label="Автоответов на диалог в сутки"
          value={c.daily_limit}
          min={0}
          max={30}
          onChange={(v) => change("daily_limit", Number(v))}
        />
      </div>
      <details>
        <summary>Рабочее время и праздники</summary>
        <div className="ai-grid">
          {["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"].map((label, i) => (
            <TextInput
              key={label}
              label={label}
              description="Например: 09:00-13:00, 14:00-18:00. Пусто — выходной"
              value={(c.hours[String(i + 1)] || []).join(", ")}
              onChange={(e) =>
                change("hours", {
                  ...c.hours,
                  [String(i + 1)]: e.currentTarget.value
                    .split(",")
                    .map((s) => s.trim())
                    .filter(Boolean),
                })
              }
            />
          ))}
        </div>
        <TagsInput
          label="Праздники, ГГГГ-ММ-ДД"
          value={c.holidays}
          onChange={(v) => change("holidays", v)}
        />
      </details>
      <details>
        <summary>Эскалация и правила цены</summary>
        <TagsInput
          label="Ключевые фразы для передачи менеджеру"
          value={c.keywords}
          onChange={(v) => change("keywords", v)}
        />
        <TagsInput
          label="Теги клиентов: всегда менеджеру"
          value={c.human_tags}
          onChange={(v) => change("human_tags", v)}
        />
        <NumberInput
          label="Коэффициент: USD = KRW / 1000 × коэффициент"
          value={c.coefficient ?? ""}
          min={0.001}
          onChange={(v) => change("coefficient", v === "" ? null : Number(v))}
        />
        <Select
          label="Округление цены"
          value={c.rounding}
          data={[
            { value: "up", label: "Вверх до целого доллара" },
            { value: "half", label: "Вверх до 0,5 доллара" },
            { value: "none", label: "Без округления" },
          ]}
          onChange={(v) => change("rounding", v || "up")}
        />
      </details>
      <details>
        <summary>Стиль и справочная информация</summary>
        <Textarea
          label="Правила стиля"
          autosize
          minRows={3}
          value={c.style}
          onChange={(e) => change("style", e.currentTarget.value)}
        />
        <TagsInput
          label="Запрещённые фразы и обещания"
          value={c.forbidden}
          onChange={(v) => change("forbidden", v)}
        />
        <TextInput
          label="Подпись"
          value={c.signature}
          onChange={(e) => change("signature", e.currentTarget.value)}
        />
        {Object.entries({
          shipping: "Доставка",
          payment: "Оплата",
          timing: "Сроки",
          warranty: "Гарантия",
          about: "О компании",
        }).map(([k, v]) => (
          <Textarea
            key={k}
            label={v}
            autosize
            minRows={3}
            value={c.policies[k] || ""}
            onChange={(e) =>
              change("policies", { ...c.policies, [k]: e.currentTarget.value })
            }
          />
        ))}
      </details>
      <details>
        <summary>Тарифы моделей для учёта расходов</summary>
        <p className="muted">
          USD за миллион токенов. При смене модели укажите её тариф. Расчёт без
          скидки за кэш, счёт провайдера может отличаться.
        </p>
        {[...new Set([c.model, c.classifier, c.embedding_model])].map(
          (model) => (
            <div className="ai-grid" key={model}>
              <b>{model}</b>
              {[0, 1].map((index) => (
                <NumberInput
                  key={index}
                  label={index ? "Выходящие токены" : "Входящие токены"}
                  min={0}
                  value={c.rates[model]?.[index] ?? ""}
                  onChange={(v) => {
                    const rates = {
                      ...c.rates,
                      [model]: [...(c.rates[model] || [0, 0])],
                    };
                    rates[model][index] = Number(v);
                    change("rates", rates);
                  }}
                />
              ))}
            </div>
          ),
        )}
      </details>
      {feedback && <Alert>{feedback}</Alert>}
      <div className="ai-actions">
        <Button
          loading={busy === "save"}
          disabled={!!busy}
          onClick={() => void save()}
        >
          Сохранить правила AI
        </Button>
        <Button
          variant="default"
          loading={busy === "check"}
          disabled={!!busy}
          onClick={() => void check()}
        >
          Проверить OpenAI
        </Button>
      </div>
    </div>
  );
}
function PromptSettings() {
  const query = useQueryClient();
  const data = useQuery({
    queryKey: ["ai-prompts"],
    queryFn: () =>
      api<{
        base: string;
        items: {
          id: string;
          body: string;
          active: boolean;
          created_at: string;
        }[];
      }>("/api/ai/prompts"),
  });
  const [body, setBody] = useState(""),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false);
  async function act(path: string, value?: unknown) {
    setBusy(true);
    setError("");
    try {
      await api(path, "POST", value);
      setBody("");
      await query.invalidateQueries({ queryKey: ["ai-prompts"] });
    } catch (e) {
      setError(fail(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <details className="ai-prompts">
      <summary>Системный промпт · версии и откат</summary>
      <p className="muted">
        Дополнительные инструкции дополняют встроенные правила достоверности и
        передачи менеджеру.
      </p>
      {error && <Alert color="red">{error}</Alert>}
      <Textarea
        label="Новая версия инструкций"
        value={body}
        onChange={(e) => setBody(e.currentTarget.value)}
        autosize
        minRows={4}
      />
      <Button
        disabled={!body.trim()}
        loading={busy}
        onClick={() => void act("/api/ai/prompts", { body })}
      >
        Сохранить и включить версию
      </Button>
      {data.data?.items.map((p) => (
        <details key={p.id}>
          <summary>
            {new Date(p.created_at).toLocaleString()}{" "}
            {p.active ? "· Действующая" : ""}
          </summary>
          <pre>{p.body}</pre>
          {!p.active && (
            <Button
              variant="light"
              loading={busy}
              onClick={() => void act("/api/ai/prompts/" + p.id + "/activate")}
            >
              Вернуться к этой версии
            </Button>
          )}
        </details>
      ))}
    </details>
  );
}

export function AIReport() {
  const data = useQuery({
    queryKey: ["ai-report"],
    queryFn: () =>
      api<{
        month_cost: number;
        groups: {
          week: string;
          intent: string;
          decision: string;
          state: string;
          count: number;
        }[];
        recent: Draft[];
        usage: {
          model: string;
          tokens_in: number;
          tokens_out: number;
          cost_usd: number;
          error: string | null;
          created_at: string;
        }[];
      }>("/api/reports/ai"),
    refetchInterval: 15000,
  });
  if (data.error) return <Alert color="red">{fail(data.error)}</Alert>;
  if (!data.data) return <Loader />;
  const totals = new Map<string, number>();
  for (const g of data.data.groups)
    totals.set(
      g.week + g.intent,
      (totals.get(g.week + g.intent) || 0) + g.count,
    );
  return (
    <div className="ai-report">
      <section className="surface settings-card">
        <h3>AI за последние 30 дней</h3>
        <p>Расход за текущий месяц: ${data.data.month_cost.toFixed(4)}</p>
        <div className="ai-table">
          <Table>
            <Table.Thead>
              <Table.Tr>
                {[
                  "Неделя",
                  "Тип вопроса",
                  "Состояние",
                  "Решение",
                  "Количество",
                  "Доля",
                ].map((x) => (
                  <Table.Th key={x}>{x}</Table.Th>
                ))}
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {data.data.groups.map((g, i) => (
                <Table.Tr key={i}>
                  <Table.Td>{g.week.slice(0, 10)}</Table.Td>
                  <Table.Td>{intents[g.intent]}</Table.Td>
                  <Table.Td>{states[g.state] || g.state}</Table.Td>
                  <Table.Td>{decisions[g.decision] || g.decision}</Table.Td>
                  <Table.Td>{g.count}</Table.Td>
                  <Table.Td>
                    {Math.round(
                      (g.count / (totals.get(g.week + g.intent) || 1)) * 100,
                    )}
                    %
                  </Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        </div>
        {!data.data.groups.length && <p>AI-обращений пока нет.</p>}
      </section>
      <section className="surface settings-card">
        <h3>Последние решения и причины</h3>
        {data.data.recent.map((d) => (
          <details key={d.id}>
            <summary>
              {intents[d.intent]} · {states[d.state]} · {decisions[d.decision]}
            </summary>
            <p>{reasons[d.reason] || d.reason}</p>
            <p dir="auto">{d.body}</p>
            {d.final_text && <p dir="auto">Итог: {d.final_text}</p>}
            <p>{d.policy.reasons?.map((r) => reasons[r] || r).join("; ")}</p>
          </details>
        ))}
      </section>
      <section className="surface settings-card">
        <h3>Журнал запросов AI</h3>
        {data.data.usage.map((u, i) => (
          <p key={i}>
            {u.model} · {u.tokens_in} / {u.tokens_out} токенов · $
            {u.cost_usd.toFixed(6)} {u.error ? "· " + u.error : ""}
          </p>
        ))}
      </section>
    </div>
  );
}
