from datetime import UTC, datetime
from typing import Any

from agent_rooms.application.dto import MemberView, RoomSummary, Wake
from agent_rooms.domain.models import Message


def iso(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat(timespec="milliseconds").removesuffix("+00:00") + "Z"


def message_json(message: Message) -> dict[str, Any]:
    return {
        "id": message.id,
        "room": message.room,
        "sender": message.sender,
        "alias": message.alias,
        "kind": message.kind.value,
        "text": message.text,
        "reply_to": message.reply_to,
        "created_at": iso(message.created_at),
    }


def member_json(member: MemberView) -> dict[str, Any]:
    return {
        "agent": member.agent,
        "alias": member.alias,
        "role": member.role.value,
        "listening": member.listening,
    }


def summary_json(summary: RoomSummary) -> dict[str, Any]:
    return {
        "slug": summary.slug,
        "topic": summary.topic,
        "status": summary.status.value,
        "max_messages": summary.max_messages,
        "message_count": summary.message_count,
        "members": [member_json(member) for member in summary.members],
        "created_at": iso(summary.created_at),
    }


def wake_json(wake: Wake) -> dict[str, Any]:
    return {
        "room": wake.room,
        "alias": wake.alias,
        "messages": [message_json(message) for message in wake.messages],
        "prompt": wake.prompt,
    }
