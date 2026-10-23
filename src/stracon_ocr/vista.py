"""Imagen de una página con las cajas de señales y campos dibujadas, para mostrar al revisor en la webapp."""
from __future__ import annotations

import io
from pathlib import Path
from typing import Any

import pypdfium2 as pdfium
from PIL import Image, ImageDraw

from . import ingesta
from .bloqueos import PDFIUM

COLORES = {"alta": (220, 38, 38), "media": (234, 120, 8), "baja": (202, 160, 0), "info": (100, 116, 139)}
COLOR_CAMPO = (37, 99, 235)


class PaginaNoDisponible(LookupError):
    pass


def imagen_pagina(ruta: Path, documento: dict[str, Any], n: int, dpi: int) -> tuple[Image.Image, float]:
    """Imagen de la página n y el factor de escala de las coordenadas del contrato a píxeles.

    Si el OCR enderezó la página (foto o escaneo girado), la imagen se gira igual: las cajas están en la página enderezada."""
    img, f = _imagen_pagina(ruta, documento, n, dpi)
    paginas = documento.get("paginas", [])
    giro = (paginas[n - 1].get("calidad") or {}).get("rotacion_corregida", 0) if 1 <= n <= len(paginas) else 0
    return (img.rotate(giro, expand=True) if giro else img), f


def _imagen_pagina(ruta: Path, documento: dict[str, Any], n: int, dpi: int) -> tuple[Image.Image, float]:
    datos = ruta.read_bytes()
    origen = documento["archivo"]["origen"]
    if origen in {"pdf_nativo", "pdf_escaneado"}:
        with PDFIUM:
            pdf = pdfium.PdfDocument(datos)
            try:
                if not 1 <= n <= len(pdf):
                    raise PaginaNoDisponible(f"El documento tiene {len(pdf)} página(s)")
                return pdf[n - 1].render(scale=dpi / 72).to_pil(), dpi / 72
            finally:
                pdf.close()
    if origen == "imagen":
        paginas = ingesta.paginas_imagen(datos)
    elif origen in {"docx", "doc"}:
        paginas = [p for p in ingesta.paginas_docx(datos if origen == "docx" else ingesta.convertir_doc(datos)) if p.imagen is not None]
        paginas = [p for p in paginas if p.n == n]
        n = 1
    else:
        paginas = []
    if not 1 <= n <= len(paginas):
        raise PaginaNoDisponible("La página no tiene imagen (texto de Word) o no existe")
    img = paginas[n - 1].imagen
    f = min(1.0, 1600 / max(img.size))  # las fotos se muestran a lo sumo a 1600 px
    if f < 1:
        img = img.resize((int(img.width * f), int(img.height * f)))
    return img, f


def dibujar(img: Image.Image, documento: dict[str, Any], n: int, escala: float, senales: bool = True, campos: bool = True) -> Image.Image:
    img = img.convert("RGB")
    capa = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(capa)

    def caja(b):
        return [b[0] * escala, b[1] * escala, b[2] * escala, b[3] * escala]

    if campos:
        for nombre, c in documento.get("campos", {}).items():
            if c.get("pagina") == n and c.get("bbox"):
                r = caja(c["bbox"])
                d.rectangle(r, outline=COLOR_CAMPO + (200,), width=2)
                d.text((r[0], max(0, r[1] - 12)), nombre, fill=COLOR_CAMPO + (255,))
    if senales:
        for i, s in enumerate(documento.get("senales", []), start=1):
            if s.get("pagina") == n and s.get("bbox"):
                color = COLORES.get(s["severidad"], COLORES["info"])
                r = caja(s["bbox"])
                r = [r[0] - 4, r[1] - 4, r[2] + 4, r[3] + 4]
                d.rectangle(r, fill=color + (40,), outline=color + (255,), width=3)
                d.rectangle([r[2] - 2, r[1] - 16, r[2] + 16, r[1]], fill=color + (255,))
                d.text((r[2] + 2, r[1] - 15), str(i), fill=(255, 255, 255, 255))
    return Image.alpha_composite(img.convert("RGBA"), capa).convert("RGB")


def png(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()
