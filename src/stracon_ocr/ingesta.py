"""Ingesta: hash, MIME real, conversión y páginas normalizadas (PDF, imágenes, DOCX y DOC)."""
from __future__ import annotations

import hashlib
import io
import mimetypes
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path

import puremagic
from PIL import Image, ImageOps

from .config import Config
from .layout import Pal
from .motores import pdf_nativo
from .trabajo import DocTrabajo, PaginaTrabajo

try:  # HEIC/HEIF de celulares
    from pillow_heif import register_heif_opener

    register_heif_opener()
except Exception:  # pragma: no cover
    pass

MIME_PDF = "application/pdf"
MIME_DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
MIME_DOC = "application/msword"
LADO_MAX_IMAGEN = 3500  # px; las fotos más grandes se reducen (no mejora el OCR y lo hace lento)


class FormatoNoSoportado(ValueError):
    pass


def mime_real(datos: bytes, nombre: str) -> str | None:
    try:
        candidatos = puremagic.magic_string(datos, filename=nombre)
    except puremagic.PureError:
        return None
    for c in candidatos:
        if c.mime_type:
            # puremagic reconoce .docx como zip: se confirma mirando el paquete
            if c.mime_type in {"application/zip", "application/x-zip-compressed"} and _es_docx(datos):
                return MIME_DOCX
            return c.mime_type
    return None


def _es_docx(datos: bytes) -> bool:
    try:
        with zipfile.ZipFile(io.BytesIO(datos)) as z:
            return "word/document.xml" in z.namelist()
    except zipfile.BadZipFile:
        return False


def documento_minimo(ruta: Path, datos: bytes, nombre: str) -> DocTrabajo:
    """Identificación del archivo (hash y tipo MIME) sin páginas: sirve también cuando el archivo no se puede abrir."""
    real = MIME_DOCX if _es_docx(datos) else mime_real(datos, nombre)
    return DocTrabajo(
        ruta=ruta,
        nombre=nombre,
        datos=datos,
        sha256=hashlib.sha256(datos).hexdigest(),
        mime_real=real,
        mime_declarado=mimetypes.guess_type(nombre)[0],
        origen="desconocido",
    )


def cargar(ruta: Path, cfg: Config, datos: bytes | None = None, nombre: str | None = None) -> DocTrabajo:
    datos = datos if datos is not None else ruta.read_bytes()
    nombre = nombre or ruta.name
    doc = documento_minimo(ruta, datos, nombre)
    real = doc.mime_real
    if real == MIME_PDF or datos[:5] == b"%PDF-":
        doc.origen = "pdf_nativo"  # se confirma en el parseo según haya texto
        doc.paginas = pdf_nativo.leer_paginas(datos, cfg.dpi)
    elif real == MIME_DOCX:
        doc.origen = "docx"
        doc.docx_datos = datos
        doc.paginas = paginas_docx(datos)
    elif real == MIME_DOC or datos[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":
        doc.origen = "doc"
        doc.docx_datos = convertir_doc(datos)
        doc.paginas = paginas_docx(doc.docx_datos)
    elif real and real.startswith("image/") or _abre_como_imagen(datos):
        doc.origen = "imagen"
        doc.paginas = paginas_imagen(datos)
    else:
        raise FormatoNoSoportado(f"Formato no soportado: {real or 'desconocido'} ({nombre})")
    return doc


def _abre_como_imagen(datos: bytes) -> bool:
    try:
        Image.open(io.BytesIO(datos)).verify()
        return True
    except Exception:
        return False


def paginas_imagen(datos: bytes) -> list[PaginaTrabajo]:
    img = Image.open(io.BytesIO(datos))
    paginas = []
    for i in range(getattr(img, "n_frames", 1)):
        img.seek(i)
        cuadro = ImageOps.exif_transpose(img.copy()).convert("RGB")
        dpi = img.info.get("dpi")
        dpi = int(round(float(dpi[0]))) if dpi and dpi[0] else 96
        if max(cuadro.size) > LADO_MAX_IMAGEN:
            f = LADO_MAX_IMAGEN / max(cuadro.size)
            cuadro = cuadro.resize((int(cuadro.width * f), int(cuadro.height * f)), Image.LANCZOS)
            dpi = int(dpi * f)
        paginas.append(PaginaTrabajo(n=i + 1, ancho=cuadro.width, alto=cuadro.height, dpi=dpi, imagen=cuadro, escala=1.0))
    return paginas


def paginas_docx(datos: bytes) -> list[PaginaTrabajo]:
    """Texto del DOCX en orden (párrafos y tablas) como una página lógica, más sus imágenes como páginas para OCR.

    Las cajas del texto son sintéticas (renglón y columna) y no se publican en el contrato.
    """
    from docx import Document as Docx
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    d = Docx(io.BytesIO(datos))
    lineas: list[str] = [p.text for s in d.sections for p in s.header.paragraphs]
    for hijo in d.element.body.iterchildren():
        if hijo.tag.endswith("}p"):
            lineas.append(Paragraph(hijo, d).text)
        elif hijo.tag.endswith("}tbl"):
            for fila in Table(hijo, d).rows:
                celdas = []
                for c in fila.cells:
                    if c.text.strip() and (not celdas or celdas[-1] != c.text.strip()):
                        celdas.append(c.text.strip())
                lineas.append((" " * 8).join(celdas))  # el hueco separa las celdas en segmentos distintos
    palabras: list[Pal] = []
    for r, linea in enumerate(l for l in lineas if l.strip()):
        col = 0
        for parte in linea.split(" "):
            if parte:
                palabras.append(Pal(parte, (col * 6.0, r * 20.0, (col + len(parte)) * 6.0, r * 20.0 + 12), 1.0, orden=len(palabras), motor="python-docx"))
            col += len(parte) + 1
    paginas = [PaginaTrabajo(n=1, ancho=0, alto=0, dpi=None, nativas=palabras, fuente_texto="docx", motor="python-docx")]
    with zipfile.ZipFile(io.BytesIO(datos)) as z:
        for nombre in sorted(n for n in z.namelist() if n.startswith("word/media/")):
            try:
                for p in paginas_imagen(z.read(nombre)):
                    p.n = len(paginas) + 1
                    paginas.append(p)
            except Exception:
                continue  # formatos de imagen no rasterizables (emf, wmf)
    return paginas


def soffice() -> str | None:
    for c in ("soffice", r"C:\Program Files\LibreOffice\program\soffice.exe", r"C:\Program Files (x86)\LibreOffice\program\soffice.exe"):
        if shutil.which(c) or Path(c).exists():
            return shutil.which(c) or c
    return None


def convertir_doc(datos: bytes) -> bytes:
    exe = soffice()
    if not exe:
        raise FormatoNoSoportado("Para leer .doc hace falta LibreOffice instalado (soffice)")
    with tempfile.TemporaryDirectory() as tmp:
        origen = Path(tmp) / "entrada.doc"
        origen.write_bytes(datos)
        subprocess.run([exe, "--headless", "--convert-to", "docx", "--outdir", tmp, str(origen)], check=True, capture_output=True, timeout=120)
        return (Path(tmp) / "entrada.docx").read_bytes()
