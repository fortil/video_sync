---
tema: arquitectura
tipo: nota
proyecto: video-sync
creada: 2026-07-02
actualizada: 2026-07-02
estado: activa
aliases: [Arquitectura, Pipeline y módulos, Dataclasses del pipeline]
tags: [video-sync, arquitectura, pipeline, modulos, dataclasses]
---

# 34.02 Arquitectura

Fuente: `AGENTS.md`, `requirements.txt` y el paquete `synced_edit/` del repo.

El proyecto es un pipeline lineal de un solo proceso: una canción local y unas carpetas de medios entran por `cli.py`, y salen tres artefactos JSON/Markdown más un `.mp4`. No hay servidor, ni cola, ni estado persistente entre corridas salvo `assets.json` (los tags del clasificador, que cuestan dinero y por eso se cachean en disco).

## La regla que gobierna la estructura

`AGENTS.md:4`, literal:

> Keep beat detection, timeline generation, and rendering as separate modules.

Se cumple, y no de forma nominal. Verificado por lectura:

| Módulo | ¿Ejecuta ffmpeg? | ¿Lee el contenido de los medios? | ¿Decide tiempos musicales? |
|---|---|---|---|
| `audio_analysis.py` | sí (`trim_audio`, decodificación a WAV, `ffprobe`) | sí, el audio | sí: `beats[]`, `duration`, `sections[]` |
| `timeline.py` | **no**: cero `subprocess`, cero filtergraphs | **no**, solo `suffix` y `iterdir()` | sí: `cut_points`, asignación de assets |
| `renderer.py` | sí, es lo único que hace | sí, para renderizar | **no**: recibe el cronograma y lo obedece |

La regla complementaria (`AGENTS.md:3`) es *"Use Python for media planning and FFmpeg for final rendering"*: el planning vive entero en memoria y se materializa en `timeline.json`; el render es un traductor de ese JSON a comandos ffmpeg. Corolario práctico: un `timeline.json` se puede editar a mano y re-renderizar, y es exactamente lo que promete el reporte (`report.py:58`).

**Divergencia real con `AGENTS.md:7-10`:** la regla exige `outputs/timeline.json` + `outputs/final.mp4`. Solo se cumple en el modo sin `--project-folder` (`cli.py:230-232`). Con `--project-folder`, el CLI escribe en `<project>/output/`: carpeta **singular**: con nombres derivados del proyecto (`cli.py:236,245-248`). Además `--skip-render` (`cli.py:57,159`) termina sin producir `.mp4`. Ver [[20-Proyectos/34-video-sync/34.07-uso-y-flags/index|34.07 Uso y flags]].

## Diagrama del pipeline

```
                          argparse (cli.py:18-78)
                                   │
                    _resolve_project_defaults()  cli.py:80
                                   │
                    _ensure_assets_metadata()    cli.py:81  ──► classifier.classify_folder()
                                   │                              │  (solo si hay --project-folder
                                   │                              │   y falta algún asset en el JSON)
                                   │                              ▼
                                   │                         assets.json  ◄── OpenAI /v1/responses
                                   │                              │            (urllib, no SDK)
                    parse_timecode(--audio-start/-end)  cli.py:82-83
                                   │            timecode.py
                                   ▼
              ┌────────  audio_start or audio_end is not None ?  ────────┐
              │ sí                                                    no │
              ▼                                                          │
    trim_audio()  audio_analysis.py:130                                  │
    ffmpeg -y -ss S -i song.mp3 -t D -vn -acodec pcm_s16le               │
              │                                                          │
              ▼                                                          │
    work/trimmed_audio.wav  (cli.py:87, ruta HARDCODEADA, relativa al CWD)
              │                                                          │
              └───────────────────────┬──────────────────────────────────┘
                                      ▼
                    analyze_audio(path, manual_bpm)   cli.py:90
                    audio_analysis.py:33
                      librosa │ ffmpeg-energy │ manual-bpm
                                      │
                                      ▼
                              AudioAnalysis  ── mutado por el CLI (cli.py:91-93):
                                      │        source_audio_path/_start/_end
                                      ▼
                    detect_emotion(analysis)          cli.py:96-97
                    audio_analysis.py:54  ──► (emoción, confianza)
                                      │        efectivo: cli.py:111
                                      ├──────────────────────────►  write_analysis()
                                      │                             audio_analysis.py:125
                                      │                                    │
                                      │                                    ▼
                                      │                          audio_analysis.json
                                      ▼
    collect_assets(paths)   timeline.py:56   ──►  list[Path] absolutos, ordenados
                                      │
    load_asset_tags(project_folder)   asset_selection.py:32  ◄── assets.json
                                      │
    select_assets(assets, mood, selection, metadata, project_folder)   cli.py:119
    asset_selection.py:47   ──► ranking por score + interleave 2 img / 1 vid
                                      │
                                      ▼
                    build_timeline(analysis, assets, ...)   cli.py:126
                    timeline.py:71   ──► cut_points ──► segments ──► items
                                      │  (sin ffmpeg, sin I/O de medios)
                                      ▼
                                  Timeline  ── enriquecido por el CLI:
                                      │        timeline.audio.update(...)  cli.py:140-149
                                      │        timeline.selection = {...}  cli.py:150-156
                                      ├──────────────────────────►  write_timeline()
                                      │                             timeline.py:306
                                      │                                    │
                                      │                                    ▼
                                      │                              timeline.json
                                      ▼
                    render_timeline(timeline, output, ...)   cli.py:160
                    renderer.py:36   [omitido con --skip-render]
                          │
                          ├─ _render_clip() × N          ──► clip_NNNN.mp4   (tmp)
                          ├─ xfade filtergraph | concat  ──► silent.mp4      (tmp)
                          ├─ _warn_if_duration_drifted() ──► stderr si |drift| > 0.15 s
                          └─ _mux_audio()                ──► final .mp4
                                      │
                                      ▼
                    write_report(path, timeline, output)     cli.py:172
                    report.py:9   ──► reporte_YYYY-MM-DD_*.md
```

Los cuatro `print` de stdout del CLI salen al final (`cli.py:174-177`): `Analysis:`, `Timeline:`, `Video:`, `Report:`. Todo lo demás del CLI (progreso, avisos, degradaciones) va a **stderr**; la única otra línea de stdout del pipeline es el resumen final del clasificador (`classifier.py:449`).

### Artefactos

| Artefacto | Quién lo escribe | Ruta por defecto (sin `--project-folder`) |
|---|---|---|
| `assets.json` | `classifier._write_metadata` (`:455`) | `<project>/assets.json`: **solo existe con `--project-folder`** |
| `work/trimmed_audio.wav` | `trim_audio` (`audio_analysis.py:130`) | `work/trimmed_audio.wav`, hardcodeado (`cli.py:87`) |
| `audio_analysis.json` | `write_analysis` (`audio_analysis.py:125`) | `outputs/audio_analysis.json` (`cli.py:232`) |
| `timeline.json` | `write_timeline` (`timeline.py:306`) | `outputs/timeline.json` (`cli.py:231`) |
| `final.mp4` | `render_timeline` (`renderer.py:36`) | `outputs/final.mp4` (`cli.py:230`) |
| `reporte_<fecha>_synced_edit.md` | `write_report` (`report.py:9`) | `outputs/` (`cli.py:171`) |

Los tres JSON se escriben con el mismo patrón: `parent.mkdir(parents=True, exist_ok=True)` + `write_text(json.dumps(..., indent=2) + "\n", encoding="utf-8")`. **Ninguno es atómico** (sin tmp+rename): una interrupción a media escritura deja el archivo truncado.

Los intermedios del render (`clip_NNNN.mp4`, `concat.txt`, `silent.mp4`, `bed*.wav`) viven en un `tempfile.TemporaryDirectory(dir=work_dir)` (`renderer.py:55`) y se borran al salir del `with`, incluso ante excepción.

## Tabla de módulos

Líneas medidas con `wc -l` sobre el código del repo.

| Módulo | Líneas | Responsabilidad |
|---|---:|---|
| `synced_edit/cli.py` | 261 | Único orquestador. Define los 32 flags, resuelve rutas por proyecto (`_resolve_project_defaults`), dispara el clasificador (`_ensure_assets_metadata`), encadena las 8 llamadas del pipeline y enriquece `Timeline.audio`/`Timeline.selection` con metadatos que ningún otro módulo produce (`effective_mood`, `mode`, `metadata_file`). Es el único que importa a todos los demás. |
| `synced_edit/timecode.py` | 38 | `parse_timecode(value) -> float \| None`. Único símbolo público. Cero dependencias (ni stdlib más allá de `from __future__`). Acepta `"150"`, `"MM:SS"`, `"HH:MM:SS"`, `int`/`float`. |
| `synced_edit/audio_analysis.py` | 390 | Beat detection y cronograma. `AudioAnalysis`, `analyze_audio`, `detect_emotion`, `trim_audio`, `probe_duration`, `write_analysis`. Produce `beats[]`, `onset_strength[]`, `sections[]`, `duration`, `bpm`. Ver [[20-Proyectos/34-video-sync/34.03-analisis-de-audio/index|34.03 Analisis de audio]]. |
| `synced_edit/timeline.py` | 418 | Planificación. `TimelineItem`, `Timeline`, `collect_assets`, `build_timeline`, `write_timeline`, `load_timeline`. Convierte beats en `cut_points`, los cut points en `segments` y asigna assets a cada slot. Además es la **única** fuente de `transition_hint`. Ver [[20-Proyectos/34-video-sync/34.04-timeline-y-cortes/index|34.04 Timeline y cortes]]. |
| `synced_edit/renderer.py` | 635 | Traducción del `Timeline` a ffmpeg: un clip por item, ensamblado (xfade o concat), auditoría de deriva y mux de audio. Única dependencia opcional del render (`cv2`). Ver [[20-Proyectos/34-video-sync/34.05-render-y-sincronizacion/index|34.05 Render y sincronizacion]]. |
| `synced_edit/classifier.py` | 489 | Etiquetado de medios con un modelo de visión. `classify_folder`, `DEFAULT_TAGS` (24 tags), extracción de frames con ffmpeg, HTTP a `POST https://api.openai.com/v1/responses` por `urllib`. Incremental por defecto y persistente tras **cada** asset. Ejecutable standalone (`python3 -m synced_edit.classifier`). |
| `synced_edit/asset_selection.py` | 156 | Ranking. `MOOD_TAGS` (10 moods), `AssetProfile`, `load_asset_tags`, `select_assets`, `_interleave_types`. Traduce mood + tags a un orden de assets. Determinista: no importa `random`. Ver [[20-Proyectos/34-video-sync/34.06-clasificacion-y-seleccion/index|34.06 Clasificacion y seleccion]]. |
| `synced_edit/report.py` | 60 | `write_report(path, timeline, output_video, author="Codex")`. Único consumidor del `Timeline` para Markdown. No lee disco, solo escribe. |

Fuera de esos ocho: `synced_edit/__init__.py` (4 líneas: docstring + `__version__ = "0.1.0"`; **no re-exporta nada**, sin `__all__`) y `src/synced_edit.py` (6 líneas: `from synced_edit.cli import main`).

**Gotcha de invocación:** `src/synced_edit.py` comparte nombre con el paquete `synced_edit/`. Al ejecutar `python src/synced_edit.py`, Python antepone `src/` a `sys.path` y `import synced_edit` puede resolver al propio script. No hay `pyproject.toml` ni `setup.py`, así que el paquete no es instalable. La invocación fiable es desde la raíz del repo:

```bash
python3 -m synced_edit.cli --project-folder /ruta/al/proyecto
python3 -m synced_edit.classifier /ruta/al/proyecto
python3 -m pytest tests/ -q      # `pytest` pelado falla: no hay conftest.py
```

## Las tres dataclasses

Son el contrato entre módulos. Ninguna es `frozen`, ninguna valida nada, ninguna usa `slots`.

### `AudioAnalysis`: `audio_analysis.py:14-30`

```python
@dataclass
class AudioAnalysis:
    audio_path: str
    duration: float
    bpm: float
    beats: list[float]
    method: str
    source_audio_path: str | None = None
    source_audio_start: float | None = None
    source_audio_end: float | None = None
    onset_strength: list[float] = field(default_factory=list)
    sections: list[dict] = field(default_factory=list)
    detected_emotion: str = ""
    emotion_confidence: float = 0.0

    def to_json(self) -> dict:
        return asdict(self)
```

Los cinco primeros son obligatorios. Es mutable **a propósito**: `audio_analysis.py` nunca escribe `source_audio_*` (los pone `cli.py:91-93`) ni `detected_emotion`/`emotion_confidence` (los pone `cli.py:97`). `method` toma exactamente tres valores: `"manual-bpm"` (`:45`), `"librosa"` (`:192`), `"ffmpeg-energy"` (`:242`). Cada `dict` de `sections` tiene las claves `start`, `end`, `energy_level ∈ {"low","medium","high"}`.

### `TimelineItem`: `timeline.py:18-31`

```python
@dataclass
class TimelineItem:
    index: int
    source: str
    source_type: str
    start: float
    end: float
    duration: float
    effect: str
    transition_hint: str = "cut"
    # For videos reused more than once: which appearance this is (0, 1, 2, ...).
    # The renderer seeks to a different fragment of the source per appearance so a
    # repeated video never shows the same moment twice. Always 0 for images.
    fragment: int = 0
```

Solo dos campos tienen default: `transition_hint = "cut"` y `fragment = 0`. `source` es **`str`**, no `Path` (`_make_item` hace `str(source)`, `timeline.py:295`). Valores reales por campo:

- `index`: siempre `len(items)` en construcción → invariante `items[i].index == i`, denso y 0-based. El renderer nombra los intermedios `clip_{item.index:04d}.mp4` (`renderer.py:61`), así que depende de esa unicidad.
- `source_type`: `"video"` (`:207`) o `"image"` (`:226`, `:235`). Se decide con `path.suffix.lower() in IMAGE_EXTENSIONS`; **todo lo que no sea imagen es video**, incluido un `.txt` que se colara.
- `effect`: los videos reciben siempre el literal `"fit"` (`:207`) y nunca pasan por `_zoompan_filter`. Las imágenes rotan en round-robin sobre `["zoom_in","zoom_out"]` si `focus in ("center","face")`, o sobre esos dos más `["pan_left","pan_right"]` en caso contrario (`:123-128`).
- `transition_hint`: `"xfade"` **si y solo si** el `start` del item cae en una sección con `energy_level == "low"` (`_transition_hint_for`, `:324-328`); cualquier otro caso, `"cut"`.
- `fragment`: 0, 1, 2… por reaparición del mismo `str(path)` de video (`:205-210`). Siempre 0 para imágenes.

### `Timeline`: `timeline.py:34-53`

```python
@dataclass
class Timeline:
    audio: dict
    width: int
    height: int
    fps: int
    items: list[TimelineItem]
    selection: dict | None = None
    focus: str = "dynamic"

    def to_json(self) -> dict:
        return {
            "audio": self.audio,
            "width": self.width,
            "height": self.height,
            "fps": self.fps,
            "focus": self.focus,
            "selection": self.selection or {},
            "items": [asdict(item) for item in self.items],
        }
```

- `audio` es literalmente `analysis.to_json()` (`:257`), es decir `asdict(AudioAnalysis)`, **más** las seis claves que le inyecta el CLI (`cli.py:140-149`): cinco sobrescriben claves ya existentes con el mismo valor (los tres `source_audio_*` más `detected_emotion` y `emotion_confidence`, que también son campos de `AudioAnalysis`) y solo una es nueva: `effective_mood`, que **no existe** en `AudioAnalysis` y solo llega por esa vía.
- `selection` queda `None` tras `build_timeline` (no se pasa en `:256-263`); lo puebla el CLI en `cli.py:150-156`. `to_json` normaliza `None → {}`, así que el JSON nunca lleva `null` ahí.
- Aparte de `audio["audio_path"]` (que el mux lee en `renderer.py:124`), `focus` es lo único de `Timeline` que el renderer consulta fuera de `items`/`fps`/`width`/`height`, y solo para imágenes (`_crop_filter`, `renderer.py:563`).

**Asimetría de round-trip verificada:** `load_timeline` (`:311-321`) lee `selection` con default `{}`, no `None` → `load(write(t))` cambia `t.selection` de `None` a `{}`. El JSON sí es estable. `TimelineItem(**item)` es estricto: una clave extra o una obligatoria ausente lanza `TypeError`; `load_timeline` **no valida** contigüidad, ni `duration == end - start`, ni `index == i`.

## El contrato temporal

Es la invariante central del proyecto y la que el fix de sincronía tuvo que restaurar.

> **`item.start` y `item.end` son coordenadas de tiempo-canción**, absolutas, con origen en `0.0` del audio **analizado**: es decir, del recortado si hubo `--audio-start`/`--audio-end`. **Y `sum(item.duration for item in timeline.items)` es la duración de esa canción recortada.**

Cómo se garantiza, paso a paso:

1. `analyze_audio` mide el WAV que recibe. Si el CLI recortó (`cli.py:85-88`), ese WAV es `work/trimmed_audio.wav` → `analysis.duration` es la duración **recortada**, no la del `song.mp3` original. El original solo sobrevive como metadato (`source_audio_path/_start/_end`), que ninguna función usa para calcular nada.
2. `_land_on_duration` (`timeline.py:406-418`) fuerza `cut_points[-1] == analysis.duration`: si el último corte queda a menos de `min_gap` (0.35 s) del final, lo **desplaza** al final en vez de añadir una astilla. De ahí `song_end = cut_points[-1]` (`:97`).
3. Los `segments` son pares adyacentes de `cut_points` (`:98`) → cubren `[0.0, song_end]` sin huecos ni solapes.
4. Cada item nace de un segmento con `_make_item`, y los items son **contiguos por construcción**: `items[i].end == items[i+1].start` antes de redondear. El troceado de imágenes largas re-encola el resto (`work.appendleft((piece_end, end))`, `:230`) y el crecimiento de videos consume segmentos enteros (`:200-201`): ninguna de las dos operaciones abre un hueco.
5. Con `--max-items`, la truncación rompería la cobertura, así que el código **estira el último clip conservado** hasta `song_end` (`timeline.py:249-254`). El comentario dice por qué: *"otherwise `-shortest` would clip it"*.

El renderer audita exactamente esa suma (`renderer.py:256-273`):

```python
expected = sum(item.duration for item in timeline.items)
actual = _probe_duration(silent_video)
drift = actual - expected
if abs(drift) > _DURATION_DRIFT_TOLERANCE:   # 0.15 s
    print(f"Warning: rendered video duration ({actual:.2f}s) diverges from the "
          f"song-time schedule ({expected:.2f}s) by {drift:+.2f}s. Audio and "
          "video may be out of sync.", file=sys.stderr)
```

El docstring de esa función es la formulación canónica del contrato: *"Every TimelineItem.duration is a slice of song-time, and their sum is the length the video is supposed to end up."* Solo avisa: no aborta ni corrige.

### Consecuencias de diseño

- **El cronograma nunca se acorta.** `timeline.py` no reserva ni descuenta el 0.25 s del xfade: `duration` es duración musical pura y la constante `0.25` **no aparece** en el archivo. Toda la compensación del solape es responsabilidad del renderer, que padea el clip entrante. Ver [[20-Proyectos/34-video-sync/34.05-render-y-sincronizacion/fix-desincronizacion-xfade|Fix desincronizacion xfade]].
- **Los beats son absolutos, no acumulativos.** `beats[i]` no se deriva sumando duraciones de items. Por eso una pérdida en el vídeo renderizado es *deriva* contra un cronograma que no se movió, y no un reajuste mutuo.
- **`item.duration` no es exactamente `item.end - item.start`.** `_make_item` (`timeline.py:297-299`) redondea `start` y `end` a 4 decimales, pero calcula `duration = round(end - start, 4)` con los valores **sin redondear**. La discrepancia es de orden 1e-4 por item; sobre ~100 items, ≤10 ms: dos órdenes de magnitud por debajo de la tolerancia de 0.15 s, pero explica por qué la tolerancia no puede ser estricta.
- **Caso borde no defendido:** `cut_points` de un solo elemento → `segments == []` → `items == []` → suma 0, `Timeline` válido, sin error.

## Dependencias externas

`requirements.txt` declara cuatro paquetes. **Ninguna dependencia del pipeline de render es obligatoria por pip**; lo único indispensable son dos binarios del sistema.

| Dependencia | Declarada | ¿Obligatoria? | Fallback verificado |
|---|---|---|---|
| `ffmpeg` (binario) | no declarable por pip | **sí, de facto** | `render_timeline` avisa (`RuntimeError("ffmpeg is required to render the video")`, `renderer.py:46-47`); `_analyze_with_energy` también (`audio_analysis.py:199-200`). Pero **`trim_audio` no comprueba nada** y corre antes del render (`cli.py:88`) → sin ffmpeg y con `--audio-start`, sale un `FileNotFoundError` crudo de `subprocess`. |
| `ffprobe` (binario) | no declarable | **sí, de facto** | **Nadie lo verifica.** `_warn_if_duration_drifted` lo invoca en **todos** los renders (`renderer.py:265`), pese a que `render_timeline` solo comprobó `ffmpeg`. |
| `librosa>=0.10.2` | `requirements.txt:2` | no | `try: _analyze_with_librosa() / except Exception: _analyze_with_energy()` (`audio_analysis.py:48-51`). El `except` es **desnudo y mudo**: no distingue "no instalado" de "falló con este archivo". El único rastro es el campo `method`. Import diferido dentro de la función (`:163`). |
| `numpy>=1.26` | `requirements.txt:3` | no | **Ningún módulo la importa** (grep confirmado). Es transitiva de librosa. |
| `soundfile>=0.12` | `requirements.txt:4` | no | Idem: transitiva, backend de `librosa.load`. El fallback de energía decodifica con `ffmpeg` + `wave` + `struct` de la stdlib. |
| `certifi>=2024.2.2` | `requirements.txt:11` | no | `try: import certifi / except ImportError: certifi = None` (`classifier.py:40-43`) → `default_ssl_context()` cae a `ssl.create_default_context()` del sistema (`:76-79`). Si esa verificación falla, hay un `RuntimeError` con instrucciones. Solo afecta al clasificador. |
| `opencv` (`cv2`) | **COMENTADA** (`requirements.txt:16`) | no | `try: import cv2 / except ImportError: cv2 = None` (`renderer.py:11-14`) → `_detect_face_center` avisa **una sola vez** por proceso (`_FACE_WARNED`, `:16`) y `_crop_filter` cae a crop centrado. |
| SDK `openai` | **no existe** | — | El clasificador **no usa el SDK**: habla HTTP crudo con `urllib.request.urlopen(request, timeout=90, context=default_ssl_context())` contra `POST https://api.openai.com/v1/responses` (`classifier.py:286-297`). Sus únicos imports son stdlib + `certifi` opcional. |
| `OPENAI_API_KEY` (env) | `requirements.txt:10` lo documenta | condicional | Solo se exige si hay assets pendientes: `classify_folder` retorna antes (`:395-401`) si todo está ya en `assets.json`. Sin key y con pendientes → `RuntimeError("Missing OPENAI_API_KEY...")` (`:406-409`), que el CLI **sí** atrapa (`cli.py:218-223`) y continúa con los tags existentes. |
| `pytest` | **no declarada** | no | Existen `tests/test_renderer.py` y `tests/test_timeline.py`. No hay `requirements-dev.txt`. |

Trampas verificadas de esta tabla:

1. **`--focus face` es el default del CLI** (`cli.py:47`) pero su dependencia está comentada. Una instalación limpia con `pip install -r requirements.txt` degrada el modo por defecto a `center` con un solo aviso a stderr.
2. **Discrepancia de nombre de paquete:** el help de `--focus` dice *"needs opencv-python"* y el warning del renderer sugiere `pip install opencv-python`, pero `requirements.txt:15` recomienda `opencv-python-headless`. Ambos proveen `cv2`.
3. **El CLI solo atrapa `RuntimeError` del clasificador** (`cli.py:218`). Un fallo de red no-SSL se re-lanza crudo desde `classifier.py:309` → aborta el pipeline con traceback.
4. **Sin límites superiores** (`<`) en ninguna restricción: un major bump de librosa/numpy rompe sin aviso de pip. Y como el `except Exception` es mudo, rompería *en silencio*.
5. `_ensure_assets_metadata` (que **gasta llamadas a la API**) corre en `cli.py:81`, **antes** de `parse_timecode` (`:82`). Un `--audio-start "1:99"` aborta con traceback después de haber pagado la clasificación.

## Enlaces

- [[20-Proyectos/34-video-sync/index|Video Sync]]
- [[20-Proyectos/34-video-sync/34.01-vision-y-estado/index|34.01 Vision y estado]]
- [[20-Proyectos/34-video-sync/34.03-analisis-de-audio/index|34.03 Analisis de audio]]: `AudioAnalysis`, `beats[]`, `sections[]`, `detect_emotion`, `trim_audio`
- [[20-Proyectos/34-video-sync/34.04-timeline-y-cortes/index|34.04 Timeline y cortes]]: `cut_points`, asignación de assets, `_make_item`, `transition_hint`
- [[20-Proyectos/34-video-sync/34.05-render-y-sincronizacion/index|34.05 Render y sincronizacion]]: traducción a ffmpeg, xfade, auditoría de deriva, mux
- [[20-Proyectos/34-video-sync/34.06-clasificacion-y-seleccion/index|34.06 Clasificacion y seleccion]]: `assets.json`, `MOOD_TAGS`, scoring e interleave
- [[20-Proyectos/34-video-sync/34.07-uso-y-flags/index|34.07 Uso y flags]]: los 32 flags y las rutas de salida por modo
- [[20-Proyectos/34-video-sync/34.08-pruebas-y-verificacion/index|34.08 Pruebas y verificacion]]: cobertura real y el gotcha de `sys.path`
