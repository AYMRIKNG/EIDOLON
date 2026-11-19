"""
file_converter.py — EIDOLON Universal File Conversion Engine

Supported conversions:
- Text / Markdown / Code / Logs (.txt, .md, .log, .py, .js, .json, .html, etc.) -> PDF, DOCX, HTML, TXT
- Word Documents (.docx) -> PDF, TXT, MD, HTML
- PDF (.pdf) -> DOCX, TXT
- Images (.png, .jpg, .jpeg, .webp, .bmp, .ico) -> PDF, PNG, JPG, WEBP, ICO
- Spreadsheets & Data (.csv, .xlsx, .json) -> XLSX, CSV, PDF (table), JSON, HTML
- Presentations (.pptx) -> PDF, TXT
- Audio & Video (.mp4, .avi, .mkv, .wav, .mp3, etc.) -> MP3, WAV, MP4 (via ffmpeg if available)

Pops up a clickable resource button on the EIDOLON overlay HUD upon completion.
"""
from __future__ import annotations

import os
import sys
import json
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

# ── Helper Paths ─────────────────────────────────────────────────────────────

def _resolve_source_path(file_path_str: str, player=None) -> Optional[Path]:
    """Resolves file path from input or currently uploaded file on player."""
    if file_path_str and file_path_str.strip():
        p = Path(file_path_str.strip())
        if p.exists() and p.is_file():
            return p.resolve()
        
        # Check standard user locations (Desktop, Downloads, Documents)
        home = Path.home()
        candidates = [
            home / "Desktop" / p.name,
            home / "Downloads" / p.name,
            home / "Documents" / p.name,
            Path.cwd() / p.name,
        ]
        for c in candidates:
            if c.exists() and c.is_file():
                return c.resolve()

    # Fallback to currently uploaded file in UI
    if player:
        for attr in ("_current_file", "current_file"):
            cur = getattr(player, attr, None)
            if cur and Path(cur).exists() and Path(cur).is_file():
                return Path(cur).resolve()
        
        # Check attached_files list
        att = getattr(player, "attached_files", None)
        if att and isinstance(att, list) and len(att) > 0:
            first = att[0]
            if Path(first).exists() and Path(first).is_file():
                return Path(first).resolve()

    return None


def _get_output_path(source_path: Path, target_format: str, custom_out: str = "") -> Path:
    """Computes a clean output path without overwriting the original."""
    target_ext = f".{target_format.lower().lstrip('.')}"
    
    if custom_out and custom_out.strip():
        out = Path(custom_out.strip())
        if out.is_dir():
            return out / f"{source_path.stem}{target_ext}"
        return out.with_suffix(target_ext)

    out_dir = source_path.parent
    base_name = source_path.stem
    candidate = out_dir / f"{base_name}{target_ext}"
    
    # If same name as source or already exists, add _converted suffix or index
    if candidate == source_path or candidate.exists():
        candidate = out_dir / f"{base_name}_converted{target_ext}"
        idx = 1
        while candidate.exists():
            candidate = out_dir / f"{base_name}_converted_{idx}{target_ext}"
            idx += 1
            
    return candidate


# ── Conversion Implementations ────────────────────────────────────────────────

def _convert_text_to_pdf(source: Path, target: Path) -> bool:
    """Converts .txt, .md, .py, code, or logs to a high-quality PDF using PyQt6."""
    from PyQt6.QtGui import QGuiApplication, QTextDocument, QPdfWriter, QPageSize
    from PyQt6.QtCore import QSizeF

    app = QGuiApplication.instance() or QGuiApplication(sys.argv)
    doc = QTextDocument()
    
    raw_text = source.read_text(encoding="utf-8", errors="replace")
    ext = source.suffix.lower()

    if ext == ".md":
        doc.setMarkdown(raw_text)
    else:
        # Generate styled HTML layout with title, header and line breaks
        date_str = datetime.now().strftime("%d/%m/%Y %H:%M")
        escaped_text = (raw_text
                        .replace("&", "&amp;")
                        .replace("<", "&lt;")
                        .replace(">", "&gt;")
                        .replace("\n", "<br>"))
        
        is_code = ext in (".py", ".js", ".ts", ".html", ".css", ".json", ".sql", ".sh", ".bat", ".ps1")
        font_family = "'Consolas', 'Courier New', monospace" if is_code else "'Segoe UI', Arial, sans-serif"
        
        html_content = f"""
        <!DOCTYPE html>
        <html>
        <head>
          <meta charset="utf-8">
          <style>
            body {{
              font-family: {font_family};
              font-size: 10.5pt;
              color: #1e293b;
              line-height: 1.6;
              margin: 20px;
            }}
            .header {{
              border-bottom: 2px solid #00d4ff;
              padding-bottom: 8px;
              margin-bottom: 18px;
            }}
            .title {{
              font-size: 16pt;
              font-weight: bold;
              color: #0f172a;
              margin: 0;
            }}
            .meta {{
              font-size: 9pt;
              color: #64748b;
              margin-top: 4px;
            }}
            .content {{
              white-space: pre-wrap;
              word-wrap: break-word;
            }}
          </style>
        </head>
        <body>
          <div class="header">
            <div class="title">📄 {source.name}</div>
            <div class="meta">Converted by EIDOLON • {date_str}</div>
          </div>
          <div class="content">{escaped_text}</div>
        </body>
        </html>
        """
        doc.setHtml(html_content)

    writer = QPdfWriter(str(target))
    writer.setPageSize(QPageSize(QPageSize.PageSizeId.A4))
    doc.print(writer)
    del writer
    return target.exists() and target.stat().st_size > 0


def _convert_docx_to_pdf(source: Path, target: Path) -> bool:
    """Converts DOCX to PDF using Word COM if available, or PyQt6 fallback."""
    # 1. Try Windows native Word COM (high fidelity)
    if sys.platform == "win32":
        try:
            import comtypes.client
            word = comtypes.client.CreateObject("Word.Application")
            word.Visible = False
            doc = word.Documents.Open(str(source.resolve()))
            doc.SaveAs(str(target.resolve()), FileFormat=17)  # 17 = wdFormatPDF
            doc.Close()
            word.Quit()
            if target.exists() and target.stat().st_size > 0:
                return True
        except Exception:
            pass

    # 2. Universal fallback: python-docx + PyQt6
    try:
        import docx
        d = docx.Document(source)
        paragraphs = []
        for p in d.paragraphs:
            if p.text.strip():
                paragraphs.append(f"<p>{p.text.strip()}</p>")
        
        # Tables support
        for table in d.tables:
            table_html = "<table border='1' cellspacing='0' cellpadding='4' style='border-collapse:collapse; margin:10px 0; width:100%;'>"
            for row in table.rows:
                table_html += "<tr>"
                for cell in row.cells:
                    table_html += f"<td>{cell.text.strip()}</td>"
                table_html += "</tr>"
            table_html += "</table>"
            paragraphs.append(table_html)

        html_body = "".join(paragraphs) or "<p>(Document vide)</p>"
        
        from PyQt6.QtGui import QGuiApplication, QTextDocument, QPdfWriter, QPageSize
        app = QGuiApplication.instance() or QGuiApplication(sys.argv)
        doc = QTextDocument()
        doc.setHtml(f"""
        <html>
        <head>
          <style>
            body {{ font-family: 'Segoe UI', Arial, sans-serif; font-size: 11pt; color: #1e293b; line-height: 1.6; margin: 25px; }}
            h1 {{ font-size: 18pt; color: #0f172a; border-bottom: 2px solid #00d4ff; padding-bottom: 6px; }}
            p {{ margin-bottom: 10px; }}
          </style>
        </head>
        <body>
          <h1>{source.stem}</h1>
          {html_body}
        </body>
        </html>
        """)
        writer = QPdfWriter(str(target))
        writer.setPageSize(QPageSize(QPageSize.PageSizeId.A4))
        doc.print(writer)
        del writer
        return target.exists() and target.stat().st_size > 0
    except Exception as e:
        print(f"[FileConverter:docx_pdf:err] {e}", flush=True)
        return False


def _convert_pdf_to_docx(source: Path, target: Path) -> bool:
    """Converts PDF to Word DOCX via pdfplumber and python-docx."""
    try:
        import pdfplumber
        import docx

        d = docx.Document()
        d.add_heading(source.stem, level=0)

        with pdfplumber.open(source) as pdf:
            for idx, page in enumerate(pdf.pages, 1):
                text = page.extract_text()
                if text:
                    for para in text.split("\n\n"):
                        if para.strip():
                            d.add_paragraph(para.strip())
                
                # Check for tables
                tables = page.extract_tables()
                for table_data in tables:
                    if not table_data:
                        continue
                    t = d.add_table(rows=len(table_data), cols=len(table_data[0]))
                    t.style = "Table Grid"
                    for r_idx, row in enumerate(table_data):
                        for c_idx, val in enumerate(row):
                            t.cell(r_idx, c_idx).text = str(val or "")
                
                if idx < len(pdf.pages):
                    d.add_page_break()

        d.save(str(target))
        return target.exists() and target.stat().st_size > 0
    except Exception as e:
        print(f"[FileConverter:pdf_docx:err] {e}", flush=True)
        return False


def _convert_text_to_docx(source: Path, target: Path) -> bool:
    """Converts Text/Markdown to DOCX."""
    try:
        import docx
        d = docx.Document()
        d.add_heading(source.stem, level=0)
        
        raw_text = source.read_text(encoding="utf-8", errors="replace")
        for line in raw_text.splitlines():
            line_s = line.strip()
            if line_s.startswith("# "):
                d.add_heading(line_s[2:], level=1)
            elif line_s.startswith("## "):
                d.add_heading(line_s[3:], level=2)
            elif line_s.startswith("### "):
                d.add_heading(line_s[4:], level=3)
            elif line_s.startswith("- ") or line_s.startswith("* "):
                d.add_paragraph(line_s[2:], style="List Bullet")
            elif line_s:
                d.add_paragraph(line)
            else:
                d.add_paragraph("")
                
        d.save(str(target))
        return target.exists() and target.stat().st_size > 0
    except Exception as e:
        print(f"[FileConverter:text_docx:err] {e}", flush=True)
        return False


def _convert_image(source: Path, target: Path, target_format: str) -> bool:
    """Converts images between formats or to PDF."""
    try:
        from PIL import Image
        img = Image.open(source)
        fmt = target_format.lower().lstrip(".")
        
        if fmt == "pdf":
            if img.mode in ("RGBA", "P"):
                img = img.convert("RGB")
            img.save(str(target), "PDF", resolution=100.0)
        elif fmt in ("jpg", "jpeg"):
            if img.mode in ("RGBA", "P"):
                img = img.convert("RGB")
            img.save(str(target), "JPEG", quality=92)
        elif fmt == "png":
            img.save(str(target), "PNG")
        elif fmt == "webp":
            img.save(str(target), "WEBP", quality=90)
        elif fmt == "ico":
            img.save(str(target), format="ICO", sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
        else:
            img.save(str(target))
            
        return target.exists() and target.stat().st_size > 0
    except Exception as e:
        print(f"[FileConverter:img:err] {e}", flush=True)
        return False


def _convert_data(source: Path, target: Path, target_format: str) -> bool:
    """Converts CSV, Excel, or JSON tables."""
    try:
        import pandas as pd
        src_ext = source.suffix.lower()
        fmt = target_format.lower().lstrip(".")

        if src_ext in (".csv", ".tsv"):
            sep = "\t" if src_ext == ".tsv" else ","
            df = pd.read_csv(source, sep=sep, encoding="utf-8", errors="replace")
        elif src_ext in (".xlsx", ".xls"):
            df = pd.read_excel(source)
        elif src_ext == ".json":
            df = pd.read_json(source)
        else:
            return False

        if fmt == "xlsx":
            df.to_excel(str(target), index=False)
        elif fmt == "csv":
            df.to_csv(str(target), index=False, encoding="utf-8")
        elif fmt == "json":
            df.to_json(str(target), orient="records", indent=2, force_ascii=False)
        elif fmt == "html":
            df.to_html(str(target), index=False, classes="table")
        elif fmt == "pdf":
            table_html = df.to_html(classes="table", index=False)
            from PyQt6.QtGui import QGuiApplication, QTextDocument, QPdfWriter, QPageSize
            app = QGuiApplication.instance() or QGuiApplication(sys.argv)
            doc = QTextDocument()
            doc.setHtml(f"""
            <html>
            <head>
              <style>
                body {{ font-family: 'Segoe UI', sans-serif; font-size: 9pt; color: #1e293b; margin: 20px; }}
                h2 {{ color: #0284c7; margin-bottom: 12px; }}
                table {{ border-collapse: collapse; width: 100%; }}
                th, td {{ border: 1px solid #cbd5e1; padding: 6px 8px; text-align: left; }}
                th {{ background: #0284c7; color: white; font-weight: 600; }}
                tr:nth-child(even) {{ background: #f8fafc; }}
              </style>
            </head>
            <body>
              <h2>Tableau de données — {source.name}</h2>
              {table_html}
            </body>
            </html>
            """)
            writer = QPdfWriter(str(target))
            writer.setPageSize(QPageSize(QPageSize.PageSizeId.A4))
            doc.print(writer)
            del writer
        else:
            return False

        return target.exists() and target.stat().st_size > 0
    except Exception as e:
        print(f"[FileConverter:data:err] {e}", flush=True)
        return False


def _convert_pptx(source: Path, target: Path, target_format: str) -> bool:
    """Converts PowerPoint presentations to PDF or TXT."""
    fmt = target_format.lower().lstrip(".")
    
    # 1. Try Windows PowerPoint COM for native PDF export
    if fmt == "pdf" and sys.platform == "win32":
        try:
            import comtypes.client
            powerpoint = comtypes.client.CreateObject("PowerPoint.Application")
            pres = powerpoint.Presentations.Open(str(source.resolve()), WithWindow=False)
            pres.SaveAs(str(target.resolve()), 32)  # 32 = ppSaveAsPDF
            pres.Close()
            powerpoint.Quit()
            if target.exists() and target.stat().st_size > 0:
                return True
        except Exception:
            pass

    # 2. Extract slides to styled document
    try:
        from pptx import Presentation
        prs = Presentation(source)
        slides_text = []
        for i, slide in enumerate(prs.slides, 1):
            slide_content = []
            for shape in slide.shapes:
                if hasattr(shape, "text") and shape.text.strip():
                    slide_content.append(shape.text.strip())
            joined = "\n".join(slide_content)
            slides_text.append(f"<h3>Diapositive {i}</h3><p>{joined.replace(chr(10), '<br>')}</p><hr>")

        if fmt == "txt":
            plain = "\n\n".join(f"--- Diapositive {i} ---\n" + "\n".join(slide_content) for i, slide in enumerate(prs.slides, 1))
            target.write_text(plain, encoding="utf-8")
            return target.exists()
        
        if fmt == "pdf":
            from PyQt6.QtGui import QGuiApplication, QTextDocument, QPdfWriter, QPageSize
            app = QGuiApplication.instance() or QGuiApplication(sys.argv)
            doc = QTextDocument()
            doc.setHtml(f"""
            <html>
            <head>
              <style>
                body {{ font-family: 'Segoe UI', Arial, sans-serif; font-size: 11pt; color: #1e293b; margin: 25px; }}
                h1 {{ color: #0284c7; border-bottom: 2px solid #00d4ff; padding-bottom: 6px; }}
                h3 {{ color: #0f172a; margin-top: 16px; }}
                hr {{ border: 0; height: 1px; background: #e2e8f0; margin: 16px 0; }}
              </style>
            </head>
            <body>
              <h1>Présentation : {source.stem}</h1>
              {"".join(slides_text)}
            </body>
            </html>
            """)
            writer = QPdfWriter(str(target))
            writer.setPageSize(QPageSize(QPageSize.PageSizeId.A4))
            doc.print(writer)
            del writer
            return target.exists() and target.stat().st_size > 0
    except Exception as e:
        print(f"[FileConverter:pptx:err] {e}", flush=True)

    return False


def _convert_av_media(source: Path, target: Path, target_format: str) -> bool:
    """Converts audio/video using ffmpeg."""
    try:
        cmd = ["ffmpeg", "-i", str(source), str(target), "-y"]
        res = subprocess.run(cmd, capture_output=True, timeout=120)
        return target.exists() and target.stat().st_size > 0
    except Exception as e:
        print(f"[FileConverter:ffmpeg:err] {e}", flush=True)
        return False


# ── Main Dispatcher ──────────────────────────────────────────────────────────

def convert_file(parameters: dict, player=None, speak=None) -> str:
    """Universal File Converter entry point."""
    file_path_str = parameters.get("file_path", "")
    target_format = (parameters.get("target_format") or parameters.get("format") or "").strip().lower().lstrip(".")
    custom_out = parameters.get("output_path", "")

    # 1. Resolve source file
    source = _resolve_source_path(file_path_str, player=player)
    if not source:
        return ("Erreur : Aucun fichier source spécifié ou téléversé. "
                "Veuillez déposer un fichier sur l'application ou préciser son chemin complet.")

    if not target_format:
        return f"Erreur : Veuillez spécifier le format de destination souhaité pour '{source.name}' (ex: pdf, docx, png, xlsx, etc.)."

    src_ext = source.suffix.lower().lstrip(".")
    if src_ext == target_format:
        return f"Le fichier '{source.name}' est déjà au format {target_format.upper()}."

    # 2. Determine output path
    target = _get_output_path(source, target_format, custom_out)
    target.parent.mkdir(parents=True, exist_ok=True)

    print(f"[FileConverter] Converting {source.name} ({src_ext}) -> {target.name} ({target_format})...", flush=True)
    if player and hasattr(player, "write_log"):
        player.write_log(f"SYS: Conversion en cours : {source.name} -> {target.name}...")

    # 3. Perform conversion based on file type
    success = False
    text_exts = {"txt", "md", "log", "rst", "py", "js", "ts", "json", "html", "css", "sql", "sh", "bat", "ps1"}
    image_exts = {"png", "jpg", "jpeg", "webp", "bmp", "tiff", "ico"}
    data_exts = {"csv", "tsv", "xlsx", "xls"}
    av_exts = {"mp4", "avi", "mov", "mkv", "mp3", "wav", "m4a", "ogg", "flac"}

    if src_ext in text_exts:
        if target_format == "pdf":
            success = _convert_text_to_pdf(source, target)
        elif target_format in ("docx", "doc"):
            success = _convert_text_to_docx(source, target)
        elif target_format in ("txt", "text"):
            shutil.copyfile(source, target)
            success = target.exists()
    elif src_ext in ("docx", "doc"):
        if target_format == "pdf":
            success = _convert_docx_to_pdf(source, target)
        elif target_format in ("txt", "md"):
            try:
                import docx
                d = docx.Document(source)
                target.write_text("\n\n".join(p.text for p in d.paragraphs if p.text.strip()), encoding="utf-8")
                success = target.exists()
            except Exception:
                success = False
    elif src_ext == "pdf":
        if target_format in ("docx", "doc"):
            success = _convert_pdf_to_docx(source, target)
        elif target_format in ("txt", "text"):
            try:
                import pdfplumber
                with pdfplumber.open(source) as pdf:
                    target.write_text("\n\n".join(p.extract_text() or "" for p in pdf.pages), encoding="utf-8")
                success = target.exists()
            except Exception:
                success = False
    elif src_ext in image_exts:
        success = _convert_image(source, target, target_format)
    elif src_ext in data_exts:
        success = _convert_data(source, target, target_format)
    elif src_ext in ("pptx", "ppt"):
        success = _convert_pptx(source, target, target_format)
    elif src_ext in av_exts:
        success = _convert_av_media(source, target, target_format)

    # Fallback generic copy if formats match or text
    if not success and target_format == "txt":
        try:
            target.write_text(source.read_text(encoding="utf-8", errors="ignore"), encoding="utf-8")
            success = target.exists()
        except Exception:
            pass

    if not success or not target.exists():
        return (f"Échec de la conversion : la conversion de '{source.name}' ({src_ext.upper()}) "
                f"vers le format {target_format.upper()} n'a pas pu être effectuée.")

    # 4. CRITICAL: Display the clickable button card on the HUD overlay
    res_card = [{
        "type": "file",
        "name": target.name,
        "target": str(target.resolve())
    }]
    if player and hasattr(player, "show_resources") and callable(player.show_resources):
        player.show_resources(res_card)

    log_success = f"Fichier converti avec succès : {target.resolve()}"
    if player and hasattr(player, "write_log"):
        player.write_log(f"SYS: {log_success}")

    return (
        f"Conversion terminée avec succès, monsieur ! "
        f"Le fichier '{source.name}' a été converti en {target_format.upper()} : {target.resolve()}\n\n"
        f"Le bouton d'accès direct est affiché sur votre interface HUD. Cliquez simplement dessus pour l'ouvrir immédiatement."
    )


# ── Action Tool Declaration ──────────────────────────────────────────────────

TOOL = {
    "name": "file_converter",
    "description": (
        "Moteur de conversion universel de fichiers pour EIDOLON. "
        "Convertit instantanément n'importe quel fichier téléversé ou spécifié vers le format cible souhaité "
        "(ex: texte vers PDF, markdown vers PDF, DOCX vers PDF, PDF vers DOCX, images vers PDF ou formats d'image, "
        "CSV vers Excel/PDF, audio/vidéo). "
        "Affiche automatiquement un bouton cliquable interactif sur l'interface pour ouvrir directement le nouveau fichier généré."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "file_path": {
                "type": "STRING",
                "description": "Chemin du fichier source à convertir. S'il n'est pas précisé, le fichier actuellement téléversé ou déposé par l'utilisateur est utilisé automatiquement."
            },
            "target_format": {
                "type": "STRING",
                "description": "Format cible d'extension souhaité (ex: 'pdf', 'docx', 'txt', 'png', 'jpg', 'webp', 'xlsx', 'csv', 'mp3')."
            },
            "output_path": {
                "type": "STRING",
                "description": "Chemin ou dossier de destination optionnel. Par défaut, le fichier est créé dans le même dossier que l'original."
            }
        },
        "required": ["target_format"]
    },
    "handler": convert_file
}
