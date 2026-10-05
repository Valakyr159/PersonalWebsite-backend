# CLAUDE.md

Backend del chatbot RAG del portfolio (frontend: repo hermano `../PersonalWebsite` /
`Valakyr159/Valakyr159.github.io`). Ver `Plan.md` en ese repo para el contexto completo del proyecto.

## Qué es esto

Un servidor **MCP (Model Context Protocol)** sobre SSE, no una API REST. El frontend Angular se conecta
con `@modelcontextprotocol/sdk`'s `Client` y llama tools (`upload_pdf`, `query_rag`, `clear_session`),
no endpoints HTTP convencionales. Esta arquitectura fue una decisión deliberada de mantener (ver
`Plan.md` del frontend) en vez de reescribir al REST+streaming que describía el `plan.md` original.

## Stack

Starlette + `mcp` SDK (**pineado a `<2.0.0`**, ver Gotchas) + `fastembed` (embeddings locales vía ONNX
Runtime, `BAAI/bge-small-en-v1.5`, **no** PyTorch) + `groq` (LLM, `llama-3.1-8b-instant`) + PyMuPDF
(parseo de PDF). Todo en memoria, por sesión, sin base de datos — las sesiones expiran por TTL
(`SessionManager.ttl`, default 1h, limpieza perezosa en cada `get_session()`, no hay job en background).

## Estructura

```
src/mcp_server/
  server.py             ← app Starlette, define los 3 tools MCP, rutas /sse /messages /health
  session_manager.py    ← SessionManager singleton: chunks + embeddings + historial por sesión
  rag_tools.py           ← generate_rag_response(): arma el prompt, llama a Groq
  pdf_tools.py            ← extract_text_from_pdf_base64(), chunk_text(), MAX_PDF_SIZE_MB
  genshin.py              ← REST (no MCP) para la guía de Genshin: /genshin/profile/{uid}, /genshin/meta
  data/genshin_roster.json ← generado, no editar a mano (ver abajo)
scripts/build_genshin_roster.py ← regenera el roster desde el store público de Enka
tests/                    ← pytest, ver más abajo
```

No existe `src/mcp_server/app/` — esa carpeta era una implementación paralela muerta (REST + LlamaIndex,
nunca terminada, con un import roto) del prototipo original y se descartó al migrar este repo.

## Correr local

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env   # y rellenar GROQ_API_KEY
python -m src.mcp_server.server        # sirve en :8000 (usa $PORT si está definido)
pytest -v
```

## Variables de entorno

| Variable | Default | Notas |
|---|---|---|
| `GROQ_API_KEY` | — | requerida para respuestas reales; sin ella, Groq devuelve error y el tool responde con un mensaje de error amigable |
| `PORT` | 8000 | el host (Render, etc.) lo inyecta en runtime y pisa este default — no hardcodear un puerto distinto en el Dockerfile |
| `ALLOWED_ORIGINS` | `https://valakyr159.github.io` | CSV; añadir `http://localhost:4200` en dev si hace falta probar contra el front local |
| `GEMINI_API_KEY` | — | meta de Genshin. Local: `.env`; producción: dashboard de Render (`sync: false`) |
| `GEMINI_META_MODELS` / `GEMINI_CHAT_MODELS` | `gemini-3.5-flash-lite,gemini-3.1-flash-lite` | CSV, primero el principal; el siguiente se usa ante 429/503 |
| `SUPABASE_URL` / `SUPABASE_ANON_KEY` / `GENSHIN_DB_SECRET` | — | almacén del meta y de los topes diarios; sin las tres, memoria. **Nunca la `service_role`** (ver abajo) |
| `GENSHIN_META_SOURCES` | genshin.gg tier-list + teams + Fandom `Version` | CSV de URLs https que Gemini lee para el meta |
| `MAX_PDF_SIZE_MB` | 20 | se valida sobre el tamaño del base64 antes de parsear |

## Tests

`pytest` (ver `tests/conftest.py`): reemplaza `fastembed.TextEmbedding` real por un embedder
bag-of-words determinístico antes de que `session_manager.py` lo importe — evita descargar el modelo
real y depender de red en CI. Los tests de `rag_tools` mockean `Groq.chat.completions.create`
directamente, nunca pegan a la API real. `tests/test_server.py` importa `server.py` de verdad (no solo
`session_manager`/`rag_tools`/`pdf_tools` por separado) — es la única razón por la que el bug descrito
abajo (`mcp` 2.0.0) se detectaría en CI la próxima vez.

## Deploy a Render

Manual la primera vez (requiere conectar tu cuenta de GitHub a Render), después es automático en cada
push a `main` — ver `Plan.md` del frontend, Fase 2, para los pasos completos:
1. En https://dashboard.render.com → New → Blueprint → conectar este repo (`Valakyr159/PersonalWebsite-backend`).
   Render detecta `render.yaml` solo y crea el servicio (plan free, Docker, healthcheck en `/health`).
2. Configurar el secret `GROQ_API_KEY` en la pestaña "Environment" del servicio (no está en `render.yaml`
   a propósito — `sync: false` — nunca en el código).
3. Cada push a `main` re-despliega automáticamente (Render vigila el repo).

Nota: se evaluó Hugging Face Spaces primero, pero su plan gratis dejó de incluir SDK Docker/Gradio (solo
Static, que no puede correr este backend Python) — de ahí el cambio a Render.

## Gotchas conocidos

- **`mcp[cli]` está pineado a `>=1.29.0,<2.0.0`, no lo subas sin revisar `server.py` primero.** La API de
  bajo nivel de `Server` (`@app.list_tools()` / `@app.call_tool()`, que es como está escrito
  `server.py`) fue reemplazada en `mcp` 2.0.0 (`add_request_handler`, `streamable_http_app`, sin esos
  decoradores) — con el rango sin techo original (`>=1.0.0`) el primer deploy a Render instaló 2.0.0 y
  el proceso ni siquiera arrancaba (`AttributeError` en el import). Ninguno de los tests de
  `session_manager`/`rag_tools`/`pdf_tools` lo detectó porque ninguno importa `server.py` — de ahí
  `tests/test_server.py`.
- **`sentence-transformers` (PyTorch) se cambió por `fastembed` (ONNX Runtime) por memoria, no por
  preferencia técnica.** El primer deploy a Render murió con "Ran out of memory (used over 512MB)"
  porque solo importar `torch` ya consume varios cientos de MB. `fastembed` + `BAAI/bge-small-en-v1.5`
  con `threads=1` deja el proceso en desarrollo local en ~340MB de pico (import + un embed real) — deja
  margen bajo el límite de 512MB del free tier, pero no es holgado; si se añade otra dependencia pesada,
  volver a medir con `resource.getrusage(resource.RUSAGE_SELF).ru_maxrss` antes de asumir que cabe.
- El plan free de Render "duerme" el servicio tras ~15 min de inactividad — el primer request tras
  dormir tarda ~30-60s (cold start). No es un bug, es una limitación conocida del hosting gratuito.
- CORS lee `ALLOWED_ORIGINS` en el arranque del proceso — cambiar esa variable en Render requiere que el
  servicio se reinicie (Render lo hace solo al guardar la env var), no basta con guardarla y ya.
- El Dockerfile no fija el puerto a un valor específico de plataforma (antes tenía `ENV PORT=7860` para
  HF Spaces) — ahora usa `8000` como default genérico porque cada plataforma inyecta su propio `PORT`
  en runtime de todos modos.


## Genshin (`genshin.py`, `genshin_store.py`, `db/genshin_meta.sql`)

**Qué dispara una llamada a Gemini (y qué no)** — es lo que cuida la cuota gratuita (500/día por modelo Lite):
- Importar UID, marcar personajes, «¿A quién sacar?», comparar: **nunca** (Enka y el motor del navegador).
- `POST /genshin/meta`: **solo** si no hay snapshot de menos de 12 h. Entonces lanza UNA generación en segundo
  plano (candado), con tope diario (`META_DAILY_CAP`=6) y enfriamiento de 5 min tras un fallo. No hay botón
  ni parámetro para forzarla. Sondear no cuesta nada: responde desde el snapshot.
- `POST /genshin/chat`: 1 llamada por mensaje, con tope diario global (`CHAT_DAILY_CAP`=300) y 20/10 min por IP.

- **Perfil**: proxy a Enka.Network (`/api/uid/{uid}`). Enka exige `User-Agent` propio (el navegador no puede
  fijarlo) y solo devuelve la **vitrina** (hasta 8, si el jugador la tiene visible), nunca el roster completo.
- **Meta** (`MetaService`): estados `fresh | updating | stale | unavailable`. `updating` trae `elapsed` (el
  contador de la UI) y el snapshot anterior si existe (el navegador sigue mostrándolo). Gemini lee las páginas
  de `GENSHIN_META_SOURCES` con **`url_context`** (NO `google_search`: sin cuota en este proyecto).
- **Almacén** (`genshin_store.py`): Supabase si están las tres variables; si no, o si falla, memoria (Render
  gratis se duerme a los 15 min y pierde memoria y disco). **El proyecto Supabase es compartido** (antes
  `patitas-a-la-obra`, ahora «PersonalWebsite», con datos reales de otra app), así que el backend NO usa la
  `service_role` (daría acceso a todo): usa la clave `anon`, que no puede nada por sí sola (RLS sin policies y
  sin grants), y solo llama a funciones `genshin_*` (`SECURITY DEFINER`) que exigen `GENSHIN_DB_SECRET`; en la
  base solo vive su SHA-256. Si el entorno de Render se filtrara, solo se podría manipular el meta de Genshin.
  Los contadores diarios son atómicos (`genshin_bump_usage`). Ver `db/genshin_meta.sql`.
- **Convención para ramificar más proyectos personales en ese Supabase**: prefijo por app (`foo_*`) en tablas y
  funciones, una fila en `pw_app_access` con el hash del secreto de esa app, y solo funciones que llamen a
  `pw_check_access('foo', p_secret)` primero. Nunca darle la `service_role` a un backend.
- **Modelos**: `GEMINI_META_MODELS` y `GEMINI_CHAT_MODELS` (CSV, primero el principal). Por defecto
  `gemini-3.5-flash-lite,gemini-3.1-flash-lite`: los de 20 peticiones/día (3.8/3.6/3.7/3.5 Flash) no sirven de
  base. `gemini-2.5-flash` ya no existe para claves nuevas. Un 429 de cuota **diaria** no se reintenta.
- **Un snapshot del modelo de reserva** (`degraded`) no afirma banners, caduca en 10 min y **nunca reemplaza**
  uno bueno ya guardado, aunque esté caduco: uno bueno viejo vale más que uno fresco dudoso (el 3.1 Lite
  inventó banners en producción).
- Todo lo del modelo se **valida contra el roster** (`validate_meta`); solo se acreditan como fuentes las
  páginas que `url_context` reportó como `SUCCESS`.
- Los tests **nunca** llaman a la API real (la cuota es compartida con producción y con otros proyectos).
- Regenerar el roster al salir un personaje: `python scripts/build_genshin_roster.py` (también lo copia a
  `../PersonalWebsite/public/genshin/roster.json`). Los viajeros no están modelados.
