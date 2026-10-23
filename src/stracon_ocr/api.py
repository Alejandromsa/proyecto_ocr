"""API HTTP para la webapp (FastAPI). Documentación interactiva en /docs.

Arranque: `uv run socr servir` (por defecto http://127.0.0.1:8000).
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from concurrent.futures import Future
from contextlib import asynccontextmanager
from functools import partial
from importlib.resources import files
from typing import Annotated, Any

from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, Response
from starlette.concurrency import run_in_threadpool

from . import __version__, motores, vista
from .config import Config, cargar
from .contrato import CONTRATO_VERSION, Documento, Expediente, esquema_json
from .ingesta import FormatoNoSoportado
from .motores import MotorNoDisponible
from .motores.tesseract import ejecutable as ejecutable_tesseract
from .pipeline import Procesador
from .tipos import TIPOS
from .trabajadores import Grupo

log = logging.getLogger(__name__)
TIPOS_MIME = {"pdf_nativo": "application/pdf", "pdf_escaneado": "application/pdf", "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"}


def crear_app(cfg: Config | None = None) -> FastAPI:
    cfg = cfg or cargar()
    proc = Procesador(cfg)
    repo = proc.repo
    # procesos de trabajo: varios documentos a la vez; los demás esperan en cola sin bloquear la API
    grupo = Grupo(cfg, proc)

    @asynccontextmanager
    async def ciclo(_app: FastAPI):
        await asyncio.to_thread(grupo.calentar)
        yield
        grupo.cerrar()

    app = FastAPI(
        title="stracon-ocr",
        version=__version__,
        description="OCR, forense documental y extracción a JSON (contrato v" + CONTRATO_VERSION + "). "
                    "La API no decide: entrega hallazgos con evidencia para el revisor.",
        lifespan=ciclo,
    )
    app.add_middleware(CORSMiddleware, allow_origins=cfg.cors, allow_methods=["*"], allow_headers=["*"])
    app.state.procesador = proc

    async def _leer(archivo: UploadFile) -> bytes:
        datos = await archivo.read()
        if len(datos) > cfg.max_mb * 1024 * 1024:
            raise HTTPException(413, f"El archivo supera {cfg.max_mb} MB")
        if not datos:
            raise HTTPException(400, "Archivo vacío")
        return datos

    def _error_http(e: Exception) -> HTTPException | None:
        if isinstance(e, FormatoNoSoportado):
            return HTTPException(415, str(e))
        if isinstance(e, MotorNoDisponible):
            return HTTPException(400, str(e))
        return None

    async def _procesar(datos: bytes, nombre: str, expediente: str | None, tipo: str | None, motor: str | None, guardar: bool) -> Documento:
        if tipo and tipo not in TIPOS:
            raise HTTPException(422, f"Tipo desconocido: {tipo}. Válidos: {', '.join(TIPOS)}")
        try:
            return await grupo.procesar(datos=datos, nombre=nombre, expediente=expediente, tipo=tipo, motor=motor, guardar=guardar)
        except Exception as e:
            http = _error_http(e)
            if http:
                raise http from e
            raise

    # --- estado y catálogos ----------------------------------------------------------------------

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    def inicio() -> str:
        return files("stracon_ocr").joinpath("web/index.html").read_text(encoding="utf-8")

    @app.get("/salud", tags=["estado"])
    def salud() -> dict[str, Any]:
        disp = motores.disponibles(cfg)
        return {
            "estado": "ok",
            "version": __version__,
            "contrato": CONTRATO_VERSION,
            "reglas": proc.reglas["version"],
            "motor_por_defecto": cfg.motor,
            "motores": disp,
            "tesseract": ejecutable_tesseract(cfg.tesseract_cmd),
            "llm": cfg.ollama_modelo,
            "db": str(cfg.db),
            "trabajadores": grupo.n,
        }

    @app.get("/tipos", tags=["estado"])
    def tipos() -> list[dict[str, Any]]:
        return [{"clave": t.clave, "nombre": t.nombre, "campos": [{"nombre": c.nombre, "clase": c.clase, "clave": c.clave} for c in t.campos]}
                for t in TIPOS.values()]

    @app.get("/esquema", tags=["estado"])
    def esquema() -> dict[str, Any]:
        return esquema_json()

    # --- documentos ------------------------------------------------------------------------------

    @app.post("/documentos", response_model=Documento, tags=["documentos"],
              summary="Procesa un documento y devuelve el JSON (síncrono, ~2-5 s por página)")
    async def procesar_documento(
        archivo: Annotated[UploadFile, File(description="PDF, imagen (JPG/PNG/HEIC/TIFF), DOCX o DOC")],
        expediente: Annotated[str | None, Form(description="RUT del postulante (agrupa los documentos)")] = None,
        tipo: Annotated[str | None, Form(description="Fuerza el tipo (si no, se clasifica)")] = None,
        motor: Annotated[str | None, Form(description="rapidocr | tesseract | azure")] = None,
        guardar: Annotated[str | None, Form(description="'false' = solo devolver el JSON, sin guardar")] = None,
    ) -> Documento:
        datos = await _leer(archivo)
        # texto y no bool: FastAPI toma un booleano de formulario ausente como False y por defecto se guarda
        si_guardar = (guardar or "true").strip().lower() not in {"false", "0", "no"}
        return await _procesar(datos, archivo.filename or "documento", expediente, tipo, motor, si_guardar)

    @app.get("/documentos", tags=["documentos"], summary="Lista resumida de documentos procesados")
    def listar(expediente: str | None = None, tipo: str | None = None, limite: int = Query(100, le=1000)) -> list[dict[str, Any]]:
        return repo.listar_documentos(expediente, tipo, limite)

    @app.get("/documentos/{doc_id}", tags=["documentos"])
    def obtener(doc_id: str) -> dict[str, Any]:
        d = repo.obtener_documento(doc_id)
        if not d:
            raise HTTPException(404, "Documento no encontrado")
        return d

    @app.post("/documentos/{doc_id}/reprocesar", response_model=Documento, tags=["documentos"],
              summary="Vuelve a procesar el original con la versión y las reglas actuales (reemplaza el resultado)")
    async def reprocesar(doc_id: str) -> Documento:
        d = repo.obtener_documento(doc_id)
        ruta = repo.ruta_archivo(d["archivo"]["sha256"]) if d else None
        if not ruta:
            raise HTTPException(404, "Documento o archivo original no encontrado")
        tipo = d["clasificacion"]["tipo"] if d["clasificacion"]["metodo"] == "manual" else None
        nuevo = await _procesar(ruta.read_bytes(), d["archivo"]["nombre"], d.get("expediente"), tipo, None, True)
        if d.get("expediente"):
            await run_in_threadpool(proc.contrastar, d["expediente"])
        return nuevo

    @app.get("/documentos/{doc_id}/archivo", tags=["documentos"], summary="Archivo original")
    def archivo_original(doc_id: str) -> Response:
        d = repo.obtener_documento(doc_id)
        ruta = repo.ruta_archivo(d["archivo"]["sha256"]) if d else None
        if not ruta:
            raise HTTPException(404, "Archivo no encontrado")
        mime = d["archivo"]["mime_real"] or TIPOS_MIME.get(d["archivo"]["origen"], "application/octet-stream")
        return Response(ruta.read_bytes(), media_type=mime,
                        headers={"Content-Disposition": f'inline; filename="{d["archivo"]["nombre"]}"'})

    @app.get("/documentos/{doc_id}/paginas/{n}/imagen", tags=["documentos"],
             summary="PNG de la página con las cajas de señales (rojo/naranjo/amarillo) y campos (azul)")
    def imagen(doc_id: str, n: int, dpi: int = Query(110, ge=50, le=300), senales: bool = True, campos: bool = True) -> Response:
        d = repo.obtener_documento(doc_id)
        ruta = repo.ruta_archivo(d["archivo"]["sha256"]) if d else None
        if not ruta:
            raise HTTPException(404, "Documento no encontrado")
        try:
            img, escala = vista.imagen_pagina(ruta, d, n, dpi)
        except vista.PaginaNoDisponible as e:
            raise HTTPException(404, str(e)) from e
        return Response(vista.png(vista.dibujar(img, d, n, escala, senales, campos)), media_type="image/png")

    @app.get("/buscar", tags=["documentos"], summary="Búsqueda de texto completo (FTS5, sin tildes)")
    def buscar(q: str, limite: int = Query(50, le=500)) -> list[dict[str, Any]]:
        try:
            return repo.buscar_texto(q, limite)
        except Exception as e:
            raise HTTPException(400, f"Consulta inválida: {e}") from e

    # --- trabajos asíncronos -----------------------------------------------------------------------

    @app.post("/trabajos", tags=["trabajos"], status_code=202,
              summary="Encola uno o más archivos; consultar el avance en GET /trabajos/{id}")
    async def encolar(archivos: Annotated[list[UploadFile], File()], expediente: Annotated[str | None, Form()] = None,
                      tipo: Annotated[str | None, Form()] = None, motor: Annotated[str | None, Form()] = None) -> dict[str, Any]:
        creados = []
        for a in archivos:
            datos = await _leer(a)
            tid = str(uuid.uuid4())
            nombre = a.filename or "documento"
            if tipo and tipo not in TIPOS:
                raise HTTPException(422, f"Tipo desconocido: {tipo}. Válidos: {', '.join(TIPOS)}")
            repo.crear_trabajo(tid, nombre, expediente)
            repo.actualizar_trabajo(tid, estado="en_cola")
            futuro = grupo.enviar(datos=datos, nombre=nombre, expediente=expediente, tipo=tipo, motor=motor, guardar=True)
            futuro.add_done_callback(partial(_terminar, tid, expediente))
            creados.append({"id": tid, "nombre": nombre, "estado": "en_cola"})
        return {"trabajos": creados}

    def _terminar(tid: str, expediente: str | None, futuro: Future) -> None:
        try:
            doc = futuro.result()
            repo.actualizar_trabajo(tid, estado="listo", documento_id=doc.id, intentos=1)
            if expediente:
                proc.contrastar(expediente)
        except Exception as e:  # noqa: BLE001 - se informa en el trabajo
            http = _error_http(e)
            if http is None:
                log.exception("Trabajo %s falló", tid)
            repo.actualizar_trabajo(tid, estado="error", error=http.detail if http else f"{type(e).__name__}: {e}", intentos=1)

    @app.get("/trabajos/{tid}", tags=["trabajos"])
    def trabajo(tid: str, incluir_documento: bool = False) -> dict[str, Any]:
        t = repo.obtener_trabajo(tid)
        if not t:
            raise HTTPException(404, "Trabajo no encontrado")
        if incluir_documento and t.get("documento_id"):
            t["documento"] = repo.obtener_documento(t["documento_id"])
        return t

    # --- expedientes ----------------------------------------------------------------------------

    @app.post("/expedientes/{expediente}/documentos", tags=["expedientes"],
              summary="Procesa varios documentos de un postulante (síncrono) y devuelve el contraste")
    async def procesar_expediente(expediente: str, archivos: Annotated[list[UploadFile], File()],
                                  motor: Annotated[str | None, Form()] = None) -> dict[str, Any]:
        leidos = [(await _leer(a), a.filename or "documento") for a in archivos]
        # en paralelo: cada documento va a un proceso de trabajo libre
        docs = await asyncio.gather(*(_procesar(datos, nombre, expediente, None, motor, True) for datos, nombre in leidos))
        exp = await run_in_threadpool(proc.contrastar, expediente)
        return {"documentos": [{"id": d.id, "nombre": d.archivo.nombre, "tipo": d.clasificacion.tipo, "resumen": d.resumen} for d in docs],
                "expediente": exp.model_dump(mode="json")}

    @app.post("/expedientes/{expediente}/contraste", response_model=Expediente, tags=["expedientes"],
              summary="Recalcula el contraste con los documentos guardados del expediente")
    def contrastar(expediente: str) -> Expediente:
        if not repo.listar_documentos(expediente, None, 1):
            raise HTTPException(404, "El expediente no tiene documentos")
        return proc.contrastar(expediente)

    @app.get("/expedientes/{expediente}", tags=["expedientes"])
    def obtener_expediente(expediente: str) -> dict[str, Any]:
        e = repo.obtener_expediente(expediente)
        if not e:
            raise HTTPException(404, "Expediente sin contraste (usar POST /expedientes/{expediente}/contraste)")
        e["documentos_detalle"] = repo.listar_documentos(expediente, None, 200)
        return e

    return app
