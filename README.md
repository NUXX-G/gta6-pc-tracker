# GTA VI PC Tracker

**🌐 https://nuxx-g.github.io/gta6-pc-tracker/** · [Español](#español) · [English](#english)

Live, bilingual and spoiler-free status of the open-source projects trying to run the Xbox Series build of GTA VI on PC.
Seguimiento en directo, bilingüe y sin spoilers de los proyectos open source que intentan ejecutar en PC la versión de Xbox Series de GTA VI.

> ⚠️ **No existe GTA 6 oficial para PC. There is no official GTA 6 for PC.** Cualquier "descarga de GTA 6 PC" es una estafa o malware. Any "GTA 6 PC download" is a scam or malware.

---

## Español

### Qué es
Una web estática que responde, cada hora, a: *¿Se puede jugar GTA 6 en PC? ¿Qué proyectos hay, qué hace cada uno, cuánto llevan, qué pinta bien, qué pinta mal, cuánto les falta y dónde leer más?*

### En qué se diferencia
- Bilingüe ES/EN de verdad (`i18n/es.yml`, `i18n/en.yml`), con SEO, hreflang y RSS por idioma.
- Actualización **cada hora**, 100 % gratuita: solo GitHub Actions + GitHub Pages. Sin servidores, sin claves, sin IA en ejecución.
- Ficha profunda por proyecto con lo que pinta bien y lo que pinta mal, **cada punto con su evidencia enlazada**.
- Credibilidad por fuente, detección de clones y malware, compatibilidad de GPU y **cero spoilers**.
- **20 proyectos** seguidos (incluida la build privada CGBJ), descubiertos también a partir de otros trackers (GTA VI PC Race y PortWatch VI), cada uno con su artículo, su enlace al repo y la etapa que le da cada tracker.
- **Galería de imágenes y vídeos** de todas las fuentes (READMEs de los proyectos, trackers, YouTube, Mastodon, Lemmy, Reddit), etiquetados por origen y credibilidad. Nada externo se carga hasta que el visitante pulsa.
- **Prensa**: titulares de medios en ES/EN que pasan los filtros legal y de spoilers, más selección propia.
- **Contadores en directo**: tiempo desde el lanzamiento en consolas y desde la publicación del ejecutable de Xbox (sin enlazarlo).
- **Guías**: "Cómo funciona" (emulador vs. capa de compatibilidad vs. recompilación, shaders, fases) y "Descargas falsas" (cómo reconocer estafas y qué hacer si has picado).

### Cómo funciona
`scripts/run_all.py` (cron `17 * * * *`):
1. **Recolecta**: API de GitHub (`collect_github.py`), imágenes y vídeos de READMEs, trackers y YouTube sin clave (`collect_media.py`), registro DMCA público (`collect_dmca.py`), radar de repos nuevos (`collect_discovery.py`), Reddit JSON/RSS (`collect_reddit.py`), Hacker News, Bluesky, Mastodon y Lemmy (`collect_social.py`), Google News + RSS de medios (`collect_news.py`) y trackers externos (`collect_trackers.py`). Si una fuente falla, el resto sigue y la web lo indica.
2. **Filtra**: `legal_filter.py` (allowlist de dominios + palabras/extensiones prohibidas; descarta el elemento entero) y `spoiler_filter.py`.
3. **Clasifica**: `credibility.py`, `clone_detector.py`.
4. **Analiza**: `analyze.py` (fases, %, áreas de commits, señales, GPU, estimaciones, diffs e historial).
5. **Genera**: `summarize.py` (plantillas ES/EN, sin IA), `build_site.py` (Jinja2 con autoescape) y `make_og_image.py`.

Páginas: inicio, ficha por proyecto, `como-funciona/`, `descargas-falsas/`, `galeria/`, `noticias/` y `estado/` (y sus equivalentes en inglés). Los textos largos están en `content/*.yml`.

Datos en `data/`: `latest.json` (incluye `media_items`, `press`, `external` y `milestones`), `candidates.json`, `sources_health.json`, `events.json` y `history/` (7 días horarios + resumen diario + cambios de fase). Los textos de terceros de foros y redes **nunca** se guardan ni se publican: solo metadatos, números y títulos generados por plantilla. Excepción: los **titulares de prensa** (solo medios de la allowlist, solo si hablan de PC/ports y pasan los filtros legal, de spoilers y de exclusión), configurable con `news.show_press_headlines`.

Modo sin red: `python scripts/run_all.py --offline` (usa `tests/fixtures/`).

### Fuentes
GitHub REST (con el `GITHUB_TOKEN` automático), `github/dmca`, búsqueda de GitHub, Reddit, Hacker News (Algolia), Bluesky, Mastodon (mastodon.social), Lemmy (lemmy.world), Google News RSS, RSS de medios y los trackers [GTA VI PC Race](https://vuyp.github.io/gta6-pc-race/), [PortWatch VI](https://portwatch-vi.xyz/) y [mtwildwood](https://gta.mtwildwood.net/gta-6-pc). El estado de cada una se ve en la página **Estado del sistema**.

### Reglas de credibilidad (`config/credibility.yml`)
- 🟢 **Confirmado**: README, commit o release del propio proyecto; post del propio dev; o reproducido por un tracker.
- 🟡 **Comunidad**: prensa especializada o hilos con evidencia (enlace al repo seguido, o post muy votado que enlaza código).
- 🔴 **Sin verificar**: posts anónimos o lenguaje de rumor.
- ⛔ **Probablemente falso**: rumor + algo imposible según el estado conocido (p. ej. "jugable a 60 fps" cuando nadie ha pasado de la fase 5). No cuenta y sale tachado en Alertas.

### Fases y porcentaje (`config/scoring.yml`)
| Fase | "El golpe" | Técnico | % |
|---|---|---|---|
| 1 | Reconocimiento | shaders y assets | 0–25 (completa = 20) |
| 2 | Entrar | el exe arranca | 25 |
| 3 | Primer vistazo | primer frame | 40 |
| 4 | Menús | menús | 55 |
| 5 | Dentro del mapa | carga el mundo | 70 |
| 6 | Luces encendidas | mundo iluminado | 80 |
| 7 | Golpe completado | ~30 fps / estable | 90 / 100 |

Una fase **parcial** cuenta a medio camino entre la anterior y esa. La fase se detecta por palabras clave y por la tabla de estado del README, pero **la fase manual de `projects.yml` manda**. Si el README apunta más alto que la fase confirmada, sale el aviso *"Afirmación del propio proyecto, pendiente de reproducción"* hasta que un tracker lo respalde. Las estimaciones son reglas con rangos amplios, siempre etiquetadas como orientativas.

### Cómo editar
- **Proyectos**: `config/projects.yml` (nombre, slug, repo —`null` para builds privadas, con `link` a la fuente—, `kind`, `based_on`, artículo `article_es/article_en`, textos ES/EN, fase manual, puntos fuertes/débiles con evidencia). La tabla "Lo que siguen otros trackers" muestra qué proyectos de los trackers aún no están en esta lista.
- **Contadores**: `config/settings.yml → milestones`. **Galería**: `config/settings.yml → media`. Los candidatos del radar nunca entran solos: se promocionan añadiéndolos aquí.
- **Enlaces curados**: `config/curated_links.yml` (solo dominios de `config/allowlist.yml`, títulos neutros escritos a mano).
- **Spoilers**: `config/spoiler_terms.yml`. **Lista negra**: `config/blocklist.yml`. **Allowlist**: `config/allowlist.yml`.
- **Base path**: `config/settings.yml → site.base_path` (único sitio).

### Política de spoilers
La web no contiene nada del contenido del juego. Las imágenes y vídeos externos están ocultos tras un botón ("Mostrar vista previa") con aviso de spoilers; la CSP solo permite cargar previsualizaciones de hosts concretos. Nunca se muestran títulos de foros o redes: cada elemento lleva un título generado ("Vídeo de progreso · VIctory · r/pcgaming") y el aviso *"Enlace externo: puede contener spoilers"*. Se descartan los posts marcados como spoiler y los que contienen términos de `spoiler_terms.yml`. Miniaturas desactivadas. Un test garantiza que ningún HTML generado contiene esos términos.

### Política de retirada
Abre un issue con la plantilla **"Takedown request"**. **Retiramos primero y revisamos después.** Ningún contenido de issues o PRs de terceros se publica automáticamente.

### Aviso legal
Proyecto informativo y no comercial, sin relación con Rockstar Games, Take-Two Interactive ni Microsoft. No proporciona ni enlaza el juego, archivos filtrados, torrents, ejecutables, claves ni hashes. Sin marcas ni assets de Rockstar/Take-Two (las ilustraciones son SVG propios). Sin cookies ni analítica; solo se hacen peticiones a terceros si el visitante pulsa para ver una vista previa.

---

## English

### What it is
A static site that answers, every hour: *Can you play GTA 6 on PC? Which projects exist, what does each do, how far along are they, what looks good, what looks bad, how far is left and where can I read more?*

### What makes it different
Truly bilingual ES/EN, hourly updates, a deep sheet and article per project (20 tracked, including the private CGBJ build, several found through GTA VI PC Race and PortWatch VI), per-source credibility, clone/malware detection, GPU compatibility, zero spoilers, a media gallery labelled by origin and credibility (click-to-load), press headlines, live counters (time since the console launch and since the Xbox executable surfaced), a "How it works" guide and a "Fake downloads" warning page. Mobile-first layout. 100 % free: GitHub Actions + Pages only, no servers, no API keys, no runtime AI.

### How it works
See the Spanish section above for the pipeline: collect → legal & spoiler filters → credibility & clone detection → analysis → template-based ES/EN texts → static build. Offline mode: `python scripts/run_all.py --offline`.

### Credibility and scoring
- 🟢 Confirmed: the project's own README/commit/release, its developer, or a tracker reproduction.
- 🟡 Community: specialised press or threads with evidence.
- 🔴 Unverified: anonymous posts or rumour language.
- ⛔ Probably false: rumour + a technically impossible claim given the known state. Never counts.

Phases: 1 prep (0–25 %), 2 exe starts (25), 3 first frame (40), 4 menus (55), 5 world loads (70), 6 world lit (80), 7 ~30 fps (90) / stable (100); partial = half-way. The manual phase in `projects.yml` wins over keyword detection. Rules: `config/scoring.yml`, `config/credibility.yml`.

### How to add projects and links
Edit `config/projects.yml` and `config/curated_links.yml`. Spoiler terms: `config/spoiler_terms.yml`. Blocklist: `config/blocklist.yml`. Base path: `config/settings.yml`.

### Spoiler policy, takedowns and legal
No game content, ever. Generated titles only, external links flagged, spoiler-tagged posts dropped, thumbnails off. Takedowns: open an issue with the **"Takedown request"** template; we remove first and review afterwards. Not affiliated with Rockstar Games, Take-Two Interactive or Microsoft; we never provide or link the game or leaked files.

### Development
```bash
python -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
python -m pytest -q && ruff check . && ruff format --check .
python scripts/run_all.py --offline --out _site_offline
GITHUB_TOKEN=... python scripts/run_all.py
```

License: MIT.
