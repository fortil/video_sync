---
tema: visión y estado del proyecto
tipo: nota
proyecto: video-sync
creada: 2026-07-02
actualizada: 2026-07-02
estado: activa
aliases: [Visión y estado, Estado de video-sync]
tags: [video-sync, vision, estado, arquitectura]
---

# 34.01 Visión y estado

Fuente: `AGENTS.md`, `LICENSE`, `synced_edit/cli.py` y `README.md` del repo.

## Objetivo

Producir ediciones de video sincronizadas al beat a partir de **medios locales** aportados por el usuario: una canción y carpetas de imágenes/videos. El pipeline analiza el audio, decide los cortes, ordena los assets por afinidad emocional y renderiza con FFmpeg. Nada se descarga de internet salvo las llamadas a la API de OpenAI del clasificador, que sirven para etiquetar assets, no para obtener media.

El diseño prioriza la **revisabilidad**: el `timeline.json` intermedio es determinista y editable, así que el timing se puede inspeccionar, ajustar y volver a renderizar sin re-analizar.

## Qué existe hoy

Dos pipelines que se unifican en un solo CLI.

| Módulo | Responsabilidad |
|---|---|
| `synced_edit/audio_analysis.py` | Beats, BPM, secciones de energía, emoción detectada |
| `synced_edit/timeline.py` | Cortes, asignación de assets, `transition_hint` |
| `synced_edit/asset_selection.py` | Ranking por mood, interleave imagen/video |
| `synced_edit/classifier.py` | Etiquetado de assets vía OpenAI (incremental) |
| `synced_edit/renderer.py` | Clips, ensamblado (xfade o concat), mux de audio |
| `synced_edit/report.py` | Reporte Markdown de la corrida |
| `synced_edit/cli.py` | Orquestación y resolución de rutas |

Entrada del CLI (verificado en `cli.py:260-261`):

```bash
python3 -m synced_edit.cli --project-folder /ruta/al/proyecto
```

`src/synced_edit.py` es un shim que importa `synced_edit.cli:main`, pero comparte nombre con el paquete: ejecutarlo directo (`python src/synced_edit.py`) puede auto-importarse en vez de resolver el paquete. Usar siempre `python -m synced_edit.cli` desde la raíz.

Detalle de cada bloque en [[20-Proyectos/34-video-sync/34.02-arquitectura/index|34.02 Arquitectura]].

## Artefactos de cada corrida

Cada render produce **cuatro** salidas. Las rutas dependen de si se pasa `--project-folder`.

| Artefacto | Sin `--project-folder` | Con `--project-folder` |
|---|---|---|
| Análisis de audio | `outputs/audio_analysis.json` | `<project>/output/{safe_name}{range_suffix}_audio_analysis.json` |
| Timeline | `outputs/timeline.json` | `<project>/output/{safe_name}{range_suffix}_timeline.json` |
| Video final | `outputs/final.mp4` | `<project>/output/{safe_name}{range_suffix}.mp4` |
| Reporte Markdown | `outputs/reporte_{YYYY-MM-DD}_synced_edit.md` | `<project>/output/reporte_{YYYY-MM-DD}_{safe_name}.md` |

Donde (`cli.py:235-248`):

- `safe_name = project.name.replace(" ", "_")`: **solo** reemplaza espacios; otros caracteres pasan tal cual.
- `range_suffix` viene de `_range_suffix(args.audio_start, args.audio_end)` (`cli.py:252-257`): vacío si `start in {"0", "0.0", "0:00"}` y `end is None`; si no, `_{start}_{end}` con `:` → `-` y `.` → `_` (ej. `_1-30_end`).
- La carpeta con project folder es `output/` en **singular**, no `outputs/`.

Cualquiera de las cuatro rutas se puede sobreescribir con `--analysis`, `--timeline`, `--output`, `--report`.

Diagrama del flujo:

```
song.mp3 ──> analyze_audio ──> audio_analysis.json
                  │
                  ├──> build_timeline ──> timeline.json
images/ ──┐       │         ▲
videos/ ──┴──> select_assets┘
                  │
                  └──> render_timeline ──> final.mp4
                            │
                            └──> write_report ──> reporte_*.md
```

## Reglas del proyecto (`AGENTS.md`)

Cinco reglas, transcritas del archivo:

1. Python para la planificación de medios, FFmpeg para el render final.
2. Detección de beats, generación de timeline y render como módulos separados.
3. No descargar audio ni video con copyright de YouTube o YouTube Music.
4. Las entradas deben ser archivos locales aportados por el usuario.
5. Cada render debe producir: `outputs/timeline.json`, `outputs/final.mp4` y un reporte Markdown corto.

Cumplimiento verificado contra el código: las reglas 1, 2 y 4 se cumplen (separación real en módulos, render por subprocess a `ffmpeg`, todas las entradas son `Path` locales). La regla 3 no tiene código de descarga que la viole.

La **regla 5 se cumple solo en el modo sin `--project-folder`**. Con project folder las rutas son `<project>/output/` (singular) con nombres derivados del proyecto, no `outputs/timeline.json` ni `outputs/final.mp4`. Además, `.gitignore` ignora `outputs/` (plural) pero **no** `output/`: un proyecto dentro del repo deja sus `_timeline.json`, `_audio_analysis.json` y `reporte_*.md` trackeables (los `.mp4` sí caen por el patrón global `*.mp4`). El flag `--skip-render` es otra excepción: termina sin producir `.mp4` pero igual escribe el reporte.

Los flags y su semántica están en [[20-Proyectos/34-video-sync/34.07-uso-y-flags/index|34.07 Uso y flags]]; la receta probada con carpeta de proyecto en [[20-Proyectos/34-video-sync/34.07-uso-y-flags/flujo-por-carpeta-de-proyecto|Flujo por carpeta de proyecto]].

## Licencia

MIT. `Copyright (c) 2026 William Penagos` (`LICENSE:3`). Texto MIT estándar sin modificaciones.

## Estado actual

El **fix de desincronización xfade está aplicado y verificado**. El bug nació en `e3438d2` (creación de `_build_xfade_filtergraph`), sobrevivió a `cc7c84e` y `295ef0c`, y está corregido en el repo: `renderer.py:426` acumula `cumulative += item.duration` **sin restar** `_XFADE_DURATION`, y los clips entrantes se renderizan 0.25 s más largos para pagar el solape. La duración real del video ensamblado vuelve a igualar `Σ item.duration`, es decir, el cronograma de la canción.

Se añadió `_warn_if_duration_drifted` (`renderer.py:256-273`), que compara la duración real del `silent.mp4` contra la suma programada y avisa a `stderr` si la diferencia supera `_DURATION_DRIFT_TOLERANCE = 0.15` s. Solo advierte: no aborta ni corrige.

El detalle completo del fix, su identidad algebraica y la medición empírica están en [[20-Proyectos/34-video-sync/34.05-render-y-sincronizacion/fix-desincronizacion-xfade|Fix desincronizacion xfade]].


## Deuda y límites conocidos

**Cobertura de tests limitada a timeline + xfade.** Existen `tests/test_timeline.py` (3 tests, sin cambios desde el commit inicial `9648c07`) y `tests/test_renderer.py` (2 tests, añadido con el fix de xfade). Los 5 pasan, desde la raíz del repo:

```bash
python3 -m pytest tests/ -q     # 5 passed
```

Nótese `python3 -m pytest`, no `pytest` pelado: no hay `conftest.py`, `pytest.ini`, `pyproject.toml` ni `setup.cfg`, así que el ejecutable `pytest` no inyecta el CWD en `sys.path` y falla con `ModuleNotFoundError: No module named 'synced_edit'`. `pytest` tampoco está declarado en `requirements.txt`.

**No hay tests end-to-end de render.** Nada ejercita `render_timeline` contra media real: los tests de renderer solo componen strings de filtergraph con `_XFADE_AVAILABLE` monkeypatcheado, sin invocar FFmpeg. Tampoco hay `tests/test_audio_analysis.py`, ni tests de `classifier.py`, `asset_selection.py`, `report.py` ni `cli.py`. Ver [[20-Proyectos/34-video-sync/34.08-pruebas-y-verificacion/index|34.08 Pruebas y verificacion]].

Otros límites verificados:

| Límite | Dónde |
|---|---|
| `--focus face` es el default pero `opencv-python-headless` está comentado en `requirements.txt` → instalación limpia degrada a `center`, avisando una sola vez por `stderr` (`_FACE_WARNED`) | `cli.py:47` vs `requirements.txt:16`; aviso en `renderer.py:591-600` |
| El reporte cuenta xfades por `transition_hint`, sin aplicar los filtros reales del renderer → sobreestima; con >50 items informa xfades inexistentes | `report.py:24-26` vs `renderer.py:357-374` |
| `--mood` default `"bittersweet"` siempre gana sobre la emoción auto-detectada; hay que pasar `--mood ""` para usar la detección | `cli.py:52,111` |
| El reporte con project folder no incluye `range_suffix` → dos rangos del mismo proyecto el mismo día se pisan el `.md` | `cli.py:248` |
| `Path("work")` hardcodeado y relativo al CWD; `work/trimmed_audio.wav` de nombre fijo → colisión entre renders paralelos | `cli.py:87,163` |
| El clasificador (que gasta llamadas a la API) corre antes de validar los timecodes → un `--audio-start` inválido aborta después de pagar | `cli.py:81-83` |
| Comentario obsoleto en `_mux_audio` describe un desfase real: con `mix_video_audio=True` y `fragment != 0`, imagen y audio del clip salen de momentos distintos de la fuente | `renderer.py:164-167` vs `renderer.py:516-519` |
| Deriva de `+0.03 %` en `method="ffmpeg-energy"` por `hop_seconds=0.046` ≠ `1014/22050` (≈ +0.07 s a los 4 min): independiente del bug de xfade | `audio_analysis.py:225-227` |
| El README no menciona xfade en absoluto: ni la duración de 0.25 s, ni el cap de 50 clips, ni el aviso de deriva | `README.md` |
| El endpoint del clasificador está fijo en `https://api.openai.com/v1/responses`; no hay variable para apuntar a otro servidor compatible con la API de OpenAI (por ejemplo un modelo de visión local). Antes de cambiarlo, validar los 24 tags con fixtures | `classifier.py:287` |

El README sí documenta correctamente los 32 flags del CLI con sus defaults exactos (contrastado 1:1 contra `cli.py`).

## Notas relacionadas

- [[20-Proyectos/34-video-sync/index|Video Sync]]
- [[20-Proyectos/34-video-sync/34.02-arquitectura/index|34.02 Arquitectura]]
- [[20-Proyectos/34-video-sync/34.03-analisis-de-audio/index|34.03 Analisis de audio]]
- [[20-Proyectos/34-video-sync/34.04-timeline-y-cortes/index|34.04 Timeline y cortes]]
- [[20-Proyectos/34-video-sync/34.05-render-y-sincronizacion/index|34.05 Render y sincronizacion]]
- [[20-Proyectos/34-video-sync/34.06-clasificacion-y-seleccion/index|34.06 Clasificacion y seleccion]]
