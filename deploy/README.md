# Развёртывание и обслуживание KORAVTO CRM

Все команды выполняются на сервере через Gateway. Рабочая копия — `/opt/crm`,
Compose — `/opt/crm/deploy/docker-compose.yml`, окружение — `/opt/crm/deploy/.env`.
Используйте root или sudo. Не включайте `set -x`, не выводите `.env`, `docker inspect`
или развёрнутый `docker compose config`: там могут быть секреты.

## Первый запуск

1. Подготовить Ubuntu, Docker Engine и Compose plugin, git, openssl, ufw,
   unattended-upgrades. ZeroTier работает на хосте. Проверить DNS A/AAAA, AWS Security Group
   и firewall. Публичные входящие: 80/tcp, 443/tcp, 9993/udp; 22/tcp — только доверенные
   адреса управления Gateway/заказчика. Сервис Mobis 8080 доступен только через ZeroTier.
2. Клонировать репозиторий в **новую** директорию:

```sh
git clone --branch main https://github.com/vasilevsergeya/erp_koravto.git /opt/crm
cd /opt/crm
python3 deploy/scripts/init-env.py
cd deploy
docker compose config --quiet
docker compose build api web backup
docker compose up -d
docker compose ps
```

`init-env.py` создаёт каталоги данных, `.env` (600), отдельный пароль PostgreSQL (600).
Все секреты генерируются `openssl rand`; повторный запуск отказывается заменять `.env`.
В `.env` только инфраструктура. Ключи Wazzup, OpenAI, 1С и SMTP вводятся в интерфейсе.
Не удаляйте `MASTER_ENCRYPTION_KEY`: без него секреты интеграций из восстановленной БД не расшифровать.
Сохраните `.env` отдельно в защищённом хранилище администратора. Пароль PostgreSQL
в `data/secrets/postgres-password` должен соответствовать паролю в `DATABASE_URL`.

API применяет Alembic под advisory lock до запуска HTTP. Воркеры ждут миграции; они
не зависят от `api: healthy`, иначе `/health`, учитывающий воркер, создавал бы цикл запуска.
Убедиться в ответе `200`:

```sh
curl --fail --silent --show-error https://erp.korean.parts/health
docker compose exec -T api python -m app.cli create-admin --email admin@example.com --password-file /run/bootstrap/admin.txt
docker compose exec -T api python -m app.cli seed-boards
sh scripts/tag-release.sh
```

Пароль сохранится в `/opt/crm/data/bootstrap/admin.txt`, режим 600. Передайте его владельцу
однократно по защищённому каналу; после смены пароля удалите файл. В журнал он не выводится.

## Первый вход администратора

1. Открыть https://erp.korean.parts и ввести email и временный пароль.
2. Придумать новый пароль (не менее 7 символов).
3. Открыть «Настройки» → «WhatsApp / Wazzup», сохранить ключ, нажать «Проверить подключение».
4. Нажать «Подписать вебхуки». Если Wazzup уже связан с другой CRM, система отдельно
   спросит о замене подписки: после замены предыдущая CRM перестанет получать вебхуки.
5. Убедиться в активной подписке и времени тестового вебхука. Отправить сообщение с
   другого телефона на подключённый WhatsApp и проверить раздел «Диалоги».

Inbox принимает и отправляет сообщения и файлы. Доступны новый диалог по номеру,
цитаты, внутренние заметки, назначение ответственного, поиск и ручная привязка заказов.
Полноценные задачи, AI и живые вызовы 1С/SMTP не реализованы. Доски доступны для просмотра.
Ключи будущих интеграций можно сохранить, но это не запускает интеграции.

## Проверки

```sh
cd /opt/crm/deploy
docker compose ps
curl --fail --silent --show-error https://erp.korean.parts/health
docker run --rm --memory 512m --memory-swap 1g --network koravto-crm_backend --env-file .env koravto-crm-api:current python -m app.selftest
docker compose exec -T backup sh /scripts/backup.sh
```

Проверка выполняется в отдельном контейнере с лимитом 512 МБ и не делит лимит памяти рабочего API.
`selftest` создаёт отдельную базу `crm_verify_*`, применяет миграции, запускает тестовый
воркер, проверяет вход по паролю/смену временного пароля/CSRF, настройки, идемпотентность и приём сообщений.
Тестовая БД удаляется в `finally`. Redis DB 15 зарезервирована для этих тестов;
рабочее приложение использует DB 0. Не запускайте два selftest одновременно.
Данные реального администратора и рабочие сообщения не изменяются.

## Обновление

Перед изменением схемы создайте свежую копию. Сохраните текущие образы, чтобы работал откат:

```sh
cd /opt/crm/deploy
docker compose exec -T backup sh /scripts/backup.sh
sh scripts/tag-release.sh
git pull --ff-only origin main
docker compose build api web backup
docker compose up -d
curl --fail --silent --show-error https://erp.korean.parts/health
sh scripts/tag-release.sh
```

Миграции применяются автоматически. Для несовместимой миграции сначала остановите
`api` и `worker` и согласуйте окно обслуживания. Перед добавлением новой миграции
обновите ожидаемый revision в `app/wait_schema.py`. Не удаляйте предыдущие теги образов
до завершения проверки новой версии. Версии Python/JS библиотек закреплены lock-файлами.

## Откат

Узнать доступные образы и выбрать SHA ранее проверенного релиза:

```sh
docker image ls --filter reference='koravto-crm-*'
CRM_RELEASE=PREVIOUS_COMMIT_SHA docker compose up -d --no-build
curl --fail --silent --show-error https://erp.korean.parts/health
```

Замените `PREVIOUS_COMMIT_SHA` на сохранённый 12-символьный тег. Чтобы пережить последующие
сеансы обслуживания, зафиксируйте выбранный тег в документации релиза либо экспортируйте
`CRM_RELEASE` перед следующей командой Compose. Не запускайте обычное `up` с устаревшим
`current` после отката. При несовместимой/необратимой миграции сначала восстановите
дамп до обновления в новую базу и переключите `DATABASE_URL`, затем старые образы.

## Бэкапы

Каждый день в 03:00 Asia/Seoul создаётся `/opt/crm/backups/crm-YYYYMMDD-HHMMSS/`:

- `database.dump`: PostgreSQL custom dump;
- `attachments.tar.gz`: локальные неизменяемые файлы вложений;
- `manifest.json`: время, имя базы, SHA-256 файлов (секреты окружения не включены).

Хранятся 30 дней, удаляются только завершённые копии заданного формата. Запись атомарная,
параллельный запуск блокируется. При ошибке есть две повторные попытки. Последний успех —
`backups/status.json`, ошибки — `docker compose logs --tail 50 backup`. Файл `.env`, мастер-ключ
и исходники не входят в дамп. Локальный бэкап не защищает от потери диска/инстанса.

S3 выключен. Если позже задан `BACKUP_S3_BUCKET` и параметры доступа S3, завершённые копии
также выгружаются туда; срок хранения S3 настраивается lifecycle-правилом бакета отдельно.

## Проверенное восстановление на пустую базу

`restore.sh` проверяет SHA-256 и создаёт новую базу. Он **никогда не удаляет существующую**.
Имя новой базы обязано начинаться с `crm_restore_`.

```sh
cd /opt/crm/deploy
docker compose exec -T backup sh /scripts/restore.sh /backups/crm-YYYYMMDD-HHMMSS --database crm_restore_check
docker compose exec -T postgres psql -U crm -d crm_restore_check -c 'SELECT version_num FROM alembic_version;'
docker compose exec -T postgres psql -U crm -d crm_restore_check -c 'SELECT count(*) FROM users;'
```

После проверки ненужную **тестовую** базу можно удалить точной командой:

```sh
docker compose exec -T postgres dropdb -U crm crm_restore_check
```

Для рабочего восстановления:

1. Остановить `api worker backup`. Сохранить текущую `.env` отдельно (600), не менять
   `MASTER_ENCRYPTION_KEY` и `SECRET_KEY` на новые.
2. Запустить PostgreSQL и Redis. Восстановить выбранный дамп в `crm_restore_recovery`:

```sh
docker compose stop api worker backup
docker compose run --rm --no-deps backup sh /scripts/restore.sh /backups/crm-YYYYMMDD-HHMMSS --database crm_restore_recovery
```

3. Если потеряны вложения, распаковать архив на хосте в **пустой временный каталог**,
   проверить файлы, назначить владельца UID/GID 10001 и заменить каталог `data/files`
   при остановленных приложениях. Существующие вложения предварительно сохранить:

```sh
mkdir -m 700 /opt/crm/data/files-restored
tar --no-same-owner -xzf /opt/crm/backups/crm-YYYYMMDD-HHMMSS/attachments.tar.gz -C /opt/crm/data/files-restored
chown -R 10001:10001 /opt/crm/data/files-restored
mv /opt/crm/data/files /opt/crm/data/files-before-restore
mv /opt/crm/data/files-restored /opt/crm/data/files
```

4. В редакторе заменить только имя базы в `DATABASE_URL` на `crm_restore_recovery`.
   Пароль, пользователя и мастер-ключ сохранить. Проверить `.env` имеет права 600.
5. Запустить требуемый релиз, проверить health, вход, настройки, диалоги и вложения:

```sh
CRM_RELEASE=RESTORED_RELEASE_SHA docker compose up -d --no-build --force-recreate api worker backup
curl --fail --silent --show-error https://erp.korean.parts/health
```

Старая база остаётся доступной для диагностики. На новом сервере восстановите защищённую
`.env` и пароль PostgreSQL до запуска, используйте тот же мастер-ключ, клонируйте нужный
релиз, затем выполните процедуру выше. Наличие одного дампа без мастер-ключа недостаточно.

## Переменные окружения и отдельный сервис

1. Добавить инфраструктурную переменную в `.env.example` с безопасным примером и в
   `environment` нужного сервиса Compose. Реальное значение — только в `.env`.
2. Проверить конфигурацию без вывода секретов, пересоздать нужный сервис:

```sh
docker compose config --quiet
docker compose up -d --no-deps --force-recreate api
```

Простой `restart` не подхватывает изменившееся окружение. Если параметр используется
также воркером, пересоздать и его. Ключи интеграций меняются в интерфейсе без перезапуска;
кэш сбрасывается с использованием версии в Redis и pub/sub.

## Файлы и S3

По умолчанию `FILES_BACKEND=local`, том `data/files`, доступ по подписанным ссылкам на
10 минут. Для новых файлов можно переключить `FILES_BACKEND=s3`, задать `S3_BUCKET`,
регион, endpoint при необходимости и учётные данные, пересоздать `api worker`.
Старые вложения сохраняют свой backend и остаются в старом хранилище; автоматического
переноса нет. Объекты S3 требуют собственного versioning/lifecycle/backup — локальный
архив содержит только локальный том. В текущем развёртывании S3 не используется.

## Восстановление доступа администратора

Только через доверенный Gateway с правами администратора сервера:

```sh
docker compose exec -T api python -m app.cli reset-admin --email admin@example.com --password-file /run/bootstrap/admin-recovery.txt
```

Сбрасываются сессии и пароль; при следующем входе обязательна смена временного пароля.
Файл назначения должен быть новым. После передачи пароля и настройки удалить его.

## Журналы и ресурсы

Docker: `json-file`, 50 МБ × 5 для всех сервисов. Прикладные JSON-логи с correlation ID —
`data/logs/app`, ежедневная ротация с 14 архивами. Caddy отбрасывает URI и HTTP-заголовки
из access/runtime логов, чтобы не записывать токен вебхука, cookie или Authorization.
Пользовательские тексты, пароли и ключи не записываются в прикладные логи.

Исходный сервер t3.micro имеет около 1 ГБ RAM и одновременно выполняет парсер Mobis.
Для первого запуска добавлен swap 2 ГБ и ограничения памяти контейнеров. Это не проверка
производительности полноценной CRM: перед работой всей команды нужен запас памяти и
нагрузочная проверка. Автоматические обновления ОС включены; автоматическая перезагрузка
отключена, чтобы не прерывать парсер. Требуемую перезагрузку планировать отдельно.


## Вход по паролю (изменение от 11.11.2026)

По запросу владельца двухфакторная аутентификация удалена. После ввода email и пароля
пользователь получает доступ; временный пароль по-прежнему нужно заменить при первом входе.
Действующие сессии, ожидавшие настройки TOTP, допускаются после смены временного пароля.
QR-коды и TOTP API удалены. Миграция 0002 очищает сохранённые и незавершённые TOTP-секреты,
сохраняя пароли, роли, ограничения попыток входа, CSRF и остальные данные.

После обновления перезагрузить страницу, чтобы получить новый интерфейс. Если прежняя
15-минутная сессия истекла, войти с уже установленным паролем.

Перед откатом на релиз с миграцией 0001 остановить API и worker, выполнить downgrade
используя **новый** образ, затем запустить старый релиз:

```sh
docker compose stop api worker
docker compose run --rm --no-deps -T api python - <<'PYDOWN'
from alembic import command
from alembic.config import Config
from app.core.db import engine
with engine.begin() as connection:
    config = Config("alembic.ini")
    config.attributes["connection"] = connection
    command.downgrade(config, "0001")
PYDOWN
CRM_RELEASE=PREVIOUS_COMMIT_SHA docker compose up -d --no-build api worker web
```

Удалённые TOTP-секреты при таком откате не восстанавливаются: старый интерфейс потребует
повторного подключения аутентификатора. Для полного восстановления прежнего состояния
использовать дамп до обновления по основной процедуре восстановления.

## Обновление Inbox от 12.11.2026 (миграция 0003)

Миграция добавляет поля очереди, управление диалогами, метаданные файлов, индекс поиска
и квитанции доставки. Существующие диалоги, сообщения и настройки сохраняются.
Перед обновлением выполнить backup и tag-release. После build/up проверить /health.
В Caddy добавлен отдельный лимит тела 11 MB для `/api/uploads` (10 MiB файл плюс multipart);
для вебхуков/прочего API сохранён 1 MB. Caddy нужно пересоздать вместе с api/worker/web,
так как его административный интерфейс выключен.

Команда selftest проверяет исходящие только с подменённым Wazzup API, в отдельной базе.
Она не использует реальные ключи Wazzup и не отправляет клиентам тестовых сообщений.
Для сквозной приёмки реальной доставки менеджер должен отправить текст и файл на свой
тестовый WhatsApp, проверить статусы доставлено/прочитано и ответ с телефона.

После таймаута повтор ограничен 60-секундным окном `crmMessageId`. При неизвестном
результате или прерывании отправки воркером сообщение получает «Доставка не подтверждена».
Перед ручным повтором интерфейс требует проверить WhatsApp и подтвердить риск дубля.
Положительный ответ REPEATED_CRM_MESSAGE_ID означает «Принято Wazzup», но не «Прочитано».
Если провайдер не вернул messageId и не передал наш UUID в вебхуке, автоматическое
сопоставление такого сообщения с последующим echo не выполняется по догадке о тексте.

Для отката к коду 0002 не нужно удалять новые поля: API старой версии совместим с
добавленными полями. Старый worker ждёт строго 0002, поэтому остановите api/worker и
выполните downgrade до 0002 новым образом перед запуском старых. Сначала сохраните
новый дамп: downgrade удаляет данные очереди/цитат/назначений, добавленные в 0003.
Предпочтительный откат после начала работы: исправление вперёд или восстановление
дампа в отдельную базу по основной процедуре. Не запускайте старый worker с очередью
исходящих сообщений, которую он не поддерживает.
