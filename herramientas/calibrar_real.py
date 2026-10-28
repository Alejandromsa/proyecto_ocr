"""Calibración con documentos reales: etiqueta esperada por nombre de archivo, procesamiento y métricas agregadas.

Recorre un conjunto con la estructura <cargo>/<postulante>/<archivo>, deriva el tipo esperado de cada archivo por
heurísticas de nombre (con correcciones manuales opcionales), lo procesa con el `Procesador` y calcula:
exactitud de clasificación y matriz de confusión, completitud de campos clave por tipo (con procedencia y confianza),
coherencia del RUT por postulante, estados, señales, observaciones, tiempos y errores; y el contraste por postulante.

Uso:
  python herramientas/calibrar_real.py procesar <conjunto> <salida> [--correcciones c.json] [--trabajadores 2] [--sin-cache]
  python herramientas/calibrar_real.py comparar <metricas_antes.json> <metricas_despues.json>
  python herramientas/calibrar_real.py renderizar <conjunto> <salida> <id> [--pagina 1]
  (también por entorno: SOCR_CALIB_CONJUNTO y SOCR_CALIB_SALIDA)

PRIVACIDAD: el conjunto tiene datos personales. La carpeta de salida (JSON por documento, índice de rutas, PNG, base
SQLite y caché del OCR) debe quedar FUERA del repositorio, y nada de lo que se genera se commitea. Las métricas usan
identificadores opacos (c01_p02_a03 = cargo 1, postulante 2, archivo 3, en orden alfabético) y no copian valores.
El archivo de correcciones (también fuera del repositorio) es un JSON {sha256[:16] o id: {"esperado": tipo,
"tambien": [tipos], "nota": "..."}} para los casos que el nombre no resuelve.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import logging
import os
import pickle
import re
import statistics
import sys
import time
import unicodedata
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import replace
from datetime import date
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

TIPOS_MVP = ("cedula_identidad", "licencia_conducir", "certificado_estudios_medios", "certificado_estudios_profesionales",
             "certificado_antecedentes", "hoja_vida_conductor")
# etiquetas esperadas que no son tipos del MVP: la clasificación correcta es "desconocido" (o el tipo propio, si existe)
OTROS = ("validacion_emisor", "otro")
ABREV = {"cedula_identidad": "CED", "licencia_conducir": "LIC", "certificado_estudios_medios": "EMED",
         "certificado_estudios_profesionales": "EPRO", "certificado_antecedentes": "ANT", "hoja_vida_conductor": "HVC",
         "validacion_emisor": "VAL", "otro": "OTRO", "desconocido": "DESC", "estudios_ambiguo": "EST?"}
# campos clave que se miden por tipo (los obligatorios del tipo y los que usa el contraste)
CAMPOS_CLAVE = {
    "cedula_identidad": ("rut", "nombre", "numero_documento", "fecha_nacimiento", "fecha_vencimiento"),
    "licencia_conducir": ("rut", "nombre", "clases", "fecha_emision", "fecha_vencimiento"),
    "certificado_estudios_medios": ("rut", "nombre", "establecimiento", "anio_egreso"),
    "certificado_estudios_profesionales": ("rut", "nombre", "institucion", "titulo", "fecha_titulacion"),
    "certificado_antecedentes": ("rut", "nombre", "folio", "codigo_verificacion", "fecha_emision", "resultado", "fines"),
    "hoja_vida_conductor": ("rut", "nombre", "folio", "codigo_verificacion", "clases", "fecha_emision"),
    "validacion_emisor": ("rut", "folio", "codigo_verificacion"),
}


# campos que se informan pero no cuentan en el total (no vienen en todos los documentos reales del tipo)
CAMPOS_OPCIONALES = {"hoja_vida_conductor": ("fecha_control",), "licencia_conducir": ("folio", "municipalidad"),
                     "certificado_estudios_profesionales": ("numero_certificado", "fecha_emision")}


# --- etiquetas esperadas -------------------------------------------------------------------------

def _norm(s: str) -> str:
    s = "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c)).lower()
    s = re.sub(r"\.pdf$", "", s)
    return " " + re.sub(r"[^a-z0-9]+", " ", s).strip() + " "


def etiqueta_por_nombre(nombre: str) -> tuple[str, list[str]]:
    """Tipo esperado según el nombre del archivo (ruidoso: abreviaturas, errores de tipeo, tipos mezclados).
    Devuelve (tipo, también) donde `también` son otros tipos mencionados en el mismo archivo."""
    n = _norm(nombre)
    tiene = lambda *ps: any(re.search(p, n) for p in ps)  # noqa: E731
    if tiene(r" validaci", r" verificaci"):
        return "validacion_emisor", []
    if tiene(r" foto ") and tiene(r" carnet"):
        return "otro", []
    if tiene(r" resoluci"):
        return "otro", []
    cedula = tiene(r" cedula", r" carnet", r" ci ")
    if tiene(r" licencia (de )?(ensenan[sz]a|educacion)? ?media", r" ensenan[sz]a media", r" educacion media", r" 4to medio",
             r" cuarto medio", r" licencia media"):
        return "certificado_estudios_medios", []
    if tiene(r" hoja (de )?vida", r" hvid", r" hvc", r" hdvc?", r" hdvi", r"^ \d+ ?hdvc"):
        return "hoja_vida_conductor", []
    if tiene(r" ant(ec|eced|ecedentes?)?\b", r" ant fe", r" cert ant", r" cet ant", r" antecedente"):
        return "certificado_antecedentes", []
    if tiene(r" licencia"):
        return "licencia_conducir", ["cedula_identidad"] if cedula else []
    if cedula:
        return "cedula_identidad", []
    if tiene(r" titulo", r" profesional", r" profecional", r" universidad", r" superior", r" ingenier", r" inacap"):
        return "certificado_estudios_profesionales", []
    if tiene(r" estudios?", r" educacion"):
        return "estudios_ambiguo", []  # "Certificado de estudios": medio o superior, se resuelve mirando el documento
    return "otro", []


# --- conjunto ------------------------------------------------------------------------------------

def _extendida(p: Path) -> Path:
    """En Windows, una carpeta que termina en punto o espacio ("K.V.") no se puede abrir con la ruta normal (Win32 recorta
    el punto final): se usa la ruta extendida \\\\?\\ que la respeta."""
    if os.name != "nt":
        return p
    s = str(p.resolve()) if not str(p).startswith("\\\\?\\") else str(p)
    return Path(s if s.startswith("\\\\?\\") else "\\\\?\\" + s)


def _hijos(p: Path) -> list[Path]:
    with os.scandir(_extendida(p)) as it:
        return sorted((Path(e.path) for e in it), key=lambda x: x.name.lower())  # como WindowsPath: sin distinguir mayúsculas


def recorrer(conjunto: Path) -> list[dict[str, Any]]:
    """Archivos en orden estable con id opaco: c<cargo>_p<postulante>_a<archivo>."""
    from stracon_ocr.pipeline import EXTENSIONES

    filas = []
    cargos = [d for d in _hijos(conjunto) if d.is_dir()]
    for ic, cargo in enumerate(cargos, 1):
        for ip, post in enumerate([d for d in _hijos(cargo) if d.is_dir()], 1):
            archivos = [f for f in _hijos(post) if f.is_file() and f.suffix.lower() in EXTENSIONES]
            for ia, f in enumerate(archivos, 1):
                datos = f.read_bytes()
                tipo, tambien = etiqueta_por_nombre(f.name)
                generica = "documentos" in _norm(post.name)  # carpetas «Documentos_Genéricos/Sin_Clasificar»
                filas.append({
                    "id": f"c{ic:02d}_p{ip:02d}_a{ia:02d}", "cargo": cargo.name, "postulante": f"c{ic:02d}_p{ip:02d}",
                    "carpeta_generica": generica, "ruta": str(f), "relativa": str(f).split(conjunto.name, 1)[-1].lstrip("\\/"),
                    "sha": hashlib.sha256(datos).hexdigest(), "bytes": len(datos), "esperado_nombre": tipo,
                    "esperado": tipo, "tambien": tambien, "rut_nombre": _rut_en_nombre(f.name), "folio_nombre": _folio_en_nombre(f.name),
                })
    return filas


def _rut_en_nombre(nombre: str) -> str | None:
    """ANT_<folio>_<cuerpo RUT>.pdf / HVID_<folio>_<cuerpo RUT>.pdf (nombre con que descarga el Registro Civil)."""
    m = re.search(r"(?:ANT|HVID)(?:_FE)?_(\d{12})_(\d{7,8})", nombre, re.I)
    return m.group(2) if m else None


def _folio_en_nombre(nombre: str) -> str | None:
    m = re.search(r"(?:ANT|HVID)(?:_FE)?_(\d{12})_\d{7,8}", nombre, re.I)
    return m.group(1) if m else None


def aplicar_correcciones(filas: list[dict], ruta: Path | None) -> int:
    if not ruta or not ruta.exists():
        return 0
    corr = json.loads(ruta.read_text(encoding="utf-8"))
    n = 0
    for f in filas:
        c = corr.get(f["sha"][:16]) or corr.get(f["id"])
        if c:
            f["esperado"] = c.get("esperado", f["esperado"])
            f["tambien"] = c.get("tambien", f["tambien"])
            f["nota"] = c.get("nota")
            n += 1
    return n


# --- procesamiento con caché del parseo ------------------------------------------------------------

_proc = None


def _iniciar(cfg, cache: str | None) -> None:
    """Proceso de trabajo: un Procesador por proceso (como trabajadores.Grupo) con caché opcional del parseo/OCR."""
    global _proc
    from stracon_ocr.layout import agrupar
    from stracon_ocr.motores import obtener
    from stracon_ocr.pipeline import Procesador

    logging.basicConfig(level=logging.ERROR)
    cache_dir = Path(cache) if cache else None

    class ProcesadorCache(Procesador):
        """El parseo (render + OCR) se guarda por SHA-256, DPI y motor: así se puede iterar sobre las reglas de
        clasificación, extracción y forense sin repetir el OCR. La agrupación en segmentos (layout) se rehace siempre."""

        def _parsear(self, doc, motor, usados):
            if cache_dir is None:
                return super()._parsear(doc, motor, usados)
            f = cache_dir / f"{doc.sha256[:24]}_{self.cfg.dpi}_{motor}.pkl"
            if f.exists():
                estado = pickle.loads(f.read_bytes())
                if len(estado["paginas"]) == len(doc.paginas):
                    for p, e in zip(doc.paginas, estado["paginas"]):
                        angulo = e.pop("angulo")
                        nativas = e.pop("palabras_nativas")
                        for k, v in e.items():
                            setattr(p, k, v)
                        if angulo and p.imagen is not None:
                            p.imagen = p.imagen.rotate(angulo, expand=True)
                        p.palabras = p.nativas if nativas else p.ocr
                        p.segmentos = agrupar(p.palabras, p.n)
                    doc.origen = estado["origen"]
                    usados.update(estado["usados"])
                    doc.tiempos["ocr_cache"] = estado.get("ms_ocr", 0)
                    return None
            r = super()._parsear(doc, motor, usados)
            estado = {"origen": doc.origen, "usados": dict(usados), "ms_ocr": doc.tiempos.get("ocr", 0), "paginas": [
                {"fuente_texto": p.fuente_texto, "motor": p.motor, "ocr": p.ocr, "ocr2": p.ocr2, "rotadas": p.rotadas,
                 "calidad": p.calidad, "similitud_ocr": p.similitud_ocr, "rotacion": p.rotacion, "ancho": p.ancho, "alto": p.alto,
                 "angulo": int(p.calidad.get("rotacion_corregida", 0) or 0), "palabras_nativas": p.palabras is p.nativas}
                for p in doc.paginas]}
            f.write_bytes(pickle.dumps(estado))
            return r

    _proc = ProcesadorCache(cfg)
    if _proc.repo is not None:
        _proc.repo.guardar_archivo = lambda *a, **k: None  # los originales no se copian
    motor = obtener(cfg.motor, cfg)
    if hasattr(motor, "_cargar"):
        motor._cargar()


def _procesar(ruta: str, expediente: str, hoy: str) -> tuple[str, int]:
    t = time.perf_counter()
    doc = _proc.procesar(ruta, expediente=expediente, hoy=date.fromisoformat(hoy))
    return doc.model_dump_json(), int((time.perf_counter() - t) * 1000)


def procesar(args) -> None:
    from stracon_ocr.config import cargar
    from stracon_ocr.contrato import Documento
    from stracon_ocr.trabajadores import hilos_por_trabajador

    conjunto, salida = Path(args.conjunto), Path(args.salida)
    if _dentro_del_repo(salida):
        sys.exit("La salida no puede quedar dentro del repositorio (datos personales).")
    (salida / "docs").mkdir(parents=True, exist_ok=True)
    cache = salida / "cache_ocr"
    cache.mkdir(exist_ok=True)
    filas = recorrer(conjunto)
    n_corr = aplicar_correcciones(filas, Path(args.correcciones) if args.correcciones else salida / "correcciones.json")
    if args.solo:
        filas = [f for f in filas if any(f["id"].startswith(s) for s in args.solo.split(","))]
    (salida / "indice.json").write_text(json.dumps(filas, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{len(filas)} archivos, {n_corr} correcciones manuales")

    cfg = cargar()
    db = salida / "calibracion.db"
    for sufijo in ("", "-wal", "-shm"):
        Path(str(db) + sufijo).unlink(missing_ok=True)
    cfg.db = db
    n = max(1, args.trabajadores)
    cfg = replace(cfg, trabajadores=0, hilos_ocr=cfg.hilos_ocr if cfg.hilos_ocr > 0 else hilos_por_trabajador(n))
    hoy = args.hoy or date.today().isoformat()
    t0 = time.perf_counter()
    resultados: dict[str, dict] = {}
    with ProcessPoolExecutor(n, initializer=_iniciar, initargs=(cfg, None if args.sin_cache else str(cache))) as pool:
        futuros = {pool.submit(_procesar, f["ruta"], f["postulante"], hoy): f for f in filas}
        for k, fut in enumerate(as_completed(futuros), 1):
            f = futuros[fut]
            try:
                js, ms = fut.result()
                (salida / "docs" / f"{f['id']}.json").write_text(js, encoding="utf-8")
                resultados[f["id"]] = {"doc": json.loads(js), "ms": ms}
            except Exception as e:  # noqa: BLE001 - se informa y sigue
                resultados[f["id"]] = {"error": f"{type(e).__name__}: {e}", "ms": 0}
            if k % 10 == 0 or k == len(filas):
                print(f"  {k}/{len(filas)} ({time.perf_counter() - t0:.0f} s)", flush=True)
    total = time.perf_counter() - t0
    # contraste por postulante (id opaco de carpeta, no RUT)
    expedientes = contrastar(filas, resultados, cfg)
    metricas = calcular(filas, resultados, expedientes, total, n, cfg.hilos_ocr, cfg.dpi)
    metricas["etiqueta"] = args.etiqueta
    nombre = f"metricas{('_' + args.etiqueta) if args.etiqueta else ''}.json"
    (salida / nombre).write_text(json.dumps(metricas, ensure_ascii=False, indent=1), encoding="utf-8")
    (salida / "expedientes.json").write_text(json.dumps(expedientes, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    escribir_csv(salida / "resumen.csv", filas, resultados)
    imprimir(metricas)
    print(f"\nMétricas: {salida / nombre}")


def recalcular(args) -> None:
    """Métricas a partir de los JSON ya generados (p. ej. tras corregir etiquetas), sin volver a procesar."""
    from stracon_ocr.config import cargar

    conjunto, salida = Path(args.conjunto), Path(args.salida)
    filas = recorrer(conjunto)
    aplicar_correcciones(filas, Path(args.correcciones) if args.correcciones else salida / "correcciones.json")
    carpeta = salida / args.docs
    resultados = {}
    for f in filas:
        r = carpeta / f"{f['id']}.json"
        if r.exists():
            d = json.loads(r.read_text(encoding="utf-8"))
            resultados[f["id"]] = {"doc": d, "ms": d["duracion_ms"]}
    filas = [f for f in filas if f["id"] in resultados]
    cfg = cargar()
    expedientes = contrastar(filas, resultados, cfg)
    metricas = calcular(filas, resultados, expedientes, sum(r["ms"] for r in resultados.values()) / 1000 / max(1, args.trabajadores),
                        args.trabajadores, cfg.hilos_ocr, cfg.dpi)
    metricas["etiqueta"] = args.etiqueta
    nombre = f"metricas{('_' + args.etiqueta) if args.etiqueta else ''}.json"
    (salida / nombre).write_text(json.dumps(metricas, ensure_ascii=False, indent=1), encoding="utf-8")
    imprimir(metricas)


def _dentro_del_repo(p: Path) -> bool:
    raiz = Path(__file__).resolve().parents[1]
    try:
        p.resolve().relative_to(raiz)
        return True
    except ValueError:
        return False


def contrastar(filas, resultados, cfg) -> dict[str, Any]:
    from stracon_ocr import contraste
    from stracon_ocr.contrato import Documento
    from stracon_ocr.pipeline import cargar_reglas

    reglas = cargar_reglas(cfg.reglas)
    por_post: dict[str, list] = defaultdict(list)
    for f in filas:
        r = resultados.get(f["id"], {})
        if "doc" in r and not f["carpeta_generica"]:
            por_post[f["postulante"]].append(Documento.model_validate(r["doc"]))
    salida = {}
    for post, docs in por_post.items():
        exp = contraste.contrastar(post, docs, reglas, [])
        salida[post] = {"documentos": len(docs), "tipos": sorted({d.clasificacion.tipo for d in docs}),
                        "discrepancias": [d.model_dump() for d in exp.discrepancias], "identidad": exp.identidad.model_dump(),
                        "faltantes": exp.faltantes, "estado": exp.estado}
    return salida


# --- métricas ------------------------------------------------------------------------------------

def _pct(a: int, b: int) -> float | None:
    return round(100 * a / b, 1) if b else None


def _percentil(xs: list[float], q: float) -> float | None:
    if not xs:
        return None
    xs = sorted(xs)
    i = min(len(xs) - 1, max(0, int(round(q * (len(xs) - 1)))))
    return xs[i]


def _rut_mayoritario(filas, resultados) -> dict[str, str]:
    """RUT de referencia por postulante: el más repetido entre sus documentos (y el del nombre del archivo, si viene)."""
    votos: dict[str, Counter] = defaultdict(Counter)
    for f in filas:
        if f["rut_nombre"]:
            votos[f["postulante"]][f["rut_nombre"]] += 2
        d = resultados.get(f["id"], {}).get("doc")
        rut = ((d or {}).get("campos", {}).get("rut") or {}).get("valor_normalizado")
        if rut:
            votos[f["postulante"]][rut.split("-")[0]] += 1
    return {p: c.most_common(1)[0][0] for p, c in votos.items() if c}


def _nombre_referencia(filas, resultados) -> dict[str, str]:
    """Nombre de referencia por postulante: el de sus certificados del Registro Civil en PDF nativo (texto del emisor)."""
    ref = {}
    for f in filas:
        d = resultados.get(f["id"], {}).get("doc")
        if not d or f["carpeta_generica"] or d["archivo"]["origen"] != "pdf_nativo":
            continue
        if d["clasificacion"]["tipo"] in ("certificado_antecedentes", "hoja_vida_conductor") and "nombre" in d["campos"]:
            ref.setdefault(f["postulante"], d["campos"]["nombre"]["valor"])
    return ref


def _mismo_nombre(a: str, b: str) -> bool:
    from rapidfuzz import fuzz
    from stracon_ocr.texto import normalizar_nombre

    from stracon_ocr.contraste import similitud_nombres

    return similitud_nombres(a, b)[0] >= 0.9 or fuzz.token_set_ratio(normalizar_nombre(a), normalizar_nombre(b)) >= 90


def calcular(filas, resultados, expedientes, total_s, trabajadores, hilos, dpi) -> dict[str, Any]:
    tipos_obtenidos = set()
    confusion: dict[str, Counter] = defaultdict(Counter)
    aciertos_mvp = total_mvp = aciertos_todos = total_todos = 0
    fallos = []
    completitud: dict[str, dict[str, Counter]] = defaultdict(lambda: defaultdict(Counter))
    docs_por_tipo = Counter()
    estados, estados_mvp = Counter(), Counter()
    senales, senales_ma = Counter(), Counter()
    docs_con_senal = defaultdict(set)
    observaciones = Counter()
    validaciones = Counter()
    errores = Counter()
    tiempos = []
    lentos = []
    ilegibles = []
    origenes = Counter()
    rut_ref = _rut_mayoritario(filas, resultados)
    nombre_ref = _nombre_referencia(filas, resultados)
    nombre_ok: dict[str, Counter] = defaultdict(Counter)
    errores_nombre = []
    rut_ok = Counter()
    ambiguos = []
    for f in filas:
        r = resultados.get(f["id"], {})
        if "error" in r:
            errores[f"excepcion:{r['error'][:60]}"] += 1
            continue
        d = r["doc"]
        esperado = f["esperado"]
        obtenido = d["clasificacion"]["tipo"]
        tipos_obtenidos.add(obtenido)
        confusion[esperado][obtenido] += 1
        if esperado == "estudios_ambiguo":
            ambiguos.append(f["id"])
        correcto_otro = obtenido in ("desconocido", esperado)
        # un archivo con varios documentos (licencia y cédula) vale clasificado por cualquiera de ellos
        ok = obtenido in [esperado, *f.get("tambien", [])] if esperado in TIPOS_MVP else correcto_otro
        if esperado in TIPOS_MVP:
            total_mvp += 1
            aciertos_mvp += ok
        if esperado != "estudios_ambiguo":
            total_todos += 1
            aciertos_todos += ok
            if not ok:
                fallos.append({"id": f["id"], "cargo": f["cargo"], "esperado": esperado, "obtenido": obtenido,
                               "confianza": d["clasificacion"]["confianza"], "origen": d["archivo"]["origen"],
                               "alternativas": d["clasificacion"].get("alternativas", [])})
        origenes[d["archivo"]["origen"]] += 1
        estados[d["resumen"]["estado"]] += 1
        if esperado in TIPOS_MVP:
            estados_mvp[d["resumen"]["estado"]] += 1
        for s in d["senales"]:
            senales[f"{s['id']}:{s['severidad']}"] += 1
            if s["severidad"] in ("media", "alta"):
                senales_ma[f"{s['id']}:{s['severidad']}"] += 1
                docs_con_senal[f"{s['id']}:{s['severidad']}"].add(f["id"])
        for o in d["observaciones"]:
            observaciones[o["id"]] += 1
        for v in d["validaciones"]:
            if v["resultado"] == "falla":
                validaciones[v["regla"]] += 1
        for e in d["errores"]:
            errores[f"{e['etapa']}:{e['mensaje'][:60]}"] += 1
        if any(o["id"] == "archivo.ilegible" for o in d["observaciones"]):
            ilegibles.append(f["id"])
        tiempos.append(r["ms"])
        lentos.append((r["ms"], f["id"], d["archivo"]["paginas"], d["archivo"]["origen"], round(f["bytes"] / 1e6, 2)))
        # completitud de campos: sobre el tipo esperado y solo si se clasificó bien (si no, se mide la clasificación)
        tipo_medir = esperado if esperado in CAMPOS_CLAVE else None
        if tipo_medir and obtenido == tipo_medir:
            docs_por_tipo[tipo_medir] += 1
            ref_n = nombre_ref.get(f["postulante"]) if not f["carpeta_generica"] else None
            leido_n = (d["campos"].get("nombre") or {}).get("valor")
            if ref_n and leido_n:
                bien = _mismo_nombre(ref_n, leido_n)
                nombre_ok[tipo_medir]["bien" if bien else "mal"] += 1
                if not bien:
                    errores_nombre.append(f["id"])
            for campo in CAMPOS_CLAVE[tipo_medir] + CAMPOS_OPCIONALES.get(tipo_medir, ()):
                c = d["campos"].get(campo)
                comp = completitud[tipo_medir][campo]
                comp["docs"] += 1
                if c and c.get("valor_normalizado") not in (None, "", []):
                    comp["presente"] += 1
                    comp[f"metodo:{c['metodo']}"] += 1
                    comp["conf_suma_x1000"] += int(1000 * (c.get("conf") or 0))
        # RUT del postulante: válido y coincide con el de referencia (no con el de un tercero)
        rut = (d["campos"].get("rut") or {}).get("valor_normalizado")
        if esperado in TIPOS_MVP and obtenido == esperado:
            ref = None if f["carpeta_generica"] else rut_ref.get(f["postulante"])
            ref = f["rut_nombre"] or ref
            if rut:
                rut_ok["leido"] += 1
                if ref and rut.split("-")[0] == ref:
                    rut_ok["coincide_referencia"] += 1
                elif ref:
                    rut_ok["distinto_referencia"] += 1
            folio = (d["campos"].get("folio") or {}).get("valor_normalizado")
            if f["folio_nombre"]:
                rut_ok["folio_en_nombre"] += 1
                rut_ok["folio_coincide_nombre"] += folio == f["folio_nombre"]
    comp_tabla = {}
    for t, campos in completitud.items():
        comp_tabla[t] = {}
        for campo, c in campos.items():
            metodos = {k.split(":", 1)[1]: v for k, v in c.items() if k.startswith("metodo:")}
            comp_tabla[t][campo] = {"presente_pct": _pct(c["presente"], c["docs"]), "docs": c["docs"], "metodos": metodos,
                                    "conf_media": round(c["conf_suma_x1000"] / 1000 / c["presente"], 3) if c["presente"] else None}
        contables = {k: c for k, c in campos.items() if k not in CAMPOS_OPCIONALES.get(t, ())}
        presentes = sum(c["presente"] for c in contables.values())
        total = sum(c["docs"] for c in contables.values())
        comp_tabla[t]["_global_pct"] = _pct(presentes, total)
    exactitud_por_tipo = {}
    for t in TIPOS_MVP + OTROS:
        fila = confusion.get(t, Counter())
        n = sum(fila.values())
        if n:
            ok = n - sum(1 for x in fallos if x["esperado"] == t)
            exactitud_por_tipo[t] = {"n": n, "aciertos": ok, "pct": _pct(ok, n)}
    discrepancias = Counter()
    for e in expedientes.values():
        for x in e["discrepancias"]:
            discrepancias[f"{x['campo']}:{x['severidad']}"] += 1
    return {
        "fecha": date.today().isoformat(), "documentos": len(filas), "dpi": dpi, "trabajadores": trabajadores, "hilos_ocr": hilos,
        "clasificacion": {"exactitud_mvp_pct": _pct(aciertos_mvp, total_mvp), "n_mvp": total_mvp,
                          "exactitud_todos_pct": _pct(aciertos_todos, total_todos), "n_todos": total_todos,
                          "por_tipo": exactitud_por_tipo, "ambiguos_sin_corregir": ambiguos,
                          "confusion": {k: dict(v) for k, v in sorted(confusion.items())}, "fallos": fallos},
        "campos": comp_tabla, "docs_medidos_por_tipo": dict(docs_por_tipo), "rut": dict(rut_ok),
        "nombre_vs_referencia": {t: dict(c) for t, c in nombre_ok.items()}, "nombres_distintos": errores_nombre,
        "estados": dict(estados), "estados_mvp": dict(estados_mvp), "origenes": dict(origenes),
        "senales": dict(senales.most_common()), "senales_media_alta": dict(senales_ma.most_common()),
        "docs_con_senal_media_alta": {k: sorted(v) for k, v in docs_con_senal.items()},
        "observaciones": dict(observaciones.most_common()), "validaciones_fallidas": dict(validaciones.most_common()),
        "errores": dict(errores), "ilegibles": ilegibles,
        "tiempos": {"total_s": round(total_s, 1), "p50_ms": _percentil(tiempos, 0.5), "p95_ms": _percentil(tiempos, 0.95),
                    "max_ms": max(tiempos, default=None), "media_ms": round(statistics.mean(tiempos)) if tiempos else None,
                    "docs_por_min": round(60 * len(tiempos) / total_s, 1) if total_s else None,
                    "mas_lentos": [{"ms": ms, "id": i, "paginas": p, "origen": o, "mb": mb} for ms, i, p, o, mb in sorted(lentos, reverse=True)[:8]]},
        "contraste": {"postulantes": len(expedientes),
                      "con_3_o_mas": sum(1 for e in expedientes.values() if e["documentos"] >= 3),
                      "discrepancias": dict(discrepancias),
                      "por_postulante": {p: [f"{x['campo']}:{x['severidad']}:{x.get('similitud')}" for x in e["discrepancias"]]
                                         for p, e in expedientes.items() if e["discrepancias"]}},
    }


def escribir_csv(ruta: Path, filas, resultados) -> None:
    """Resumen por documento (sin valores: solo qué campos se encontraron)."""
    with ruta.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["id", "cargo", "esperado", "obtenido", "confianza", "origen", "paginas", "ms", "estado", "senales_media_alta",
                    "observaciones", "validaciones_fallidas", "campos", "errores"])
        for f in filas:
            r = resultados.get(f["id"], {})
            d = r.get("doc")
            if not d:
                w.writerow([f["id"], f["cargo"], f["esperado"], "ERROR", "", "", "", 0, "", "", "", "", "", r.get("error")])
                continue
            w.writerow([f["id"], f["cargo"], f["esperado"], d["clasificacion"]["tipo"], d["clasificacion"]["confianza"],
                        d["archivo"]["origen"], d["archivo"]["paginas"], r["ms"], d["resumen"]["estado"],
                        " ".join(f"{s['id']}:{s['severidad']}" for s in d["senales"] if s["severidad"] in ("media", "alta")),
                        " ".join(o["id"] for o in d["observaciones"]),
                        " ".join(v["regla"] for v in d["validaciones"] if v["resultado"] == "falla"),
                        " ".join(sorted(d["campos"])), " ".join(e["etapa"] for e in d["errores"])])


def imprimir(m: dict) -> None:
    c = m["clasificacion"]
    print(f"\nClasificación: MVP {c['exactitud_mvp_pct']} % (n={c['n_mvp']}) · todos {c['exactitud_todos_pct']} % (n={c['n_todos']})")
    for t, v in c["por_tipo"].items():
        print(f"  {ABREV.get(t, t):5} {v['aciertos']}/{v['n']} ({v['pct']} %)")
    columnas = sorted({o for fila in c["confusion"].values() for o in fila})
    print("\nMatriz de confusión (filas = esperado, columnas = obtenido)")
    print("       " + " ".join(f"{ABREV.get(o, o)[:5]:>5}" for o in columnas))
    for e, fila in c["confusion"].items():
        print(f"{ABREV.get(e, e)[:6]:6} " + " ".join(f"{fila.get(o, 0) or '.':>5}" for o in columnas))
    if c["ambiguos_sin_corregir"]:
        print(f"Ambiguos sin corregir: {', '.join(c['ambiguos_sin_corregir'])}")
    print("\nCampos clave (presentes, % de los documentos bien clasificados)")
    for t, campos in m["campos"].items():
        partes = [f"{k}={v['presente_pct']}" for k, v in campos.items() if not k.startswith("_")]
        print(f"  {ABREV.get(t, t):5} global {campos['_global_pct']} % · " + " ".join(partes))
    print(f"RUT: {m['rut']}")
    print(f"Nombre frente al de los certificados del Registro Civil: {m.get('nombre_vs_referencia')} · distintos: {m.get('nombres_distintos')}")
    print(f"\nEstados: {m['estados']} · MVP: {m['estados_mvp']}")
    print(f"Señales media/alta: {m['senales_media_alta']}")
    print(f"Observaciones: {m['observaciones']}")
    print(f"Validaciones fallidas: {m['validaciones_fallidas']}")
    print(f"Errores: {m['errores']} · ilegibles: {m['ilegibles']}")
    t = m["tiempos"]
    print(f"Tiempos: total {t['total_s']} s · p50 {t['p50_ms']} ms · p95 {t['p95_ms']} ms · máx {t['max_ms']} ms · {t['docs_por_min']} docs/min")
    print(f"Contraste: {m['contraste']['postulantes']} postulantes ({m['contraste']['con_3_o_mas']} con 3+ docs) · {m['contraste']['discrepancias']}")


def comparar(args) -> None:
    a = json.loads(Path(args.antes).read_text(encoding="utf-8"))
    b = json.loads(Path(args.despues).read_text(encoding="utf-8"))
    print(f"{'':40} {'antes':>8} {'después':>8}")
    print(f"{'Clasificación MVP %':40} {a['clasificacion']['exactitud_mvp_pct']:>8} {b['clasificacion']['exactitud_mvp_pct']:>8}")
    print(f"{'Clasificación todos %':40} {a['clasificacion']['exactitud_todos_pct']:>8} {b['clasificacion']['exactitud_todos_pct']:>8}")
    for t in TIPOS_MVP + OTROS:
        va, vb = a["clasificacion"]["por_tipo"].get(t, {}), b["clasificacion"]["por_tipo"].get(t, {})
        print(f"{'  clas ' + t:40} {str(va.get('pct')):>8} {str(vb.get('pct')):>8}")
    for t in CAMPOS_CLAVE:
        ca, cb = a["campos"].get(t, {}), b["campos"].get(t, {})
        print(f"{'  campos ' + t:40} {str(ca.get('_global_pct')):>8} {str(cb.get('_global_pct')):>8}")
    for k in ("estados", "senales_media_alta"):
        print(f"{k}:\n  antes   {a[k]}\n  después {b[k]}")


def renderizar(args) -> None:
    """PNG de una página para revisarla a ojo (queda en la salida, fuera del repositorio)."""
    import pypdfium2 as pdfium

    filas = {f["id"]: f for f in recorrer(Path(args.conjunto))}
    f = filas[args.id]
    salida = Path(args.salida) / "png"
    salida.mkdir(parents=True, exist_ok=True)
    pdf = pdfium.PdfDocument(f["ruta"])
    paginas = range(len(pdf)) if args.pagina == 0 else [args.pagina - 1]
    for i in paginas:
        img = pdf[i].render(scale=args.escala).to_pil()
        destino = salida / f"{args.id}_p{i + 1}.png"
        img.save(destino)
        print(destino)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("procesar")
    p.add_argument("conjunto", nargs="?", default=os.environ.get("SOCR_CALIB_CONJUNTO"))
    p.add_argument("salida", nargs="?", default=os.environ.get("SOCR_CALIB_SALIDA"))
    p.add_argument("--correcciones")
    p.add_argument("--trabajadores", type=int, default=2)
    p.add_argument("--hoy", help="fecha de referencia para las vigencias (AAAA-MM-DD); por defecto hoy")
    p.add_argument("--sin-cache", action="store_true", help="repite el OCR aunque esté en caché")
    p.add_argument("--solo", help="prefijos de id separados por coma (p. ej. c08_p01,c09)")
    p.add_argument("--etiqueta", default="", help="sufijo del archivo de métricas (antes, despues…)")
    q = sub.add_parser("recalcular")
    q.add_argument("conjunto")
    q.add_argument("salida")
    q.add_argument("--docs", default="docs", help="subcarpeta de la salida con los JSON")
    q.add_argument("--correcciones")
    q.add_argument("--trabajadores", type=int, default=2)
    q.add_argument("--etiqueta", default="")
    c = sub.add_parser("comparar")
    c.add_argument("antes")
    c.add_argument("despues")
    r = sub.add_parser("renderizar")
    r.add_argument("conjunto")
    r.add_argument("salida")
    r.add_argument("id")
    r.add_argument("--pagina", type=int, default=1, help="0 = todas")
    r.add_argument("--escala", type=float, default=1.4)
    a = ap.parse_args()
    if a.cmd == "procesar":
        if not a.conjunto or not a.salida:
            ap.error("faltan el conjunto y la salida (argumentos o SOCR_CALIB_CONJUNTO / SOCR_CALIB_SALIDA)")
        procesar(a)
    elif a.cmd == "comparar":
        comparar(a)
    elif a.cmd == "recalcular":
        recalcular(a)
    else:
        renderizar(a)


if __name__ == "__main__":
    main()
