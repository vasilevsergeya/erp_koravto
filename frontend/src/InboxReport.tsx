import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Alert, Table, TextInput } from "@mantine/core";
import { useTranslation } from "react-i18next";
import { api } from "./api";
import i18n from "./i18n";
const ru = {
  inboxReport: "Отчёт по диалогам",
  inboxReportHint: "Входящие сообщения и время первого ответа",
  start: "С",
  end: "По",
  reportEmployee: "Сотрудник",
  inbound: "Входящие",
  unanswered: "Без ответа",
  replies: "Первые ответы",
  average: "Среднее, мин",
  median: "Медиана, мин",
  unassigned: "Без ответственного",
  phone: "Ответы с телефона",
  ai: "AI",
  reportHint:
    "Период по корейскому времени. Входящие считаются по текущему ответственному, ответы — по их автору. Время отсчитывается от первого сообщения клиента до первого успешно отправленного ответа; заметки и ошибки отправки не учитываются. Входящие серии начавшиеся до периода учитываются от первого сообщения внутри периода.",
  noRows: "За выбранный период сообщений нет.",
};
const en = {
  inboxReport: "Inbox report",
  inboxReportHint: "Incoming messages and first response time",
  start: "From",
  end: "To",
  reportEmployee: "Employee",
  inbound: "Incoming",
  unanswered: "Unanswered",
  replies: "First replies",
  average: "Average, min",
  median: "Median, min",
  unassigned: "Unassigned",
  phone: "Phone replies",
  ai: "AI",
  reportHint:
    "Korea time. Incoming messages use the current assignee; replies use their author. Response time starts at the first incoming message in the selected period and ends at the first successfully sent reply. Notes and failed sends are excluded.",
  noRows: "No messages in this period.",
};
i18n.addResourceBundle("ru", "v1", ru, true, true);
i18n.addResourceBundle("en", "v1", en, true, true);
i18n.addResourceBundle("ru", "translation", ru, true, true);
i18n.addResourceBundle("en", "translation", en, true, true);
type Row = {
  owner: string;
  email: string | null;
  inbound: number;
  unanswered: number;
  replies: number;
  average_seconds: number | null;
  median_seconds: number | null;
};
export function InboxReport() {
  const { t } = useTranslation("v1");
  const today = new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Seoul",
  }).format(new Date());
  const [start, setStart] = useState(today.slice(0, 8) + "01"),
    [end, setEnd] = useState(today);
  const data = useQuery({
    queryKey: ["inbox-report", start, end],
    queryFn: () =>
      api<{ rows: Row[] }>(
        "/api/reports/inbox?" + new URLSearchParams({ start, end }),
      ),
    enabled: !!start && !!end,
  });
  const minutes = (n: number | null) =>
    n === null ? "—" : (Number(n) / 60).toFixed(1);
  return (
    <div className="settings-page">
      <section className="surface settings-card">
        <h3>{t("inboxReport")}</h3>
        <p>{t("reportHint")}</p>
        <div style={{ display: "flex", gap: 12, flexWrap: "wrap" }}>
          <TextInput
            type="date"
            label={t("start")}
            value={start}
            onChange={(e) => setStart(e.currentTarget.value)}
          />
          <TextInput
            type="date"
            label={t("end")}
            value={end}
            onChange={(e) => setEnd(e.currentTarget.value)}
          />
        </div>
        {data.error && (
          <Alert mt="md" color="red">
            {data.error.message}
          </Alert>
        )}
        <Table.ScrollContainer minWidth={650}>
          <Table>
            <Table.Thead>
              <Table.Tr>
                {[
                  "reportEmployee",
                  "inbound",
                  "unanswered",
                  "replies",
                  "average",
                  "median",
                ].map((k) => (
                  <Table.Th key={k}>{t(k)}</Table.Th>
                ))}
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {data.data?.rows.map((r) => (
                <Table.Tr key={r.owner}>
                  <Table.Td>{r.email || t(r.owner)}</Table.Td>
                  <Table.Td>{r.inbound}</Table.Td>
                  <Table.Td>{r.unanswered}</Table.Td>
                  <Table.Td>{r.replies}</Table.Td>
                  <Table.Td>{minutes(r.average_seconds)}</Table.Td>
                  <Table.Td>{minutes(r.median_seconds)}</Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        </Table.ScrollContainer>
        {data.data?.rows.length === 0 && <p>{t("noRows")}</p>}
      </section>
    </div>
  );
}
