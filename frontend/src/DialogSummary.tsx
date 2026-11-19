import {
  Alert,
  Button,
  Group,
  Modal,
  Select,
  Stack,
  Text,
} from "@mantine/core";
import { useMutation } from "@tanstack/react-query";
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

export function DialogSummary({
  conversationId,
  managerLanguage,
}: {
  conversationId: string;
  managerLanguage: string;
}) {
  const [opened, setOpened] = useState(false);
  const [choice, setChoice] = useState<string | null>(null);
  const language = choice || managerLanguage;
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
              disabled={request.isPending}
              onChange={(value) => {
                if (value) setChoice(value);
              }}
            />
            <Button
              loading={request.isPending}
              onClick={() => request.mutate(language)}
            >
              {result ? "Обновить сводку" : "Сформировать"}
            </Button>
          </Group>
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
