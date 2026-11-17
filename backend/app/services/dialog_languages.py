"""Language choices shared by the editor, AI and cached timeline translations."""

import re
from typing import Annotated

from fastapi import HTTPException
from pydantic import Field

Language = Annotated[str, Field(pattern=r"^[a-z]{2,3}(?:-[A-Za-z]{2,4})?$")]


def manager_language(user):
    return getattr(user, "manager_language", None) or "en"


def client_language(customer, fallback=""):
    if not customer:
        return fallback
    manual = customer.translation_client_language
    return (customer.language or fallback) if manual in ("", "auto") else manual


def incoming_enabled(customer, language):
    if not customer:
        return False
    if customer.incoming_translation_auto:
        return bool(client_language(customer) and client_language(customer) != language)
    return customer.translate_incoming


def outgoing_language(customer, language, requested=None):
    if requested == "original":
        return language
    if requested:
        return requested
    if customer and customer.translate_outgoing:
        target = client_language(customer)
        if not target:
            raise HTTPException(409, "Язык клиента ещё не определён. Выберите язык отправки.")
        return target
    return language


def neutral_greeting(text, customer_name, declared_name=""):
    """Drop an unverified name in a greeting; never borrow names from history/examples."""
    allowed = re.sub(r"\s+", " ", customer_name or "").strip().casefold()
    candidate = declared_name.strip()
    match = re.match(
        r"^\s*(?:hello|hi|dear|hola|bonjour|здравствуйте|привет|уважаемый|مرحب[اًا]*|أهلا)\s*[,،]?\s+([^\n!！,،:;?]{1,60})[!！,،:]",
        text,
        re.I,
    )
    if not candidate and match:
        found = match.group(1).strip()
        if len(found.split()) <= 3 and (
            found[:1].isupper() or re.search(r"[\u0600-\u06ff]", found)
        ):
            candidate = found
    valid = bool(candidate and allowed and candidate.casefold() in (allowed, allowed.split()[0]))
    generic = {"customer", "client", "sir", "madam", "клиент", "друг", "amigo", "cliente"}
    if candidate.casefold() in generic:
        return text, False
    if candidate and not valid and candidate in text[:100]:
        # Keep the rest of the answer unchanged. Name removal is literal, never a regex from input.
        text = text.replace(candidate, "", 1)
        text = re.sub(r"[ \t]+([!！,،:])", r"\1", text)
        text = re.sub(r"^([^\n]{1,30}?)[ \t]*[,،][ \t]*[,،]", r"\1,", text)
        return text, True
    return text, False
