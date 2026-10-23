"""Observaciones: el documento no se puede validar y queda «Observado» con la acción y el mensaje para el postulante."""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pikepdf
import pytest
from PIL import Image, ImageDraw, ImageFont

from stracon_ocr.pipeline import Procesador

from .conftest import HOY, mockup

FUENTE = Path(r"C:\Windows\Fonts\arial.ttf")


def _imagen(ruta: Path, lineas: list[str]) -> Path:
    if not FUENTE.exists():
        pytest.skip("Falta la fuente Arial para generar la imagen de prueba")
    img = Image.new("RGB", (1600, 1000), "white")
    d = ImageDraw.Draw(img)
    f = ImageFont.truetype(str(FUENTE), 44)
    for i, t in enumerate(lineas):
        d.text((80, 80 + i * 90), t, fill="black", font=f)
    img.save(ruta, "PNG")
    return ruta


def _ids(d) -> set[str]:
    return {o.id for o in d.observaciones}


def test_documentos_limpios_sin_observaciones(proc_sin_bd: Procesador):
    for nombre in ("1-cedula-identidad.pdf", "2-licencia-conducir.pdf", "3-hoja-vida-conductor.pdf",
                   "4-certificado-ceim.pdf", "5-certificado-estudios-medios.pdf", "6-certificado-antecedentes.pdf"):
        d = proc_sin_bd.procesar(mockup(f"16884102-7/{nombre}"), guardar=False, hoy=HOY)
        assert d.observaciones == [], (nombre, [o.id for o in d.observaciones])
        assert d.resumen["estado"] == "sin_senales" and d.resumen["estado_texto"] == "Sin señales"


def test_tipo_no_reconocido(proc_sin_bd: Procesador, tmp_path):
    ruta = _imagen(tmp_path / "otro.png", ["FACTURA DE COMPRA", "Cliente: Comercial Andes Limitada", "Total a pagar 45.990 pesos",
                                           "Gracias por su preferencia"])
    d = proc_sin_bd.procesar(ruta, guardar=False, hoy=HOY)
    assert d.clasificacion.tipo == "desconocido"
    assert "contenido.tipo_no_reconocido" in _ids(d)
    assert d.resumen["estado"] == "observado" and d.resumen["requiere_revision"]
    o = next(o for o in d.observaciones if o.id == "contenido.tipo_no_reconocido")
    assert o.accion == "solicitar_reenvio" and "otro.png" in o.mensaje_postulante


def test_campos_obligatorios_faltantes(proc_sin_bd: Procesador, tmp_path):
    ruta = _imagen(tmp_path / "antecedentes.png", ["CERTIFICADO DE ANTECEDENTES", "Servicio de Registro Civil e Identificación",
                                                   "Fines particulares", "Sin antecedentes"])
    d = proc_sin_bd.procesar(ruta, guardar=False, hoy=HOY)
    assert d.clasificacion.tipo == "certificado_antecedentes"
    o = next(o for o in d.observaciones if o.id == "contenido.campos_faltantes")
    assert {"rut", "folio", "codigo_verificacion", "fecha_emision"} <= set(o.evidencia["faltan"])
    assert o.motivo == "incompleto" and "RUT" in o.mensaje_postulante
    assert d.resumen["estado"] == "observado"


def test_licencia_solo_reverso(proc_sin_bd: Procesador, tmp_path):
    ruta = _imagen(tmp_path / "reverso.png", ["RESTRICCIONES", "DEBE USAR LENTES", "FIRMA DEL CONDUCTOR",
                                              "DIRECTOR DE TRANSITO", "PRIMER OTORGAMIENTO 01/01/2010"])
    d = proc_sin_bd.procesar(ruta, guardar=False, hoy=HOY)
    assert d.clasificacion.tipo == "licencia_conducir"
    ids = _ids(d)
    assert "contenido.solo_reverso" in ids and "contenido.campos_faltantes" not in ids
    assert "frente" in next(o for o in d.observaciones if o.id == "contenido.solo_reverso").mensaje_postulante


def test_pdf_danado(proc_sin_bd: Procesador, tmp_path):
    ruta = tmp_path / "danado.pdf"
    ruta.write_bytes(b"%PDF-1.7\n" + b"\x00basura sin estructura\x13" * 200)
    d = proc_sin_bd.procesar(ruta, guardar=False, hoy=HOY)
    assert "archivo.ilegible" in _ids(d)
    assert d.resumen["estado"] == "observado" and d.errores
    assert d.archivo.sha256 and d.archivo.paginas == 0


def test_pdf_con_contrasena(proc_sin_bd: Procesador, tmp_path):
    ruta = tmp_path / "protegido.pdf"
    with pikepdf.open(mockup("16884102-7/6-certificado-antecedentes.pdf")) as pdf:
        pdf.save(ruta, encryption=pikepdf.Encryption(owner="dueno", user="clave"))
    d = proc_sin_bd.procesar(ruta, guardar=False, hoy=HOY)
    o = next(o for o in d.observaciones if o.id == "archivo.ilegible")
    assert "contraseña" in o.detalle and "sin contraseña" in o.mensaje_postulante
    assert d.resumen["estado"] == "observado"


def test_antecedentes_vencido(proc_sin_bd: Procesador):
    d = proc_sin_bd.procesar(mockup("16884102-7/6-certificado-antecedentes.pdf"), guardar=False, hoy=date(2027, 6, 1))
    o = next(o for o in d.observaciones if o.id == "contenido.vencido")
    assert o.motivo == "vigencia" and o.accion == "solicitar_vigente"
    assert "60 días" in o.mensaje_postulante


def test_expediente_faltantes_y_reverso_con_anverso(cfg_tmp, tmp_path):
    proc = Procesador(cfg_tmp)
    rut = "16884102-7"
    proc.procesar(mockup(f"{rut}/2-licencia-conducir.pdf"), expediente=rut, hoy=HOY)
    proc.procesar(mockup(f"{rut}/6-certificado-antecedentes.pdf"), expediente=rut, hoy=HOY)
    reverso = _imagen(tmp_path / "reverso.png", ["RESTRICCIONES", "FIRMA DEL CONDUCTOR", "DIRECTOR DE TRANSITO",
                                                 "PRIMER OTORGAMIENTO 01/01/2010"])
    d = proc.procesar(reverso, expediente=rut, hoy=HOY)
    assert "contenido.solo_reverso" in _ids(d)  # el reverso suelto está observado...
    exp = proc.contrastar(rut)
    ids = [(o.id, o.documento) for o in exp.observaciones]
    assert ("contenido.solo_reverso", "licencia_conducir") not in ids  # ...pero no en el expediente: llegó el anverso
    faltantes = {o.documento for o in exp.observaciones if o.id == "expediente.documento_faltante"}
    assert faltantes == {"cedula_identidad", "certificado_estudios_medios", "certificado_estudios_profesionales", "hoja_vida_conductor"}
    assert exp.estado == "observado"
    assert all(o.accion == "solicitar_documento" for o in exp.observaciones if o.id == "expediente.documento_faltante")
