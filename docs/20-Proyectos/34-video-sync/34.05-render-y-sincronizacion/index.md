---
tema: render y sincronización
tipo: índice
proyecto: video-sync
creada: 2026-07-02
actualizada: 2026-07-02
estado: activa
aliases: [Render y sincronización, 34.05 Render]
tags: [video-sync, render, ffmpeg, xfade]
---

# 34.05 Render y sincronización

Fuente: `synced_edit/renderer.py` del repo.

Este bloque cubre la última etapa del pipeline: convertir el `Timeline` (ver [[20-Proyectos/34-video-sync/34.04-timeline-y-cortes/index|34.04 Timeline y cortes]]) en un `.mp4` con audio. El renderer no decide nada musical: recibe un cronograma en tiempo-canción y su único deber es no deformarlo.

## Notas del bloque

- [[20-Proyectos/34-video-sync/34.05-render-y-sincronizacion/fix-desincronizacion-xfade|Fix desincronizacion xfade]]: la deriva acumulativa de 0.25 s por transición y su corrección.

## Flujo de `render_timeline()`

Firma real (`renderer.py:36-45`): `render_timeline(timeline, output_path, work_dir=None, mix_video_audio=False, video_audio_volume=1.0, background_audio_volume=0.15, crf=18, preset="medium")`.

```
render_timeline(timeline, output_path, work_dir=Path("work"), ...)
  │
  ├─ shutil.which("ffmpeg") ──► RuntimeError("ffmpeg is required to render the video")
  │                             (ffprobe NO se verifica, pese a ser obligatorio)
  ├─ output_path.parent.mkdir(parents=True, exist_ok=True)
  ├─ work_dir.mkdir(parents=True, exist_ok=True)
  │
  └─ with TemporaryDirectory(dir=work_dir) as tmp:      ◄── autolimpiado al salir
       │
       ├─ xfade_on = _xfade_enabled(items)              ◄── UNA vez, antes del bucle
       │
       ├─ POR CLIP ────────────────────────────────────────────────────┐
       │    incoming_xfade = xfade_on and _uses_incoming_xfade(item,…) │
       │    _render_clip(...) ─► tmp/clip_{item.index:04d}.mp4         │ CRF 12 / veryfast
       │                                                              ─┘
       ├─ ENSAMBLADO ─► tmp/silent.mp4                                   CRF --crf / --preset
       │    xfade_on ? _build_xfade_filtergraph(...) : concat demuxer
       │    (si el filtergraph devuelve None ─► concat)
       │
       ├─ _warn_if_duration_drifted(silent.mp4, timeline)                solo avisa
       │
       └─ MUX ─► output_path                                             -c:v copy
            _mux_song_only  |  _mux_audio (bed + canción)
```

El work dir temporal se crea con `tempfile.TemporaryDirectory(dir=str(base_work_dir))` (`renderer.py:55`). Todo lo intermedio (`clip_*.mp4`, `concat.txt`, `silent.mp4`, `aud_*.wav`, `bed*.wav`) vive dentro y desaparece al salir del `with`, incluso ante excepción. Lo único que sobrevive fuera es `output_path`. El CLI pasa `work_dir=Path("work")`, hardcodeado y relativo al CWD (`cli.py:163`).

`audio_path = timeline.audio["audio_path"]` (`renderer.py:124`) es acceso por clave dura: `KeyError` si el JSON no lo trae.

## Calidad en dos generaciones

```python
_INTERMEDIATE_CRF = 12        # renderer.py:32
_INTERMEDIATE_PRESET = "veryfast"
```

Los clips por ítem se codifican **near-lossless** y el encode del ensamblado es el que define la calidad real (`--crf 18`, `--preset medium` por defecto en el CLI). El motivo, literal en el comentario (`renderer.py:28-31`): así la **única compresión significativa** es la final, evitando la pérdida visible de encadenar dos generaciones lossy. Los temporales pesan más en disco, pero viven dentro del work dir autolimpiado.

| Generación | CRF | Preset | Dónde |
|---|---|---|---|
| Clips intermedios | `12` (fijo) | `veryfast` (fijo) | `_render_image_clip` / `_render_video_clip` |
| Ensamblado → `silent.mp4` | `--crf` (18) | `--preset` (medium) | rama xfade y rama concat |
| Mux final | — | — | `-c:v copy` (no re-encodea) |

`--crf`/`--preset` no tocan los intermedios: bajar `--crf` mejora el único encode que importa.

## Render por clip

`_render_clip` (`renderer.py:440`) despacha por `item.source_type`: `"image"` → `_render_image_clip`; **cualquier otro valor** → `_render_video_clip`. Sin validación.

### Imágenes: `loop 1` + `zoompan`

```
ffmpeg -y -loop 1 -i <source> -t <render_duration:.4f> \
  -vf "scale=W:H:force_original_aspect_ratio=increase,<crop>,<zoompan>,format=yuv420p" \
  -r <fps> -an -c:v libx264 -preset veryfast -crf 12 clip_NNNN.mp4
```

- `render_duration = item.duration + (0.25 if incoming_xfade else 0.0)` (`:462`)
- `frames = max(1, int(round(render_duration * timeline.fps)))` (`:463`)

El padding en imágenes es **puramente formulaico** (comentario `:457-461`): el zoompan corre 0.25 s más, sin discontinuidad visual.

### Vídeos: `-stream_loop -1` + `-ss` por fragmento

```
ffmpeg -y [-ss <offset:.4f>] -stream_loop -1 -i <source> -t <render_duration:.4f> \
  -vf "scale=W:H:force_original_aspect_ratio=increase,crop=W:H,fps=FPS,format=yuv420p" \
  -an -c:v libx264 -preset veryfast -crf 12 clip_NNNN.mp4
```

- `extra_head = 0.25 if incoming_xfade else 0.0`; `render_duration = item.duration + extra_head` (`:510-511`)
- `-ss` va **antes de `-i`** (input seeking) y solo se emite `if offset > 0` (`:518-519`)
- `-stream_loop -1` hace que un vídeo más corto que el slot dé la vuelta en lugar de cortarse

Diferencia de criterio con las imágenes (comentario `:506-509`): para metraje real el padding se toma como **lead-in ANTES del inicio nominal**, restándolo al seek, para que la transición mezcle contenido contiguo y sin salto.

El `crop` de los vídeos está **hardcodeado a centro**: `--focus` no les aplica.

### `_fragment_offset()`

```python
def _fragment_offset(source, item, extra_head=0.0) -> float:   # renderer.py:534
    fragment = getattr(item, "fragment", 0)
    if not fragment:
        return 0.0
    source_duration = _probe_duration(source)
    if source_duration <= item.duration:
        return 0.0
    capacity = max(1, int(source_duration / item.duration))
    base = (fragment % capacity) * (source_duration / capacity)
    return round(max(0.0, base - extra_head), 4)
```

`capacity = floor(source_duration / item.duration)` = cuántos fragmentos no solapados de la longitud del clip caben en la fuente. `fragment % capacity` mapea la aparición a uno de ellos, y `* (source_duration / capacity)` los reparte **uniformemente**: las primeras `capacity` apariciones caen en momentos distintos y equiespaciados. El docstring (`:537-542`) lo contrasta con el `fragment * clip % source` anterior, que producía colisiones modulares arbitrarias.

`extra_head` desplaza el seek hacia atrás, clampeado con `max(0.0, ...)` cuando el fragmento arranca demasiado cerca del inicio de la fuente. `item.fragment` lo asigna `build_timeline` (0, 1, 2… por reaparición del mismo `str(path)`); las imágenes siempre llevan 0.

Casos en los que retorna `0.0` **ignorando `extra_head`**: `fragment == 0` (primera aparición), `fragment % capacity == 0`, o fuente no más larga que el clip. En esos casos el clip padded arranca en `fuente[0]` y sus primeros 0.25 s se consumen en la mezcla: la duración total sigue siendo correcta, pero el contenido a opacidad plena se desplaza 0.25 s dentro del clip.

Cada llamada con `fragment != 0` dispara un `ffprobe` (sin caché).

## Encuadre y efectos (solo imágenes)

### `--focus dynamic|center|face`

`_crop_filter` (`renderer.py:563`) solo consulta caras cuando `timeline.focus == "face"`:

```python
x_expr = f"clip({nx:.4f}*iw-{width}/2,0,iw-{width})"
y_expr = f"clip({ny:.4f}*ih-{height}/2,0,ih-{height})"
return f"crop={width}:{height}:x='{x_expr}':y='{y_expr}'"
```

Cualquier otro modo (y todo fallo) cae a `crop={width}:{height}` centrado. `iw/ih` son las dimensiones **ya escaladas**; el centro normalizado es invariante a escala porque el escalado es uniforme (comentario `:576-577`).

`_detect_face_center` (`renderer.py:584`) con OpenCV **opcional** (`try: import cv2 / except ImportError: cv2 = None`, `:11-14`):

| Parámetro | Valor |
|---|---|
| Cascada | `haarcascade_frontalface_default.xml` |
| `scaleFactor` | `1.1` |
| `minNeighbors` | `5` |
| `minSize` | `(40, 40)` |
| Selección | cara de **mayor área** |
| Retorno | `((x + w/2) / iw, (y + h/2) / ih)` normalizado |

Devuelve `None` (→ crop centrado) si falta OpenCV, si `cv2.imread` falla o si no hay caras. El aviso de fallback se imprime **una sola vez por proceso** (latch global `_FACE_WARNED`, `:16`). Se reconstruye el `CascadeClassifier` en cada llamada, y la llamada es **por clip**, no por asset: una foto usada 3 veces corre Haar 3 veces.

`--focus face` es el default del CLI, pero `opencv-python-headless` está **comentado** en `requirements.txt`: una instalación limpia degrada a `center` silenciosamente (salvo por el aviso único).

### `_zoompan_filter()`

```
zoompan=z='<z>':x='<x>':y='<y>':d=<frames>:s=<W>x<H>:fps=<fps>
```

| `effect` | `z` | `x` |
|---|---|---|
| `zoom_out` | `if(lte(on,1),1.12,max(1.0,zoom-0.0015))` | centrado |
| `zoom_in` | `min(zoom+0.0015,1.12)` | centrado |
| `pan_left` | `min(zoom+0.0015,1.12)` | `iw/2-(iw/zoom/2)-on*2` |
| `pan_right` | `min(zoom+0.0015,1.12)` | `iw/2-(iw/zoom/2)+on*2` |

Paso de zoom **±0.0015 por frame**, tope `1.12`, piso `1.0`; pan **±2 px por frame de salida**. Derivada: `(1.12 − 1.0) / 0.0015 = 80` frames ≈ **2.67 s a 30 fps** para completar el recorrido; después el zoom se satura. Con `--max-image-duration 4` (default) hay ~1.33 s de imagen congelada al final de cada slot largo.

El `else` es catch-all: cualquier `effect` desconocido usa la expresión de zoom-in centrado. `build_timeline` restringe los efectos a `["zoom_in","zoom_out"]` cuando `focus in ("center","face")`; los vídeos reciben `"fit"` pero nunca pasan por este filtro.

## Ensamblado

### `_check_xfade_support()`

```python
result = subprocess.run(["ffmpeg", "-filters"], capture_output=True, text=True)
_XFADE_AVAILABLE = "xfade" in result.stdout
```

Sin `check=True`: un ffmpeg que falle da stdout vacío → `False` → fallback a concat, no excepción. Cachea en el global de módulo `_XFADE_AVAILABLE` (tri-estado `None`/`True`/`False`), **nunca invalidado**: en un proceso largo el sondeo se congela tras la primera llamada. Matchea por subcadena (`xfade_opencl` también daría `True`).

### Elegibilidad

```python
def _uses_incoming_xfade(item, index, total_items) -> bool:   # renderer.py:357
    return (
        index > 0
        and getattr(item, "transition_hint", "cut") == "xfade"
        and item.duration >= _XFADE_DURATION * 2      # >= 0.5 s
        and index < total_items - 1
    )

def _xfade_enabled(items) -> bool:                            # renderer.py:369
    return 2 <= len(items) <= 50 and _check_xfade_support()
```

`_uses_incoming_xfade` describe al clip **entrante ("clip B")**, no al saliente. El último clip se excluye a propósito *to avoid audio cutoff* (comentario `:358-360`). El `transition_hint` lo produce `_transition_hint_for` en `timeline.py`: `"xfade"` solo si el `start` del ítem cae en una sección con `energy_level == "low"`.

El tope de **50 clips** (rango cerrado `2 <= len <= 50`) está justificado en el código (`:371-372`): el filter graph de ffmpeg se vuelve inestable con 50+ entradas y filtros anidados → fallback a concat plano. Con `len(items)` fuera de rango ni siquiera se invoca ffmpeg (cortocircuito).

| Condición | Ensamblado | Padding |
|---|---|---|
| `len(items) < 2` o `> 50` | concat demuxer | ninguno |
| `ffmpeg -filters` sin `xfade` | concat demuxer | ninguno |
| `xfade_on` pero ningún ítem elegible | concat demuxer | ninguno |
| `xfade_on` y ≥1 ítem elegible | `filter_complex` | +0.25 s solo a los elegibles |

`xfade_on` gobierna a la vez el padding por clip y el filtergraph, así que **nunca** se da el caso "clips padded que acaban en concat".

### `_build_xfade_filtergraph()`: la aritmética corregida

Normalización de **cada** entrada antes de encadenar:

```
[{i}:v]fps={fps},settb=1/{fps},format=yuv420p,setsar=1[n{i:04d}]
```

Por qué (comentario `:392-398`): los clips de imagen (zoompan/loop) y los de vídeo acaban con **timebases distintos**, y `xfade` falla con *"input link timebases do not match"*. Se fuerza fps, timebase, SAR y pixel format comunes. Y se usa `settb=1/fps` **y no AVTB (1/1000000)** porque el filtro `concat` resetea su timebase de salida; volver a AVTB frente a una entrada xfade derivada de fps reabriría el crash. Por eso cada `concat` y cada `xfade` llevan un `,settb=1/{fps}` de re-sellado.

```python
prev_label = norm_labels[0]
cumulative = items[0].duration                 # renderer.py:407-408

for i in range(1, len(items)):
    if use_xfade:
        offset = max(0.0, cumulative - _XFADE_DURATION)
        filters.append(
            f"[{prev_label}][{norm_labels[i]}]"
            f"xfade=transition=fade:duration={_XFADE_DURATION}:offset={offset:.4f}"
            f",settb=1/{fps}[{next_label}]"
        )
        cumulative += item.duration            # :426  ← SIN restar
    else:
        filters.append(
            f"[{prev_label}][{norm_labels[i]}]concat=n=2:v=1:a=0,settb=1/{fps}[{next_label}]"
        )
        cumulative += item.duration            # :433
    prev_label = next_label

return ";".join(filters), prev_label
```

`cumulative` es la suma verdadera de tiempo-canción y **no se le resta nada**. La identidad que lo sostiene: `xfade` produce `len_out = offset + len(B)`, y B se renderizó con `item.duration + 0.25`, así que

```
len_out = (cumulative_prev − 0.25) + (dur_B + 0.25) = cumulative_prev + dur_B = cumulative_nuevo
```

El solape lo paga el padding, no el cronograma. Detalle completo del bug anterior y su derivación en [[20-Proyectos/34-video-sync/34.05-render-y-sincronizacion/fix-desincronizacion-xfade|Fix desincronizacion xfade]].

`_build_xfade_filtergraph` recibe `clip_paths` pero **no lo usa**: la correspondencia entre el orden de los `-i` y el orden de `items` es implícita.

### Los dos comandos de ensamblado

```bash
# Rama xfade: un -i por clip
ffmpeg -y -i clip_0000.mp4 -i clip_0001.mp4 … \
  -filter_complex "<FC>" -map "[<out_label>]" \
  -c:v libx264 -preset medium -crf 18 -pix_fmt yuv420p -an silent.mp4

# Rama concat: concat.txt con líneas "file '<ruta posix>'"
ffmpeg -y -f concat -safe 0 -i concat.txt \
  -vf format=yuv420p,setsar=1 \
  -c:v libx264 -preset medium -crf 18 -pix_fmt yuv420p -an silent.mp4
```

El fallback concat **re-encodea en vez de `-c copy`** (comentario `:92-95`): los clips por ítem difieren en timebase/SAR, lo que rompía el concat demuxer con stream copy o producía timestamps corruptos.

## Mux de audio

`_mux_audio` (`renderer.py:137`) cortocircuita a `_mux_song_only` si `not mix_video_audio or video_audio_volume <= 0 or not video_items`, o si ningún vídeo tiene pista de audio.

```bash
# _mux_song_only: la canción es el único audio
ffmpeg -y -i silent.mp4 -i <song> -map 0:v:0 -map 1:a:0 \
  -c:v copy -c:a aac -shortest -movflags +faststart <output>
```

Nótese que `_mux_song_only` **no lleva `-b:a`** (bitrate AAC por defecto de ffmpeg), mientras la rama mezclada usa `192k`. `-shortest` recorta al más corto: si `silent.mp4` acaba antes que la canción, el audio se corta ahí: de ahí la regla de que el último clip nunca hace xfade.

### El bed de audio (`--mix-video-audio`, on por defecto en el CLI)

Con `--mix-video-audio` (default `True` en el CLI, `False` en la firma de librería), el audio propio de cada clip de vídeo se coloca en su posición de timeline sobre una **cama silenciosa de longitud completa** y se mezcla **por encima** de la canción, que queda de fondo a `--background-audio-volume 0.15`.

Extracción por clip (previo filtro `_has_audio_stream`):

```bash
ffmpeg -y -stream_loop -1 -i <src> -t <it.duration:.4f> -vn \
  -ar 48000 -ac 2 -c:a pcm_s16le aud_NNNN.wav
```

`_build_video_audio_bed(auds, total, tmp_path, batch=24)`: con ≤24 clips, una sola pasada a `bed.wav`; con más, lotes de 24 → `bed_{start:04d}.wav` y una segunda pasada que los suma con `amix`. El racional es explícito (`:284-286`): mismo criterio *robustez sobre astucia* que el tope de 50 clips del xfade. Cada cama de lote ya es de longitud completa y está posicionada, así que la segunda pasada solo suma.

`_mix_chunk_to_bed` construye la base y coloca cada wav:

```bash
ffmpeg -y -f lavfi -t <total:.4f> -i anullsrc=channel_layout=stereo:sample_rate=48000 \
       -i aud_0001.wav -i aud_0002.wav … \
  -filter_complex "[1:a]aresample=async=1:first_pts=0,adelay=<ms>:all=1[a1];…;\
                   [0:a][a1][a2]…amix=inputs=<N+1>:normalize=0:dropout_transition=0[bed]" \
  -map "[bed]" -t <total:.4f> -c:a pcm_s16le bed.wav
```

Posicionamiento exacto (`:328`): `delay = max(0, int(round(it.start * 1000)))`: el `start` en tiempo-canción, en milisegundos, con piso 0. `adelay=<ms>:all=1` aplica el retardo a todos los canales. La entrada `0:a` es el `anullsrc` de longitud `total` (= `_probe_duration(silent.mp4)`, el vídeo real, no la suma del cronograma).

**Por qué la base full-length evita el ducking** (docstring `:147-157`): con ella, *todo* `amix` ve entradas de longitud completa, así que no puede hacer ducking/pumping: la interferencia que se oía en el intento anterior. Un `amix` cuyas entradas terminan antes reparte ganancia dinámicamente al perderlas; con `dropout_transition=0` y `normalize=0` sobre entradas full-length, la suma es estática.

Mezcla final:

```bash
ffmpeg -y -i silent.mp4 -i bed.wav -i <song> \
  -filter_complex "[1:a]aformat=…,volume=1.0[vid];\
                   [2:a]aformat=…,volume=0.15[song];\
                   [vid][song]amix=inputs=2:normalize=0:dropout_transition=0,alimiter=limit=0.95[mix]" \
  -map 0:v:0 -map "[mix]" -c:v copy -c:a aac -b:a 192k \
  -shortest -movflags +faststart <output>
```

`normalize=0` mantiene el audio de vídeo a nivel pleno; `alimiter=limit=0.95` solo captura picos raros y no bombea (comentario `:191-192`). `-c:v copy`: el mux no re-encodea vídeo.

> **Comentario desactualizado.** `renderer.py:164-167` afirma que `_render_video_clip` usa `-stream_loop -1 -i src -t duration` *with NO `-ss`*. Dejó de ser cierto: hoy emite `-ss` vía `_fragment_offset` (`:516-519`), mientras la extracción de audio sigue sin `-ss` y con `-t it.duration`. Para vídeos con `fragment != 0` y `--mix-video-audio`, la imagen arranca en `fuente[offset]` pero su audio en `fuente[0]` → desfase intra-clip igual al offset. **No afecta** al camino por defecto de librería ni a la sincronía global vídeo↔canción.

## Red de seguridad: `_warn_if_duration_drifted()`

```python
_DURATION_DRIFT_TOLERANCE = 0.15                       # renderer.py:26

expected = sum(item.duration for item in timeline.items)
actual = _probe_duration(silent_video)
drift = actual - expected
if abs(drift) > _DURATION_DRIFT_TOLERANCE:
    print(f"Warning: rendered video duration ({actual:.2f}s) diverges from the "
          f"song-time schedule ({expected:.2f}s) by {drift:+.2f}s. Audio and "
          "video may be out of sync.", file=sys.stderr)
```

Se invoca en `renderer.py:122`, después de producir `silent.mp4` y antes del mux, así que cubre por igual la rama xfade y la rama concat. **Solo advierte**: no lanza, no corrige, no aborta.

El docstring (`:257-262`) explica su razón de ser: cada `TimelineItem.duration` es una porción de tiempo-canción y su suma es la longitud que el vídeo debe tener; si un bug de render la encoge o la estira, audio y vídeo se separan en reproducción **sin ningún otro síntoma visible**. De ahí que exista el chequeo.

La tolerancia de `0.15` s (≈4.5 frames a 30 fps) está calibrada para absorber la cuantización a frame entero de los clips —que no se acumula, porque los errores de ±½ frame se cancelan— y aun así detectar **una sola** regresión de xfade (`0.25 > 0.15`). Ejecuta `ffprobe` incondicionalmente en cada render, pese a que `render_timeline` solo verificó `ffmpeg`.

## Notas

- La fuente de la verdad es el código de este repo; esta nota lo documenta.
- El cronograma no se negocia: `item.start` / `item.duration` son tiempo-canción puro y `timeline.py` **no reserva margen** para los 0.25 s del xfade. Toda la compensación vive en el renderer. Ver [[20-Proyectos/34-video-sync/34.04-timeline-y-cortes/index|34.04 Timeline y cortes]].
- El detalle del bug de deriva acumulativa, su derivación algebraica y la calibración de la tolerancia están en [[20-Proyectos/34-video-sync/34.05-render-y-sincronizacion/fix-desincronizacion-xfade|Fix desincronizacion xfade]].
- Estados globales de proceso: `_XFADE_AVAILABLE` (caché del sondeo) y `_FACE_WARNED` (latch del aviso). En renders sucesivos in-process no se re-sondea ni se re-avisa.
- `clip_{item.index:04d}.mp4` asume `item.index` único. `build_timeline` lo garantiza (`index=len(items)`), pero un `timeline.json` editado a mano con índices repetidos sobrescribiría clips en silencio.
- Índice del proyecto: [[20-Proyectos/34-video-sync/index|Video Sync]].
