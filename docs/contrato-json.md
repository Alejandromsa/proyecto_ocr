# Contrato JSON v1.0

Cada documento procesado produce un único JSON que se valida con Pydantic (`src/stracon_ocr/contrato.py`) antes de guardarse. El JSON Schema se exporta a `docs/contrato-v1.schema.json`. Si un cambio rompe la compatibilidad, sube `contrato_version`.

Reglas comunes a todo el contrato:
- **Coordenadas.** Los `bbox` van como `[x0, y0, x1, y1]` en puntos PDF (1/72 pulgada), con origen arriba a la izquierda y relativos a su página (`dpi: null`). En las imágenes se usan píxeles y la página declara `dpi` (el de la imagen, o 96 si no lo trae). En el texto de un Word no hay coordenadas (`bbox: null`).
- **Confianzas.** Siempre de 0 a 1. Se normaliza la escala de cada motor: Tesseract usa 0–100.
- **Fechas.** En ISO 8601 (`2026-10-21`). Las marcas de tiempo llevan zona horaria (`2026-10-21T16:20:00-03:00`).
- **Hallazgos.** La librería no decide: emite hallazgos con evidencia y la decisión es del revisor humano.

## Documento

```jsonc
{
  "contrato_version": "1.0",
  "id": "uuid",
  "expediente": "12794536-5",            // id del expediente (opcional): un RUT en datos/dorado o un id opaco por carpeta de postulante
  "archivo": {
    "nombre": "6-certificado-antecedentes.pdf",
    "sha256": "…",
    "mime_real": "application/pdf",      // puremagic
    "mime_declarado": "application/pdf", // según la extensión
    "bytes": 84213,
    "paginas": 1,
    "origen": "pdf_nativo",              // pdf_nativo | pdf_escaneado | imagen | docx | doc
    "metadatos": {                        // para el revisor; según el origen
      "info": {"Producer": "…", "Creator": "…", "CreationDate": "…", "ModDate": "…"},
      "xmp": {}, "revisiones": {"eof": 1, "linealizado": false, "revisiones_extra": 0},
      "firmas": [], "exif": {}, "docx": {}
    }
  },
  "clasificacion": {
    "tipo": "certificado_antecedentes",
    "confianza": 0.97,
    "metodo": "reglas",                  // reglas | estadistico | llm | manual
    "alternativas": [{"tipo": "hoja_vida_conductor", "confianza": 0.02}]
  },
  "paginas": [{
    "n": 1, "ancho": 612, "alto": 792, "dpi": null, "rotacion": 0,
    "fuente_texto": "nativo",            // nativo | ocr | docx
    "motor": "pdfplumber",               // pdfplumber | rapidocr | tesseract | azure_di | python-docx
    "bloques": [{                         // segmentos: frases contiguas de una línea (las columnas van separadas)
      "bbox": [72, 90, 540, 104], "texto": "CERTIFICADO DE ANTECEDENTES", "conf": 1.0,
      "palabras": [{"t": "CERTIFICADO", "bbox": [72, 90, 160, 104], "conf": 1.0,
                    "fuente": "ABCDEF+Arial-Bold", "tam": 12.0}]   // fuente y tam solo en PDF nativo
    }],
    "tablas": [{"bbox": [], "celdas": [[ "…" ]]}],
    "ocr_comparado": {"motor": "rapidocr", "similitud": 0.99}, // PDF nativo: texto nativo frente al OCR del render
    "calidad": {                          // solo páginas leídas por OCR (fotos y escaneos); lo define calidad.py
      "nitidez": 412.3, "brillo": 201.5, "contraste": 61.2, "lado_px": 2339, "dpi": 200,
      "palabras": 87, "confianza_media": 0.962, "fraccion_conf_baja": 0.05, "puntaje_lectura": 80.1, "rotacion_aplicada": 0
    }
  }],
  "codigos": [{
    "tipo": "qr",                         // qr | pdf417 | mrz
    "contenido": "https://…",
    "datos": {"run": "12794536-5", "numero_documento": "…"},
    "pagina": 1, "bbox": [], "valido": true
  }],
  "campos": {
    "rut": {
      "valor": "12.794.536-5", "valor_normalizado": "12794536-5",
      "pagina": 1, "bbox": [300, 210, 380, 222], "conf": 0.98,
      "metodo": "zona",                   // zona (rótulo → valor por posición) | regex | ocr | llm | azure | qr | mrz | nativo
      "regla_id": "certificado_antecedentes.rut.rotulo.v1",
      "evidencia": "RUT → 12.794.536-5"
    }
  },
  "senales": [{
    "id": "pdf.revisiones_incrementales",
    "familia": "integridad",              // integridad | metadatos | estructura | docx | visual | ocr | duplicado
    "severidad": "alta",                  // info | baja | media | alta
    "campo": null, "pagina": null, "bbox": null,
    "detalle": "El PDF tiene 2 revisiones; la última modifica la página 1",
    "evidencia": {"eof": 2, "objetos_modificados": [12, 14]}
  }],
  "observaciones": [{                     // no se puede validar: el revisor pide una aclaración (no es adulteración)
    "id": "contenido.solo_reverso",
    "motivo": "incompleto",               // calidad | incompleto | no_reconocido | archivo | vigencia | no_verificable | faltante
    "detalle": "Se recibió solo el reverso de la licencia de conducir: falta el anverso (frente)…",
    "accion": "solicitar_reenvio",        // solicitar_reenvio | solicitar_original | solicitar_documento | solicitar_vigente | revisar
    "mensaje_postulante": "Recibimos solo el reverso de su licencia de conducir. Por favor envíe también el frente…",
    "documento": null,                    // tipo de documento (en las observaciones del expediente)
    "campo": null, "pagina": null,
    "evidencia": {"marcas_reverso": ["PRIMEROTORGAMIENTO", "RESTRICCIONES"]}
  }],
  "validaciones": [{
    "regla": "rut.modulo11",
    "resultado": "ok",                    // ok | falla | no_aplica
    "campo": "rut",
    "detalle": null,                      // en una falla, la explicación para el revisor
    "evidencia": {                        // por qué: valor leído, cálculo, fechas o formato esperado
      "leido": "12. 345.678-5", "rut": "12.345.678-5", "dv_documento": "5", "dv_calculado": "5",
      "calculo": "8×2 + 7×3 + 6×4 + 5×5 + 4×6 + 3×7 + 2×2 + 1×3 = 138; 138 mod 11 = 6; 11 − 6 = 5",
      "metodo_lectura": "zona", "confianza_lectura": 0.994, "pagina": 1
    }
  }],
  "versiones": {
    "pipeline": "0.1.0", "reglas": "2026.10.21",
    "motores": {"rapidocr": "3.10.0", "tesseract": "5.5.3"},
    "modelo_llm": null
  },
  "procesado_en": "2026-10-21T16:20:00-03:00",
  "duracion_ms": 2140,
  "tiempos_ms": {"ingesta": 140, "ocr": 1900, "parseo": 1920, "codigos": 160, "forense": 30},
  "errores": [],                          // la etapa que falló y su mensaje; el resto del JSON queda válido
  "resumen": {                            // calculado; no decide, solo cuenta
    "senales": 1, "por_severidad": {"info": 0, "baja": 0, "media": 0, "alta": 1},
    "severidad_max": "alta", "observaciones": 0, "validaciones_fallidas": 0,
    "estado": "senal_alta",               // senal_alta | senal_media | observado | sin_senales
    "estado_texto": "Señal alta",         // Señal alta | Señal media | Observado | Sin señales (textos del mockup)
    "requiere_revision": true,
    "vencido": false                     // true si vigencia.estado = vencido
  },
  "vigencia": {                          // calculada con la fecha del análisis; no decide
    "estado": "vigente",                 // vigente | vencido | sin_fecha (aplica pero no se leyó la fecha) | no_aplica
    "texto": "Vigente",
    "fecha_vencimiento": "2027-05-10",    // ISO; antecedentes = emisión + 60 días (reglas.json)
    "dias": 201,                          // días que quedan (negativo si ya venció)
    "regla": "vigencia.licencia", "campo": "fecha_vencimiento", "detalle": null
  }
}
```

`vigencia` dice si el documento está vencido: cédula y licencia por su fecha de vencimiento, hoja de vida por «Fecha próximo control» (si no la trae, `no_aplica`), certificado de antecedentes por su antigüedad (`dias_vigencia_antecedentes` de reglas.json) y título profesional si declara vigencia. Un documento vencido trae además la observación `contenido.vencido` (acción `solicitar_vigente`), así que queda **Observado**.

`resumen.estado` resume el documento para el revisor con esta prioridad: **Señal alta > Señal media > Observado > Sin señales**.
Las señales son indicios de adulteración; las observaciones dicen que el documento **no se puede validar** (foto ilegible,
solo el reverso, datos que no se leen, imagen de un documento electrónico, vencido, archivo dañado) y traen el mensaje para
pedir la aclaración al postulante. Un archivo que no se puede abrir (dañado o con contraseña) no da error: sale como
documento `observado` con `archivo.ilegible`, sin páginas.

Métodos de `campos`:
- `zona`: valor ubicado junto a su rótulo (a la derecha o debajo, en la misma columna).
- `regex`: valor encontrado en una frase del texto (por ejemplo, el primer RUT válido).
- `ocr`: en un PDF nativo, el valor no existe como texto y se leyó del render (va con la señal `pdf.campo_rasterizado`).
- `llm`: respaldo opcional con un LLM local; confianza 0,5 y solo si el valor aparece literal en el texto.
- `mrz`: en la cédula, el valor no se leyó en el anverso y se tomó de la MRZ con su dígito de control correcto.

Regla de identidad del expediente: `expediente` puede ser un id opaco (no se asume que sea un RUT). Solo si tiene forma
de RUT válido se usa como RUT de referencia; si no, `identidad.rut` es el de la cédula o el más repetido entre los
documentos, e `identidad.nombre` es el nombre mejor respaldado (grupos de nombres compatibles; a igualdad, el del texto
nativo del emisor) y en su versión más completa.

## Expediente (contraste entre documentos)

```jsonc
{
  "contrato_version": "1.0",
  "expediente": "12794536-5",
  "documentos": [{"id": "uuid", "tipo": "cedula_identidad", "sha256": "…", "nombre": "1-cedula-identidad.pdf"}],  // la última versión de cada tipo
  "faltantes": ["licencia_conducir"],
  "identidad": {"rut": "12794536-5", "nombre": "…", "fecha_nacimiento": "1985-03-14"},
  "discrepancias": [{
    "campo": "nombre",
    "documentos": ["cedula_identidad", "certificado_estudios_profesionales"],
    "valores": ["JUAN ANDRÉS PÉREZ SOTO", "JUAN ANDRES PERES SOTO"],
    "similitud": 0.93,
    "severidad": "media",
    "detalle": "El apellido no coincide"
  }],
  "fuentes": [{
    "documento": "certificado_antecedentes",
    "fuente": "Registro Civil",
    "estado": "pendiente_revisor",        // coincide | no_coincide | no_disponible | pendiente_revisor
    "folio": "…", "codigo_verificacion": "…", "url": "…"
  }],
  "duplicados": [{"tipo": "sha256", "documento": "…", "otro_expediente": "15419663-3", "otro_documento": "uuid", "distancia": 0}],  // sha256 | phash_pagina
  "observaciones": [{                     // faltantes + observaciones de los documentos vigentes (con "documento" = tipo)
    "id": "expediente.documento_faltante", "motivo": "faltante", "accion": "solicitar_documento",
    "documento": "licencia_conducir", "detalle": "Falta el documento: Licencia de conducir",
    "mensaje_postulante": "Falta su licencia de conducir. Por favor envíelo para completar su expediente."
  }],
  "estado": "observado",                  // calculado: observado | sin_observaciones
  "procesado_en": "…"
}
```

`no_disponible` es un estado de la fuente, **no** una discrepancia. Por tipo vale el documento con más datos (el anverso
antes que el reverso) y, a igualdad, el último procesado: por eso un reverso suelto no deja observado el expediente si
también llegó el anverso.

## Tipos de documento (MVP)

Campos implementados (`src/stracon_ocr/tipos/`). Todos tienen además `nombre` y `rut`. En **negrita**, los campos clave (más señales forenses).

| Clave | Documento | Campos |
|---|---|---|
| `cedula_identidad` | Cédula de identidad | nombres, apellidos, **numero_documento**, **fecha_nacimiento**, fecha_emision, **fecha_vencimiento**, nacionalidad, sexo + MRZ/QR en `codigos` |
| `licencia_conducir` | Licencia de conducir | **clases** (lista), **folio**, fecha_emision (último control), **fecha_vencimiento** (fecha de control), municipalidad, direccion, restricciones |
| `certificado_estudios_medios` | Certificado de estudios medios | establecimiento, **anio_egreso**, **folio**, promedio, fecha_emision |
| `certificado_estudios_profesionales` | Certificado de estudios profesionales | **numero_certificado**, institucion, titulo, area, fecha_titulacion, **fecha_emision**, **vigencia** |
| `certificado_antecedentes` | Certificado de antecedentes | **folio**, **codigo_verificacion**, **fecha_emision**, resultado (`Sin antecedentes` / `Con antecedentes`), fines (`ESPECIALES` / `PARTICULARES`) |
| `hoja_vida_conductor` | Hoja de vida del conductor | **folio**, **codigo_verificacion**, **clases**, **fecha_control** (solo si la trae: en las reales aparece únicamente en licencias de duración restringida), fecha_emision, anotaciones |
| `validacion_emisor` | Copia de verificación del emisor (portal del Registro Civil: el certificado con la marca «COPIA» y «Copia no válida como certificado»). No es obligatoria ni cuenta como el certificado | **folio**, **codigo_verificacion**, fecha_emision, documento_verificado (`certificado_antecedentes` / `hoja_vida_conductor`) |
| `desconocido` | Sin clasificar | — |

Los rótulos que reconoce cada campo están en el mismo archivo del tipo (por ejemplo, "N° de documento", "Número de documento").

Notas de la calibración con documentos reales (oct 2026):
- Cédula (formato 2013 en adelante): `numero_documento` es el patrón impreso `XXX.XXX.XXX` (9 caracteres, puede empezar
  con letras); las fechas se ordenan nacimiento < emisión < vencimiento y la MRZ las confirma. `nombre` = nombres +
  apellidos (la MRZ completa un apellido perdido).
- Licencia: dos formatos (tarjeta antigua con la clase arriba; tarjeta nueva con la tabla CLASE | PRIMER | ACTUAL
  OTORGAMIENTO | PRÓXIMO CONTROL). En la nueva, `clases` son las filas con fechas, `fecha_emision` el otorgamiento
  actual más reciente y `fecha_vencimiento` el próximo control más lejano.
- Los títulos de técnico de nivel medio del Mineduc («Certificado de título técnico profesional») se clasifican como
  `certificado_estudios_medios`.
- El RUT del postulante es el de una persona natural (< 50.000.000) y se prefiere el rotulado «RUN»: el RUT de la
  institución emisora o de su representante no se toma.
- Un archivo con más de un documento (p. ej. cédula y licencia) se clasifica por el principal y lleva la observación
  `contenido.varios_documentos` con los otros tipos y sus páginas en `evidencia.otros`.
