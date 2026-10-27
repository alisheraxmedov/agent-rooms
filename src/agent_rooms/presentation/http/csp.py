import base64
import hashlib
import re

_INLINE_BLOCK = re.compile(r"<(script|style)(?:\s[^>]*)?>(.*?)</\1\s*>", re.DOTALL | re.IGNORECASE)


def _sources(blocks: list[str]) -> str:
    if not blocks:
        return "'none'"
    return " ".join(
        "'sha256-" + base64.b64encode(hashlib.sha256(block.encode("utf-8")).digest()).decode() + "'"
        for block in blocks
    )


def content_security_policy(html: str) -> str:
    """CSP that allows exactly the inline <script> and <style> blocks present in `html`."""
    blocks: dict[str, list[str]] = {"script": [], "style": []}
    for tag, body in _INLINE_BLOCK.findall(html):
        blocks[tag.lower()].append(body)
    scripts, styles = _sources(blocks["script"]), _sources(blocks["style"])
    return (
        f"default-src 'none'; script-src {scripts}; style-src {styles}; "
        "connect-src 'self'; img-src 'self' data:; base-uri 'none'; form-action 'none'; "
        "frame-ancestors 'none'"
    )
