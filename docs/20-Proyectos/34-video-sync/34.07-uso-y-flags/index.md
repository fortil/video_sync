---
tema: uso y flags del cli
tipo: índice
proyecto: video-sync
creada: 2026-07-02
actualizada: 2026-07-02
estado: activa
aliases: [Uso y flags, Referencia de flags, CLI de synced_edit]
tags: [video-sync, cli, flags, uso]
---

# 34.07 Uso y flags

Fuente: `synced_edit/cli.py` y `synced_edit/timecode.py` del repo.

Referencia completa de la interfaz de línea de comandos de `synced_edit`. Todos los defaults de esta nota salen de las llamadas `add_argument` del parser de `main()` en `synced_edit/cli.py:19-78`. Donde el default del CLI difiere del default de la función de librería, se señala explícitamente.

## Invocación

El punto de entrada verificable es el módulo:

```bash
python -m synced_edit.cli --project-folder ~/proyectos/demo
```

Existe `src/synced_edit.py`, un shim de 6 líneas que hace `from synced_edit.cli import main`. Al ejecutarlo directo (`python src/synced_edit.py`) Python antepone `src/` a `sys.path` y `import synced_edit` puede resolver al propio script en lugar del paquete. Usa `python -m synced_edit.cli` desde la raíz del repo. No hay `pyproject.toml` ni `setup.py`: el paquete no es instalable.

## Los dos modos de uso

`_resolve_project_defaults(args)` (`cli.py:226-249`) bifurca todo el comportamiento de rutas según haya o no `--project-folder`.

```
                    ┌─ --project-folder ausente ─┐
                    │  --audio OBLIGATORIO        │
                    │  output   = outputs/final.mp4
                    │  timeline = outputs/timeline.json
                    │  analysis = outputs/audio_analysis.json
                    │  report   = outputs/reporte_<fecha>_synced_edit.md
                    │  assets   = assets/images assets/videos
                    │  clasificador: NUNCA corre
                    └─────────────────────────────┘

                    ┌─ --project-folder presente ─┐
   <project>/       │  audio    = <project>/song.mp3
     song.mp3       │  assets   = [<project>/images, <project>/videos]
     images/        │  salidas  → <project>/output/   (SINGULAR)
     videos/        │  clasificador: corre si falta assets.json
     assets.json    │
     output/        └─────────────────────────────┘
```

### Modo A: flags sueltos

Sin `--project-folder`, `--audio` es obligatorio: si falta, `raise SystemExit("--audio is required unless --project-folder is provided")` (`cli.py:228-229`).

```bash
python -m synced_edit.cli \
  --audio ~/musica/cancion.mp3 \
  --assets ~/fotos ~/clips \
  --output outputs/final.mp4
```

Los defaults `outputs/final.mp4`, `outputs/timeline.json` y `outputs/audio_analysis.json` se resuelven en `cli.py:230-232`. El reporte **no** se resuelve ahí: queda en `None` y lo decide `main()` en `cli.py:171` como `Path("outputs") / f"reporte_{date.today().isoformat()}_synced_edit.md"`.

### Modo B: `--project-folder` (convención)

```bash
python -m synced_edit.cli --project-folder ~/proyectos/demo
```

La convención de carpeta es `song.mp3` + `images/` + `videos/`, con salidas a `output/`. Cualquier flag de ruta explícito gana sobre la convención (`args.audio or project / "song.mp3"`, `cli.py:240`).

`--audio` no se valida en este punto: si `<project>/song.mp3` no existe, el `FileNotFoundError` sale de `analyze_audio` (`audio_analysis.py:35-36`), no del CLI.

**Detección de `--assets` por comparación de valor** (`cli.py:241-244`): el código compara `args.assets != [Path("assets/images"), Path("assets/videos")]`. Si pasas explícitamente `--assets assets/images assets/videos` junto a `--project-folder`, la lista es idéntica al default y **se descarta en silencio**, sustituida por `[<project>/images, <project>/videos]`. No hay forma de distinguir "default" de "pasado explícitamente".

## Flags: entradas y salidas

| Flag | Tipo | Default real | Cita |
|---|---|---|---|
| `--project-folder` | `Path` | `None` | `cli.py:19-24` |
| `--audio` | `Path` | `None` (obligatorio sin project folder) | `cli.py:25` |
| `--assets` | `Path`, `nargs="+"` | `[Path("assets/images"), Path("assets/videos")]` | `cli.py:26-32` |
| `--output` | `Path` | `None` → resuelto por modo | `cli.py:33` |
| `--timeline` | `Path` | `None` → resuelto por modo | `cli.py:34` |
| `--analysis` | `Path` | `None` → resuelto por modo | `cli.py:35` |
| `--report` | `Path` | `None` → resuelto por modo | `cli.py:36` |

`collect_assets` (`timeline.py:56-68`) **no es recursivo** (solo hijos directos de cada carpeta) y **no deduplica**. Extensiones aceptadas: `IMAGE_EXTENSIONS = {.jpg .jpeg .png .webp .bmp .tiff}` y `VIDEO_EXTENSIONS = {.mp4 .mov .m4v .webm .mkv .avi}` (`timeline.py:14-15`). `.heic` de iPhone **no** está en la lista: se ignora en silencio.

## Flags: tamaño y calidad

| Flag | Tipo | Default real | Notas |
|---|---|---|---|
| `--width` | `int` | `1080` | `cli.py:37` |
| `--height` | `int` | `1920` | vertical 9:16, `cli.py:38` |
| `--fps` | `int` | `30` | `cli.py:39` |
| `--crf` | `int` | `18` | solo el encode final, `cli.py:40` |
| `--preset` | `str` | `"medium"` | solo el encode final, `cli.py:41` |

`--crf` y `--preset` gobiernan únicamente el ensamblado final. Los clips intermedios usan constantes fijas del renderer: `_INTERMEDIATE_CRF = 12` y `_INTERMEDIATE_PRESET = "veryfast"` (`renderer.py:32-33`), casi sin pérdida, para que la única compresión significativa sea la última.

## Flags: cortes y timing

| Flag | Tipo | Default real | Notas |
|---|---|---|---|
| `--beats-per-cut` | `int` | `2` | `cli.py:42` |
| `--max-image-duration` | `float` | `4.0` | `0` desactiva; no afecta videos |
| `--min-video-duration` | `float` | `4.0` | `0` desactiva; no afecta imágenes |
| `--max-asset-uses` | `int` | `3` | videos exentos; `0` desactiva |
| `--seed` | `int` | `0` | `random.Random(seed)`, determinista |
| `--focus` | `{dynamic,center,face}` | `"face"` | `cli.py:47` |
| `--max-items` | `int` | `None` | `cli.py:56` |
| `--manual-bpm` | `float` | `None` | `cli.py:53` |

**Divergencias CLI vs. librería** (verificadas, no son bugs pero sí trampas para quien use `build_timeline` como API):

| Parámetro | Default de librería | Default del CLI |
|---|---|---|
| `beats_per_cut` | `4` (`timeline.py:77`) | `2` |
| `max_image_duration` | `None` (`timeline.py:79`) | `4.0` |
| `min_video_duration` | `None` (`timeline.py:80`) | `4.0` |
| `focus` | `"dynamic"` (`timeline.py:82`) | `"face"` |
| `mix_video_audio` | `False` (`renderer.py:40`) | `True` |

`max_asset_uses=3` y `seed=0` sí coinciden en ambos lados.

Detalles que el help no dice:

- `--max-asset-uses 0` o negativo ⇒ `cap = None`, sin límite (`timeline.py:105`). Solo enteros `>= 1` producen cap.
- `--min-video-duration` es best-effort, no un piso duro: el crecimiento se detiene si se acaban los segmentos al final de la canción (`timeline.py:198-204`).
- `--max-image-duration` se viola deliberadamente cuando el techo de seguridad `max(1, int(span / 0.35))` recorta el número de piezas por debajo del `ceil` (`timeline.py:221`), y **siempre** para el último item cuando `--max-items` lo estira hasta `song_end` (`timeline.py:249-254`).
- `--focus face` es el default pero su dependencia (`cv2`) está **comentada** en `requirements.txt:16`. Instalación limpia con `pip install -r requirements.txt` ⇒ degrada a `center` con un aviso único a stderr. Además `--focus face` **solo afecta a imágenes**: `_render_video_clip` hardcodea crop centrado (`renderer.py:502-505`).
- `--focus` en `center` o `face` restringe los efectos a `["zoom_in","zoom_out"]`; cualquier otro valor (incluido `dynamic`) usa los 4 con paneos (`timeline.py:125-128`).
- `--manual-bpm 0` es falsy ⇒ se ignora en silencio y corre el análisis normal (`audio_analysis.py:38`). Un valor negativo es truthy y provoca **bucle infinito** en `_regular_beats`.
- `--manual-bpm` puentea el análisis: `onset_strength` queda vacío ⇒ cortes uniformes en vez de adaptativos, y el reporte dirá `uniform (beats_per_cut)`.

## Flags: mezcla de audio

| Flag | Tipo | Default real | Notas |
|---|---|---|---|
| `--mix-video-audio` / `--no-mix-video-audio` | `BooleanOptionalAction` | `True` | `cli.py:48` |
| `--video-audio-volume` | `float` | `1.0` | `cli.py:49` |
| `--background-audio-volume` | `float` | `0.15` | `cli.py:50` |

Ambos volúmenes se pasan siempre al renderer, incluso con `--no-mix-video-audio`; el renderer decide. Con `mix_video_audio=False` (o `--video-audio-volume <= 0`, o cero clips de video) se cae a `_mux_song_only`: la canción es el único audio (`renderer.py:158-161`).

El docstring de `_mux_audio` (`renderer.py:149-150`) todavía describe `mix_video_audio=False` como "today's proven behavior", descripción que ya no refleja el default efectivo del CLI.

## Flags: selección y mood

| Flag | Tipo | Default real | Notas |
|---|---|---|---|
| `--selection` | `{order,smart}` | `"smart"` | `cli.py:51` |
| `--mood` | `str` | `"bittersweet"` | `cli.py:52` |
| `--no-auto-emotion` | `store_true` | `False` | `cli.py:63-67` |

`--selection order` devuelve los assets sin tocar (`asset_selection.py:54-55`): `--mood` y `assets.json` se ignoran por completo. Solo `smart` puntúa e intercala (patrón 2 imágenes → 1 video).

**El default `"bittersweet"` siempre gana sobre la emoción auto-detectada.** La resolución es `effective_mood = args.mood or (analysis.detected_emotion if not args.no_auto_emotion else None)` (`cli.py:111`). Como `"bittersweet"` es truthy, la detección nunca se usa salvo que pases `--mood ""` explícitamente:

```bash
# usar la emocion detectada del audio
python -m synced_edit.cli --project-folder ~/proyectos/demo --mood ""
```

Peor aún: `"bittersweet"` **no es clave de `MOOD_TAGS`** (`asset_selection.py:10-21`, que define exactamente 10 moods: `sad, calm, happy, dramatic, intense, melancholic, mysterious, euphoric, nostalgic, gentle`). Un mood desconocido degrada a `set()` sin warning (`asset_selection.py:60`), así que el término `len(tags & mood_tags) * 4` es 0 y con el default de fábrica solo aplican los bonus incondicionales del scoring.

Las emociones que `detect_emotion` puede emitir son 6: `intense, happy, dramatic, calm, melancholic, sad` (`audio_analysis.py:78-117`). De ellas, `sad` es **código muerto** (exige varianza normalizada `> 0.5` sobre datos acotados a `[0,1]`, máximo teórico 0.25) y el rango de BPM `[95, 110)` siempre cae en `calm` con confianza fija `0.3`.

El umbral de confianza `0.4` (`cli.py:98`) **solo cambia el texto del log** a stderr, no el comportamiento.

## Flags: autoclasificación

| Flag | Tipo | Default real | Notas |
|---|---|---|---|
| `--no-auto-classify` | `store_true` | `False` | `cli.py:58-62` |
| `--classify-model` | `str` | `"gpt-4.1-mini"` | `cli.py:68-72` |
| `--classify-max-tags` | `int` | `5` | `cli.py:73-78` |

`_ensure_assets_metadata` (`cli.py:180-223`) tiene un guard duro: `if args.project_folder is None or args.no_auto_classify: return`. **Sin `--project-folder` el clasificador nunca corre**, aunque pases `--classify-model`.

Llama a `classify_folder(project, output=project/"assets.json", model=..., max_tags=..., allowed_tags=list(DEFAULT_TAGS))` sin pasar `incremental` ⇒ usa el default `True` (`classifier.py:354`). `DEFAULT_TAGS` son 24 tags (`classifier.py:48-73`).

Optimización de costo real: si todos los assets ya están en `assets.json`, `classify_folder` retorna **antes** de exigir `OPENAI_API_KEY` (`classifier.py:395-401`). Una carpeta ya clasificada funciona sin key y sin red.

El CLI captura **solo `RuntimeError`** (`cli.py:218-223`) e imprime `Auto-classification skipped: ...` a stderr, continuando con los tags existentes. Un `URLError` no-SSL (DNS, timeout) se re-lanza crudo desde `classifier.py:309` y **aborta el pipeline con traceback**.

## Flags: control de render

| Flag | Tipo | Default real | Notas |
|---|---|---|---|
| `--audio-start` | `str` (timecode) | `"0"` | `cli.py:54` |
| `--audio-end` | `str` (timecode) | `None` | `cli.py:55` |
| `--skip-render` | `store_true` | `False` | `cli.py:57` |

### `--skip-render`: iterar rápido sin renderizar

```bash
python -m synced_edit.cli --project-folder ~/proyectos/demo --skip-render
```

Corta el pipeline justo antes de `render_timeline` (`cli.py:159-169`). Escribe análisis, timeline y reporte; ffmpeg no toca vídeo. Útil para revisar el JSON del timeline (cortes, `transition_hint`, orden de assets) antes de pagar minutos de encode.

Dos gotchas: el reporte **igual se escribe** referenciando un `.mp4` que no existe (`cli.py:171-172`), y el `print` final imprime `Video: <ruta>` sin condicionar (`cli.py:176`).

### Formatos de timecode

`parse_timecode` (`timecode.py:4-37`) acepta tres formas para `--audio-start` y `--audio-end`:

| Forma | Ejemplo | Fórmula | Cita |
|---|---|---|---|
| Segundos | `"150"`, `"12.5"` | `float(text)` | `timecode.py:13-14` |
| `MM:SS` | `"2:30"` → `150.0` | `minutes * 60 + seconds` | `timecode.py:32` |
| `HH:MM:SS` | `"01:02:03.5"` → `3723.5` | `hours * 3600 + minutes * 60 + seconds` | `timecode.py:37` |

Validaciones: cadena vacía → `ValueError("time value cannot be empty")`; número de partes distinto de 2 o 3 → `ValueError(f"invalid time value: {value!r}")`; negativos → `ValueError("time values must be >= 0")`; `seconds >= 60` en `MM:SS` o `minutes >= 60`/`seconds >= 60` en `HH:MM:SS` → error dedicado. Sin límite superior en horas.

Gotchas verificados:

- Las partes se parsean como `float`, no `int`: `"1:30.5"` = `90.5` es válido, y `"1.5:30"` = `120.0` también (minutos fraccionarios permitidos, sin validación).
- **La validación de negativos no cubre el camino sin `:`**: `parse_timecode("-5")` retorna `-5.0` sin error, porque el `return float(text)` ocurre antes. El guard real vive aguas abajo en `trim_audio` (`audio_analysis.py:133-134`).
- `isinstance(value, int | float)` usa sintaxis PEP 604 en runtime ⇒ requiere Python ≥ 3.10.

### Recorte condicional

```python
if audio_start or audio_end is not None:
    audio_path = Path("work") / "trimmed_audio.wav"
    trim_audio(args.audio, audio_path, start=audio_start, end=audio_end)
```
(`cli.py:85-88`)

Con `--audio-start 0` y sin `--audio-end` no se recorta: `analyze_audio` recibe el archivo original. Con `--audio-end` sin `--audio-start`, sí recorta desde 0.

`Path("work")` está **hardcodeado y es relativo al CWD** (`cli.py:87` y `cli.py:163`), no respeta `--project-folder`. El nombre `work/trimmed_audio.wav` es fijo, sin PID ni hash: dos renders en paralelo desde el mismo CWD colisionan.

**Orden de validación peligroso**: `_resolve_project_defaults` (`cli.py:80`) y `_ensure_assets_metadata` (`cli.py:81`, que puede gastar llamadas a la API de OpenAI) corren **antes** de `parse_timecode` (`cli.py:82-83`). Un `--audio-start "1:99"` aborta con traceback crudo después de haber clasificado y pagado.

## Nombres de archivo de salida con `--project-folder`

```python
project      = args.project_folder.expanduser().resolve()   # cli.py:235
output_dir   = project / "output"                            # cli.py:236  SINGULAR
safe_name    = project.name.replace(" ", "_")                # cli.py:237
range_suffix = _range_suffix(args.audio_start, args.audio_end)  # cli.py:238
```

| Salida | Patrón | Cita |
|---|---|---|
| Video | `<project>/output/{safe_name}{range_suffix}.mp4` | `cli.py:245` |
| Timeline | `<project>/output/{safe_name}{range_suffix}_timeline.json` | `cli.py:246` |
| Análisis | `<project>/output/{safe_name}{range_suffix}_audio_analysis.json` | `cli.py:247` |
| Reporte | `<project>/output/reporte_{YYYY-MM-DD}_{safe_name}.md` | `cli.py:248` |

`safe_name` **solo** reemplaza espacios por `_`. Otros caracteres del nombre de carpeta (`:`, `#`, `%`, acentos, emojis) pasan tal cual al nombre de archivo.

**El reporte NO lleva `range_suffix`.** Dos rangos distintos del mismo proyecto renderizados el mismo día se pisan el `.md` (`write_report` hace `path.write_text`, sobrescribe). El video, timeline y análisis sí se diferencian.

### `_range_suffix`: el sufijo de rango

```python
def _range_suffix(start: str, end: str | None) -> str:
    if start in {"0", "0.0", "0:00"} and end is None:
        return ""
    safe_start = start.replace(":", "-").replace(".", "_")
    safe_end   = "end" if end is None else end.replace(":", "-").replace(".", "_")
    return f"_{safe_start}_{safe_end}"
```
(`cli.py:252-257`)

Opera sobre las **cadenas crudas** de `argparse`, no sobre segundos parseados (se llama en `cli.py:238`, antes de `parse_timecode` en `cli.py:82`).

| `--audio-start` | `--audio-end` | Sufijo | Nombre resultante (proyecto `demo`) |
|---|---|---|---|
| `"0"` (default) | — | `""` | `demo.mp4` |
| `"1:30"` | — | `"_1-30_end"` | `demo_1-30_end.mp4` |
| `"0"` | `"2:45"` | `"_0_2-45"` | `demo_0_2-45.mp4` |
| `"1:05.5"` | `"2:00"` | `"_1-05_5_2-00"` | `demo_1-05_5_2-00.mp4` |
| `"00:17"` | `"02:03"` | `"_00-17_02-03"` | `demo_00-17_02-03.mp4` |

**El set de "inicio cero" es literal y cerrado**: `{"0", "0.0", "0:00"}`. `--audio-start "00:00"`, `"0:00:00"`, `"0.00"` o `"00"` **no** matchean y producen sufijo (`_00-00_end`, etc.) aunque semánticamente sean cero. Consecuencia práctica: `--audio-start 00:00` y `--audio-start 0` escriben a **archivos de salida distintos**.

## Ejemplos ejecutables

```bash
# proyecto completo, convencion por defecto
python -m synced_edit.cli --project-folder ~/proyectos/demo

# rango de la cancion + usar la emocion detectada
python -m synced_edit.cli --project-folder ~/proyectos/demo \
  --audio-start 00:17 --audio-end 02:03 --mood ""
# -> output/demo_00-17_02-03.mp4

# iterar el timeline sin renderizar
python -m synced_edit.cli --project-folder ~/proyectos/demo --skip-render

# horizontal, cortes mas largos, sin audio de los clips
python -m synced_edit.cli --project-folder ~/proyectos/demo \
  --width 1920 --height 1080 --beats-per-cut 4 --no-mix-video-audio

# flags sueltos, calidad alta, orden literal de los assets
python -m synced_edit.cli \
  --audio ~/musica/cancion.mp3 --assets ~/fotos ~/clips \
  --output outputs/final.mp4 --crf 16 --preset slow --selection order

# clasificador standalone (requiere OPENAI_API_KEY)
export OPENAI_API_KEY="..."
python -m synced_edit.classifier ~/proyectos/demo \
  --output ~/proyectos/demo/assets.json
```

## Salida a stdout / stderr

`main()` imprime 4 líneas a **stdout** al terminar (`cli.py:174-177`): `Analysis:`, `Timeline:`, `Video:`, `Report:`. Todo lo demás (progreso del clasificador, avisos de emoción, coarsening de la rejilla, cap excedido, aviso de deriva de duración, fallback de OpenCV) va a **stderr**.

## Discrepancias con `AGENTS.md` y `.gitignore`

`AGENTS.md:7-10` exige que todo render produzca `outputs/timeline.json`, `outputs/final.mp4` y un reporte Markdown. Se cumple solo en el modo sin `--project-folder`. Con `--project-folder` las salidas van a `<project>/output/` (**singular**) con nombres derivados del proyecto. `--skip-render` es otra excepción: termina sin `.mp4`.

Consecuencia en `.gitignore`: el patrón ignora `outputs/` (plural) pero **no** `output/` (singular). Si un proyecto vive dentro del repo, sus `output/*_timeline.json`, `output/*_audio_analysis.json` y `output/reporte_*.md` quedan trackeables (los `.mp4` sí caen por el patrón global `*.mp4`).

## Verificación de la referencia contra el README

Contrastados 1:1 los 32 flags documentados en el README contra `cli.py`: **todos los defaults coinciden**. El README no contradice al código en ningún valor.

Lo que el README **omite** (y esta nota cubre):

- El subsistema xfade completo: `_XFADE_DURATION = 0.25`, el origen de `transition_hint` en secciones de energía baja, el cap de 50 clips, el aviso de deriva de duración.
- Los patrones de nombre de archivo con `--project-folder` (solo dice "las salidas van a `/path/to/project/output/`").
- La existencia del directorio `work/` hardcodeado.
- Cómo correr los tests. Nota práctica verificada: `python3 -m pytest tests/ -q` pasa (5 tests), pero `pytest tests/ -q` falla con `ModuleNotFoundError: No module named 'synced_edit'`: no hay `conftest.py` ni `pyproject.toml` que inyecte la raíz en `sys.path`. `pytest` tampoco está declarado en `requirements.txt`.

## Notas

- [[20-Proyectos/34-video-sync/34.07-uso-y-flags/flujo-por-carpeta-de-proyecto|Flujo por carpeta de proyecto]]: receta probada de flags para `--project-folder`.
- [[20-Proyectos/34-video-sync/34.04-timeline-y-cortes/index|34.04 Timeline y cortes]]: qué hacen `--beats-per-cut`, `--max-image-duration`, `--min-video-duration`, `--max-asset-uses` y `--seed` por dentro.
- [[20-Proyectos/34-video-sync/34.06-clasificacion-y-seleccion/index|34.06 Clasificacion y seleccion]]: `MOOD_TAGS`, scoring de `--selection smart` y el clasificador incremental.
- [[20-Proyectos/34-video-sync/34.03-analisis-de-audio/index|34.03 Analisis de audio]]: `--manual-bpm`, `detect_emotion` y el recorte con `--audio-start`/`--audio-end`.
- [[20-Proyectos/34-video-sync/34.05-render-y-sincronizacion/index|34.05 Render y sincronizacion]]: `--crf`, `--preset`, `--focus` y la mezcla de audio.
- [[20-Proyectos/34-video-sync/index|Video Sync]]: índice del proyecto.
