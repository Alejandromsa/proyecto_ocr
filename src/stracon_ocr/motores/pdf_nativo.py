"""Lectura de PDF: texto nativo con fuente y tamaño por carácter (pdfplumber) y render (pypdfium2).

Los caracteres se depuran antes de armar palabras: el texto girado (marcas de agua) va aparte y el texto
encimado se separa en capas, para que no se mezcle con los rótulos y valores.
"""
from __future__ import annotations

import io

import pdfplumber
import pypdfium2 as pdfium
from pdfplumber.utils import extract_words

from ..bloqueos import PDFIUM
from ..layout import Pal, depurar_superpuestas
from ..trabajo import PaginaTrabajo


def _es_blanco(color) -> bool:
    if color is None:
        return False
    if isinstance(color, (int, float)):
        return color >= 0.95
    vals = list(color) if isinstance(color, (list, tuple)) else []
    if not vals or not all(isinstance(v, (int, float)) for v in vals):  # patrones o espacios de color con nombre
        return False
    if len(vals) == 4:  # CMYK
        return all(v <= 0.05 for v in vals)
    return all(v >= 0.95 for v in vals)


def _girado(c: dict) -> bool:
    m = c.get("matrix") or (1, 0, 0, 1)
    return abs(m[1]) > 1e-3 or abs(m[2]) > 1e-3


def _pal_caracter(c: dict, orden: int) -> Pal:
    return Pal(t=c["text"], bbox=(c["x0"], c["top"], c["x1"], c["bottom"]), fuentes=(c.get("fontname", ""),),
               tams=(round(float(c.get("size", 0)), 2),), color=tuple(c.get("non_stroking_color") or ()) or None,
               orden=orden, motor="pdfplumber")


def leer_paginas(datos: bytes, dpi: int) -> list[PaginaTrabajo]:
    with PDFIUM:
        return _leer_paginas(datos, dpi)


def _leer_paginas(datos: bytes, dpi: int) -> list[PaginaTrabajo]:
    paginas: list[PaginaTrabajo] = []
    render = pdfium.PdfDocument(datos)
    try:
        with pdfplumber.open(io.BytesIO(datos)) as pdf:
            for i, pg in enumerate(pdf.pages):
                rectos, girados, original = [], [], {}
                for k, c in enumerate(pg.chars):
                    if not c["text"].strip():
                        continue
                    p = _pal_caracter(c, k)
                    original[id(p)] = c
                    (girados if _girado(c) else rectos).append(p)
                limpios, superposiciones = depurar_superpuestas(rectos)
                nativas = []
                for w in extract_words([original[id(p)] for p in limpios], return_chars=True, keep_blank_chars=False):
                    chars = w["chars"]
                    nativas.append(Pal(
                        t=w["text"],
                        bbox=(w["x0"], w["top"], w["x1"], w["bottom"]),
                        fuentes=tuple(c.get("fontname", "") for c in chars),
                        tams=tuple(round(float(c.get("size", 0)), 2) for c in chars),
                        color=tuple(chars[0].get("non_stroking_color") or ()) or None,
                        orden=len(nativas),
                        motor="pdfplumber",
                    ))
                imagenes = [
                    {"bbox": (im["x0"], im["top"], im["x1"], im["bottom"]), "srcsize": list(im.get("srcsize") or ()), "nombre": im.get("name")}
                    for im in pg.images
                ]
                rellenos = [
                    {"bbox": (r["x0"], r["top"], r["x1"], r["bottom"]), "blanco": _es_blanco(r.get("non_stroking_color"))}
                    for r in pg.rects
                    if r.get("fill")
                ]
                imagen = render[i].render(scale=dpi / 72).to_pil()
                paginas.append(PaginaTrabajo(
                    n=i + 1, ancho=float(pg.width), alto=float(pg.height), dpi=None, rotacion=int(pg.rotation or 0),
                    imagen=imagen, escala=72 / dpi, nativas=nativas, rotadas=girados, imagenes=imagenes,
                    rellenos=rellenos, superposiciones=superposiciones,
                ))
    finally:
        render.close()
    return paginas
