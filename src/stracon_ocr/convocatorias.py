"""Análisis de convocatorias completas de SharePoint (ver docs/api-convocatorias.md).

Una convocatoria se lee con el flujo de Power Automate (`listDeepest`), se arma su jerarquía cargo / postulante / archivos,
se descargan los archivos (`getFile`), se procesan con el `Procesador` y el grupo de trabajadores de la API y se calcula el
contraste de cada postulante. Todo queda en JSON (`almacen.resultados`): el estado vive en `<id>/convocatoria.json`, así que
un reinicio del servicio a mitad se reanuda con un nuevo POST (los archivos ya listos no se repiten).

La URL del flujo es un secreto: solo vive en memoria durante el análisis (nunca en disco, logs ni mensajes de error).
"""
from __future__ import annotations

import hashlib
import logging
import threading
import traceback
import unicodedata
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import unquote, urlsplit

from .almacen.resultados import ResultadosJSON, asignar_nombres
from .config import Config
from .contrato import Documento
from .flujo import ClienteFlujo, ErrorFlujo, Transporte
from .ingesta import FormatoNoSoportado
from .pipeline import EXTENSIONES, Procesador
from .trabajadores import Grupo

log = logging.getLogger(__name__)
INDICE = "convocatoria.json"
EXPEDIENTE = "_expediente.json"
VERSION_INDICE = 1
DESCARGAS_SIMULTANEAS = 2
MSG_INTERRUMPIDO = ("El análisis se interrumpió (el servicio se reinició). Envíe de nuevo POST /convocatorias "
                    "para reanudarlo: los archivos ya procesados no se repiten.")


def _ahora() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


# --- validación de la solicitud -------------------------------------------------------------------


@dataclass
class Solicitud:
    flujo_url: str
    sitio_url: str
    ruta: str
    nombre: str


def id_convocatoria(sitio_url: str, ruta: str) -> str:
    """Estable: sha1(sitio_url.lower() + "|" + ruta.lower())[:12] (sin barras finales, que no cambian de carpeta)."""
    clave = sitio_url.strip().rstrip("/").lower() + "|" + ruta.strip().rstrip("/").lower()
    return hashlib.sha1(clave.encode("utf-8")).hexdigest()[:12]


def id_valido(valor: str) -> bool:
    return len(valor) == 12 and all(c in "0123456789abcdef" for c in valor)


def id_expediente(ruta_postulante: str) -> str:
    """Clave opaca del expediente de un postulante. El RUT real sale de los documentos (`Expediente.identidad.rut`)."""
    return hashlib.sha1(ruta_postulante.encode("utf-8")).hexdigest()[:10]


def _error(campo: str, mensaje: str) -> dict[str, Any]:
    return {"loc": ["body", campo], "msg": mensaje, "type": "value_error"}


def _host_en(host: str, sufijos: list[str]) -> bool:
    return any(host.endswith("." + s) for s in sufijos)


def validar_solicitud(datos: Any, cfg: Config) -> tuple[Solicitud | None, list[dict[str, Any]]]:
    """Valida el cuerpo de `POST /convocatorias`. Los mensajes nunca repiten el valor recibido (la URL del flujo es secreta)."""
    if not isinstance(datos, dict):
        return None, [{"loc": ["body"], "msg": "El cuerpo debe ser un objeto JSON.", "type": "value_error"}]
    errores: list[dict[str, Any]] = []

    def texto(campo: str, obligatorio: bool = True) -> str | None:
        v = datos.get(campo)
        if v is None or (isinstance(v, str) and not v.strip()):
            if obligatorio:
                errores.append(_error(campo, f"El campo «{campo}» es obligatorio."))
            return None
        if not isinstance(v, str):
            errores.append(_error(campo, f"El campo «{campo}» debe ser un texto."))
            return None
        return v.strip()

    flujo, sitio, ruta, nombre = texto("flujo_url"), texto("sitio_url"), texto("ruta"), texto("nombre", False)

    if flujo is not None:
        try:
            u = urlsplit(flujo)
            host, puerto = (u.hostname or "").lower(), u.port
        except ValueError:
            u, host, puerto = None, "", None
        local = host in {"127.0.0.1", "localhost"} and host in cfg.flujo_hosts  # solo si se habilitó para pruebas
        if u is None or not host or u.username or u.password or any(c in flujo for c in " \r\n\t\\"):
            errores.append(_error("flujo_url", "La URL del flujo no es válida."))
        elif not (local or any(host.endswith("." + h) for h in cfg.flujo_hosts)):
            errores.append(_error("flujo_url", "La URL del flujo debe ser de Power Automate (*.logic.azure.com o *.powerplatform.com)."))
        elif u.scheme != "https" and not (local and u.scheme == "http"):
            errores.append(_error("flujo_url", "La URL del flujo debe usar HTTPS."))
        elif puerto not in (None, 443) and not local:
            errores.append(_error("flujo_url", "La URL del flujo no es válida."))

    ruta_sitio = None
    if sitio is not None:
        try:
            u = urlsplit(sitio)
            host, puerto = (u.hostname or "").lower(), u.port
            ruta_sitio = unquote(u.path).rstrip("/").lower()
        except ValueError:
            host, puerto, u = "", None, None
        if u is None or u.scheme != "https" or not host.endswith(".sharepoint.com") or u.username or u.password or puerto not in (None, 443):
            errores.append(_error("sitio_url", "La URL del sitio debe ser HTTPS y de SharePoint (*.sharepoint.com)."))
            ruta_sitio = None
        elif len([p for p in ruta_sitio.split("/") if p]) != 2 or ruta_sitio.split("/")[1] not in {"sites", "teams"}:
            errores.append(_error("sitio_url", "La URL del sitio debe ser del tipo https://<tenant>.sharepoint.com/sites/<sitio>."))
            ruta_sitio = None

    if ruta is not None:
        baja = ruta.lower()
        if not baja.startswith(("/sites/", "/teams/")):
            errores.append(_error("ruta", "La ruta debe empezar por /sites/ o /teams/."))
        elif ".." in ruta or "%2e" in baja or "\\" in ruta or any(ord(c) < 32 for c in ruta):
            errores.append(_error("ruta", "La ruta no es válida (no puede contener «..», «\\» ni caracteres de control)."))
        elif len([p for p in ruta.split("/") if p]) < 3:
            errores.append(_error("ruta", "La ruta debe apuntar a la carpeta de la convocatoria: /sites/<sitio>/<biblioteca>/<carpeta>."))
        elif ruta_sitio and not (baja.rstrip("/") == ruta_sitio or baja.startswith(ruta_sitio + "/")):
            errores.append(_error("ruta", "La ruta no pertenece al sitio indicado en sitio_url."))

    if nombre is not None and len(nombre) > 200:
        errores.append(_error("nombre", "El nombre no puede superar los 200 caracteres."))
    if errores:
        return None, errores
    assert flujo and sitio and ruta
    ruta = ruta.rstrip("/")
    return Solicitud(flujo, sitio.rstrip("/"), ruta, nombre or ruta.rsplit("/", 1)[-1]), []


# --- jerarquía (port de normalizeConvocatoria del mockup, vd-conector.js) -----------------------------


def _suave(v: Any) -> str | None:
    """Texto de un campo informativo: SharePoint lo entrega como texto, objeto o lista; lo ilegible queda vacío."""
    if isinstance(v, str):
        return v
    if isinstance(v, list):
        return _suave(v[0]) if v else None
    if isinstance(v, dict):
        return next((v[k] for k in ("Label", "Value", "Title", "Description") if isinstance(v.get(k), str)), None)
    return None


def _orden(texto: str) -> tuple[str, str]:
    """Orden estable por nombre sin tildes ni mayúsculas (aproxima la colación del español)."""
    sin_tildes = "".join(c for c in unicodedata.normalize("NFD", texto) if not unicodedata.combining(c))
    return sin_tildes.casefold(), texto


def armar_jerarquia(crudo: Any, ruta_solicitada: str) -> dict[str, Any]:
    """Cuerpo de `listDeepest` -> {ruta, cargos: [{nombre, postulantes: [{nombre, ruta, expediente_id, generico, archivos}]}]}.

    Jerarquía relativa a la carpeta de la convocatoria: nivel 1 = cargo, nivel 2 = postulante y los archivos cuelgan del
    postulante a cualquier profundidad. Es postulante toda carpeta de 2 segmentos y también cada par cargo/postulante que se
    infiere de un archivo de 3 o más segmentos (carpeta no listada). Se ignoran los elementos fuera de la ruta, los archivos
    con menos de 3 segmentos y los cargos sin postulantes. Las rutas se comparan sin distinguir mayúsculas y los nombres se
    conservan tal como vienen (sin decodificar).
    """
    malo = ErrorFlujo("La respuesta del flujo no tiene una ruta inicial, carpetas o archivos válidos.", "bad_response")
    if not isinstance(crudo, dict):
        raise malo
    inicial = crudo.get("rutaInicial")
    base = (inicial if isinstance(inicial, str) and inicial.strip() else ruta_solicitada).strip().rstrip("/")
    if not base:
        raise malo
    prefijo = base.lower() + "/"

    def lista(valor: Any) -> list[Any]:
        if valor is None:
            return []
        if not isinstance(valor, list):
            raise malo
        return valor

    def relativo(item: Any) -> list[str] | None:
        if not isinstance(item, dict) or not isinstance(item.get("FileLeafRef"), str) or not item["FileLeafRef"].strip() \
                or not isinstance(item.get("FileRef"), str) or not item["FileRef"].strip():
            raise malo
        ref = item["FileRef"].strip()
        return [s for s in ref[len(base) + 1:].split("/") if s] if ref.lower().startswith(prefijo) else None

    personas: dict[str, dict[str, Any]] = {}

    def persona(seg: list[str], carpeta: dict[str, Any] | None) -> dict[str, Any]:
        clave = (seg[0] + "/" + seg[1]).lower()
        if clave not in personas:
            personas[clave] = {"nombre": carpeta["FileLeafRef"] if carpeta else seg[1], "ruta": f"{base}/{seg[0]}/{seg[1]}",
                               "cargo": seg[0], "archivos": [], "_vistos": set()}
        return personas[clave]

    for item in lista(crudo.get("carpetas")):
        seg = relativo(item)
        if seg and len(seg) == 2:
            persona(seg, item)
    for item in lista(crudo.get("archivos")):
        seg = relativo(item)
        if str(item.get("FSObjType")) == "1":
            continue  # una carpeta en la lista de archivos (filtro del flujo mal configurado): no es un archivo
        if seg and len(seg) >= 3:
            p = persona(seg, None)
            ref = item["FileRef"].strip()
            if ref.lower() not in p["_vistos"]:
                p["_vistos"].add(ref.lower())
                p["archivos"].append({"ruta": ref, "nombre": item["FileLeafRef"].strip(), "modificado": _suave(item.get("Modified")) or None})

    cargos: dict[str, dict[str, Any]] = {}
    for p in personas.values():
        p.pop("_vistos")
        p["archivos"].sort(key=lambda a: _orden(a["nombre"]) + (a["ruta"],))
        p["expediente_id"] = id_expediente(p["ruta"])
        p["generico"] = p["nombre"].lower().startswith("documentos_")
        c = cargos.setdefault(p.pop("cargo").lower(), {"nombre": p["ruta"].split("/")[-2], "postulantes": []})
        c["postulantes"].append(p)
    for c in cargos.values():
        c["postulantes"].sort(key=lambda p: _orden(p["nombre"]) + (p["ruta"],))
    return {"ruta": base, "cargos": sorted(cargos.values(), key=lambda c: _orden(c["nombre"]))}


# --- vistas de salida --------------------------------------------------------------------------------


def vista_documento(doc: dict[str, Any]) -> dict[str, Any]:
    """`DocumentoVista`: el Documento sin `paginas[].bloques` ni `paginas[].tablas` (pesan mucho). Conserva ancho y alto."""
    v = dict(doc)
    v["paginas"] = [{k: val for k, val in p.items() if k not in {"bloques", "tablas"}} for p in doc.get("paginas", [])]
    return v


# --- servicio ----------------------------------------------------------------------------------------


class ServicioConvocatorias:
    def __init__(self, cfg: Config, proc: Procesador, grupo: Grupo, almacen: ResultadosJSON, *,
                 transporte: Transporte | None = None, hoy: date | None = None, espera_reintento: float = 1.0) -> None:
        self.cfg, self.proc, self.grupo, self.almacen = cfg, proc, grupo, almacen
        self.transporte, self.hoy, self.espera_reintento = transporte, hoy, espera_reintento
        self._activas: dict[str, _Analisis] = {}
        self._lock = threading.Lock()
        self._detener = threading.Event()
        self._vistas: OrderedDict[str, dict[str, Any]] = OrderedDict()  # DocumentoVista por documento_id (evita releer JSON enormes)

    # --- ciclo de vida -----------------------------------------------------------------------

    def iniciar(self, sol: Solicitud) -> dict[str, Any]:
        cid = id_convocatoria(sol.sitio_url, sol.ruta)
        with self._lock:
            if cid in self._activas:  # ya se está analizando: no se lanza otra
                idx = self.almacen.leer((cid, INDICE)) or {}
                return {"id": cid, "nombre": idx.get("nombre", sol.nombre), "estado": idx.get("estado", "leyendo")}
            self._detener.clear()
            idx = self.almacen.leer((cid, INDICE)) or {"version": VERSION_INDICE, "id": cid, "arbol": []}
            idx.update(nombre=sol.nombre, ruta=sol.ruta, sitio_url=sol.sitio_url, estado="leyendo", error=None,
                       iniciado_en=_ahora(), terminado_en=None)
            idx.setdefault("total", 0)
            self.almacen.escribir((cid, INDICE), idx)
            cliente = ClienteFlujo(sol.flujo_url, sol.sitio_url, transporte=self.transporte, espera_reintento=self.espera_reintento)
            analisis = _Analisis(self, cid, idx, cliente)
            self._activas[cid] = analisis
            analisis.start()
        return {"id": cid, "nombre": sol.nombre, "estado": "leyendo"}

    def cerrar(self) -> None:
        """Detiene los análisis en curso entre archivo y archivo; quedan reanudables (estado `analizando` en disco)."""
        self._detener.set()
        with self._lock:
            activos = list(self._activas.values())
        for a in activos:
            a.join(timeout=10)

    def esperar(self, cid: str, timeout: float = 600) -> None:
        a = self._activas.get(cid)
        if a:
            a.join(timeout)

    # --- consultas ---------------------------------------------------------------------------

    def _indice(self, cid: str) -> dict[str, Any] | None:
        if not id_valido(cid):
            return None
        idx = self.almacen.leer((cid, INDICE))
        if idx and idx.get("estado") in {"leyendo", "analizando"} and cid not in self._activas:
            idx["estado"], idx["error"] = "error", MSG_INTERRUMPIDO  # se cayó el servicio a mitad: no hay nadie analizando
        return idx

    def progreso(self, cid: str) -> dict[str, Any] | None:
        idx = self._indice(cid)
        if idx is None:
            return None
        claves = ("id", "nombre", "ruta", "estado", "total", "procesados", "omitidos", "con_error", "postulantes", "cargos",
                  "iniciado_en", "terminado_en", "error")
        return {k: idx.get(k) for k in claves}

    def resultados(self, cid: str) -> dict[str, Any] | None:
        idx = self._indice(cid)
        if idx is None:
            return None
        cargos = []
        for c in idx.get("arbol", []):
            postulantes = []
            for p in c["postulantes"]:
                base = (cid, c["carpeta"], p["carpeta"])
                documentos = []
                for a in p["archivos"]:
                    documentos.append({
                        "ruta": a["ruta"], "nombre": a["nombre"], "modificado": a.get("modificado"), "estado": a["estado"],
                        "motivo": "El archivo ya no está en SharePoint." if a["estado"] == "retirado" else a.get("motivo"),
                        "documento": self._vista(base, a)})
                postulantes.append({
                    "nombre": p["nombre"], "ruta": p["ruta"], "expediente_id": p["expediente_id"], "generico": p["generico"],
                    "expediente": self.almacen.leer(base + (EXPEDIENTE,)) if p.get("expediente") else None,
                    "documentos": documentos})
            cargos.append({"nombre": c["nombre"], "postulantes": postulantes})
        return {"convocatoria": {k: idx.get(k) for k in ("id", "nombre", "ruta", "estado")}, "cargos": cargos}

    def _vista(self, base: tuple[str, ...], a: dict[str, Any]) -> dict[str, Any] | None:
        if a["estado"] not in {"listo", "retirado"} or not a.get("documento_id") or not a.get("archivo"):
            return None
        did = a["documento_id"]
        with self._lock:
            if did in self._vistas:
                self._vistas.move_to_end(did)
                return self._vistas[did]
        doc = self.almacen.leer(base + (a["archivo"],))
        if doc is None:
            return None
        vista = vista_documento(doc)
        with self._lock:
            self._vistas[did] = vista
            while len(self._vistas) > 3000:
                self._vistas.popitem(last=False)
        return vista

    def documento(self, cid: str, partes: tuple[str, ...]) -> Documento | None:
        d = self.almacen.leer((cid,) + partes)
        return Documento.model_validate(d) if d else None


class _Detenido(Exception):
    """El servicio se está cerrando: el análisis queda a medias, reanudable."""


class _Analisis(threading.Thread):
    """Un análisis de convocatoria (un hilo). El índice `idx` es el estado y se guarda en disco tras cada cambio."""

    def __init__(self, svc: ServicioConvocatorias, cid: str, idx: dict[str, Any], cliente: ClienteFlujo) -> None:
        super().__init__(name=f"convocatoria-{cid}", daemon=True)
        self.svc, self.cid, self.idx, self.cliente = svc, cid, idx, cliente
        self.lock = threading.RLock()
        self.sucios: set[str] = set()  # expedientes cuyo contraste hay que recalcular
        self.pendientes: dict[str, int] = {}
        self.descargas = threading.Semaphore(DESCARGAS_SIMULTANEAS)

    # --- ciclo ----------------------------------------------------------------------------------

    def run(self) -> None:
        try:
            self._leer()
            self._analizar()
            self._terminar("listo")
        except _Detenido:
            log.info("Convocatoria %s detenida con el servicio: se reanuda con un nuevo POST", self.cid)
        except ErrorFlujo as e:
            self._terminar("error", f"No se pudo leer la convocatoria: {e.mensaje}")
        except Exception as e:  # noqa: BLE001 - un error inesperado no debe dejar la convocatoria en «analizando»
            log.error("Error inesperado en la convocatoria %s: %s", self.cid, self.cliente.redactar(
                "".join(traceback.format_exception(type(e), e, e.__traceback__))))
            self._terminar("error", "Error interno al analizar la convocatoria.")
        finally:
            with self.svc._lock:
                self.svc._activas.pop(self.cid, None)

    def _terminar(self, estado: str, error: str | None = None) -> None:
        """Estado final y baja del registro de activas en un solo paso: un POST que llegue justo después ya puede lanzar otro análisis."""
        with self.svc._lock, self.lock:
            if estado == "listo":
                self._recalcular()
            self.idx.update(estado=estado, error=self.cliente.redactar(error) if error else None, terminado_en=_ahora())
            self._guardar()
            self.svc._activas.pop(self.cid, None)

    def _guardar(self) -> None:
        with self.lock:
            self.svc.almacen.escribir((self.cid, INDICE), self.idx)

    def _revisar_detencion(self) -> None:
        if self.svc._detener.is_set():
            raise _Detenido

    # --- lectura y fusión con lo anterior -----------------------------------------------------

    def _leer(self) -> None:
        crudo = self.cliente.listar(self.idx["ruta"])
        self._revisar_detencion()
        arbol = armar_jerarquia(crudo, self.idx["ruta"])
        with self.lock:
            self._fusionar(arbol)
            self._recalcular()
            self.idx["estado"] = "analizando"
            self._guardar()

    def _fusionar(self, nuevo: dict[str, Any]) -> None:
        """Mezcla el árbol leído con el índice anterior: reutiliza lo que no cambió, marca `retirado` lo que desapareció."""
        max_mb = self.svc.cfg.max_mb
        previos_c = {c["nombre"].lower(): c for c in self.idx.get("arbol", [])}
        resultado: list[dict[str, Any]] = []
        for cn in nuevo["cargos"]:
            c = previos_c.pop(cn["nombre"].lower(), None) or {"postulantes": []}
            c.update(nombre=cn["nombre"], vigente=True)
            previos_p = {p["ruta"].lower(): p for p in c["postulantes"]}
            postulantes = []
            for pn in cn["postulantes"]:
                p = previos_p.pop(pn["ruta"].lower(), None) or {"archivos": []}
                p.update(nombre=pn["nombre"], ruta=pn["ruta"], expediente_id=pn["expediente_id"], generico=pn["generico"], vigente=True)
                previos_a = {a["ruta"].lower(): a for a in p["archivos"]}
                archivos = []
                for fn in pn["archivos"]:
                    a = previos_a.pop(fn["ruta"].lower(), None)
                    archivos.append(self._fusionar_archivo(p, a, fn, max_mb))
                for a in previos_a.values():  # ya no está en SharePoint: se conserva su JSON
                    if a["estado"] != "retirado":
                        a["estado_anterior"], a["estado"] = a["estado"], "retirado"
                        self.sucios.add(p["expediente_id"])
                    archivos.append(a)
                p["archivos"] = archivos
                postulantes.append(p)
            postulantes += [self._retirar_postulante(p) for p in previos_p.values()]
            c["postulantes"] = postulantes
            resultado.append(c)
        for c in previos_c.values():  # cargos que ya no existen
            c["vigente"] = False
            c["postulantes"] = [self._retirar_postulante(p) for p in c["postulantes"]]
            resultado.append(c)
        self._asignar_carpetas(resultado)
        self.idx["arbol"] = resultado
        self.idx["ruta_inicial"] = nuevo["ruta"]
        for c in resultado:  # sin nada que hacer en el postulante pero sin contraste escrito: se calcula igual
            for p in c["postulantes"]:
                if not p.get("expediente"):
                    self.sucios.add(p["expediente_id"])

    def _retirar_postulante(self, p: dict[str, Any]) -> dict[str, Any]:
        p["vigente"] = False
        for a in p["archivos"]:
            if a["estado"] != "retirado":
                a["estado_anterior"], a["estado"] = a["estado"], "retirado"
                self.sucios.add(p["expediente_id"])
        return p

    def _fusionar_archivo(self, p: dict[str, Any], a: dict[str, Any] | None, f: dict[str, Any], max_mb: int) -> dict[str, Any]:
        soportado = PurePosixPath(f["nombre"]).suffix.lower() in EXTENSIONES
        eid = p["expediente_id"]
        if a is None:
            a = {"ruta": f["ruta"], "sha256": None, "documento_id": None, "motivo": None, "bytes": None}
            nuevo = True
        else:
            nuevo = False
        cambio = nuevo or a.get("modificado") != f["modificado"]
        a.update(nombre=f["nombre"], modificado=f["modificado"], contado=soportado)
        if not soportado:  # nunca entra al pipeline: se muestra como omitido y no cuenta en los totales
            a.update(estado="omitido", motivo=f"Formato no soportado ({PurePosixPath(f['nombre']).suffix.lower() or 'sin extensión'}).")
            return a
        previo = a.get("estado_anterior") if a.get("estado") == "retirado" else a.get("estado")
        if a.get("estado") == "retirado":
            self.sucios.add(eid)  # vuelve a estar: el contraste debe incluirlo
        a.pop("estado_anterior", None)
        if not cambio and previo == "listo":
            a["estado"] = "listo"
        elif not cambio and previo == "omitido" and not (a.get("limite_mb") and a["limite_mb"] < max_mb):
            a["estado"] = "omitido"  # (si se subió SOCR_MAX_MB, un archivo omitido por tamaño se vuelve a intentar)
        else:
            a["estado"] = "pendiente"
            self.sucios.add(eid)
        return a

    def _asignar_carpetas(self, arbol: list[dict[str, Any]]) -> None:
        """Nombres en disco saneados y sin colisiones; lo ya asignado en ejecuciones anteriores no cambia."""
        nc = asignar_nombres({c["nombre"].lower(): c["nombre"] for c in arbol},
                             {c["nombre"].lower(): c["carpeta"] for c in arbol if c.get("carpeta")}, reservados=[INDICE])
        for c in arbol:
            c["carpeta"] = nc[c["nombre"].lower()]
            np = asignar_nombres({p["ruta"].lower(): p["nombre"] for p in c["postulantes"]},
                                 {p["ruta"].lower(): p["carpeta"] for p in c["postulantes"] if p.get("carpeta")})
            for p in c["postulantes"]:
                p["carpeta"] = np[p["ruta"].lower()]
                na = asignar_nombres({a["ruta"].lower(): a["nombre"] for a in p["archivos"]},
                                     {a["ruta"].lower(): a["archivo"] for a in p["archivos"] if a.get("archivo")},
                                     extension=".json", reservados=[EXPEDIENTE])
                for a in p["archivos"]:
                    a["archivo"] = na[a["ruta"].lower()]

    def _recalcular(self) -> None:
        total = proc = omit = err = 0
        cargos = postulantes = 0
        for c in self.idx["arbol"]:
            cargos += bool(c.get("vigente"))
            for p in c["postulantes"]:
                postulantes += bool(p.get("vigente")) and bool(c.get("vigente"))
                for a in p["archivos"]:
                    if a.get("contado") and a["estado"] != "retirado":
                        total += 1
                        proc += a["estado"] == "listo"
                        omit += a["estado"] == "omitido"
                        err += a["estado"] == "error"
        self.idx.update(total=total, procesados=proc, omitidos=omit, con_error=err, postulantes=postulantes, cargos=cargos)

    # --- análisis ---------------------------------------------------------------------------------

    def _analizar(self) -> None:
        tareas = [(c, p, a) for c in self.idx["arbol"] for p in c["postulantes"] for a in p["archivos"] if a["estado"] == "pendiente"]
        for _, p, _ in tareas:
            self.pendientes[p["expediente_id"]] = self.pendientes.get(p["expediente_id"], 0) + 1
        log.info("Convocatoria %s: %d archivos por analizar", self.cid, len(tareas))
        for c in self.idx["arbol"]:  # lo que cambió sin archivos por procesar (retirados, sin contraste): se contrasta ya
            for p in c["postulantes"]:
                if p["expediente_id"] in self.sucios and not self.pendientes.get(p["expediente_id"]) and c.get("vigente") and p.get("vigente"):
                    self._contrastar(c, p)
        with self.lock:
            self._guardar()
        if not tareas:
            return
        with ThreadPoolExecutor(max(1, self.svc.grupo.n), thread_name_prefix=self.name) as pool:
            futuros = [pool.submit(self._archivo, c, p, a) for c, p, a in tareas]
            for f in futuros:
                f.result()
        self._revisar_detencion()

    def _archivo(self, c: dict[str, Any], p: dict[str, Any], a: dict[str, Any]) -> None:
        try:
            self._revisar_detencion()
            self._procesar(c, p, a)
        except _Detenido:
            return
        except Exception as e:  # noqa: BLE001 - un archivo que falla no detiene la convocatoria
            with self.lock:
                motivo = e.mensaje if isinstance(e, ErrorFlujo) else f"No se pudo procesar el documento ({type(e).__name__})."
                if isinstance(e, ErrorFlujo):
                    motivo = f"No se pudo descargar el archivo: {motivo}"
                else:
                    log.error("Falló un archivo de la convocatoria %s: %s", self.cid, self.cliente.redactar(
                        "".join(traceback.format_exception(type(e), e, e.__traceback__))))
                a.update(estado="error", motivo=self.cliente.redactar(motivo), sha256=None, documento_id=None)
        self._terminar_archivo(c, p)

    def _terminar_archivo(self, c: dict[str, Any], p: dict[str, Any]) -> None:
        with self.lock:
            self.pendientes[p["expediente_id"]] -= 1
            terminado = self.pendientes[p["expediente_id"]] == 0
            self._recalcular()
            self._guardar()
        if terminado and not self.svc._detener.is_set():
            self._contrastar(c, p)

    def _procesar(self, c: dict[str, Any], p: dict[str, Any], a: dict[str, Any]) -> None:
        svc = self.svc
        limite = svc.cfg.max_mb * 1024 * 1024
        with self.descargas:
            r = self.cliente.descargar(a["ruta"], limite)
        if r["contenido"] is None:
            with self.lock:
                a.update(estado="omitido", motivo=r["motivo_omitido"], limite_mb=svc.cfg.max_mb, sha256=None, documento_id=None,
                         bytes=r["bytes_declarados"])
            return
        datos = r["contenido"]
        sha = hashlib.sha256(datos).hexdigest()
        destino = (self.cid, c["carpeta"], p["carpeta"], a["archivo"])
        with self.lock:
            igual = a.get("sha256") == sha and a.get("documento_id") and svc.almacen.existe(destino)
        if igual:  # cambió la fecha pero no el contenido: se reutiliza el resultado
            with self.lock:
                a.update(estado="listo", motivo=None)
            return
        try:
            doc = svc.grupo.enviar(datos=datos, nombre=a["nombre"], expediente=p["expediente_id"], guardar=True, hoy=svc.hoy).result()
        except FormatoNoSoportado as e:
            with self.lock:
                a.update(estado="omitido", motivo=str(e), sha256=None, documento_id=None, bytes=len(datos))
            return
        svc.almacen.escribir(destino, doc.model_dump(mode="json"))
        with self.lock:
            a.update(estado="listo", motivo=None, sha256=sha, documento_id=doc.id, bytes=len(datos))
            a.pop("limite_mb", None)

    def _contrastar(self, c: dict[str, Any], p: dict[str, Any]) -> None:
        """Contraste entre los documentos listos del postulante y `_expediente.json`."""
        svc = self.svc
        try:
            with self.lock:
                archivos = [a["archivo"] for a in p["archivos"] if a["estado"] == "listo"]
            docs = [d for d in (svc.documento(self.cid, (c["carpeta"], p["carpeta"], f)) for f in archivos) if d is not None]
            exp = svc.proc.contrastar(p["expediente_id"], docs)
            svc.almacen.escribir((self.cid, c["carpeta"], p["carpeta"], EXPEDIENTE), exp.model_dump(mode="json"))
            with self.lock:
                p["expediente"] = True
                self.sucios.discard(p["expediente_id"])
                self._guardar()
        except Exception as e:  # noqa: BLE001 - el contraste no debe detener la convocatoria
            log.error("No se pudo calcular el contraste de un postulante de %s: %s", self.cid, self.cliente.redactar(f"{type(e).__name__}: {e}"))
