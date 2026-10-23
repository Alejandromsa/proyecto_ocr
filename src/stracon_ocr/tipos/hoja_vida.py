from __future__ import annotations

import re

from .base import NOMBRE, RUT, CampoSpec, TipoSpec


def completar(doc, campos: dict, crear) -> dict:
    """Anotaciones del Registro Nacional de Conductores: se cuentan las 'FECHA ANOTACION'."""
    if "anotaciones" in campos:
        return {}
    n = len(re.findall(r"FECHA\s*ANOTACI[OÓ]N", doc.texto, re.I))
    if n:
        return {"anotaciones": crear("anotaciones", f"{n} anotación(es)", "FECHA ANOTACION")}
    if re.search(r"SIN\s*ANTECEDENTES", doc.texto, re.I):
        return {"anotaciones": crear("anotaciones", "Sin anotaciones", "SIN ANTECEDENTES")}
    return {}


TIPO = TipoSpec(
    clave="hoja_vida_conductor",
    nombre="Hoja de vida del conductor",
    titulos=("Hoja de vida del conductor", "Certificado de hoja de vida del conductor"),
    palabras_clave=("Registro Nacional de Conductores", "Anotaciones", "Licencias registradas", "Próximo control", "Registro Civil",
                    "Fecha otorgamiento"),
    campos=(
        NOMBRE,
        RUT,
        CampoSpec("folio", ("Folio", "N° de folio"), "codigo", clave=True),
        CampoSpec("codigo_verificacion", ("Código", "Código de verificación", "Código verificación"), "codigo", clave=True),
        CampoSpec("clases", ("Clase", "Clases"), "clases", clave=True),
        CampoSpec("fecha_control", ("Próximo control", "Fecha de control"), "fecha", clave=True),
        CampoSpec("fecha_emision", ("Fecha de emisión", "Fecha emisión"), "fecha"),
        CampoSpec("anotaciones", ("Anotaciones",)),
    ),
    completar=completar,
    obligatorios=("rut", "folio", "codigo_verificacion"),
)
