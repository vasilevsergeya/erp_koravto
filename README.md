# KORAVTO CRM — first deployment

Python 3.12 / FastAPI, PostgreSQL 16 + pgvector, Redis + arq, React 18 / TypeScript.
Deployment: <https://erp.korean.parts>. Operational instructions: [deploy/README.md](deploy/README.md).

This first stage includes administrator bootstrap, forced password change, mandatory TOTP,
encrypted integration settings, read-only seeded boards, durable Wazzup inbound messages,
attachments, WebSocket notifications, HTTPS and verified database backups.

The complete task workflow, outbound messages, AI responses, customer management, live 1C
integration, SMTP delivery and the remaining CRM v1 features are subsequent development.
Saving credentials does not enable those integrations.

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
