"""Genera datos/verdad-mockup.json: la señal que debe disparar cada PDF alterado del mockup.

Las alteraciones son las de ALTERACIONES en stracon-validacion-documental/herramientas/generar-expedientes.ps1
(idCaso|nDoc → efecto). Solo se aplican a la v1; la v2 y el resto de los documentos deben salir sin señales.
Uso: uv run python herramientas/generar_verdad_mockup.py [ruta-al-csv-del-mockup]
"""
from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

CSV = Path(sys.argv[1] if len(sys.argv) > 1 else r"C:\Users\amsalinas\Desktop\stracon-validacion-documental\datos-prueba-validacion.csv")
ARCHIVOS = ["1-cedula-identidad", "2-licencia-conducir", "5-certificado-estudios-medios", "4-certificado-ceim",
            "6-certificado-antecedentes", "3-hoja-vida-conductor"]

# efecto visual → señal del catálogo
SENAL = {
    "font": "pdf.fuentes_mezcladas_campo",
    "ghost": "pdf.texto_superpuesto",
    "blur": "pdf.campo_rasterizado",
    "stamp": "pdf.imagen_baja_nitidez",
}
ALTERACIONES = {
    "c1|3": ("stamp", None), "c1|4": ("plain", "validacion:formato.codigo_verificacion"),
    "c6|5": ("plain", None), "c11|5": ("font", None), "c15|3": ("plain", None), "c19|3": ("ghost", None),
    "c23|1": ("font", None), "c23|3": ("ghost", None), "r02|3": ("stamp", None), "r04|4": ("blur", None),
    "r14|5": ("plain", None), "r17|3": ("font", None), "r18|3": ("font", None), "r22|5": ("plain", None),
    "r26|1": ("ghost", None),
}

filas = {r["id_expediente"]: r for r in csv.DictReader(CSV.open(encoding="utf-8-sig"), delimiter=";") if r.get("id_expediente")}
documentos: dict[str, list[str]] = {}
externas: dict[str, str] = {}
for clave, (efecto, extra) in ALTERACIONES.items():
    caso, n = clave.split("|")
    if caso not in filas:
        continue
    rut = filas[caso]["rut"].replace(".", "")
    archivo = f"{rut}/{ARCHIVOS[int(n)]}.pdf"
    esperado = [SENAL[efecto]] if efecto in SENAL else []
    if extra:
        esperado.append(extra)
    if esperado:
        documentos[archivo] = esperado
    else:
        externas[archivo] = "Solo cambia el valor: se detecta al contrastar con el emisor (Agente 2) o entre documentos"

salida = Path(__file__).resolve().parents[1] / "datos" / "verdad-mockup.json"
salida.write_text(json.dumps({"fuente": str(CSV), "documentos": documentos, "externas": externas}, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"{len(documentos)} documentos con señal esperada, {len(externas)} alteraciones externas → {salida}")
