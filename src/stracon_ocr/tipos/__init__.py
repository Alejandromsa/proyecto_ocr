"""Registro de tipos de documento. Para agregar uno: un módulo con TIPO = TipoSpec(...), sumarlo aquí y agregar su clave a
`contrato.TipoDocumento`. Los seis del MVP más `validacion_emisor` (copia del portal de verificación, no es obligatoria)."""
from __future__ import annotations

from . import antecedentes, cedula, estudios_medios, estudios_prof, hoja_vida, licencia, validacion_emisor
from .base import CampoSpec, TipoSpec

TIPOS: dict[str, TipoSpec] = {
    m.TIPO.clave: m.TIPO for m in (cedula, licencia, estudios_medios, estudios_prof, antecedentes, hoja_vida,
                                   validacion_emisor)
}

__all__ = ["TIPOS", "CampoSpec", "TipoSpec"]
