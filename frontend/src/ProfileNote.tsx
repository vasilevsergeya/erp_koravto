import { useEffect, useRef, useState } from "react";
import {
  useQuery,
  useQueryClient,
  useInfiniteQuery,
} from "@tanstack/react-query";
import { Alert } from "@mantine/core";
import { api, ApiError } from "./api";

type Note = {
  version: number;
  text: string;
  author: string | null;
  updated_at: string | null;
};
function stamp(note: Note) {
  return note.updated_at
    ? [note.author, new Date(note.updated_at).toLocaleString("ru-RU")].join(
        " · ",
      )
    : "";
}
export function ProfileNote({
  id,
  admin,
  expanded = false,
  openRequest = 0,
}: {
  id: string;
  admin: boolean;
  expanded?: boolean;
  openRequest?: number;
}) {
  const cache = useQueryClient();
  const url = "/api/customers/" + id + "/profile-note";
  const key = ["profile-note", id];
  const note = useQuery({
    queryKey: key,
    queryFn: () => api<Note>(url),
    refetchInterval: 15000,
  });
  const [open, setOpen] = useState(expanded || openRequest > 0);
  const input = useRef<HTMLTextAreaElement>(null);
  useEffect(() => {
    if (openRequest) {
      setOpen(true);
      input.current?.focus();
    }
  }, [openRequest]);

  const [draft, setDraft] = useState<Note | null>(null);
  const [conflict, setConflict] = useState<Note | null>(null);
  const [error, setError] = useState("");
  const [history, setHistory] = useState(false);
  const saving = useRef(false);
  const [busy, setBusy] = useState(false);
  const revisions = useInfiniteQuery({
    queryKey: ["note-history", id],
    enabled: admin && history,
    initialPageParam: 0,
    queryFn: ({ pageParam }) =>
      api<Note[]>(url + "/history?before=" + pageParam),
    getNextPageParam: (last) =>
      last.length === 50 ? last.at(-1)?.version : undefined,
  });
  function close() {
    setHistory(false);
    setDraft(null);
    setConflict(null);
    setError("");
    setOpen(expanded);
  }
  async function save() {
    if (saving.current || conflict || !draft) return;
    saving.current = true;
    setBusy(true);
    try {
      const result = await api<Note>(url, "PUT", {
        text: draft.text,
        version: draft.version,
      });
      cache.setQueryData(key, result);
      close();
      for (const k of ["conversation", "note-history"])
        void cache.invalidateQueries({ queryKey: [k] });
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) {
        const result = await note.refetch();
        if (result.data) setConflict(result.data);
      }
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      saving.current = false;
      setBusy(false);
    }
  }
  const current = draft || note.data;
  return (
    <section className="crm-profile-note" aria-label="Заметка о клиенте">
      {!open ? (
        <button className="crm-note-preview" onClick={() => setOpen(true)}>
          Заметка: {note.data?.text.split("\n")[0] || "добавить"}
        </button>
      ) : (
        <>
          <label>
            Заметка о клиенте
            <textarea
              ref={input}
              rows={4}
              maxLength={2000}
              disabled={!current || busy}
              value={current?.text || ""}
              onChange={(e) =>
                current && setDraft({ ...current, text: e.target.value })
              }
              onBlur={(e) => {
                if (
                  !e.relatedTarget ||
                  !e.currentTarget.parentElement?.parentElement?.contains(
                    e.relatedTarget,
                  )
                )
                  void save();
              }}
              onKeyDown={(e) => {
                if (e.key === "Escape") {
                  e.preventDefault();
                  e.stopPropagation();
                  close();
                }
                if (e.ctrlKey && e.key === "Enter") {
                  e.preventDefault();
                  void save();
                }
              }}
            />
          </label>
          {note.data && <small>{stamp(note.data)}</small>}
          <div>
            <button
              type="button"
              disabled={!draft || busy || !!conflict}
              onClick={() => void save()}
            >
              Сохранить
            </button>
            <button type="button" onClick={close}>
              Отмена
            </button>
          </div>
        </>
      )}
      {error && <Alert color="orange">{error}</Alert>}
      {note.error && <small role="alert">{note.error.message}</small>}
      {conflict && (
        <div role="alert">
          Сохранённая версия: <pre>{conflict.text || "Пусто"}</pre>
          <button
            onClick={() => {
              setDraft({ ...conflict, text: draft?.text || "" });
              setConflict(null);
              setError("");
            }}
          >
            Продолжить с моим текстом
          </button>
          <button onClick={close}>Принять сохранённую версию</button>
        </div>
      )}
      {admin && open && (
        <button onClick={() => setHistory(!history)}>история</button>
      )}
      {admin && history && (
        <div>
          {revisions.data?.pages.flat().map((n) => (
            <article key={n.version}>
              <small>{stamp(n)}</small>
              <pre>{n.text || "Пусто"}</pre>
            </article>
          ))}
          {revisions.hasNextPage && (
            <button onClick={() => void revisions.fetchNextPage()}>Ещё</button>
          )}
        </div>
      )}
    </section>
  );
}
