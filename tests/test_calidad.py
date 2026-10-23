"""Calidad de imagen y orientación automática, con un PDF del mockup convertido en foto y degradado."""
from __future__ import annotations

import pypdfium2 as pdfium
import pytest
from PIL import Image, ImageEnhance, ImageFilter

from stracon_ocr.pipeline import Procesador

from .conftest import HOY, mockup

DOC = "16884102-7/6-certificado-antecedentes.pdf"


@pytest.fixture(scope="module")
def foto() -> Image.Image:
    return pdfium.PdfDocument(str(mockup(DOC)))[0].render(scale=200 / 72).to_pil().convert("RGB")


def _procesar(proc: Procesador, img: Image.Image, tmp_path, nombre: str):
    ruta = tmp_path / nombre
    img.save(ruta, "JPEG", quality=88)
    return proc.procesar(ruta, guardar=False, hoy=HOY)


def _calidad(d) -> list[str]:
    return [o.id for o in d.observaciones if o.id.startswith("calidad.")]


def test_foto_nitida_no_queda_observada_por_calidad(proc_sin_bd: Procesador, foto, tmp_path):
    d = _procesar(proc_sin_bd, foto, tmp_path, "nitida.jpg")
    assert _calidad(d) == [], d.paginas[0].calidad
    assert d.campos["rut"].valor_normalizado == "16884102-7"
    c = d.paginas[0].calidad
    assert c["palabras"] > 20 and c["confianza_media"] > 0.85 and c["rotacion_aplicada"] == 0


@pytest.mark.parametrize("degradacion", ["muy_borrosa", "muy_oscura"])
def test_foto_degradada_queda_observada(proc_sin_bd: Procesador, foto, tmp_path, degradacion):
    if degradacion == "muy_borrosa":
        img = foto.filter(ImageFilter.GaussianBlur(9))
    else:
        img = ImageEnhance.Brightness(foto).enhance(0.08)
    d = _procesar(proc_sin_bd, img, tmp_path, f"{degradacion}.jpg")
    obs = [o for o in d.observaciones if o.id.startswith("calidad.")]
    assert obs, (d.paginas[0].calidad, [o.id for o in d.observaciones])
    assert all(o.accion == "solicitar_reenvio" and o.mensaje_postulante for o in obs)
    assert d.resumen["estado"] in {"observado", "senal_media", "senal_alta"}


@pytest.mark.parametrize("giro, esperado", [(90, 270), (180, 180), (270, 90)])
def test_foto_girada_se_endereza(proc_sin_bd: Procesador, foto, tmp_path, giro, esperado):
    d = _procesar(proc_sin_bd, foto.rotate(giro, expand=True), tmp_path, f"girada_{giro}.jpg")
    assert d.paginas[0].calidad["rotacion_aplicada"] == esperado
    assert d.clasificacion.tipo == "certificado_antecedentes"
    assert d.campos["rut"].valor_normalizado == "16884102-7"
