from __future__ import annotations

import re

from telethon.tl.custom.message import Message

from src.events.receiver import (
    TelegramAttachmentPayload,
    TelegramMessagePayload,
    TelegramMessageReceivedEvent,
    TelegramReplySenderPayload,
    TelegramSenderPayload,
    TelegramTransportPayload,
)
from src.utils.time import utc_now


MENTION_RE = re.compile(r"@([A-Za-z0-9_]+)")


def normalize_content(text: str | None, media_type: str | None) -> tuple[str, str | None]:
    raw_text = text or ""
    cleaned = raw_text.strip()
    if cleaned:
        return cleaned, raw_text
    if media_type == "sticker":
        return "[sticker]", raw_text
    if media_type:
        return f"[{media_type}]", raw_text
    return "[no text content]", raw_text


def extract_mentions(text: str) -> list[str]:
    lowered = text.lower()
    mentions = [match.group(1).lower() for match in MENTION_RE.finditer(text)]
    if "amber" in lowered and "amber" not in mentions:
        mentions.append("amber")
    return mentions


def infer_media_type(message: Message) -> str | None:
    if message.sticker:
        return "sticker"
    if message.media:
        return getattr(message.file, "mime_type", None) or "media"
    return None


def build_attachment_payload(message: Message, media_type: str | None) -> TelegramAttachmentPayload:
    file_name = getattr(message.file, "name", None) if message.file is not None else None
    mime_type = getattr(message.file, "mime_type", None) if message.file is not None else None
    file_id = str(getattr(message.document, "id", None)) if getattr(message, "document", None) is not None else None
    sticker_set_id = None
    if message.sticker and getattr(message.media, "document", None) is not None:
        sticker_set_id = str(getattr(message.media.document, "id", None))
    return TelegramAttachmentPayload(
        media_type=media_type,
        file_id=file_id,
        file_name=file_name,
        mime_type=mime_type,
        sticker_set_id=sticker_set_id,
    )


def build_reply_sender_payload(reply_message: Message | None) -> TelegramReplySenderPayload:
    if reply_message is None:
        return TelegramReplySenderPayload()
    sender = getattr(reply_message, "sender", None)
    return TelegramReplySenderPayload(
        id=str(getattr(reply_message, "sender_id", "")) if getattr(reply_message, "sender_id", None) else None,
        name=getattr(sender, "first_name", None) or getattr(reply_message, "post_author", None),
    )


def build_sender_payload(sender: object | None, message: Message) -> TelegramSenderPayload:
    return TelegramSenderPayload(
        id=str(getattr(sender, "id", getattr(message, "sender_id", "unknown"))),
        name=getattr(sender, "first_name", None)
        or getattr(sender, "title", None)
        or getattr(message, "post_author", None)
        or "unknown",
        username=getattr(sender, "username", None),
        is_self=bool(message.out),
    )


def build_transport_payload(message: Message) -> TelegramTransportPayload:
    chat_id = int(message.chat_id) if message.chat_id is not None else 0
    return TelegramTransportPayload(
        peer_id=chat_id,
        raw_chat_id=chat_id,
        raw_message_id=int(message.id),
        thread_id=getattr(message, "reply_to_top_id", None),
    )


async def normalize_telegram_message(message: Message) -> TelegramMessageReceivedEvent:
    sender = await message.get_sender()
    reply_message = await message.get_reply_message() if message.reply_to_msg_id else None
    media_type = infer_media_type(message)
    content, raw_text = normalize_content(message.message, media_type)

    # Normalize reply metadata separately so context can reason about threaded replies.
    reply_to_content = None
    reply_to_raw_text = None
    if reply_message is not None:
        reply_media_type = infer_media_type(reply_message)
        reply_to_content, reply_to_raw_text = normalize_content(reply_message.message, reply_media_type)

    # Preserve raw Telegram identifiers alongside normalized content for downstream transport operations.
    payload = TelegramMessagePayload(
        message_id=int(message.id),
        chat_id=int(message.chat_id) if message.chat_id is not None else 0,
        thread_id=getattr(message, "reply_to_top_id", None),
        sender=build_sender_payload(sender, message),
        timestamp=message.date or utc_now(),
        content=content,
        raw_text=raw_text,
        reply_to_message_id=message.reply_to_msg_id,
        reply_to_sender=build_reply_sender_payload(reply_message),
        reply_to_content=reply_to_content,
        reply_to_raw_text=reply_to_raw_text,
        mentions=extract_mentions(content),
        attachment=build_attachment_payload(message, media_type),
        transport=build_transport_payload(message),
        edited_at=getattr(message, "edit_date", None),
        reaction_count=len(getattr(getattr(message, "reactions", None), "results", []) or []),
    )
    return TelegramMessageReceivedEvent(chat_id=payload.chat_id, payload=payload)
