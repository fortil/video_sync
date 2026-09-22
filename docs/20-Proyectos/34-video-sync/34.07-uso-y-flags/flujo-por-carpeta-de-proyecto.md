---
tema: flujo por carpeta de proyecto
tipo: nota
proyecto: video-sync
creada: 2026-07-02
actualizada: 2026-09-22
estado: activa
aliases: [Flujo por carpeta de proyecto, Receta de flags por proyecto]
tags: [video-sync, uso, flags, project-folder, cli]
---

# Flujo por carpeta de proyecto

Fuente: `synced_edit/cli.py` del repo.

## Contexto

Cada edición vive en su propia carpeta de proyecto (en los ejemplos, `~/proyectos/demo`), con `song.mp3`, `images/` y `videos/`. Las salidas caen en `<carpeta>/output/`. Los defaults del CLI, entre ellos `--mood bittersweet` (alegría más nostalgia), están calibrados para montajes personales de fotos y clips caseros sobre una canción, no para un edit genérico.

```
<carpeta>/
├── song.mp3          ← --audio por defecto (cli.py:240)
├── images/           ← --assets[0] por defecto (cli.py:241-244)
├── videos/           ← --assets[1] por defecto
├── assets.json       ← tags del clasificador (incremental)
└── output/           ← salidas (SINGULAR, cli.py:236)
```

## Comando probado

```bash
python3 -m synced_edit.cli \
  --project-folder ~/proyectos/demo \
  --selection smart \
  --mood bittersweet \
  --beats-per-cut 2 \
  --max-image-duration 9 \
  --min-video-duration 4 \
  --focus center
```

Casi todo ese comportamiento **ya es default del CLI**, así que el comando mínimo basta:

```bash
python3 -m synced_edit.cli --project-folder ~/proyectos/demo
```

Dos matices reales: el comando probado fija `--max-image-duration 9` y `--focus center`, que **no** son los defaults (`4.0` y `face`, `cli.py:43,47`). Si se quieren esos valores exactos hay que seguir pasándolos.

## Flags y defaults (leídos de `cli.py`)

| Flag | Default en código | Cita |
|---|---|---|
| `--selection` | `smart` | `cli.py:51` |
| `--mood` | `bittersweet` | `cli.py:52` |
| `--beats-per-cut` | `2` | `cli.py:42` |
| `--max-image-duration` | `4.0` (0 desactiva) | `cli.py:43` |
| `--min-video-duration` | `4.0` (0 desactiva) | `cli.py:44` |
| `--max-asset-uses` | `3` (0 desactiva) | `cli.py:45` |
| `--seed` | `0` | `cli.py:46` |
| `--focus` | `face` | `cli.py:47` |
| `--crf` | `18` | `cli.py:40` |
| `--preset` | `medium` | `cli.py:41` |
| `--mix-video-audio` | activado | `cli.py:48` |
| `--video-audio-volume` | `1.0` | `cli.py:49` |
| `--background-audio-volume` | `0.15` | `cli.py:50` |
| `--width` / `--height` / `--fps` | `1080` / `1920` / `30` | `cli.py:37-39` |
| `--audio-start` / `--audio-end` | `"0"` / `None` | `cli.py:54-55` |
| `--skip-render` | `False` | `cli.py:57` |

## Por qué existe cada flag

**`--beats-per-cut 2`**: cortes más rápidos. Sustituye el uso de `--manual-bpm`, rechazado porque fuerza cambios en una rejilla uniforme aunque no haya cambio real de beat: con `manual_bpm` truthy, `analyze_audio` devuelve `method="manual-bpm"` y `onset_strength` vacío, así que `build_timeline` cae a `_cut_points` uniforme en vez de la rejilla adaptativa. Ojo: `--manual-bpm 0` es falsy y se ignora en silencio.

**`--max-image-duration`**: solo IMÁGENES. Un slot largo (p. ej. una intro hablada que dejaba una foto congelada ~26 s) se parte en piezas, y cada pieza la ocupa una **foto distinta**, no la misma re-animada. La fórmula real acota el número de piezas: `parts = min(ceil(span / max_image_duration), max(1, int(span / 0.35)))` (`timeline.py:216-221`): el segundo término evita astillas sub-0.35 s, y cuando muerde, el máximo pedido se supera a propósito.

**`--min-video-duration`**: solo VIDEOS. Un video de fuente corta (<2 s) crecía como un flash; ahora absorbe los beats siguientes hasta `min(start + min_video_duration, song_end)`, y el sobrante se re-encola como clip propio solo si mide ≥0.35 s (`timeline.py:198-204`). Es best-effort: si la canción se acaba, el clip queda más corto.

**`--max-asset-uses 3`**: tope de apariciones **por imagen**. Los videos están exentos: cada reaparición lleva `TimelineItem.fragment` incremental, y el renderer hace `-ss` a un fragmento distinto (`_fragment_offset`, `renderer.py:534`), así que un video repetido nunca muestra el mismo momento. Invariantes adicionales de `build_timeline`: el mismo asset nunca sale dos veces seguidas, y si las imágenes se agotan bajo el cap, el cap se relaja (con aviso a `stderr`) en vez de congelar una foto.

**`--seed`**: el orden es una bolsa re-barajada por pasada (`random.Random(seed)`). La primera ronda **no** se baraja: conserva el orden smart/mood para la apertura (`timeline.py:171`). Cambiar el seed re-baraja el mismo material; se añadió porque con pocos assets el round-robin fijo repetía la misma secuencia en bucle.

**`--focus center` / `face`**: `center` y `face` restringen los efectos a `["zoom_in", "zoom_out"]` y eliminan los paneos (`timeline.py:125-128`), que fue lo que hizo que una foto paneara hacia un fondo vacío. `face` centra el crop en la cara detectada; necesita `opencv-python-headless`, que está comentado en `requirements.txt`; sin él degrada a crop centrado con un aviso único por proceso. **`face` solo afecta a imágenes**: `_render_video_clip` fija crop centrado.

**Calidad (`--crf` / `--preset`)**: los renders se veían degradados porque el código antiguo usaba `-preset veryfast` sin CRF (default 23 de ffmpeg) en **dos generaciones** de encode. Hoy los intermedios por clip son casi sin pérdida (`_INTERMEDIATE_CRF = 12`, `_INTERMEDIATE_PRESET = "veryfast"`, `renderer.py:32-33`) y la única compresión significativa es el encode final, gobernado por `--crf 18` / `--preset medium`.

## Aviso práctico: los renders son lentos

Un edit típico son 100+ clips, cada uno con su propio encode: varios minutos por render. **Conviene fijar los flags antes de renderizar.** Para iterar sobre el timeline sin pagar el encode:

```bash
python3 -m synced_edit.cli --project-folder ~/proyectos/demo --skip-render
```

Deja el `_timeline.json` y el `_audio_analysis.json` inspeccionables. Dos detalles reales de `--skip-render`: igual escribe el reporte Markdown y también imprime `Video: <ruta>` apuntando a un `.mp4` que no existe (`cli.py:171-176`).

## Caso con rango de audio

```bash
python3 -m synced_edit.cli \
  --project-folder ~/proyectos/demo \
  --audio-start 00:17 \
  --audio-end 02:03
```

El rango recorta el audio a `work/trimmed_audio.wav` antes de analizarlo (`cli.py:86-88`), así que **todos los tiempos del análisis son relativos al recorte**; el original solo sobrevive como metadato en `source_audio_path` / `source_audio_start` / `source_audio_end`.

Salidas que produce ese comando, según `_resolve_project_defaults` y `_range_suffix` (`cli.py:235-257`), con `range_suffix = "_00-17_02-03"`:

```
output/demo_00-17_02-03.mp4
output/demo_00-17_02-03_timeline.json
output/demo_00-17_02-03_audio_analysis.json
output/reporte_<YYYY-MM-DD>_demo.md     ← SIN range_suffix
```

El material recortado atraviesa el subsistema de crossfades, que arrastraba una desincronización acumulativa: ver [[20-Proyectos/34-video-sync/34.05-render-y-sincronizacion/fix-desincronizacion-xfade|Fix desincronizacion xfade]].

## Gotchas verificados

- **`--mood bittersweet` no puntúa por mood.** `MOOD_TAGS` (`asset_selection.py:10-21`) tiene exactamente 10 claves: `sad, calm, happy, dramatic, intense, melancholic, mysterious, euphoric, nostalgic, gentle`. `"bittersweet"` **no está**, así que `MOOD_TAGS.get("bittersweet", set())` devuelve un set vacío: el término `len(tags & mood_tags) * 4` es 0, y ninguna de las reglas condicionales por mood (`asset_selection.py:106,112-125`) matchea. Con el default solo aplican los bonus incondicionales: `flower` +2.5, `plant`/`nature` +1.5, `calm` +1.0, `bright` +0.6, `motion` en video +1.2, video +0.4. Un mood desconocido no falla, degrada en silencio. Para puntuar por emoción hay que pasar un mood del vocabulario (p. ej. `--mood melancholic`) o `--mood ""` para usar la emoción auto-detectada.
- **El reporte se pisa entre rangos.** `--output` / `--timeline` / `--analysis` llevan `range_suffix`, pero el reporte no (`cli.py:248`): dos rangos distintos del mismo proyecto **el mismo día** escriben el mismo `.md`.
- **`--audio-start 00:00` ≠ `--audio-start 0`.** El set de "inicio cero" es literal y cerrado: `{"0", "0.0", "0:00"}` (`cli.py:253`). `00:00` no matchea, genera sufijo `_00-00_end` y escribe a **archivos distintos**.
- **`--mood` siempre gana a la auto-detección.** `effective_mood = args.mood or (detected if not no_auto_emotion else None)` (`cli.py:111`); como el default `"bittersweet"` es truthy, la emoción detectada solo se usa con `--mood ""`.
- **El clasificador corre antes de validar los timecodes.** `_ensure_assets_metadata` (que puede gastar llamadas a la API) se ejecuta en `cli.py:81`, y `parse_timecode` en `cli.py:82-83`: un `--audio-start` inválido aborta con traceback **después** de haber pagado.
- **`work/` es relativo al CWD** y `work/trimmed_audio.wav` tiene nombre fijo: dos renders en paralelo desde el mismo directorio colisionan.
- **`--focus face` es el default pero su dependencia está comentada** en `requirements.txt`: una instalación limpia con `pip install -r requirements.txt` degrada a `center` con un único aviso a stderr (no falla el import).

## Clasificación incremental

`assets.json` se rellena, no se reconstruye: al re-ejecutar, solo los assets nuevos van al modelo. Una carpeta completamente clasificada **no necesita API key ni red** (`classifier.py:395-401` retorna antes de exigirla). Para re-etiquetar desde cero: `python3 -m synced_edit.classifier <carpeta> --force`. Un video del que ffmpeg no logre extraer frames se persiste como `[]` y **nunca se reintenta** en modo incremental.

## Enlaces

- [[20-Proyectos/34-video-sync/34.07-uso-y-flags/index|34.07 Uso y flags]]
- [[20-Proyectos/34-video-sync/34.06-clasificacion-y-seleccion/index|34.06 Clasificacion y seleccion]]
- [[20-Proyectos/34-video-sync/34.05-render-y-sincronizacion/fix-desincronizacion-xfade|Fix desincronizacion xfade]]
- [[20-Proyectos/34-video-sync/index|Video Sync]]
