"""Cliente del flujo de Power Automate que lee SharePoint (acciones `listDeepest` y `getFile`).

El flujo se invoca con POST `text/plain` y cuerpo JSON `{action, siteUrl, path}`. La URL del flujo es un secreto
(lleva la firma de acceso): se usa solo en memoria durante la petición y nunca se escribe en logs, en JSON ni en los
mensajes de error. Por eso ninguna excepción de red se propaga con su texto original (puede traer la URL): se traduce
a `ErrorFlujo`, que tiene mensajes fijos en español.
"""
from __future__ import annotations

import base64
import binascii
import json
import re
import time
import urllib.error
import urllib.request
from typing import Any, Callable
from urllib.parse import parse_qsl, urlsplit

# transporte(url, cuerpo, timeout) -> (código HTTP, cuerpo de la respuesta); inyectable para las pruebas
Transporte = Callable[[str, bytes, float], tuple[int, bytes]]

LIMITE_RESPUESTA_LISTA = 200 * 1024 * 1024  # una biblioteca enorme no debe agotar la memoria
LIMITE_FLUJO_MB = 10  # el flujo solo entrega el contenido de archivos de hasta 10 MB

MENSAJES_CODIGO = {
    "bad_request": "El flujo rechazó la solicitud (datos no válidos).",
    "forbidden": "El flujo no permite ese sitio o esa ruta (revise la lista de sitios permitidos del flujo).",
    "not_found": "El flujo no encontró la carpeta o el archivo en SharePoint.",
    "too_large": "El archivo supera el tamaño máximo que entrega el flujo.",
    "timeout": "El flujo tardó demasiado en responder.",
    "throttled": "El flujo recibió demasiadas solicitudes; vuelva a intentarlo en unos minutos.",
    "internal": "El flujo tuvo un error interno.",
}


class ErrorFlujo(Exception):
    """Fallo al hablar con el flujo. El mensaje está en español y no contiene la URL del flujo."""

    def __init__(self, mensaje: str, codigo: str | None = None, reintentable: bool = False) -> None:
        super().__init__(mensaje)
        self.mensaje = mensaje
        self.codigo = codigo
        self.reintentable = reintentable


def secretos_de(url: str) -> list[str]:
    """Trozos de la URL que no deben aparecer en ninguna salida (la URL entera, su ruta, su consulta y los valores de la firma)."""
    partes = urlsplit(url)
    trozos = {url, partes.query, partes.path + ("?" + partes.query if partes.query else "")}
    trozos |= {v for _, v in parse_qsl(partes.query) if len(v) >= 6}
    if len(partes.path) > 8:
        trozos.add(partes.path)
    return sorted((t for t in trozos if t and len(t) >= 6), key=len, reverse=True)


def redactar(texto: str, url: str | None) -> str:
    """Reemplaza por «[flujo]» cualquier fragmento secreto de la URL del flujo."""
    if not url:
        return texto
    for s in secretos_de(url):
        texto = texto.replace(s, "[flujo]")
    return texto


def _transporte_urllib(url: str, cuerpo: bytes, timeout: float) -> tuple[int, bytes]:
    peticion = urllib.request.Request(url, data=cuerpo, method="POST", headers={"Content-Type": "text/plain; charset=utf-8"})
    try:
        with urllib.request.urlopen(peticion, timeout=timeout) as r:  # noqa: S310 - el esquema y el host se validan al recibir la URL
            return r.status, r.read(LIMITE_RESPUESTA_LISTA + 1)
    except urllib.error.HTTPError as e:  # el flujo respondió con un código de error: el cuerpo puede traer {ok:false, error}
        try:
            return e.code, e.read(1024 * 1024)
        finally:
            e.close()


class ClienteFlujo:
    def __init__(self, url: str, sitio_url: str, *, transporte: Transporte | None = None, timeout: float = 120.0,
                 espera_reintento: float = 1.0) -> None:
        self._url = url  # secreto: no se expone en repr ni en errores
        self.sitio_url = sitio_url
        self._transporte = transporte or _transporte_urllib
        self.timeout = timeout
        self.espera_reintento = espera_reintento

    def __repr__(self) -> str:
        return f"ClienteFlujo(sitio_url={self.sitio_url!r})"

    def redactar(self, texto: str) -> str:
        return redactar(texto, self._url)

    # --- llamada base --------------------------------------------------------------------------

    def _llamar(self, accion: str, ruta: str) -> dict[str, Any]:
        """POST al flujo con un reintento ante error de red. Devuelve el cuerpo JSON con `ok: true`."""
        cuerpo = json.dumps({"action": accion, "siteUrl": self.sitio_url, "path": ruta}).encode("utf-8")
        for intento in (1, 2):
            try:
                return self._una_vez(cuerpo)
            except ErrorFlujo as e:
                if not e.reintentable or intento == 2:
                    raise
                if self.espera_reintento:
                    time.sleep(self.espera_reintento)
        raise AssertionError("inalcanzable")  # pragma: no cover

    def _una_vez(self, cuerpo: bytes) -> dict[str, Any]:
        try:
            estado, datos = self._transporte(self._url, cuerpo, self.timeout)
        except TimeoutError:
            raise ErrorFlujo("El flujo tardó demasiado en responder.", "timeout", reintentable=True) from None
        except Exception:  # noqa: BLE001 - el texto original puede traer la URL: se descarta
            raise ErrorFlujo("No se pudo contactar el flujo de SharePoint. Revise la conexión de red.", "unreachable",
                             reintentable=True) from None
        try:
            respuesta = json.loads(datos.decode("utf-8-sig")) if datos else None
        except (ValueError, UnicodeDecodeError):
            respuesta = None
        if isinstance(respuesta, dict) and respuesta.get("ok") is False:
            raise self._error_del_flujo(respuesta, estado)
        if estado in (429, 502, 503, 504):
            raise ErrorFlujo(MENSAJES_CODIGO["throttled"] if estado == 429 else f"El flujo no está disponible (HTTP {estado}).",
                             "throttled" if estado == 429 else "unavailable", reintentable=True)
        if estado != 200:
            raise ErrorFlujo(f"El flujo respondió con un error (HTTP {estado}).", "http")
        if not isinstance(respuesta, dict) or respuesta.get("ok") is not True:
            raise ErrorFlujo("La respuesta del flujo no tiene el formato esperado.", "bad_response")
        return respuesta

    @staticmethod
    def _error_del_flujo(respuesta: dict[str, Any], estado: int) -> ErrorFlujo:
        error = respuesta.get("error")
        codigo = error.get("code") if isinstance(error, dict) else None
        codigo = codigo if isinstance(codigo, str) and re.fullmatch(r"[a-z_]{1,32}", codigo) else None
        if codigo in MENSAJES_CODIGO:
            return ErrorFlujo(MENSAJES_CODIGO[codigo], codigo)
        return ErrorFlujo("El flujo respondió con un error" + (f" (código {codigo})" if codigo else f" (HTTP {estado})") + ".", codigo)

    # --- acciones ----------------------------------------------------------------------------------

    def listar(self, ruta: str) -> dict[str, Any]:
        """`listDeepest`: árbol plano `{rutaInicial, carpetas[], archivos[]}` bajo la ruta."""
        return self._llamar("listDeepest", ruta)

    def descargar(self, ruta: str, max_bytes: int) -> dict[str, Any]:
        """`getFile`: devuelve `{nombre, modificado, bytes_declarados, contenido}`.

        `contenido` es None si el archivo supera `max_bytes` (o el límite del flujo): se informa en `motivo_omitido` sin
        decodificar nada. Un contenido inválido o vacío es un `ErrorFlujo`.
        """
        r = self._llamar("getFile", ruta)
        declarado = r.get("size")
        declarado = int(declarado) if isinstance(declarado, (int, float)) and not isinstance(declarado, bool) else \
            int(declarado) if isinstance(declarado, str) and declarado.strip().isdigit() else None
        salida: dict[str, Any] = {"nombre": r.get("name") if isinstance(r.get("name"), str) else None,
                                  "modificado": r.get("modificado") if isinstance(r.get("modificado"), str) and r.get("modificado") else None,
                                  "bytes_declarados": declarado, "contenido": None, "motivo_omitido": None}
        mb = max_bytes // (1024 * 1024)
        if declarado is not None and declarado > max_bytes:
            salida["motivo_omitido"] = f"Supera el tamaño máximo permitido ({mb} MB)."
            return salida
        b64 = r.get("contentBase64")
        if not isinstance(b64, str) or not b64:
            if declarado is not None and declarado > LIMITE_FLUJO_MB * 1024 * 1024:
                salida["motivo_omitido"] = f"El flujo no entrega el contenido de archivos de más de {LIMITE_FLUJO_MB} MB."
                return salida
            raise ErrorFlujo("El flujo no entregó el contenido del archivo.", "sin_contenido")
        if len(b64) * 3 // 4 > max_bytes + 4:  # sin decodificar: no se llena la memoria con un archivo que se va a descartar
            salida["motivo_omitido"] = f"Supera el tamaño máximo permitido ({mb} MB)."
            return salida
        try:
            datos = base64.b64decode(b64, validate=False)
        except (binascii.Error, ValueError):
            raise ErrorFlujo("El contenido del archivo que entregó el flujo no es válido.", "bad_response") from None
        if not datos:
            raise ErrorFlujo("El archivo descargado está vacío.", "vacio")
        if len(datos) > max_bytes:
            salida["motivo_omitido"] = f"Supera el tamaño máximo permitido ({mb} MB)."
            return salida
        salida["contenido"] = datos
        return salida
