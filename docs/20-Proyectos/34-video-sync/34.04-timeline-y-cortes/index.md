---
tema: timeline y cortes
tipo: nota
proyecto: video-sync
creada: 2026-07-02
actualizada: 2026-07-02
estado: activa
aliases: [Timeline y cortes, Cut points, Asignación de assets]
tags: [video-sync, timeline, cortes, assets]
---

# 34.04 Timeline y cortes

Fuente: `synced_edit/timeline.py` del repo.

El módulo convierte el análisis de audio ([[20-Proyectos/34-video-sync/34.03-analisis-de-audio/index|34.03 Análisis de audio]]) en una lista de `TimelineItem` contigua que cubre toda la canción. No toca ffmpeg, no lee el contenido de los medios y no ejecuta subprocesos: solo compone el cronograma que el renderer debe respetar. Además produce el campo `transition_hint`, que es lo único de este archivo que condiciona el render ([[20-Proyectos/34-video-sync/34.05-render-y-sincronizacion/index|34.05 Render y sincronización]]).

## Flujo

```
analysis (beats, onset_strength, duration, sections)
   |
   |-- onset_strength truthy --> _adaptive_cut_points(beats, onset, duration, beats_per_cut)
   |                                   |- si len(onset) < len(beats) --> delega ------+
   |-- vacío -----------------> _cut_points(beats, duration, beats_per_cut) <---------+
                                        |
                                        +-- dedupe 0.35 + _land_on_duration --> cut_points
                                                                                   |
song_end = cut_points[-1]                                                          |
segments = zip(cut_points, cut_points[1:]) <---------------------------------------+
   |
   |-- [solo si cap AND no videos AND len(segments) > cap*len(images)]
   |       _downsample_segments(segments, cap*len(images))   + aviso a stderr
   |
   +-- work = deque(segments)
         while work:  start, end = work.popleft();  source = _pick()
             video  -> crece hasta min_video_duration comiendo beats; fragment = vid_frag[src]++
             imagen -> parte con max_image_duration; re-encola el resto al frente
         |
         +-- [max_items] items[:max_items]  +  items[-1].end = round(song_end, 4)
                 |
                 +-- Timeline(audio=analysis.to_json(), width, height, fps, items, focus)
```

## Defaults: firma vs. CLI

La firma de `build_timeline` (`timeline.py:71-84`) y el CLI (`synced_edit/cli.py`) divergen en cuatro parámetros. El CLI siempre pasa el valor explícito, así que los defaults de la firma solo aplican a llamadas programáticas.

| Parámetro | Default en la firma | Default del CLI | Flag |
|---|---|---|---|
| `width` / `height` / `fps` | `1080` / `1920` / `30` | iguales | `--width` / `--height` / `--fps` |
| `beats_per_cut` | `4` (`:77`) | **`2`** | `--beats-per-cut` |
| `max_items` | `None` (`:78`) | `None` | `--max-items` |
| `max_image_duration` | `None` (`:79`) | **`4.0`** | `--max-image-duration` |
| `min_video_duration` | `None` (`:80`) | **`4.0`** | `--min-video-duration` |
| `max_asset_uses` | `3` (`:81`) | `3` | `--max-asset-uses` |
| `focus` | `"dynamic"` (`:82`) | **`"face"`** | `--focus` |
| `seed` | `0` (`:83`) | `0` | `--seed` |

Validaciones de entrada (`:85-88`): `assets` vacío → `ValueError("No image or video assets found")`; `beats_per_cut < 1` → `ValueError("beats_per_cut must be >= 1")`. No se valida `width/height/fps`, ni `focus` contra un enum, ni que `max_items` sea positivo.

## Cut points

La rama se elige en `:90-95`: si `analysis.onset_strength` es truthy va a `_adaptive_cut_points`, si está vacío a `_cut_points`. Con `--manual-bpm`, `onset_strength` queda vacío y siempre se toma la ruta uniforme.

### `_cut_points(beats, duration, beats_per_cut)`: `:389`

Rejilla uniforme, sin sensibilidad al ritmo.

```python
if not beats:
    beats = [0.0]
points = [0.0]
normalized = [beat for beat in beats if 0 < beat < duration]
points.extend(normalized[beats_per_cut - 1 :: beats_per_cut])
if points[-1] < duration:
    points.append(duration)
```

El slice `normalized[beats_per_cut - 1 :: beats_per_cut]` toma el último beat de cada grupo: con el default del CLI (`beats_per_cut=2`) es `normalized[1::2]` → índices 1, 3, 5… El filtro es estrictamente abierto (`0 < beat < duration`). Sin beats detectados, `normalized` queda vacío y el resultado es `[0.0, duration]`: un único segmento con toda la canción.

Cierre: dedupe con piso **0.35 s** hardcodeado (`:400` y `:402`, sin variable `min_gap`) y `_land_on_duration(deduped, duration, 0.35)`.

### `_adaptive_cut_points(beats, onset_strength, duration, beats_per_cut)`: `:331`

Guard de delegación (`:337-338`): `if not onset_strength or len(onset_strength) < len(beats): return _cut_points(...)`. El contrato implícito es que `onset_strength` está indexado **por índice de beat**, en paralelo a `beats` (el acceso es `onset_norm[i]`).

Normalización y umbrales (`:340-346`):

```python
max_onset = max(onset_strength) or 1.0
onset_norm = [v / max_onset for v in onset_strength]
sorted_norm = sorted(onset_norm)
n = len(sorted_norm)
threshold_high = sorted_norm[int(0.70 * n)]   # percentil 70
threshold_low  = sorted_norm[int(0.30 * n)]   # percentil 30
```

Percentiles **por índice truncado, no interpolados**. `max(onset_strength) or 1.0` solo protege contra un máximo exactamente `0`.

Intervalo por beat (`:360-365`):

| Condición sobre `strength` (normalizado a [0,1]) | `interval` |
|---|---|
| `>= threshold_high` (percentil 70) | **2** beats |
| `<= threshold_low` (percentil 30) | **6** beats |
| banda intermedia (30–70) | **`beats_per_cut`** (2 vía CLI) |

Decisión de corte (`:353-374`):

- Filtro: `if beat_time <= 0 or beat_time >= duration: continue`: el índice `i` sigue avanzando en los beats descartados, así que `beats_since = i - last_cut_idx` los cuenta igual.
- `strength = onset_norm[i] if i < len(onset_norm) else 0.5`: el `else 0.5` es **código muerto** (el guard de `:337` ya garantiza `len(onset) >= len(beats)`).
- Downbeat: `is_downbeat = (i % 4 == 0)`: asume compás 4/4 y que `beats[0]` es el downbeat. Un downbeat fuerza corte con solo `beats_since >= 2`, lo que anula el intervalo de 6 en pasajes suaves.
- Corte efectivo: `(should_cut_by_interval or should_cut_by_downbeat) and beat_time - last_cut_time >= min_gap`, con `min_gap = 0.35` (`:351`). El piso de 0.35 s es la guarda dura real; el intervalo en beats es solo la primera condición.

Cierre (`:376-386`): append de `duration` si `points[-1] < duration`; dedupe con el mismo piso de 0.35 (el `duration` recién añadido puede quedar a menos de 0.35 del último beat y ser descartado, por eso viene `_land_on_duration` justo después); `_land_on_duration(deduped, duration, min_gap)` muta la lista in-place y devuelve `None`.

### `_land_on_duration(deduped, duration, min_gap)`: `:406`

```python
if deduped[-1] >= duration:
    return
if duration - deduped[-1] >= min_gap or len(deduped) == 1:
    deduped.append(duration)
else:
    deduped[-1] = duration
```

Tres ramas: no-op si ya llega; append si el hueco final mide al menos `min_gap`; **snap** del último corte a `duration` si no (absorbe la astilla en el clip anterior en vez de emitir un clip de duración ~0). El `or len(deduped) == 1` cubre canciones de menos de 0.35 s, donde hay que appendear o no habría ningún segmento.

**Invariante resultante:** `cut_points[0] == 0.0`, `cut_points[-1] == analysis.duration`, y todo par adyacente dista `>= 0.35 s`: salvo el caso degenerado de un único segmento en canciones ultracortas.

## `_transition_hint_for`: origen del hint que usa el renderer

```python
def _transition_hint_for(start_time: float, sections: list[dict]) -> str:   # :324
    for section in sections:
        if section.get("start", 0.0) <= start_time < section.get("end", float("inf")):
            return "xfade" if section.get("energy_level") == "low" else "cut"
    return "cut"
```

Esta es la **única fuente** de `transition_hint` en el proyecto. Contrato exacto:

- Devuelve `"xfade"` si y solo si la sección que contiene el `start` del item tiene `energy_level == "low"` (igualdad exacta, case-sensitive). `"medium"`, `"high"`, ausente o cualquier otro valor → `"cut"`.
- Intervalo **semiabierto** `start <= start_time < end`: un item que empieza justo en el `end` de una sección pertenece a la siguiente.
- Gana la **primera** sección que hace match, no la mejor ni la última. Una sección sin `end` (default `inf`) hace match con todo lo posterior y cortocircuita el resto de la lista.
- Sin match o `sections == []` → `"cut"` (default también del dataclass, `:27`).

El hint es una propiedad **del item entrante**, derivada de su propio `start`. `timeline.py` **no** fuerza `"cut"` en el item 0: si la sección inicial es de baja energía, `items[0].transition_hint == "xfade"` aunque no exista clip previo con el que cruzar. Filtrar ese caso es responsabilidad del renderer (`_uses_incoming_xfade` exige `index > 0`). Tampoco se reserva ni descuenta margen alguno para el xfade: `duration` es duración musical pura y toda la compensación de los 0.25 s vive en el renderer. Detalle completo en [[20-Proyectos/34-video-sync/34.05-render-y-sincronizacion/index|34.05 Render y sincronización]] y [[20-Proyectos/34-video-sync/34.05-render-y-sincronizacion/fix-desincronizacion-xfade|Fix desincronización xfade]].

## Asignación de assets

Clasificación (`:100-104`): `_is_image(path)` es `path.suffix.lower() in IMAGE_EXTENSIONS`. **Todo lo que no sea imagen se trata como vídeo**: `VIDEO_EXTENSIONS` solo se usa como filtro en `collect_assets` (`:64,66`), no aquí.

Cap (`:105`): `cap = max_asset_uses if max_asset_uses and max_asset_uses > 0 else None`. Solo enteros `>= 1` producen cap; `0`, negativos y `None` lo desactivan.

### Cap de usos: imágenes sí, vídeos no

`--max-asset-uses 3` (default) limita a 3 apariciones **por imagen**. Los vídeos están exentos por diseño: una reaparición de vídeo no es una repetición visual, porque recibe el siguiente índice de `fragment` y el renderer hace seek a otro momento de la fuente (`:196-211`):

```python
frag = vid_frag.get(skey, 0)
items.append(_make_item(len(items), source, "video", start, end, "fit", analysis, fragment=frag))
vid_frag[skey] = frag + 1
```

La primera aparición lleva `fragment=0`, la segunda `1`, y así sucesivamente, sin techo, indexado por `str(path)`. Las imágenes llevan siempre `fragment=0` y su contador es `img_use[skey] = img_use.get(skey, 0) + 1` (`:238`), que se incrementa **una vez por item emitido**, incluidas las piezas parciales de un slot troceado.

### Nunca el mismo asset dos veces seguidas

`_pick()` (`:155-189`) recorre el bag de izquierda a derecha y hace `pop(i)` del **primer** elemento cuyo `str()` difiera de `last_source`. Si todo el bag restante es `last_source`, cae al fallback (`:182-189`): toma de `assets` completo un asset distinto, barajado; solo cuando `others` está vacío (un único asset en toda la carpeta) se permite el repeat back-to-back, que ahí es genuinamente inevitable.

Dos gotchas del fallback: el asset devuelto **no se saca del `round_bag`** (se sirve fuera de ronda), y puede exceder el cap de imágenes: por eso hay un chequeo explícito de `cap_exceeded` en `:187-188`. `last_source` lo actualiza el bucle principal (`:210`, `:240`), nunca `_pick`.

### La bolsa barajada por ronda y por qué existe

```python
rng = random.Random(seed)   # :151  RNG local, no toca el global de random
...
if not round_bag:
    eligible = [a for a in assets
                if not (_is_image(a) and cap and img_use.get(str(a), 0) >= cap)]
    if not eligible:
        eligible = list(assets)
        cap_exceeded = True
    if rounds_done > 0:
        rng.shuffle(eligible)
    round_bag = eligible
    rounds_done += 1
```

Una ronda es una aparición de cada asset todavía elegible: toda imagen bajo el cap más **todos** los vídeos (el filtro exige `_is_image(a)`, así que los vídeos nunca se excluyen). El motivo del barajado está en el comentario `:146-150`: sin él, un round-robin fijo hace que una carpeta con pocos assets reproduzca la misma secuencia de dos minutos en bucle; rebarajar por ronda garantiza que ciclos consecutivos no sean idénticos.

**La primera ronda no se baraja** (`if rounds_done > 0`): conserva el orden que entregó la selección smart/mood ([[20-Proyectos/34-video-sync/34.06-clasificacion-y-seleccion/index|34.06 Clasificación y selección]]) para que la apertura siga el ranking. A partir de la segunda, `rng.shuffle`. Con el mismo `--seed` y el mismo orden de `assets`, el resultado es determinista y reproducible.

Cuando todas las imágenes llegan al cap y no hay vídeo que llene el slot, el cap se **relaja** (las imágenes repiten, barajadas) en vez de congelar una foto, y se marca `cap_exceeded`.

## `--max-image-duration` vs `--min-video-duration`

Son dos conceptos distintos y ninguno cruza el tipo de asset: `--max-image-duration` no afecta a vídeos y `--min-video-duration` no afecta a imágenes. Sustituyeron a los antiguos `--max-clip-duration` / `--min-clip-duration`, que sí cruzaban.

### `--max-image-duration`: una imagen larga → SECUENCIA de fotos distintas

```python
span = end - start                                                    # :215
parts = 1
if max_image_duration and span > max_image_duration:
    parts = min(math.ceil(span / max_image_duration), max(1, int(span / min_gap)))   # :221
if parts >= 2:
    piece_end = start + span / parts
    items.append(_make_item(len(items), source, "image", start, piece_end, ...))
    work.appendleft((piece_end, end))                                  # :230
```

Con `parts >= 2` se emite **solo la primera pieza** y el resto `(piece_end, end)` se re-encola al frente del `deque`. En la siguiente iteración `_pick()` elige otra foto (por el filtro `!= last_source`), así que un slot largo se llena con fotos **distintas**, nunca con la misma repetida. El resto re-encolado vuelve a pasar por el mismo cálculo y se subdivide progresivamente hasta caber bajo el máximo.

El segundo término de `parts`, `max(1, int(span / min_gap))` con `min_gap = 0.35`, es un techo de seguridad: acota el número de piezas para que ninguna baje de ~0.35 s. **Gotcha:** cuando ese techo recorta el `ceil`, el máximo se viola conscientemente. Ejemplo real: `span=1.0`, `max_image_duration=0.1` → `ceil(10)=10` vs `max(1, int(2.857))=2` → `parts=2` → piezas de 0.5 s, cinco veces el máximo pedido.

### `--min-video-duration`: un vídeo corto CRECE comiendo beats siguientes

```python
if min_video_duration and (end - start) < min_video_duration:         # :198
    target = min(start + min_video_duration, song_end)
    while end < target - 1e-9 and work:
        end = work.popleft()[1]
    if end - target >= min_gap:
        work.appendleft((target, end))
        end = target
```

`target` va clampeado a `song_end`, así que nunca excede el final de la canción. El bucle devora segmentos siguientes conservando solo su `end` (los cortes intermedios desaparecen, que es exactamente la intención). La épsilon `1e-9` evita una iteración de más por error de coma flotante. El sobrante se re-encola **solo si mide `>= 0.35 s`**; si mide menos, el vídeo se lo queda para no generar una astilla.

**No es un piso duro:** el `while` también termina si `work` se vacía, así que un vídeo al final de la canción puede quedar más corto que `min_video_duration`.

Los vídeos reciben siempre `effect="fit"` (`:207`) y **no** incrementan `effect_index` (`:239`), así que la rotación de efectos de imagen es independiente de cuántos vídeos se intercalen. Los efectos disponibles dependen de `focus` (`:125-128`): `"center"` o `"face"` → `["zoom_in", "zoom_out"]`, porque ese encuadre quiere mantener al sujeto en cuadro y el paneo deriva el crop hacia los bordes (y hacia fondos vacíos); cualquier otro valor (incluido `"dynamic"` y typos) → `+ ["pan_left", "pan_right"]`.

## `_downsample_segments` y avisos por stderr

El engrosado de la rejilla se dispara con una condición triple **AND** (`:111`):

```python
if cap and not videos and len(segments) > cap * len(images):
    capacity = cap * len(images)
    print(f"Only {len(images)} image(s) for {len(segments)} cuts: coarsening to "
          f"{capacity} clips so none repeats more than {cap}x "
          "(add more media or shorten the audio range for more variety).", file=sys.stderr)
    segments = _downsample_segments(segments, capacity)
```

El `not videos` es la clave: como los vídeos pueden repetirse libremente y cada aparición muestra otro fragmento, **si existe cualquier vídeo la rejilla nunca se engrosa**: los slots sobrantes simplemente van a vídeos. Solo una carpeta de puras fotos, con menos fotos de las que el cap permite cubrir, fuerza cortes más largos.

```python
def _downsample_segments(segments, target):                            # :266
    if target >= len(segments) or target < 1:
        return segments
    boundaries = [segments[0][0]] + [seg[1] for seg in segments]
    total = len(segments)
    keep = sorted({round(i * total / target) for i in range(target + 1)})
    points = [boundaries[i] for i in keep]
    return list(zip(points, points[1:]))
```

Solo fusiona segmentos adyacentes, nunca parte: conserva el primer boundary (`0.0`) y el último (`song_end`), y muestrea los intermedios en fracciones pares. Como el piso de 0.35 s ya se cumplía en la entrada, se preserva por transitividad.

**Gotchas:** `keep` es un `set`, así que las colisiones de `round` se colapsan → el resultado puede tener **menos** de `target` segmentos (`len(result) <= target`, no `== target`). Además `round()` de Python usa banker's rounding (`round(0.5)==0`, `round(2.5)==2`), lo que sesga los boundaries cuando `total/target` cae en `.5`.

Ambos avisos son `print(..., file=sys.stderr)`, no `logging`. El segundo, `cap_exceeded` (`:242-247`), se emite una sola vez al final:

```
Not enough images to keep every photo under {cap} uses; some repeat more often.
Add more photos for more variety.
```

Se activa desde `_pick`, tanto al relajar el cap en el refill (`:170`) como en el fallback anti-cadena (`:188`). Invariante verificado: si `cap_exceeded` es `True`, `cap` no es `None`, así que el mensaje nunca imprime `None`. Nótese que se marca al **relajar**, no al usar: puede quedar `True` aunque el cap acabara respetándose de facto.

## `--max-items` y el estiramiento del último clip

```python
if max_items and len(items) > max_items:                               # :249
    # Truncate, then stretch the kept final clip back to the song end so the
    # video still covers the full audio (otherwise -shortest would clip it).
    items = items[:max_items]
    items[-1].end = round(song_end, 4)
    items[-1].duration = round(items[-1].end - items[-1].start, 4)
```

Este es el **único** punto donde `end`/`duration` se mutan tras `_make_item`. `start`, `effect` y `transition_hint` no se recalculan: correcto, porque `start` no cambia.

La invariante "cubrir todo el audio" gana sobre "hold máximo": el clip final estirado **ignora por completo `max_image_duration`** y puede durar minutos (p. ej. `--max-items 5` sobre una canción de 3 min). Es el precio de que el `-shortest` del renderer no recorte el vídeo.

Casos borde no defendidos: `max_items=0` es falsy → no trunca (no produce timeline vacío); `max_items=-1` es truthy y `len(items) > -1` siempre es cierto → hace `items[:-1]`, cortando el último item, y luego estira el nuevo último.

## `TimelineItem` y `timeline.json`

### Campos exactos: `:18-31`

| Campo | Tipo | Default | Nota |
|---|---|---|---|
| `index` | `int` | — | invariante `items[i].index == i`, denso y 0-based; se preserva tras truncar |
| `source` | `str` | — | `str(path)` absoluto, **no** `Path` |
| `source_type` | `str` | — | solo `"image"` o `"video"` |
| `start` | `float` | — | `round(start, 4)` |
| `end` | `float` | — | `round(end, 4)` |
| `duration` | `float` | — | `round(end - start, 4)`: calculado con los valores **sin redondear** |
| `effect` | `str` | — | `"fit"` en vídeos; `zoom_in`/`zoom_out`/`pan_left`/`pan_right` en imágenes |
| `transition_hint` | `str` | `"cut"` | `"xfade"` solo en secciones `energy_level == "low"` |
| `fragment` | `int` | `0` | aparición N-ésima del vídeo; siempre `0` en imágenes |

`_make_item` (`:283-303`) redondea `start` y `end` a 4 decimales, pero calcula `duration` desde los originales: `item.duration` puede diferir de `item.end - item.start` (ya redondeados) hasta ~1e-4 s. Los items son contiguos por construcción (`end` de uno == `start` del siguiente, pre-redondeo), así que `sum(item.duration) ≈ song_end` con un error acumulado del orden de `1e-4 × N_items`.

### JSON de salida: `Timeline.to_json()` `:44-53`

Claves en este orden exacto:

```json
{
  "audio": { ... },        // analysis.to_json() completo
  "width": 1080,
  "height": 1920,
  "fps": 30,
  "focus": "face",
  "selection": {},
  "items": [ { "index": 0, "source": "...", "source_type": "image",
               "start": 0.0, "end": 2.0, "duration": 2.0,
               "effect": "zoom_in", "transition_hint": "cut", "fragment": 0 } ]
}
```

`build_timeline` construye el `Timeline` **sin pasar `selection`** (`:256-263`) → queda `None` → `self.selection or {}` lo serializa como `{}`. Quien quiera poblarlo debe asignarlo después de construir (el CLI lo hace tras `build_timeline`).

`write_timeline` (`:306-308`): `mkdir(parents=True, exist_ok=True)`, `json.dumps(..., indent=2) + "\n"`, utf-8. Escritura **no atómica** (sin tmp+rename) y sobrescribe sin aviso.

`load_timeline` (`:311-321`): claves obligatorias `audio`, `width`, `height`, `fps`, `items` (`KeyError` si faltan); `selection` → `{}` y `focus` → `"dynamic"` como defaults. `TimelineItem(**item)` es **estricto**: una clave extra en el JSON lanza `TypeError`. No valida contigüidad, ni `duration == end - start`, ni `index == i`: un `timeline.json` editado a mano puede romper las invariantes y llegar al renderer.

Round-trip: `write_timeline` emite `"selection": {}` cuando era `None` y `load_timeline` lo lee como `{}` → `load(write(t))` cambia ese campo de `None` a `{}`, aunque el JSON en disco sí es estable.

## Constantes y código muerto

| Valor | Cita | Significado |
|---|---|---|
| `0.35` | `:138`, `:351`, `:400`, `:402` | `min_gap`: **cuatro copias sin constante compartida** |
| `1e-9` | `:200` | épsilon del crecimiento de vídeo |
| `4` | `:253-254`, `:297-299` | decimales de todos los tiempos |
| `0.70` / `0.30` | `:345-346` | percentiles high/low de onset |
| `2` / `6` | `:361`, `:363` | intervalos de beats para onset fuerte / débil |
| `4` | `:367` | módulo del downbeat (`i % 4 == 0`) |
| `2` | `:369` | `beats_since` mínimo para cortar en downbeat |

`0.25` (la duración del xfade) **no aparece en este archivo**: vive íntegra en el renderer.

Código muerto verificado: `field` importado en `:8` y nunca usado; `else 0.5` en `:358`; `if not points` en `:376` (`points` arranca `[0.0]`); `next_i` redundante en `_compute_sections` del módulo de audio. `collect_assets` (`:56-68`) no deduplica, no es recursivo (solo hijos directos) e ignora `.heic`/`.gif` por no estar en `IMAGE_EXTENSIONS`.

## Enlaces

- [[20-Proyectos/34-video-sync/index|Video Sync]]
- [[20-Proyectos/34-video-sync/34.03-analisis-de-audio/index|34.03 Análisis de audio]]: produce `beats`, `onset_strength`, `duration` y `sections`; la ruta adaptativa y el `transition_hint` dependen enteramente de ellos.
- [[20-Proyectos/34-video-sync/34.05-render-y-sincronizacion/index|34.05 Render y sincronización]]: consume `transition_hint`, `fragment` y `duration`; el hint condiciona el filtergraph xfade.
- [[20-Proyectos/34-video-sync/34.05-render-y-sincronizacion/fix-desincronizacion-xfade|Fix desincronización xfade]]: por qué `duration` es duración musical pura y el margen de 0.25 s no se descuenta aquí.
- [[20-Proyectos/34-video-sync/34.06-clasificacion-y-seleccion/index|34.06 Clasificación y selección]]: el orden que conserva la primera ronda de `_pick`.
- [[20-Proyectos/34-video-sync/34.07-uso-y-flags/index|34.07 Uso y flags]]
