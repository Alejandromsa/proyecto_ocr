"""Resultados en archivos JSON, uno por documento, agrupados por convocatoria (hasta migrar a una base documental).

    <raíz>/<id>/convocatoria.json                       progreso + índice de archivos
    <raíz>/<id>/<cargo>/<postulante>/<archivo>.json     Documento completo (contrato v1.0)
    <raíz>/<id>/<cargo>/<postulante>/_expediente.json   Expediente (contraste entre los documentos del postulante)

La escritura va detrás de `ResultadosJSON`: el pipeline y el servicio de convocatorias solo conocen esa interfaz, de modo
que pasar a Cosmos DB (u otro) es escribir otra implementación. Las partes de la ruta son nombres ya saneados
(`sanear_nombre`/`asignar_nombres`); la implementación rechaza cualquier parte que pudiera salirse de la raíz.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
import unicodedata
import uuid
from pathlib import Path
from typing import Any, Iterable, Protocol, Sequence

LIMITE_NOMBRE = 80  # por tramo: deja margen al límite de 260 caracteres de las rutas de Windows
RESERVADOS_WINDOWS = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
_INVALIDOS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


class ResultadosJSON(Protocol):
    """Lo mínimo que el servicio necesita para guardar y leer JSON. `partes` es la ruta relativa a la raíz, ya saneada."""

    def leer(self, partes: Sequence[str]) -> Any | None: ...

    def escribir(self, partes: Sequence[str], datos: Any) -> None: ...

    def existe(self, partes: Sequence[str]) -> bool: ...


def hash_corto(texto: str, largo: int = 6) -> str:
    return hashlib.sha1(texto.encode("utf-8")).hexdigest()[:largo]


def sanear_nombre(nombre: str, limite: int = LIMITE_NOMBRE) -> str:
    """Nombre válido en Windows: sin `<>:"/\\|?*` ni controles, sin puntos ni espacios finales, sin nombres reservados."""
    n = _INVALIDOS.sub("_", unicodedata.normalize("NFC", nombre)).strip(" ").rstrip(". ")
    if not n:
        n = "_"
    if n.split(".")[0].rstrip(" ").upper() in RESERVADOS_WINDOWS:
        n = "_" + n
    if len(n) > limite:  # el corte pierde información: el hash mantiene distintos a los nombres largos con el mismo comienzo
        n = n[: limite - 7].rstrip(". ") + "-" + hash_corto(nombre)
    return n


def asignar_nombres(nombres: dict[str, str], previos: dict[str, str] | None = None, extension: str = "",
                    reservados: Iterable[str] = ()) -> dict[str, str]:
    """Nombre en disco de cada elemento de una misma carpeta. `nombres` es {clave única: nombre original}.

    Conserva lo ya asignado en `previos` ({clave: nombre en disco}): los nombres no cambian entre ejecuciones. Si el nombre
    saneado coincide con otro de la carpeta (sin distinguir mayúsculas, como Windows), se le agrega
    `-<6 caracteres del hash de la clave>`. `extension` se agrega al final (p. ej. `.json`) y cuenta para la colisión.
    """
    previos = dict(previos or {})
    usados = {v.lower() for v in previos.values()} | {r.lower() for r in reservados}
    resultado = dict(previos)
    for k in sorted((k for k in nombres if k not in previos), key=lambda k: (nombres[k].lower(), k)):
        base = sanear_nombre(nombres[k], LIMITE_NOMBRE - len(extension))
        candidato, n = base + extension, 0
        while candidato.lower() in usados:
            sufijo = "-" + hash_corto(k if n == 0 else f"{k}#{n}")
            candidato = base[: LIMITE_NOMBRE - len(extension) - len(sufijo)].rstrip(". ") + sufijo + extension
            n += 1
        usados.add(candidato.lower())
        resultado[k] = candidato
    return {k: resultado[k] for k in nombres}


class ArchivosJSON:
    """`ResultadosJSON` sobre el sistema de archivos. Escritura atómica: archivo temporal en la misma carpeta + renombrar."""

    def __init__(self, raiz: Path | str) -> None:
        self.raiz = Path(raiz)
        self._raiz_abs = self.raiz.resolve()

    def _ruta(self, partes: Sequence[str]) -> Path:
        if not partes:
            raise ValueError("Ruta de resultados vacía")
        for p in partes:
            if not p or p in {".", ".."} or sanear_nombre(p, 200) != p:
                raise ValueError(f"Nombre no válido en la ruta de resultados: {p!r}")
        ruta = self.raiz.joinpath(*partes)
        if self._raiz_abs not in ruta.resolve().parents:
            raise ValueError("La ruta de resultados sale de la raíz")
        return ruta

    def existe(self, partes: Sequence[str]) -> bool:
        return self._ruta(partes).is_file()

    def leer(self, partes: Sequence[str]) -> Any | None:
        ruta = self._ruta(partes)
        for intento in range(8):
            try:
                return json.loads(ruta.read_text(encoding="utf-8"))
            except FileNotFoundError:
                return None
            except PermissionError:  # Windows: justo en el instante de reemplazarlo otro hilo
                time.sleep(0.02 * (intento + 1))
        raise PermissionError(f"No se pudo leer {ruta.name}")

    def escribir(self, partes: Sequence[str], datos: Any) -> None:
        ruta = self._ruta(partes)
        ruta.parent.mkdir(parents=True, exist_ok=True)
        tmp = ruta.with_name(f".{uuid.uuid4().hex[:8]}.tmp")
        try:
            with open(tmp, "w", encoding="utf-8", newline="\n") as f:
                json.dump(datos, f, ensure_ascii=False, indent=2, default=str)
                f.flush()
                os.fsync(f.fileno())
            for intento in range(8):
                try:
                    os.replace(tmp, ruta)
                    return
                except PermissionError:  # Windows: otro hilo lo está leyendo en este instante
                    time.sleep(0.02 * (intento + 1))
            os.replace(tmp, ruta)
        finally:
            tmp.unlink(missing_ok=True)
