import type { QueryClient } from "@tanstack/react-query";

export type Member = { id: string; name: string; role: string; label?: string };

export function memberOptions(members?: Member[]) {
  return (members || []).map((member) => ({
    value: member.id,
    label: member.label || member.name,
  }));
}

export function invalidateTeamQueries(query: QueryClient) {
  const keys = [
    "team",
    "admin-users",
    "conversation-counts",
    "tasks",
    "task",
    "task-report",
    "messages",
    "customer",
    "customers",
  ];
  return Promise.all(
    keys.map((key) => query.invalidateQueries({ queryKey: [key] })),
  );
}
