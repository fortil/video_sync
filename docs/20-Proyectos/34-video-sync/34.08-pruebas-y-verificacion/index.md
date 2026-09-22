---
tema: pruebas y verificación
tipo: nota
proyecto: video-sync
creada: 2026-07-02
actualizada: 2026-07-02
estado: activa
aliases: [Pruebas y verificación, Tests de video-sync, Verificación de sincronía]
tags: [video-sync, tests, pytest, verificacion, xfade]
---

# 34.08 Pruebas y verificación

Fuente: `tests/test_timeline.py` y `tests/test_renderer.py` del repo.

La suite es deliberadamente pequeña: 5 tests, todos de lógica pura, sin FFmpeg ni media real. Cubre las dos zonas donde un error es silencioso e irrecuperable: la aritmética de los cortes y la aritmética del cronograma en el filtergraph de xfade.

## Qué existe hoy

| Archivo | Tests | Qué protege |
|---|---|---|
| `tests/test_timeline.py` | 3 | `build_timeline` con beats regulares, `parse_timecode`, selección `smart` por mood |
| `tests/test_renderer.py` | 2 | La aritmética de `_build_xfade_filtergraph` preserva el cronograma; casos borde de `_uses_incoming_xfade` |

`tests/test_timeline.py` no cambia desde el commit inicial `9648c07`. `tests/test_renderer.py` es nuevo y llegó **con** el fix de xfade: los tres commits previos (`e3438d2`, `cc7c84e`, `295ef0c`) se hicieron sin añadir un solo test.

## Cómo se corren

Desde la raíz del repo:

```bash
python3 -m pytest tests/ -q
# 5 passed in 0.10s
```

Resultado: **5 passed**.

**Usar `python3 -m pytest`, no `pytest` pelado.** El ejecutable falla:

```bash
pytest tests/ -q
# ModuleNotFoundError: No module named 'synced_edit'
# Interrupted: 2 errors during collection
```

Causa verificada: no existe `conftest.py`, `pytest.ini`, `pyproject.toml`, `setup.cfg` ni `tox.ini` en el repo. `python3 -m pytest` inyecta el CWD en `sys.path`; el ejecutable `pytest` no. Tampoco hay `pyproject.toml`/`setup.py`, así que el paquete no es instalable en modo editable para evitarlo.

`pytest` **no está declarado** en `requirements.txt` ni existe `requirements-dev.txt`. Hay que instalarlo aparte.

Ningún test necesita FFmpeg (aunque esté en el PATH del entorno).

## `tests/test_timeline.py`

### `test_build_timeline_from_regular_beats`

Construye un `AudioAnalysis` sintético (`duration=8.0`, `bpm=120.0`, `beats=[0.0, 0.5, ... 7.5]`, `method="test"`) y llama `build_timeline(analysis, [a.jpg, b.mp4], width=720, height=1280, fps=24, beats_per_cut=4)`.

Como `onset_strength` queda en su default `[]`, se toma la rama uniforme `_cut_points` en vez de la adaptativa. Traza verificada: `normalized[3::4]` → `[2.0, 4.0, 6.0]` → `cut_points = [0, 2, 4, 6, 8]` → 4 segmentos de 2 s.

Las aserciones `items[0].source_type == "image"` e `items[1].source_type == "video"` son deterministas por una regla concreta de `_pick`: **la primera ronda no se baraja** (`if rounds_done > 0: rng.shuffle(eligible)`, `timeline.py:171`), así que preserva el orden dado. Los items 2 y 3 sí pasan por `rng.shuffle` con `random.Random(0)` y el test no los asserta.

### `test_parse_timecode_seconds_and_clock_formats`

Los tres formatos de `parse_timecode`:

| Entrada | Salida | Fórmula |
|---|---|---|
| `"150"` | `150.0` | `float(text)` (sin `:`) |
| `"2:30"` | `150.0` | `minutes * 60 + seconds` |
| `"01:02:03.5"` | `3723.5` | `hours * 3600 + minutes * 60 + seconds` |

No cubre las validaciones (cadena vacía, negativos, `seconds >= 60`) ni el gotcha de que `parse_timecode("-5")` retorna `-5.0` sin error.

### `test_smart_selection_prioritizes_mood_tags`

`select_assets([a.jpg, b.jpg], mood="sad", selection="smart", metadata={"images/a.jpg": {"bright"}, "images/b.jpg": {"flower","calm"}}, project_folder=Path("/project"))` y afirma `selected[0].name == "b.jpg"`.

Aritmética del scoring, recalculada contra `asset_selection.py:94-109` con `MOOD_TAGS["sad"] = {calm, cool, flower, plant, closeup, nature, melancholic, gentle, nostalgic}`:

| Asset | Tags | `len(∩) * 4` | Bonus | Total |
|---|---|---|---|---|
| `b.jpg` | `{flower, calm}` | `2 * 4 = 8.0` | `+2.5` flower, `+1.0` calm | **11.5** |
| `a.jpg` | `{bright}` | `0` | `+0.6` bright | **0.6** |

El test también ejercita de paso `_tags_for_asset`, que resuelve la clave probando `[asset.name, str(asset), relative_to(project_folder)]`: acierta con el tercer candidato (`images/b.jpg`), que es exactamente la forma de clave que escribe el clasificador.

## `tests/test_renderer.py`

### `test_xfade_filtergraph_preserves_schedule_duration`

Fixture: 4 items de duraciones `[3.0, 2.0, 2.5, 1.8]` con hints `[cut, xfade, xfade, xfade]`, `fps=30`. Tres aserciones:

```python
assert offsets == [2.75, 4.75]
assert "concat=n=2:v=1:a=0" in filter_complex
assert filter_complex.count("xfade=transition=fade") == 2
```

El item 3 lleva hint `"xfade"` a propósito: por ser el último **nunca** transiciona (regla `index < total_items - 1` de `_uses_incoming_xfade`, que existe para no cortar el audio de cola). El test blinda ese borde.

### Por qué NO necesita FFmpeg

`_build_xfade_filtergraph` **solo compone un string**: no abre archivos, no lanza subprocesos, no lee los `clip_paths` (de hecho el parámetro ni se usa dentro de la función; solo `len(items)` gobierna las etiquetas). Las rutas del fixture (`/tmp/clip_0000.mp4`) son ficticias y nunca se tocan.

El único punto que sí invocaría FFmpeg es `_check_xfade_support()`, que corre `ffmpeg -filters` y busca la subcadena `"xfade"`. Se cortocircuita con:

```python
monkeypatch.setattr(renderer, "_XFADE_AVAILABLE", True)
```

`_check_xfade_support` retorna el global cacheado sin sondear si no es `None` (`renderer.py:346-347`). `monkeypatch` restaura `None` al terminar, así que no contamina otros tests. El test verifica **aritmética pura y texto**, no comportamiento de FFmpeg.

Corolario honesto: la identidad `duración(xfade) = offset + duración(B)` sobre la que descansa el fix es semántica documentada de FFmpeg y **no la verifica ningún test**. El test protege el cálculo, no la premisa.

### Por qué el fixture necesita ≥2 items con xfade

Con **un solo** xfade el test no detecta nada. La aritmética del bug y la del fix divergen solo a partir del segundo:

| Paso | Fix (hoy) | Bug (`cumulative += item.duration - _XFADE_DURATION`) |
|---|---|---|
| `cumulative` inicial | `3.0` | `3.0` |
| i=1 → `offset` | `max(0, 3.0-0.25)` = **2.75** | `max(0, 3.0-0.25)` = **2.75** ← idéntico |
| i=1 → `cumulative` | `+= 2.0` → `5.0` | `+= 2.0-0.25` → `4.75` |
| i=2 → `offset` | `max(0, 5.0-0.25)` = **4.75** | `max(0, 4.75-0.25)` = **4.50** ← delata |

El primer offset es idéntico con y sin el bug porque `cumulative` arranca en `items[0].duration` y todavía no acumuló ninguna resta. La regresión solo se manifiesta cuando un xfade **hereda** el `cumulative` de otro xfade anterior. De ahí que el fixture lleve items 1 y 2 ambos elegibles: `[2.75, 4.75]` pasa, `[2.75, 4.50]` falla.

El delta por transición es exactamente `_XFADE_DURATION = 0.25` s, acumulativo: N xfades encadenados producían `N * 0.25` s de deriva contra el cronograma de la canción.

### `test_uses_incoming_xfade_boundary_conditions`

Una aserción por cada término del `and` de `_uses_incoming_xfade`, con `total = 4`:

| Caso | Resultado | Término que decide |
|---|---|---|
| índice 0, hint `xfade` | `False` | `index > 0` |
| índice 3 (último), hint `xfade` | `False` | `index < total_items - 1` |
| `duration=0.4`, hint `xfade` | `False` | `item.duration >= _XFADE_DURATION * 2` → `0.4 >= 0.5` |
| índice 1, hint `cut` | `False` | `transition_hint == "xfade"` |
| índice 1, `duration=3.0`, hint `xfade` | `True` | todos se cumplen |

Umbral exacto: `_XFADE_DURATION * 2 = 0.5` s, comparación `>=` → 0.5 exacto **sí** califica.

Este test importa porque `_uses_incoming_xfade` es la **única fuente de verdad** de la condición, consumida por los tres puntos que antes la duplicaban inline: `render_timeline` (decide el padding del clip), el guard `needs_xfade` y el bucle `use_xfade` del filtergraph. Si los tres divergieran, se renderizarían clips con padding que acaban en la rama concat sin xfade (o al revés). El test congela la definición.

## Verificación end-to-end manual

No hay test automatizado de render real. La comprobación es manual, en dos niveles.

### 1. Aviso automático de deriva

`_warn_if_duration_drifted` (`renderer.py:256-273`) corre en **cada** render, después de producir `silent.mp4` y antes del mux: cubre por igual la rama xfade y la rama concat:

```python
expected = sum(item.duration for item in timeline.items)
actual = _probe_duration(silent_video)
drift = actual - expected
if abs(drift) > _DURATION_DRIFT_TOLERANCE:   # 0.15 s
    print(f"Warning: rendered video duration ({actual:.2f}s) diverges from the "
          f"song-time schedule ({expected:.2f}s) by {drift:+.2f}s. Audio and "
          "video may be out of sync.", file=sys.stderr)
```

Solo advierte a stderr: no aborta ni corrige. La tolerancia `_DURATION_DRIFT_TOLERANCE = 0.15` s ≈ 4.5 frames a 30 fps: absorbe la cuantización a frame entero de los clips (medida: ~0.03 s, y **no** escala con el número de clips porque los errores de ±½ frame se cancelan) y aun así detecta una sola regresión de xfade (`0.25 > 0.15`).

### 2. `ffprobe` contra la duración del audio recortado

Contrastar la duración de la salida contra la del audio que realmente se analizó (el recortado, si hubo `--audio-start`/`--audio-end`):

```bash
PROJ=~/proyectos/demo

# duracion del video final
ffprobe -v error -show_entries format=duration \
  -of default=noprint_wrappers=1:nokey=1 "$PROJ/output/demo.mp4"

# duracion del audio analizado (el recortado vive en work/, relativo al CWD)
ffprobe -v error -show_entries format=duration \
  -of default=noprint_wrappers=1:nokey=1 work/trimmed_audio.wav

# el cronograma programado, desde el timeline
python3 -c "import json,sys; t=json.load(open(sys.argv[1])); \
print(sum(i['duration'] for i in t['items']), t['audio']['duration'])" \
  "$PROJ/output/demo_timeline.json"
```

Las tres magnitudes deben coincidir dentro del error de cuantización. Ojo con dos matices verificados:

- `timeline.audio["duration"]` es la duración del audio **analizado**, no la del `.mp4` ni la del original. Con `--max-items`, `build_timeline` estira el último clip hasta `song_end`, así que `Σ item.duration` sigue cubriendo la canción pero los items ya no reflejan la rejilla original.
- Sin recorte no existe `work/trimmed_audio.wav`: el análisis corre sobre el archivo original y hay que probar ese.

El reporte Markdown **no sirve** para esta verificación: publica `audio.duration` pero no la suma real ni el drift, y su línea "Crossfade transitions" cuenta por `transition_hint` sin aplicar los filtros reales del renderer, así que sobreestima (ver [[20-Proyectos/34-video-sync/34.05-render-y-sincronizacion/index|34.05 Render y sincronizacion]]).

## Huecos de cobertura (honestos)

| Hueco | Consecuencia |
|---|---|
| **No hay tests end-to-end de render real** | Nada ejercita `render_timeline` contra media. Los tests de renderer solo componen strings; ni `_render_image_clip`, ni `_render_video_clip`, ni `_mux_audio`, ni el ensamblado se ejecutan nunca |
| **No hay tests del clasificador** | `classifier.py` (489 líneas, llamadas HTTP a OpenAI, extracción de frames con FFmpeg, metadata incremental) sin un solo test. `normalize_tags`, `extract_json_object` y `load_existing_metadata` son lógica pura testeable sin red |
| **No hay tests del bed de audio** | `_build_video_audio_bed`, `_mix_chunk_to_bed` y el posicionamiento `adelay = int(round(it.start * 1000))` no se verifican. Es donde vive el desfase intra-clip conocido entre imagen y audio propio con `fragment != 0` |
| **No hay `tests/test_audio_analysis.py`** | `detect_emotion`, `_compute_sections`, `_estimate_bpm` y `_pick_energy_peaks` sin cobertura, pese a ser aritmética pura. `test_timeline.py` solo importa `AudioAnalysis` como fixture |
| **Nada cubre `cli.py` ni `report.py`** | `_range_suffix`, `_resolve_project_defaults` y el conteo de xfades del reporte son puros y testeables |
| **`_fragment_offset` sin test** | Incluyendo el parámetro `extra_head` que el fix añadió para el lead-in de los videos |
| **`_xfade_enabled` sin test** | El rango `2 <= len(items) <= 50` no está congelado por ninguna aserción |
| **La premisa de FFmpeg no se verifica** | `duración(xfade) = offset + duración(B)` se asume; el test valida el cálculo que descansa sobre ella |

Ninguno de estos huecos bloquea el fix, pero sí explica por qué la verificación de sincronía sigue dependiendo de `_warn_if_duration_drifted` en runtime en vez de un test.

## Notas relacionadas

- [[20-Proyectos/34-video-sync/34.05-render-y-sincronizacion/fix-desincronizacion-xfade|Fix desincronizacion xfade]]: el bug, su identidad algebraica y la medición empírica que estos tests congelan.
- [[20-Proyectos/34-video-sync/34.05-render-y-sincronizacion/index|34.05 Render y sincronizacion]]: `_uses_incoming_xfade`, el padding por clip y `_warn_if_duration_drifted` en su contexto.
- [[20-Proyectos/34-video-sync/34.04-timeline-y-cortes/index|34.04 Timeline y cortes]]: la rejilla de cortes que verifica `test_build_timeline_from_regular_beats`.
- [[20-Proyectos/34-video-sync/34.06-clasificacion-y-seleccion/index|34.06 Clasificacion y seleccion]]: el scoring que verifica `test_smart_selection_prioritizes_mood_tags`.
- [[20-Proyectos/34-video-sync/34.01-vision-y-estado/index|34.01 Vision y estado]]: estado del fix y deuda del proyecto.
- [[20-Proyectos/34-video-sync/index|Video Sync]]: índice del proyecto.
