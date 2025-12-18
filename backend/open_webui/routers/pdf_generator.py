import json
import logging
import time
import uuid
from io import BytesIO
from pathlib import Path

import aiofiles
from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, status
from fastapi.responses import FileResponse
from pydantic import BaseModel

from open_webui.config import CACHE_DIR
from open_webui.env import SRC_LOG_LEVELS
from open_webui.models.chats import ChatTitleMessagesForm
from open_webui.routers.files import upload_file_handler
from open_webui.utils.auth import get_admin_user, get_verified_user_or_none
from open_webui.utils.pdf_generator import PDFGenerator

log = logging.getLogger(__name__)
log.setLevel(SRC_LOG_LEVELS.get("ROUTERS", logging.INFO))

PDF_CACHE_DIR = CACHE_DIR / "pdf_generator" / "generations"
PDF_CACHE_DIR.mkdir(parents=True, exist_ok=True)

router = APIRouter()


def _pdf_paths(*, pdf_id: str) -> tuple[Path, Path]:
    file_path = PDF_CACHE_DIR / f"{pdf_id}.pdf"
    meta_path = PDF_CACHE_DIR / f"{pdf_id}.json"
    return file_path, meta_path


def _find_cached_pdf(pdf_id: str) -> Path | None:
    pdf_id = str(pdf_id or "").strip()
    if not pdf_id:
        return None
    file_path, _meta = _pdf_paths(pdf_id=pdf_id)
    return file_path if file_path.is_file() else None


class PdfGeneratorStatus(BaseModel):
    available: bool
    enabled: bool


@router.get("/status", response_model=PdfGeneratorStatus)
async def pdf_generator_status(request: Request):
    enabled = bool(getattr(request.app.state.config, "ENABLE_PDF_GENERATOR", True) or False)
    return {"available": enabled, "enabled": enabled}


class PdfGeneratorConfig(BaseModel):
    ENABLE_PDF_GENERATOR: bool


@router.get("/config", response_model=PdfGeneratorConfig)
async def get_pdf_generator_config(request: Request, user=Depends(get_admin_user)):
    return {
        "ENABLE_PDF_GENERATOR": bool(
            getattr(request.app.state.config, "ENABLE_PDF_GENERATOR", True) or False
        )
    }


@router.post("/config/update", response_model=PdfGeneratorConfig)
async def update_pdf_generator_config(
    request: Request, form_data: PdfGeneratorConfig, user=Depends(get_admin_user)
):
    request.app.state.config.ENABLE_PDF_GENERATOR = bool(form_data.ENABLE_PDF_GENERATOR)
    return await get_pdf_generator_config(request, user=user)


class PdfGenerateForm(BaseModel):
    input: str
    title: str | None = None
    message_id: str | None = None


class PdfGenerateResponse(BaseModel):
    id: str
    media_type: str
    view_url: str
    download_url: str
    file_id: str | None = None


@router.post("/generate", response_model=PdfGenerateResponse)
async def generate_pdf(
    request: Request,
    form_data: PdfGenerateForm,
    user=Depends(get_verified_user_or_none),
):
    enabled = bool(getattr(request.app.state.config, "ENABLE_PDF_GENERATOR", True) or False)
    if not enabled:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="PDF Generator is disabled")

    text = str(form_data.input or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="Input text is required")

    if len(text) > 100_000:
        raise HTTPException(status_code=400, detail="Input text is too long")

    title = str(form_data.title or "").strip() or "PDF"

    pdf_id = uuid.uuid4().hex
    file_path, meta_path = _pdf_paths(pdf_id=pdf_id)

    try:
        chat_form = ChatTitleMessagesForm(
            title=title,
            messages=[
                {
                    "role": "assistant",
                    "content": text,
                    "timestamp": int(time.time()),
                }
            ],
        )
        pdf_bytes = PDFGenerator(chat_form).generate_chat_pdf()
    except Exception as e:
        log.exception(e)
        raise HTTPException(status_code=500, detail="Failed to generate PDF")

    try:
        async with aiofiles.open(file_path, "wb") as f:
            await f.write(pdf_bytes)
        async with aiofiles.open(meta_path, "w", encoding="utf-8") as f:
            await f.write(
                json.dumps(
                    {
                        "title": title,
                        "message_id": form_data.message_id,
                        "created_at": int(time.time()),
                    },
                    ensure_ascii=False,
                )
            )
    except Exception as e:
        log.exception(e)
        raise HTTPException(status_code=500, detail="Failed to store PDF")

    file_id: str | None = None
    if user is not None:
        try:
            meta = {
                "generated": True,
                "source": "pdf_generator",
                "title": title,
                **({"message_id": str(form_data.message_id)} if form_data.message_id else {}),
            }
            upload = UploadFile(
                file=BytesIO(pdf_bytes),
                filename=f"pdf-{pdf_id}.pdf",
                headers={"content-type": "application/pdf"},
            )
            file_item = upload_file_handler(request, file=upload, metadata=meta, process=False, user=user)
            file_id = getattr(file_item, "id", None)
        except Exception:
            # Persistence is best-effort; generation itself should still succeed.
            file_id = None

    view_url = f"/api/v1/pdf_generator/{pdf_id}"
    download_url = f"/api/v1/pdf_generator/{pdf_id}/download"
    return {
        "id": pdf_id,
        "media_type": "application/pdf",
        "view_url": view_url,
        "download_url": download_url,
        "file_id": file_id,
    }


@router.get("/{pdf_id}")
async def get_pdf(pdf_id: str):
    file_path = _find_cached_pdf(pdf_id)
    if not file_path:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="PDF not found")
    return FileResponse(file_path, media_type="application/pdf")


@router.get("/{pdf_id}/download")
async def download_pdf(pdf_id: str):
    file_path = _find_cached_pdf(pdf_id)
    if not file_path:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="PDF not found")
    return FileResponse(
        file_path,
        media_type="application/pdf",
        filename=f"chat-{pdf_id}.pdf",
    )

