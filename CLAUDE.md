# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Qué es

`stracon-ocr`: librería + API (FastAPI) de OCR, forense documental y extracción a JSON para la validación documental de postulantes (STRACON). Es la pieza real detrás del Agente 1 (señales de adulteración) y la parte determinista del Agente 2 (contraste entre documentos) del mockup `stracon-validacion-documental`. **No decide**: entrega hallazgos con evidencia (página, caja, valores) y la decisión es del revisor humano. Código, textos y comentarios van en español; el README es la referencia de uso y variables de entorno `SOCR_*`.

## Comandos

Windows 11, Python 3.11 exacto (`requires-python = ">=3.11,<3.12"`), gestor `uv`.

```powershell
python -m uv sync --extra tesseract --extra firmas      # instalar (extras opcionales: layout, azure)
.venv\Scripts\socr servir                               # API + consola en http://127.0.0.1:8000 (docs en /docs)
.venv\Scripts\socr procesar datos\dorado\12794536-5\2-licencia-conducir.pdf -d
.venv\Scripts\socr expediente datos\dorado\12794536-5 --salida exportado\12794536-5
.venv\Scripts\socr evaluar                              # mide detección contra la verdad conocida del mockup
.venv\Scripts\python -m pytest                          # todas las pruebas
.venv\Scripts\python -m pytest tests/test_pipeline.py -k nombre_de_la_prueba   # una sola
```

No hay linter ni formateador configurado. Las pruebas usan los PDF de `datos/dorado/<RUT>/` (expedientes ficticios del mockup); si falta uno, `conftest.mockup()` hace `skip` en vez de fallar. La fecha de referencia de las pruebas es `HOY = 2026-10-21`: pasar `hoy=` a `Procesador.procesar` para que las vigencias sean deterministas.

## Arquitectura

Entrada principal: `pipeline.Procesador.procesar()`, que orquesta las etapas en este orden: ingesta → parseo → códigos (QR/PDF417/MRZ) → clasificación → extracción → (LLM opcional) → forense → validación → pHash/duplicados → observaciones → almacenamiento. Piezas que hay que entender leyendo varios archivos:

- **Etapas aisladas**: `_etapa()` captura cualquier excepción, la registra en `Documento.errores` y sigue. El JSON sale siempre válido; una etapa rota no debe tumbar el documento. Un archivo ilegible (dañado, con contraseña) no es un error: genera una observación `archivo_ilegible` (resultado "Observado"). Solo `FormatoNoSoportado` se propaga (la API lo traduce a 415).
- **Parseo híbrido** (`Procesador._parsear`): por página, PDF con texto nativo (`pdfplumber`, con fuente y tamaño por carácter) o OCR (`motores/`: rapidocr por defecto, tesseract y azure opcionales). En PDF nativo también se hace OCR del render (página 1 o páginas donde algo puede tapar texto) para comparar texto declarado vs visible (`ocr.motores_discrepan`, `similitud_ocr`). Las páginas escaneadas se reorientan y pasan por un segundo motor (Tesseract) si está instalado. Los estados intermedios viven en `trabajo.DocTrabajo`/`PaginaTrabajo`; el contrato público está en `contrato.py` (modelos Pydantic, versión de contrato `CONTRATO_VERSION`).
- **Tipos de documento declarativos** (`tipos/`): cada tipo (cédula, licencia, hoja de vida, CEIM, estudios medios, antecedentes; hay también `estudios_prof`) es un `TipoSpec` con títulos, palabras clave, `CampoSpec` (rótulos, clase, `clave`) y patrones. `clasificacion.py` y `extraccion.py` son genéricos y se alimentan de `TIPOS`; agregar un tipo = agregar un módulo ahí y registrarlo, no tocar el pipeline. Cada campo extraído lleva procedencia (página, caja, confianza, método, regla).
- **Reglas externalizadas**: umbrales, productores/editores sospechosos, formatos de folio/código y fuentes viven en `src/stracon_ocr/reglas.json` (override con `SOCR_REGLAS`; su `version` queda en el JSON de salida). Los formatos de folio son los de los documentos **ficticios** del mockup: hay que reemplazarlos por los reales antes de producción.
- **Forense** (`forense/`): metadatos y estructura de PDF, DOCX, imagen (EXIF, ELA) y señales derivadas del OCR. Cada hallazgo es una `Senal` con `id`, `familia`, `severidad` y evidencia; el catálogo se documenta en `docs/catalogo-senales.md`.
- **Validación y contraste**: `validacion.py` valida por documento (RUT módulo 11, MRZ ICAO 9303, QR vs OCR, fechas, vigencias, folios); `contraste.py` compara entre documentos del mismo expediente (identidad, RUT, nombre con rapidfuzz, nacimiento, clases de licencia, duplicados). Un expediente se identifica por el RUT del postulante (nombre de la carpeta en `datos/dorado/`).
- **Almacén**: interfaz `almacen.Repositorio`, implementación `RepositorioSQLite` (JSONB + FTS5 para búsqueda sin tildes); los originales se guardan por SHA-256 en `datos/archivos/`. Detecta duplicados por SHA-256 y pHash entre expedientes. Cambiar de base = otra implementación de `Repositorio`.
- **API** (`api.py`, `crear_app`): endpoints síncronos (`POST /documentos`, `/expedientes/{rut}/documentos`) y asíncronos (`POST /trabajos` + `GET /trabajos/{id}`). Procesa en un `ProcessPoolExecutor` (`trabajadores.Grupo`): cada proceso carga una vez su motor OCR y su conexión SQLite; con `SOCR_TRABAJADORES=0` todo corre en el proceso actual (así lo usan las pruebas, ver `cfg_tmp`). `vista.py` dibuja las cajas de señales/campos sobre la página; `web/index.html` es la consola de prueba servida en `/`.
- **LLM opcional**: `llm.py` (Ollama, apagado salvo `SOCR_OLLAMA_MODELO`) solo completa campos faltantes; el modelo usado queda en `versiones.modelo_llm`.

## Datos

`datos/dorado/` son PDF ficticios del mockup y están versionados. Todo lo demás en `datos/` (`*.db`, `archivos/`) y `exportado/` está en `.gitignore` porque puede contener documentos reales con datos personales: no commitearlos ni enviarlos a servicios externos (el motor `azure` envía la página a la nube: solo con documentos ficticios). `herramientas/` tiene scripts de apoyo (`generar_verdad_mockup.py` genera la verdad conocida que usa `socr evaluar`).

## Nota sobre la documentación

El README enlaza `docs/manual-de-uso.md`, `contrato-json.md`, `catalogo-senales.md`, `analisis-librerias-ocr.md` y `api-webapp.md`, pero esa carpeta `docs/` no está en este checkout. Para el contrato JSON, la fuente de verdad es `contrato.py` (también expuesto en `GET /esquema`).
