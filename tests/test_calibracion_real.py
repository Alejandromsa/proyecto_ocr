"""Patrones observados al calibrar con documentos reales (oct 2026), reproducidos con datos inventados.

Ningún valor de estas pruebas corresponde a una persona real: los RUT se generan con el dígito verificador correcto y
los nombres son ficticios.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from stracon_ocr.clasificacion import clasificar, otros_documentos
from stracon_ocr.codigos import digito_icao, interpretar_codigo
from stracon_ocr.config import Config
from stracon_ocr.contraste import contrastar, similitud_nombres
from stracon_ocr.contrato import Archivo, Campo, Clasificacion, Documento, Pagina, Versiones
from stracon_ocr.extraccion import extraer
from stracon_ocr.forense.pdf_estructura import cambio_de_estilo, fuentes_mezcladas, marca_de_agua, texto_superpuesto
from stracon_ocr.layout import Pal, Superposicion, agrupar
from stracon_ocr.pipeline import Procesador, cargar_reglas
from stracon_ocr.texto import dv_rut, es_rut_persona, normalizar_fecha, normalizar_nombre
from stracon_ocr.tipos.licencia import _clases_de
from stracon_ocr.trabajo import DocTrabajo, PaginaTrabajo

REGLAS = cargar_reglas(Config().reglas)


def rut(cuerpo: int) -> str:
    return f"{cuerpo:,}".replace(",", ".") + "-" + dv_rut(cuerpo)


def pagina(lineas: list[tuple[float, float, str]], fuente: str = "ocr", h: float = 10, n: int = 1) -> PaginaTrabajo:
    """Página sintética: (x, y, texto) por línea; cada palabra con su caja."""
    pals = []
    for x, y, texto in lineas:
        for w in texto.split():
            pals.append(Pal(w, (x, y, x + 6 * len(w), y + h), 0.97, orden=len(pals)))
            x += 6 * len(w) + 5
    p = PaginaTrabajo(n=n, ancho=800, alto=1000, dpi=96, fuente_texto=fuente, palabras=pals)
    p.segmentos = agrupar(pals, n)
    return p


def doc(*paginas: PaginaTrabajo, origen: str = "imagen") -> DocTrabajo:
    return DocTrabajo(ruta=Path("x.pdf"), nombre="x.pdf", datos=b"", sha256="0" * 64, mime_real="application/pdf",
                      mime_declarado="application/pdf", origen=origen, paginas=list(paginas))


# --- texto y códigos --------------------------------------------------------------------------------

def test_fecha_con_mes_y_anio_pegados_y_rut_de_institucion():
    assert normalizar_fecha("17 OCT2022") == "2022-10-17"  # cédula leída por OCR
    assert normalizar_fecha("20 sep22026") is None
    assert es_rut_persona(rut(12345678)) and not es_rut_persona(rut(81000000))


def test_pdf417_registro_civil_escapes_reales():
    # escapes observados: '$a' = á minúscula, '%n' = Ñ, '%,' = coma, '%1'/'%2' = paréntesis
    texto = ("$%$20 2026%310%322 500700000002 a1b2c3d4e5f6$GNOMBRE %. ANA MU%nOZ P$EREZ $CR.U.N. %. 12.794.536%35 "
             "$CP$agina 1 $CRESTRICCI$ON %1Art 4%2%, LENTES")
    datos, valido = interpretar_codigo(texto)
    assert valido and datos["nombre"] == "ANA MUÑOZ PÉREZ"
    assert "Página" in datos["texto"] and "(Art 4), LENTES" in datos["texto"]


# --- clasificación ----------------------------------------------------------------------------------

def test_copia_de_verificacion_no_es_el_certificado():
    p = pagina([(50, 40, "SERVICIO DE REGISTRO CIVIL E IDENTIFICACIÓN"), (400, 40, "FOLIO : 500700000003"),
                (400, 60, "Código Verificación:"), (420, 75, "0a1b2c3d4e5f"), (200, 110, "COPIA DE"),
                (150, 125, "CERTIFICADO DE ANTECEDENTES"), (100, 160, "NOMBRE : ANA PRUEBA SOTO"),
                (100, 180, f"R.U.N. : {rut(12345678)}"), (100, 200, "SIN ANTECEDENTES"),
                (100, 500, "Copia no válida como certificado")], fuente="nativo")
    d = doc(p, origen="pdf_nativo")
    c = clasificar(d)
    assert c.tipo == "validacion_emisor"
    campos = extraer(d, c.tipo)
    assert campos["folio"].valor_normalizado == "500700000003"
    assert campos["documento_verificado"].valor == "certificado_antecedentes"


def test_titulo_tecnico_de_nivel_medio_es_estudios_medios():
    p = pagina([(100, 100, "CERTIFICADO DE TÍTULO TÉCNICO PROFESIONAL"), (60, 140, "Certifico que don(ña) ANA PRUEBA SOTO, RUN"),
                (60, 155, f"{rut(12345678)}, obtuvo el TÍTULO DE TÉCNICO DE NIVEL MEDIO EN MECÁNICA, en el establecimiento"),
                (60, 170, "educacional LICEO DE PRUEBA, comuna de CALAMA."),
                (60, 185, "SECRETARÍA REGIONAL MINISTERIAL DE EDUCACIÓN según consta en el Registro Nº 123 de fecha 01/02/2015"),
                (60, 400, "Ayuda Mineduc")], fuente="nativo")
    d = doc(p, origen="pdf_nativo")
    c = clasificar(d)
    assert c.tipo == "certificado_estudios_medios"
    campos = extraer(d, c.tipo)
    assert campos["anio_egreso"].valor_normalizado == "2015"
    assert campos["nombre"].valor_normalizado == "ANA PRUEBA SOTO"


def test_licencia_y_cedula_en_el_mismo_pdf():
    cedula = pagina([(100, 60, "CÉDULA DE IDENTIDAD"), (200, 60, "REPÚBLICA DE CHILE"), (200, 80, "APELLIDOS"),
                     (200, 120, "NACIONALIDAD"), (200, 160, "FECHA DE NACIMIENTO"), (200, 200, "FIRMA DEL TITULAR"),
                     (100, 300, "Nació en: CALAMA"), (100, 320, "Profesión: No informada")], n=1)
    licencia = pagina([(250, 60, "LICENCIA DE CONDUCTOR"), (260, 80, "REPÚBLICA DE CHILE"), (250, 200, "FECHA DE CONTROL 01/01/2030"),
                       (250, 220, "DIRECTOR DE TRANSITO"), (250, 240, "PRIMER OTORGAMIENTO"), (250, 260, "RESTRICCIONES")], n=2)
    d = doc(cedula, licencia, origen="pdf_escaneado")
    tipo = clasificar(d).tipo
    assert tipo in {"cedula_identidad", "licencia_conducir"}
    otros = otros_documentos(d, tipo)
    assert {o["tipo"] for o in otros} == {"cedula_identidad", "licencia_conducir"} - {tipo}


# --- extracción -------------------------------------------------------------------------------------

def test_cedula_nueva_con_columnas_juntas_y_mrz():
    r = 12345678
    l1 = "INCHL" + "512345678" + digito_icao("512345678") + "<" * 15
    nac, venc = "900115", "300115"
    l2 = nac + digito_icao(nac) + "F" + venc + digito_icao(venc) + "CHL" + f"{r}<{dv_rut(r)}" + "<" + "0"
    l3 = "PRUEBA<SOTO<<ANA<MARIA<<<<<<<<"
    p = pagina([(200, 80, "APELLIDOS"), (200, 86, "PRUEBA"), (200, 100, "SOTO"), (200, 116, "NOMBRES"), (200, 122, "ANA MARIA"),
                (200, 140, "NACIONALIDAD SEXO"), (200, 147, "CHILENA F"),
                (200, 165, "FECHA DE NACIMIENTO NÚMERO DOCUMENTO"), (200, 171, "15 ENE 1990 512.345.678"),
                (200, 190, "FECHA DE EMISIÓN FECHA DE VENCIMIENTO"), (200, 196, "10 MAR 2020 15 ENE2030"),
                (60, 240, f"RUN {rut(r)}"), (60, 400, l1), (60, 415, l2.ljust(30, "<")[:30]), (60, 430, l3)])
    d = doc(p)
    campos = extraer(d, "cedula_identidad")
    assert campos["numero_documento"].valor == "512.345.678"
    assert campos["fecha_nacimiento"].valor_normalizado == "1990-01-15"
    assert campos["fecha_emision"].valor_normalizado == "2020-03-10"
    assert campos["fecha_vencimiento"].valor_normalizado == "2030-01-15"
    assert campos["nombre"].valor_normalizado == "ANA MARIA PRUEBA SOTO"
    assert campos["sexo"].valor == "F"


def test_licencia_nueva_tabla_de_clases():
    p = pagina([(150, 50, "LICENCIA DE CONDUCTOR"), (200, 80, "RUN"), (200, 90, rut(12345678)),
                (200, 110, "APELLIDOS"), (200, 120, "PRUEBA"), (200, 132, "SOTO"), (200, 150, "NOMBRES"), (200, 160, "ANA MARIA"),
                (200, 180, "MUNICIPALIDAD"), (200, 190, "CALAMA"),
                (60, 300, "CLASE"), (150, 300, "PRIMER"), (250, 300, "ACTUAL"), (350, 300, "PRÓXIMO"),
                (150, 312, "OTORGAMIENTO OTORGAMIENTO"), (350, 312, "CONTROL"),
                (60, 330, "C"), (60, 350, "B"), (150, 350, "01/02/2010 05/06/2024 05/06/2030"), (60, 370, "A4"),
                (60, 390, "D"), (150, 390, "03/03/2015 05/06/2024 05/06/2028")])
    campos = extraer(doc(p), "licencia_conducir")
    assert campos["clases"].valor_normalizado == ["B", "D"]
    assert campos["fecha_emision"].valor_normalizado == "2024-06-05"
    assert campos["fecha_vencimiento"].valor_normalizado == "2030-06-05"
    assert campos["nombre"].valor_normalizado == "ANA MARIA PRUEBA SOTO"


def test_linea_de_clases_de_la_tarjeta_antigua():
    assert _clases_de("BC") == ["B", "C"]
    assert _clases_de("A2/A4/C/D") == ["A2", "A4", "C", "D"]
    assert _clases_de("A21A4") == ["A2", "A4"]  # la barra leída como 1
    assert _clases_de("AS A5 Ley 19.495") == ["A5"]
    assert _clases_de("CLASE B D") == ["B", "D"]
    assert _clases_de("CALAMA") is None


def test_rut_del_postulante_y_no_de_la_institucion():
    p = pagina([(60, 100, f"La UNIVERSIDAD DE PRUEBA, RUT N°{rut(81000000)}, cuyo representante legal es JUAN OTRO, RUT N°{rut(6000000)},"),
                (60, 115, f"certifica que ANA PRUEBA SOTO, RUN {rut(12345678)}, se ha certificado en el Perfil Ocupacional OPERADOR.")],
               fuente="nativo")
    campos = extraer(doc(p, origen="pdf_nativo"), "certificado_estudios_profesionales")
    assert campos["rut"].valor_normalizado == f"12345678-{dv_rut(12345678)}"


def test_rut_mal_leido_no_se_reemplaza_por_otra_aparicion():
    """Una foto con un dígito del RUT cambiado deja el DV inválido; si se tomara la otra aparición válida se ocultaría la
    edición (prueba_alteraciones, oct 2026). Se conserva lo leído: falla rut.modulo11 y sale rut.inconsistente."""
    from stracon_ocr.forense import rut_inconsistente

    bueno = rut(12345678)
    malo = bueno[:-1] + ("0" if bueno[-1] != "0" else "1")
    p = pagina([(250, 60, "LICENCIA DE CONDUCTOR"), (250, 100, f"RUN {malo}"), (60, 300, bueno)])
    d = doc(p)
    campos = extraer(d, "licencia_conducir")
    assert campos["rut"].valor == malo
    assert rut_inconsistente(d, campos)[0].severidad == "media"
    # y al revés: si el campo toma la aparición original válida, la editada (DV roto) también cuenta
    campos["rut"] = campos["rut"].model_copy(update={"valor": bueno, "valor_normalizado": bueno.replace(".", "")})
    assert rut_inconsistente(d, campos)


# --- forense ----------------------------------------------------------------------------------------

def _palabra(texto: str, fuentes: list[str]) -> Pal:
    return Pal(texto, (100, 100, 100 + 6 * len(texto), 110), fuentes=tuple(fuentes), tams=tuple(10.0 for _ in texto))


def test_cambio_de_estilo_no_es_tipografia_mezclada():
    # Mineduc: "Nº" en normal pegado al código en negrita (misma familia, tramos de 2+ caracteres)
    w = _palabra("Nº1a2b3c", ["Helvetica"] * 2 + ["Helvetica-Bold"] * 6)
    utiles = [(ch, f, t) for ch, f, t in zip(w.t, w.fuentes, w.tams) if ch.isalnum()]
    assert cambio_de_estilo(utiles)
    p = PaginaTrabajo(n=1, ancho=600, alto=800, dpi=None, fuente_texto="nativo", palabras=[w])
    assert fuentes_mezcladas(p, {}) == []
    # un solo dígito con otra fuente (como la alteración del mockup) sí se informa
    w2 = _palabra("2026", ["Georgia-Bold"] * 3 + ["Georgia"])
    p2 = PaginaTrabajo(n=1, ancho=600, alto=800, dpi=None, fuente_texto="nativo", palabras=[w2])
    assert fuentes_mezcladas(p2, {})


def test_puntuacion_encimada_no_es_texto_reemplazado():
    coma = Pal(",", (100, 100, 103, 110), fuentes=("Times-Roman",), tams=(10.0,))
    tres = Pal("3", (100, 100, 106, 110), fuentes=("Bookman",), tams=(10.0,))
    p = PaginaTrabajo(n=1, ancho=600, alto=800, dpi=None, fuente_texto="nativo", superposiciones=[Superposicion([coma], [tres])])
    assert texto_superpuesto(p, {}) == []


def test_marca_de_agua_translucida_frente_a_parche():
    palabras = [Pal(f"p{i}", (100 + 40 * (i % 4), 100 + 20 * (i // 4), 130 + 40 * (i % 4), 110 + 20 * (i // 4))) for i in range(16)]
    assert marca_de_agua(palabras, palabras[:3])  # la marca solo tapa las letras que cruza
    assert not marca_de_agua(palabras, palabras)  # todo oculto: un parche opaco
    assert not marca_de_agua(palabras[:2], palabras[:1])  # pocas palabras: parche sobre un dato


def test_pdf_solo_imagen_armado_por_el_postulante_no_es_senal_media(proc_sin_bd, tmp_path):
    """Un certificado impreso a PDF como imagen con "Microsoft: Print To PDF": productor y revisiones quedan en baja; lo
    que corresponde es pedir el original (observación), no marcarlo como adulterado."""
    import pikepdf
    import pypdfium2 as pdfium

    from .conftest import HOY, mockup

    img = pdfium.PdfDocument(str(mockup("16884102-7/6-certificado-antecedentes.pdf")))[0].render(scale=150 / 72).to_pil().convert("RGB")
    ruta = tmp_path / "impreso.pdf"
    img.save(ruta, "PDF", resolution=150)
    with pikepdf.open(ruta, allow_overwriting_input=True) as pdf:
        pdf.docinfo["/Producer"] = "Microsoft: Print To PDF"
        pdf.save(ruta)
    d = proc_sin_bd.procesar(ruta, guardar=False, hoy=HOY)
    prod = [s for s in d.senales if s.id == "meta.productor_no_esperado"]
    assert prod and prod[0].severidad == "baja"
    assert d.resumen["estado"] in {"observado", "sin_senales"}, [(s.id, s.severidad, s.detalle) for s in d.senales]
    assert any(o.id == "contenido.imagen_de_electronico" for o in d.observaciones)


# --- contraste --------------------------------------------------------------------------------------

def test_nombres_compatibles_y_distintos():
    assert similitud_nombres("ANA MARIA PRUEBA SOTO", "ANA MARIA PRUEBASOTO")[0] == 1.0  # palabras pegadas
    assert similitud_nombres("ANA MARIA PRUEBA SOTO", "A NA MARIA PRUEBA SOTO")[0] == 1.0  # espacio del OCR
    assert similitud_nombres("ANA MARIA PRUEBA SOTO", "ANA MARIA PRUEBA")[0] == 1.0  # segundo apellido omitido
    assert similitud_nombres("ANA MARIA PRUEBA SOTO", "ANA MARI PRUEBA SOTO")[0] == 1.0  # MRZ truncada
    assert similitud_nombres("PRUEBA SOTO ANA MARIA", "ANA MARIA PRUEBA SOTO")[0] == 1.0  # orden APELLIDOS NOMBRES
    assert similitud_nombres("ANA MARIA PRUEBA SOTO", "JUAN CARLOS OTRO DIAZ")[0] < 0.6


def _doc(tipo: str, campos: dict[str, str], origen: str = "pdf_nativo", fuente: str = "nativo") -> Documento:
    return Documento(id=tipo, expediente="exp-opaco-1",
                     archivo=Archivo(nombre=f"{tipo}.pdf", sha256=tipo, mime_real="application/pdf", mime_declarado="application/pdf",
                                     bytes=1, paginas=1, origen=origen),
                     clasificacion=Clasificacion(tipo=tipo, confianza=1.0),
                     paginas=[Pagina(n=1, ancho=1, alto=1, fuente_texto=fuente, motor="x")],
                     campos={k: Campo(valor=str(v), valor_normalizado=v, pagina=1, metodo="zona") for k, v in campos.items()},
                     versiones=Versiones(pipeline="t", reglas="t"), procesado_en=datetime.now(timezone.utc))


def test_contraste_con_expediente_opaco_y_formatos_de_nombre():
    r = f"12345678-{dv_rut(12345678)}"
    docs = [
        _doc("certificado_antecedentes", {"rut": r, "nombre": "ANA MARIA PRUEBA SOTO", "folio": "500700000004",
                                          "codigo_verificacion": "0A1B2C3D4E5F"}),
        _doc("hoja_vida_conductor", {"rut": r, "nombre": "ANA MARÍA PRUEBA SOTO", "clases": ["A2", "A4", "B", "D"]}),
        _doc("licencia_conducir", {"rut": r, "nombre": "ANA MARIA PRUEBA", "clases": ["A4", "B"]}, "imagen", "ocr"),
        _doc("certificado_estudios_profesionales", {"rut": r, "nombre": "Ana María PruebaSoto"}),
        _doc("validacion_emisor", {"rut": r, "folio": "500700000004", "codigo_verificacion": "0A1B2C3D4E5F"}),
    ]
    exp = contrastar("exp-opaco-1", docs, REGLAS)
    assert exp.identidad.rut == r and normalizar_nombre(exp.identidad.nombre) == "ANA MARIA PRUEBA SOTO"
    assert exp.discrepancias == []  # sin falsas discrepancias por formato del nombre ni por clases históricas
    fuente = next(f for f in exp.fuentes if f.documento == "certificado_antecedentes")
    assert fuente.estado == "coincide"  # la copia de verificación trae el mismo folio, código y RUN
    # un nombre y un RUT realmente distintos siguen saliendo
    otro = _doc("certificado_estudios_medios", {"rut": f"11111111-{dv_rut(11111111)}", "nombre": "JUAN CARLOS OTRO DIAZ"})
    exp = contrastar("exp-opaco-1", docs + [otro], REGLAS)
    assert {(x.campo, x.severidad) for x in exp.discrepancias} >= {("rut", "alta"), ("nombre", "alta")}


def test_licencia_con_clase_que_no_esta_en_la_hoja_de_vida():
    r = f"12345678-{dv_rut(12345678)}"
    docs = [_doc("hoja_vida_conductor", {"rut": r, "clases": ["B"]}), _doc("licencia_conducir", {"rut": r, "clases": ["A4", "B"]})]
    exp = contrastar("exp-opaco-2", docs, REGLAS)
    assert any(x.campo == "clases" for x in exp.discrepancias)


# --- duplicados -------------------------------------------------------------------------------------

class _RepoFalso:
    def __init__(self, docs):
        self.docs = docs

    def documentos_de_expediente(self, expediente):
        return self.docs.get(expediente, [])

    def obtener_documento(self, doc_id):
        return None


def test_mismo_archivo_del_mismo_postulante_en_otro_expediente_es_info():
    from stracon_ocr.contrato import Duplicado

    r = f"12345678-{dv_rut(12345678)}"
    proc = Procesador(Config(), guardar=False)
    proc.repo = _RepoFalso({"cargo-a/post-1": [_doc("certificado_antecedentes", {"rut": r})],
                            "cargo-b/post-9": [_doc("hoja_vida_conductor", {"rut": r})],
                            "cargo-c/post-5": [_doc("hoja_vida_conductor", {"rut": f"11111111-{dv_rut(11111111)}"})]})
    d = doc(pagina([(10, 10, "x")]))
    dup = Duplicado(tipo="sha256", documento="abc", otro_expediente="cargo-b/post-9", otro_documento="z")
    assert proc._senales_duplicado([dup], d, "certificado_estudios_medios", "cargo-a/post-1")[0].severidad == "info"
    dup = Duplicado(tipo="sha256", documento="abc", otro_expediente="cargo-c/post-5", otro_documento="z")
    assert proc._senales_duplicado([dup], d, "certificado_estudios_medios", "cargo-a/post-1")[0].severidad == "alta"
    # página parecida de una plantilla del emisor (certificado): no es evidencia
    dup = Duplicado(tipo="phash_pagina", documento="pagina 1", otro_expediente="cargo-c/post-5", otro_documento="z", distancia=0)
    assert proc._senales_duplicado([dup], d, "certificado_antecedentes", "cargo-a/post-1") == []
