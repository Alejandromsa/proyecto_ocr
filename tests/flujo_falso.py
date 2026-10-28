"""Flujo de Power Automate falso para las pruebas de convocatorias: un servidor HTTP local en un hilo, sin red real.

Responde `listDeepest` con la forma real del flujo (carpetas y archivos con FileLeafRef, FileRef, FileDirRef, FSObjType numérico,
Modified, EstadoValidacion null, FieldValuesAsText.Editor y SIN File_x0020_Size) y `getFile` con `contentBase64`.
`transporte` es el transporte inyectable de `ClienteFlujo`: manda la petición al servidor local conservando la ruta y la consulta
de la URL «de Power Automate» (así se comprueba que el secreto llega al flujo) sin tocar internet.
"""
from __future__ import annotations

import base64
import json
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

# URL con la apariencia de una real; la firma (sig) es el secreto que no debe aparecer en ninguna salida
FIRMA = "SECRETOFIRMA_Zx9Qw7Lm3"
FLUJO_URL = ("https://prod-12.westus.logic.azure.com:443/workflows/0123456789abcdef0123456789abcdef/triggers/manual/paths/invoke"
             f"?api-version=2016-06-01&sp=%2Ftriggers%2Fmanual%2Frun&sv=1.0&sig={FIRMA}")
SITIO_URL = "https://straconcom.sharepoint.com/sites/Casodeuso_validacindocumental"
RUTA = "/sites/Casodeuso_validacindocumental/libreria test/PROCESO_202611"


class FlujoFalso:
    def __init__(self, base: str = RUTA) -> None:
        self.base = base
        self.archivos: dict[str, dict] = {}  # FileRef -> {"datos": bytes, "modificado": str, "declarar_tamano": bool}
        self.carpetas: set[str] = set()
        self.llamadas: list[tuple[str, str]] = []  # (acción, path) de cada petición que llegó al flujo
        self.recibidos: list[str] = []  # ruta + consulta con que se invocó el flujo (para comprobar la firma)
        self.fallos: dict[tuple[str, str | None], str] = {}  # (acción, FileRef o None) -> "ok_false" | "http500" | "base64" | "sin_contenido"
        self.red_caida = 0  # las próximas N peticiones fallan como un error de red (con la URL en el texto del error)
        self._bloqueo: tuple[str, threading.Event] | None = None
        self._lock = threading.Lock()
        self._servidor = ThreadingHTTPServer(("127.0.0.1", 0), self._manejador())
        self.puerto = self._servidor.server_address[1]
        threading.Thread(target=self._servidor.serve_forever, daemon=True).start()

    # --- contenido -----------------------------------------------------------------------------

    def carpeta(self, *partes: str) -> str:
        for i in range(1, len(partes) + 1):
            self.carpetas.add("/".join([self.base, *partes[:i]]))
        return "/".join([self.base, *partes])

    def archivo(self, partes: list[str], datos: bytes, modificado: str = "2026-10-27T15:10:00Z", declarar_tamano: bool = True) -> str:
        """Agrega un archivo en la ruta relativa a la convocatoria (cargo/postulante/…/nombre) y sus carpetas intermedias."""
        for i in range(1, len(partes)):
            self.carpetas.add("/".join([self.base, *partes[:i]]))
        ref = "/".join([self.base, *partes])
        self.archivos[ref] = {"datos": datos, "modificado": modificado, "declarar_tamano": declarar_tamano}
        return ref

    def quitar(self, ref: str) -> None:
        self.archivos.pop(ref, None)

    def contar(self, accion: str, ref: str | None = None) -> int:
        return sum(1 for a, p in self.llamadas if a == accion and (ref is None or p == ref))

    def bloquear(self, ref: str) -> threading.Event:
        """`getFile` de ese archivo espera hasta que se libere el evento devuelto."""
        ev = threading.Event()
        self._bloqueo = (ref, ev)
        return ev

    def cerrar(self) -> None:
        self._servidor.shutdown()
        self._servidor.server_close()

    # --- cuerpos de respuesta ---------------------------------------------------------------------

    def _elemento(self, ref: str, es_carpeta: bool) -> dict:
        dir_ref, _, nombre = ref.rpartition("/")
        return {"FileLeafRef": nombre, "FileRef": ref, "FileDirRef": dir_ref, "FSObjType": 1 if es_carpeta else 0,
                "Modified": self.archivos[ref]["modificado"] if not es_carpeta else "2026-10-20T10:00:00Z",
                "UltimaActividad": None, "EstadoValidacion": None, "FieldValuesAsText": {"Editor": "Nelson Bernal Cubillas"}}

    def _lista(self) -> dict:
        carpetas = [self._elemento(r, True) for r in sorted(self.carpetas)]
        archivos = [self._elemento(r, False) for r in sorted(self.archivos)]
        return {"ok": True, "rutaInicial": self.base, "totalCarpetas": len(carpetas), "totalArchivos": len(archivos),
                "carpetas": carpetas, "archivos": archivos}

    def _archivo(self, ref: str) -> tuple[int, dict]:
        f = self.archivos.get(ref)
        if f is None:
            return 404, {"ok": False, "error": {"code": "not_found", "message": "No existe el archivo."}}
        cuerpo = {"ok": True, "name": ref.rsplit("/", 1)[-1], "carpeta": "x", "estadoValidacion": "Pendiente", "ultimaActividad": "",
                  "modificado": f["modificado"], "modificadoPor": "Nelson Bernal Cubillas",
                  "contentBase64": base64.b64encode(f["datos"]).decode("ascii")}
        if f["declarar_tamano"]:
            cuerpo["size"] = len(f["datos"])
        return 200, cuerpo

    # --- servidor -------------------------------------------------------------------------------------

    def _manejador(self):
        falso = self

        class Manejador(BaseHTTPRequestHandler):
            def log_message(self, *args):  # sin ruido en la salida de pytest
                pass

            def do_POST(self):  # noqa: N802
                cuerpo = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))).decode("utf-8"))
                accion, ruta = cuerpo.get("action"), cuerpo.get("path")
                with falso._lock:
                    falso.llamadas.append((accion, ruta))
                    falso.recibidos.append(self.path)
                bloqueo = falso._bloqueo
                if accion == "getFile" and bloqueo and bloqueo[0] == ruta:
                    bloqueo[1].wait(30)
                fallo = falso.fallos.get((accion, ruta if accion == "getFile" else None))
                if fallo == "http500":
                    estado, salida = 500, "error interno"
                elif fallo == "ok_false":
                    estado, salida = 403, {"ok": False, "error": {"code": "forbidden", "message": "Sitio no permitido."}}
                elif accion == "listDeepest":
                    estado, salida = 200, falso._lista()
                elif accion == "getFile":
                    estado, salida = falso._archivo(ruta)
                    if fallo == "base64" and estado == 200:
                        salida["contentBase64"] = "%%%A%%%"
                    if fallo == "sin_contenido" and estado == 200:
                        salida.pop("contentBase64")
                else:
                    estado, salida = 400, {"ok": False, "error": {"code": "bad_request", "message": "Acción desconocida."}}
                datos = (salida if isinstance(salida, str) else json.dumps(salida)).encode("utf-8")
                self.send_response(estado)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(datos)))
                self.end_headers()
                self.wfile.write(datos)

        return Manejador

    def transporte(self, url: str, cuerpo: bytes, timeout: float) -> tuple[int, bytes]:
        with self._lock:
            if self.red_caida > 0:
                self.red_caida -= 1
                raise OSError(f"No se pudo conectar a {url} (simulado)")
        u = urlsplit(url)
        destino = f"http://127.0.0.1:{self.puerto}{u.path}" + (f"?{u.query}" if u.query else "")
        peticion = urllib.request.Request(destino, data=cuerpo, method="POST", headers={"Content-Type": "text/plain"})
        try:
            with urllib.request.urlopen(peticion, timeout=timeout) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()
