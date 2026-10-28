"""Orquestación: ingesta → parseo → códigos → clasificación → extracción → forense → validación → almacenamiento.

Cada etapa registra su error sin romper el resto: el JSON sale siempre válido, con `errores` si algo falló.
"""
from __future__ import annotations

import importlib.metadata as md
import json
import logging
import time
from collections import Counter
import uuid
from datetime import date, datetime
from pathlib import Path
from typing import Any

import imagehash

from . import __version__, calidad, codigos, contraste, extraccion, forense, ingesta, llm, motores, observaciones, validacion
from .almacen import Repositorio, RepositorioSQLite
from .clasificacion import clasificar
from .config import Config, cargar
from .contrato import (Archivo, Bloque, Clasificacion, Documento, Duplicado, ErrorEtapa, Expediente, OcrComparado, Pagina,
                       Palabra, Senal, Versiones)
from .layout import Pal, agrupar, area, escalar, redondear, solape, union
from .texto import normalizar
from .tipos import TIPOS
from .trabajo import DocTrabajo, PaginaTrabajo

log = logging.getLogger(__name__)
EXTENSIONES = {".pdf", ".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".webp", ".heic", ".heif", ".docx", ".doc"}


def cargar_reglas(ruta: Path) -> dict[str, Any]:
    return json.loads(Path(ruta).read_text(encoding="utf-8"))


def _a_pagina(palabras: list[Pal], escala: float) -> list[Pal]:
    for p in palabras:
        p.bbox = escalar(p.bbox, escala)
    return palabras


def _motivo_ilegible(e: Exception) -> str:
    texto = f"{type(e).__name__}: {e}"
    if "password" in texto.lower() or "encrypt" in texto.lower():
        return "está protegido con contraseña"
    return "está dañado o incompleto"


def _puede_tapar_texto(p: PaginaTrabajo) -> bool:
    """Imagen o rectángulo blanco sobre palabras: solo ahí el OCR del render aporta fuera de la página 1."""
    pagina = p.ancho * p.alto
    tapas = [t for t in [im["bbox"] for im in p.imagenes] + [r["bbox"] for r in p.rellenos if r["blanco"]]
             if area(t) < 0.5 * pagina]  # los fondos de página no tapan nada
    return any(solape(w.bbox, t) > 0.8 for t in tapas for w in p.nativas)


def _similitud(nativas: list[Pal], ocr: list[Pal]) -> float:
    """Fracción de palabras nativas (3+ caracteres) que el OCR también ve."""
    vistas = {normalizar(o.t) for o in ocr}
    texto_ocr = " ".join(vistas)
    objetivo = [normalizar(n.t) for n in nativas if len(n.t) >= 3]
    if not objetivo:
        return 1.0
    return round(sum(1 for t in objetivo if t in vistas or t in texto_ocr) / len(objetivo), 3)


class Procesador:
    def __init__(self, cfg: Config | None = None, repo: Repositorio | None = None, guardar: bool = True) -> None:
        self.cfg = cfg or cargar()
        self.reglas = cargar_reglas(self.cfg.reglas)
        self.repo = repo if repo is not None else (RepositorioSQLite(self.cfg.db) if guardar else None)

    # --- documento ------------------------------------------------------------------------------

    def procesar(self, ruta: Path | str | None = None, *, datos: bytes | None = None, nombre: str | None = None,
                 expediente: str | None = None, tipo: str | None = None, motor: str | None = None,
                 guardar: bool = True, hoy: date | None = None) -> Documento:
        t0 = time.perf_counter()
        ruta = Path(ruta) if ruta else Path(nombre or "documento")
        motor = motor or self.cfg.motor
        obs_previas = []
        try:
            doc = ingesta.cargar(ruta, self.cfg, datos=datos, nombre=nombre)
        except ingesta.FormatoNoSoportado:
            raise
        except Exception as e:  # PDF dañado, con contraseña, imagen truncada: resultado "Observado", no un error
            log.warning("No se pudo abrir %s: %s", nombre or ruta.name, e)
            datos = datos if datos is not None else ruta.read_bytes()
            doc = ingesta.documento_minimo(ruta, datos, nombre or ruta.name)
            doc.errores.append(("ingesta", f"{type(e).__name__}: {e}"))
            obs_previas.append(observaciones.archivo_ilegible(doc.nombre, _motivo_ilegible(e)))
        doc.tiempos["ingesta"] = int((time.perf_counter() - t0) * 1000)
        usados: dict[str, str] = {}

        self._etapa(doc, "parseo", self._parsear, doc, motor, usados)
        cods = self._etapa(doc, "codigos", codigos.leer_codigos, doc) or []
        if tipo and tipo in TIPOS:
            clas = Clasificacion(tipo=tipo, confianza=1.0, metodo="manual")
        else:
            clas = self._etapa(doc, "clasificacion", clasificar, doc) or Clasificacion()
        campos = self._etapa(doc, "extraccion", extraccion.extraer, doc, clas.tipo) or {}
        modelo_llm = None
        if self.cfg.ollama_modelo and clas.tipo in TIPOS:
            faltan = [c for c in TIPOS[clas.tipo].campos if c.nombre not in campos]
            extra = self._etapa(doc, "llm", llm.completar, doc.texto, faltan, clas.tipo, self.cfg) or {}
            campos.update(extra)
            modelo_llm = self.cfg.ollama_modelo if extra else None
        senales: list[Senal] = self._etapa(doc, "forense", forense.analizar, doc, clas.tipo, campos, self.reglas) or []
        valids = self._etapa(doc, "validacion", validacion.validar, clas.tipo, campos, cods, self.reglas, hoy) or []
        for v in valids:
            if v.regla == "qr.coincide_ocr" and v.resultado == "falla":
                # el código lo genera el emisor: si no coincide con lo visible, alguien editó uno de los dos
                senales.append(Senal(id="codigo.no_coincide", familia="integridad", severidad="alta", detalle=v.detalle,
                                     evidencia=v.evidencia))
            if v.regla == "qr.coincide_ocr" and v.evidencia.get("coinciden"):
                senales = _confirmadas_por_codigo(senales, set(v.evidencia["coinciden"]))
        phashes = self._etapa(doc, "phash", self._phashes, doc) or []
        if self.repo is not None:
            dups = self._etapa(doc, "duplicados", self.repo.duplicados, doc.sha256, expediente, phashes,
                               self.reglas["umbrales"]["phash_distancia_pagina"]) or []
            senales += self._senales_duplicado(dups, doc, clas.tipo, expediente)
        obs = obs_previas + (self._etapa(doc, "observaciones", observaciones.analizar, doc, clas, campos, senales, valids,
                                         self.reglas) or [])

        documento = Documento(
            id=str(uuid.uuid4()),
            expediente=expediente,
            archivo=Archivo(nombre=doc.nombre, sha256=doc.sha256, mime_real=doc.mime_real, mime_declarado=doc.mime_declarado,
                            bytes=len(doc.datos), paginas=len(doc.paginas), origen=doc.origen, metadatos=_metadatos(doc)),
            clasificacion=clas,
            paginas=[self._pagina_contrato(p) for p in doc.paginas],
            codigos=cods,
            campos=campos,
            senales=senales,
            observaciones=obs,
            validaciones=valids,
            versiones=Versiones(pipeline=__version__, reglas=self.reglas["version"], motores=usados, modelo_llm=modelo_llm),
            procesado_en=datetime.now().astimezone(),
            duracion_ms=int((time.perf_counter() - t0) * 1000),
            tiempos_ms=doc.tiempos,
            errores=[ErrorEtapa(etapa=e, mensaje=m) for e, m in doc.errores],
        )
        if guardar and self.repo is not None:
            self.repo.guardar_archivo(doc.sha256, doc.datos)
            self.repo.guardar_documento(documento, doc.texto, phashes)
        return documento

    def _etapa(self, doc: DocTrabajo, nombre: str, fn, *args):
        t = time.perf_counter()
        try:
            return fn(*args)
        except Exception as e:  # la etapa falla, el documento sigue
            log.exception("Etapa %s falló en %s", nombre, doc.nombre)
            doc.errores.append((nombre, f"{type(e).__name__}: {e}"))
            return None
        finally:
            doc.tiempos[nombre] = doc.tiempos.get(nombre, 0) + int((time.perf_counter() - t) * 1000)

    def _parsear(self, doc: DocTrabajo, motor: str, usados: dict[str, str]) -> None:
        min_nativas = self.reglas["umbrales"]["palabras_min_pdf_nativo"]
        principal = None
        segundo = None

        def ocr(p: PaginaTrabajo) -> list[Pal]:
            nonlocal principal
            t = time.perf_counter()
            principal = principal or motores.obtener(motor, self.cfg)
            usados[principal.nombre] = principal.version()
            palabras = _a_pagina(principal.leer(p.imagen), p.escala)
            doc.tiempos["ocr"] = doc.tiempos.get("ocr", 0) + int((time.perf_counter() - t) * 1000)
            if not p.nativas:  # el texto inclinado (marca de agua) va aparte, como en el PDF nativo
                p.rotadas = [w for w in palabras if w.girada]
            return [w for w in palabras if not w.girada]

        def girar(p: PaginaTrabajo, img0, ancho0: float, alto0: float, angulo: int) -> None:
            p.imagen = img0.rotate(angulo, expand=True) if angulo else img0
            p.ancho, p.alto = (alto0, ancho0) if angulo in (90, 270) else (ancho0, alto0)

        def orientar(p: PaginaTrabajo) -> None:
            """Foto o escaneo girado. RapidOCR lee el texto girado con buena confianza, pero las líneas quedan
            verticales o en orden inverso y se pierde la estructura (rótulo → valor), así que se detecta la orientación
            reconociendo recortes de palabras en cada orientación. Las cajas quedan en la imagen girada
            (p.rotacion = grados antihorarios aplicados)."""
            img0, ancho0, alto0 = p.imagen, p.ancho, p.alto
            if hasattr(principal, "orientacion"):
                # en una página girada 90° casi todo el texto se ve "inclinado" y cae en p.rotadas; si no domina,
                # lo inclinado es una marca de agua y no sirve de muestra
                muestra = p.ocr + p.rotadas if len(p.rotadas) > len(p.ocr) else p.ocr
                en_px = [Pal(w.t, escalar(w.bbox, 1 / p.escala), w.conf) for w in muestra]
                angulo, puntajes = principal.orientacion(img0, en_px)
                p.calidad["orientacion_puntajes"] = {str(k): v for k, v in puntajes.items()}
                if angulo:
                    girar(p, img0, ancho0, alto0, angulo)
                    p.ocr = ocr(p)
                    p.rotacion = (int(p.rotacion or 0) + angulo) % 360
                    p.calidad["rotacion_corregida"] = angulo  # la vista gira la imagen igual para dibujar las cajas
                return
            # otros motores: si la lectura sale débil se prueban las demás orientaciones y gana la que lee más texto
            u = self.reglas.get("calidad", {})
            confs = [w.conf for w in p.ocr]
            fuerte = len(p.ocr) >= u.get("palabras_debil", 15) and confs and sum(confs) / len(confs) >= u.get("confianza_debil", 0.88)
            if fuerte:
                return
            mejor = (calidad.puntaje_lectura(p.ocr), 0, p.ocr, p.rotadas)
            for angulo in (90, 270, 180):
                girar(p, img0, ancho0, alto0, angulo)
                palabras = ocr(p)
                puntaje = calidad.puntaje_lectura(palabras)
                # otra orientación gana solo si lee claramente más (no por ruido de la misma lectura)
                if puntaje > mejor[0] * 1.3 + 3:
                    mejor = (puntaje, angulo, palabras, p.rotadas)
            _, angulo, p.ocr, p.rotadas = mejor
            girar(p, img0, ancho0, alto0, angulo)
            p.rotacion = (int(p.rotacion or 0) + angulo) % 360
            if angulo:
                p.calidad["rotacion_corregida"] = angulo

        escaneadas = 0
        for p in doc.paginas:
            if p.fuente_texto == "docx":
                p.palabras = p.nativas
                usados["python-docx"] = md.version("python-docx")
            elif p.nativas and len(p.nativas) >= min_nativas:
                p.fuente_texto, p.motor = "nativo", "pdfplumber"
                usados["pdfplumber"] = md.version("pdfplumber")
                p.palabras = p.nativas
                if self.cfg.comparar_ocr and p.imagen is not None and (p.n == 1 or _puede_tapar_texto(p)):
                    p.ocr = ocr(p)
                    p.similitud_ocr = _similitud(p.palabras, p.ocr)
            elif p.imagen is not None:
                escaneadas += 1
                p.fuente_texto = "ocr"
                p.ocr = ocr(p)
                orientar(p)
                p.palabras, p.motor = p.ocr, principal.nombre
                segundo = segundo or motores.segundo_motor(self.cfg, motor)
                if segundo:
                    usados[segundo.nombre] = segundo.version()
                    p.ocr2 = _a_pagina(segundo.leer(p.imagen), p.escala)
                p.calidad = {**p.calidad, **calidad.medir(p)}
            p.segmentos = agrupar(p.palabras, p.n)
        if doc.origen == "pdf_nativo":
            usados["pypdfium2"] = md.version("pypdfium2")
            if doc.paginas and escaneadas == len(doc.paginas):
                doc.origen = "pdf_escaneado"

    def _phashes(self, doc: DocTrabajo) -> list[tuple[int, str]]:
        return [(p.n, str(imagehash.phash(p.imagen))) for p in doc.paginas[:3] if p.imagen is not None]

    def _rut_expediente(self, expediente: str | None, sha256: str) -> str | None:
        """RUT más repetido entre los otros documentos del expediente (sin contar el archivo duplicado): identifica al
        postulante aunque el expediente sea un id opaco y no un RUT."""
        if self.repo is None or not expediente:
            return None
        try:
            docs = self.repo.documentos_de_expediente(expediente)
        except Exception:  # noqa: BLE001 - sin el expediente se informa como antes
            return None
        ruts = Counter(d.campos["rut"].valor_normalizado for d in docs
                       if d.archivo.sha256 != sha256 and "rut" in d.campos and d.campos["rut"].valor_normalizado)
        return ruts.most_common(1)[0][0] if ruts else None

    def _tipo_documento(self, doc_id: str) -> str | None:
        try:
            otro = self.repo.obtener_documento(doc_id) if self.repo is not None else None
        except Exception:  # noqa: BLE001
            otro = None
        return ((otro or {}).get("clasificacion") or {}).get("tipo")

    def _senales_duplicado(self, dups, doc: DocTrabajo | None = None, tipo: str = "desconocido",
                           expediente: str | None = None) -> list[Senal]:
        """Duplicados en otros expedientes.

        - Mismo archivo (SHA-256): alta, salve que los dos expedientes sean del mismo postulante (el RUT más repetido en
          sus otros documentos coincide): el mismo postulante en dos convocatorias o cargos, o el archivo repetido en una
          carpeta de documentos genéricos. Entonces es `info`.
        - Página casi idéntica (pHash): solo en fotos y escaneos de documentos físicos (cédula, licencia) frente a otro
          documento físico. Los certificados del Registro Civil y del Mineduc comparten plantilla y quedan a distancia
          0–2 entre personas distintas aunque sean escaneos (calibración oct 2026: 808 falsas alertas en 133 documentos),
          así que ahí la similitud de página no es evidencia.
        """
        senales = []
        sha = doc.sha256 if doc is not None else ""
        paginas_ocr = {p.n for p in doc.paginas if p.fuente_texto == "ocr"} if doc is not None else set()
        fisicos = set(self.reglas.get("tipos_documento_fisico", []))
        rut_este: str | None = None
        consultado = False
        for d in dups:
            if d.tipo != "sha256":
                n = d.documento.rsplit(" ", 1)[-1]
                if tipo not in fisicos or not n.isdigit() or int(n) not in paginas_ocr:
                    continue
                otro_tipo = self._tipo_documento(d.otro_documento)
                if otro_tipo is not None and otro_tipo not in fisicos:
                    continue
            if not consultado:
                rut_este, consultado = self._rut_expediente(expediente, sha), True
            rut_otro = self._rut_expediente(d.otro_expediente, sha) if rut_este else None
            mismo = bool(rut_este and rut_otro and rut_este == rut_otro)
            del_mismo = " del mismo postulante (sus otros documentos traen el mismo RUT)" if mismo else ""
            if d.tipo == "sha256":
                senales.append(Senal(
                    id="dup.archivo_identico", familia="duplicado", severidad="info" if mismo else "alta",
                    detalle=f"El mismo archivo (SHA-256) ya fue entregado en el expediente {d.otro_expediente}{del_mismo}",
                    evidencia=d.model_dump()))
            else:
                senales.append(Senal(
                    id="dup.pagina_similar", familia="duplicado", severidad="info" if mismo else "media",
                    detalle=(f"La {d.documento} es casi idéntica (pHash a distancia {d.distancia}) a un documento del expediente "
                             f"{d.otro_expediente}{del_mismo}"),
                    evidencia=d.model_dump()))
        return senales

    def _pagina_contrato(self, p: PaginaTrabajo) -> Pagina:
        con_caja = p.fuente_texto != "docx"
        bloques = [
            Bloque(
                bbox=redondear(s.bbox) if con_caja else None,
                texto=s.texto,
                conf=round(s.conf, 3),
                palabras=[Palabra(t=w.t, bbox=redondear(w.bbox) if con_caja else None, conf=round(w.conf, 3),
                                  fuente=w.fuente if p.fuente_texto == "nativo" else None,
                                  tam=w.tam if p.fuente_texto == "nativo" else None) for w in s.palabras],
            )
            for s in p.segmentos
        ]
        if p.rotadas:
            rot = sorted(p.rotadas, key=lambda w: w.orden)
            bloques.append(Bloque(bbox=redondear(union([w.bbox for w in rot])), texto="".join(w.t for w in rot), conf=1.0))
        return Pagina(
            n=p.n, ancho=round(p.ancho, 2), alto=round(p.alto, 2), dpi=p.dpi, rotacion=p.rotacion,
            fuente_texto=p.fuente_texto, motor=p.motor or (p.palabras[0].motor if p.palabras else ""),
            bloques=bloques,
            ocr_comparado=OcrComparado(motor=self.cfg.motor, similitud=p.similitud_ocr) if p.similitud_ocr is not None else None,
            calidad=p.calidad,
        )

    # --- expediente ------------------------------------------------------------------------------

    def procesar_carpeta(self, carpeta: Path | str, expediente: str | None = None, motor: str | None = None,
                         hoy: date | None = None) -> tuple[list[Documento], Expediente]:
        carpeta = Path(carpeta)
        expediente = expediente or carpeta.name
        docs = []
        for f in sorted(carpeta.iterdir()):
            if f.is_file() and f.suffix.lower() in EXTENSIONES:
                docs.append(self.procesar(f, expediente=expediente, motor=motor, hoy=hoy))
        return docs, self.contrastar(expediente, docs)

    def contrastar(self, expediente: str, docs: list[Documento] | None = None) -> Expediente:
        if docs is None:
            docs = self.repo.documentos_de_expediente(expediente) if self.repo is not None else []
        dups = [Duplicado(**s.evidencia) for d in docs for s in d.senales if s.familia == "duplicado"]
        exp = contraste.contrastar(expediente, docs, self.reglas, dups)
        if self.repo is not None:
            self.repo.guardar_expediente(exp)
        return exp


def _confirmadas_por_codigo(senales: list[Senal], campos: set[str]) -> list[Senal]:
    """Una lectura OCR dudosa de un campo que el código del emisor (MRZ con dígito de control, PDF417, QR) confirma no es
    indicio de nada: queda como `info` (calibración oct 2026: la fecha de nacimiento de cédulas fotografiadas, sobre el
    fondo de seguridad, se lee con confianza 0,55 y la MRZ dice lo mismo)."""
    salida = []
    for s in senales:
        if s.familia == "ocr" and s.campo in campos and s.severidad != "info":
            s = s.model_copy(update={"severidad": "info", "detalle": s.detalle + " (el código del emisor confirma el valor)"})
        salida.append(s)
    return salida


def _metadatos(doc: DocTrabajo) -> dict[str, Any]:
    """Metadatos para el revisor (productor, fechas, revisiones, EXIF, propiedades del Word)."""
    m = {}
    for k in ("info", "xmp", "revisiones", "firmas", "docx", "exif"):
        if doc.pdf_info.get(k):
            m[k] = doc.pdf_info[k]
    return m
