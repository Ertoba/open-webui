from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Dict, Any, List
from html import escape
import os
import sys

from markdown import markdown

import site
from fpdf import FPDF

from open_webui.env import STATIC_DIR, FONTS_DIR
from open_webui.models.chats import ChatTitleMessagesForm


class PDFGenerator:
    """
    Description:
    The `PDFGenerator` class is designed to create PDF documents from chat messages.
    The process involves transforming markdown content into HTML and then into a PDF format

    Attributes:
    - `form_data`: An instance of `ChatTitleMessagesForm` containing title and messages.

    """

    def __init__(self, form_data: ChatTitleMessagesForm):
        self.html_body = None
        self.messages_html = None
        self.form_data = form_data

        self.css = Path(STATIC_DIR / "assets" / "pdf-style.css").read_text()

    def format_timestamp(self, timestamp: float) -> str:
        """Convert a UNIX timestamp to a formatted date string."""
        try:
            date_time = datetime.fromtimestamp(timestamp)
            return date_time.strftime("%Y-%m-%d, %H:%M:%S")
        except (ValueError, TypeError) as e:
            # Log the error if necessary
            return ""

    def _build_html_message(self, message: Dict[str, Any]) -> str:
        """Build HTML for a single message."""
        raw_role = str(message.get("role", "user") or "user")
        role = escape(raw_role)
        raw_content = str(message.get("content", "") or "")
        timestamp = message.get("timestamp")

        raw_model = message.get("model") if raw_role == "assistant" else ""
        model = escape(raw_model) if isinstance(raw_model, str) else ""

        date_str = escape(self.format_timestamp(timestamp) if timestamp else "")

        html_content = escape(raw_content)
        try:
            html_content = markdown(html_content, extensions=["extra", "sane_lists"])
        except Exception:
            html_content = html_content.replace("\n", "<br/>")
        html_message = f"""
            <div>
                <div>
                    <h4>
                        <strong>{role.title()}</strong>
                        <span style="font-size: 12px;">{model}</span>
                    </h4>
                    <div> {date_str} </div>
                </div>
                <br/>
                <br/>

                <div>
                    {html_content}
                </div>
            </div>
            <br/>
          """
        return html_message

    def _generate_html_body(self) -> str:
        """Generate the full HTML body for the PDF."""
        escaped_title = escape(self.form_data.title)
        return f"""
        <html>
            <head>
                <meta name="viewport" content="width=device-width, initial-scale=1.0" />
                <meta charset="utf-8" />
            </head>
            <body>
            <div>
                <div>
                    <h2>{escaped_title}</h2>
                    {self.messages_html}
                </div>
            </div>
            </body>
        </html>
        """

    def _font_candidates(self) -> list[Path]:
        candidates: list[Path] = []

        override = (os.getenv("PDF_FONT_PATH") or os.getenv("PDF_FONTS_PATH") or "").strip()
        if override:
            p = Path(override).expanduser()
            try:
                if p.is_dir():
                    for ext in ("*.ttf", "*.otf", "*.ttc"):
                        candidates.extend(sorted(p.glob(ext), key=lambda x: str(x).lower()))
                else:
                    candidates.append(p)
            except Exception:
                candidates.append(p)

        try:
            if FONTS_DIR and Path(FONTS_DIR).is_dir():
                for ext in ("*.ttf", "*.otf", "*.ttc"):
                    candidates.extend(sorted(Path(FONTS_DIR).glob(ext), key=lambda x: str(x).lower()))
        except Exception:
            pass

        if sys.platform.startswith("win"):
            fonts_dir = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
            candidates.extend(
                [
                    fonts_dir / "sylfaen.ttf",
                    fonts_dir / "arialuni.ttf",
                    fonts_dir / "arial.ttf",
                    fonts_dir / "segoeui.ttf",
                ]
            )
        else:
            candidates.extend(
                [
                    Path("/usr/share/fonts/truetype/noto/NotoSansGeorgian-Regular.ttf"),
                    Path("/usr/share/fonts/truetype/noto/NotoSerifGeorgian-Regular.ttf"),
                    Path("/usr/share/fonts/opentype/noto/NotoSansGeorgian-Regular.ttf"),
                    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
                    Path("/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"),
                ]
            )

        seen: set[Path] = set()
        unique: list[Path] = []
        for p in candidates:
            if p in seen:
                continue
            seen.add(p)
            unique.append(p)
        return unique

    def _resolve_base_font_path(self) -> Path | None:
        preferred_names = (
            "NotoSansGeorgian-Regular.ttf",
            "NotoSerifGeorgian-Regular.ttf",
            "DejaVuSans.ttf",
            "sylfaen.ttf",
            "NotoSans-Regular.ttf",
            "NotoSans-Variable.ttf",
            "LiberationSans-Regular.ttf",
            "arialuni.ttf",
        )

        candidates = self._font_candidates()
        for name in preferred_names:
            for p in candidates:
                try:
                    if p.name.lower() == name.lower() and p.is_file():
                        return p
                except Exception:
                    continue

        for p in candidates:
            try:
                if p.is_file():
                    return p
            except Exception:
                continue

        return None

    def _weasyprint_css(self) -> str:
        font_path = self._resolve_base_font_path()
        css = str(self.css or "")

        if font_path is not None:
            font_uri = font_path.resolve().as_uri()
            css = (
                f"@font-face {{ font-family: 'OWUIFont'; src: url('{font_uri}'); }}\n"
                "html, body { font-family: 'OWUIFont', 'NotoSans', 'DejaVu Sans', 'Sylfaen', sans-serif; }\n"
                + css
            )
        else:
            css = "html, body { font-family: 'NotoSans', 'DejaVu Sans', 'Sylfaen', sans-serif; }\n" + css

        return css

    def generate_chat_pdf(self) -> bytes:
        """
        Generate a PDF from chat messages.
        """
        try:
            global FONTS_DIR

            # Build HTML messages
            messages_html_list: List[str] = [
                self._build_html_message(msg) for msg in self.form_data.messages
            ]
            self.messages_html = "<div>" + "".join(messages_html_list) + "</div>"

            # Generate full HTML body (for full HTML renderers)
            self.html_body = self._generate_html_body()

            engine = str(os.getenv("PDF_ENGINE") or "weasyprint").strip().lower() or "weasyprint"

            if engine == "weasyprint":
                try:
                    from weasyprint import CSS, HTML
                    from weasyprint.text.fonts import FontConfiguration
                except Exception:
                    engine = "fpdf"

            if engine == "weasyprint":
                font_config = FontConfiguration()
                css = CSS(
                    string=self._weasyprint_css(),
                    base_url=str(STATIC_DIR),
                    font_config=font_config,
                )

                pdf_bytes = HTML(string=self.html_body, base_url=str(STATIC_DIR)).write_pdf(
                    stylesheets=[css], font_config=font_config
                )
                return bytes(pdf_bytes)

            # Fallback: fpdf2 HTML subset (no CSS support)
            pdf = FPDF()
            pdf.add_page()

            # When running using `pip install` the static directory is in the site packages.
            if not FONTS_DIR.exists():
                FONTS_DIR = Path(site.getsitepackages()[0]) / "static/fonts"
            # When running using `pip install -e .` the static directory is in the site packages.
            # This path only works if `open-webui serve` is run from the root of this project.
            if not FONTS_DIR.exists():
                FONTS_DIR = Path(".") / "backend" / "static" / "fonts"

            base_font_path = self._resolve_base_font_path()
            base_font_file = str(base_font_path) if base_font_path else f"{FONTS_DIR}/NotoSans-Regular.ttf"

            pdf.add_font("NotoSans", "", base_font_file)
            pdf.add_font("NotoSans", "b", base_font_file)
            pdf.add_font("NotoSans", "i", base_font_file)
            pdf.add_font("NotoSansKR", "", f"{FONTS_DIR}/NotoSansKR-Regular.ttf")
            pdf.add_font("NotoSansJP", "", f"{FONTS_DIR}/NotoSansJP-Regular.ttf")
            pdf.add_font("NotoSansSC", "", f"{FONTS_DIR}/NotoSansSC-Regular.ttf")
            pdf.add_font("Twemoji", "", f"{FONTS_DIR}/Twemoji.ttf")

            pdf.set_font("NotoSans", size=12)
            pdf.set_fallback_fonts(
                ["NotoSansKR", "NotoSansJP", "NotoSansSC", "Twemoji"]
            )

            pdf.set_auto_page_break(auto=True, margin=15)

            pdf.write_html(f"<h2>{escape(self.form_data.title)}</h2>")
            pdf.write_html(self.messages_html)

            # Save the pdf with name .pdf
            pdf_bytes = pdf.output()

            return bytes(pdf_bytes)
        except Exception as e:
            raise e
