# Documentación del proyecto (espejo para Obsidian)

Esta carpeta contiene la documentación técnica de `video_sync` organizada con Johnny Decimal sobre buckets PARA, para poder copiarla a un vault de Obsidian sin retrabajo.

## Cómo usarla

Copia `20-Proyectos/34-video-sync/` dentro de la carpeta `20-Proyectos/` de tu vault. Los wikilinks usan rutas completas desde la raíz del vault (`[[20-Proyectos/34-video-sync/...]]`), así que resuelven en cuanto la carpeta está en su sitio. También se leen bien en GitHub como Markdown normal.

`34` es el ID que este proyecto ocupa en el vault del autor. Si ese vault renumera, el espejo se renueva en el siguiente sync. No hay enlaces relativos que arreglar.

## Contenido

| Ruta | Qué es |
|---|---|
| `34-video-sync/index.md` | Mapa del proyecto: resumen, estado, bloques, arranque rápido |
| `34.01-vision-y-estado/` | Qué resuelve, decisiones de diseño, deuda conocida |
| `34.02-arquitectura/` | Módulos y contratos entre ellos |
| `34.03-analisis-de-audio/` | Beats, BPM, secciones, emoción |
| `34.04-timeline-y-cortes/` | Cut points, asignación de assets, reglas de variedad |
| `34.05-render-y-sincronizacion/` | Render con FFmpeg, xfade, postmortem de la deriva |
| `34.06-clasificacion-y-seleccion/` | Clasificador con IA, vocabulario de tags, scoring por mood |
| `34.07-uso-y-flags/` | CLI completo, defaults reales, receta probada con carpeta de proyecto |
| `34.08-pruebas-y-verificacion/` | Qué cubren los tests y cómo correrlos |

Los ejemplos usan una carpeta de proyecto ficticia (`~/proyectos/demo`); ninguno corresponde a un render real.
