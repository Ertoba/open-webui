import json
import logging
import mimetypes
import time
import uuid
import base64
import asyncio
from pathlib import Path

import aiofiles
import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import FileResponse
from pydantic import BaseModel

from open_webui.config import CACHE_DIR
from open_webui.env import (
    AIOHTTP_CLIENT_TIMEOUT,
    REDIS_KEY_PREFIX,
    SRC_LOG_LEVELS,
)
from open_webui.models.files import FileForm, Files
from open_webui.storage.provider import Storage
from open_webui.utils.auth import get_admin_user, get_verified_user_or_none
from open_webui.utils.domain_credits import commit_generation, preflight_generation

log = logging.getLogger(__name__)
log.setLevel(SRC_LOG_LEVELS.get("ROUTERS", logging.INFO))

MUSIC_CACHE_DIR = CACHE_DIR / "music" / "generations"
MUSIC_CACHE_DIR.mkdir(parents=True, exist_ok=True)

ELEVENLABS_BASE_URL = "https://api.elevenlabs.io"

router = APIRouter()

_REQUEST_CACHE: dict[str, tuple[float, dict]] = {}
_REQUEST_CACHE_LOCK = asyncio.Lock()

MUSIC_REQUEST_TTL_SECONDS = 15 * 60


def _get_or_set_anon_id(request: Request, response: Response) -> str:
    anon_id = (
        request.headers.get("X-OWUI-ANON-ID") or request.cookies.get("owui_anon_id") or ""
    ).strip()
    if anon_id:
        return anon_id

    anon_id = uuid.uuid4().hex
    response.set_cookie(
        key="owui_anon_id",
        value=anon_id,
        max_age=60 * 60 * 24 * 365,
        samesite="lax",
    )
    return anon_id


def _enabled(request: Request) -> bool:
    return bool(getattr(request.app.state.config, "ELEVENLABS_MUSIC_ENABLED", True) or False)


def _api_key(request: Request) -> str:
    return str(getattr(request.app.state.config, "ELEVENLABS_API_KEY", "") or "").strip()


def _default_format(request: Request) -> str:
    return str(
        getattr(request.app.state.config, "ELEVENLABS_MUSIC_DEFAULT_FORMAT", "mp3_44100_128")
        or "mp3_44100_128"
    ).strip()


def _default_model_id(request: Request) -> str:
    return str(getattr(request.app.state.config, "ELEVENLABS_MUSIC_MODEL_ID", "music_v1") or "music_v1").strip()


def _mode(request: Request) -> str:
    return str(getattr(request.app.state.config, "ELEVENLABS_MUSIC_MODE", "detailed") or "detailed").strip().lower()


def _default_length_ms(request: Request) -> int:
    try:
        return int(getattr(request.app.state.config, "ELEVENLABS_MUSIC_DEFAULT_LENGTH_MS", 30000) or 30000)
    except Exception:
        return 30000


def _max_length_ms(request: Request) -> int:
    try:
        return int(getattr(request.app.state.config, "ELEVENLABS_MUSIC_MAX_LENGTH_MS", 120000) or 120000)
    except Exception:
        return 120000


def _sanitize_ext(ext: str | None) -> str:
    ext = (ext or "").strip().lower()
    if ext.startswith("."):
        ext = ext[1:]
    return ext or "mp3"


def _ext_from_output_format(output_format: str | None) -> str:
    fmt = (output_format or "").strip().lower()
    if not fmt:
        return "mp3"
    head = fmt.split("_", 1)[0].strip()
    if head in {"mp3", "wav", "ogg", "m4a", "flac"}:
        return head
    return "mp3"


def _guess_media_type_for_ext(ext: str) -> str:
    ext = _sanitize_ext(ext)
    if ext == "mp3":
        return "audio/mpeg"
    if ext == "wav":
        return "audio/wav"
    if ext == "ogg":
        return "audio/ogg"
    if ext == "m4a":
        return "audio/mp4"
    if ext == "flac":
        return "audio/flac"
    return mimetypes.types_map.get(f".{ext}") or "application/octet-stream"


def _music_paths(*, audio_id: str, ext: str) -> tuple[Path, Path]:
    ext = _sanitize_ext(ext)
    file_path = MUSIC_CACHE_DIR / f"{audio_id}.{ext}"
    meta_path = MUSIC_CACHE_DIR / f"{audio_id}.json"
    return file_path, meta_path


def _find_cached_music_file(audio_id: str) -> tuple[Path, str, str] | None:
    audio_id = str(audio_id or "").strip()
    if not audio_id:
        return None

    for p in MUSIC_CACHE_DIR.glob(f"{audio_id}.*"):
        if p.name.endswith(".json"):
            continue
        ext = _sanitize_ext(p.suffix)
        media_type = _guess_media_type_for_ext(ext)
        return p, ext, media_type
    return None


async def _read_meta(audio_id: str) -> dict | None:
    _file_path, meta_path = _music_paths(audio_id=audio_id, ext="mp3")
    if not meta_path.is_file():
        return None
    try:
        async with aiofiles.open(meta_path, "r", encoding="utf-8") as f:
            return json.loads(await f.read())
    except Exception:
        return None


def _persist_music_file_to_user_files(
    request: Request,
    *,
    user,
    local_file_path: Path,
    filename: str,
    content_type: str,
    metadata: dict,
) -> str | None:
    try:
        with open(local_file_path, "rb") as f:
            contents, storage_path = Storage.upload_file(
                f,
                f"{uuid.uuid4()}_{filename}",
                {
                    "OpenWebUI-User-Email": user.email,
                    "OpenWebUI-User-Id": user.id,
                    "OpenWebUI-User-Name": user.name,
                },
            )
        file_id = str(uuid.uuid4())
        Files.insert_new_file(
            user.id,
            FileForm(
                **{
                    "id": file_id,
                    "filename": filename,
                    "path": storage_path,
                    "data": {},
                    "meta": {
                        "name": filename,
                        "content_type": content_type,
                        "size": len(contents),
                        "data": metadata,
                    },
                }
            ),
        )
        return file_id
    except Exception:
        return None


class ElevenLabsMusicConfig(BaseModel):
    ELEVENLABS_MUSIC_ENABLED: bool
    ELEVENLABS_API_KEY: str
    ELEVENLABS_MUSIC_MODE: str
    ELEVENLABS_MUSIC_DEFAULT_FORMAT: str
    ELEVENLABS_MUSIC_MODEL_ID: str
    ELEVENLABS_MUSIC_DEFAULT_LENGTH_MS: int
    ELEVENLABS_MUSIC_MAX_LENGTH_MS: int


@router.get("/config", response_model=ElevenLabsMusicConfig)
async def get_music_config(request: Request, user=Depends(get_admin_user)):
    return {
        "ELEVENLABS_MUSIC_ENABLED": _enabled(request),
        "ELEVENLABS_API_KEY": _api_key(request),
        "ELEVENLABS_MUSIC_MODE": _mode(request) or "detailed",
        "ELEVENLABS_MUSIC_DEFAULT_FORMAT": _default_format(request),
        "ELEVENLABS_MUSIC_MODEL_ID": _default_model_id(request),
        "ELEVENLABS_MUSIC_DEFAULT_LENGTH_MS": _default_length_ms(request),
        "ELEVENLABS_MUSIC_MAX_LENGTH_MS": _max_length_ms(request),
    }


@router.post("/config/update", response_model=ElevenLabsMusicConfig)
async def update_music_config(
    request: Request, form_data: ElevenLabsMusicConfig, user=Depends(get_admin_user)
):
    request.app.state.config.ELEVENLABS_MUSIC_ENABLED = bool(form_data.ELEVENLABS_MUSIC_ENABLED)
    request.app.state.config.ELEVENLABS_API_KEY = str(form_data.ELEVENLABS_API_KEY or "").strip()
    # Streaming is explicitly forbidden: force detailed mode always.
    request.app.state.config.ELEVENLABS_MUSIC_MODE = "detailed"
    request.app.state.config.ELEVENLABS_MUSIC_DEFAULT_FORMAT = str(form_data.ELEVENLABS_MUSIC_DEFAULT_FORMAT or "mp3_44100_128").strip()
    request.app.state.config.ELEVENLABS_MUSIC_MODEL_ID = str(form_data.ELEVENLABS_MUSIC_MODEL_ID or "music_v1").strip()
    try:
        request.app.state.config.ELEVENLABS_MUSIC_DEFAULT_LENGTH_MS = int(form_data.ELEVENLABS_MUSIC_DEFAULT_LENGTH_MS)
    except Exception:
        request.app.state.config.ELEVENLABS_MUSIC_DEFAULT_LENGTH_MS = _default_length_ms(request)
    try:
        request.app.state.config.ELEVENLABS_MUSIC_MAX_LENGTH_MS = int(form_data.ELEVENLABS_MUSIC_MAX_LENGTH_MS)
    except Exception:
        request.app.state.config.ELEVENLABS_MUSIC_MAX_LENGTH_MS = _max_length_ms(request)

    return await get_music_config(request, user=user)


class MusicStatus(BaseModel):
    available: bool
    enabled: bool
    configured: bool
    redis_available: bool
    credits_required: bool
    default_model: str


@router.get("/status", response_model=MusicStatus)
async def music_status(
    request: Request,
    response: Response,
    user=Depends(get_verified_user_or_none),
):
    if user is None:
        _get_or_set_anon_id(request, response)

    enabled = _enabled(request)
    configured = bool(_api_key(request))

    is_admin = getattr(user, "role", None) == "admin"
    redis_available = getattr(request.app.state, "redis", None) is not None
    credits_required = not is_admin

    available = bool(enabled and configured and (not credits_required or redis_available))
    return {
        "available": available,
        "enabled": enabled,
        "configured": configured,
        "redis_available": redis_available,
        "credits_required": credits_required,
        "default_model": _default_model_id(request),
    }


class MusicStreamForm(BaseModel):
    request_id: str
    prompt: str | None = None
    composition_plan: str | None = None
    music_length_ms: int | None = None
    output_format: str | None = None
    force_instrumental: bool | None = False
    model_id: str | None = None
    chat_id: str
    message_id: str


@router.get("/{audio_id}/meta")
async def get_music_meta(audio_id: str):
    audio_id = str(audio_id or "").strip()
    if not audio_id:
        raise HTTPException(status_code=404, detail="Not found")

    meta = await _read_meta(audio_id)
    if not meta:
        raise HTTPException(status_code=404, detail="Not found")

    return {
        **meta,
        "id": audio_id,
        "play_url": f"/api/v1/music/{audio_id}",
        "download_url": f"/api/v1/music/{audio_id}/download",
    }
def _music_request_cache_key(request_id: str) -> str:
    return f"{REDIS_KEY_PREFIX}:music:request:{request_id}"


def _require_uuid(value: str, *, field: str) -> str:
    value = (value or "").strip()
    if not value:
        raise HTTPException(status_code=422, detail=f"{field} is required")
    try:
        uuid.UUID(value)
    except Exception:
        raise HTTPException(status_code=422, detail=f"{field} must be a UUID")
    return value


async def _cache_get(request: Request, request_id: str) -> dict | None:
    request_id = (request_id or "").strip()
    if not request_id:
        return None

    redis = getattr(request.app.state, "redis", None)
    if redis is not None:
        try:
            raw = await redis.get(_music_request_cache_key(request_id))
            if raw:
                if isinstance(raw, (bytes, bytearray)):
                    raw = raw.decode("utf-8", errors="replace")
                parsed = json.loads(raw)
                if isinstance(parsed, dict):
                    return parsed
        except Exception:
            pass

    now = time.time()
    async with _REQUEST_CACHE_LOCK:
        cached = _REQUEST_CACHE.get(request_id)
        if not cached:
            return None
        expires_at, value = cached
        if expires_at <= now:
            _REQUEST_CACHE.pop(request_id, None)
            return None
        return value


async def _cache_set(request: Request, request_id: str, value: dict, *, ttl_seconds: int) -> None:
    redis = getattr(request.app.state, "redis", None)
    if redis is not None:
        try:
            await redis.set(_music_request_cache_key(request_id), json.dumps(value, ensure_ascii=False), ex=ttl_seconds)
            return
        except Exception:
            pass

    expires_at = time.time() + ttl_seconds
    async with _REQUEST_CACHE_LOCK:
        _REQUEST_CACHE[request_id] = (expires_at, value)


async def _cache_set_pending_nx(request: Request, request_id: str, *, ttl_seconds: int) -> bool:
    pending = {"status": "pending", "created_at": int(time.time())}

    redis = getattr(request.app.state, "redis", None)
    if redis is not None:
        try:
            created = await redis.set(
                _music_request_cache_key(request_id),
                json.dumps(pending, ensure_ascii=False),
                nx=True,
                ex=ttl_seconds,
            )
            return bool(created)
        except Exception:
            pass

    async with _REQUEST_CACHE_LOCK:
        now = time.time()
        existing = _REQUEST_CACHE.get(request_id)
        if existing and existing[0] > now:
            return False
        _REQUEST_CACHE[request_id] = (now + ttl_seconds, pending)
        return True


def _response_from_cached_entry(entry: dict) -> dict:
    status_value = str(entry.get("status") or "").strip().lower()
    if status_value == "pending":
        return {"status": "pending"}
    if status_value == "complete":
        result = entry.get("result")
        if isinstance(result, dict):
            return result
        raise HTTPException(status_code=502, detail="Invalid cached result")
    if status_value == "error":
        http_status = int(entry.get("http_status") or 502)
        detail = str(entry.get("detail") or "Music generation failed")
        raise HTTPException(status_code=http_status, detail=detail)
    return {"status": "pending"}


@router.get("/requests/{request_id}")
async def get_music_request(request: Request, request_id: str):
    request_id = _require_uuid(request_id, field="request_id")
    entry = await _cache_get(request, request_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Not found")
    return _response_from_cached_entry(entry)


async def _elevenlabs_compose(
    request: Request,
    *,
    payload: dict,
    ext: str,
) -> tuple[bytes, str, dict]:
    api_key = _api_key(request)
    if not api_key:
        raise HTTPException(status_code=400, detail="ElevenLabs API key is not configured")

    base_url = str(getattr(request.app.state.config, "ELEVENLABS_API_BASE_URL", ELEVENLABS_BASE_URL) or ELEVENLABS_BASE_URL).strip()
    url = base_url.rstrip("/") + "/v1/music/compose"

    headers = {"Content-Type": "application/json", "xi-api-key": api_key}

    timeout = httpx.Timeout(AIOHTTP_CLIENT_TIMEOUT)
    transport = httpx.AsyncHTTPTransport(retries=0)

    async with httpx.AsyncClient(
        timeout=timeout,
        trust_env=True,
        follow_redirects=False,
        transport=transport,
    ) as client:
        resp = await client.post(url, headers=headers, json=payload)
        raw_text = resp.text

        if resp.status_code >= 400:
            log.error("ElevenLabs status=%s body=%s", resp.status_code, raw_text)
            if resp.status_code == 402:
                raise HTTPException(status_code=402, detail="ElevenLabs credits insufficient")
            raise HTTPException(status_code=502, detail=f"ElevenLabs error {resp.status_code}: {raw_text}")

        content_type = str(resp.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
        media_type = _guess_media_type_for_ext(ext)
        meta_extra: dict = {}

        if content_type.startswith("audio/"):
            return resp.content, content_type, meta_extra

        try:
            parsed = resp.json()
        except Exception:
            raise HTTPException(status_code=502, detail="Unexpected ElevenLabs response")

        if not isinstance(parsed, dict):
            raise HTTPException(status_code=502, detail="Unexpected ElevenLabs response")

        meta_extra = {k: v for k, v in parsed.items() if k not in ("audio", "audio_base64", "audio_b64")}

        audio_b64 = (
            parsed.get("audio_base64")
            or parsed.get("audio_b64")
            or parsed.get("audio")
            or parsed.get("audio_data")
        )
        audio_bytes: bytes | None = None
        if isinstance(audio_b64, str) and audio_b64.strip():
            b64_str = audio_b64.strip()
            if b64_str.startswith("data:") and "," in b64_str:
                header, b64_payload = b64_str.split(",", 1)
                media_type = header.split(";", 1)[0].replace("data:", "").strip() or media_type
                b64_str = b64_payload
            audio_bytes = base64.b64decode(b64_str)

        mt = parsed.get("media_type") or parsed.get("mime_type") or parsed.get("content_type")
        if isinstance(mt, str) and mt.strip():
            media_type = mt.split(";", 1)[0].strip().lower()

        if not audio_bytes:
            raise HTTPException(status_code=502, detail="No audio returned by ElevenLabs")
        return audio_bytes, media_type, meta_extra


async def _run_generation_and_cache(
    request: Request,
    *,
    request_id: str,
    payload: dict,
    length_ms: int,
    output_format: str,
    model_id: str,
    ext: str,
    prompt: str,
    plan: str,
    user,
    form_data: MusicStreamForm,
    credits_subject_id: str | None = None,
    credits_free_limit: int | None = None,
    credits_mode: str | None = None,
    credits_cost: int = 0,
) -> None:
    try:
        audio_id = uuid.uuid4().hex
        file_path, meta_path = _music_paths(audio_id=audio_id, ext=ext)

        audio_bytes, media_type, meta_extra = await _elevenlabs_compose(request, payload=payload, ext=ext)

        async with aiofiles.open(file_path, "wb") as f:
            await f.write(audio_bytes)

        charged_paid = False
        redis = getattr(request.app.state, "redis", None)
        if (
            redis is not None
            and credits_subject_id
            and credits_mode
            and credits_free_limit is not None
        ):
            try:
                _status_after, charged_paid = await commit_generation(
                    redis,
                    domain="music",
                    subject_id=credits_subject_id,
                    free_limit=int(credits_free_limit or 0),
                    mode=str(credits_mode),
                    cost_credits=int(credits_cost or 0),
                    now_ts=int(time.time()),
                )
            except Exception:
                log.exception("Failed to commit music credits charge")

        file_id: str | None = None
        if user is not None and file_path.is_file():
            file_id = _persist_music_file_to_user_files(
                request,
                user=user,
                local_file_path=file_path,
                filename=f"music-{audio_id}.{ext}",
                content_type=media_type,
                metadata={
                    "source": "music",
                    "provider": "elevenlabs",
                    "generated": True,
                    "format": ext,
                    "model": model_id,
                    "output_format": output_format,
                    "music_length_ms": length_ms,
                },
            )

        meta: dict = {
            "provider": "elevenlabs",
            "source": "music",
            "generated": True,
            "model": model_id,
            "output_format": output_format,
            "ext": ext,
            "media_type": media_type,
            "music_length_ms": length_ms,
            "created_at": int(time.time()),
            **({"prompt": prompt} if prompt else {"composition_plan": plan}),
            **({"chat_id": form_data.chat_id} if form_data.chat_id else {}),
            **({"message_id": form_data.message_id} if form_data.message_id else {}),
            **({"file_id": file_id} if file_id else {}),
            **({"metadata": meta_extra} if meta_extra else {}),
        }

        try:
            async with aiofiles.open(meta_path, "w", encoding="utf-8") as f:
                await f.write(json.dumps(meta, ensure_ascii=False))
        except Exception:
            # Metadata write failure should not make the generation fail (credits-safe).
            log.exception("Failed to write music meta audio_id=%s", audio_id)

        result = {
            "id": audio_id,
            "ext": ext,
            "media_type": media_type,
            "play_url": f"/api/v1/music/{audio_id}",
            "download_url": f"/api/v1/music/{audio_id}/download",
            "file_id": file_id,
            "charged": bool(charged_paid),
        }
        await _cache_set(
            request,
            request_id,
            {"status": "complete", "result": result, "updated_at": int(time.time())},
            ttl_seconds=MUSIC_REQUEST_TTL_SECONDS,
        )
    except HTTPException as e:
        await _cache_set(
            request,
            request_id,
            {"status": "error", "http_status": int(e.status_code), "detail": str(e.detail), "updated_at": int(time.time())},
            ttl_seconds=MUSIC_REQUEST_TTL_SECONDS,
        )
    except Exception as e:
        log.exception("Music generation failed request_id=%s", request_id)
        await _cache_set(
            request,
            request_id,
            {"status": "error", "http_status": 502, "detail": str(e), "updated_at": int(time.time())},
            ttl_seconds=MUSIC_REQUEST_TTL_SECONDS,
        )


@router.post("/generate")
async def generate_music(
    request: Request,
    response: Response,
    form_data: MusicStreamForm,
    user=Depends(get_verified_user_or_none),
):
    if user is None:
        _get_or_set_anon_id(request, response)

    if not _enabled(request):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Music is disabled")

    request_id = _require_uuid(form_data.request_id, field="request_id")
    cached = await _cache_get(request, request_id)
    if cached:
        return _response_from_cached_entry(cached)

    is_admin = getattr(user, "role", None) == "admin"
    redis = getattr(request.app.state, "redis", None)

    credits_subject_id: str | None = None
    credits_free_limit: int | None = None
    credits_cost: int | None = None
    credits_mode: str | None = None

    if not is_admin:
        if redis is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Music generation temporarily unavailable",
            )

        credits_subject_id = (
            user.id
            if user is not None
            else f"anon:{_get_or_set_anon_id(request, response)}"
        )
        credits_free_limit = (
            int(getattr(request.app.state.config, "MUSIC_CREDITS_FREE_AUTH", 0) or 0)
            if user is not None
            else int(getattr(request.app.state.config, "MUSIC_CREDITS_FREE_ANON", 0) or 0)
        )
        credits_cost = int(getattr(request.app.state.config, "MUSIC_CREDITS_COST", 0) or 0)

        try:
            preflight = await preflight_generation(
                redis,
                domain="music",
                subject_id=credits_subject_id,
                free_limit=credits_free_limit,
                cost_credits=credits_cost,
                require_auth_after_free=True,
                is_authenticated=user is not None,
            )
            credits_mode = preflight.mode
        except PermissionError:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Please sign in to continue music generation",
            )
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_402_PAYMENT_REQUIRED,
                detail="Insufficient credits for music generation",
            )

    prompt = (form_data.prompt or "").strip()
    plan = (form_data.composition_plan or "").strip()

    if bool(prompt) == bool(plan):
        raise HTTPException(status_code=422, detail="Provide exactly one of prompt or composition_plan")

    length_ms = 30000 if form_data.music_length_ms is None else int(form_data.music_length_ms or 0)
    if length_ms <= 0:
        raise HTTPException(status_code=422, detail="music_length_ms must be a positive integer")
    if length_ms > _max_length_ms(request):
        raise HTTPException(status_code=422, detail="music_length_ms exceeds max allowed length")

    output_format = (form_data.output_format or _default_format(request) or "").strip()
    model_id = (form_data.model_id or _default_model_id(request) or "").strip()

    ext = _ext_from_output_format(output_format)

    payload: dict = {
        "music_length_ms": length_ms,
        "output_format": output_format,
        "force_instrumental": bool(form_data.force_instrumental or False),
        "model_id": model_id,
        **({"prompt": prompt} if prompt else {"composition_plan": plan}),
    }

    created = await _cache_set_pending_nx(request, request_id, ttl_seconds=MUSIC_REQUEST_TTL_SECONDS)
    if not created:
        return {"status": "pending"}

    asyncio.create_task(
        _run_generation_and_cache(
            request,
            request_id=request_id,
            payload=payload,
            length_ms=length_ms,
            output_format=output_format,
            model_id=model_id,
            ext=ext,
            prompt=prompt,
            plan=plan,
            user=user,
            form_data=form_data,
            credits_subject_id=credits_subject_id,
            credits_free_limit=credits_free_limit,
            credits_mode=credits_mode,
            credits_cost=int(credits_cost or 0),
        )
    )
    return {"status": "pending"}


@router.get("/{audio_id}")
async def get_music(audio_id: str):
    found = _find_cached_music_file(audio_id=audio_id)
    if not found:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Audio not found")
    file_path, _ext, media_type = found
    return FileResponse(file_path, media_type=media_type)


@router.get("/{audio_id}/download")
async def download_music(audio_id: str):
    found = _find_cached_music_file(audio_id=audio_id)
    if not found:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Audio not found")
    file_path, ext, media_type = found
    return FileResponse(file_path, media_type=media_type, filename=f"music-{audio_id}.{ext}")
