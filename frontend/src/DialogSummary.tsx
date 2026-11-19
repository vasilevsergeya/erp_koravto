import {
  Alert,
  Button,
  Group,
  Modal,
  Select,
  Stack,
  Text,
} from "@mantine/core";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "./api";
import { languageName, languages } from "./MessageTools";

const taskHeading: Record<string, string> = {
  ru: "Задача",
  en: "Task",
  ko: "할 일",
  de: "Aufgabe",
  fr: "Tâche",
  es: "Tarea",
  it: "Compito",
  pt: "Tarefa",
  ar: "المهمة",
  zh: "任务",
  ja: "タスク",
  tr: "Görev",
  pl: "Zadanie",
  uk: "Завдання",
  he: "משימה",
  id: "Tugas",
  vi: "Nhiệm vụ",
  th: "งาน",
  hi: "कार्य",
  nl: "Taak",
};

export function useSummaryLanguage(
  userId: string,
  enabled: boolean,
  fallback: string,
) {
  const cache = useQueryClient();
  const key = ["summary-language", userId];
  const path = "/api/me/summary-language";
  const preference = useQuery({
    queryKey: key,
    queryFn: () => api<{ language: string | null }>(path),
    enabled,
  });
  const save = useMutation({
    mutationFn: (language: string) =>
      api<{ language: string }>(path, "PUT", { language }),
    onSuccess: async (updated) => {
      await cache.cancelQueries({ queryKey: key });
      cache.setQueryData(key, updated);
    },
  });
  return {
    language:
      (save.isPending ? save.variables : preference.data?.language) || fallback,
    busy: preference.isLoading || save.isPending,
    error: save.isError
      ? "Не удалось сохранить язык сводки. Выберите язык ещё раз."
      : preference.isError
        ? "Не удалось загрузить сохранённый язык сводки."
        : null,
    change: save.mutate,
  };
}

export function DialogSummary({
  conversationId,
  preference,
}: {
  conversationId: string;
  preference: ReturnType<typeof useSummaryLanguage>;
}) {
  const [opened, setOpened] = useState(false);
  const { language } = preference;
  const options = languages.some((item) => item.value === language)
    ? languages
    : [...languages, { value: language, label: languageName(language) }];
  const request = useMutation({
    mutationFn: (target: string) =>
      api<{ summary: string; task: string }>(
        `/api/conversations/${conversationId}/summary`,
        "POST",
        { language: target },
      ),
  });
  const result = request.variables === language ? request.data : undefined;
  return (
    <>
      <Button size="compact-xs" variant="light" onClick={() => setOpened(true)}>
        AI-сводка
      </Button>
      <Modal
        opened={opened}
        onClose={() => setOpened(false)}
        title="AI-сводка диалога"
        centered
      >
        <Stack gap="md">
          <Group align="flex-end">
            <Select
              flex="1"
              label="Язык сводки"
              searchable
              allowDeselect={false}
              data={options}
              value={language}
              disabled={request.isPending || preference.busy}
              onChange={(value) => {
                if (value) preference.change(value);
              }}
            />
            <Button
              loading={request.isPending}
              disabled={preference.busy}
              onClick={() => request.mutate(language)}
            >
              {result ? "Обновить сводку" : "Сформировать"}
            </Button>
          </Group>
          {preference.error && (
            <Alert color="red" role="alert">
              {preference.error}
            </Alert>
          )}
          {request.isError && request.variables === language && (
            <Alert color="red" role="alert">
              {request.error.message}
            </Alert>
          )}
          {result && (
            <Stack
              gap="xs"
              lang={language}
              dir="auto"
              aria-live="polite"
              style={{ overflowWrap: "anywhere" }}
            >
              <Text style={{ whiteSpace: "pre-wrap" }}>{result.summary}</Text>
              <section>
                <Text fw={700}>
                  {taskHeading[language.split("-")[0]] || "Задача"}
                </Text>
                <Text style={{ whiteSpace: "pre-wrap" }}>{result.task}</Text>
              </section>
            </Stack>
          )}
        </Stack>
      </Modal>
    </>
  );
}
