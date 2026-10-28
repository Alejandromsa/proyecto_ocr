"""Señales forenses deterministas (ver docs/catalogo-senales.md). Ninguna decide: todas son hallazgos con evidencia."""
from __future__ import annotations

from typing import Any

from rapidfuzz.distance import Levenshtein

from ..contrato import Campo, Senal
from ..layout import redondear
from ..texto import RE_RUT, normalizar_rut
from ..trabajo import DocTrabajo
from . import docx, imagen, ocr_senales, pdf_estructura, pdf_metadatos


def analizar(doc: DocTrabajo, tipo: str, campos: dict[str, Campo], reglas: dict[str, Any]) -> list[Senal]:
    senales: list[Senal] = []
    if doc.origen in {"pdf_nativo", "pdf_escaneado"}:
        senales += pdf_metadatos.analizar(doc, reglas, tipo)
        senales += pdf_estructura.analizar(doc, tipo, campos, reglas)
    if doc.docx_datos:
        senales += docx.analizar(doc, tipo, campos, reglas)
    if doc.origen == "imagen":
        senales += imagen.analizar(doc, campos, reglas)
    senales += ocr_senales.analizar(doc, tipo, campos, reglas)
    senales += rut_inconsistente(doc, campos)
    return senales


def rut_inconsistente(doc: DocTrabajo, campos: dict[str, Campo]) -> list[Senal]:
    """El RUT suele aparecer varias veces (licencia: junto a la foto y en los campos; certificados: encabezado y tablas).
    Si una aparición difiere en 1 o 2 dígitos de la del campo, probablemente se editó solo una. RUT muy distintos son
    de terceros y no cuentan."""
    rut = campos.get("rut")
    if not rut or not rut.valor_normalizado:
        return []
    principal = rut.valor_normalizado.replace("-", "")
    distintos: dict[str, tuple] = {}
    for p in doc.paginas:
        for s in p.segmentos:
            for m in RE_RUT.finditer(s.texto):
                otro = normalizar_rut(m.group(0))
                if not otro or otro == rut.valor_normalizado or not 0 < Levenshtein.distance(principal, otro.replace("-", "")) <= 2:
                    continue
                # todas las apariciones cuentan, también las de DV inválido: en una foto editada la aparición cambiada
                # queda con el DV roto y el campo toma la original (prueba_alteraciones, oct 2026). Si fue el OCR, la
                # severidad media y el detalle lo advierten
                distintos.setdefault(otro, (m.group(0), p, s))
    if not distintos:
        return []
    otro, (texto, p, s) = next(iter(distintos.items()))
    por_ocr = p.fuente_texto == "ocr" or rut.metodo == "ocr"
    return [Senal(
        id="rut.inconsistente", familia="estructura", severidad="media" if por_ocr else "alta", campo="rut", pagina=p.n,
        bbox=redondear(s.bbox) if p.fuente_texto != "docx" else None,
        detalle=f"El documento trae el RUT {rut.valor} y, en otra parte, {texto}: difieren en uno o dos dígitos"
                + (" (puede ser un error de lectura del OCR: comparar en la imagen)" if por_ocr else ""),
        evidencia={"rut_campo": rut.valor, "otras_apariciones": [v[0] for v in distintos.values()]},
    )]
