"""Prueba de detección con documentos reales: genera copias adulteradas (verdad conocida) y mide qué detecta.

Para cada documento con un RUT ubicado en la página se cambia un dígito del RUT con técnicas típicas de falsificación:

  PDF nativo
    editor_ilovepdf        se vuelve a guardar el PDF declarando iLovePDF como productor
    incremental_tapado     actualización incremental: rectángulo blanco sobre el RUT y RUT nuevo encima (guardado "rápido")
    reescrito_tapado       igual, pero el PDF se reescribe completo (como lo hace un editor)
    digito_encimado        solo se dibuja otro dígito encima del último dígito, sin tapar
    rasterizado            la página se convierte en imagen y se guarda como PDF (reimpresión/escaneo)
  Imagen (PDF nativo renderizado, o foto)
    foto_editada_exif      se pinta el RUT nuevo sobre la imagen y se guarda con EXIF de Photoshop
    foto_editada           igual, sin metadatos (lo que haría alguien cuidadoso)

Una alteración se cuenta como detectada si aparece al menos una de sus señales esperadas con severidad media o alta.
Los originales deben salir sin señales media o alta.

Uso: python herramientas/prueba_alteraciones.py <carpeta con documentos> <carpeta de salida> [--hoy 2026-10-22]
Los documentos y sus copias pueden tener datos personales: la carpeta de salida no debe quedar dentro del repositorio.
"""
from __future__ import annotations

import argparse
import io
import json
import logging
import re
import sys
from datetime import date
from pathlib import Path

import pikepdf
import pypdfium2 as pdfium
from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from stracon_ocr.contrato import ORDEN_SEVERIDAD, Documento  # noqa: E402
from stracon_ocr.pipeline import EXTENSIONES, Procesador  # noqa: E402

DPI = 200
ESPERADAS = {
    "editor_ilovepdf": ["meta.productor_no_esperado", "pdf.firma_invalida"],
    "incremental_tapado": ["pdf.revisiones_incrementales", "pdf.cambios_post_firma", "pdf.objeto_tapa_texto", "pdf.texto_superpuesto", "codigo.no_coincide"],
    "reescrito_tapado": ["pdf.objeto_tapa_texto", "pdf.texto_superpuesto", "pdf.firma_invalida", "codigo.no_coincide"],
    "digito_encimado": ["pdf.texto_superpuesto", "pdf.fuentes_mezcladas_campo", "pdf.firma_invalida", "codigo.no_coincide"],
    "rasterizado": ["pdf.solo_imagen", "pdf.firma_invalida"],
    "foto_editada_exif": ["img.exif_editor", "codigo.no_coincide"],
    "foto_editada": ["codigo.no_coincide", "rut.inconsistente", "img.ela_zona", "ocr.confianza_anomala", "ocr.motores_discrepan"],
}


def otro_rut(valor: str) -> str:
    """Cambia el último dígito del cuerpo del RUT (12.345.678-5 → 12.345.671-5)."""
    m = re.search(r"(\d)(\s*-\s*[\dkK])", valor)
    if not m:
        return valor
    nuevo = str((int(m.group(1)) + 7) % 10)
    return valor[: m.start(1)] + nuevo + valor[m.end(1):]


def _fuente_ttf(tam: int) -> ImageFont.ImageFont:
    for f in (r"C:\Windows\Fonts\arial.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"):
        if Path(f).exists():
            return ImageFont.truetype(f, tam)
    return ImageFont.load_default()


def _overlay(alto_pagina: float, caja: list[float], texto: str, tapar: bool, fuente: str = "Helvetica") -> bytes:
    """Contenido PDF: rectángulo blanco opcional sobre la caja y el texto nuevo en la misma posición."""
    x0, y0, x1, y1 = caja
    h = y1 - y0
    base = alto_pagina - y1  # PDF tiene el origen abajo
    partes = []
    if tapar:
        partes.append(f"q 1 1 1 rg {x0 - 1:.2f} {base - 1:.2f} {x1 - x0 + 2:.2f} {h + 2:.2f} re f Q")
    partes.append(f"q 0 0 0 rg BT /FX {h * 0.95:.2f} Tf {x0:.2f} {base + h * 0.2:.2f} Td ({texto}) Tj ET Q")
    return ("\n".join(partes) + "\n").encode()


def _con_fuente(pdf: pikepdf.Pdf, pagina: pikepdf.Page, fuente: str) -> None:
    fuente_pdf = pdf.make_indirect(pikepdf.Dictionary(Type=pikepdf.Name.Font, Subtype=pikepdf.Name.Type1,
                                                      BaseFont=pikepdf.Name("/" + fuente)))
    pagina.add_resource(fuente_pdf, pikepdf.Name.Font, pikepdf.Name("/FX"))


def pdf_reescrito(origen: Path, n: int, caja: list[float], texto: str, tapar: bool, fuente: str = "Helvetica") -> bytes:
    pdf = pikepdf.open(origen)
    pagina = pdf.pages[n - 1]
    _con_fuente(pdf, pagina, fuente)
    alto = float(pagina.mediabox[3])
    pagina.contents_add(pikepdf.Stream(pdf, b"q\n"), prepend=True)  # el contenido original puede dejar la matriz cambiada
    pagina.contents_add(pikepdf.Stream(pdf, b"Q\n" + _overlay(alto, caja, texto, tapar, fuente)), prepend=False)
    buf = io.BytesIO()
    pdf.save(buf)
    return buf.getvalue()


def pdf_incremental(origen: Path, n: int, caja: list[float], texto: str) -> bytes:
    """Agrega el cambio como actualización incremental: los bytes originales (y su firma) quedan intactos."""
    import pypdf

    capa = pikepdf.new()
    original = pikepdf.open(origen)
    mb = original.pages[n - 1].mediabox
    capa.add_blank_page(page_size=(float(mb[2]), float(mb[3])))
    _con_fuente(capa, capa.pages[0], "Helvetica")
    capa.pages[0].contents_add(pikepdf.Stream(capa, _overlay(float(mb[3]), caja, texto, True)), prepend=False)
    buf_capa = io.BytesIO()
    capa.save(buf_capa)
    w = pypdf.PdfWriter(str(origen), incremental=True)
    w.pages[n - 1].merge_page(pypdf.PdfReader(buf_capa).pages[0])
    out = io.BytesIO()
    w.write(out)
    return out.getvalue()


def pdf_rasterizado(origen: Path) -> bytes:
    doc = pdfium.PdfDocument(str(origen))
    imgs = [doc[i].render(scale=DPI / 72).to_pil().convert("RGB") for i in range(len(doc))]
    buf = io.BytesIO()
    imgs[0].save(buf, "PDF", resolution=DPI, save_all=True, append_images=imgs[1:])
    return buf.getvalue()


def imagen_editada(img: Image.Image, caja_px: list[float], texto: str, con_exif: bool) -> bytes:
    img = img.convert("RGB").copy()
    x0, y0, x1, y1 = caja_px
    d = ImageDraw.Draw(img)
    fondo = img.getpixel((max(0, int(x0) - 3), max(0, int(y0) - 3)))  # color del papel junto al campo
    d.rectangle([x0 - 2, y0 - 2, x1 + 2, y1 + 2], fill=fondo)
    d.text((x0, y0), texto, fill=(20, 20, 20), font=_fuente_ttf(int((y1 - y0) * 0.95)))
    buf = io.BytesIO()
    if con_exif:
        exif = Image.Exif()
        exif[305] = "Adobe Photoshop 25.0 (Windows)"
        img.save(buf, "JPEG", quality=90, exif=exif)
    else:
        img.save(buf, "JPEG", quality=90)
    return buf.getvalue()


def generar(proc: Procesador, origen: Path, salida: Path, hoy: date) -> dict[str, str]:
    """Genera las variantes de un documento. Devuelve {archivo: alteración}."""
    d = proc.procesar(origen, guardar=False, hoy=hoy)
    rut = d.campos.get("rut")
    if not rut or not rut.bbox or not rut.pagina:
        return {}
    nuevo = otro_rut(rut.valor)
    base = salida / f"{origen.parent.name}__{origen.stem}"
    variantes: dict[str, bytes] = {}
    if d.archivo.origen == "pdf_nativo":
        variantes["editor_ilovepdf"] = _productor(origen, "iLovePDF")
        variantes["incremental_tapado"] = pdf_incremental(origen, rut.pagina, rut.bbox, nuevo)
        variantes["reescrito_tapado"] = pdf_reescrito(origen, rut.pagina, rut.bbox, nuevo, tapar=True)
        x0, y0, x1, y1 = rut.bbox
        ancho_char = (x1 - x0) / max(len(rut.valor), 1)
        pos = len(rut.valor) - 3  # dígito antes del guion
        caja_dig = [x0 + ancho_char * pos, y0, x0 + ancho_char * (pos + 1), y1]
        variantes["digito_encimado"] = pdf_reescrito(origen, rut.pagina, caja_dig, nuevo[pos], tapar=False, fuente="Courier")
        variantes["rasterizado"] = pdf_rasterizado(origen)
        img = pdfium.PdfDocument(str(origen))[rut.pagina - 1].render(scale=DPI / 72).to_pil()
        caja_px = [v * DPI / 72 for v in rut.bbox]
    elif d.archivo.origen == "pdf_escaneado":
        img = pdfium.PdfDocument(str(origen))[rut.pagina - 1].render(scale=DPI / 72).to_pil()
        caja_px = [v * DPI / 72 for v in rut.bbox]
    elif d.archivo.origen == "imagen":
        img = Image.open(origen)
        caja_px = rut.bbox
    else:
        return {}
    variantes["foto_editada_exif"] = imagen_editada(img, caja_px, nuevo, con_exif=True)
    variantes["foto_editada"] = imagen_editada(img, caja_px, nuevo, con_exif=False)
    hechos = {}
    for nombre, datos in variantes.items():
        ext = ".jpg" if nombre.startswith("foto") else ".pdf"
        ruta = Path(f"{base}__{nombre}{ext}")
        ruta.write_bytes(datos)
        hechos[ruta.name] = nombre
    return hechos


def _productor(origen: Path, productor: str) -> bytes:
    pdf = pikepdf.open(origen)
    pdf.docinfo["/Producer"] = productor
    buf = io.BytesIO()
    pdf.save(buf)
    return buf.getvalue()


def senales_relevantes(d: Documento) -> set[str]:
    return {s.id for s in d.senales if ORDEN_SEVERIDAD[s.severidad] >= ORDEN_SEVERIDAD["media"]}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("entrada", type=Path)
    ap.add_argument("salida", type=Path)
    ap.add_argument("--hoy", default=date.today().isoformat())
    a = ap.parse_args()
    logging.disable(logging.WARNING)
    a.salida.mkdir(parents=True, exist_ok=True)
    hoy = date.fromisoformat(a.hoy)
    proc = Procesador(guardar=False)
    filas = []
    for origen in sorted(f for f in a.entrada.rglob("*") if f.is_file() and f.suffix.lower() in EXTENSIONES):
        d = proc.procesar(origen, guardar=False, hoy=hoy)
        filas.append({"archivo": f"{origen.parent.name}/{origen.name}", "alteracion": "original", "tipo": d.clasificacion.tipo,
                      "esperado": [], "hallado": sorted(senales_relevantes(d)), "detectado": None,
                      "falso_positivo": bool(senales_relevantes(d))})
        for archivo, alteracion in generar(proc, origen, a.salida, hoy).items():
            dv = proc.procesar(a.salida / archivo, guardar=False, hoy=hoy)
            hallado = senales_relevantes(dv)
            esperado = ESPERADAS[alteracion]
            filas.append({"archivo": archivo, "alteracion": alteracion, "tipo": dv.clasificacion.tipo, "esperado": esperado,
                          "hallado": sorted(hallado), "detectado": bool(hallado & set(esperado)), "falso_positivo": None,
                          "detalle": [s.detalle for s in dv.senales if s.id in hallado][:4]})
            marca = "OK " if filas[-1]["detectado"] else "-- "
            print(f"{marca}{alteracion:20} {dv.clasificacion.tipo:36} {sorted(hallado)}")
    alteradas = [f for f in filas if f["alteracion"] != "original"]
    originales = [f for f in filas if f["alteracion"] == "original"]
    por_tecnica = {}
    for f in alteradas:
        t = por_tecnica.setdefault(f["alteracion"], [0, 0])
        t[0] += f["detectado"]
        t[1] += 1
    resumen = {
        "originales": len(originales),
        "originales_con_falso_positivo": sum(f["falso_positivo"] for f in originales),
        "alteradas": len(alteradas),
        "detectadas": sum(f["detectado"] for f in alteradas),
        "por_tecnica": {k: f"{v[0]}/{v[1]}" for k, v in sorted(por_tecnica.items())},
    }
    (a.salida / "resultado.json").write_text(json.dumps({"resumen": resumen, "detalle": filas}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(resumen, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
