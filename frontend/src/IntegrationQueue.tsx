import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Alert, Button, Modal, Table } from "@mantine/core";
import { api } from "./api";

type Queue = {
  pending: number; failed: number;
  items: { id: string; created_at: string; attempts: number; reason: string }[];
};
export function IntegrationQueue() {
  const [opened, setOpened] = useState(false);
  const [page, setPage] = useState(0);
  const data = useQuery({
    queryKey: ["failed-events", page],
    queryFn: () => api<Queue>(`/api/integrations/events/failed?offset=${page * 50}`),
    refetchInterval: 60_000,
  });
  return <>
    <Button variant="light" size="xs" mt="sm" onClick={() => setOpened(true)}>
      Ошибки входящих событий{data.data ? ` · ${data.data.failed}` : ""}
    </Button>
    <Modal opened={opened} onClose={() => setOpened(false)} title="Ошибки входящих событий" size="lg">
      {data.error && <Alert color="red">{data.error.message}</Alert>}
      {data.data && <>
        <p>Ожидают обработки: {data.data.pending}. Завершились ошибкой: {data.data.failed}.</p>
        <p>После 10 попыток событие исключается из ожидающей очереди. Его данные сохраняются для разбора.</p>
        <Table><Table.Thead><Table.Tr><Table.Th>Получено</Table.Th><Table.Th>Попытки</Table.Th><Table.Th>Причина</Table.Th></Table.Tr></Table.Thead>
          <Table.Tbody>{data.data.items.map(item => <Table.Tr key={item.id}>
            <Table.Td>{new Date(item.created_at).toLocaleString("ru-RU", { timeZone: "Asia/Seoul" })} (Сеул)</Table.Td>
            <Table.Td>{item.attempts}</Table.Td><Table.Td>{item.reason}</Table.Td>
          </Table.Tr>)}</Table.Tbody></Table>
        {!data.data.items.length && <p>Ошибочных событий нет.</p>}
        <Button variant="subtle" disabled={!page} onClick={() => setPage(p => p - 1)}>Назад</Button>
        <Button variant="subtle" disabled={(page + 1) * 50 >= data.data.failed} onClick={() => setPage(p => p + 1)}>Далее</Button>
      </>}
    </Modal>
  </>;
}
