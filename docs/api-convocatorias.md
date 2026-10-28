# API de convocatorias (backend del mockup de validación documental)

Contrato entre el mockup `stracon.validacion.documental` y `stracon-ocr`. El mockup crea una convocatoria, la lee de SharePoint y, **justo después**, pide a este servicio que la analice. Este servicio lee SharePoint por el flujo de Power Automate, procesa cada PDF, deja el resultado en archivos JSON y el mockup consulta el avance y los resultados.

## Estructura esperada en SharePoint

`<biblioteca> / <carpeta de la convocatoria> / <cargo> / <postulante> / <archivos>`. La ruta que se envía apunta a la carpeta de la convocatoria. Nivel 1 bajo ella = cargo, nivel 2 = postulante, y los archivos cuelgan del postulante (a cualquier profundidad). Una carpeta de postulante cuyo nombre empieza por `Documentos_` (`Documentos_Genéricos`, `Documentos_Sin_Clasificar`) no es una persona: se procesa igual pero se marca `generico: true`.

## Flujo de Power Automate que se consume

POST con `Content-Type: text/plain` y cuerpo JSON `{action, siteUrl, path}` a la URL del flujo. Acciones usadas (ver `../stracon.validacion.documental/flujo_listDeepest.md` y `docs/02_conector_mockup_contrato.md` del mockup):

- `listDeepest` → `{ok, rutaInicial, carpetas[], archivos[]}` (árbol plano con `FileLeafRef`, `FileRef`, `FileDirRef`, `FSObjType`, `Modified`, `EstadoValidacion`, `UltimaActividad`, `FieldValuesAsText.Editor`; **no trae `File_x0020_Size`**). La jerarquía se arma en el cliente: la lógica de referencia está en `../stracon.validacion.documental/vd-conector.js` (`normalizeConvocatoria`): comparar rutas sin distinguir mayúsculas, ignorar archivos con menos de 3 segmentos bajo la convocatoria, un postulante puede existir solo como carpeta.
- `getFile` con `path` = `FileRef` del archivo → `{ok, name, contentBase64, ...}`. El flujo valida el sitio permitido y responde `ok:false` con `error.code` si falla.

La URL del flujo es un secreto: **no se guarda, no se escribe en logs ni en los JSON, no aparece en mensajes de error**. Llega en cada petición.

## Endpoints

### `POST /convocatorias` → 202
```json
{ "flujo_url": "https://…powerplatform.com/…", "sitio_url": "https://<tenant>.sharepoint.com/sites/<sitio>",
  "ruta": "/sites/<sitio>/<biblioteca>/<carpeta>", "nombre": "PROCESO_202611" }
```
`nombre` es opcional (por defecto el último tramo de `ruta`). Responde `{id, nombre, estado}`. El `id` es estable: `sha1(sitio_url.lower() + "|" + ruta.lower())[:12]`. Si esa convocatoria ya se está analizando, responde 202 con el mismo `id` sin lanzar otra. Si ya terminó, esta llamada es una **actualización incremental**: vuelve a leer `listDeepest` y solo descarga y procesa los archivos nuevos o cuyo `Modified` cambió (los demás reutilizan su JSON); los que desaparecieron se marcan `estado: "retirado"` pero no se borran sus JSON. Validar `sitio_url` (HTTPS, `*.sharepoint.com`) y `ruta` (empieza por `/sites/` o `/teams/`, sin `..`); 422 si no. Validar `flujo_url` (HTTPS, host `*.logic.azure.com` o `*.powerplatform.com`).

### `GET /convocatorias/{id}` → progreso
```json
{ "id": "…", "nombre": "PROCESO_202611", "ruta": "…", "estado": "analizando",
  "total": 133, "procesados": 41, "omitidos": 0, "con_error": 1, "postulantes": 35, "cargos": 11,
  "iniciado_en": "…", "terminado_en": null, "error": null }
```
`estado`: `leyendo` (listDeepest) → `analizando` → `listo`, o `error` (con `error` en español sin secretos). `total` = archivos PDF/imagen/Word que cuelgan de un postulante. 404 si no existe.

### `GET /convocatorias/{id}/resultados` → resultados (parciales mientras analiza)
```json
{ "convocatoria": { "id": "…", "nombre": "…", "ruta": "…", "estado": "listo" },
  "cargos": [ { "nombre": "OPERADORES", "postulantes": [ {
      "nombre": "A.DUARTE", "ruta": "/sites/…/OPERADORES/A.DUARTE", "expediente_id": "<sha1(ruta)[:10]>", "generico": false,
      "expediente": { /* Expediente del contrato v1.0 o null si aún no se calcula */ },
      "documentos": [ { "ruta": "<FileRef>", "nombre": "02.- Licencia de Conducir A. DUARTE.pdf", "modificado": "2026-10-27T19:26:39",
                        "estado": "listo", "motivo": null, "documento": { /* DocumentoVista */ } } ] } ] } ] }
```
`documentos[].estado`: `pendiente` | `listo` | `error` | `omitido` (supera `SOCR_MAX_MB`, por defecto **25 MB**, o formato no soportado; `motivo` en español) | `retirado`. `DocumentoVista` = el `Documento` completo del contrato v1.0 **sin** `paginas[].bloques` ni `paginas[].tablas` (pesan mucho): conserva `paginas: [{n, ancho, alto, dpi, rotacion, fuente_texto, motor}]` (el mockup necesita `ancho`/`alto` para convertir las cajas), `id`, `archivo`, `clasificacion`, `codigos`, `campos`, `senales`, `observaciones`, `validaciones`, `resumen`, `versiones`, `errores`. El JSON completo sigue en `GET /documentos/{id}`.

### Existentes que se reutilizan
`GET /documentos/{id}`, `GET /documentos/{id}/archivo`, `GET /salud`. CORS según `SOCR_CORS`.

## Archivos JSON (hasta migrar a una base documental)

Raíz: `SOCR_RESULTADOS` (por defecto `datos/resultados/`, ya ignorada por git: puede contener datos personales).

```
<id>/convocatoria.json                       progreso + índice de archivos (ruta, modificado, sha256, documento_id, estado, motivo)
<id>/<cargo>/<postulante>/<archivo>.json     Documento completo (contrato v1.0)
<id>/<cargo>/<postulante>/_expediente.json   Expediente (contraste entre los documentos del postulante)
```
Nombres saneados para Windows (sin `<>:"/\|?*`, sin puntos ni espacios finales; si dos nombres colisionan, sufijo con 6 caracteres del hash). Se escriben de forma atómica (archivo temporal + renombrar). El SQLite actual se mantiene para duplicados, búsqueda y trabajos. La escritura de JSON va detrás de una interfaz pequeña (`ResultadosJSON`) para sustituirla después por Cosmos DB sin tocar el pipeline.

## Reglas de procesamiento

- Clave de expediente de cada postulante: `expediente_id = sha1(ruta del postulante)[:10]` (opaca; el RUT real sale de los documentos y queda en `Expediente.identidad.rut`). Verificar que el contraste y las validaciones no asuman que la clave es un RUT.
- Descarga: `getFile` secuencial o con 2 en paralelo; reintento una vez ante error de red; el archivo decodificado no puede superar `SOCR_MAX_MB`.
- Procesamiento: reutilizar el `Procesador` y el grupo de trabajadores existentes; al terminar los documentos de un postulante, calcular su contraste y escribir `_expediente.json`.
- Un fallo en un archivo no detiene la convocatoria: queda `estado: "error"` con motivo.
- Los PDF reales y los resultados contienen datos personales: nunca a logs, nunca a servicios externos (motor `azure` prohibido aquí).

## Implementación (decisiones donde el contrato dejaba margen)

Código: `src/stracon_ocr/convocatorias.py` (jerarquía, orquestación, estado), `flujo.py` (cliente del flujo, solo `urllib`), `almacen/resultados.py` (`ResultadosJSON` + `ArchivosJSON`) y los tres endpoints en `api.py`. Pruebas: `tests/test_convocatorias.py` con un flujo falso local (`tests/flujo_falso.py`), sin red.

- **Variables**: `SOCR_RESULTADOS` (raíz de los JSON), `SOCR_MAX_MB` (25 por defecto; límite por archivo descargado) y `SOCR_FLUJO_HOSTS` (dominios permitidos del flujo; solo para pruebas locales admite `127.0.0.1`/`localhost`, los únicos que aceptan `http`).
- **Estado y reanudación**: todo el estado vive en `<id>/convocatoria.json` (`arbol` con cargos, postulantes y archivos más los contadores). Si el servicio se reinicia a mitad, `GET` informa `estado: "error"` con un mensaje que pide repetir el `POST`; ese `POST` (que trae la URL del flujo, que no se guarda) reanuda y solo procesa lo que quedó `pendiente`.
- **Actualización incremental**: se compara cada archivo por (ruta sin distinguir mayúsculas, `Modified`). Un archivo con `Modified` distinto se vuelve a descargar, pero si su SHA-256 no cambió se reutiliza el resultado sin reprocesar. Los archivos con `error` se reintentan en cada actualización; los `omitido` por tamaño se reintentan si se sube `SOCR_MAX_MB`. Un cargo o postulante que desaparece conserva sus archivos como `retirado`.
- **`total`** cuenta los archivos con formato soportado (los mismos que acepta el pipeline) bajo un postulante. Los de otro formato (`.txt`, `.xlsx`…) se listan como `omitido` («Formato no soportado») pero no entran en `total` ni en `omitidos`.
- **Límite del flujo**: `getFile` solo entrega el contenido de archivos de hasta 10 MB; si no hay contenido y el tamaño declarado lo supera, el archivo queda `omitido` con ese motivo. El tamaño se revisa antes de decodificar el base64.
- **`id`**: `sha1(sitio_url.lower() + "|" + ruta.lower())[:12]` quitando antes las barras finales. `expediente_id` es `sha1(ruta del postulante)[:10]` y es la clave del expediente en el SQLite y en el contraste; el RUT real sale de los documentos.
- **Nombres en disco**: `<archivo>.json` incluye la extensión original (`licencia.pdf.json`); cada tramo mide como máximo 80 caracteres (si se corta, se agrega el hash); los colisionados (sin distinguir mayúsculas) reciben `-<6 hex del hash de la ruta>` y lo ya asignado no cambia entre ejecuciones.
- **Concurrencia**: un hilo por convocatoria; archivos en paralelo según `SOCR_TRABAJADORES` (mínimo 1) y como máximo 2 descargas simultáneas. El contraste de un postulante se calcula cuando terminan todos sus archivos.
- **Errores 422**: `{"detail": [{"loc": ["body", campo], "msg": "…", "type": "value_error"}]}`; nunca repiten el valor recibido (la URL del flujo es secreta). Las validaciones extra respecto del texto original: la `ruta` debe pertenecer al sitio de `sitio_url` y tener al menos sitio/biblioteca/carpeta.
- **Secreto**: la URL vive solo en memoria durante el análisis; las excepciones de red se traducen sin su texto original y cualquier mensaje o log pasa por `redactar()` (reemplaza la URL, su ruta y su firma por `[flujo]`).

Probar a mano: levantar el flujo falso (`FlujoFalso` en `tests/flujo_falso.py`), `SOCR_FLUJO_HOSTS=logic.azure.com,powerplatform.com,127.0.0.1 socr servir`, y `POST /convocatorias` con `flujo_url` apuntando a `http://127.0.0.1:<puerto>/…`.
