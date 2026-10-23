# Plan: librería `stracon-ocr` (OCR, forense y extracción a JSON)

## Avance al 21 oct 2026

Construidas F1 a F6 en versión MVP, más una **API HTTP para la webapp** (FastAPI, `socr servir`) con consola de prueba.
Ver [README](../README.md) y [api-webapp.md](api-webapp.md).

| Fase | Estado | Notas |
|---|---|---|
| F1 Esqueleto | Hecho | uv, Python 3.11, contrato Pydantic, SQLite JSONB + FTS5, ingesta, CLI |
| F2 OCR y códigos | Hecho | RapidOCR sobre **OpenVINO** (5× más rápido que ONNX Runtime en el i7-1270P: ~2 s/pág.), Tesseract opcional, zxing-cpp, MRZ propia con dígitos ICAO, adaptador Azure DI (sin probar contra el servicio) |
| F3 Forense | Hecho en lo determinista | Pendientes: doble compresión, copy-move, recaptura de pantalla, geometría de caracteres, línea base, plantilla |
| F4 Clasificación y extracción | Hecho con reglas | TF-IDF no hizo falta (135/135 clasificados); Ollama opcional |
| F5 Validación y contraste | Hecho | |
| F6 Evaluación | Hecho sobre el mockup | Recall 1,0 en las 11 alteraciones detectables, 0 falsos positivos en 124 limpios, p50 ~2,6 s/doc |

Desvíos frente al plan:
- **Docling** no se integró: los documentos del MVP son formularios de una página y la extracción por rótulo y posición
  basta. Queda como extra opcional (`layout`) para tablas.
- **PassportEye** se reemplazó por una lectura propia de la MRZ sobre el texto OCR (ya estaba el OCR; evita otra dependencia de Tesseract).
- **ExifTool** no es obligatorio: XMP e Info se leen con pikepdf y EXIF con Pillow.
- Se agregaron las señales `pdf.campo_rasterizado` y `pdf.imagen_baja_nitidez` (las piden los casos del mockup).
- La depuración de texto encimado se hace **por carácter** antes de armar palabras, y el texto girado (marcas de agua) va aparte.

Próximos pasos sugeridos: muestras auténticas anonimizadas para calibrar umbrales y formatos de folio; reverso de la cédula
(MRZ) y QR reales; conectar la webapp (ver `api-webapp.md`); autenticación y despliegue.

## Contexto
Desde el 21 oct 2026 el trabajo pasa del mockup a una librería Python real que:
1) extrae todo el contenido de PDF (nativos y escaneados), Word e imágenes;
2) detecta adulteraciones y discrepancias; 3) guarda el resultado como JSON en una BD documental.
Es la pieza real detrás del "Agente 1" (adulteraciones) y de la parte determinista del "Agente 2"
(contraste) del mockup. Alcance inicial: los 6 documentos del Agente 1 (cédula, licencia de conducir,
cert. estudios medios, cert. estudios profesionales, cert. antecedentes, hoja de vida del conductor);
diseño extensible a los 16 del expediente.

Lo primero que pidió el usuario es el **análisis exhaustivo de librerías**: ya está investigado
(3 investigaciones web verificadas a oct 2026) y se entrega como documento en la Fase 0.

### Decisiones tomadas con el usuario
- Proyecto aparte: `C:\Users\amsalinas\Desktop\stracon-ocr`, paquete `stracon_ocr`, CLI `socr`.
- Local + **adaptador opcional a Azure Document Intelligence** ya en el MVP (apagado por defecto).
- **Solo licencias permisivas** (MIT/BSD/Apache/MPL/PSF/dominio público) en el núcleo.
- BD: **SQLite JSONB + FTS5** detrás de una capa `Repositorio` (migrable a Azure DocumentDB / PostgreSQL).

### Restricciones
Portátil Windows 11, i7-1270P, 16 GB RAM, sin GPU NVIDIA (Intel UHD/Iris Xe), Python 3.11.
MVP preliminar (dimensionar en consecuencia). Sin git: respaldos en `Desktop\stracon-respaldos`.
Reglas de negocio heredadas: la decisión siempre es humana (la librería solo emite hallazgos
explicables); "Fuente no disponible" no es discrepancia.

## Stack elegido (resultado del análisis)
| Capa | Elección | Licencia | Por qué |
|---|---|---|---|
| Ingesta | puremagic (MIME real), hashlib SHA-256, pillow-heif (HEIC), OpenCV (orientación/deskew), LibreOffice headless (.doc→.docx/.pdf) | MIT/BSD/Apache/MPL | Python puro en Windows; convierte todo a PDF/imagen normalizada |
| PDF nativo | **pypdfium2** (render + texto) y **pdfplumber** (caracteres con fuente, tamaño y bbox) | Apache/BSD, MIT | Reemplazan a PyMuPDF (AGPL) |
| OCR principal | **RapidOCR 3.10** (PP-OCRv5/v6 latin) sobre **OpenVINO** | Apache-2.0 | ~0,5–1,5 s/pág en CPU, bbox + confianza por palabra |
| OCR forense / 2.º motor | **Tesseract 5.5 `spa`** vía pytesseract | Apache-2.0 | Confianza por palabra y carácter; voto entre motores |
| Layout y tablas | **Docling 2.x** (Heron + TableFormer, OCR = RapidOCR) | MIT / Apache | Corre bien en CPU; guarda DoclingDocument como capa raw |
| Word | docx2python + python-docx (+ zipfile/lxml para forense) | MIT | Texto, tablas, imágenes embebidas, metadatos |
| QR / PDF417 / MRZ | **zxing-cpp**, **PassportEye** + `mrz` (dígitos ICAO 9303) | Apache/MIT | Cédula: QR (RUN, n.º doc), MRZ TD1, PDF417 en cédulas antiguas |
| Nube opcional | azure-ai-documentintelligence (modelo `prebuilt-read`/`layout`) | MIT (SDK) | Comparar precisión; ~USD 1,5/1.000 pág |
| Forense PDF | **pikepdf** (revisiones, Info vs XMP, fuentes, objetos), **pyHanko** (firmas PAdES), **ExifTool**/pyexiftool | MPL/MIT | Determinista y explicable |
| Forense imagen | ExifTool, ELA y doble compresión con OpenCV, **ImageHash** (pHash), heurística FFT de moiré | BSD/Apache | ML (CAT-Net v2) queda para fase posterior |
| Validaciones | python-stdnum (`cl.rut`) o módulo 11 propio, **rapidfuzz**, unidecode, regex por emisor | LGPL*/MIT | *stdnum es LGPL (uso como lib OK); preferir implementación propia de 15 líneas |
| Clasificación | Reglas (palabras clave/emisor) → TF-IDF + LogReg (scikit-learn) | BSD | ms/doc; LLM solo si baja confianza |
| Extracción | Plugins por tipo: regex + zonas; fallback opcional **Ollama** (Qwen3.5-4B / Gemma 4 E4B) con `format=JSON Schema` | MIT/Apache | LLM ~30–60 s/doc en CPU: solo fallback |
| Contrato | **Pydantic v2** + JSON Schema exportado | MIT | Validación y versión del contrato |
| BD | **SQLite 3.45+ JSONB + FTS5** (`unicode61 remove_diacritics 2`) | Dominio público | Cero instalación |
| Proyecto | **uv**, layout `src/`, **Typer** (CLI), pytest, jiwer (CER/WER) | MIT/Apache | — |

**Descartados (y por qué):** PyMuPDF/pymupdf4llm (AGPL), FastMRZ (AGPL), Unstract (AGPL),
Surya/Marker/Chandra (pesos OpenRAIL con tope de facturación), LayoutLMv3 (CC BY-NC-SA),
TruFor/DocTamper/TextShield (no comercial), olmOCR/DeepSeek-OCR 2/dots/Nanonets (requieren GPU),
EasyOCR (estancado, lento), spaCy ES (modelos GPL), Kraken (manuscritos). VLM-OCR permisivos
(PaddleOCR-VL-1.5/1.6, GLM-OCR vía llama.cpp, 20–60 s/pág) quedan como "segunda opinión"
opcional para páginas difíciles, **nunca** como fuente forense (no dan confianza por palabra y
pueden "corregir" dígitos alterados).

## Arquitectura
Pipeline por documento (cada etapa escribe su parte del JSON; todas registran versión y duración):
1. `ingesta` — SHA-256, MIME real, conversión (.doc, HEIC), normalización de imagen, páginas.
2. `parseo` — por página: si tiene capa de texto → pdfplumber/pypdfium2; si no → OCR
   (RapidOCR + Tesseract; Azure DI si `--motor azure`). Docling para layout/tablas. En PDF nativo
   también se OCR-ea el render para comparar texto declarado vs. visible.
3. `codigos` — QR/PDF417/MRZ.
4. `clasificacion` — tipo + confianza + método.
5. `extraccion` — plugin del tipo: campos con procedencia (página, bbox, confianza, método, regla).
6. `forense` — señales de adulteración (catálogo abajo).
7. `validacion` — reglas por documento (RUT válido, fechas, vigencia, formato de folio).
8. `contraste` — por expediente: discrepancias entre documentos y QR/MRZ vs OCR.
9. `almacenamiento` — JSON validado a SQLite.

Estructura del proyecto:
```
stracon-ocr/
  pyproject.toml  uv.lock  README.md
  docs/analisis-librerias-ocr.md   docs/contrato-json.md   docs/catalogo-senales.md
  src/stracon_ocr/
    contrato.py            # modelos Pydantic (Documento, Pagina, Palabra, Campo, Senal, Discrepancia)
    pipeline.py            # orquesta etapas, versiones, tiempos
    ingesta/               # mime.py, convertir.py (LibreOffice, HEIC), imagen.py (deskew)
    motores/               # base.py (interfaz MotorOCR), rapidocr.py, tesseract.py, azure_di.py, docling.py, pdf_nativo.py
    codigos/               # qr.py (zxing-cpp), mrz.py
    clasificacion/         # reglas.py, estadistico.py
    tipos/                 # plugins: cedula.py, licencia.py, estudios_medios.py, estudios_prof.py, antecedentes.py, hoja_vida.py
    forense/               # pdf_estructura.py, pdf_metadatos.py, firmas.py, docx.py, imagen.py, ocr_senales.py, duplicados.py
    validacion/            # rut.py, fechas.py, nombres.py (normalización + rapidfuzz)
    contraste.py           # discrepancias entre documentos de un expediente
    almacen/               # repositorio.py (interfaz), sqlite.py
    llm/ollama.py          # fallback opcional
    cli.py                 # socr procesar | expediente | buscar | exportar | evaluar
  tests/  datos/dorado/  datos/alterados/
```
Plugins por tipo con interfaz común: `esquema` (Pydantic), `puntaje_clasificacion()`, `extraer()`,
`validar()`, `version`. Motores OCR con interfaz común `MotorOCR.leer(imagen) -> list[Palabra]`.

## Contrato JSON (v1.0, resumen)
`contrato_version`, `archivo{nombre, sha256, mime_real, bytes, paginas, origen}`,
`clasificacion{tipo, confianza, metodo, alternativas[]}`,
`paginas[{n, ancho, alto, dpi, fuente_texto: nativo|ocr, motor, bloques[{bbox, texto, conf, palabras[{t, bbox, conf}]}], tablas[]}]`,
`codigos[{tipo: qr|pdf417|mrz, contenido, pagina, bbox, valido}]`,
`campos{<nombre>: {valor, valor_normalizado, pagina, bbox, conf, metodo: nativo|regex|zona|llm|azure, regla_id, evidencia}}`,
`senales[{id, familia: integridad|metadatos|estructura|visual|ocr|duplicado, severidad, campo?, pagina?, bbox?, detalle, evidencia}]`,
`validaciones[{regla, resultado: ok|falla|no_aplica, detalle}]`,
`versiones{pipeline, reglas, motores{}, modelo_llm?}`, `procesado_en`, `duracion_ms`.
Discrepancias a nivel expediente: `{campo, documentos[], valores[], similitud, severidad}`;
"fuente no disponible" se registra como estado, no como discrepancia.

## BD (SQLite)
Tablas: `documentos(id, sha256 UNIQUE, expediente, rut, tipo, contrato_version, json JSONB, creado)`,
`expedientes(id, rut, json JSONB)`, `trabajos(id, ruta, estado, error, intentos)`,
`documentos_fts` (FTS5 sobre texto completo), `hashes_percepcion(doc_id, tipo, phash)` para duplicados
entre postulantes. Índices sobre expresiones `json ->> '$.campos.rut.valor_normalizado'`.

## Catálogo de señales del MVP (deterministas primero)
- Integridad PDF: revisiones incrementales (>1 `%%EOF`/`/Prev`) con render de versión previa;
  firma PAdES inválida o con cambios posteriores (pyHanko).
- Metadatos: Producer/Creator fuera de lista blanca por emisor (iLovePDF, Photoshop, Word, Canva…);
  ModDate ≠ CreationDate; Info vs XMP incoherentes; historial XMP.
- Estructura: fuentes/subsets distintos dentro de un campo o línea; texto superpuesto; rectángulos
  o imágenes tapando texto; OCG ocultas; PDF "solo imagen" donde se espera nativo; texto nativo ≠ OCR del render.
- DOCX: `lastModifiedBy`, `revision`, `TotalTime`, rsid distinto en campos clave, `w:ins/w:del`;
  un certificado oficial en .docx ya es señal.
- Imagen: EXIF con software de edición; ELA/doble compresión por zona; moiré (foto de pantalla).
- OCR: z-score de confianza por palabra en el campo; varianza de altura/anchura; desvío de línea base;
  campo fuera de la zona de plantilla.
- Duplicados: mismo SHA-256 o pHash (página, foto, firma) en otro postulante.
- Validación/contraste: RUT módulo 11; QR y MRZ vs OCR (RUN, n.º de documento, fechas, dígitos ICAO);
  nombres/RUT/fecha de nacimiento entre los 6 documentos (rapidfuzz); vigencias (antecedentes,
  licencia vencida); folio/código del Registro Civil guardado para verificación manual (sin API pública).

## Fases
- **F0 — Análisis (entregable inmediato):** crear el proyecto y escribir
  `docs/analisis-librerias-ocr.md` con las tablas comparativas completas (OCR clásico, parsers,
  VLM-OCR, nube, QR/MRZ, forense PDF/imagen/DOCX, ML de tampering y benchmarks DocForge-Bench /
  AIForge-Doc, extracción/LLM local, clasificación, BD documentales, ingesta), licencias, versiones,
  CPU-viabilidad, fuentes, descartes y el stack elegido. Más `docs/contrato-json.md` y `docs/catalogo-senales.md`.
- **F1 — Esqueleto:** `uv init`, Python 3.11, dependencias base, `contrato.py`, `almacen/sqlite.py`,
  ingesta, extracción de PDF nativo, CLI `socr procesar <archivo|carpeta>`. Prerrequisitos de Windows:
  Tesseract (instalador UB Mannheim + `spa.traineddata`), LibreOffice, ExifTool; Ollama opcional.
- **F2 — OCR y códigos:** RapidOCR/OpenVINO, Tesseract, Docling, zxing-cpp, MRZ, adaptador Azure DI
  (credenciales por variables de entorno; solo con documentos ficticios).
- **F3 — Forense determinista:** módulos de `forense/` según catálogo.
- **F4 — Clasificación y extracción:** reglas + TF-IDF; 6 plugins; fallback Ollama opcional.
- **F5 — Validación y contraste por expediente:** `socr expediente <carpeta>`.
- **F6 — Evaluación:** dataset dorado y alterados; `socr evaluar` con CER/WER, F1 por campo,
  precisión/recall de señales, latencia p50/p95; comparación local vs Azure DI.
- Posterior (fuera del MVP): CAT-Net v2 como indicio con mapa de calor calibrado; VLM-OCR de
  segunda opinión; integración con el mockup (el JSON alimenta las señales del Agente 1).

## Datos de prueba
- Reutilizar los PDF ficticios del mockup (`stracon-validacion-documental\expedientes\<RUT>\*.pdf`,
  6 por postulante) copiándolos a `stracon-ocr\datos\dorado\` (sin modificar el mockup).
- Script `herramientas/generar_alterados.py`: crea copias adulteradas con verdad conocida
  (actualización incremental con pikepdf, Producer cambiado, texto superpuesto, imagen pegada,
  RUT cambiado, render a JPG recomprimido, foto con moiré simulado) para medir detección.

## Verificación
- `uv run pytest`: unit tests por módulo (RUT, MRZ, cada señal con su PDF alterado) y snapshot del
  contrato (JSON válido contra el schema exportado).
- `uv run socr procesar datos\dorado\<RUT>` → revisa JSON en SQLite (`socr buscar --rut ...`).
- `uv run socr evaluar`: cada alteración generada debe disparar su señal y los originales no
  (meta MVP: recall ≥ 0,9 en alteraciones deterministas, falsos positivos ≈ 0 en originales).
- Medir latencia real por página en el i7-1270P (RapidOCR, Tesseract, Docling, Azure DI).
- Respaldo de la carpeta en `Desktop\stracon-respaldos` al cerrar cada fase (sin git).
