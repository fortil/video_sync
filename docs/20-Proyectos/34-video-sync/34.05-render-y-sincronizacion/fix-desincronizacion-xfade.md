---
tema: desincronización xfade
tipo: nota
proyecto: video-sync
creada: 2026-07-02
actualizada: 2026-09-22
estado: activa
aliases: [Fix desincronizacion xfade, Deriva acumulada xfade, Postmortem 2026-07-02]
tags: [video-sync, renderer, xfade, postmortem, ffmpeg]
---

# Fix de desincronización xfade

Fuente: `synced_edit/renderer.py` del repo.

Postmortem del bug de deriva audio/vídeo corregido el 2026-07-02. El fix está en `main` (`synced_edit/renderer.py` más `tests/test_renderer.py`), commit «Fix cumulative audio/video desync from crossfade transitions».

## Síntoma

Se detectó en el render `output/demo_00-17_02-03.mp4` (con `--audio-start 00:17 --audio-end 02:03`) que el sonido se desincronizaba alrededor del minuto **1:04**. Reproducible en todas las corridas: `--seed 0` fija la selección de assets, así que el timeline resultante es idéntico entre ejecuciones.

Nada más fallaba: sin errores de ffmpeg, sin avisos, sin clips faltantes. La única evidencia era el desfase audible.

## Causa raíz

`xfade` de ffmpeg **superpone** `_XFADE_DURATION = 0.25` s del clip A con el clip B (`renderer.py:21`). Dos clips de longitud `len(A)` y `len(B)` encadenados con un xfade producen `len(A) + len(B) - 0.25`, no la suma.

El código anterior compensaba esa pérdida en el acumulador:

```python
# ANTES (synced_edit/renderer.py, rama use_xfade)
cumulative += item.duration - _XFADE_DURATION
```

Ese `- _XFADE_DURATION` era **correcto para calcular el offset del siguiente xfade**: el acumulador coincidía con la longitud real del vídeo producido hasta ese punto, así que cada `offset = cumulative - 0.25` caía donde debía y el filtergraph nunca fallaba. El vídeo era consistente **consigo mismo**.

Lo que faltaba: **nada reponía esos 0.25 s contra el cronograma de tiempo-de-canción**. `item.start` e `item.duration` son rebanadas de tiempo de la canción, y su suma es la duración del audio recortado: `timeline.py` no reserva ni descuenta margen alguno para el xfade. Cada xfade adelantaba acumulativamente **todo el contenido visual posterior** respecto de la música.

```
Cronograma de canción (item.start / item.duration): NUNCA cambió
  0         3.0             5.0                  7.5          9.3
  |----A----|-------B-------|----------C---------|-----D------|

ANTES: B y C se renderizaban a su duración pelada; el solape se robaba del cronograma
  |----A--[><]----B------[><]---------C----------|-----D------|
                                                        salida = 8.8  (9.3 - 2*0.25)
                                                        deriva  = -0.50 s

HOY: B y C se renderizan +0.25 s; el solape come el padding, no el cronograma
  |----A--[==]----B------[==]---------C----------|-----D------|
                                                        salida = 9.3
                                                        deriva  = 0.00 s
```

## Amplificador: `--mix-video-audio`

`--mix-video-audio` está **ON por defecto** en el CLI (`cli.py:48`, `BooleanOptionalAction, default=True`): nótese que el default de librería de `render_timeline` es el opuesto (`mix_video_audio: bool = False`, `renderer.py:40`).

Con la mezcla activa, `_mix_chunk_to_bed` coloca el audio propio de cada clip sobre una cama silenciosa usando el cronograma **original sin comprimir** (`renderer.py:328`):

```python
delay = max(0, int(round(it.start * 1000)))
# [{k}:a]aresample=async=1:first_pts=0,adelay={delay}:all=1[a{k}]
```

Vídeo comprimido (perdiendo 0.25 s por xfade) + audio de cada clip anclado en su `item.start` intacto = divergencia creciente entre lo que se ve y lo que se oye. El bug existía igual sin la mezcla (la canción también quedaba desfasada), pero la mezcla lo hace mucho más audible: el sonido ambiente de un clip suena cuando su imagen ya pasó.

## Evidencia dura

Del `timeline.json` anterior del render afectado:

| Métrica | Valor |
|---|---|
| Items del timeline | 40 |
| Duración programada (`sum(item.duration)`) | 106.0 s |
| Rango de audio | `00:17`–`02:03` → 106 s |
| Items con `transition_hint = xfade` | 15 |
| De ellos, elegibles según `_uses_incoming_xfade` | 13 |
| Deriva acumulada máxima | 13 × 0.25 = **3.25 s** |
| Punto de canción donde se completa | ≈ segundo 59 |

Con 40 items, `_xfade_enabled` pasa el rango `2 <= len(items) <= 50` (`renderer.py:373`), así que el filtergraph xfade sí se usaba: la deriva estaba plenamente activa. Los 3.25 s ya acumulados hacia el segundo ~59 explican que el desfase se perciba con claridad hacia 1:04.

## Por qué se disparaba en un punto concreto

`_transition_hint_for(start_time, sections)` (`timeline.py:324-328`) asigna `"xfade"` **si y solo si** el `start` del item cae en una sección con `energy_level == "low"`; cualquier otro valor da `"cut"`. Las secciones de energía las calcula `_compute_sections` por terciles relativos sobre ventanas de 8 beats, y luego fusiona adyacentes del mismo nivel.

Consecuencia: los xfade **se agrupan en tramos concretos de la canción** (los pasajes suaves), no se reparten uniformemente. La deriva por tanto **salta por escalones** de 0.25 s dentro de esos tramos y se queda plana fuera de ellos. No crece suave, y por eso el usuario podía señalar un punto donde "de repente" se nota, en vez de percibir una degradación gradual desde el principio.

Un agravante estructural: un slot largo de imagen troceado por `--max-image-duration` dentro de un pasaje de baja energía genera **varios xfade consecutivos**, cada uno con su propio coste de 0.25 s.

## La corrección

Cuatro cambios, todos en `synced_edit/renderer.py`.

**1. Extraer la condición a una única fuente de verdad.** Antes vivía inline y duplicada en dos sitios (el guard `needs_xfade` y el bucle `use_xfade`), con el `i < len(items) - 1` presente solo en uno de ellos:

```python
def _uses_incoming_xfade(item: TimelineItem, index: int, total_items: int) -> bool:
    return (
        index > 0
        and getattr(item, "transition_hint", "cut") == "xfade"
        and item.duration >= _XFADE_DURATION * 2
        and index < total_items - 1
    )


def _xfade_enabled(items: list[TimelineItem]) -> bool:
    return 2 <= len(items) <= 50 and _check_xfade_support()
```

Ahora la consumen los tres puntos que deben coincidir: `render_timeline` (`renderer.py:60`), el guard `needs_xfade` (`renderer.py:384-386`) y el bucle del filtergraph (`renderer.py:413`).

**2. El clip entrante se renderiza `item.duration + _XFADE_DURATION`.**

```python
# _render_image_clip (renderer.py:462): puramente formulaico
render_duration = item.duration + (_XFADE_DURATION if incoming_xfade else 0.0)
frames = max(1, int(round(render_duration * timeline.fps)))

# _render_video_clip (renderer.py:510-516): el padding se toma como lead-in
extra_head = _XFADE_DURATION if incoming_xfade else 0.0
render_duration = item.duration + extra_head
offset = _fragment_offset(source, item, extra_head)
```

**3. `_fragment_offset(source, item, extra_head)` corre el seek 0.25 s antes**, con clamp a `>= 0`, para que el relleno sea metraje contiguo real y no un salto:

```python
capacity = max(1, int(source_duration / item.duration))
base = (fragment % capacity) * (source_duration / capacity)
return round(max(0.0, base - extra_head), 4)
```

**4. `cumulative` deja de restar.**

```python
if use_xfade:
    offset = max(0.0, cumulative - _XFADE_DURATION)
    filters.append(
        f"[{prev_label}][{norm_labels[i]}]"
        f"xfade=transition=fade:duration={_XFADE_DURATION}:offset={offset:.4f}"
        f",settb=1/{fps}[{next_label}]"
    )
    cumulative += item.duration   # sin restar
```

La identidad que lo cierra: `xfade` produce `offset + len(B)`; con B padded a `dur_B + 0.25`,

```
len_out = (cumulative_prev - 0.25) + (dur_B + 0.25) = cumulative_prev + dur_B = cumulative_nuevo
```

El solape lo paga el padding, no el cronograma.

## Traza aritmética

Items `[3.0, 2.0(xfade), 2.5(xfade), 1.8]`, `fps=30`. El item 3 lleva hint `xfade` pero es el último, así que cae a `concat`.

| i | `duration` | ¿xfade? | `render_duration` | `cumulative` antes | `offset` | `cumulative` después | Longitud real de ffmpeg |
|---|---|---|---|---|---|---|---|
| 0 | 3.0 | no (índice 0) | 3.0 | — | — | 3.0 | 3.0 |
| 1 | 2.0 | sí | **2.25** | 3.0 | **2.75** | 5.0 | 2.75 + 2.25 = **5.0** |
| 2 | 2.5 | sí | **2.75** | 5.0 | **4.75** | 7.5 | 4.75 + 2.75 = **7.5** |
| 3 | 1.8 | no (último) | 1.8 | 7.5 |: (concat) | 9.3 | 7.5 + 1.8 = **9.3** |

`cumulative` coincide con la longitud real de salida **en cada paso**, y el total (9.3) coincide con `sum(item.duration)`. Con el código anterior el mismo timeline daba **8.8 = 9.3 − 2×0.25**.

## Por qué NO hubo que tocar el audio

`item.start` nunca estuvo mal. El cronograma que produce `timeline.py` son tiempos **absolutos** de la canción, no acumulados de duraciones de clip: `cut_points[0] == 0.0`, `cut_points[-1] == analysis.duration`, y los items son contiguos por construcción. Nada de eso se acortó jamás: solo el vídeo renderizado se había desviado.

Al realinear el vídeo con el cronograma, la cama de audio (que ya colocaba cada clip en `delay = item.start`) cae en su sitio sola. Cero cambios en `_mux_audio`, `_build_video_audio_bed` o `_mix_chunk_to_bed`.

## Red de seguridad añadida

`_warn_if_duration_drifted(silent_video, timeline)` (`renderer.py:256`), invocado en `renderer.py:122` **después** de producir `silent.mp4` y **antes** de `_mux_audio`: cubre por igual la rama xfade y la rama concat:

```python
expected = sum(item.duration for item in timeline.items)
actual = _probe_duration(silent_video)
drift = actual - expected
if abs(drift) > _DURATION_DRIFT_TOLERANCE:   # 0.15 s
    print(
        f"Warning: rendered video duration ({actual:.2f}s) diverges from the "
        f"song-time schedule ({expected:.2f}s) by {drift:+.2f}s. Audio and "
        "video may be out of sync.",
        file=sys.stderr,
    )
```

Solo advierte: no aborta ni corrige. La tolerancia de **0.15 s** (`renderer.py:26`) ≈ 4.5 frames a 30 fps: absorbe la cuantización a frame entero de los clips (que no se acumula linealmente, los errores de ±½ frame se cancelan) y aun así **detecta una sola regresión de xfade**, porque 0.25 > 0.15.

Más `tests/test_renderer.py`, primer archivo de tests añadido desde el commit inicial, que entra en el mismo commit que el fix:

| Test | Qué blinda |
|---|---|
| `test_xfade_filtergraph_preserves_schedule_duration` | `offsets == [2.75, 4.75]`; el último item cae a `concat=n=2:v=1:a=0`; exactamente 2 `xfade=transition=fade` |
| `test_uses_incoming_xfade_boundary_conditions` | Los 4 términos de la condición, uno por aserción negativa, más un quinto caso elegible que devuelve `True` |

Usa `monkeypatch.setattr(renderer, "_XFADE_AVAILABLE", True)` para cortocircuitar el sondeo `ffmpeg -filters`: el test no toca disco ni ffmpeg, solo compone strings.

## Verificación

```bash
# Ojo: 'pytest' pelado falla con ModuleNotFoundError (no hay conftest.py ni pyproject.toml).
python3 -m pytest tests/ -q
# 5 passed

python3 -m synced_edit.cli --project-folder ~/proyectos/demo --audio-start 00:17 --audio-end 02:03
ffprobe -v error -show_entries format=duration -of default=noprint_wrappers=1:nokey=1 \
  output/demo_00-17_02-03.mp4
# 105.966667   (esperado 106.0 → deriva −0.03 s, dentro de tolerancia)
```

Re-render real del proyecto de ejemplo `demo`: **105.966667 s** contra **106.0 s** esperados, sin aviso de deriva. El residuo de ~0.03 s es cuantización a frame entero (1 frame a 30 fps), no deriva de xfade.

## Casos borde documentados

| Caso | Comportamiento |
|---|---|
| Primer clip (`index == 0`) | Nunca hace xfade: no tiene predecesor con quien mezclar |
| Último clip (`index == total-1`) | Nunca hace xfade: evita cortar la cola del audio (`-shortest`) |
| `item.duration < 2 * _XFADE_DURATION` (0.5 s) | No elegible; `>= 0.5` exacto **sí** califica |
| `len(items) < 2` o `> 50` | `_xfade_enabled` es `False` → concat plano, **ningún** clip se extiende |
| ffmpeg sin filtro `xfade` | Ídem: `_check_xfade_support()` da `False` → concat plano |
| Hints `xfade` pero ninguno elegible | `_build_xfade_filtergraph` devuelve `None` → concat. Coherente: si ninguno califica, ninguno se padea |
| Fuente no más larga que el clip (`source_duration <= item.duration`) | `-stream_loop -1` cubre el tail; `_fragment_offset` retorna `0.0` |
| `fragment == 0` (primera aparición) | `_fragment_offset` retorna `0.0` **antes** de aplicar `extra_head` → el lead-in no se puede tomar y los primeros 0.25 s de la fuente se consumen en el blend. **No afecta la duración total** ni la sincronía canción↔vídeo; solo desplaza el contenido dentro del clip |

**Invariante clave verificado:** `xfade_on` (`renderer.py:57`) gobierna el padding y `_build_xfade_filtergraph` usa exactamente las mismas dos condiciones (`_xfade_enabled` cacheado + `_uses_incoming_xfade`). Nunca se da el caso "clips padded que acaban en la rama concat": que reintroduciría la deriva con signo contrario.

## Deuda relacionada (fuera de este fix)

- El comentario de `_mux_audio` (`renderer.py:164-167`) afirma que `_render_video_clip` no usa `-ss`. **Es falso desde el commit `295ef0c`**: con `fragment != 0` la imagen se sirve desde `source[offset]` (el `base` del fragmento, menos el lead-in si el clip va padded) pero su audio se extrae desde `source[0]` (`renderer.py:176-180`, sin `-ss` y con `-t item.duration`). Desfase intra-clip, independiente de este postmortem, sin tests.
- El reporte Markdown cuenta "Crossfade transitions" con `transition_hint == "xfade"` a secas (`report.py:24-26`), sin aplicar `_uses_incoming_xfade` ni `_xfade_enabled` → **sobreestima**, y con >50 items informa xfades donde no hay ninguno.

---

Bloque: [[20-Proyectos/34-video-sync/34.05-render-y-sincronizacion/index|34.05 Render y sincronizacion]]
Tests: [[20-Proyectos/34-video-sync/34.08-pruebas-y-verificacion/index|34.08 Pruebas y verificacion]]
