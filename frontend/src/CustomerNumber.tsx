import { useState } from "react";

export function CustomerNumber({ value }: { value?: string | null }) {
  const [copied, setCopied] = useState(false);
  if (!value) return null;
  return (
    <button
      type="button"
      className="crm-number"
      title="Скопировать номер клиента"
      onClick={async () => {
        try {
          await navigator.clipboard.writeText(value);
          setCopied(true);
          setTimeout(() => setCopied(false), 1200);
        } catch {
          window.prompt("Скопировать номер клиента", value);
        }
      }}
    >
      {copied ? "Скопировано" : value}
    </button>
  );
}

const pattern = /(?<![\p{L}\p{N}_])([CС]-?[0-9]+)(?![\p{L}\p{N}_])/giu;
export function NumberLinks({ text }: { text: string }) {
  return (
    <>
      {text.split(pattern).map((part, i) =>
        i % 2 ? (
          <a key={i} href={"/?client=" + encodeURIComponent(part)}>
            {part}
          </a>
        ) : (
          part
        ),
      )}
    </>
  );
}
