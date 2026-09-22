---
tema: edición de video sincronizada a la música
tipo: índice
proyecto: video-sync
creada: 2026-07-02
actualizada: 2026-09-22
estado: activa
aliases: [Video Sync, video_sync]
tags: [video-sync, ffmpeg, python, edicion-video]
---

# Video Sync: Proyecto

`video_sync` monta videos editados al ritmo de una canción a partir de media local (fotos y clips) más un archivo de audio local. Une dos pipelines que antes vivían separados: un **clasificador de assets con IA** (`synced_edit.classifier`, modelo de visión de OpenAI vía HTTP crudo, escribe un `assets.json` que mapea ruta relativa → tags) y un **renderer sincronizado a los beats** (`synced_edit.cli`, que analiza el audio, construye un `timeline.json` determinista y renderiza con FFmpeg). Están cableados: si apuntas el renderer a una carpeta de proyecto sin `assets.json`, clasifica primero: de forma incremental, solo los assets nuevos: y sigue de largo hasta el render, así que una carpeta de media cruda se convierte en un corte terminado con un solo comando. El cronograma manda: `audio_analysis.py` produce los beats y la duración en tiempo-canción, `timeline.py` los reparte en items, y el renderer tiene que encajar el video en ese cronograma, nunca al revés.

**Fuente de la verdad:** el repositorio público `fortil/video_sync`: código en `synced_edit/` y `tests/`, documentación en `docs/20-Proyectos/34-video-sync/`. Estas notas son su espejo documental; ante discrepancia, manda el repo.

**Estado (2026-07-02):** bug de desincronización por xfade corregido y verificado (5/5 tests pasan; el re-render del proyecto de ejemplo `demo` da 105.97 s frente a los 106.0 s esperados). El clip entrante de cada crossfade ahora se renderiza 0.25 s más largo y `cumulative` deja de restar la duración del xfade, así que el solape lo paga el padding y no el cronograma. Detalle completo en [[20-Proyectos/34-video-sync/34.05-render-y-sincronizacion/fix-desincronizacion-xfade|Fix desincronizacion xfade]].

```text
media local + canción
        │
        ├─ classifier.py  ──▶ assets.json (tags por asset, incremental)
        │
        ├─ audio_analysis.py ──▶ beats, bpm, duration, sections, emoción
        │        │
        │        └─▶ timeline.py ──▶ cut points ──▶ items (start/end/duration/effect/transition_hint)
        │                                │
        │        asset_selection.py ─────┘  (orden smart por mood + tags)
        │
        └─ renderer.py ──▶ clips ffmpeg ──▶ xfade | concat ──▶ silent.mp4 ──▶ mux audio ──▶ .mp4
                                                                    │
                                                          report.py ──▶ reporte_*.md
```

## Bloques

- [[20-Proyectos/34-video-sync/34.01-vision-y-estado/index|34.01 Vision y estado]]: qué resuelve el proyecto, estado vigente, decisiones de diseño y deuda conocida.
- [[20-Proyectos/34-video-sync/34.02-arquitectura/index|34.02 Arquitectura]]: separación en módulos (`audio_analysis` / `timeline` / `renderer` / `classifier` / `asset_selection` / `cli`), contratos entre ellos y divergencias entre defaults de librería y de CLI.
- [[20-Proyectos/34-video-sync/34.03-analisis-de-audio/index|34.03 Analisis de audio]]: detección de beats, BPM y secciones por los tres caminos (`librosa`, `ffmpeg-energy`, `manual-bpm`), y la inferencia de emoción desde onsets y energía.
- [[20-Proyectos/34-video-sync/34.04-timeline-y-cortes/index|34.04 Timeline y cortes]]: cut points adaptativos según onset, piso de 0.35 s, asignación de assets por rondas y reglas de variedad (`--max-asset-uses`, fragmentos de video).
- [[20-Proyectos/34-video-sync/34.05-render-y-sincronizacion/index|34.05 Render y sincronizacion]]: render por clip con FFmpeg, filtergraph de xfade, mezcla de audio de clips sobre la canción y el aviso de deriva de duración.
- [[20-Proyectos/34-video-sync/34.06-clasificacion-y-seleccion/index|34.06 Clasificacion y seleccion]]: clasificador incremental contra la API de OpenAI, vocabulario de 24 tags y scoring por mood que ordena los assets en modo `smart`.
- [[20-Proyectos/34-video-sync/34.07-uso-y-flags/index|34.07 Uso y flags]]: CLI completo, defaults reales tomados del código, resolución de rutas de salida y timecodes.
- [[20-Proyectos/34-video-sync/34.08-pruebas-y-verificacion/index|34.08 Pruebas y verificacion]]: qué cubren los tests, cómo correrlos y qué queda verificado solo por lectura del código.

## Arranque rápido

Mínimo real (`--audio` es obligatorio si no hay `--project-folder`; `--assets` ya trae por defecto `assets/images assets/videos`):

```bash
python3 -m synced_edit.cli --audio assets/audio/song.mp3
```

Escribe `outputs/audio_analysis.json`, `outputs/timeline.json`, `outputs/final.mp4` y `outputs/reporte_<YYYY-MM-DD>_synced_edit.md`.

Con carpeta de proyecto (`song.mp3`, `images/`, `videos/` dentro):

```bash
python3 -m synced_edit.cli \
  --project-folder /ruta/al/proyecto \
  --audio-start 0:18 \
  --audio-end 0:40 \
  --selection smart \
  --mood sad
```

Aquí las salidas van a `<proyecto>/output/` (singular) con nombres derivados del proyecto: `{nombre}{sufijo_rango}.mp4`, `{nombre}{sufijo_rango}_timeline.json`, `{nombre}{sufijo_rango}_audio_analysis.json` y `reporte_<YYYY-MM-DD>_{nombre}.md`.

Invoca siempre por `python3 -m synced_edit.cli`: `src/synced_edit.py` comparte nombre con el paquete `synced_edit/` y ejecutarlo directo arriesga un self-import.

Para la receta probada con carpeta de proyecto (rangos de audio y combinación de flags), ver [[20-Proyectos/34-video-sync/34.07-uso-y-flags/flujo-por-carpeta-de-proyecto|Flujo por carpeta de proyecto]].

## Requisitos

| Requisito | Cuándo hace falta |
|---|---|
| Python 3.11+ | Siempre. |
| `ffmpeg` en `PATH` | Siempre: render, recorte de audio y extracción de frames para clasificar. También `ffprobe` (el renderer lo llama sin verificarlo antes). |
| `pip install -r requirements.txt` | `librosa` / `numpy` / `soundfile` mejoran la detección de beats; son opcionales: sin ellas `analyze_audio` cae al analizador de energía por ffmpeg **en silencio** (la única pista es el campo `method`). `certifi` hace que el HTTPS a OpenAI verifique bien en los builds de Python de macOS. |
| `OPENAI_API_KEY` | Solo si el clasificador corre de verdad: hace falta `--project-folder`, no pasar `--no-auto-classify`, y que queden assets sin tag. Si la carpeta ya está clasificada entera, no se pide key ni se toca la red. |

`opencv-python-headless` está **comentado** en `requirements.txt`, pero `--focus face` es el default del CLI: en una instalación limpia el encuadre por cara degrada a centrado (aviso una sola vez por proceso).

## Restricción del proyecto

`AGENTS.md` es explícito: **no descargar audio ni video con copyright de YouTube o YouTube Music**. Las entradas son siempre archivos locales que aporta el usuario. El código lo cumple: no hay ningún camino de descarga de media: la única salida a red es la API de OpenAI del clasificador, y solo para pedir *tags*, nunca media.

Nota de divergencia verificada: `AGENTS.md` exige que todo render produzca `outputs/timeline.json` y `outputs/final.mp4`, pero el modo `--project-folder` escribe en `<proyecto>/output/` (singular) con nombres derivados del proyecto, y `--skip-render` termina sin `.mp4`. El reporte Markdown sí se genera siempre.
