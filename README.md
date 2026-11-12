# KORAVTO CRM — first deployment

Python 3.12 / FastAPI, PostgreSQL 16 + pgvector, Redis + arq, React 18 / TypeScript.
Deployment: <https://erp.korean.parts>. Operational instructions: [deploy/README.md](deploy/README.md).

Implemented: password login with forced first-password change, encrypted settings,
HTTPS/backups, durable inbound and outbound WhatsApp messages, attachments up to 10 MiB,
new conversations by telephone, quotes, internal notes, assignments, human flags,
close/reopen, unread counters, text/phone/order search and manual order references.
Outbound jobs persist in PostgreSQL before sending; HTTP retries reuse a client UUID.
Provider timeouts are retried only inside Wazzup's 60-second deduplication window;
unknown delivery after that window requires an explicit duplicate-risk confirmation.

The complete task workflow, AI responses, unified customer management, live 1C integration,
SMTP delivery and historical Wazzup import remain subsequent development. Boards are still
read-only. Saving credentials does not enable those unfinished integrations. This release
is an Inbox increment, not acceptance of the full CRM v1 specification.

## Development

Backend dependencies are pinned in `backend/requirements.lock`; frontend dependencies in
`frontend/package-lock.json`. Run `ruff check`, `mypy app`, `pytest` in backend and
`npm ci && npm run build && npm run lint` in frontend.

Do not commit `.env`, credentials, customer data, dumps, logs or generated administrator passwords.
The repository is public; runtime data and integration credentials remain on the server.

## Integration references

- https://wazzup24.com/help/api-en/webhooks/
- https://arq-docs.helpmanual.io/
- https://docs.docker.com/engine/install/ubuntu/
- https://caddyserver.com/docs/automatic-https


On 2026-11-11 the owner requested removal of two-factor authentication. Revision 0002
clears existing TOTP enrollment secrets; passwords and user roles are preserved.

## Клиенты и заказы из 1С

Inbox использует сервис `koravto-1c` (контракт `afbf86b`): `GET /prod/hs/koravto/customers?phone=...`, HTTP Basic Auth. В Настройках → «Клиенты и заказы · 1С» укажите полный адрес сервиса, пользователя и пароль. Пароль зашифрован, доступен только серверу; браузер получает только признак настройки. Запросы идут с API/worker через существующую сеть ZeroTier.

Автоматический поиск выполняется для личных WhatsApp-диалогов при открытии карточки и фоновым обработчиком (не чаще одного запроса за 2 секунды, TTL 15 минут). Кнопка «Обновить из 1С» обновляет досрочно с защитой от повторных запросов в течение 10 секунд. При сбое повтор через минуту; последняя успешная информация сохраняется с отметкой устаревания. Закрытые диалоги без новых сообщений не опрашиваются постоянно.

Один найденный клиент даёт имя в списке и заголовке чата. Несколько совпадений отображаются отдельно, без автоматического выбора: сервис ищет последние шесть цифр в телефонах 1С и не отдаёт уникальный ID контрагента. Номера заказов — строки `realizations` (НомерВКаналеПродаж либо номер реализации 1С); даты, суммы и статусы этот сервис не отдаёт. Порядок, ведущие нули, одинаковые имена и повторяющиеся номера документов сохраняются. Ручные имя чата и привязки заказов не изменяются. Поиск Inbox учитывает сохранённые имена и номера из 1С.

Миграция `0004` добавляет только поля кэша. Недоступность 1С не блокирует переписку. Изменение телефона/настроек подключения скрывает старый результат до нового поиска. Учётные данные, тела ошибок и телефоны не попадают в журналы интеграции.
