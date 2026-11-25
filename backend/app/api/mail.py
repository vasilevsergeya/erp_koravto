import asyncio
from contextlib import asynccontextmanager

import httpx
from fastapi import APIRouter, Depends, HTTPException, Path, Query
from sqlalchemy.orm import Session

from app.api.auth import current_user
from app.core.db import get_db
from app.core.settings import direct_secret
from app.integrations.gmail import THREAD_ID, GmailError, GmailMailbox, bearer, belongs_to, describe
from app.integrations.gmail_imap import imap_credentials
from app.integrations.onec import OneCAdapter, OneCError
from app.services.onec_customers import connection_settings

router = APIRouter(prefix="/api/mail", tags=["mail"], dependencies=[Depends(current_user)])

_ERRORS = {
    "imap_login": (
        502,
        "Gmail отклонил вход. Проверьте полный адрес ящика и пароль приложения Google.",
    ),
    "imap_unavailable": (
        502,
        "Не удалось подключиться к Gmail по IMAP. Проверьте доступ к IMAP и повторите попытку.",
    ),
    "all_mail_hidden": (502, "В настройках ярлыков Gmail включите показ папки «Вся почта» в IMAP."),
    "page_expired": (409, "Список писем изменился. Обновите почту."),
    "invalid_credentials": (
        502,
        "Токен Gmail недействителен или истёк. Обновите его в настройках.",
    ),
    "access_denied": (
        502,
        "Gmail отказал в доступе. Проверьте право gmail.readonly и доступность Gmail API.",
    ),
    "not_found": (404, "Цепочка писем больше недоступна."),
    "rate_limited": (503, "Gmail временно ограничил частоту запросов. Повторите позже."),
    "too_large": (413, "Цепочка слишком велика для просмотра в ERP."),
}


def gmail_setting(db: Session = Depends(get_db)):
    value = direct_secret(db, "gmail.credentials")
    db.commit()
    return value


@asynccontextmanager
async def mailbox(value):
    try:
        async with asyncio.timeout(45):
            imap = imap_credentials(value)
            if imap:
                yield imap
            else:
                async with httpx.AsyncClient(
                    timeout=15, follow_redirects=False, trust_env=False
                ) as client:
                    yield GmailMailbox(client, await bearer(client, value))
    except (GmailError, httpx.HTTPError, TimeoutError, KeyError, TypeError, ValueError) as exc:
        code = str(exc) if isinstance(exc, GmailError) else "unavailable"
        status, message = _ERRORS.get(code, (502, "Не удалось получить ответ от Gmail."))
        raise HTTPException(status, message) from None


@router.get("/dialogs")
async def dialogs(
    page: str = Query("", max_length=1024),
    q: str = Query("", max_length=200, pattern=r"^[^\x00-\x1f\x7f]*$"),
    value: str = Depends(gmail_setting),
):
    if not value:
        return {"mailbox": None, "dialogs": [], "next": None}
    async with mailbox(value) as gmail:
        return await gmail.dialog_page(page, q)


@router.get("/dialogs/messages")
async def dialog_messages(
    email: str = Query(max_length=254, pattern=r"^[^@\s<>\x00-\x1f\x7f]+@[^@\s<>\x00-\x1f\x7f]+$"),
    page: str = Query("", max_length=1024),
    value: str = Depends(gmail_setting),
):
    if not value:
        raise HTTPException(409, "Почта Gmail ещё не подключена.")
    async with mailbox(value) as gmail:
        return await gmail.dialog_page(page, "", email)


@router.get("/threads")
async def threads(
    page: str = Query("", max_length=1024),
    q: str = Query("", max_length=200, pattern=r"^[^\x00-\x1f\x7f]*$"),
    value: str = Depends(gmail_setting),
):
    if not value:
        return {"mailbox": None, "threads": [], "next": None}
    async with mailbox(value) as gmail:
        return await gmail.threads(page, q)


@router.get("/threads/{thread_id}")
async def thread(
    thread_id: str = Path(pattern="^" + THREAD_ID + "$"), value=Depends(gmail_setting)
):
    if not value:
        raise HTTPException(409, "Почта Gmail ещё не подключена.")
    async with mailbox(value) as gmail:
        return await gmail.thread(thread_id)


def onec_settings(db: Session = Depends(get_db)):
    values, _ = connection_settings(db)
    db.commit()
    return values


@router.get("/threads/{thread_id}/customer")
async def customer(
    thread_id: str = Path(pattern="^" + THREAD_ID + "$"),
    value: str = Depends(gmail_setting),
    email: str = Query(max_length=254, pattern=r"^[^@\s<>]+@[^@\s<>]+$"),
    values=Depends(onec_settings),
):
    if not value:
        raise HTTPException(409, "Почта Gmail ещё не подключена.")
    if not all(values):
        return {"status": "not_configured", "clients": []}
    async with mailbox(value) as gmail:
        summary = describe(await gmail.metadata(thread_id), await gmail.address())
        if not summary or not belongs_to(summary, email):
            raise HTTPException(404, "Адрес не найден в этой переписке.")
    try:
        clients = await OneCAdapter(*values).customers_by_email(email)
    except OneCError:
        raise HTTPException(502, "Не удалось получить данные клиента из 1С.") from None
    return {"status": "ok", "clients": clients}
