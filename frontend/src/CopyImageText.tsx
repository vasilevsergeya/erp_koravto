import { useLayoutEffect, useRef, useState, type RefObject } from "react";
import { Copy } from "lucide-react";

type OcrResult = { text: string; uncertain: boolean };

function recognize(
  image: HTMLImageElement,
  angle: number,
  signal: AbortSignal,
) {
  const canvas = document.createElement("canvas");
  const { naturalWidth: w, naturalHeight: h } = image;
  const sideways = angle % 180 !== 0;
  // Keep the whole photo, including content beyond the zoomed viewport.
  const scale = Math.min(
    1,
    Math.sqrt(16_000_000 / (w * h)),
    16384 / Math.max(w, h),
  );
  canvas.width = Math.max(1, Math.round((sideways ? h : w) * scale));
  canvas.height = Math.max(1, Math.round((sideways ? w : h) * scale));
  const ctx = canvas.getContext("2d");
  if (!ctx) throw new Error("Canvas unavailable");
  ctx.fillStyle = "white";
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  ctx.translate(canvas.width / 2, canvas.height / 2);
  ctx.rotate((angle * Math.PI) / 180);
  ctx.scale(scale, scale);
  ctx.drawImage(image, -w / 2, -h / 2);

  const worker = new Worker(new URL("./imageOcr.worker.ts", import.meta.url), {
    type: "module",
  });
  let onAbort: () => void;
  let timeout: number;
  return new Promise<OcrResult>((resolve, reject) => {
    onAbort = () => reject(new DOMException("Cancelled", "AbortError"));
    signal.addEventListener("abort", onAbort, { once: true });
    timeout = window.setTimeout(
      () => reject(new Error("OCR timeout")),
      120_000,
    );
    worker.onerror = () => reject(new Error("OCR failed"));
    worker.onmessage = ({ data }) => {
      if (typeof data.text === "string")
        resolve({ text: data.text, uncertain: data.uncertain === true });
      else reject(new Error("OCR failed"));
    };
    canvas.toBlob((blob) => {
      if (signal.aborted) return;
      if (blob) worker.postMessage(blob);
      else reject(new Error("Image unavailable"));
    }, "image/png");
  }).finally(() => {
    clearTimeout(timeout);
    signal.removeEventListener("abort", onAbort);
    worker.terminate();
  });
}

export function CopyImageText({
  image,
  angle,
  disabled,
}: {
  image: RefObject<HTMLImageElement>;
  angle: number;
  disabled: boolean;
}) {
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const [manual, setManual] = useState(false);
  const cached = useRef<OcrResult | null>(null);
  const pending = useRef<AbortController | null>(null);
  useLayoutEffect(() => () => pending.current?.abort(), []);

  async function copy() {
    if (!image.current || pending.current) return;
    const controller = new AbortController();
    pending.current = controller;
    const { signal } = controller;
    setBusy(true);
    setManual(false);
    setNotice(cached.current ? "" : "Распознаём текст…");
    try {
      const result = cached.current
        ? Promise.resolve(cached.current)
        : recognize(image.current, angle, signal);
      // Start the clipboard operation inside the click handler, also on Safari.
      let writing: Promise<boolean> | undefined;
      if (typeof ClipboardItem !== "undefined" && navigator.clipboard?.write) {
        const blob = result.then(({ text }) => {
          if (!text || signal.aborted) throw new Error("No text to copy");
          return new Blob([text], { type: "text/plain" });
        });
        // A browser can reject clipboard access before it consumes the promise.
        void blob.catch(() => {});
        try {
          writing = navigator.clipboard
            .write([new ClipboardItem({ "text/plain": blob })])
            .then(
              () => true,
              () => false,
            );
        } catch {
          /* Fall back to writing plain text below. */
        }
      }
      const recognized = await result;
      const { text } = recognized;
      if (signal.aborted) return;
      if (!text) {
        setNotice("На изображении не найден текст.");
        return;
      }
      cached.current = recognized;
      if (!(await writing)) {
        if (signal.aborted) return;
        try {
          await navigator.clipboard.writeText(text);
        } catch {
          if (!signal.aborted) {
            setManual(true);
            setNotice(
              "Браузер не разрешил копирование. Выделите и скопируйте текст ниже.",
            );
          }
          return;
        }
      }
      if (!signal.aborted)
        setNotice(
          recognized.uncertain
            ? "Текст скопирован. Сверьте распознанные символы с фото."
            : "Текст скопирован",
        );
    } catch {
      if (!signal.aborted)
        setNotice("Не удалось распознать текст. Попробуйте ещё раз.");
    } finally {
      if (!signal.aborted) setBusy(false);
      pending.current = null;
    }
  }

  return (
    <>
      <button
        type="button"
        className="gallery-copy"
        disabled={disabled || busy}
        onClick={() => void copy()}
        title="Скопировать текст с изображения"
      >
        <Copy aria-hidden="true" /> Скопировать текст
      </button>
      <span role="status" className="gallery-ocr-status">
        {notice}
      </span>
      {manual && (
        <textarea
          className="gallery-ocr-result"
          aria-label="Распознанный текст"
          readOnly
          value={cached.current?.text ?? ""}
          onFocus={(event) => event.currentTarget.select()}
        />
      )}
    </>
  );
}
