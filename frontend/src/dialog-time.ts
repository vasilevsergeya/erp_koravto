export const dayKey = (value: string | number) =>
  new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Seoul" }).format(
    new Date(value),
  );
export const clockTime = (value: string | number) =>
  new Intl.DateTimeFormat("ru-RU", {
    hour: "2-digit",
    minute: "2-digit",
    timeZone: "Asia/Seoul",
  }).format(new Date(value));
export const shortTime = (value: string | number) =>
  dayKey(value) === dayKey(new Date().toISOString())
    ? clockTime(value)
    : new Intl.DateTimeFormat("ru-RU", {
        day: "2-digit",
        month: "2-digit",
        timeZone: "Asia/Seoul",
      }).format(new Date(value));
export const dayLabel = (value: string | number) =>
  dayKey(value) === dayKey(new Date().toISOString())
    ? "Сегодня"
    : new Intl.DateTimeFormat("ru-RU", {
        day: "numeric",
        month: "long",
        timeZone: "Asia/Seoul",
      }).format(new Date(value));
