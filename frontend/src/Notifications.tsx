import { useEffect, useRef, useState, type ReactNode } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Alert, Button, Checkbox, TextInput } from "@mantine/core";
import { api } from "./api";
import { openConversation } from "./customer-api";
import "./ai.css";
const fail = (e: unknown) => (e instanceof Error ? e.message : String(e));
type Prefs = {
  email: boolean;
  telegram: boolean;
  browser: boolean;
  sound: boolean;
};
type Result = {
  preferences: Prefs;
  email: string;
  telegram_linked: boolean;
  smtp_configured: boolean;
  telegram_configured: boolean;
};
const stateLabel: Record<string, string> = {
  pending: "Ожидает отправки",
  sending: "Отправляется",
  sent: "Отправлено",
  retry: "Ожидает повторной попытки",
  failed: "Ошибка",
  uncertain: "Результат не подтверждён",
  cancelled: "Отменено",
};
const errorLabel: Record<string, string> = {
  email_not_configured: "SMTP не настроен",
  smtp_not_configured: "SMTP не настроен",
  smtp_auth_failed: "Ошибка авторизации SMTP",
  smtp_rejected: "Сервер отклонил письмо",
  smtp_unavailable: "SMTP недоступен",
  telegram_not_configured: "Telegram не настроен",
  telegram_rejected:
    "Бот не может отправить сообщение. Откройте бота и нажмите Start.",
  telegram_unavailable: "Telegram недоступен",
  telegram_rate_limit: "Лимит Telegram, повтор позже",
  delivery_unknown: "Проверьте получение: повтор автоматически не выполняется",
};
export function NotificationIntegration({ controls }: { controls: ReactNode }) {
  const [busy, setBusy] = useState(""),
    [feedback, setFeedback] = useState("");
  async function check(channel: string) {
    setBusy(channel);
    setFeedback("");
    try {
      await api("/api/notifications/check/" + channel, "POST");
      setFeedback("Подключение работает");
    } catch (e) {
      const code = fail(e);
      setFeedback(errorLabel[code] || code);
    } finally {
      setBusy("");
    }
  }
  return (
    <section className="surface settings-card">
      <h3>Уведомления · Email и Telegram</h3>
      <p className="muted">
        Каждый сотрудник выбирает каналы в разделе «Мои уведомления». Проверка
        соединения не отправляет письма или сообщения.
      </p>
      {controls}
      <div className="ai-actions">
        <Button
          variant="default"
          loading={busy === "email"}
          disabled={!!busy}
          onClick={() => void check("email")}
        >
          Проверить SMTP
        </Button>
        <Button
          variant="default"
          loading={busy === "telegram"}
          disabled={!!busy}
          onClick={() => void check("telegram")}
        >
          Проверить бота
        </Button>
      </div>
      {feedback && <Alert>{feedback}</Alert>}
    </section>
  );
}
function sound() {
  const ctx = new AudioContext();
  const o = ctx.createOscillator();
  const g = ctx.createGain();
  o.connect(g);
  g.connect(ctx.destination);
  o.frequency.value = 660;
  g.gain.value = 0.06;
  o.start();
  o.stop(ctx.currentTime + 0.12);
  o.onended = () => void ctx.close();
}
export function PersonalNotifications() {
  const query = useQueryClient();
  const data = useQuery({
    queryKey: ["notification-prefs"],
    queryFn: () => api<Result>("/api/me/notifications"),
  });
  const [error, setError] = useState(""),
    [busy, setBusy] = useState(false),
    [chat, setChat] = useState(""),
    [code, setCode] = useState(""),
    [codeSent, setCodeSent] = useState(false);
  async function act(path: string, method: string, body?: unknown) {
    setBusy(true);
    setError("");
    try {
      await api(path, method, body);
      await query.invalidateQueries({ queryKey: ["notification-prefs"] });
      return true;
    } catch (e) {
      setError(fail(e));
      return false;
    } finally {
      setBusy(false);
    }
  }
  async function change(key: keyof Prefs, value: boolean) {
    if (!data.data) return;
    if (key === "browser" && value) {
      if (!("Notification" in window)) {
        setError("Браузер не поддерживает уведомления");
        return;
      }
      if ((await Notification.requestPermission()) !== "granted") {
        setError("Разрешите уведомления для этого сайта в браузере");
        return;
      }
    }
    await act("/api/me/notifications", "PUT", {
      ...data.data.preferences,
      [key]: value,
    });
  }
  return (
    <div className="notify-settings">
      <section className="surface settings-card">
        <h3>Мои каналы уведомлений</h3>
        <p className="muted">
          Колокольчик CRM работает всегда. Внешние каналы получают только
          уведомления, созданные после включения.
        </p>
        {(error || data.error) && (
          <Alert color="red">{error || fail(data.error)}</Alert>
        )}
        {data.data && (
          <div className="notify-settings">
            <Checkbox
              label={"Email: " + data.data.email}
              checked={data.data.preferences.email}
              disabled={busy}
              onChange={(e) => void change("email", e.currentTarget.checked)}
            />
            {!data.data.smtp_configured && (
              <small>Для Email администратору нужно настроить SMTP.</small>
            )}
            <Checkbox
              label="Telegram"
              checked={data.data.preferences.telegram}
              disabled={busy || !data.data.telegram_linked}
              onChange={(e) => void change("telegram", e.currentTarget.checked)}
            />
            <Checkbox
              label="Уведомления браузера о новых сообщениях"
              checked={data.data.preferences.browser}
              disabled={busy}
              onChange={(e) => void change("browser", e.currentTarget.checked)}
            />
            <Checkbox
              label="Звук новых сообщений"
              checked={data.data.preferences.sound}
              disabled={busy}
              onChange={(e) => void change("sound", e.currentTarget.checked)}
            />
            <Button
              variant="subtle"
              onClick={() => {
                try {
                  sound();
                } catch {
                  setError("Браузер не разрешил воспроизведение звука");
                }
              }}
            >
              Проверить звук
            </Button>
            {data.data.telegram_linked ? (
              <Button
                variant="subtle"
                color="red"
                onClick={() => void act("/api/me/telegram", "DELETE")}
              >
                Отключить Telegram
              </Button>
            ) : (
              <details>
                <summary>Подключить свой Telegram</summary>
                <p>
                  Сначала откройте бота организации и нажмите Start. Укажите
                  свой числовой Telegram ID. Бот отправит код для подтверждения.
                </p>
                {!data.data.telegram_configured && (
                  <Alert color="orange">
                    Администратор ещё не добавил токен бота.
                  </Alert>
                )}
                <TextInput
                  label="Мой Telegram ID"
                  value={chat}
                  onChange={(e) => setChat(e.currentTarget.value)}
                />
                <Button
                  mt="sm"
                  disabled={!data.data.telegram_configured || !chat || busy}
                  onClick={async () => {
                    if (
                      await act("/api/me/telegram/start", "POST", {
                        chat_id: chat,
                      })
                    )
                      setCodeSent(true);
                  }}
                >
                  Отправить мне код
                </Button>
                {codeSent && (
                  <>
                    <TextInput
                      label="Код из Telegram"
                      value={code}
                      onChange={(e) => setCode(e.currentTarget.value)}
                    />
                    <Button
                      mt="sm"
                      loading={busy}
                      onClick={() =>
                        void act("/api/me/telegram/confirm", "POST", { code })
                      }
                    >
                      Подтвердить Telegram
                    </Button>
                  </>
                )}
              </details>
            )}
          </div>
        )}
      </section>
      <DeliveryLog />
    </div>
  );
}
function DeliveryLog() {
  const data = useQuery({
    queryKey: ["notification-deliveries"],
    queryFn: () =>
      api<
        {
          id: string;
          channel: string;
          state: string;
          error: string | null;
          title: string;
          created_at: string;
        }[]
      >("/api/notifications/deliveries"),
    refetchInterval: 15000,
  });
  return (
    <section className="surface settings-card">
      <h3>Журнал доставки</h3>
      {data.error && <Alert color="red">{fail(data.error)}</Alert>}
      {!data.data?.length && <p>Внешних отправок пока нет.</p>}
      {data.data?.map((d) => (
        <div className="notify-delivery" key={d.id}>
          <b>{d.title}</b>
          <small>
            {d.channel} · {stateLabel[d.state]} ·{" "}
            {new Date(d.created_at).toLocaleString()}
          </small>
          {d.error && <small>{errorLabel[d.error] || d.error}</small>}
        </div>
      ))}
    </section>
  );
}
export function NotificationEvents() {
  const prefs = useQuery({
    queryKey: ["notification-prefs"],
    queryFn: () => api<Result>("/api/me/notifications"),
  });
  const data = useQuery({
    queryKey: ["notifications", 0],
    queryFn: () =>
      api<{
        items: {
          id: string;
          kind: string;
          created_at: string;
          payload: { conversation_id?: string; title?: string };
        }[];
      }>("/api/notifications?offset=0"),
    refetchInterval: 15000,
  });
  const seen = useRef<Set<string> | null>(null);
  const started = useRef(Date.now());
  useEffect(() => {
    const items = data.data?.items;
    if (!items) return;
    if (!seen.current) {
      seen.current = new Set(items.map((n) => n.id));
      return;
    }
    const fresh = items.filter(
      (n) =>
        !seen.current!.has(n.id) &&
        new Date(n.created_at).getTime() >= started.current &&
        n.kind === "inbox.message",
    );
    for (const n of items) seen.current.add(n.id);
    if (seen.current.size > 1000)
      seen.current = new Set(items.map((n) => n.id));
    if (!fresh.length) return;
    if (prefs.data?.preferences.sound) {
      try {
        sound();
      } catch {
        /* Browser autoplay policy. */
      }
    }
    if (
      prefs.data?.preferences.browser &&
      "Notification" in window &&
      Notification.permission === "granted" &&
      document.hidden
    ) {
      for (const item of fresh.slice(0, 3)) {
        const n = new Notification("KORAVTO · Новое сообщение", {
          body: "Откройте диалог в CRM",
          tag: item.id,
        });
        n.onclick = () => {
          window.focus();
          if (item.payload.conversation_id)
            openConversation(item.payload.conversation_id);
          n.close();
        };
      }
    }
  }, [data.data, prefs.data]);
  return null;
}
