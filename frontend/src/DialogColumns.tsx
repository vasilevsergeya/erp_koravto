import { useRef, useState, type CSSProperties, type PointerEvent } from "react";

const limits = { list: [240, 280, 480], panel: [220, 250, 420] } as const;
type Side = keyof typeof limits;

export function useDialogColumns(user: string) {
  const root = useRef<HTMLDivElement>(null);
  const key = (side: Side) => `crm-width:${user}:${side}`;
  const clamp = (side: Side, width: number) =>
    Math.max(limits[side][0], Math.min(limits[side][2], width));
  function read(side: Side) {
    const stored = Number(localStorage.getItem(key(side)));
    return stored && Number.isFinite(stored)
      ? clamp(side, stored)
      : limits[side][1];
  }
  const [width, setWidth] = useState(() => ({
    list: read("list"),
    panel: read("panel"),
  }));
  const drag = useRef<{ side: Side; x: number; start: number } | null>(null);
  function resize(side: Side, value: number, save: boolean) {
    const next = clamp(side, value);
    setWidth((old) => ({ ...old, [side]: next }));
    if (save) localStorage.setItem(key(side), String(next));
  }
  function move(e: PointerEvent<HTMLDivElement>, save: boolean) {
    if (!drag.current) return;
    const { side, x, start } = drag.current;
    resize(side, start + (e.clientX - x) * (side === "list" ? 1 : -1), save);
  }
  function handle(side: Side) {
    return (
      <div
        role="separator"
        aria-label={side === "list" ? "Ширина списка" : "Ширина карточки"}
        aria-orientation="vertical"
        className={`crm-resize crm-resize-${side}`}
        onPointerDown={(e) => {
          if (e.pointerType !== "mouse" || e.button !== 0) return;
          e.preventDefault();
          const column = root.current?.querySelector(
            side === "list" ? ".crm-dialogs" : ".crm-customer",
          );
          drag.current = {
            side,
            x: e.clientX,
            start: column?.getBoundingClientRect().width ?? width[side],
          };
          e.currentTarget.setPointerCapture(e.pointerId);
          root.current?.classList.add("resizing");
        }}
        onPointerMove={(e) => move(e, false)}
        onPointerUp={(e) => move(e, true)}
        onLostPointerCapture={() => {
          drag.current = null;
          root.current?.classList.remove("resizing");
        }}
        onDoubleClick={() => resize(side, limits[side][1], true)}
      />
    );
  }
  return {
    root,
    handle,
    style: {
      "--list-width": width.list + "px",
      "--panel-width": width.panel + "px",
    } as CSSProperties,
  };
}
