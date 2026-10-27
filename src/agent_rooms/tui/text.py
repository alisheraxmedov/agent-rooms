import unicodedata
from datetime import datetime, tzinfo

_DROPPED_CATEGORIES = frozenset({"Cc", "Cf", "Cs", "Co", "Cn", "Zl", "Zp"})


def clean(text: str) -> str:
    """Make untrusted text safe to draw: no terminal control sequences, no invisible or bidi characters."""
    text = text.replace("\r\n", "\n").replace("\t", "    ")
    return "".join(ch for ch in text if ch == "\n" or unicodedata.category(ch) not in _DROPPED_CATEGORIES)


def local_time(iso: str, now: datetime | None = None, tz: tzinfo | None = None) -> str:
    try:
        moment = datetime.fromisoformat(iso).astimezone(tz)
    except ValueError:
        return ""
    today = (now or datetime.now(tz)).astimezone(tz).date()
    return moment.strftime("%H:%M" if moment.date() == today else "%b %d %H:%M")
