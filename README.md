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
