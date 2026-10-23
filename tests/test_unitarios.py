from __future__ import annotations

from stracon_ocr.codigos import digito_icao, interpretar_codigo, interpretar_td1
from stracon_ocr.extraccion import normalizar_valor
from stracon_ocr.layout import Pal, agrupar, depurar_superpuestas
from stracon_ocr.texto import diagnostico_rut, dv_rut, normalizar, normalizar_fecha, normalizar_rut, rut_valido


def test_rut_modulo11():
    assert dv_rut(12794536) == "5"
    assert rut_valido("12.794.536-5")
    assert rut_valido("15000005-K") and rut_valido("15.000.005-k")
    assert not rut_valido("12.794.536-4")
    assert normalizar_rut("RUN: 12.794.536-5") == "12794536-5"
    assert normalizar_rut("sin rut") is None


def test_rut_con_espacios_del_ocr():
    # caso real: el OCR leyó "12. 345.678-5" (espacio tras el punto); el RUT es válido
    assert normalizar_rut("12. 345.678-5") == "12345678-5"
    assert rut_valido("12. 345.678-5")
    assert normalizar_rut("Folio 12345 - 6") is None  # no confundir otros números con un RUT


def test_diagnostico_rut():
    d = diagnostico_rut("12.345.678-4")
    assert not d["valido"] and d["motivo"] == "digito_verificador"
    assert d["dv_documento"] == "4" and d["dv_calculado"] == "5"
    assert d["calculo"].endswith("= 138; 138 mod 11 = 6; 11 − 6 = 5")
    assert diagnostico_rut("RUT ilegible")["motivo"] == "formato"


def test_validacion_rut_explica_el_motivo():
    from stracon_ocr.contrato import Campo
    from stracon_ocr.validacion import validar_rut

    v = validar_rut(Campo(valor="12.345.678-4", valor_normalizado="12345678-4", metodo="zona", conf=0.97))
    assert v.resultado == "falla"
    assert "da 5" in v.detalle and "trae 4" in v.detalle and "OCR" in v.detalle
    assert v.evidencia["dv_calculado"] == "5" and v.evidencia["confianza_lectura"] == 0.97
    assert validar_rut(Campo(valor="12. 345.678-5", metodo="zona", conf=0.99)).resultado == "ok"


def test_valor_se_corta_en_el_rotulo_siguiente():
    from stracon_ocr.extraccion import recortar
    from stracon_ocr.layout import Segmento

    seg = Segmento([Pal(t, (i * 40, 0, i * 40 + 35, 10)) for i, t in enumerate("12.345.678-5 Fecha nacimiento: 3 Marzo 1990".split())], 1)
    assert recortar(seg).texto == "12.345.678-5"
    seg = Segmento([Pal(t, (i * 40, 0, i * 40 + 35, 10)) for i, t in enumerate("Sin antecedentes".split())], 1)
    assert recortar(seg).texto == "Sin antecedentes"


def test_pdf417_registro_civil():
    # formato real del Registro Civil con datos inventados
    texto = ("$%$4 2026%310%320 500700000001 0a1b2c3d4e5f$GNOMBRE %. ANA MAR$IA P$EREZ SOTO $C$C$CR.U.N. %. 12.794.536%35 "
             "Fecha nacimiento%. 1 Enero 1990 $C$C$CREGISTRO GENERAL DE CONDENAS")
    datos, valido = interpretar_codigo(texto)
    assert valido
    assert datos["folio"] == "500700000001" and datos["codigo_verificacion"] == "0a1b2c3d4e5f"
    assert datos["fecha_emision"] == "2026-10-20" and datos["run"] == "12794536-5"
    assert datos["nombre"] == "ANA MARÍA PÉREZ SOTO"


def test_codigo_del_emisor_frente_al_documento():
    from stracon_ocr.contrato import Campo, Codigo
    from stracon_ocr.pipeline import cargar_reglas
    from stracon_ocr.config import Config
    from stracon_ocr.validacion import validar

    reglas = cargar_reglas(Config().reglas)
    cod = Codigo(tipo="pdf417", contenido="…", datos={"folio": "500700000001", "run": "12794536-5", "nombre": "ANA MARÍA PÉREZ SOTO"})
    campos = {"folio": Campo(valor="500700000009", metodo="zona"), "rut": Campo(valor="12.794.536-5", valor_normalizado="12794536-5", metodo="zona"),
              "nombre": Campo(valor="ANA MARIA PEREZ SOTO", metodo="zona")}
    v = next(x for x in validar("certificado_antecedentes", campos, [cod], reglas) if x.regla == "qr.coincide_ocr")
    assert v.resultado == "falla" and "folio" in v.detalle
    assert set(v.evidencia["coinciden"]) == {"rut", "nombre"}


def test_revisiones_que_no_son_alteracion():
    from stracon_ocr.forense.pdf_metadatos import tipo_de_actualizacion

    assert tipo_de_actualizacion(b"\n12 0 obj << /Type /Sig /ByteRange [0 100 200 50] >> endobj\nxref") == "firma"
    assert tipo_de_actualizacion(b"\nxref\n0 1\n0000000000 65535 f \ntrailer << /Prev 123 >>\nstartxref\n456\n") == "vacia"
    assert tipo_de_actualizacion(b"\n7 0 obj << /Producer (iLovePDF) >> endobj\nxref") == "contenido"


def test_rotulos_y_fechas_pegados():
    assert normalizar_fecha("16deoctubrede2026") == "2026-10-16"
    assert normalizar_fecha("FechadeEmisión:10deoctubrede2026") == "2026-10-10"
    assert normalizar_fecha("10-04-2025") == "2025-04-10"


def test_fechas_en_espanol():
    assert normalizar_fecha("16 oct 2028") == "2028-10-16"
    assert normalizar_fecha("5 de octubre de 2026") == "2026-10-05"
    assert normalizar_fecha("21/10/2026") == "2026-10-21"
    assert normalizar_fecha("2026-10-21") == "2026-10-21"
    assert normalizar_fecha("20 sep22026") is None


def test_normalizar_rotulos():
    assert normalizar("N° de documento:") == "N DE DOCUMENTO"
    assert normalizar("Número de documento") == "N DE DOCUMENTO"
    assert normalizar("CÓDIGO DE VERIFICACIÓN") == "CODIGO DE VERIFICACION"


def test_valores_por_clase():
    assert normalizar_valor("clases", "A2, B y D") == ["A2", "B", "D"]
    assert normalizar_valor("numero", "5,8") == 5.8
    assert normalizar_valor("codigo", "Sin dígitos") is None


def test_mrz_td1_ejemplo_icao():
    # ejemplo de la norma ICAO 9303 parte 5
    lineas = ["I<UTOD231458907<<<<<<<<<<<<<<<", "7408122F1204159UTO<<<<<<<<<<<6", "ERIKSSON<<ANNA<MARIA<<<<<<<<<<"]
    c = interpretar_td1(lineas)
    assert c.valido, c.datos["controles"]
    assert c.datos["numero_documento"] == "D23145890"
    assert c.datos["fecha_nacimiento"] == "1974-08-12"
    assert c.datos["apellidos"] == "ERIKSSON" and c.datos["nombres"] == "ANNA MARIA"
    assert digito_icao("D23145890") == "7"


def test_qr_cedula_chilena():
    datos, valido = interpretar_codigo("https://portal.sidiv.registrocivil.cl/docstatus?RUN=12794536-5&type=CEDULA&serial=512794536")
    assert valido and datos["run"] == "12794536-5" and datos["numero_documento"] == "512794536"
    datos, valido = interpretar_codigo("CONTENIDO-DESCONOCIDO")
    assert valido is None  # formato no reconocido: no es adulteración


def _char(t, x, fuente="Georgia-Bold", orden=0, y=100, w=7.0):
    return Pal(t, (x, y, x + w, y + 12), fuentes=(fuente,), tams=(12.0,), orden=orden)


def test_superposicion_capa_distinta():
    visibles = [_char(c, 100 + 8 * i, orden=i) for i, c in enumerate("2026")]
    fantasma = [_char(c, 101.5 + 7.6 * i, fuente="Georgia", orden=10 + i) for i, c in enumerate("2024")]
    sombra = [_char(c, 100 + 8 * i, orden=20 + i) for i, c in enumerate("2026")]  # sombra idéntica: se ignora
    extra = [_char(c, 200 + 8 * i, orden=30 + i) for i, c in enumerate("ABCDEFGHIJ")]  # da peso a la fuente dominante
    limpios, sup = depurar_superpuestas(visibles + fantasma + sombra + extra)
    assert "".join(c.t for c in limpios if c.bbox[0] < 150) == "2026"
    assert len(sup) == 1 and sup[0].texto_debajo.replace(" ", "") == "2024"


def test_sin_superposicion_en_texto_normal():
    chars = [_char(c, 100 + 7.2 * i, orden=i) for i, c in enumerate("NOMBRE")]
    limpios, sup = depurar_superpuestas(chars)
    assert len(limpios) == 6 and not sup


def test_agrupar_columnas():
    palabras = [Pal("NOMBRE", (57, 222, 90, 230)), Pal("RUT", (315, 222, 330, 230)), Pal("Juan", (57, 234, 80, 246)), Pal("Pérez", (84, 234, 115, 246))]
    segs = agrupar(palabras, 1)
    assert [s.texto for s in segs] == ["NOMBRE", "RUT", "Juan Pérez"]
