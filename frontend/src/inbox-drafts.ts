import type { Message } from "./api";

export type ComposerDraft = {
  text: string;
  file: File | null;
  attachment: string | null;
  note: boolean;
  quoted: Message | null;
  sendId: string;
};
// Files stay in memory across a session handoff; text survives a page reload too.
const memory = new Map<string, ComposerDraft>();
const key = (user: string, conversation: string) => `crm-draft:${user}:${conversation}`;
export function loadDraft(user: string, conversation: string | null): ComposerDraft | undefined {
  if (!conversation) return;
  const name = key(user, conversation);
  if (memory.has(name)) return memory.get(name);
  try {
    const raw = sessionStorage.getItem(name);
    if (!raw) return;
    const value = JSON.parse(raw);
    if (typeof value.text !== "string" || typeof value.sendId !== "string") return;
    return { ...value, file: null };
  } catch { /* Storage may be disabled; in-memory drafts still work. */ }
}
export function saveDraft(user: string, conversation: string, value: ComposerDraft) {
  memory.set(key(user, conversation), value);
  try {
    sessionStorage.setItem(key(user, conversation), JSON.stringify({ ...value, file: null }));
    sessionStorage.setItem(`crm-active-conversation:${user}`, conversation);
  } catch { /* Do not lose the editor when browser storage is full. */ }
}
export function removeDraft(user: string, conversation: string) {
  memory.delete(key(user, conversation));
  try { sessionStorage.removeItem(key(user, conversation)); } catch { /* optional storage */ }
}
