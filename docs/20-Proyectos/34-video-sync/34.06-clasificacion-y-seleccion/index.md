---
tema: clasificación y selección de assets
tipo: nota
proyecto: video-sync
creada: 2026-07-02
actualizada: 2026-07-02
estado: activa
aliases: [Clasificación y selección, Clasificador y selección de assets]
tags: [video-sync, clasificador, seleccion, openai, assets]
---

# 34.06 Clasificación y selección

Fuente: `synced_edit/classifier.py` y `synced_edit/asset_selection.py` del repo.

Dos etapas independientes unidas por un solo archivo. El **clasificador** manda cada asset a un modelo de visión de OpenAI y escribe `assets.json` (ruta relativa → lista de tags). La **selección** lee ese archivo y ordena los assets por afinidad con el mood antes de que `build_timeline` los reparta en los cortes. Ninguna de las dos toca el render ni el cronograma de la canción.

```
images/ ┐
videos/ ┴─> classify_folder ──> assets.json ──> load_asset_tags ─┐
              (OpenAI)          (rel -> tags)                    │
                                                                 v
              collect_assets ──> [Path, ...] ──────────> select_assets ──> [Path, ...]
                                                          (mood, smart)      ordenados
                                                                                │
                                                                                v
                                                                          build_timeline
```

## El clasificador de visión

`list_assets` (`classifier.py:137-153`) busca en exactamente dos carpetas: `<root>/images` con `IMAGE_EXTENSIONS` y `<root>/videos` con `VIDEO_EXTENSIONS`. Es recursivo (`rglob("*")`) pero el filtro de extensión está **ligado a la carpeta**: una imagen dentro de `videos/` no se recoge. Carpetas inexistentes se saltan. El orden es lexicográfico case-insensitive sobre la ruta relativa POSIX, así que `images/` precede a `videos/`.

| Constante | Valor exacto | Cita |
|---|---|---|
| `IMAGE_EXTENSIONS` | `.jpg .jpeg .png .webp .gif .bmp .tif .tiff` | `classifier.py:46` |
| `VIDEO_EXTENSIONS` | `.mov .mp4 .m4v .avi .mkv .webm` | `classifier.py:47` |
| `DEFAULT_TAGS` | 24 tags (abajo) | `classifier.py:48-73` |

`DEFAULT_TAGS`, en su orden real: `flower`, `plant`, `nature`, `closeup`, `wide_shot`, `warm`, `cool`, `dark`, `bright`, `calm`, `sad`, `lonely`, `decay`, `delicate`, `high_energy`, `motion`, `dramatic`, `euphoric`, `gentle`, `intense`, `melancholic`, `mysterious`, `nostalgic`, `urban`.

Esas constantes están **duplicadas**: `asset_selection.py:7` importa `IMAGE_EXTENSIONS` / `VIDEO_EXTENSIONS` desde `.timeline` (`timeline.py:14-15`), no desde el clasificador. Y **ya divergen**: el set de imágenes de `timeline.py` es `.jpg .jpeg .png .webp .bmp .tiff`: le faltan `.gif` y `.tif`. Consecuencia real: un `.gif` o un `.tif` que el clasificador sí recoge y etiqueta cae del lado **video** en `_profile_asset` (bonus +0.4 y cubeta de videos en el intercalado, ver abajo). Los dos sets de video sí coinciden.

### Muestreo de frames de video

Un video no se manda entero: se extraen `--video-frames` frames (default **4**) y los cuatro viajan en **una sola** llamada a la API. Prefijo de nombre = `sha1(str(video_path))[:12]` para evitar colisiones en el temp dir compartido.

Comando primario: selección por cambio de escena (`classifier.py:182-197`):

```bash
ffmpeg -hide_banner -loglevel error -i <video> \
  -vf "select='eq(n,0)+gt(scene,0.12)',scale='min(1024,iw)':-2" \
  -vsync vfr -frames:v 4 <tmp>/<sha1>_frame_%02d.jpg
```

Umbral de escena **0.12**; `eq(n,0)` fuerza incluir siempre el frame 0; `scale='min(1024,iw)':-2` limita el ancho a 1024 px sin ampliar. Si eso produce **cero** frames, corre el fallback por fps fijo (`classifier.py:204-217`):

```bash
ffmpeg -hide_banner -loglevel error -i <video> \
  -vf "fps=4/10,scale='min(1024,iw)':-2" \
  -frames:v 4 <tmp>/<sha1>_fallback_%02d.jpg
```

Aritmética exacta: `fps = frame_count/10` → con el default, **0.4 fps** = un frame cada **2.5 s**. Para obtener los 4 frames el video debe durar ≳7.5 s; uno más corto devuelve menos (o ninguno).

Las **imágenes no se redimensionan**: `image_data_url` (`classifier.py:156-159`) lee el archivo completo y lo inlinea en base64 (crecimiento ~4/3). Un JPEG de 12 MP se envía tal cual.

### La llamada a la API

`classify_media` (`classifier.py:252-318`) habla HTTP crudo con `urllib` contra la API **Responses** (`POST https://api.openai.com/v1/responses`), no Chat Completions, y **no usa el SDK `openai`**. `timeout=90`, **sin reintentos ni backoff**: un 429 aborta la corrida (aunque el progreso ya escrito sobrevive).

El prompt pide `{"tags":[...]}` y dice *"**Prefer** only these tags"*: es una sugerencia. La garantía dura la aplica `normalize_tags` (`classifier.py:232-249`), que hace `strip().lower().replace(" ", "_")`, descarta lo que no esté en la allowlist, deduplica preservando orden y corta en `max_tags`.

`--sleep` (default **0.2 s**) se duerme **después de cada asset**, incluido el último. El skip de video sin frames se salta el sleep (hace `continue` antes).

## El flujo incremental

`assets.json` **se rellena, no se reconstruye**. Es el mecanismo que evita re-pagar por media ya etiquetada.

```
assets.json existe?
  ├─ no ──> metadata = {}
  └─ sí ──> load_existing_metadata()   (JSON corrupto o ilegible -> {}, en silencio)
                   │
                   v
   pending = [assets cuya clave relativa NO esté ya en metadata]
                   │
       ┌───────────┴────────────┐
       │                        │
  pending vacío            pending con items
       │                        │
  aviso a stderr          exige OPENAI_API_KEY  <- primer punto donde se pide
  return SIN escribir,          │
  SIN key, SIN red         por cada asset:
                                ├─ ¿video? -> extract_video_frames (ffmpeg)
                                ├─ classify_media (1 request)
                                ├─ _write_metadata  <- reescribe el archivo ENTERO
                                └─ time.sleep(0.2)
```

Reglas verificadas:

- **Solo se clasifica lo que falta.** `pending` filtra por presencia de la clave, no por contenido ni por fecha (`classifier.py:389-393`).
- **Se escribe tras cada asset**, no al final (`classifier.py:446`, con comentario explícito). Una corrida interrumpida con Ctrl-C o un crash **conserva el progreso** y el re-run reanuda donde se quedó.
- **Las ediciones a mano se preservan.** El merge es por clave: si editaste los tags de `images/IMG_0996.JPG`, esa clave ya existe → no entra en `pending` → nadie la pisa. `_write_metadata` reescribe el archivo completo, pero desde el dict que ya contiene tus ediciones.
- **`--force` re-clasifica todo**: `incremental=not args.force` (`classifier.py:480`) → `metadata = {}` → `pending` es la lista entera. Ignora el `assets.json` existente y lo sobrescribe: **también borra las ediciones a mano**.
- Formato de salida (`classifier.py:455-460`): claves ordenadas, `indent=2`, `ensure_ascii=False`, newline final. Escritura **no atómica** (sin tmp+rename).

**`--force` solo existe en el clasificador standalone.** El CLI principal nunca pasa `incremental` (`cli.py:211-217`), así que desde `python -m synced_edit.cli` el modo es siempre incremental. Para re-etiquetar todo hay que correr el clasificador aparte o borrar `assets.json`.

Un `assets.json` corrupto no bloquea nada aquí: `load_existing_metadata` lo trata como `{}`: pero eso **descarta todo el progreso previo**, no solo la última entrada. Y `load_asset_tags` del otro módulo **no** es igual de tolerante (ver abajo).

**Trampa del skip de video.** Si ffmpeg no extrae frames, el clasificador persiste `metadata[asset_key] = []` (`classifier.py:431-435`). Esa clave vacía cuenta como "ya clasificada" → **nunca se reintenta** en corridas incrementales. Hace falta `--force` o editar el JSON a mano. Aguas abajo el asset queda con `set()` de tags y puntúa casi 0 en `smart`. Sin ningún error visible tras la primera corrida.

## Cuándo hace falta `OPENAI_API_KEY`

La key se pide **tarde y solo si hay trabajo nuevo** (`classifier.py:403-409`, con comentario que lo declara intencional):

| Situación | ¿Pide key? |
|---|---|
| Carpeta completamente clasificada (`pending` vacío) | **No.** Retorna en `:395-401`, sin key, sin red, sin escribir |
| Hay al menos un asset sin clasificar | **Sí.** Falta → `RuntimeError("Missing OPENAI_API_KEY. Export it before running the classifier.")` |
| `--no-auto-classify` | **No.** `_ensure_assets_metadata` retorna en `:195-196` |
| Sin `--project-folder` | **No.** El clasificador **nunca corre** desde el CLI principal |

Si falta la key con trabajo pendiente, el CLI **no rompe el render**: `_ensure_assets_metadata` atrapa `RuntimeError` (`cli.py:218-223`) y avisa a `stderr`:

```
Auto-classification skipped: Missing OPENAI_API_KEY. Export it before running the classifier.
Continuing with existing tags (selection falls back to plain ordering).
```

El pipeline sigue con los tags que haya. Sin tags, `select_assets` smart puntúa todo con los bonus incondicionales y el resultado se parece a un orden plano.

**El fallback no cubre todo.** El CLI atrapa **solo `RuntimeError`**. `classify_folder` lo levanta en los casos esperados (sin key, carpeta inexistente, sin assets, ffmpeg ausente, error HTTP, fallo de certificado SSL), pero un `URLError` no-SSL se re-lanza crudo (`classifier.py:309`) → **un fallo de red (DNS, timeout) aborta el pipeline con traceback**, no cae al fallback. Igual con `CalledProcessError` de ffmpeg y `JSONDecodeError`.

Cuesta dinero y `.gitignore` ignora `assets.json` sin barra inicial → nunca se commitea, y clonar el repo fuerza re-clasificar.

## Formato de `assets.json`

Ruta relativa POSIX a la raíz del proyecto → lista de tags. El case del filesystem se preserva.

```json
{
  "images/IMG_0996.JPG": ["flower", "closeup", "warm", "delicate"],
  "images/IMG_1002.JPG": ["nature", "wide_shot", "calm"],
  "videos/CLIP_004.MOV": ["motion", "high_energy", "urban"]
}
```

`load_asset_tags` (`asset_selection.py:32-44`) lo lee y convierte cada lista a un **set** con `str(tag).strip().lower()`. Nombre **hardcodeado** `assets.json` bajo `project_folder`: si corriste el clasificador con `--output` a otra ruta, la selección **no lo encuentra** y devuelve `{}` en silencio.

Asimetría de robustez verificada: `load_existing_metadata` (clasificador) tolera JSON corrupto → `{}`; `load_asset_tags` (selección) hace `json.loads` **sin try/except** (`asset_selection.py:39`) → un `assets.json` roto **lanza `JSONDecodeError`** y tumba el pipeline.

`_tags_for_asset` (`asset_selection.py:130-141`) resuelve la clave probando tres candidatos **en este orden**: `asset.name` (basename) → `str(asset)` → ruta relativa POSIX al proyecto. Las claves que escribe el clasificador son relativas, es decir el candidato de **menor** prioridad; funciona porque el basename nunca matchea. Precedencia frágil: un `assets.json` con una clave suelta de basename ganaría sobre la relativa. Sin match → `set()`, sin error.

## `select_assets()`

```python
select_assets(assets, mood=None, selection="order", metadata=None, project_folder=None) -> list[Path]
```

`asset_selection.py:47-66`. Dos modos, nada más:

| `--selection` | Comportamiento |
|---|---|
| `order` | `return assets` tal cual (`:54-55`). **`--mood` y `assets.json` se ignoran por completo.** |
| `smart` | Puntúa, ordena e intercala. Default del CLI (`cli.py:51`). |

Cualquier otro valor → `ValueError(f"Unknown selection mode: {selection}")`. El default de la **firma** es `order`, el del **CLI** es `smart`.

### Fórmula de scoring

`_profile_asset` (`asset_selection.py:85-127`) arranca en `0.0` y acumula en este orden. `source_type = "image"` si la extensión está en el `IMAGE_EXTENSIONS` **de `timeline.py`** (el corto, sin `.gif` ni `.tif`), si no **`"video"`** (cualquier extensión desconocida cae en video, `.gif` y `.tif` incluidos).

| Regla | Delta |
|---|---|
| `len(tags & mood_tags) * 4` | **+4.0 por cada tag que coincida con el mood** |
| `flower` en tags | +2.5 |
| `plant` **o** `nature` en tags | +1.5 (no acumulativo) |
| `calm` en tags | +1.0 |
| `bright` en tags | +0.6 |
| `motion` en tags **y** es video | +1.2 |
| `high_energy` en tags **y** `mood == "sad"` | **−2.0** (única penalización) |
| es video | +0.4 (sesgo incondicional) |
| `melancholic` y mood ∈ {`sad`, `melancholic`} | +2.0 |
| `nostalgic` y mood ∈ {`sad`, `melancholic`, `nostalgic`} | +1.5 |
| `intense` y mood ∈ {`intense`, `dramatic`} | +2.0 |
| `euphoric` y mood ∈ {`happy`, `euphoric`, `intense`} | +1.5 |
| `gentle` y mood ∈ {`calm`, `gentle`, `melancholic`} | +1.5 |
| `mysterious` y `mood == "mysterious"` | +2.5 |
| `urban` y mood ∈ {`intense`, `dramatic`} | +0.8 |

Los bonus extendidos se **suman encima** del ×4: con `mood="melancholic"` y tag `melancholic`, son +4.0 +2.0 = **+6.0**. No hay deduplicación.

`MOOD_TAGS` (`asset_selection.py:10-21`) tiene **10 moods**: `sad`, `calm`, `happy`, `dramatic`, `intense`, `melancholic`, `mysterious`, `euphoric`, `nostalgic`, `gentle`. Un mood desconocido **no falla**: `MOOD_TAGS.get((mood or "").lower(), set())` degrada a set vacío.

Ejemplo verificado: `mood="sad"`, asset `.mov`, tags `{flower, calm, melancholic, high_energy}`:

```
mood_tags["sad"] = {calm, cool, flower, plant, closeup, nature, melancholic, gentle, nostalgic}
∩ = {calm, flower, melancholic} -> 3 x 4      = +12.0
flower                                        =  +2.5   -> 14.5
calm                                          =  +1.0   -> 15.5
high_energy con mood=sad                      =  -2.0   -> 13.5
video                                         =  +0.4   -> 13.9
melancholic con mood in {sad, melancholic}    =  +2.0   -> 15.9
```

### Orden final

```python
profiles.sort(key=lambda item: (-item.score, item.source_type, item.path.name.lower()))
```

Score descendente; empate → `source_type` alfabético (`"image" < "video"`, imagen primero); empate → nombre lowercase. **Determinista, sin `random`**: el "shuffled ordering" del commit `295ef0c` no vive aquí: es `_pick()` en `timeline.py`, que baraja a partir de la segunda ronda y respeta este orden en la primera.

Después, `_interleave_types` (`asset_selection.py:144-156`) reordena en **2 imágenes → 1 video → 2 imágenes → 1 video**, preservando el ranking dentro de cada tipo y drenando el que sobre cuando el otro se agota. La salida es siempre una permutación exacta de la entrada. Efecto: la variedad de tipos gana sobre el ranking puro: un video de score bajo puede quedar por delante de imágenes de score alto.

## `--mood` y la emoción autodetectada

`effective_mood = args.mood or (analysis.detected_emotion if not args.no_auto_emotion else None)` (`cli.py:111`).

| `--mood` | `--no-auto-emotion` | `effective_mood` |
|---|---|---|
| `bittersweet` (default) | no | **`bittersweet`**: el default gana siempre |
| `bittersweet` (default) | sí | `bittersweet` |
| `""` (cadena vacía) | no | la emoción detectada (`detect_emotion`) |
| `""` (cadena vacía) | sí | `None` → `mood_tags` vacío |
| `sad` explícito | cualquiera | `sad` |

**El default `"bittersweet"` siempre gana sobre la detección.** Hay que pasar `--mood ""` explícitamente para usar la emoción autodetectada; solo entonces se imprime `Using detected emotion '<x>' for asset selection.`

**`bittersweet` NO es una clave de `MOOD_TAGS`.** Verificado contra el dict real (las 10 claves están listadas arriba). Con el default del CLI, `MOOD_TAGS.get("bittersweet", set())` → **set vacío** → el término `×4` es **siempre 0**, y como las 7 reglas extendidas comparan `mood` contra literales (`"sad"`, `{"intense","dramatic"}`, …), **todas quedan muertas también**. Con `--selection smart --mood bittersweet` (los dos defaults) el scoring se reduce a los bonus incondicionales:

```
flower +2.5 | plant|nature +1.5 | calm +1.0 | bright +0.6 | motion&video +1.2 | video +0.4
```

Es decir: los tags siguen importando, pero el mood **no discrimina nada**. Un asset con `high_energy` tampoco recibe la penalización de −2.0. Para que el mood pese hay que pasar uno de los 10 nombres reales, o `--mood ""` y dejar que `detect_emotion` elija (emite `intense`, `happy`, `dramatic`, `calm`, `melancholic` o `sad`: los seis caen dentro de `MOOD_TAGS`). Ver [[20-Proyectos/34-video-sync/34.03-analisis-de-audio/index|34.03 Analisis de audio]] para los umbrales de la detección y sus zonas muertas.

**Case-sensitivity.** `mood_tags` se resuelve con `.lower()` (`:60`), pero las reglas de la penalización y las extendidas comparan `mood` **crudo**. Pasar `--mood Sad` activa el ×4 pero **desactiva en silencio** las 7 reglas extendidas y el −2.0. Usar siempre minúsculas.

El mood efectivo queda registrado en el timeline: `timeline.selection` guarda `mode`, `mood` (el crudo), `detected_emotion`, `effective_mood` y `metadata_file` (`cli.py:150-156`).

## Uso del clasificador como entrypoint independiente

```bash
export OPENAI_API_KEY="..."
python3 -m synced_edit.classifier /ruta/al/proyecto
```

Escribe `<root_dir>/assets.json` por defecto. Para forzar re-etiquetado completo:

```bash
python3 -m synced_edit.classifier /ruta/al/proyecto --force
```

| Flag | Tipo | Default exacto |
|---|---|---|
| `root_dir` | posicional |: (requerido) |
| `--output` | str | `<root_dir>/assets.json` |
| `--model` | str | `gpt-4.1-mini` |
| `--max-tags` | `int` | `5` |
| `--allowed-tags` | str | los 24 `DEFAULT_TAGS`, coma-separados |
| `--sleep` | `float` | `0.2` |
| `--video-frames` | `int` | `4` |
| `--force` | `store_true` | `False` |
| `--ffmpeg` | str | `ffmpeg` (resuelto desde PATH) |

`main()` (`classifier.py:463-485`) atrapa **solo `RuntimeError`** → mensaje a `stderr` y exit code 1. Todo lo demás sale como traceback.

Desde el CLI principal, el clasificador se controla con **tres** flags (`cli.py:58-78`): `--no-auto-classify` (no correrlo nunca), `--classify-model` (default `gpt-4.1-mini`) y `--classify-max-tags` (default `5`). `--sleep` y `--video-frames` **no están expuestos** ahí: `_ensure_assets_metadata` no los pasa, así que aplican los defaults `0.2` y `4`.

Los logs de progreso van todos a `stderr` (`[3/12] Classifying images/IMG_0996.JPG...`); solo la línea final `Wrote N entries to <path> (M new).` va a `stdout`.

**Orden de operaciones peligroso.** `_ensure_assets_metadata` corre en `cli.py:81`, **antes** de `parse_timecode` (`:82-83`). Un `--audio-start` inválido aborta con traceback **después** de haber clasificado y pagado. Conviene validar el rango con una corrida corta antes de lanzar un proyecto grande.

## Notas relacionadas

- [[20-Proyectos/34-video-sync/index|Video Sync]]
- [[20-Proyectos/34-video-sync/34.01-vision-y-estado/index|34.01 Vision y estado]]
- [[20-Proyectos/34-video-sync/34.02-arquitectura/index|34.02 Arquitectura]]
- [[20-Proyectos/34-video-sync/34.03-analisis-de-audio/index|34.03 Analisis de audio]]
- [[20-Proyectos/34-video-sync/34.04-timeline-y-cortes/index|34.04 Timeline y cortes]]
- [[20-Proyectos/34-video-sync/34.07-uso-y-flags/index|34.07 Uso y flags]]
