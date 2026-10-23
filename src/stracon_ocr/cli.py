"""CLI `socr`: procesar | expediente | buscar | exportar | servir | esquema | evaluar."""
from __future__ import annotations

import json
import logging
import statistics
import time
from datetime import date
from pathlib import Path
from typing import Optional

import typer

from .contrato import ORDEN_SEVERIDAD, Documento, esquema_json
from .pipeline import EXTENSIONES, Procesador

app = typer.Typer(help="OCR, forense documental y extracción a JSON (stracon-ocr)", no_args_is_help=True, add_completion=False)


def _resumen(d: Documento) -> str:
    r = d.resumen
    sev = ", ".join(f"{k}:{v}" for k, v in r["por_severidad"].items() if v) or "sin señales"
    return f"{d.archivo.nombre:40} {d.clasificacion.tipo:36} campos={len(d.campos):2} señales=[{sev}] {d.duracion_ms} ms"


def _guardar_json(d: Documento | dict, salida: Path, nombre: str) -> None:
    salida.mkdir(parents=True, exist_ok=True)
    texto = d.model_dump_json(indent=2) if isinstance(d, Documento) else json.dumps(d, ensure_ascii=False, indent=2, default=str)
    (salida / nombre).write_text(texto, encoding="utf-8")


@app.command()
def procesar(
    ruta: Path = typer.Argument(..., exists=True, help="Archivo o carpeta"),
    expediente: Optional[str] = typer.Option(None, help="RUT del postulante (en una carpeta, por defecto su nombre)"),
    tipo: Optional[str] = typer.Option(None, help="Fuerza el tipo de documento"),
    motor: Optional[str] = typer.Option(None, help="rapidocr | tesseract | azure"),
    salida: Optional[Path] = typer.Option(None, help="Carpeta donde escribir los JSON"),
    guardar: bool = typer.Option(True, help="Guardar en la base SQLite"),
    detalle: bool = typer.Option(False, "--detalle", "-d", help="Mostrar campos y señales"),
) -> None:
    """Procesa un archivo o todos los archivos de una carpeta."""
    proc = Procesador(guardar=guardar)
    archivos = [ruta] if ruta.is_file() else sorted(f for f in ruta.iterdir() if f.is_file() and f.suffix.lower() in EXTENSIONES)
    if ruta.is_dir() and expediente is None:
        expediente = ruta.name
    for f in archivos:
        d = proc.procesar(f, expediente=expediente, tipo=tipo, motor=motor, guardar=guardar)
        typer.echo(_resumen(d))
        if detalle:
            for k, c in d.campos.items():
                typer.echo(f"    {k:22} {c.valor!r} → {c.valor_normalizado!r} ({c.metodo}, {c.conf:.2f})")
            for s in d.senales:
                typer.echo(f"    [{s.severidad.upper():5}] {s.id}: {s.detalle}")
            for v in d.validaciones:
                if v.resultado == "falla":
                    typer.echo(f"    [VALID] {v.regla}: {v.detalle}")
            for e in d.errores:
                typer.echo(f"    [ERROR] {e.etapa}: {e.mensaje}")
        if salida:
            _guardar_json(d, salida, f"{Path(d.archivo.nombre).stem}.json")


@app.command()
def expediente(
    carpeta: Path = typer.Argument(..., exists=True, file_okay=False, help="Carpeta con los documentos de un postulante"),
    rut: Optional[str] = typer.Option(None, help="RUT del expediente (por defecto, el nombre de la carpeta)"),
    motor: Optional[str] = typer.Option(None),
    salida: Optional[Path] = typer.Option(None, help="Carpeta donde escribir los JSON"),
) -> None:
    """Procesa los documentos de un postulante y contrasta los datos entre ellos."""
    proc = Procesador()
    docs, exp = proc.procesar_carpeta(carpeta, expediente=rut, motor=motor)
    for d in docs:
        typer.echo(_resumen(d))
    typer.echo(f"\nIdentidad: {exp.identidad.model_dump()}")
    typer.echo(f"Faltantes: {exp.faltantes or 'ninguno'}")
    for x in exp.discrepancias:
        typer.echo(f"  [DISCREPANCIA {x.severidad}] {x.campo}: {x.detalle} {x.valores}")
    for f in exp.fuentes:
        typer.echo(f"  [FUENTE] {f.documento}: {f.fuente} → {f.estado} (folio {f.folio}, código {f.codigo_verificacion})")
    for dup in exp.duplicados:
        typer.echo(f"  [DUPLICADO] {dup.tipo} con expediente {dup.otro_expediente}")
    if salida:
        for d in docs:
            _guardar_json(d, salida, f"{Path(d.archivo.nombre).stem}.json")
        _guardar_json(exp.model_dump(mode="json"), salida, "expediente.json")


@app.command()
def buscar(
    consulta: Optional[str] = typer.Argument(None, help="Texto (FTS5, sin tildes)"),
    rut: Optional[str] = typer.Option(None, help="Lista los documentos de un expediente"),
    tipo: Optional[str] = typer.Option(None),
) -> None:
    """Busca por texto completo o lista los documentos de un expediente."""
    proc = Procesador()
    filas = proc.repo.buscar_texto(consulta) if consulta else proc.repo.listar_documentos(rut, tipo)
    for f in filas:
        typer.echo(json.dumps(f, ensure_ascii=False, default=str))


@app.command()
def exportar(
    doc_id: Optional[str] = typer.Argument(None, help="Id del documento"),
    rut: Optional[str] = typer.Option(None, help="Exporta todos los documentos y el contraste de un expediente"),
    salida: Path = typer.Option(Path("exportado"), help="Carpeta de destino"),
) -> None:
    """Exporta JSON desde la base."""
    proc = Procesador()
    if doc_id:
        d = proc.repo.obtener_documento(doc_id)
        if not d:
            raise typer.BadParameter("Documento no encontrado")
        _guardar_json(d, salida, f"{doc_id}.json")
    elif rut:
        for d in proc.repo.documentos_de_expediente(rut):
            _guardar_json(d, salida, f"{d.id}.json")
        exp = proc.repo.obtener_expediente(rut)
        if exp:
            _guardar_json(exp, salida, "expediente.json")
    typer.echo(f"Exportado a {salida}")


@app.command()
def servir(host: str = "127.0.0.1", puerto: int = 8000, recargar: bool = False) -> None:
    """Levanta la API HTTP para la webapp (documentación en /docs)."""
    import uvicorn

    uvicorn.run("stracon_ocr.api:crear_app", factory=True, host=host, port=puerto, reload=recargar)


@app.command()
def esquema(salida: Path = typer.Option(Path("docs/contrato-v1.schema.json"))) -> None:
    """Exporta el JSON Schema del contrato."""
    salida.parent.mkdir(parents=True, exist_ok=True)
    salida.write_text(json.dumps(esquema_json(), ensure_ascii=False, indent=2), encoding="utf-8")
    typer.echo(f"Esquema escrito en {salida}")


@app.command()
def evaluar(
    carpeta: Path = typer.Option(Path("datos/dorado"), exists=True, file_okay=False, help="Carpeta con subcarpetas por RUT"),
    verdad: Path = typer.Option(Path("datos/verdad-mockup.json"), exists=True, help="Señales esperadas por archivo"),
    hoy: Optional[str] = typer.Option("2026-10-21", help="Fecha de referencia para vigencias (ISO)"),
    salida: Optional[Path] = typer.Option(Path("datos/evaluacion.json")),
) -> None:
    """Mide detección contra la verdad conocida: recall de alteraciones, falsos positivos y latencia."""
    logging.getLogger().setLevel(logging.ERROR)
    esperado: dict[str, list[str]] = json.loads(verdad.read_text(encoding="utf-8"))["documentos"]
    proc = Procesador(guardar=False)
    ref = date.fromisoformat(hoy) if hoy else None
    filas, latencias = [], []
    for sub in sorted(p for p in carpeta.iterdir() if p.is_dir()):
        for f in sorted(x for x in sub.iterdir() if x.suffix.lower() in EXTENSIONES):
            clave = f"{sub.name}/{f.name}"
            t = time.perf_counter()
            d = proc.procesar(f, expediente=sub.name, guardar=False, hoy=ref)
            latencias.append(time.perf_counter() - t)
            hallado = {s.id for s in d.senales if ORDEN_SEVERIDAD[s.severidad] >= ORDEN_SEVERIDAD["media"]}
            hallado |= {f"validacion:{v.regla}" for v in d.validaciones if v.resultado == "falla"}
            exp = set(esperado.get(clave, []))
            rut = d.campos.get("rut")
            filas.append({"archivo": clave, "tipo": d.clasificacion.tipo, "esperado": sorted(exp), "hallado": sorted(hallado),
                          "rut_extraido": rut.valor_normalizado if rut else None, "campos": len(d.campos),
                          "acierto": sorted(exp & hallado), "omitido": sorted(exp - hallado),
                          "extra": sorted(hallado - exp), "errores": [e.mensaje for e in d.errores]})
            # en un documento alterado, otras señales sobre la misma alteración son evidencia extra, no error
            marca = "OK " if exp <= hallado and (exp or not hallado) else "!! "
            typer.echo(f"{marca}{clave:58} {d.clasificacion.tipo:36} esperado={sorted(exp)} hallado={sorted(hallado)}")
    con_alteracion = [f for f in filas if f["esperado"]]
    limpios = [f for f in filas if not f["esperado"]]
    recall = sum(len(f["acierto"]) for f in con_alteracion) / max(1, sum(len(f["esperado"]) for f in con_alteracion))
    fp_docs = sum(1 for f in limpios if f["extra"])
    tipos_ok = sum(1 for f in filas if f["tipo"] != "desconocido")
    lat = sorted(latencias)
    resumen = {
        "documentos": len(filas),
        "con_alteracion": len(con_alteracion),
        "recall_alteraciones": round(recall, 3),
        "documentos_limpios": len(limpios),
        "limpios_con_falso_positivo": fp_docs,
        "clasificados": tipos_ok,
        "rut_extraido_igual_carpeta": sum(1 for f in filas if f["rut_extraido"] == f["archivo"].split("/")[0].upper()),
        "campos_promedio": round(statistics.mean(f["campos"] for f in filas), 1) if filas else 0,
        "latencia_p50_s": round(statistics.median(lat), 2) if lat else None,
        "latencia_p95_s": round(lat[int(0.95 * (len(lat) - 1))], 2) if lat else None,
    }
    typer.echo("\n" + json.dumps(resumen, ensure_ascii=False, indent=2))
    if salida:
        salida.write_text(json.dumps({"resumen": resumen, "detalle": filas}, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    app()
