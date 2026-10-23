# Análisis de librerías: OCR, forense documental y extracción a JSON

Proyecto `stracon-ocr` · 21 de octubre de 2026 · Versiones y licencias verificadas en PyPI, GitHub y sitios oficiales en esa fecha.

**Convenciones:**
- **est.** = latencia estimada a partir de benchmarks en otros CPU (Xeon, EPYC, móviles). Hay que medirla en el equipo objetivo (Fase 6).
- **verificar** = dato no confirmado en una fuente primaria.
- Las puntuaciones de benchmark son en su mayoría autodeclaradas por los autores.

## 1. Qué se necesita y con qué restricciones

**Objetivo.** Una librería Python que:
1. Extrae todo el contenido de PDF nativos, PDF escaneados, imágenes (JPG, PNG o HEIC de celular) y Word (.docx, .doc): texto, tablas, layout, campos clave, QR, códigos de barras y MRZ.
2. Detecta adulteraciones y discrepancias.
3. Guarda el resultado como JSON en una base documental.

**Documentos.** Documentos chilenos de postulantes. El MVP cubre seis:
- Cédula de identidad
- Licencia de conducir
- Certificado de estudios medios
- Certificado de estudios profesionales
- Certificado de antecedentes
- Hoja de vida del conductor

El diseño es extensible a los 16 documentos del expediente.

| Restricción | Valor | Impacto en la elección |
|---|---|---|
| Hardware | Windows 11, i7-1270P, 16 GB de RAM, **sin GPU NVIDIA** (Intel Iris Xe) | Descarta los VLM de más de 2B parámetros. Favorece ONNX y OpenVINO |
| Licencias | **Solo permisivas** (MIT, BSD, Apache, MPL, PSF, dominio público). El uso será comercial | Descarta AGPL, pesos no comerciales y OpenRAIL con tope de facturación |
| Despliegue | Local. Azure Document Intelligence como adaptador opcional | Todo lo crítico debe correr sin internet |
| Explicabilidad | La decisión es siempre humana. La librería solo emite hallazgos con evidencia | El núcleo es determinista. El ML se usa solo como indicio |
| Requisito forense | Caja (bbox) y confianza **por palabra** | Los VLM-OCR no sirven como fuente forense |
| Alcance | MVP preliminar | Se prefiere lo más simple que funcione, con una ruta de migración clara |

## 2. OCR clásico: motores con caja y confianza por palabra

| Herramienta | Versión / fecha | Licencia | Español | bbox + conf. por palabra | CPU: latencia/pág. y RAM | ONNX / OpenVINO | Veredicto |
|---|---|---|---|---|---|---|---|
| **Tesseract** | 5.5.3 (jul-2026) | Apache-2.0 | `spa`, `spa_old` | **Sí**: palabra, línea y carácter (`image_to_data`, hOCR) | est. 1–3 s a 300 dpi, <0,5 GB | No hace falta | **Elegido como 2.º motor forense** |
| **RapidOCR** | 3.10.0 (8-oct-2026) | Apache-2.0 (los modelos tienen su propio MODEL_LICENSES, verificar) | Modelos PP-OCR latin | Caja por línea y palabra (`return_word_box`); conf. por línea y carácter | est. 0,5–1,5 s, ~0,5 GB | **ONNX Runtime y OpenVINO** | **Elegido como motor principal** |
| PaddleOCR 3.7 (PP-OCRv6, PP-StructureV3) | 3.7.0 (11-jun-2026) | Apache-2.0 | PP-OCRv6 cubre 46 escrituras latinas. Spanish no figura en la lista, verificar | Sí (`return_word_box`) | PP-OCRv6 small: 0,59 s/img en Xeon con OpenVINO. StructureV3 est. 5–15 s, 2–4 GB | OpenVINO y ONNX oficiales | Alternativa (RapidOCR ya usa estos modelos con menos dependencias) |
| docTR / OnnxTR | 1.1.0 (21-ago-2026) | Apache-2.0 | Sí (vocabulario multilingüe) | **Sí** | OnnxTR int8 est. 1–3 s, ~1 GB | ONNX y OpenVINO (OnnxTR) | Alternativa sólida. También sirve para corregir la orientación |
| OCRmyPDF | 17.13.0 | MPL-2.0 | Vía Tesseract | Sí (capa hOCR en el PDF) | Igual que Tesseract. En Windows conviene WSL | Plugins | Útil para exportar un PDF buscable, no se necesita en el núcleo |
| EasyOCR | 1.7.2 (sep-2024) | Apache-2.0 | Sí | Por fragmento | Lento, est. 3–8 s | No | **Descartado**: estancado y lento |
| Surya OCR 2 | 0.22.x (modelo de may-2026) | Código Apache; **pesos OpenRAIL-M modificada** (gratis solo bajo USD 5M de facturación) | ~90 idiomas | Por línea | est. >20 s | GGUF comunitario | **Descartado**: licencia de los pesos |
| Kraken | 7.1.1 | Apache-2.0 | Entrenable | Sí | Bien | No | **Descartado**: orientado a manuscritos e históricos |

## 3. Parsers de documentos y lectura de PDF nativo

| Herramienta | Versión | Licencia | Qué aporta | CPU | Veredicto |
|---|---|---|---|---|---|
| **Docling** (IBM, Linux Foundation) | 2.141.0 (20-oct-2026) | MIT; modelos Apache | Layout (Heron), tablas (TableFormer), OCR conectable (RapidOCR, Tesseract), DOCX, imágenes, bbox por elemento, DoclingDocument JSON | Paper: mediana 0,79 s/pág. y media 3,1 s en x86 | **Elegido** para layout y tablas |
| **pypdfium2** | 5.14.0 | Apache / BSD-3 | Render de página a imagen; texto con bbox por carácter | ms/pág. | **Elegido** (render y texto) |
| **pdfplumber** | 0.11.10 (jun-2026) | MIT | Cada carácter con fuente, tamaño y bbox; tablas; líneas y rectángulos | 0,1–0,5 s/pág. | **Elegido** (base de las señales tipográficas) |
| PyMuPDF / pymupdf4llm | 2.0 (19-oct-2026, absorbe pymupdf4llm) | **AGPL-3.0 o comercial** | Lo más rápido y completo | ms/pág. | **Descartado** por la AGPL, salvo que se compre licencia |
| Granite-Docling-258M | VLM 2025-26 | Apache-2.0 | DocTags de extremo a extremo | est. 15–40 s/pág. (GGUF) | Opcional, no es prioritario |
| MinerU 4.0 / MinerU2.5-Pro 1.2B | 4.0.12 | Licencia propia "basada en Apache con condiciones" (verificar) | OmniDocBench v1.6: 95,7 | Nivel `basic` va en CPU; con VLM est. 30–90 s | En pausa hasta revisar la licencia |
| Marker 2.0 (Datalab) | 2.0.0 (jul-2026) | Código Apache; **pesos OpenRAIL-M** | olmOCR-Bench: fast 66,6, balanced 76,0 | Modo `fast` est. 2–5 s | **Descartado**: licencia de los pesos |
| Unstructured | 0.27.32 | Apache-2.0 | Particionado para RAG | Utilizable | No aporta bbox fino. Descartado |
| MarkItDown (Microsoft) | 0.1.9 (21-oct-2026) | MIT | DOCX, PPTX y PDF a Markdown, **sin bbox** | Instantáneo | Solo para vistas rápidas o RAG |
| LlamaParse | SaaS | Comercial | — | Nube | Solo como referencia |

## 4. VLM-OCR: modelos de visión y lenguaje

**Advertencia de fondo:** estos modelos no entregan confianza por palabra y pueden "corregir" un dígito alterado, con lo que esconden justo lo que se busca. Por eso **nunca se usan como fuente forense**: solo sirven como segunda opinión para estructurar páginas difíciles o extraer campos.

| Modelo | Tamaño / fecha | Licencia de pesos | Benchmark | ¿Viable en el i7-1270P? |
|---|---|---|---|---|
| **PaddleOCR-VL-1.5 / 1.6** | 0,9B (ene / may-2026) | Apache-2.0 | OmniDocBench v1.6: **96,3** (VL-1.6) | **Sí, lento**. GGUF oficial (1.5) con llama.cpp. est. 20–60 s/pág., 2–3 GB |
| **GLM-OCR** (Z.ai) | 0,9B (mar-2026) | MIT | OmniDocBench v1.5: 94,6; v1.6: 95,2 | **Sí**. GGUF de ggml-org. est. 20–60 s/pág. |
| LightOnOCR-2-1B | 1B (ene-2026) | Apache-2.0 | olmOCR-Bench 83,2 | Posible. est. 30–90 s |
| Qwen3-VL 2B / 4B | 2025 | Apache-2.0 | Generalista, buen español | GGUF oficial. est. 30 s–3 min/pág. Útil para extraer campos con prompt |
| MinerU2.5-Pro | 1,2B | Licencia MinerU | OmniDocBench v1.6: 95,7 | Posible (verificar la licencia) |
| DeepSeek-OCR 2 | ~3B MoE (ene-2026) | MIT | OmniDocBench v1.5: 91,1 | No: en la práctica requiere GPU |
| Chandra OCR 2 | 4B (mar-2026) | OpenRAIL-M modificada (tope de USD 2M) | olmOCR-Bench **85,9** (SOTA) | No: requiere GPU y la licencia no sirve |
| dots.mocr | 3B | Propia (verificar) | olmOCR-Bench 83,9 | No: requiere GPU |
| olmOCR 2 (AllenAI) | 7B | Apache-2.0 | olmOCR-Bench 78,5–82,4 | No: requiere NVIDIA de 12 GB o más |
| Nanonets-OCR2-3B, MonkeyOCR-pro-3B | 3B | Verificar | olmOCR-Bench 69,5 (Nanonets) | No: requiere GPU |
| GOT-OCR2, Florence-2, SmolDocling | 0,25–0,8B (2024-25) | Varias (Florence-2 es MIT) | Superados | Corren, pero están obsoletos |

OpenVINO ya está integrado como backend de llama.cpp (CPU, iGPU y NPU de Intel). No encontré pruebas publicadas con modelos OCR concretos sobre ese backend, así que conviene medirlo.

## 5. Servicios en la nube (referencia y adaptador opcional)

| Servicio | OCR básico | Estructurado | bbox + conf. por palabra | Nota |
|---|---|---|---|---|
| **Azure AI Document Intelligence** | ~USD 1,5 por 1.000 pág. (Read) | Prebuilt ~USD 10; custom ~USD 30; Query Fields +USD 10 | **Sí** | **Adaptador opcional en el MVP**. Encaja con el ecosistema Azure del cliente |
| Google Document AI | ~USD 1,5 por 1.000 | Form Parser USD 10–30 | Sí | Solo como referencia |
| AWS Textract | USD 1,5 por 1.000 | Tablas USD 15; Forms USD 50 | Sí | Solo como referencia |
| Mistral OCR 4 / 4.1 | USD 4 por 1.000 (batch USD 2) | Markdown | No | Solo como referencia |

## 6. QR, códigos de barras y MRZ

| Librería | Versión | Licencia | Notas | Veredicto |
|---|---|---|---|---|
| **zxing-cpp** | 3.1.1 | Apache-2.0 | QR, PDF417, DataMatrix y Code128. Tiene wheels para Windows | **Elegido** |
| pyzbar | — | MIT | No lee PDF417 y depende de una DLL de zbar | Solo como respaldo |
| **PassportEye** | 2.2.2 (mar-2025) | MIT | Detecta la MRZ con Tesseract; formatos TD1, TD2 y TD3 | **Elegido** |
| Librería `mrz` (PyPI) | — | Verificar | Valida los dígitos de control ICAO 9303 | **Elegido** (o una implementación propia) |
| FastMRZ | 2.1.2 (nov-2025) | **AGPL-3.0** | Centrado en pasaportes | **Descartado** |
| OpenCV `QRCodeDetector` | — | Apache-2.0 | Respaldo para QR | Respaldo |

**Particularidades de los documentos chilenos:**
- **Cédula de identidad:**
  - Tiene MRZ TD1 (3×30) en el reverso. Los nombres largos salen truncados, así que la comparación con el anverso debe aceptar coincidencias parciales.
  - Tiene un QR con el RUN y el número de documento, que enlaza a la verificación de vigencia del SRCeI.
  - Las cédulas anteriores a 2013 usan PDF417.
  - Hay dos formatos nuevos cuyo detalle no está publicado:
    - La cédula de diciembre de 2024 tiene un QR ampliado que da acceso al chip NFC.
    - La cédula digital (decreto de mayo de 2026) tiene un QR de vigencia limitada.
- **Certificados del Registro Civil** (antecedentes, hoja de vida del conductor, nacimiento):
  - Traen folio, código de verificación y QR.
  - Se verifican en `registrocivil.cl/OficinaInternet/verificacion/verificacioncertificado.srcei` hasta 60 días después de la compra.
  - **No hay API pública** (Verifik ofrece una comercial).

## 7. Word e imágenes

| Necesidad | Herramienta | Versión | Licencia | Notas |
|---|---|---|---|---|
| .docx: texto, tablas, encabezados, imágenes | **docx2python** | 3.7.1 (ago-2026) | MIT | Extrae las imágenes embebidas |
| .docx: estructura | **python-docx** | 1.2.0 | MIT | — |
| .docx: forense | zipfile + lxml (stdlib) | — | PSF / BSD | No hay librería dedicada; se programa en unas 100 líneas (sección 9) |
| .doc antiguo | **LibreOffice headless** | — | MPL-2.0 | `soffice --headless --convert-to docx`. Un proceso a la vez |
| HEIC de celular | **pillow-heif** | 1.8.1 (11-oct-2026) | BSD-3 | Tiene wheels para Windows |
| Orientación, deskew, recorte | **OpenCV** | — | Apache-2.0 | Más la orientación EXIF de Pillow |

## 8. Forense de PDF: detección de adulteraciones (determinista, CPU)

| Herramienta | Qué detecta | Licencia | Versión | Veredicto |
|---|---|---|---|---|
| **pikepdf** (qpdf) | Árbol de objetos, xref, revisiones incrementales, Info frente a XMP, fuentes e imágenes por página | MPL-2.0 | 10.8.0 (jun-2026) | **Elegido** |
| **pyHanko** | Validez de firmas PAdES/CMS y su cadena; cambios posteriores a la firma (DocMDP, diff de revisiones) | MIT | 0.37.0 (~ago-2026) | **Elegido** |
| **ExifTool** + pyexiftool | Metadatos XMP e Info, historial `xmpMM:History`, Producer/Creator, fechas | Artistic/GPL (binario externo, no se enlaza) · pyexiftool BSD | 13.55 | **Elegido** como herramienta externa |
| pdfplumber / pdfminer.six | Fuente y posición de cada carácter (fuentes mezcladas, texto superpuesto) | MIT | — | **Elegido** (ya está en el stack) |
| pypdf | Metadatos, recursos, escritura incremental | BSD-3 | 5.x/6.x | Apoyo |
| pdfid / pdf-parser (Didier Stevens) | Conteo de /JS, /AcroForm, %%EOF y /ObjStm; volcado de revisiones | Dominio público | 0.2.10 / 0.7.14 (mar-2026) | Apoyo para diagnóstico |
| peepdf-3 | Consola interactiva y versiones del documento | GPL-3 | 3.0.1 | Solo diagnóstico manual (no se incluye en el paquete) |

**Señales que se derivan** (el detalle está en `catalogo-senales.md`):
- Varias revisiones incrementales.
- Producer o Creator fuera de la lista blanca del emisor.
- Fechas o identificadores incoherentes entre Info y XMP.
- Fuentes o subsets mezclados dentro de un campo.
- Texto superpuesto, o rectángulos e imágenes que tapan texto.
- Capas OCG ocultas.
- Un PDF "solo imagen" cuando el emisor genera PDF nativo.
- El texto nativo no coincide con el OCR del render de la página.

## 9. Forense de imagen y de DOCX

**Técnicas clásicas** (deterministas, en CPU, explicables). Con fotos de WhatsApp dan muchos falsos positivos, por la recompresión y el escalado.

| Técnica | Herramienta | Notas |
|---|---|---|
| ELA, doble compresión JPEG (tablas de cuantización, BAG), ruido local | OpenCV y scikit-image | Sirven como indicio por zona, nunca como veredicto |
| Copy-move (clonado) | SIFT u ORB con OpenCV | Útil para sellos o firmas duplicados |
| EXIF (software de edición, miniatura distinta de la imagen) | ExifTool | Listo para usar |
| Foto de pantalla (recaptura) | Heurística FFT de moiré | No hay un modelo listo y confiable |
| Imagen generada por IA | Leer los metadatos C2PA/IPTC `trainedAlgorithmicMedia` con ExifTool | No existe un detector open source confiable |
| PRNU (huella del sensor) | — | **Inviable**: requiere fotos de referencia de la misma cámara |
| Inspección visual para el revisor | Sherloq (GUI, Python) | Herramienta de apoyo, no para decidir automáticamente |

**Duplicados entre postulantes:**

| Herramienta | Licencia | Uso |
|---|---|---|
| **ImageHash** 4.3.2 | BSD-2 | pHash y dHash de la página, la foto de carnet y la firma |
| imagededup 0.3.3 | Apache-2.0 | Búsqueda masiva (también con CNN) |
| SHA-256 (hashlib) | PSF | El mismo archivo exacto entregado por otro postulante |

**DOCX.** Partes del paquete a revisar:
- `docProps/core.xml`: `creator`, `lastModifiedBy`, `created`, `modified` y `revision`.
- `docProps/app.xml`: `Application`, `Template`, `TotalTime` y `Company`.
- `word/settings.xml`: `w:rsids`.
- Atributos `w:rsidR` en los runs de los campos clave. Un rsid distinto en el nombre, el RUT o las notas indica que se editaron en otra sesión.
- Cambios rastreados `w:ins` y `w:del`, comentarios y `customXml`.

Que un certificado oficial llegue en .docx ya es una señal en sí, porque los emisores entregan PDF.

## 10. ML de detección de manipulación (fase posterior)

| Modelo | Uso | Licencia | ¿Sirve? |
|---|---|---|---|
| **CAT-Net v1/v2** | Rastros DCT/JPEG | Código Apache-2.0; **pesos CC-BY-4.0, con uso comercial** | **Único candidato** para una fase posterior. Tiene un entorno antiguo (PyTorch 1.1) que hay que portar |
| TruFor + Noiseprint++ (CVPR 2023) | Imagen general, con mapa de calor | **Solo uso no comercial** | Descartado |
| DTD / DocTamper (CVPR 2023) | Texto alterado en documentos | Dataset no comercial; repo sin licencia | Descartado |
| TextShield-R1 (AAAI 2026) | Texto alterado, con explicación | CC-BY-NC-4.0, base de 7B | Descartado (licencia y CPU) |
| ADCD-Net (ICCV 2025) | Documentos, robusto a distorsiones | Verificar | Seguir de cerca |
| MVSS-Net, ManTra-Net, IML-ViT | Imagen general (2019-2023) | Sin licencia clara o MIT (verificar) | Superados |
| Frameworks: IMDL-BenCo (CC-BY-4.0), ForensicHub (CC-BY-4.0), PhotoHolmes (Apache, cada método con su licencia) | Comparar modelos | — | Para la evaluación, no para producción |

**Evidencia de 2026 que justifica dejar el ML para después:**
- **DocForge-Bench** (mar-2026) probó 14 métodos con sus pesos publicados y ninguno funciona bien "out of the box" en documentos. El problema principal es el umbral: calibrar con unas 10 imágenes del dominio recupera entre 39 y 55 % de la brecha.
- **AIForge-Doc** (feb-2026) evaluó documentos editados con IA (Gemini, Ideogram): TruFor llega a AUC 0,75, DTD a 0,56 y GPT-4o a 0,51, es decir, al azar.

Conclusión: el ML de imagen entra solo como "indicio con mapa de calor", calibrado con documentos auténticos del cliente, y nunca como decisión.

## 11. Validación y contraste

| Control | Herramienta | Licencia |
|---|---|---|
| RUT con módulo 11 | Implementación propia (unas 15 líneas) o `python-stdnum` (`stdnum.cl.rut`) | Propia / LGPL |
| Nombres entre documentos | **rapidfuzz** (`token_sort_ratio`) y **unidecode**, con nombres normalizados (mayúsculas, sin tildes, sin partículas "de" o "del", orden de apellidos) | MIT / GPL-2 (verificar; alternativa: `unicodedata` de la stdlib) |
| QR y MRZ frente al OCR | zxing-cpp, PassportEye y `mrz` | Apache / MIT |
| Fechas y vigencias | Reglas propias (antecedentes con más de 60 días, licencia vencida, fechas en el futuro) | — |
| Folio del Registro Civil | Se guarda el folio y el código, y se ofrece un enlace de verificación al revisor. Automatizarlo depende de los términos de uso | — |

## 12. Clasificación y extracción estructurada

**Clasificación del tipo de documento:**

| Opción | Licencia | Latencia | Veredicto |
|---|---|---|---|
| Reglas: palabras clave, emisor, título | — | Instantánea | **Capa 1**. Debería resolver el 80–90 % |
| TF-IDF + regresión logística (scikit-learn) | BSD | ms | **Capa 2**. Se entrena con 20–50 ejemplos por clase |
| Embeddings ligeros (multilingual-e5-small en ONNX) + kNN | MIT | ~50 ms | Alternativa si el OCR tiene mucho ruido |
| LLM zero-shot (Qwen3.5-2B con un enum) | Apache-2.0 | 5–15 s | Solo cuando la confianza es baja |
| LayoutLMv3 | **CC BY-NC-SA** | — | **Descartado** |
| Donut | MIT | Lento | Innecesario |

**Extracción a JSON:**

| Componente | Licencia | Versión | Rol |
|---|---|---|---|
| Regex por emisor y zonas por plantilla | — | — | **Camino principal**: determinista y auditable |
| **Pydantic v2** | MIT | 2.x | Esquema por tipo y contrato JSON |
| **Ollama** | MIT | 0.40.7 (18-oct-2026) | Runtime LLM local opcional, con `format=<JSON Schema>` |
| llama-cpp-python | MIT | 0.3.36 | Alternativa a Ollama (gramáticas GBNF) |
| OpenVINO GenAI | Apache-2.0 | 2026.4 | Alternativa que aprovecha la iGPU Intel |
| instructor | MIT | 1.17.0 | Reintentos con validación Pydantic |
| outlines | Apache-2.0 | 1.3.3 | Decodificación restringida |
| Google LangExtract | Apache-2.0 | 1.7.1 | Guarda la procedencia por offset. Con Ollama no admite `output_schema` |
| BAML / DSPy | Apache / MIT | — | Exceden lo que necesita el MVP |
| docling `DocumentExtractor` | MIT | beta | ~5–10 min/pág. en CPU. Descartado por ahora |
| Unstract | **AGPL** | — | Descartado |
| spaCy `es_core_news` | Los modelos son **GPL-3.0** | — | Descartado |
| Microsoft Presidio | MIT | 2.2.364 | Detección y anonimización de PII (con un reconocedor de RUT). Sirve para exportar datos anonimizados |

**Modelos LLM locales** con licencia permisiva y GGUF disponible: Qwen3.5 (0.8B, 2B y 4B; Apache), Gemma 4 E2B y E4B (Apache desde abr-2026), Phi-4-mini 3.8B (MIT) y NuExtract-2.0-2B (MIT).

**Latencia estimada** de un modelo de 4B en Q4 en el i7-1270P: **30–60 s por documento**. Por eso el LLM queda como fallback y no como camino principal.

## 13. Base de datos documental

| Opción | Instalación en Windows | Licencia / versión | Consultas JSON e índices | Full-text en español | Validación de esquema | Migración |
|---|---|---|---|---|---|---|
| **SQLite JSONB + FTS5** | Viene con Python | Dominio público, 3.54.1 (JSONB desde 3.45) | `json_extract`, `->>`, índices sobre expresiones | FTS5 `unicode61 remove_diacritics 2` (sin stemming) | `CHECK(json_valid())` + Pydantic | Azure SQL (JSON), PostgreSQL, DocumentDB |
| MongoDB Community | MSI | **SSPL**, 9.0.4 | Completas | `$text` con idioma spanish | `$jsonSchema` | Azure DocumentDB directo |
| PostgreSQL JSONB | Instalador EDB | PostgreSQL License, 18.6 | Índices GIN, `jsonb_path_query` | `tsvector('spanish')` con stemming | `pg_jsonschema` | Azure Database for PostgreSQL |
| FerretDB 2.x | Solo Docker | Apache-2.0 | API de Mongo | La de Postgres | Parcial | La v2 ya no soporta SQLite |
| CouchDB | MSI | Apache-2.0, 3.5.2 | Mango, vistas | Requiere Nouveau | `validate_doc_update` | Poco usado en Azure |
| DuckDB | pip | MIT, 2.0.0 | Analítico | Extensión FTS | No | Solo para análisis |
| TinyDB | pip | MIT | Sin índices | No | No | Solo prototipos |
| OpenSearch | Java o Docker | Apache-2.0 | — | Analizador spanish | Mappings | Búsqueda en la fase 2 |
| Vectoriales: sqlite-vec, LanceDB, Qdrant local, pgvector | pip / Docker | MIT / Apache | — | — | — | Para búsqueda semántica futura |

Cosmos DB for MongoDB vCore pasó a llamarse **Azure DocumentDB** en noviembre de 2025. Microsoft declara 99 % de compatibilidad con MongoDB.

**Decisión: SQLite JSONB + FTS5** detrás de una interfaz `Repositorio`. Cambiar a DocumentDB o PostgreSQL solo exige escribir otra implementación de esa interfaz; el contrato JSON no cambia.

## 14. Ingesta y empaquetado

| Necesidad | Elección | Licencia | Versión |
|---|---|---|---|
| MIME real | **puremagic** (Python puro) | MIT | 2.2.0 (abr-2026) |
| Gestión del proyecto | **uv** (layout `src/` + lockfile) | MIT / Apache | 0.13.4 |
| CLI | **Typer** | MIT | 0.27.3 |
| Cola local | Tabla `trabajos` en SQLite + `concurrent.futures` | — | — |
| Pruebas | pytest + dataset dorado + snapshots del contrato | MIT | — |
| Métricas | **jiwer** (CER/WER), F1 por campo, precisión y recall de señales, latencia p50/p95 | Apache-2.0 | — |

Se descartan python-magic (en Windows necesita una DLL) y filetype (sin mantenimiento desde 2022).

## 15. Stack elegido

| Capa | Elección |
|---|---|
| Ingesta | puremagic, SHA-256, pillow-heif, OpenCV, LibreOffice headless |
| PDF nativo | pypdfium2 + pdfplumber |
| OCR | RapidOCR con OpenVINO (principal) + Tesseract `spa` (2.º motor forense); Azure DI como opción |
| Layout y tablas | Docling |
| Word | docx2python + python-docx + zipfile/lxml |
| Códigos | zxing-cpp + PassportEye + validación ICAO |
| Forense | pikepdf, pyHanko, ExifTool, pdfplumber, OpenCV (ELA y moiré), ImageHash |
| Validación | Módulo 11 propio, rapidfuzz, reglas de fechas |
| Clasificación | Reglas → TF-IDF + LogReg |
| Extracción | Plugins con regex y zonas → fallback Ollama (Qwen3.5-4B / Gemma 4 E4B) |
| Contrato y BD | Pydantic v2 → SQLite JSONB + FTS5 |
| Herramientas | uv, Typer, pytest, jiwer |

**Prerrequisitos en Windows** (se instalan fuera de pip):
- Tesseract 5.5, instalador de UB Mannheim, con `spa.traineddata`.
- LibreOffice.
- ExifTool.
- Ollama (opcional).

### Descartes por licencia (resumen)

| Licencia | Librerías y modelos |
|---|---|
| AGPL | PyMuPDF, pymupdf4llm, FastMRZ, Unstract |
| OpenRAIL con tope de facturación | Surya, Marker, Chandra |
| No comercial | LayoutLMv3, TruFor, DocTamper, TextShield |
| GPL (modelos) | spaCy ES |

## 16. Riesgos y supuestos

| Riesgo | Mitigación |
|---|---|
| Las latencias son estimaciones | Medirlas en la Fase 6 y ajustar los motores (por ejemplo, Tesseract solo en los campos clave) |
| Fotos de celular con mucha recompresión: ELA da falsos positivos | Severidad baja por defecto; calibrar con documentos auténticos |
| Los formatos nuevos de la cédula (QR NFC, cédula digital 2026) no están documentados | El parser del QR tolera formatos desconocidos y registra "formato no reconocido", no una adulteración |
| No hay API del Registro Civil | Guardar folio y código, y que el revisor verifique ("Fuente no disponible" no es discrepancia) |
| Sin documentos reales para calibrar | Usar los PDF ficticios del mockup y copias alteradas generadas. Pedir al cliente muestras auténticas anonimizadas |
| El adaptador Azure envía documentos a la nube | Apagado por defecto y solo con documentos ficticios hasta tener autorización |
| Licencias de los modelos de RapidOCR y PP-OCR | Revisar MODEL_LICENSES antes de producción |

## 17. Fuentes principales

- **OCR y parsing:**
  - PyPI (`/pypi/<paquete>/json`) de cada paquete citado.
  - [Tesseract releases](https://github.com/tesseract-ocr/tesseract/releases) · [PP-OCRv6](https://www.paddleocr.ai/latest/en/version3.x/algorithm/PP-OCRv6/PP-OCRv6.html) · [PaddleOCR-VL-1.5](https://huggingface.co/PaddlePaddle/PaddleOCR-VL-1.5) · [GLM-OCR](https://huggingface.co/zai-org/GLM-OCR) · [Chandra](https://github.com/datalab-to/chandra) · [Marker](https://github.com/datalab-to/marker/releases) · [MinerU2.5-Pro](https://huggingface.co/opendatalab/MinerU2.5-Pro-2605-1.2B)
  - Papers: [DeepSeek-OCR 2](https://arxiv.org/pdf/2601.20552) · [LightOnOCR-2](https://www.arxiv.org/abs/2601.14251) · [Docling](https://arxiv.org/pdf/2501.17887)
  - [OpenVINO en llama.cpp](https://medium.com/openvino-toolkit/openvino-lands-in-llama-cpp-run-gguf-models-on-intel-cpu-gpu-and-npu-d6fca1d633e8) · [olmOCR-Bench](https://www.codesota.com/ocr/benchmark/olmocr-bench)
- **Forense:**
  - [pyHanko](https://pypi.org/project/pyhanko/) · [pikepdf](https://github.com/pikepdf/pikepdf) · [pdf-parser 0.7.14](https://blog.didierstevens.com/2026/03/13/update-pdf-parser-py-version-0-7-14) · [ExifTool](https://exiftool.org/history.html)
  - Modelos y frameworks: [CAT-Net](https://github.com/mjkwon2021/CAT-Net) · [TruFor](https://github.com/grip-unina/TruFor) · [DocTamper](https://github.com/qcf-568/DocTamper) · [IMDL-BenCo](https://github.com/scu-zjz/IMDLBenCo) · [ForensicHub](https://github.com/scu-zjz/ForensicHub)
  - Benchmarks: [DocForge-Bench](https://arxiv.org/abs/2603.01433) · [AIForge-Doc](https://arxiv.org/html/2602.20569) · [ADCD-Net](https://arxiv.org/html/2507.16397v1)
- **Chile:**
  - [Cédula 2024 (Diario Oficial)](https://www.diariooficial.interior.gob.cl/publicaciones/2024/12/07/44018/01/2580493.pdf) · [Cédula digital 2026](https://www.diariooficial.interior.gob.cl/publicaciones/2026/05/13/44448/01/2807801.pdf)
  - [Regula: procesamiento de la cédula chilena](https://regulaforensics.com/es/blog/chile-id-card-processing/) · [Verifik SRCeI](https://docs.verifik.co/background-check/chile-certificate-verify/)
- **Extracción y BD:**
  - [LangExtract](https://github.com/google/langextract) · [Docling](https://github.com/docling-project/docling) · [Ollama releases](https://github.com/ollama/ollama/releases) · [Presidio](https://github.com/microsoft/presidio)
  - [SQLite changes](https://sqlite.org/changes.html) · [Azure DocumentDB GA](https://devblogs.microsoft.com/cosmosdb/azure-documentdb-is-now-generally-available) · [NuExtract-2.0-2B](https://huggingface.co/numind/NuExtract-2.0-2B)
