import re
from pathlib import Path

DOCKERFILE = Path(__file__).resolve().parents[1] / "Dockerfile"
DIGEST = re.compile(r"@sha256:[0-9a-f]{64}$")
STAGES = {"builder", "runtime"}


def _image_references() -> list[str]:
    references: list[str] = []
    for line in DOCKERFILE.read_text(encoding="utf-8").splitlines():
        words = line.split()
        if line.startswith("FROM "):
            references.append(words[1])
        elif line.startswith("COPY --from="):
            source = words[1].removeprefix("--from=")
            if source not in STAGES:
                references.append(source)
    return references


def test_every_external_image_is_pinned_by_digest() -> None:
    references = _image_references()
    assert len(references) == 3  # builder base, uv binary, runtime base
    assert [ref for ref in references if not DIGEST.search(ref)] == []


def test_the_pinned_images_keep_their_readable_tags() -> None:
    references = _image_references()
    assert sorted(ref.split("@")[0] for ref in references) == [
        "ghcr.io/astral-sh/uv:0.11.7",
        "python:3.13-slim",
        "python:3.13-slim",
    ]
