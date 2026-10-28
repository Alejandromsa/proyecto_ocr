# Catálogo de señales del MVP

Cada señal es determinista, explicable y reproducible. La severidad es la que se usa por defecto; se ajusta por emisor en `reglas/*.yaml` y se calibra con documentos auténticos. Ninguna señal decide nada: el revisor valida cada una con un comentario.

**Severidad:** `alta` = indicio fuerte de alteración · `media` = requiere mirar · `baja` = contexto · `info` = solo se registra.

## Integridad del PDF (`integridad`)

| Id | Qué detecta | Cómo | Sev. |
|---|---|---|---|
| `pdf.revisiones_incrementales` | Se modificó el PDF después de generarlo | Más de un `%%EOF`. Cada actualización se clasifica: **firma** (trae `/ByteRange`; así firman el Registro Civil y el Mineduc), **vacía** (sin objetos nuevos; p. ej. Word) o **contenido**. Solo «contenido» es alerta: alta en PDF del emisor, baja en un escaneo o un PDF que es solo imagen armado por el postulante. Firma o vacía = `info` | alta / baja / info |
| `codigo.no_coincide` | El texto visible no coincide con el código del emisor | El PDF417 del Registro Civil (folio, código, RUN, nombre, fecha de emisión) o el QR/MRZ de la cédula difieren de lo que se lee en el documento | alta |
| `pdf.firma_invalida` | Firma PAdES rota o certificado no confiable | pyHanko `validate_pdf_signature` | alta |
| `pdf.cambios_post_firma` | Contenido agregado después de la firma | pyHanko: cobertura y nivel de modificación. Varias firmas (sistema + secretario general) no son alerta si lo agregado después no cambia contenido (NONE, LTA) o es otra firma (FORM_FILLING y solo revisiones de firma) | alta |
| `pdf.cifrado_o_restringido` | No se puede analizar por completo | Con contraseña de apertura: media. Solo permisos restringidos (se abre sin contraseña, como Acrobat Sign): baja, y pyHanko lo descifra para validar la firma | media / baja |

## Metadatos (`metadatos`)

| Id | Qué detecta | Cómo | Sev. |
|---|---|---|---|
| `meta.productor_no_esperado` | El PDF salió de un editor y no del sistema emisor | Producer/Creator en la lista de editores (iLovePDF, Smallpdf, Sejda, Photoshop, Word, "Print To PDF", Canva). Alta en documentos que el emisor entrega en PDF (antecedentes, hoja de vida, copia de verificación); media en el resto; baja si el PDF es solo imagen (el editor solo empaquetó una foto o un escaneo: lo que corresponde es pedir el original); `info` en cédula y licencia, que el postulante escanea y arma él mismo | alta / media / baja / info |
| `meta.fechas_incoherentes` | Edición posterior | ModDate distinta de CreationDate; fechas en el futuro. `info` si la diferencia la explica una firma electrónica válida agregada como revisión incremental | media / info |
| `meta.info_xmp_distintos` | Metadatos reescritos a medias | Info frente a XMP: fechas, CreatorTool, DocumentID e InstanceID | media |
| `meta.historial_xmp` | Pasó por un editor | Entradas en `xmpMM:History` (ExifTool) | media |
| `img.exif_editor` | La imagen pasó por un editor | Campos EXIF `Software` y `ProcessingSoftware`; miniatura distinta de la imagen | media |
| `img.generada_ia` | Imagen declarada como generada por IA | C2PA/IPTC `trainedAlgorithmicMedia` | alta |

## Estructura del contenido (`estructura`)

| Id | Qué detecta | Cómo | Sev. |
|---|---|---|---|
| `pdf.fuentes_mezcladas_campo` | Texto reemplazado con otra tipografía | pdfplumber: fuente, subset o tamaño distinto dentro de una palabra. No cuenta un cambio de estilo de la misma familia en tramos de 2+ caracteres y mismo tamaño («Nº» normal + código en negrita) | alta |
| `pdf.texto_superpuesto` | Texto escrito encima de otro | bbox de caracteres que se solapan con contenido distinto (letras o dígitos en las dos capas: una coma sobre un dígito es composición) | alta |
| `pdf.objeto_tapa_texto` | Un rectángulo blanco o una imagen oculta texto | Un rect o una imagen cuyo bbox cubre caracteres que el OCR del render no ve. No cuenta una marca de agua translúcida: cubre 8+ palabras en 3+ líneas y el OCR sigue leyendo la mayoría | alta |
| `pdf.capas_ocultas` | Contenido oculto | OCG con visibilidad apagada; anotaciones FreeText sobre el contenido | media |
| `pdf.solo_imagen` | Se reimprimió o escaneó un documento que debería ser nativo | Sin capa de texto, cuando el tipo lo entrega el emisor en PDF o cuando el propio texto dice que era electrónico ("firmado electrónicamente", "timbre electrónico", "código de verificación"). Genera la observación `contenido.imagen_de_electronico` (pedir el original) | baja |
| `rut.inconsistente` | Se editó una sola aparición del RUT | El RUT del campo y otra aparición en el documento difieren en 1 o 2 dígitos (la licencia lo repite junto a la foto; los certificados, en el encabezado y las tablas). Media si se leyó por OCR (cuentan también las apariciones con DV inválido: así queda la aparición editada en una foto) | alta / media |
| `pdf.texto_nativo_distinto_ocr` | El texto declarado no coincide con lo que se ve | Diferencia entre el texto nativo y el OCR del render por campo (CER > umbral). Si el OCR no lee nada (marca de agua o sello encima) no se marca | alta |
| `pdf.campo_rasterizado` | El valor de un campo es una imagen dentro de un PDF con texto | El rótulo existe como texto pero su valor no: solo aparece en el OCR del render o hay una imagen bajo el rótulo | alta |

## DOCX (`docx`)

| Id | Qué detecta | Cómo | Sev. |
|---|---|---|---|
| `docx.certificado_en_word` | Un certificado oficial llegó en un formato editable | Tipo de documento oficial con origen docx | alta |
| `docx.cambios_rastreados` | Ediciones visibles en el historial | `w:ins`/`w:del` y comentarios | alta |
| `docx.rsid_campo_distinto` | Un campo clave se editó en otra sesión | El rsid de los runs del campo difiere del rsid dominante | media |
| `docx.metadatos_edicion` | Rastro de edición | `lastModifiedBy`, `revision` alto, `TotalTime` y `Template` | baja |

## Visual (`visual`)

| Id | Qué detecta | Cómo | Sev. |
|---|---|---|---|
| `pdf.imagen_baja_nitidez` | Firma, timbre o dato pegado con otra resolución | Imagen incrustada en un PDF con texto nativo cuya varianza del laplaciano (en el render) está bajo el umbral | media |
| `img.ela_zona` | Zona con distinta compresión | Mapa ELA con la zona del campo sobre el umbral calibrado | baja |
| `img.doble_compresion` | Pegado de otra imagen JPEG | Tablas de cuantización y BAG por bloque | baja |
| `img.copy_move` | Sello, firma o dígito clonado | Coincidencias ORB/SIFT dentro de la misma imagen | media |
| `img.recaptura_pantalla` | Foto tomada a una pantalla | Picos de moiré en la FFT | media |

## OCR y plantilla (`ocr`)

| Id | Qué detecta | Cómo | Sev. |
|---|---|---|---|
| `ocr.confianza_anomala` | Campo pegado o retocado | z-score de la confianza por palabra frente a la página, solo en campos clave y en palabras con dígitos que forman el valor. Baja en cédula y licencia (fondo de seguridad); `info` si el código del emisor (MRZ, PDF417, QR) confirma el valor | media / baja / info |
| `ocr.geometria_caracteres` | Dígito con otra tipografía | Varianza de alto y ancho de los caracteres del campo | media |
| `ocr.linea_base` | Texto desalineado | Residuo de una regresión lineal de los bordes inferiores | media |
| `ocr.fuera_de_plantilla` | Campo movido | bbox del campo fuera de la zona del emisor (con anclas y homografía ORB) | media |
| `ocr.motores_discrepan` | Lectura ambigua de un dígito | RapidOCR y Tesseract leen distinto un campo clave | baja |

## Duplicados (`duplicado`)

| Id | Qué detecta | Cómo | Sev. |
|---|---|---|---|
| `dup.archivo_identico` | El mismo archivo en otro postulante | SHA-256 repetido en la BD. `info` si los dos expedientes son del mismo postulante (coincide el RUT más repetido de sus otros documentos) | alta / info |
| `dup.pagina_similar` | La misma foto de una tarjeta reutilizada | pHash con distancia ≤ umbral en otro expediente, solo en fotos y escaneos de cédula o licencia frente a otra tarjeta. Los certificados de un emisor comparten plantilla y no se comparan | media / info |
| `dup.foto_firma` | La misma foto de carnet o firma | pHash del recorte | alta |

## Validaciones (en `validaciones` del documento)

| Regla | Qué verifica |
|---|---|
| `rut.modulo11` | El dígito verificador del RUT |
| `mrz.digitos_control` | Los dígitos de control ICAO 9303 de la MRZ TD1 |
| `qr.coincide_ocr` | Los datos del código del emisor coinciden con el documento: RUN, número de documento y fecha de nacimiento (QR/MRZ de la cédula); folio, código de verificación, RUN, nombre y fecha de emisión (PDF417 del Registro Civil) |
| `fechas.logicas` | Fechas en orden (nacimiento < emisión < vencimiento) y ninguna en el futuro |
| `vigencia.antecedentes` | Emitido hace 60 días o menos (umbral configurable) |
| `vigencia.licencia` | La licencia (o el próximo control de la hoja de vida) no está vencida |
| `vigencia.certificado` | El certificado de estudios profesionales sigue vigente |
| `formato.folio` | El folio y el código tienen el formato del emisor (regex) |

## Contraste por expediente (en `discrepancias` del expediente)

| Campo | Comparación |
|---|---|
| RUT | Igualdad exacta normalizada entre los documentos y frente a la carpeta |
| Nombre | rapidfuzz `token_sort_ratio` sobre nombres normalizados. Por debajo de 0,90 es discrepancia; entre 0,90 y 0,97 se marca como posible error de tipeo |
| Fecha de nacimiento | Igualdad exacta |
| Fuente emisora | Estado `coincide`, `no_coincide`, `no_disponible` o `pendiente_revisor`. **`no_disponible` no es discrepancia** |
| Clases de licencia | La licencia de conducir frente a la hoja de vida del conductor |

## Observaciones (en `observaciones` del documento y del expediente)

Una observación **no es una adulteración**: dice que el documento no se puede validar bien y que hay que pedir una
aclaración al postulante. Cada una trae la acción y un mensaje sugerido (`mensaje_postulante`). El documento queda con
`resumen.estado = "observado"` si no tiene señales media o alta (prioridad: Señal alta > Señal media > Observado > Sin señales).

| Id | Motivo | Cuándo | Acción |
|---|---|---|---|
| `archivo.ilegible` | archivo | El archivo no se puede abrir: dañado, incompleto o protegido con contraseña | Solicitar reenvío |
| `calidad.*` | calidad | Foto o escaneo ilegible, borroso, oscuro, sobreexpuesto o de baja resolución (detalle abajo) | Solicitar reenvío |
| `contenido.tipo_no_reconocido` | no_reconocido | No se reconoce qué documento es | Solicitar reenvío |
| `contenido.campos_faltantes` | incompleto | No se leen los datos obligatorios del tipo (`TipoSpec.obligatorios`): cédula RUT y N° de documento; licencia RUT y fecha de control; hoja de vida RUT, folio y código; antecedentes RUT, folio, código y fecha de emisión; estudios medios nombre y RUT; título profesional, el nombre (el título AIEP no trae RUT) | Solicitar reenvío |
| `contenido.solo_reverso` | incompleto | Licencia (o cédula) sin RUT y con las marcas del reverso (primer otorgamiento, restricciones, firma del conductor, director de tránsito; MRZ en la cédula) | Solicitar reenvío (el frente) |
| `contenido.imagen_de_electronico` | no_verificable | Foto o escaneo de un documento que el emisor entrega como PDF electrónico (señal `pdf.solo_imagen`, o foto con "firmado electrónicamente", "código de verificación"…) | Solicitar PDF original |
| `contenido.vencido` | vigencia | Falla `vigencia.antecedentes`, `vigencia.licencia` o `vigencia.certificado` | Solicitar documento vigente |
| `expediente.documento_faltante` | faltante | Falta uno de los 6 tipos del expediente | Solicitar documento |

En el expediente se copian las observaciones de los documentos **vigentes** (el de más datos por tipo): un reverso suelto
no deja observado el expediente si también llegó el anverso. No se generan observaciones por señales de adulteración ni
por `formato.*`, que revisa el revisor.

Con los 22 documentos reales de la prueba, solo quedan observados los 4 reversos de licencia enviados como archivo aparte
(`contenido.solo_reverso`); los demás quedan «Sin señales».

## Calibración con 133 documentos reales (28 oct 2026)

Conjunto: una convocatoria real (11 cargos, 36 carpetas de postulante, 133 PDF: certificados del Registro Civil nativos y
escaneados, copias del portal de verificación, licencias y cédulas fotografiadas, certificados del Mineduc y títulos de
siete instituciones). Herramienta: `herramientas/calibrar_real.py` (sus salidas quedan fuera del repositorio). Casi todos
son auténticos, así que cada señal media o alta en ellos se revisó como posible falso positivo.

| | Antes | Después |
|---|---|---|
| Documentos «Sin señales» | 24 | 106 |
| «Señal media» / «Señal alta» | 71 / 22 | 1 / 1 |
| Señales media o alta | 873 (808 de `dup.pagina_similar`) | 2 |

Relajaciones (cada una con su prueba en `tests/test_calibracion_real.py` o `tests/test_pipeline.py`):

| Señal | Falso positivo observado | Ajuste |
|---|---|---|
| `dup.pagina_similar` | Certificados del Registro Civil y del Mineduc de personas distintas quedan a distancia pHash 0–2, incluso escaneados | Solo fotos y escaneos de tarjetas |
| `pdf.fuentes_mezcladas_campo` | Texto justificado sin espacios pegaba palabras en negrita y normal; «Nº» + código | Palabras separadas por huecos de 0,2 em; el cambio de estilo de una misma familia no cuenta |
| `pdf.objeto_tapa_texto` | Marca de agua «COPIA» (imagen con máscara de color) de las copias de verificación | La marca de agua translúcida no cuenta |
| `meta.productor_no_esperado` | «Microsoft: Print To PDF», iLovePDF, Canva en PDF que son solo imagen | Baja en PDF sin texto |
| `pdf.solo_imagen` | 10 certificados electrónicos escaneados o impresos | Baja + observación de pedir el original |
| `pdf.cambios_post_firma` | Título con dos firmas (sistema y secretaria general) | Una firma posterior no es contenido |
| `pdf.cifrado_o_restringido`, `pdf.firma_invalida` | Título de Acrobat Sign cifrado solo con permisos | Baja; se descifra para validar la firma |
| `meta.fechas_incoherentes` | ModDate posterior por la firma electrónica | `info` si la firma es válida y lo único agregado |
| `codigo.no_coincide` | Escape `%n` (Ñ) del PDF417 sin decodificar; MRZ mal leída frente a un rótulo | Escapes reales; no se compara un campo cuyo dígito de control MRZ falla ni un valor sin dígitos |
| `ocr.confianza_anomala` | Logos, dirección, meses («oct») y fechas de la cédula sobre el fondo de seguridad | Campos clave y palabras con dígitos; baja en tarjetas; `info` si el código del emisor confirma |
| `pdf.revisiones_incrementales` | Foto exportada del celular y anotada (PDF solo imagen) | Baja en PDF solo imagen |
| `dup.archivo_identico` | El mismo archivo en la carpeta del postulante y en una de documentos genéricos | `info` si ambos expedientes son del mismo postulante. Depende del orden de proceso: si el expediente aún no tiene otros documentos queda alta |

Lo que sigue saliendo en los auténticos: un título en PDF nativo guardado con iLovePDF (media, correcto: hay que
verificarlo con la institución) y el archivo repetido en la carpeta de documentos genéricos (alta, ver arriba).

Detección conservada: las pruebas con los PDF alterados de `datos/dorado` siguen pasando y
`herramientas/prueba_alteraciones.py` sobre 12 documentos de este conjunto (64 copias alteradas) detecta 58: editor 8/8, tapado incremental 8/8, tapado reescrito 8/8, dígito encimado 8/8, rasterizado 8/8 (por la observación de pedir el original), foto con EXIF de editor 12/12 y foto editada sin metadatos 6/12 (certificados del Registro Civil por el PDF417 y licencias por el RUT repetido; no en cédulas, certificados del Mineduc ni títulos, donde el RUT aparece una vez y no hay código legible). Ningún original da señal media o alta.
Se probaron y se descartaron dos relajaciones de `rut.inconsistente` porque ocultaban fotos editadas (el falsificador
cambia un dígito y deja el DV roto): tomar «la otra aparición válida» cuando el RUT leído no cumple el módulo 11, e
ignorar en OCR las apariciones con DV inválido. El RUT leído se conserva (falla `rut.modulo11`) y la señal se mantiene
media en OCR; en el conjunto real queda 1 caso legítimo (un dígito mal leído en una cédula fotocopiada).

## Prueba con documentos reales (22 oct 2026)

22 documentos reales de 6 postulantes: fotos y escaneos de licencias (anverso y reverso), certificados del Registro Civil
con firma electrónica, certificados del Mineduc y un certificado de título escaneado. Resultado: 22/22 clasificados, RUT
correcto en todos los que lo traen, 9/9 PDF417 del Registro Civil decodificados y coincidentes, y ninguna señal media o
alta en documentos auténticos. Lo que se corrigió a partir de esa prueba: las revisiones por firma electrónica ya no son
alerta, el editor solo es alerta en documentos del emisor, formatos reales de folio y código del Registro Civil, rótulos y
fechas sin espacios, certificados en texto corrido y la estructura de la tarjeta de licencia.

### Adulteraciones sobre documentos reales (`herramientas/prueba_alteraciones.py`)

Sobre los mismos documentos reales se generaron 84 copias con el RUT cambiado en un dígito, con 7 técnicas. Se detectan
81 (96 %) y ningún original da falsa alerta:

| Técnica | Detectadas | Cómo se detecta |
|---|---|---|
| Volver a guardar con un editor (iLovePDF) | 10/10 | Productor del PDF y firma electrónica rota |
| Tapar el RUT y escribir otro, guardado incremental | 10/10 | Cambios después de la firma y texto encimado |
| Tapar y escribir, PDF reescrito | 10/10 | Firma rota y texto encimado |
| Dibujar otro dígito encima | 10/10 | Texto encimado y firma rota |
| Convertir el PDF en imagen | 10/10 | PDF sin texto de un documento electrónico |
| Foto editada con metadatos de Photoshop | 17/17 | EXIF del editor y, en certificados del Registro Civil, código PDF417 distinto |
| Foto editada sin metadatos | 14/17 | Código PDF417 del Registro Civil o RUT repetido distinto. **No se detecta** en certificados del Mineduc ni títulos escaneados (el RUT aparece una sola vez y no hay código legible): ahí solo sirve verificar en el sitio del emisor |

Con Tesseract 5.4 como segundo motor (23 oct 2026) el resultado es el mismo: 81/84 y 0 falsas alertas. Esas fotos editadas
quedaron limpias y los dos motores leen lo mismo; en los originales, `ocr.motores_discrepan` solo aparece como nota baja en
4 de 22 fotos y escaneos.

## Estado de implementación (21 oct 2026)

**Implementadas:** todas las de integridad, metadatos, estructura y DOCX; `pdf.imagen_baja_nitidez`, `img.ela_zona`,
`img.exif_editor`, `img.generada_ia`, `ocr.confianza_anomala`, `ocr.motores_discrepan` (requiere Tesseract),
`dup.archivo_identico`, `dup.pagina_similar`, todas las validaciones y el contraste.

**Pendientes:** `img.doble_compresion`, `img.copy_move`, `img.recaptura_pantalla`, `ocr.geometria_caracteres`,
`ocr.linea_base`, `ocr.fuera_de_plantilla` (requiere plantillas reales por emisor) y `dup.foto_firma` (requiere ubicar la foto
y la firma en la cédula).

**Medición sobre los 135 PDF del mockup** (`socr evaluar`): las 11 alteraciones detectables en el documento se detectan
(recall 1,0) y ninguno de los 124 documentos limpios tiene señales media o alta. Las 4 alteraciones que solo cambian un
valor (sin rastro en el archivo) quedan para el contraste con el emisor o entre documentos. Los umbrales están calibrados
con documentos ficticios: hay que recalibrarlos con documentos auténticos del cliente.

## Calidad de imagen (observaciones, motivo `calidad`, acción `solicitar_reenvio`)

Solo en páginas leídas por OCR (fotos y escaneos). Una página con texto bien leído (≥ 12 palabras, confianza media
≥ 0,85 y ≤ 35 % de palabras dudosas) no se observa por métricas de imagen, salvo brillo o contraste extremos. Las
páginas giradas 90°, 180° o 270° se enderezan solas: el giro queda en `paginas[].calidad.rotacion_corregida` y la
imagen de la página se muestra enderezada. Umbrales en `reglas.json["calidad"]`.

| Id | Qué detecta | Cómo |
|---|---|---|
| `calidad.ilegible` | Casi no hay texto legible | Menos de 6 palabras, puntaje de lectura < 5 o confianza media < 0,70 |
| `calidad.borrosa` | Imagen desenfocada que dificulta la lectura | Página no legible y nitidez (varianza del laplaciano a 1600 px) < 60 |
| `calidad.oscura` | Foto demasiado oscura | Página no legible y brillo < 60, o brillo < 35 / contraste < 10 aunque se lea algo |
| `calidad.sobreexpuesta` | Foto lavada o con exceso de luz | Página no legible con brillo > 245 y contraste < 25, o contraste < 10 |
| `calidad.baja_resolucion` | Foto con muy poca resolución | Página no legible y lado mayor < 900 px |
| `calidad.lectura_dudosa` | El texto se lee con poca seguridad sin causa clara (reflejos, recorte, letra pequeña) | Página no legible sin otra métrica fuera de rango |

Mensaje al postulante: «Por favor envíe nuevamente el documento: una foto nítida, completa, con buena luz, sin reflejos
y tomada de frente (o el PDF original, si lo tiene).»

**Prueba con los 22 documentos reales degradados** (`herramientas/prueba_calidad.py`, 242 variantes): ningún original
queda observado por calidad. *ok* = se validó igual; *ok_observado* = se validó y además quedó observado por otra causa
(p. ej. foto de un PDF electrónico); *observado* = quedó Observado; *silencio* = perdió datos sin quedar observado.

| Degradación | ok | ok_observado | observado | silencio |
|---|---|---|---|---|
| Desenfoque leve | 7 | 11 | 4 | 0 |
| Desenfoque fuerte | 0 | 0 | 22 | 0 |
| Oscura | 7 | 12 | 3 | 0 |
| Sobreexpuesta | 3 | 15 | 4 | 0 |
| Baja resolución | 4 | 14 | 4 | 0 |
| JPEG muy comprimido | 3 | 11 | 8 | 0 |
| Girada 90° / 180° / 270° | 22 | 42 | 2 | 0 |
| Recortada a la mitad | 9 | 8 | 4 | 1* |
| Ruido | 4 | 14 | 4 | 0 |

\* El certificado de estudios cortado perdía el RUT sin quedar observado; se corrigió exigiendo RUT en estudios medios.
