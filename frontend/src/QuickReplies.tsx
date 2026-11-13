import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Alert,
  Button,
  Modal,
  Select,
  Textarea,
  TextInput,
} from "@mantine/core";
import { useTranslation } from "react-i18next";
import { api, type Conversation } from "./api";
import "./v1-i18n";

type Reply = { id: string; title: string; body: string; version: number };
const fail = (e: unknown) => (e instanceof Error ? e.message : String(e));
const useReplies = () =>
  useQuery({
    queryKey: ["quick-replies"],
    queryFn: () => api<Reply[]>("/api/quick-replies"),
    staleTime: 30000,
  });
export function QuickReplySettings() {
  const { t } = useTranslation("v1");
  const query = useQueryClient();
  const data = useReplies();
  const [edit, setEdit] = useState<Reply | null>(null),
    [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  async function save(remove = false) {
    if (!edit) return;
    setBusy(true);
    setError("");
    try {
      await api(
        "/api/quick-replies" +
          (edit.id ? "/" + edit.id : "") +
          (remove ? "?version=" + edit.version : ""),
        remove ? "DELETE" : edit.id ? "PUT" : "POST",
        remove ? undefined : edit,
      );
      setEdit(null);
      await query.invalidateQueries({ queryKey: ["quick-replies"] });
    } catch (e) {
      setError(fail(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="surface settings-card">
      <h3>{t("quickReplies")}</h3>
      <p>{t("placeholders")}</p>
      {data.error && <Alert color="red">{fail(data.error)}</Alert>}
      <Button
        onClick={() => {
          setError("");
          setEdit({ id: "", title: "", body: "", version: 1 });
        }}
      >
        {t("addReply")}
      </Button>
      {data.data?.map((r) => (
        <div className="setting-row" key={r.id}>
          <div style={{ minWidth: 0 }}>
            <b>{r.title}</b>
            <p style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>
              {r.body}
            </p>
          </div>
          <Button
            variant="light"
            onClick={() => {
              setError("");
              setEdit(r);
            }}
          >
            {t("edit")}
          </Button>
        </div>
      ))}
      <Modal
        closeButtonProps={{ "aria-label": t("close") }}
        opened={!!edit}
        onClose={() => setEdit(null)}
        title={t("quickReplies")}
      >
        {edit && (
          <>
            <TextInput
              required
              label={t("title")}
              value={edit.title}
              onChange={(e) =>
                setEdit({ ...edit, title: e.currentTarget.value })
              }
              maxLength={100}
            />
            <Textarea
              mt="sm"
              label={t("replyText")}
              value={edit.body}
              onChange={(e) =>
                setEdit({ ...edit, body: e.currentTarget.value })
              }
              maxLength={10000}
              minRows={5}
              autosize
            />
            <p>{t("placeholders")}</p>
            {error && <Alert color="red">{error}</Alert>}
            <Button
              loading={busy}
              disabled={!edit.title.trim() || !edit.body.trim()}
              onClick={() => void save()}
            >
              {t("save")}
            </Button>
            {edit.id && (
              <Button
                ml="sm"
                color="red"
                variant="subtle"
                disabled={busy}
                onClick={() => {
                  if (window.confirm(t("deleteReply"))) void save(true);
                }}
              >
                {t("delete")}
              </Button>
            )}
          </>
        )}
      </Modal>
    </section>
  );
}
export function QuickReplyPicker({
  conversation,
  text,
  onInsert,
}: {
  conversation: Conversation;
  text: string;
  onInsert: (text: string) => void;
}) {
  const { t } = useTranslation("v1");
  const data = useReplies();
  const [open, setOpen] = useState(false),
    [selected, setSelected] = useState<string | null>(null),
    [order, setOrder] = useState<string | null>(null);
  const reply = data.data?.find((r) => r.id === selected);
  const needsOrder = !!reply?.body.includes("{номер_заказа}");
  const name = conversation.display_name || conversation.name || "";
  const validName = !!name && name !== conversation.phone;
  const expanded =
    reply?.body
      .replaceAll("{имя}", validName ? name : "{имя}")
      .replaceAll("{номер_заказа}", order || "{номер_заказа}") || "";
  const ready =
    !!reply &&
    (!needsOrder || !!order) &&
    (!reply.body.includes("{имя}") || validName);
  const slash = text.startsWith("/") && !text.includes("\n");
  const search = slash ? text.slice(1).toLocaleLowerCase() : "";
  return (
    <>
      <Button size="xs" variant="subtle" onClick={() => setOpen(true)}>
        {t("quickReplies")} /
      </Button>
      {(open || slash) && (
        <div className="quick-reply-picker">
          <Select
            searchable
            label={t("quickReplies")}
            value={selected}
            data={(data.data || [])
              .filter(
                (r) => !search || r.title.toLocaleLowerCase().includes(search),
              )
              .map((r) => ({ value: r.id, label: r.title }))}
            onChange={(v) => {
              setSelected(v);
              setOrder(
                conversation.order_numbers.length === 1
                  ? conversation.order_numbers[0]
                  : null,
              );
            }}
          />
          {needsOrder && (
            <Select
              label={t("order")}
              value={order}
              data={conversation.order_numbers}
              onChange={setOrder}
            />
          )}
          {reply && (
            <>
              <p style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>
                {expanded}
              </p>
              {!ready && <p>{t("missingPlaceholder")}</p>}
              <Button
                size="xs"
                disabled={!ready}
                onClick={() => {
                  onInsert((slash ? "" : text ? text + "\n" : "") + expanded);
                  setOpen(false);
                  setSelected(null);
                }}
              >
                {t("insert")}
              </Button>
            </>
          )}
          {data.error && <Alert color="red">{fail(data.error)}</Alert>}
          <Button
            size="xs"
            variant="subtle"
            onClick={() => {
              setOpen(false);
              if (slash) onInsert("");
            }}
          >
            {t("close")}
          </Button>
        </div>
      )}
    </>
  );
}
