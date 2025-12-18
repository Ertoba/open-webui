import base64
import json
import logging
import os
import re
import sys
import unicodedata
import uuid
from dataclasses import dataclass
from functools import lru_cache
from io import BytesIO
from pathlib import Path

import aiofiles
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import FileResponse
from fastapi import UploadFile
from pydantic import BaseModel
from PIL import Image, ImageDraw, ImageFont, features

from open_webui.config import CACHE_DIR
from open_webui.env import FONTS_DIR, SRC_LOG_LEVELS
from open_webui.utils.auth import get_verified_user_or_none
from open_webui.routers.files import upload_file_handler

log = logging.getLogger(__name__)
log.setLevel(SRC_LOG_LEVELS.get("ROUTERS", logging.INFO))

PY_PHOTO_CACHE_DIR = CACHE_DIR / "py_photo" / "generations"
PY_PHOTO_CACHE_DIR.mkdir(parents=True, exist_ok=True)

router = APIRouter()


class PyPhotoGenerateForm(BaseModel):
    input: str
    size: int | None = 1024


class PyPhotoGenerateResponse(BaseModel):
    id: str
    media_type: str = "image/png"
    data_url: str
    view_url: str
    download_url: str
    file_id: str | None = None


def _py_photo_paths(*, image_id: str) -> tuple[Path, Path]:
    file_path = PY_PHOTO_CACHE_DIR / f"{image_id}.png"
    meta_path = PY_PHOTO_CACHE_DIR / f"{image_id}.json"
    return file_path, meta_path


def _font_bytes(mask) -> bytes:
    if hasattr(mask, "tobytes"):
        return mask.tobytes()
    try:
        return bytes(mask)
    except Exception:
        return bytes(bytearray(mask))


def _load_font(path: Path, size: int) -> ImageFont.FreeTypeFont:
    layout = getattr(ImageFont, "Layout", None)
    if layout is not None and hasattr(layout, "RAQM"):
        try:
            if features.check("raqm"):
                return ImageFont.truetype(str(path), size, layout_engine=layout.RAQM)
        except Exception:
            pass
    return ImageFont.truetype(str(path), size)


@lru_cache(maxsize=1024)
def _font_path_supports_sample(font_path: str, sample: str) -> bool:
    try:
        font = _load_font(Path(font_path), 32)
        baseline = font.getmask(chr(0x10FFFF))
        baseline_size = baseline.size
        baseline_bytes = _font_bytes(baseline)

        mask = font.getmask(sample)
        width, height = mask.size
        if width <= 0 or height <= 0:
            return False

        if mask.size == baseline_size and _font_bytes(mask) == baseline_bytes:
            return False
    except Exception:
        return False

    return True


def _sample_unique_chars(text: str, *, limit: int = 512) -> list[str]:
    chars: list[str] = []
    seen: set[str] = set()
    for ch in text:
        if ch.isspace() or ch in seen:
            continue
        seen.add(ch)
        chars.append(ch)
        if len(chars) >= limit:
            break
    return chars


@lru_cache(maxsize=1)
def _bundled_font_candidates() -> list[Path]:
    preferred = [
        FONTS_DIR / "NotoSans-Regular.ttf",
        FONTS_DIR / "NotoSans-Variable.ttf",
        FONTS_DIR / "NotoSansKR-Regular.ttf",
        FONTS_DIR / "NotoSansJP-Regular.ttf",
        FONTS_DIR / "NotoSansSC-Regular.ttf",
        FONTS_DIR / "Twemoji.ttf",
    ]

    extra: list[Path] = []
    try:
        if FONTS_DIR.is_dir():
            for ext in ("*.ttf", "*.otf", "*.ttc"):
                extra.extend(FONTS_DIR.glob(ext))
    except Exception:
        extra = []

    seen: set[Path] = set()
    candidates: list[Path] = []
    for path in [*preferred, *sorted(extra, key=lambda p: str(p).lower())]:
        if path in seen:
            continue
        seen.add(path)
        candidates.append(path)
    return candidates


@lru_cache(maxsize=1)
def _system_font_candidates() -> list[Path]:
    candidates: list[Path] = []

    if sys.platform.startswith("win"):
        fonts_dir = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
        candidates.extend(
            [
                fonts_dir / "sylfaen.ttf",  # includes Georgian on most Windows installs
                fonts_dir / "segoeui.ttf",
                fonts_dir / "arial.ttf",
                fonts_dir / "arialuni.ttf",
                fonts_dir / "seguiemj.ttf",
                fonts_dir / "seguisym.ttf",
            ]
        )
        return candidates

    if sys.platform == "darwin":
        candidates.extend(
            [
                Path("/System/Library/Fonts/Apple Color Emoji.ttc"),
                Path("/System/Library/Fonts/Supplemental/Arial Unicode.ttf"),
                Path("/Library/Fonts/Arial Unicode.ttf"),
                Path("/System/Library/Fonts/Supplemental/Arial Unicode MS.ttf"),
                Path("/Library/Fonts/Arial Unicode MS.ttf"),
                Path.home() / "Library/Fonts/Arial Unicode.ttf",
                Path.home() / "Library/Fonts/Arial Unicode MS.ttf",
            ]
        )
        return candidates

    # Linux / other Unix
    candidates.extend(
        [
            Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
            Path("/usr/share/fonts/truetype/dejavu/DejaVuSansCondensed.ttf"),
            Path("/usr/share/fonts/truetype/noto/NotoSansGeorgian-Regular.ttf"),
            Path("/usr/share/fonts/truetype/noto/NotoSansGeorgian.ttf"),
            Path("/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf"),
            Path("/usr/share/fonts/opentype/noto/NotoSansGeorgian-Regular.ttf"),
            Path("/usr/share/fonts/opentype/noto/NotoSansGeorgian.ttf"),
            Path("/usr/share/fonts/opentype/noto/NotoColorEmoji.ttf"),
            Path("/usr/share/fonts/truetype/freefont/FreeSans.ttf"),
            Path("/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"),
        ]
    )
    return candidates


def _user_font_candidates() -> list[Path]:
    value = (os.getenv("PY_PHOTO_FONT_PATH") or os.getenv("PY_PHOTO_FONT") or "").strip()
    if not value:
        return []

    path = Path(value).expanduser()
    try:
        if path.is_dir():
            fonts: list[Path] = []
            for ext in ("*.ttf", "*.otf", "*.ttc"):
                fonts.extend(path.glob(ext))
            return sorted(fonts, key=lambda p: str(p).lower())
        return [path]
    except Exception:
        return [path]


def _pick_font_for_sample(sample: str, *, candidates: list[Path]) -> Path | None:
    for candidate in candidates:
        try:
            if not candidate.is_file():
                continue
        except Exception:
            continue

        try:
            if _font_path_supports_sample(str(candidate), sample):
                return candidate
        except Exception:
            continue
    return None


def _resolve_font_path(*, text: str) -> Path:
    chars = _sample_unique_chars(text)
    if not chars:
        chars = ["a"]

    best: tuple[int, int, Path] | None = None
    candidates = [*_user_font_candidates(), *_bundled_font_candidates(), *_system_font_candidates()]

    for idx, candidate in enumerate(candidates):
        try:
            if not candidate.is_file():
                continue
        except Exception:
            continue

        try:
            font = ImageFont.truetype(str(candidate), 32)
        except Exception:
            continue

        try:
            baseline = font.getmask(chr(0x10FFFF))
            baseline_size = baseline.size
            baseline_bytes = _font_bytes(baseline)
        except Exception:
            continue

        missing = 0
        for ch in chars:
            try:
                mask = font.getmask(ch)
            except Exception:
                missing += 1
                continue

            if mask.size != baseline_size:
                continue

            if _font_bytes(mask) == baseline_bytes:
                missing += 1

        if best is None or missing < best[0]:
            best = (missing, idx, candidate)
            if missing == 0:
                break

    if best is None:
        raise RuntimeError("No usable font available")

    missing, _idx, path = best
    if missing:
        log.warning("PY Photo: selected font %s is missing %s glyph(s)", path.name, missing)

    return path


def _is_georgian_char(ch: str) -> bool:
    cp = ord(ch)
    return (0x10A0 <= cp <= 0x10FF) or (0x2D00 <= cp <= 0x2D2F) or (0x1C90 <= cp <= 0x1CBF)


def _is_regional_indicator(cp: int) -> bool:
    return 0x1F1E6 <= cp <= 0x1F1FF


def _is_skin_tone_modifier(cp: int) -> bool:
    return 0x1F3FB <= cp <= 0x1F3FF


def _is_variation_selector(cp: int) -> bool:
    return 0xFE00 <= cp <= 0xFE0F


def _is_tag_char(cp: int) -> bool:
    return 0xE0020 <= cp <= 0xE007E


def _is_emoji_codepoint(cp: int) -> bool:
    if cp in (0x200D, 0x20E3) or _is_variation_selector(cp) or _is_skin_tone_modifier(cp) or _is_tag_char(cp):
        return True
    if _is_regional_indicator(cp):
        return True
    return (
        0x1F000 <= cp <= 0x1FAFF
        or 0x2600 <= cp <= 0x26FF
        or 0x2700 <= cp <= 0x27BF
        or 0xFE00 <= cp <= 0xFE0F
    )


def _iter_grapheme_clusters(text: str):
    i = 0
    n = len(text)

    while i < n:
        ch = text[i]
        cp = ord(ch)

        if ch == "\n":
            yield ch
            i += 1
            continue

        cluster: list[str] = [ch]
        i += 1

        # Flags are pairs of regional indicator symbols.
        if _is_regional_indicator(cp) and i < n and _is_regional_indicator(ord(text[i])):
            cluster.append(text[i])
            i += 1
            yield "".join(cluster)
            continue

        # Keycap sequences: [0-9#*] + optional VS16 + U+20E3.
        if ch in "0123456789#*" and i < n:
            j = i
            if j < n and ord(text[j]) == 0xFE0F:
                cluster.append(text[j])
                j += 1
            if j < n and ord(text[j]) == 0x20E3:
                cluster.append(text[j])
                j += 1
                i = j
                yield "".join(cluster)
                continue

        # Common modifiers: variation selectors, combining marks, skin tones.
        while i < n:
            cp_next = ord(text[i])
            if _is_variation_selector(cp_next) or unicodedata.combining(text[i]):
                cluster.append(text[i])
                i += 1
                continue
            if _is_skin_tone_modifier(cp_next):
                cluster.append(text[i])
                i += 1
                continue
            break

        # Emoji tag sequences (e.g., subdivision flags) end with CANCEL TAG (U+E007F).
        while i < n:
            cp_next = ord(text[i])
            if _is_tag_char(cp_next) or cp_next == 0xE007F:
                cluster.append(text[i])
                i += 1
                continue
            break

        # ZWJ sequences: ... + ZWJ + ... (repeat)
        while i < n and ord(text[i]) == 0x200D:
            cluster.append(text[i])
            i += 1
            if i >= n:
                break
            cluster.append(text[i])
            i += 1
            while i < n:
                cp_next = ord(text[i])
                if _is_variation_selector(cp_next) or unicodedata.combining(text[i]) or _is_skin_tone_modifier(cp_next):
                    cluster.append(text[i])
                    i += 1
                    continue
                break

        yield "".join(cluster)


@lru_cache(maxsize=4096)
def _cluster_kind(cluster: str) -> str:
    if not cluster:
        return "base"

    if any(_is_emoji_codepoint(ord(ch)) for ch in cluster):
        return "emoji"

    for ch in cluster:
        cp = ord(ch)
        if (
            cp == 0x200D
            or _is_variation_selector(cp)
            or _is_skin_tone_modifier(cp)
            or _is_tag_char(cp)
            or cp == 0xE007F
            or unicodedata.combining(ch)
        ):
            continue
        if _is_georgian_char(ch):
            return "georgian"
        break

    return "base"


@dataclass(frozen=True)
class _FontPaths:
    base: Path
    georgian: Path
    emoji: Path


def _resolve_font_paths(*, text: str) -> _FontPaths:
    # Preserve existing behavior when emojis are not present (single-font rendering).
    if not any(_is_emoji_codepoint(ord(ch)) for ch in text):
        font_path = _resolve_font_path(text=text)
        return _FontPaths(base=font_path, georgian=font_path, emoji=font_path)

    candidates = [*_user_font_candidates(), *_system_font_candidates(), *_bundled_font_candidates()]

    base_text_parts: list[str] = []
    for cluster in _iter_grapheme_clusters(text):
        if _cluster_kind(cluster) == "base":
            base_text_parts.append(cluster)
    base_text = "".join(base_text_parts)
    if not base_text.strip():
        base_text = "a"

    emoji_candidates = sorted(
        candidates,
        key=lambda p: (
            0
            if any(k in p.name.lower() for k in ("emoji", "seguiemj", "apple color", "twemoji"))
            else 1,
            str(p).lower(),
        ),
    )
    georgian_candidates = sorted(
        candidates,
        key=lambda p: (
            0 if any(k in p.name.lower() for k in ("georgian", "sylfaen", "dejavu", "arialuni", "arial unicode")) else 1,
            str(p).lower(),
        ),
    )

    base_candidates = [*_user_font_candidates(), *_bundled_font_candidates(), *_system_font_candidates()]
    base_candidates = [p for p in base_candidates if "emoji" not in p.name.lower() and p.name.lower() != "twemoji.ttf"]

    try:
        base = _resolve_font_path(text=base_text)
    except Exception:
        base = _pick_font_for_sample("a", candidates=base_candidates) or _pick_font_for_sample("a", candidates=candidates)
        if base is None:
            raise RuntimeError("No usable font available")

    georgian = _pick_font_for_sample("\u10d0", candidates=georgian_candidates) or base
    emoji = _pick_font_for_sample("\U0001F600", candidates=emoji_candidates) or base

    return _FontPaths(base=base, georgian=georgian, emoji=emoji)


_COLOR_MAP: dict[str, tuple[int, int, int]] = {
    "white": (255, 255, 255),
    "black": (0, 0, 0),
    "gray": (200, 200, 200),
    "red": (255, 0, 0),
    "green": (34, 197, 94),
    "blue": (0, 102, 255),
    "yellow": (234, 179, 8),
    "orange": (249, 115, 22),
    "purple": (168, 85, 247),
    "cyan": (6, 182, 212),
}


@dataclass(frozen=True)
class _Token:
    text: str
    color: tuple[int, int, int]


def _apply_highlight_markup(text: str) -> str:
    out: list[str] = []
    i = 0

    while i < len(text):
        if not text.startswith("[!", i):
            out.append(text[i])
            i += 1
            continue

        end = text.find("!]", i + 2)
        if end == -1:
            out.append(text[i:])
            break

        inner = text[i + 2 : end]
        color = "blue"
        word = inner

        if "|" in inner:
            candidate_word, candidate_color = inner.rsplit("|", 1)
            candidate_color = candidate_color.strip().lower()
            if candidate_color in _COLOR_MAP:
                color = candidate_color
                word = candidate_word

        if not word:
            out.append(text[i : end + 2])
        else:
            out.append(f"[{color}]{word}[/{color}]")

        i = end + 2

    return "".join(out)


def _is_word_char(ch: str) -> bool:
    return ch.isalnum()


def _apply_chat_formatting_semantics(text: str) -> str:
    out: list[str] = []
    stack: list[str] = []
    in_code_fence = False
    in_inline_code = False
    i = 0
    at_line_start = True

    def tag_for(marker: str) -> str:
        return "blue" if marker in ("**", "H") else "red"

    def flush_stack():
        while stack:
            marker = stack.pop()
            out.append(f"[/{tag_for(marker)}]")

    while i < len(text):
        ch = text[i]

        if at_line_start:
            j = i
            while j < len(text) and text[j] in (" ", "\t"):
                j += 1

            if text.startswith("```", j):
                flush_stack()
                in_code_fence = not in_code_fence
                out.append(text[i:j])
                out.append("```")
                i = j + 3
                at_line_start = False
                continue

            if not in_code_fence and not in_inline_code:
                level = 0
                while j + level < len(text) and text[j + level] == "#":
                    level += 1

                if 1 <= level <= 6:
                    k = j + level
                    if k < len(text) and text[k].isspace() and text[k] != "\n":
                        flush_stack()
                        out.append(text[i:j])
                        out.append("[blue]")
                        stack.append("H")
                        while k < len(text) and text[k].isspace() and text[k] != "\n":
                            k += 1
                        i = k
                        at_line_start = False
                        continue

        if ch == "\n":
            flush_stack()
            out.append(ch)
            i += 1
            at_line_start = True
            continue

        at_line_start = False

        if ch == "\\" and i + 1 < len(text) and text[i + 1] in ("*", "_", "`", "#", "\\"):
            out.append(text[i + 1])
            i += 2
            continue

        if not in_code_fence and ch == "`":
            in_inline_code = not in_inline_code
            out.append(ch)
            i += 1
            continue

        if in_code_fence or in_inline_code:
            out.append(ch)
            i += 1
            continue

        if text.startswith("**", i):
            marker = "**"
            prev = text[i - 1] if i > 0 else ""
            nxt = text[i + 2] if i + 2 < len(text) else ""

            if stack and stack[-1] == marker and prev and not prev.isspace():
                out.append("[/blue]")
                stack.pop()
                i += 2
                continue

            if nxt and not nxt.isspace():
                out.append("[blue]")
                stack.append(marker)
                i += 2
                continue

            out.append("**")
            i += 2
            continue

        if ch in ("*", "_"):
            marker = ch
            prev = text[i - 1] if i > 0 else ""
            nxt = text[i + 1] if i + 1 < len(text) else ""

            if prev and nxt and _is_word_char(prev) and _is_word_char(nxt):
                out.append(ch)
                i += 1
                continue

            if stack and stack[-1] == marker and prev and not prev.isspace():
                out.append("[/red]")
                stack.pop()
                i += 1
                continue

            if nxt and not nxt.isspace():
                out.append("[red]")
                stack.append(marker)
                i += 1
                continue

            out.append(ch)
            i += 1
            continue

        out.append(ch)
        i += 1

    flush_stack()
    return "".join(out)


def _parse_color_markup(text: str) -> list[tuple[str, tuple[int, int, int]]]:
    default = _COLOR_MAP["white"]
    spans: list[tuple[str, tuple[int, int, int]]] = []
    color_stack: list[tuple[str, tuple[int, int, int]]] = [("__default__", default)]

    i = 0
    while i < len(text):
        if text[i] != "[":
            j = text.find("[", i)
            if j == -1:
                j = len(text)
            spans.append((text[i:j], color_stack[-1][1]))
            i = j
            continue

        end = text.find("]", i + 1)
        if end == -1:
            spans.append((text[i:], color_stack[-1][1]))
            break

        tag = text[i + 1 : end].strip()
        if not tag:
            spans.append(("[", color_stack[-1][1]))
            i += 1
            continue

        if tag.startswith("/"):
            tag_name = tag[1:].strip().lower()
            if tag_name in _COLOR_MAP and len(color_stack) > 1 and color_stack[-1][0] == tag_name:
                color_stack.pop()
            else:
                spans.append((text[i : end + 1], color_stack[-1][1]))
            i = end + 1
            continue

        tag_name = tag.lower()
        if tag_name in _COLOR_MAP:
            color_stack.append((tag_name, _COLOR_MAP[tag_name]))
            i = end + 1
            continue

        spans.append((text[i : end + 1], color_stack[-1][1]))
        i = end + 1

    merged: list[tuple[str, tuple[int, int, int]]] = []
    for part, color in spans:
        if not part:
            continue
        if merged and merged[-1][1] == color:
            merged[-1] = (merged[-1][0] + part, color)
        else:
            merged.append((part, color))
    return merged


_TOKEN_RE = re.compile(r"\n|[ \t\r\f\v]+|[^\s]+", re.UNICODE)


def _tokenize(spans: list[tuple[str, tuple[int, int, int]]]) -> list[_Token]:
    tokens: list[_Token] = []
    for text, color in spans:
        for piece in _TOKEN_RE.findall(text):
            tokens.append(_Token(text=piece, color=color))
    return tokens


class _FontManager:
    def __init__(self, paths: _FontPaths, size: int):
        loaded: dict[Path, ImageFont.FreeTypeFont] = {}

        def get(path: Path) -> ImageFont.FreeTypeFont:
            font = loaded.get(path)
            if font is None:
                font = _load_font(path, size)
                loaded[path] = font
            return font

        self.paths = paths
        self.base = get(paths.base)
        self.georgian = get(paths.georgian)
        self.emoji = get(paths.emoji)
        self._metrics: dict[str, tuple[int, int]] = {
            "base": self.base.getmetrics(),
            "georgian": self.georgian.getmetrics(),
            "emoji": self.emoji.getmetrics(),
        }

    def font_for_kind(self, kind: str) -> ImageFont.FreeTypeFont:
        if kind == "emoji":
            return self.emoji
        if kind == "georgian":
            return self.georgian
        return self.base

    def metrics_for_kind(self, kind: str) -> tuple[int, int]:
        return self._metrics.get(kind, self._metrics["base"])


def _iter_font_runs(text: str):
    current_kind: str | None = None
    current: list[str] = []

    for cluster in _iter_grapheme_clusters(text):
        kind = _cluster_kind(cluster)
        if current_kind is None:
            current_kind = kind
            current = [cluster]
            continue

        if kind != current_kind:
            yield "".join(current), current_kind
            current_kind = kind
            current = [cluster]
        else:
            current.append(cluster)

    if current_kind is not None and current:
        yield "".join(current), current_kind


def _line_metrics(line: list[_Token], *, fonts: _FontManager) -> tuple[int, int, int]:
    max_ascent = 0
    max_descent = 0

    for token in line:
        for run_text, kind in _iter_font_runs(token.text):
            if not run_text:
                continue
            ascent, descent = fonts.metrics_for_kind(kind)
            if ascent > max_ascent:
                max_ascent = ascent
            if descent > max_descent:
                max_descent = descent

    if max_ascent == 0 and max_descent == 0:
        max_ascent, max_descent = fonts.metrics_for_kind("base")

    return max_ascent, max_descent, int(max_ascent + max_descent)


def _total_height(line_metrics: list[tuple[int, int, int]], *, spacing: int) -> int:
    return sum(m[2] for m in line_metrics) + max(0, len(line_metrics) - 1) * spacing


def _append_ellipsis(
    draw: ImageDraw.ImageDraw,
    *,
    line: list[_Token],
    line_width: float,
    fonts: _FontManager,
    max_width: int,
    width_cache: dict[str, float],
) -> float:
    def measure(text: str) -> float:
        cached = width_cache.get(text)
        if cached is not None:
            return cached

        width = 0.0
        for run_text, kind in _iter_font_runs(text):
            if not run_text:
                continue
            font = fonts.font_for_kind(kind)
            try:
                width += float(draw.textlength(run_text, font=font))
            except Exception:
                width += float(draw.textlength(run_text, font=fonts.base))

        width_cache[text] = width
        return width

    ellipsis_text = "\u2026"
    ellipsis_width = measure(ellipsis_text)

    while line and line[-1].text.isspace():
        removed = line.pop()
        line_width -= measure(removed.text)

    while line and line_width + ellipsis_width > max_width:
        token = line[-1]
        clusters = list(_iter_grapheme_clusters(token.text))
        if len(clusters) <= 1:
            removed = line.pop()
            line_width -= measure(removed.text)
            continue

        removed_cluster = clusters.pop()
        new_text = "".join(clusters)
        line[-1] = _Token(text=new_text, color=token.color)
        line_width -= measure(removed_cluster)

        if not new_text:
            line.pop()

    if not line:
        line_width = 0.0

    line.append(_Token(text=ellipsis_text, color=_COLOR_MAP["white"]))
    return line_width + ellipsis_width


def _wrap_tokens(
    draw: ImageDraw.ImageDraw,
    *,
    tokens: list[_Token],
    fonts: _FontManager,
    max_width: int,
) -> tuple[list[list[_Token]], list[float]]:
    lines: list[list[_Token]] = []
    line_widths: list[float] = []

    current: list[_Token] = []
    current_width = 0.0
    width_cache: dict[str, float] = {}

    def measure(text: str) -> float:
        cached = width_cache.get(text)
        if cached is not None:
            return cached

        width = 0.0
        for run_text, kind in _iter_font_runs(text):
            if not run_text:
                continue
            font = fonts.font_for_kind(kind)
            try:
                width += float(draw.textlength(run_text, font=font))
            except Exception:
                width += float(draw.textlength(run_text, font=fonts.base))

        width_cache[text] = width
        return width

    def flush():
        nonlocal current, current_width
        if current:
            lines.append(current)
            line_widths.append(current_width)
        current = []
        current_width = 0.0

    for token in tokens:
        if token.text == "\n":
            flush()
            continue

        if not current and token.text.isspace():
            continue

        token_width = measure(token.text)

        if token_width > max_width:
            for cluster in _iter_grapheme_clusters(token.text):
                cluster_width = measure(cluster)
                if current and current_width + cluster_width > max_width:
                    flush()
                if cluster.isspace() and not current:
                    continue
                current.append(_Token(text=cluster, color=token.color))
                current_width += cluster_width
            continue

        if current and current_width + token_width > max_width:
            flush()
            if token.text.isspace():
                continue

        current.append(token)
        current_width += token_width

    flush()
    return lines, line_widths


def _max_lines_for_height(*, max_height: int, line_height: int, spacing: int) -> int:
    if line_height <= 0:
        return 1
    return max(1, (max_height + spacing) // (line_height + spacing))


def _truncate_lines_to_fit(
    draw: ImageDraw.ImageDraw,
    *,
    lines: list[list[_Token]],
    line_widths: list[float],
    font: ImageFont.FreeTypeFont,
    max_width: int,
    max_lines: int,
) -> tuple[list[list[_Token]], list[float]]:
    if len(lines) <= max_lines:
        return lines, line_widths

    clipped = lines[:max_lines]
    clipped_widths = line_widths[:max_lines]

    if not clipped:
        return [], []

    ellipsis = "…"
    ellipsis_width = float(draw.textlength(ellipsis, font=font))

    last = clipped[-1]
    while last and clipped_widths[-1] + ellipsis_width > max_width:
        removed = last.pop()
        clipped_widths[-1] -= float(draw.textlength(removed.text, font=font))

    if not last:
        last.append(_Token(text=ellipsis, color=_COLOR_MAP["white"]))
        clipped_widths[-1] = ellipsis_width
        return clipped, clipped_widths

    last.append(_Token(text=ellipsis, color=_COLOR_MAP["white"]))
    clipped_widths[-1] += ellipsis_width
    return clipped, clipped_widths


def _layout_text(
    *,
    text: str,
    size: int,
    padding: int,
    font_paths: _FontPaths,
) -> tuple[Image.Image, dict]:
    img = Image.new("RGB", (size, size), (0, 0, 0))
    draw = ImageDraw.Draw(img)

    text = _apply_highlight_markup(text)
    text = _apply_chat_formatting_semantics(text)
    spans = _parse_color_markup(text)
    tokens = _tokenize(spans)

    max_width = max(1, size - 2 * padding)
    max_height = max(1, size - 2 * padding)

    def fits(font_size: int):
        fonts = _FontManager(font_paths, font_size)
        lines, widths = _wrap_tokens(draw, tokens=tokens, fonts=fonts, max_width=max_width)
        base_ascent, base_descent = fonts.base.getmetrics()
        spacing = max(1, int((base_ascent + base_descent) * 0.2))
        line_metrics = [_line_metrics(line, fonts=fonts) for line in lines]
        total_height = _total_height(line_metrics, spacing=spacing)
        return total_height <= max_height, lines, widths, line_metrics, spacing, total_height

    lo, hi = 8, 120
    best = 8
    best_layout = None

    while lo <= hi:
        mid = (lo + hi) // 2
        ok, lines, widths, line_metrics, spacing, total_height = fits(mid)
        if ok:
            best = mid
            best_layout = (lines, widths, line_metrics, spacing, total_height)
            lo = mid + 1
        else:
            hi = mid - 1

    fonts = _FontManager(font_paths, best)
    if best_layout is None:
        lines, widths = _wrap_tokens(draw, tokens=tokens, fonts=fonts, max_width=max_width)
        base_ascent, base_descent = fonts.base.getmetrics()
        spacing = max(1, int((base_ascent + base_descent) * 0.2))
        line_metrics = [_line_metrics(line, fonts=fonts) for line in lines]
        total_height = _total_height(line_metrics, spacing=spacing)
    else:
        lines, widths, line_metrics, spacing, total_height = best_layout

    if total_height > max_height and lines:
        truncated = False
        while lines and total_height > max_height:
            lines.pop()
            widths.pop()
            line_metrics.pop()
            total_height = _total_height(line_metrics, spacing=spacing)
            truncated = True

        if truncated and lines:
            widths[-1] = _append_ellipsis(
                draw,
                line=lines[-1],
                line_width=widths[-1],
                fonts=fonts,
                max_width=max_width,
                width_cache={},
            )
            line_metrics[-1] = _line_metrics(lines[-1], fonts=fonts)
            total_height = _total_height(line_metrics, spacing=spacing)

    start_y = padding + max(0, (max_height - total_height) // 2)

    y = start_y
    for line, line_width, metrics in zip(lines, widths, line_metrics):
        max_ascent = metrics[0]
        x = padding + max(0, int((max_width - line_width) // 2))
        for token in line:
            for run_text, kind in _iter_font_runs(token.text):
                if not run_text:
                    continue
                font = fonts.font_for_kind(kind)
                ascent, _descent = fonts.metrics_for_kind(kind)
                draw_y = y + (max_ascent - ascent)
                draw.text((x, draw_y), run_text, font=font, fill=token.color)
                x += float(draw.textlength(run_text, font=font))
        y += metrics[2] + spacing

    meta = {
        "size": size,
        "padding": padding,
        "font_size": best,
        "font": font_paths.base.name,
        "fonts": {
            "base": font_paths.base.name,
            "georgian": font_paths.georgian.name,
            "emoji": font_paths.emoji.name,
        },
    }
    return img, meta


@router.post("/generate", response_model=PyPhotoGenerateResponse)
async def generate_py_photo(
    request: Request,
    form_data: PyPhotoGenerateForm,
    user=Depends(get_verified_user_or_none),
):
    text = str(form_data.input or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="Input text is required")

    if len(text) > 10_000:
        raise HTTPException(status_code=400, detail="Input text is too long")

    try:
        size = int(form_data.size or 1024)
    except Exception:
        size = 1024
    size = max(256, min(2048, size))
    padding = max(24, size // 12)

    try:
        font_paths = _resolve_font_paths(text=text)
        img, meta = _layout_text(text=text, size=size, padding=padding, font_paths=font_paths)
    except Exception as e:
        log.exception(e)
        raise HTTPException(status_code=500, detail="Failed to render image")

    image_id = uuid.uuid4().hex
    file_path, meta_path = _py_photo_paths(image_id=image_id)

    try:
        buf = BytesIO()
        img.save(buf, format="PNG", optimize=True)
        png_bytes = buf.getvalue()

        async with aiofiles.open(file_path, "wb") as f:
            await f.write(png_bytes)
        async with aiofiles.open(meta_path, "w", encoding="utf-8") as f:
            await f.write(
                json.dumps(
                    {"meta": meta},
                    ensure_ascii=False,
                )
            )
    except Exception as e:
        log.exception(e)
        raise HTTPException(status_code=500, detail="Failed to store image")

    data_url = f"data:image/png;base64,{base64.b64encode(png_bytes).decode('utf-8')}"
    view_url = f"/api/v1/py_photo/{image_id}"
    download_url = f"/api/v1/py_photo/{image_id}/download"

    file_id: str | None = None
    if user is not None:
        try:
            upload = UploadFile(
                file=BytesIO(png_bytes),
                filename=f"py-photo-{image_id}.png",
                headers={"content-type": "image/png"},
            )
            file_item = upload_file_handler(
                request,
                file=upload,
                metadata={
                    "generated": True,
                    "source": "py_photo",
                    "meta": meta,
                },
                process=False,
                user=user,
            )
            file_id = getattr(file_item, "id", None)
        except Exception:
            file_id = None

    return {
        "id": image_id,
        "data_url": data_url,
        "view_url": view_url,
        "download_url": download_url,
        "file_id": file_id,
    }


def _find_cached_png(image_id: str) -> Path | None:
    image_id = str(image_id or "").strip()
    if not image_id:
        return None
    file_path, _meta_path = _py_photo_paths(image_id=image_id)
    return file_path if file_path.is_file() else None


@router.get("/{image_id}")
async def get_py_photo(image_id: str):
    file_path = _find_cached_png(image_id=image_id)
    if not file_path:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Image not found")
    return FileResponse(file_path, media_type="image/png")


@router.get("/{image_id}/download")
async def download_py_photo(image_id: str):
    file_path = _find_cached_png(image_id=image_id)
    if not file_path:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Image not found")
    return FileResponse(
        file_path,
        media_type="image/png",
        filename=f"py-photo-{image_id}.png",
    )
