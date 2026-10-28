"""Copia de verificación del emisor: lo que entrega el portal de verificación del Registro Civil al ingresar el folio y
el código de un certificado (antecedentes u hoja de vida). Es el mismo certificado con la marca «COPIA» y la leyenda
«Copia no válida como certificado»: no reemplaza al certificado, pero sirve para confirmar su folio y código
(contraste.py la usa para marcar la fuente como verificada).

Calibración oct 2026: 6 de 133 documentos reales eran estas copias (PDF nativo, iText); antes se clasificaban como el
certificado original y competían con él en el expediente.
"""
from __future__ import annotations

import re

from ..texto import normalizar, sin_espacios
from .base import NOMBRE, RUT, CampoSpec, TipoSpec

DOCUMENTOS = (("COPIADECERTIFICADODEANTECEDENTES", "certificado_antecedentes"),
              ("COPIADEHOJADEVIDADELCONDUCTOR", "hoja_vida_conductor"))


def completar(doc, campos: dict, crear) -> dict:
    """Qué certificado se verificó (según el título «COPIA DE …»)."""
    texto = sin_espacios(normalizar(doc.texto))
    for frase, tipo in DOCUMENTOS:
        if frase in texto:
            seg = next((s for p in doc.paginas for s in p.segmentos if re.search(r"COPIA|CERTIFICADO|HOJA", s.norm)), None)
            return {"documento_verificado": crear("documento_verificado", tipo, frase, seg)}
    return {}


TIPO = TipoSpec(
    clave="validacion_emisor",
    nombre="Copia de verificación del emisor",
    titulos=("Copia de certificado de antecedentes", "Copia de hoja de vida del conductor"),
    palabras_clave=("Copia no válida como certificado", "Registro Civil", "Código verificación", "Folio"),
    decisivas=("Copia no válida como certificado",),
    campos=(
        NOMBRE,
        RUT,
        CampoSpec("folio", ("Folio", "N° de folio"), "codigo", clave=True),
        CampoSpec("codigo_verificacion", ("Código de verificación", "Código verificación"), "codigo", clave=True),
        CampoSpec("fecha_emision", ("Fecha de emisión", "Fecha emisión"), "fecha"),
    ),
    completar=completar,
)
