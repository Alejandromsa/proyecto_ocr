"""Registro de tipos de documento del MVP. Para agregar uno: un módulo con TIPO = TipoSpec(...) y sumarlo aquí."""
from __future__ import annotations

from . import antecedentes, cedula, estudios_medios, estudios_prof, hoja_vida, licencia
from .base import CampoSpec, TipoSpec

TIPOS: dict[str, TipoSpec] = {
    m.TIPO.clave: m.TIPO for m in (cedula, licencia, estudios_medios, estudios_prof, antecedentes, hoja_vida)
}

__all__ = ["TIPOS", "CampoSpec", "TipoSpec"]
