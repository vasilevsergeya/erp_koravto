import { useState } from "react";
import type { Conversation } from "./api";

export function CustomerAvatar({ c }: { c: Conversation }) {
  const [failed, setFailed] = useState("");
  const code = /^[A-Z]{2}$/.test(c.phone_country || "")
    ? c.phone_country
    : undefined;
  const initials = (c.display_name || c.name || "?")
    .trim()
    .split(/\s+/)
    .slice(0, 2)
    .map((s) => Array.from(s)[0])
    .join("")
    .toUpperCase();
  const flag = code
    ? Array.from(code, (s) =>
        String.fromCodePoint(127397 + s.charCodeAt(0)),
      ).join("")
    : initials;
  const tip = [
    code
      ? new Intl.DisplayNames(["ru"], { type: "region" }).of(code)
      : "Страна неизвестна",
    c.client_number,
  ]
    .filter(Boolean)
    .join(" · ");
  return (
    <span className="crm-avatar" title={tip} aria-label={tip}>
      {c.avatar_url && c.avatar_url !== failed ? (
        <img
          alt=""
          loading="lazy"
          src={c.avatar_url}
          onError={() => setFailed(c.avatar_url || "")}
        />
      ) : (
        flag
      )}
    </span>
  );
}
