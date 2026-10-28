"""Definición declarativa de un tipo de documento: cómo se reconoce y qué campos tiene."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Literal

from ..texto import normalizar

Clase = Literal["texto", "nombre", "rut", "fecha", "anio", "codigo", "clases", "numero"]


@dataclass(frozen=True)
class CampoSpec:
    nombre: str
    etiquetas: tuple[str, ...]  # rótulos del documento (se comparan normalizados)
    clase: Clase = "texto"
    clave: bool = False  # campo clave: se revisa con más señales forenses

    @property
    def etiquetas_norm(self) -> tuple[str, ...]:
        return tuple(normalizar(e) for e in self.etiquetas)


NOMBRE = CampoSpec("nombre", ("Nombre", "Nombre completo", "Nombres y apellidos", "Titular", "Nombre del titular"), "nombre", clave=True)
# los rótulos con puntos van primero: "RUN" sin puntos también calza con cabeceras de tabla ("R U N | FINES").
# "RUN" antes que "RUT": las personas se rotulan RUN y las instituciones RUT (un certificado de competencias trae el RUT
# de la universidad y el de su representante legal antes del RUN del postulante)
RUT = CampoSpec("rut", ("R.U.N.", "RUN", "R.U.T.", "RUT", "Cédula de identidad N°", "Carné de identidad N°", "C.I."), "rut", clave=True)


@dataclass(frozen=True)
class TipoSpec:
    clave: str
    nombre: str
    titulos: tuple[str, ...]  # frases que suelen ir como título
    palabras_clave: tuple[str, ...]  # frases típicas del cuerpo
    campos: tuple[CampoSpec, ...]
    version: str = "1"
    # certificados redactados como texto corrido: (campo, regex con un grupo) sobre el texto de la página.
    # Usar \s* entre palabras: hay PDF que vienen sin espacios ("FechadeEmisión:16deoctubrede2026")
    patrones: tuple[tuple[str, str], ...] = ()
    # ajuste propio del tipo después de extraer (doc, campos, crear_campo) → campos agregados o corregidos
    completar: Callable[..., dict] | None = None
    # mínimo para validar el documento: si falta alguno, el documento queda Observado (pedir reenvío)
    obligatorios: tuple[str, ...] = ()
    # frases que por sí solas identifican el tipo aunque el documento repita el título de otro (la copia de verificación
    # dice «CERTIFICADO DE ANTECEDENTES» pero también «Copia no válida como certificado»)
    decisivas: tuple[str, ...] = ()

    def campo(self, nombre: str) -> CampoSpec | None:
        return next((c for c in self.campos if c.nombre == nombre), None)
