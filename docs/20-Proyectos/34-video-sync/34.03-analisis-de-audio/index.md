---
tema: análisis de audio
tipo: nota
proyecto: video-sync
creada: 2026-07-02
actualizada: 2026-07-02
estado: activa
aliases: [Análisis de audio, Beats y emoción]
tags: [video-sync, audio, beats, emocion, librosa]
---

# 34.03 Análisis de audio

Fuente: `synced_edit/audio_analysis.py` del repo.

Este bloque produce el **cronograma de la canción**: duración, BPM, `beats[]` (tiempos absolutos en segundos), `onset_strength[]` y `sections[]`. Todo lo demás del pipeline se deriva de aquí: los cortes de [[20-Proyectos/34-video-sync/34.04-timeline-y-cortes/index|34.04 Timeline y cortes]] se posan sobre `beats[]`, y la emoción detectada alimenta el ranking de [[20-Proyectos/34-video-sync/34.06-clasificacion-y-seleccion/index|34.06 Clasificación y selección]].

El módulo no importa nada de terceros a nivel de módulo. `librosa` se importa **dentro** de `_analyze_with_librosa` (`:163`), lo que lo vuelve opcional de facto.

## Flujo

```
audio_path
   │
   ├─ [--audio-start / --audio-end]  trim_audio()  ──►  work/trimmed_audio.wav
   │                                                        │
   └────────────────────────────────────────────────────────┘
                             │
                       analyze_audio(path, manual_bpm)
                             │
        ┌────────────────────┼────────────────────────┐
        │                    │                        │
   manual_bpm truthy    try librosa           except Exception
        │                    │                        │
  method="manual-bpm"  method="librosa"      method="ffmpeg-energy"
  onset=[] sections=[]  onset por beat         onset por pico
        │                    │                        │
        └────────────────────┴────────────────────────┘
                             │
                    _compute_sections()  ──► sections[]  ──► transition_hint (34.04)
                             │
                    detect_emotion()     ──► (emocion, confianza) ──► mood (34.06)
                             │
                    write_analysis()     ──► audio_analysis.json
```

## `trim_audio()`: recorte previo

Firma: `trim_audio(source: Path, output: Path, start: float = 0.0, end: float | None = None) -> Path` (`:130`).

Validaciones antes de tocar ffmpeg:

| Condición | Error |
|---|---|
| `start < 0` | `ValueError("audio start must be >= 0")` (`:133-134`) |
| `end is not None and end <= start` | `ValueError("audio end must be greater than audio start")` (`:135-136`) |

Comando real construido (`:139-142`):

```bash
# con start=12.5, end=45.0
ffmpeg -y -ss 12.5000 -i <source> -t 32.5000 -vn -acodec pcm_s16le <output>

# sin end
ffmpeg -y -ss 12.5000 -i <source> -vn -acodec pcm_s16le <output>
```

Detalles que importan:

- Duración = `end - start`, formateada a **4 decimales** (`f"{end - start:.4f}"`).
- `-ss` va **antes** de `-i` → *input seeking* rápido. Irrelevante para la precisión aquí porque la salida es PCM sin comprimir.
- `-vn` descarta vídeo: sin esto, una carátula embebida rompería el WAV.
- `-acodec pcm_s16le` **sin `-ar` ni `-ac`** → conserva sample rate y canales del original. El contenedor se infiere de la extensión: el CLI pasa `work/trimmed_audio.wav` (`cli.py:87`), que es lo correcto.
- `subprocess.run(..., check=True, stdout=DEVNULL, stderr=DEVNULL)` (`:143`) → si ffmpeg falla, sale un `CalledProcessError` **mudo**: el diagnóstico de ffmpeg se descarta.
- **No comprueba que `ffmpeg` exista.** Si falta del PATH y se usa `--audio-start`, el error es un `FileNotFoundError` crudo de `subprocess`, no el mensaje amable de `_analyze_with_energy` (`:199-200`).

### Por qué la duración recortada pasa a ser el objetivo del timeline

El CLI decide el recorte con esta condición (`cli.py:85-88`):

```python
audio_path = args.audio
if audio_start or audio_end is not None:
    audio_path = Path("work") / "trimmed_audio.wav"
    trim_audio(args.audio, audio_path, start=audio_start, end=audio_end)
```

Con `audio_start == 0.0` y `audio_end is None` **no** recorta: `analyze_audio` recibe el archivo original.

Cuando sí recorta, `analyze_audio` mide el WAV recortado, así que `duration`, `beats[]` y `sections[]` quedan **relativos al recorte**, con el origen en `0.0`. `build_timeline` toma `analysis.duration` como `song_end` y ahí acaba el vídeo. La procedencia del original se conserva solo como metadato, sobrescrito por el CLI **después** de construir el objeto (`cli.py:91-93`):

```python
analysis.source_audio_path = str(args.audio.expanduser().resolve())  # el ORIGINAL
analysis.source_audio_start = audio_start
analysis.source_audio_end = audio_end
```

Ninguna función de este módulo escribe esos tres campos ni los usa para nada. Son el único puente hacia la línea de tiempo original.

Nota: `trim_audio` **no valida `end` contra la duración real** del archivo. Un `end` mayor que la canción produce un audio más corto en silencio; downstream no desincroniza (la duración se recalcula desde el WAV), pero el `source_audio_end` del JSON queda mintiendo.

## `analyze_audio()`: las tres rutas

Firma: `analyze_audio(audio_path: Path, manual_bpm: float | None = None) -> AudioAnalysis` (`:33`).

1. `audio_path.expanduser().resolve()` (`:34`) → el `audio_path` del JSON siempre es absoluto.
2. `if not audio_path.exists(): raise FileNotFoundError(f"Audio file not found: {audio_path}")` (`:35-36`).
3. Bifurcación:

```python
if manual_bpm:                          # :38  ← test de veracidad, NO "is not None"
    duration = probe_duration(audio_path)
    return AudioAnalysis(..., bpm=manual_bpm, beats=_regular_beats(duration, manual_bpm),
                         method="manual-bpm")
try:
    return _analyze_with_librosa(audio_path)   # :49
except Exception:                              # :50  ← desnudo, mudo
    return _analyze_with_energy(audio_path)    # :51
```

Los tres valores de `method` que existen: `"manual-bpm"` (`:45`), `"librosa"` (`:192`), `"ffmpeg-energy"` (`:242`).

### `--manual-bpm` → `method="manual-bpm"`

Puentea todo el análisis. `duration` sale de `probe_duration()` (ffprobe sobre `format=duration`), `beats` de `_regular_beats(duration, manual_bpm)`, y **`onset_strength` y `sections` quedan vacíos** (defaults del dataclass). Consecuencias en cadena:

- `build_timeline` no encuentra `onset_strength` → usa `_cut_points` uniforme en vez de `_adaptive_cut_points`, y el reporte dice `uniform (beats_per_cut)`.
- `_compute_sections` devuelve la sección única `medium` → todos los `transition_hint` salen `"cut"`, cero xfades.
- `detect_emotion` cae al perfil sintético y su confianza no supera 0.4 (ver abajo).

Ni `bpm` ni `duration` se redondean en esta rama (los otros dos caminos sí: `round(bpm, 2)`).

Gotchas verificados:

- **`--manual-bpm 0` es falsy** → se ignora en silencio y corre el análisis automático.
- **`manual_bpm` negativo → bucle infinito.** `-120` es truthy → `_regular_beats` con `step = -0.5` → `current` decrece y `current < duration` nunca falla. No hay validación de rango en ningún punto.

### `_analyze_with_librosa()`: camino preferente

```python
y, sr = librosa.load(str(audio_path), mono=True)                          # :165
tempo, beat_frames = librosa.beat.beat_track(y=y, sr=sr, units="frames")  # :166
beat_times = librosa.frames_to_time(beat_frames, sr=sr).tolist()          # :167
duration = float(librosa.get_duration(y=y, sr=sr))                        # :168
bpm = float(tempo[0] if hasattr(tempo, "__len__") else tempo)             # :169
onset_env = librosa.onset.onset_strength(y=y, sr=sr)                      # :171
```

- La línea `:169` es un shim de compatibilidad: librosa ≥0.10 devuelve `tempo` como `ndarray`.
- No se pasa `hop_length` ni a `beat_track` ni a `onset_strength` → ambos comparten la rejilla por defecto de librosa, y por eso indexar `onset_env[int(f)]` con frames de `beat_track` es coherente.
- **Umbral `< 4` beats** (`:173`): si la detección da menos de 4, se descarta y se sintetiza rejilla regular con fallback de BPM **120** si `bpm <= 0`; `onset_at_beats` queda `[]`.
- Camino normal (`:177-183`): pares `(round(float(t), 4), float(onset_env[min(int(f), len(onset_env)-1)]))` filtrados por `0 <= t <= duration` (inclusivo). `beats` se redondea a 4 decimales; `onset_at_beats` **no**.
- `int(f)` trunca y `min(..., len-1)` satura → varios beats finales pueden compartir el mismo valor de onset.

**Invariante:** en el camino bueno, `len(onset_strength) == len(beats)`. Es exactamente lo que exige `_adaptive_cut_points` (`timeline.py:337-338`) para no re-delegar al corte uniforme.

### `_analyze_with_energy()`: fallback puro-stdlib, `method="ffmpeg-energy"`

Este camino **no usa numpy ni librosa**: solo `wave`, `struct`, `math` y el binario `ffmpeg`.

```python
if not shutil.which("ffmpeg"):
    raise RuntimeError("ffmpeg is required when librosa is not installed")  # :199-200
```

Este `RuntimeError` **sí propaga** al caller: ya estamos dentro del `except`, no hay segunda red.

Comando de decodificación (`:204-221`):

```bash
ffmpeg -y -i <audio_path> -ac 1 -ar 22050 -f wav <tmpdir>/audio.wav
```

`check=True` con `stdout`/`stderr` a `DEVNULL` → mismo `CalledProcessError` mudo que `trim_audio`. El WAV vive en un `TemporaryDirectory` y se lee dentro del `with` (`:222`), sin uso tras el borrado.

Cadena de DSP (`:224-235`):

```python
duration = len(samples) / sample_rate if sample_rate else probe_duration(audio_path)
envelope = _rms_envelope(samples, sample_rate, frame_seconds=0.046)
peaks    = _pick_energy_peaks(envelope, hop_seconds=0.046, duration=duration)
onset_at_beats = _onset_strength_from_envelope(envelope, peaks, hop_seconds=0.046)
bpm = _estimate_bpm(peaks)

if not peaks or bpm <= 0:      # :230
    bpm = 120.0
    peaks = _regular_beats(duration, bpm)
    onset_at_beats = []
```

`0.046` está **hard-codeado tres veces** como literal (`:225`, `:226`, `:227`), no como constante compartida. Cambiar uno sin los otros rompe el mapeo tiempo↔índice.

Helpers, con sus constantes reales:

| Helper | Cita | Constantes y comportamiento |
|---|---|---|
| `_read_wav_mono` | `:248` | Exige `sample_width == 2` (`ValueError("Expected 16-bit PCM WAV from ffmpeg")`); little-endian `<{count}h`; normaliza por `32768.0`. Carga el audio entero como `list[float]` (~170 MB por 4 min a 22050 Hz). |
| `_rms_envelope` | `:269` | `frame = max(1, int(sample_rate * frame_seconds))`; ventanas **no solapadas**, sin ventaneo (rectangular). |
| `_pick_energy_peaks` | `:280` | Mínimo **8** frames; `threshold = mean + σ * 0.55` (umbral **global** de toda la canción); `min_gap = 0.24` s (tope 250 BPM); máximo local no estricto (`>=` a ambos lados); greedy: dentro de 0.24 s gana el **primero**, no el más fuerte. Frames `0` y `len-1` nunca se evalúan. |
| `_onset_strength_from_envelope` | `:301` | Diferencia de primer orden **rectificada**: `max(0.0, envelope[idx] - envelope[prev_idx])`. |
| `_estimate_bpm` | `:365` | Intervalos filtrados a `[0.25, 2.0]` s; **mediana superior** (`intervals[n//2]`); `bpm = 60/median`; octave folding `while bpm < 80: *=2` / `while bpm > 180: /=2`. Retorno no-cero siempre en `[80, 180]`. |
| `_regular_beats` | `:383` | `step = 60/bpm`; arranca en `0.0`; `while current < duration` (estricto); `round(current, 4)`. |

**Escalas incomparables:** el `onset_strength` de esta ruta está en escala de RMS absoluto (típicamente ≪ 1); el de librosa está en su escala espectral interna (decenas). Quien consuma `onset_strength` con umbrales absolutos estaría roto entre métodos. Tanto `detect_emotion` como `_adaptive_cut_points` lo toleran porque **ambos normalizan por `max(onset)`** antes de comparar.

**Deriva de rejilla (defecto real, presente hoy):** `_rms_envelope` calcula `frame = int(22050 * 0.046) = 1014` muestras → hop real `1014/22050 = 0.0459864` s. Pero a `_pick_energy_peaks` y `_onset_strength_from_envelope` se les pasa `hop_seconds=0.046`. El tiempo reportado del frame *i* es `i*0.046` cuando el real es `i*0.0459864`:

- Error relativo `+0.0296 %` → los beats se reportan **tarde**, proporcionalmente al tiempo transcurrido.
- A los 240 s (4 min): ≈ **+0.071 s**, unos 2 frames a 30 fps.

Es un sesgo acumulativo, no un redondeo, y aplica **solo** a `method="ffmpeg-energy"`. Fix trivial: `hop = frame / sample_rate` y pasar ese valor. Es una segunda fuente de deriva, independiente y mucho menor que la del renderer (ver [[20-Proyectos/34-video-sync/34.05-render-y-sincronizacion/fix-desincronizacion-xfade|Fix desincronizacion xfade]]).

### El `except Exception` desnudo

`:50` traga *todo*: `ImportError` (librosa ausente, el caso previsto), pero también audio corrupto, OOM, incompatibilidad de API de librosa, `IndexError`. Sin logging, sin `warnings.warn`. **El único rastro de que se degradó es el campo `method`** del análisis y del reporte. Un cambio de API de librosa baja la calidad del beat-tracking en silencio.

## `onset_strength`, `_compute_sections` y `energy_level`

`_compute_sections(beats, onset_strength, duration) -> list[dict]` (`:314`).

Guard de entrada (`:317-318`): `if len(onset_strength) < 2` → devuelve la sección única `[{"start": 0.0, "end": duration, "energy_level": "medium"}]`. Es el caso de `manual-bpm` y de los dos fallbacks de rejilla regular.

Ventaneo (`:320-332`), **window = 8 beats** (≈ 2 compases de 4/4):

```python
for i in range(0, min(len(beats), n_onset), window):
    chunk = onset_strength[i : i + window]
    mean_onset = sum(chunk) / len(chunk)
    start = beats[i]
    next_i = min(i + window, len(beats) - 1)
    end = beats[next_i] if i + window < len(beats) else duration
    windows.append({"start": start, "end": end, "mean": mean_onset})
```

Umbrales por **tercios** sobre las medias ordenadas (`:337-340`):

```python
sorted_means = sorted(w["mean"] for w in windows)
n = len(sorted_means)
threshold_low  = sorted_means[max(0, n // 3 - 1)]
threshold_high = sorted_means[min(n - 1, (2 * n) // 3)]
```

Clasificación (`:343-350`): `mean <= threshold_low` → `"low"`; `elif mean >= threshold_high` → `"high"`; si no `"medium"`. **`low` gana los empates** (se evalúa primero, con `<=`).

Reparto real según el número de ventanas:

| `n` | idx low | idx high | Resultado |
|---|---|---|---|
| 1 | `0` | `0` | La única ventana siempre es **`"low"`** (empate → gana `<=`) |
| 2 | `0` | `1` | menor → `low`, mayor → `high`; **nunca `medium`** |
| 3 | `0` | `2` | 1 low / 1 medium / 1 high |
| 6 | `1` | `4` | 2 / 2 / 2 |
| 9 | `2` | `6` | 3 / 3 / 3 (tercios exactos) |

Merge de secciones consecutivas iguales y normalización de bordes (`:352-362`):

```python
merged = [{...raw[0]...}]
for section in raw[1:]:
    if section["energy_level"] == merged[-1]["energy_level"]:
        merged[-1]["end"] = section["end"]     # :356  extiende
    else:
        merged.append({...})                   # :358
merged[0]["start"] = 0.0                       # :360
merged[-1]["end"] = duration                   # :361
```

**Invariantes de salida garantizadas:** lista no vacía; `sections[0]["start"] == 0.0`; `sections[-1]["end"] == duration`; niveles adyacentes siempre distintos; tramos contiguos sin huecos; `energy_level ∈ {"low", "medium", "high"}`.

### Para qué sirven

**`onset_strength` alimenta los cortes adaptativos.** `build_timeline` bifurca según su verdad (`timeline.py:90-95`): si tiene contenido usa `_adaptive_cut_points`, que normaliza por el máximo, calcula percentiles 70/30 y elige el intervalo de corte: **2 beats** si el onset es fuerte, **6** si es débil, `beats_per_cut` en la banda intermedia. Si está vacío, cae al corte uniforme por slice `normalized[beats_per_cut-1::beats_per_cut]`.

**`sections` alimenta el `transition_hint`.** `_transition_hint_for(start_time, sections)` (`timeline.py:324-328`) recorre las secciones y devuelve `"xfade"` **si y solo si** la sección que contiene el `start` del item tiene `energy_level == "low"`; cualquier otro valor da `"cut"`. Ese hint es el que consume `_uses_incoming_xfade` en el renderer. Detalle: la sección única `"medium"` de los fallbacks implica **cero xfades**.

### Defectos verificados de las secciones

- **Todos los `mean` iguales** (p. ej. onsets todos `0.0`) → `threshold_low == threshold_high` → todo cae en `"low"` → una única sección `low` sobre `[0, duration]`. Quien inspeccione el JSON no puede distinguir "canción plana" de "canción de baja energía". Y como `low` implica xfade, ese estado degenerado genera xfades en toda la canción.
- **Una sola ventana → `"low"` forzoso.** Ocurre con ≤ 8 beats detectados (con 9 el `range(0, …, 8)` ya produce dos ventanas).
- Los umbrales son **relativos a la canción**, no absolutos: *toda* canción con ≥3 ventanas tiene ~⅓ de ventanas `"high"` antes del merge.
- `next_i` (`:330`) es redundante: solo se usa cuando `i + window < len(beats)`, y en ese caso `min(i+window, len(beats)-1) == i+window` siempre.

## `detect_emotion()`: las 6 emociones

Firma: `detect_emotion(analysis: AudioAnalysis) -> tuple[str, float]` (`:54`). **`analyze_audio` nunca la llama**: es un paso explícito del CLI (`cli.py:96-97`), condicionado a `not args.no_auto_emotion and not analysis.detected_emotion`.

### Señales (aritmética exacta, `:64-76`)

```python
if onset:
    max_onset = max(onset) or 1.0
    onset_norm = [v / max_onset for v in onset]
    rms_mean_norm = sum(onset_norm) / len(onset_norm)
    variance = sum((v - rms_mean_norm) ** 2 for v in onset_norm) / len(onset_norm)
    rms_variance_norm = variance
else:
    rms_mean_norm = 0.4       # :71  valor sintético
    rms_variance_norm = 0.3   # :72  valor sintético

high_count = sum(1 for s in sections if s.get("energy_level") == "high")
total = len(sections) if sections else 1
high_ratio = high_count / total
```

- Normalización **por el máximo**, no min-max ni z-score. `max(onset) or 1.0` evita la división por cero exacto.
- `variance` es **poblacional** (denominador `n`).
- Como los onsets son no-negativos (librosa rectifica; `_onset_strength_from_envelope` aplica `max(0.0, …)`), `onset_norm ⊂ [0,1]` y por tanto **`rms_variance_norm ≤ 0.25`** (cota de Popoviciu). Esto tiene consecuencias, ver más abajo.
- `high_ratio` cuenta **secciones, no tiempo**: una sección `high` de 3 s pesa igual que una de 90 s.

### Cadena de decisión (`:78-117`), primera coincidencia gana

| # | Guarda exacta | Emoción | Confianza |
|---|---|---|---|
| 1 | `bpm >= 130 and rms_mean_norm > 0.6` | `intense` | `c1=min(1,(bpm-130)/30)`, `c2=min(1,(rms_mean_norm-0.6)/0.4)`, `(c1+c2)/2` |
| 2 | `bpm >= 110 and high_ratio > 0.4` | `happy` | `c1=min(1,(bpm-110)/30)`, `c2=min(1,(high_ratio-0.4)/0.6)`, `(c1+c2)/2` |
| 3 | `bpm >= 110` | `dramatic` | `min(1,(bpm-110)/40)` |
| 4 | `bpm < 80 and rms_variance_norm < 0.2` | `calm` | `c1=min(1,(80-bpm)/20)`, `c2=min(1,(0.2-rms_variance_norm)/0.2)`, `(c1+c2)/2` |
| 5 | `bpm < 80 and rms_mean_norm < 0.35` | `melancholic` | `c1=min(1,(80-bpm)/20)`, `c2=min(1,(0.35-rms_mean_norm)/0.35)`, `(c1+c2)/2` |
| 6 | `bpm < 95 and rms_variance_norm > 0.5` | `sad` | `c1=min(1,(95-bpm)/25)`, `c2=min(1,(rms_variance_norm-0.5)/0.5)`, `(c1+c2)/2` |
| 7 | (else) | `calm` | `0.3` fija |

Post-proceso (`:119-122`):

```python
if not onset:
    confidence *= 0.4                                     # :120
return emotion, round(min(1.0, max(0.0, confidence)), 3)  # :122
```

El vocabulario emitido es exactamente: **`intense`, `happy`, `dramatic`, `calm`, `melancholic`, `sad`**. Los 6 son claves de `MOOD_TAGS` en `asset_selection.py:10-21`, así que la emoción detectada siempre resuelve a un set de tags real en [[20-Proyectos/34-video-sync/34.06-clasificacion-y-seleccion/index|34.06 Clasificación y selección]]: nunca degrada al set vacío por nombre desconocido.

### El castigo `confidence *= 0.4`

Se aplica **después** de calcular la confianza, cuando `onset` está vacío. Es honesto: sin onsets, `rms_mean_norm` y `rms_variance_norm` son los valores sintéticos `0.4`/`0.3`, no medidas del audio: la emoción se infiere solo del BPM y de `high_ratio` (que también es 0.0 sin secciones). El ×0.4 refleja que dos de las tres señales son inventadas.

### El umbral 0.4 del aviso (en `cli.py`, no aquí)

`cli.py:98`:

```python
if analysis.emotion_confidence < 0.4:
    # "... (confidence {c:.0%}, low: consider using --mood to override)."
else:
    # "... (confidence {c:.0%})."
```

**Ambas ramas imprimen a stderr; el umbral solo cambia el texto, no el comportamiento.** No filtra, no descarta la emoción, no cambia el mood efectivo.

### Comportamiento con `--manual-bpm` (colapso determinista)

Con `onset=[]`, `sections=[]` → `high_ratio=0.0`, `rms_mean_norm=0.4`, `rms_variance_norm=0.3`:

- `bpm >= 110` → `dramatic`, `conf = min(1,(bpm-110)/40) * 0.4` → **máximo 0.4**.
- `bpm < 110` → `calm`, `conf = 0.3 * 0.4 = 0.12`.
- Nunca `intense` (0.4 ≯ 0.6), nunca `happy` (0.0 ≯ 0.4), nunca `melancholic` (0.4 ≮ 0.35), nunca `sad`.

Corolario operativo: **con `--manual-bpm` la confianza nunca supera 0.4**. Solo llega a 0.4 exacto cuando `bpm ≥ 150` (el `dramatic` satura en `min(…)=1.0` antes del ×0.4); ahí el guard estricto `< 0.4` de `cli.py:98` **no** dispara el aviso. Para todo `bpm < 150` la confianza queda por debajo de 0.4 y el aviso *"low: consider using --mood to override"* sí se imprime.

### Defectos reales de la cadena

1. **`"sad"` es código muerto.** La rama 6 exige `rms_variance_norm > 0.5`, pero la varianza de valores normalizados a `[0,1]` está acotada por `0.25`. Y el valor sintético sin onsets es `0.3`, tampoco `> 0.5`. **Ningún input posible activa esta rama.**
2. **Zona muerta de BPM `[95, 110)`.** Las ramas 4/5 exigen `bpm < 80`, la 6 `bpm < 95`, las 1–3 `bpm >= 110`. Toda canción entre 95 y 110 BPM cae en el `else` → `calm` con confianza fija `0.3`. Es un rango de tempo muy poblado (baladas mid-tempo): y `0.3 < 0.4`, así que siempre dispara el aviso.
3. **`"melancholic"` solo existe en la franja `0.2 ≤ rms_variance_norm ≤ 0.25`** (la rama 4 captura antes todo lo de varianza `< 0.2`). Ventana estrechísima: requiere onsets casi bimodales. En la práctica es casi tan muerta como `"sad"`.
4. **`"intense"` exige *ambas* condiciones.** Una canción a 160 BPM con `rms_mean_norm = 0.5` no es `intense`: cae en la rama 2 o 3.
5. **`high_ratio` mide fragmentación, no energía.** Los umbrales de `_compute_sections` son tercios relativos, y el merge colapsa los `high` consecutivos (bajando el ratio contado por secciones). La señal de `happy` es estructuralmente débil.
6. **Guarda redundante** en la rama 4 (`:100`): `if rms_variance_norm < 0.2 else 0.0`: la condición ya está garantizada por el `elif` de `:97`; el `else 0.0` nunca se evalúa.

## Campos exactos de `AudioAnalysis` y del JSON

`@dataclass` en `:14-30`. No es `frozen`, no valida nada, es mutable a propósito.

| Campo | Tipo | Default | Quién lo escribe |
|---|---|---|---|
| `audio_path` | `str` |: (requerido) | Este módulo; absoluto y resuelto. Es **el analizado** (el recortado, si hubo trim) |
| `duration` | `float` | — | Este módulo |
| `bpm` | `float` | — | Este módulo |
| `beats` | `list[float]` | — | Este módulo |
| `method` | `str` | — | Este módulo: `manual-bpm` \| `librosa` \| `ffmpeg-energy` |
| `source_audio_path` | `str \| None` | `None` | **`cli.py:91`**: el original |
| `source_audio_start` | `float \| None` | `None` | **`cli.py:92`** |
| `source_audio_end` | `float \| None` | `None` | **`cli.py:93`** |
| `onset_strength` | `list[float]` | `field(default_factory=list)` | Este módulo |
| `sections` | `list[dict]` | `field(default_factory=list)` | Este módulo |
| `detected_emotion` | `str` | `""` | **`cli.py:97`** |
| `emotion_confidence` | `float` | `0.0` | **`cli.py:97`** |

`to_json()` (`:29-30`) es `asdict(self)` recursivo: `sections` se copia tal cual, con las claves `start`, `end`, `energy_level`.

### `audio_analysis.json`

`write_analysis(path, analysis)` (`:125-127`) es la **única escritura de disco** del módulo (aparte del WAV temporal):

```python
path.parent.mkdir(parents=True, exist_ok=True)
path.write_text(json.dumps(analysis.to_json(), indent=2) + "\n", encoding="utf-8")
```

`indent=2`, newline final, UTF-8 explícito. Escritura **no atómica** (sin tmp+rename): un fallo a medias deja el JSON truncado. Sin `ensure_ascii=False` → los caracteres no-ASCII de las rutas se escapan como `\uXXXX`.

Forma del archivo (12 claves, el mismo orden del dataclass):

```json
{
  "audio_path": "/abs/path/work/trimmed_audio.wav",
  "duration": 165.3521,
  "bpm": 128.47,
  "beats": [0.4644, 0.9288, 1.3932],
  "method": "librosa",
  "source_audio_path": "/abs/path/proyecto/song.mp3",
  "source_audio_start": 12.5,
  "source_audio_end": 45.0,
  "onset_strength": [2.7183, 1.4142],
  "sections": [
    {"start": 0.0, "end": 32.1, "energy_level": "low"},
    {"start": 32.1, "end": 165.3521, "energy_level": "high"}
  ],
  "detected_emotion": "melancholic",
  "emotion_confidence": 0.62
}
```

Destino real: `outputs/audio_analysis.json` (`cli.py:232`) o `<output_dir>/{safe_name}{range_suffix}_audio_analysis.json` (`cli.py:247`).

Este mismo dict es lo que `build_timeline` copia entero a `Timeline.audio` (`timeline.py:257`), y de ahí lo lee `report.py` (`duration`, `bpm`, `method` por clave dura) y el renderer (`timeline.audio["audio_path"]`).

## `probe_duration()`

```bash
ffprobe -v error -show_entries format=duration -of default=noprint_wrappers=1:nokey=1 <path>
```

`subprocess.check_output(cmd, text=True).strip()` → `float(output)` (`:147-159`).

- Devuelve la duración del **contenedor** (`format=duration`), no la del stream decodificado. En MP3/VBR puede diferir ligeramente.
- **`float(output)` revienta con `ValueError`** si ffprobe imprime `N/A` o cadena vacía. Sin try/except.
- `check_output` **no redirige stderr** → los errores de ffprobe sí llegan a la consola. Inconsistente con `trim_audio` y `_analyze_with_energy`, que los descartan a `DEVNULL`.
- Solo se invoca desde `:39` (rama `manual-bpm`) y `:224` (fallback si `sample_rate == 0`, prácticamente inalcanzable con un WAV válido de ffmpeg).
- **Homónimo, no compartido:** existe un `_probe_duration` separado en `renderer.py:243`. Dos implementaciones de la misma idea en el mismo paquete.

## Relación con el resto del pipeline

Verificado por grep: este módulo **no contiene** ninguna referencia a `xfade`, `extra_head`, `_fragment_offset`, `_uses_incoming_xfade` ni `drift`. Todo eso vive en `renderer.py`. Lo que este bloque aporta es el contrato temporal:

- Los tiempos de `beats[]` son **absolutos y no acumulativos**: `beats[i]` no se deriva sumando duraciones de items. Por eso el cronograma de la canción nunca se acorta, aunque el vídeo renderizado sí lo hiciera: es la raíz del razonamiento del fix de xfade.
- **`beats[0]` es `0.0` solo en los caminos sintéticos** (`_regular_beats`). En `librosa` y `ffmpeg-energy` el primer beat detectado puede estar en, p. ej., `0.4644`. Un consumidor que asuma `beats[0] == 0.0` se equivoca en los caminos detectados. (`_cut_points` y `_adaptive_cut_points` lo resuelven anteponiendo `0.0` explícitamente.)

## Constantes mágicas

Todas son literales inline; **ninguna es constante de módulo**.

| Valor | Cita | Significado |
|---|---|---|
| `0.4` / `0.3` | `:71`, `:72` | `rms_mean_norm` / `rms_variance_norm` sintéticos sin onsets |
| `130`, `0.6`, `30`, `0.4` | `:81-84` | umbrales/escalas de `intense` |
| `110`, `0.4`, `30`, `0.6` | `:87-90` | umbrales/escalas de `happy` |
| `110`, `40` | `:93-95` | umbrales/escala de `dramatic` |
| `80`, `0.2`, `20` | `:97-100` | umbrales/escalas de `calm` |
| `80`, `0.35`, `20` | `:103-106` | umbrales/escalas de `melancholic` |
| `95`, `0.5`, `25` | `:109-112` | umbrales/escalas de `sad` (**inalcanzable**) |
| `0.3` | `:117` | confianza fija del `else` |
| `0.4` | `:120` | penalización de confianza sin onsets |
| `3` | `:122` | decimales del `round` de confianza |
| `4` | `:173` | mínimo de beats de librosa antes del fallback |
| `120` | `:174`, `:231-232` | BPM de fallback (dos sitios distintos) |
| `22050`, `1` | `:210-214` | `-ar` / `-ac` del ffmpeg de decodificación |
| `0.046` | `:225`, `:226`, `:227` | `frame_seconds` / `hop_seconds` (**triplicado**) |
| `2` | `:255` | sample width exigido (bytes) |
| `32768.0` | `:261`, `:265` | normalización int16 → float |
| `8` | `:281` | mínimo de frames de envelope |
| `0.55` | `:286` | multiplicador de σ del umbral de picos |
| `0.24` | `:287` | gap mínimo entre picos (s) → tope 250 BPM |
| `8` | `:321` | beats por ventana de sección |
| `0.25`, `2.0` | `:369` | filtro de intervalos (s) → `[30, 240]` BPM |
| `80`, `180` | `:376-379` | octava de folding del BPM |

**Inconsistencia:** `_pick_energy_peaks` usa `min_gap = 0.24` (`:287`) pero `_estimate_bpm` filtra intervalos `>= 0.25` (`:369`). Los intervalos en `[0.24, 0.25)` que sí puede generar el picker se descartan al estimar el BPM. Ventana estrecha, pero los dos números deberían ser el mismo.

## Cobertura de tests

**No existe `tests/test_audio_analysis.py`.** Este módulo no tiene tests propios; `tests/test_timeline.py:4` solo importa `AudioAnalysis` como fixture. Ver [[20-Proyectos/34-video-sync/34.08-pruebas-y-verificacion/index|34.08 Pruebas y verificacion]].

## Enlaces

- [[20-Proyectos/34-video-sync/index|Video Sync]]
- [[20-Proyectos/34-video-sync/34.04-timeline-y-cortes/index|34.04 Timeline y cortes]]: `beats[]` y `onset_strength[]` alimentan los cortes; `sections[]` alimenta el `transition_hint`
- [[20-Proyectos/34-video-sync/34.06-clasificacion-y-seleccion/index|34.06 Clasificación y selección]]: `detected_emotion` alimenta el mood efectivo y el ranking de assets
- [[20-Proyectos/34-video-sync/34.05-render-y-sincronizacion/fix-desincronizacion-xfade|Fix desincronizacion xfade]]: la deriva del renderer, independiente de la de `hop_seconds`
- [[20-Proyectos/34-video-sync/34.07-uso-y-flags/index|34.07 Uso y flags]]: `--audio-start`, `--audio-end`, `--manual-bpm`, `--no-auto-emotion`, `--mood`
