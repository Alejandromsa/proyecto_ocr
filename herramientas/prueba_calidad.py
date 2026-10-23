"""Prueba de robustez ante fotos malas: degrada documentos reales y verifica que el sistema nunca falle "en silencio".

Para cada documento se toma la primera página como imagen (los PDF se renderizan) y se generan variantes:
desenfoque leve y fuerte, oscura, sobreexpuesta, baja resolución, JPEG muy comprimido, rotada 90/180/270,
recortada a la mitad y con ruido. Cada variante se clasifica frente al original:

  ok             mismo tipo y mismo RUT que el original, sin observaciones de calidad
  ok_observado   mismo tipo y RUT, pero con alguna observación (se pide reenvío sin necesidad estricta)
  observado      el resultado es distinto o peor, pero el documento queda Observado (el revisor pide aclaración)
  silencio       el resultado es distinto o peor y NO hay observaciones: es el caso a evitar

Los originales no deben tener observaciones de calidad.

Uso: python herramientas/prueba_calidad.py <carpeta con documentos> <carpeta de salida> [--hoy 2026-10-22]
Los documentos pueden tener datos personales: la carpeta de salida no debe quedar dentro del repositorio.
"""
from __future__ import annotations

import argparse
import io
import json
import logging
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pypdfium2 as pdfium
from PIL import Image, ImageEnhance, ImageFilter

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from stracon_ocr.contrato import Documento  # noqa: E402
from stracon_ocr.pipeline import EXTENSIONES, Procesador  # noqa: E402

DPI = 200


def _escala(img: Image.Image) -> float:
    return max(img.size) / 1000


def degradar(img: Image.Image, cual: str) -> tuple[Image.Image, int]:
    """Devuelve la imagen degradada y la calidad JPEG con que se guarda."""
    img = img.convert("RGB")
    s = _escala(img)
    if cual == "desenfoque_leve":
        return img.filter(ImageFilter.GaussianBlur(1.2 * s)), 88
    if cual == "desenfoque_fuerte":
        return img.filter(ImageFilter.GaussianBlur(4.0 * s)), 88
    if cual == "oscura":
        return ImageEnhance.Brightness(img).enhance(0.30), 88
    if cual == "sobreexpuesta":
        return ImageEnhance.Contrast(ImageEnhance.Brightness(img).enhance(1.9)).enhance(0.5), 88
    if cual == "baja_resolucion":
        f = 600 / max(img.size)
        return img.resize((int(img.width * f), int(img.height * f)), Image.LANCZOS), 88
    if cual == "jpeg_comprimido":
        return img, 6
    if cual.startswith("rotada_"):
        return img.rotate(int(cual.split("_")[1]), expand=True), 88
    if cual == "recorte_mitad":
        return img.crop((0, 0, img.width, img.height // 2)), 88
    if cual == "ruido":
        arr = np.asarray(img).astype(np.float32)
        arr += np.random.default_rng(7).normal(0, 45, arr.shape)
        return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8)), 88
    raise ValueError(cual)


DEGRADACIONES = ["desenfoque_leve", "desenfoque_fuerte", "oscura", "sobreexpuesta", "baja_resolucion", "jpeg_comprimido",
                 "rotada_90", "rotada_180", "rotada_270", "recorte_mitad", "ruido"]


def primera_pagina(ruta: Path) -> Image.Image | None:
    if ruta.suffix.lower() == ".pdf":
        pdf = pdfium.PdfDocument(str(ruta))
        return pdf[0].render(scale=DPI / 72).to_pil()
    try:
        return Image.open(ruta)
    except Exception:
        return None


def _rut(d: Documento) -> str | None:
    c = d.campos.get("rut")
    return c.valor_normalizado if c else None


def clasificar(orig: Documento, var: Documento) -> str:
    igual = var.clasificacion.tipo == orig.clasificacion.tipo and _rut(var) == _rut(orig)
    if igual:
        return "ok_observado" if var.observaciones else "ok"
    return "observado" if var.observaciones else "silencio"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("entrada", type=Path)
    ap.add_argument("salida", type=Path)
    ap.add_argument("--hoy", default=date.today().isoformat())
    ap.add_argument("--solo", nargs="*", default=None, help="Degradaciones a probar (por defecto todas)")
    a = ap.parse_args()
    logging.disable(logging.WARNING)
    a.salida.mkdir(parents=True, exist_ok=True)
    hoy = date.fromisoformat(a.hoy)
    proc = Procesador(guardar=False)
    degradaciones = a.solo or DEGRADACIONES
    filas = []
    for ruta in sorted(f for f in a.entrada.rglob("*") if f.is_file() and f.suffix.lower() in EXTENSIONES):
        orig = proc.procesar(ruta, guardar=False, hoy=hoy)
        obs_calidad = [o.id for o in orig.observaciones if o.id.startswith("calidad.")]
        filas.append({"archivo": f"{ruta.parent.name}/{ruta.name}", "degradacion": "original", "tipo": orig.clasificacion.tipo,
                      "rut": _rut(orig), "observaciones": [o.id for o in orig.observaciones], "calidad": orig.paginas[0].calidad if orig.paginas else {},
                      "resultado": "observado_por_calidad" if obs_calidad else "ok"})
        print(f"{'!! ' if obs_calidad else '   '}original           {orig.clasificacion.tipo:36} {[o.id for o in orig.observaciones]}", flush=True)
        img = primera_pagina(ruta)
        if img is None:
            continue
        for cual in degradaciones:
            deg, q = degradar(img, cual)
            buf = io.BytesIO()
            deg.save(buf, "JPEG", quality=q)
            nombre = f"{ruta.parent.name}__{ruta.stem}__{cual}.jpg"
            (a.salida / nombre).write_bytes(buf.getvalue())
            var = proc.procesar(a.salida / nombre, guardar=False, hoy=hoy)
            res = clasificar(orig, var)
            filas.append({"archivo": nombre, "degradacion": cual, "tipo": var.clasificacion.tipo, "rut": _rut(var),
                          "observaciones": [o.id for o in var.observaciones], "calidad": var.paginas[0].calidad if var.paginas else {},
                          "resultado": res})
            marca = {"ok": "   ", "ok_observado": " o ", "observado": " O ", "silencio": "XX "}[res]
            print(f"{marca}{cual:18} {var.clasificacion.tipo:36} {[o.id for o in var.observaciones]}", flush=True)
    por_degradacion: dict[str, dict[str, int]] = {}
    for f in filas:
        if f["degradacion"] == "original":
            continue
        t = por_degradacion.setdefault(f["degradacion"], {"ok": 0, "ok_observado": 0, "observado": 0, "silencio": 0})
        t[f["resultado"]] += 1
    originales = [f for f in filas if f["degradacion"] == "original"]
    resumen = {
        "originales": len(originales),
        "originales_observados_por_calidad": sum(f["resultado"] != "ok" for f in originales),
        "variantes": sum(sum(v.values()) for v in por_degradacion.values()),
        "silencio": sum(v["silencio"] for v in por_degradacion.values()),
        "por_degradacion": por_degradacion,
    }
    (a.salida / "resultado.json").write_text(json.dumps({"resumen": resumen, "detalle": filas}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(resumen, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
