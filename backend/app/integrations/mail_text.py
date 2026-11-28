"""Extract the author's contribution when displaying a received email."""

import re
from html.parser import HTMLParser

_QUOTED = re.compile(r"^[\s\ufeff\u200b]*>")
_REPLY_END = re.compile(
    r"(?:\bwrote|\b(?:на)?писал[аи]?|\bпишет|\bécrit|\bescribió|\bschrieb"
    r"|\bha scritto|\bnapisał[a]?|\bpisze|작성|<[^<>\s]+@[^<>\s]+>)\s*:\s*$",
    re.I,
)
_REPLY_START = re.compile(
    r"^(?:on|le|am|el|il|w dniu|dnia)\s|^\d{1,4}[./\-년 ]\d|^(?:пн|вт|ср|чт|пт|сб|вс),",
    re.I,
)
# Gmail API previews can flatten the quote and attribution onto one physical line.
_FLAT_REPLY = re.compile(
    r"(?<!\S)(?:on|le|el|am|il)\s+(?=[^\n]{0,240}[\d@])"
    r"[^\n]{1,260}?\b(?:wrote|écrit|escribió|schrieb|scritto)\s*:\s*>",
    re.I,
)
_ORIGINAL = re.compile(r"^[-–]{2,}\s*(?:original message|исходное сообщение)\s*[-–]{2,}$", re.I)
_FROM = re.compile(r"^(?:from|от|de|von)\s*:", re.I)
_SENT = re.compile(r"^(?:sent|date|отправлено|дата|envoyé|gesendet|enviado)\s*:", re.I)
_TO = re.compile(r"^(?:to|кому|à|an|para)\s*:", re.I)
_SUBJECT = re.compile(r"^(?:subject|тема|objet|betreff|asunto)\s*:", re.I)


def remove_attribution(lines):
    """A wrapped attribution immediately before a quoted block belongs to that block."""
    while lines and not lines[-1].strip():
        lines.pop()
    if not lines or not _REPLY_END.search(lines[-1]):
        return
    start = len(lines) - 1
    # Mail clients wrap a long date/name/address across several lines.
    for i in range(start, max(-1, start - 4), -1):
        line = lines[i].strip()
        if not line:
            break
        if _REPLY_START.match(line):
            start = i
            break
    del lines[start:]


def outlook_history(lines, i):
    # A single "From:" (e.g. a shipping form) is not an Outlook reply header.
    headers = [line.strip() for line in lines[i + 1 : i + 15]]
    return any(_SENT.match(line) for line in headers) and any(
        _SUBJECT.match(line) or _TO.match(line) for line in headers
    )


def authored_text(body):
    lines = body.splitlines()
    kept: list[str] = []
    for i, line in enumerate(lines):
        if _QUOTED.match(line):
            remove_attribution(kept)
            # Separate unquoted answers written between (or below) quoted paragraphs.
            if kept:
                kept.append("")
            continue
        stripped = line.strip()
        if _ORIGINAL.fullmatch(stripped):
            following = next((lines[j] for j in range(i + 1, len(lines)) if lines[j].strip()), "")
            if _QUOTED.match(following):
                continue
            break
        if _FROM.match(stripped) and outlook_history(lines, i):
            while kept and (not kept[-1].strip() or re.fullmatch(r"\s*[_-]{3,}\s*", kept[-1])):
                kept.pop()
            break
        kept.append(line)
    return re.sub(r"\n[ \t]*\n(?:[ \t]*\n)+", "\n\n", "\n".join(kept)).strip()


class MailHTML(HTMLParser):
    blocks = {"p", "div", "li", "tr", "pre"}
    quote_classes = {"gmail_quote", "gmail_quote_container", "yahoo_quoted", "protonmail_quote"}
    attribution_classes = {"gmail_attr", "moz-cite-prefix"}

    def __init__(self):
        super().__init__()
        self.text: list[str] = []
        self.hidden_tag = ""
        self.hidden_depth = 0

    def handle_starttag(self, tag, attrs):
        if self.hidden_tag:
            if tag == self.hidden_tag:
                self.hidden_depth += 1
            return
        attributes = dict(attrs)
        classes = set((attributes.get("class") or "").lower().split())
        container = tag in {"div", "section", "span", "p", "table"}
        quoted = tag == "blockquote" or (container and bool(classes & self.quote_classes))
        if (
            tag in {"head", "script", "style"}
            or quoted
            or (container and classes & self.attribution_classes)
        ):
            self.hidden_tag, self.hidden_depth = tag, 1
            if quoted:
                self.text.append("\n>\n")
        elif tag == "br" or tag in self.blocks:
            self.text.append("\n")

    def handle_endtag(self, tag):
        if self.hidden_tag:
            if tag == self.hidden_tag:
                self.hidden_depth -= 1
                if not self.hidden_depth:
                    self.hidden_tag = ""
        elif tag in self.blocks:
            self.text.append("\n")

    def handle_data(self, data):
        if not self.hidden_tag:
            self.text.append(data)


def visible_mail_text(body, *, html=False):
    if html:
        parser = MailHTML()
        parser.feed(body)
        parser.close()
        body = "".join(parser.text)
    return authored_text(body)


def gmail_snippet(body):
    # The API has no line boundaries in some snippets. Only cut at a clear reply marker.
    text = authored_text(body)
    boundary = _FLAT_REPLY.search(text)
    return text[: boundary.start()].rstrip() if boundary else text
