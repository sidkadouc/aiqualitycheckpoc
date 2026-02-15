# OECD Quality Checker — Word Web Add-in

A Word Web Add-in that checks document content against OECD Style Guide rules in real time.

## Architecture

```
┌─────────────────────────────┐     ┌──────────────────────────┐
│   Word Web Add-in           │     │   Python API (FastAPI)   │
│   (TypeScript + Office.js)  │────▶│   /api/check-paragraph   │
│                             │◀────│   /api/check             │
│  Task Pane:                 │     │                          │
│  - Finding cards            │     │  Quality Agent:          │
│  - Auto-fix buttons         │     │  - OpenXML parser        │
│  - Highlight navigation     │     │  - Rule pre-filter       │
│                             │     │  - GPT-4.1 checker       │
│  Document:                  │     │  - Violation aggregator  │
│  - Highlighted violations   │     │  - AddinResponse builder │
│  - Applied fixes            │     └──────────────────────────┘
└─────────────────────────────┘
```

## Real-time Mode

The add-in operates in a "style advisor" mode:
- **Debounced** (2 sec): when the cursor moves to a new paragraph, the add-in sends that
  single paragraph to `/api/check-paragraph`
- **Latency**: ~2-5 seconds per paragraph (Azure OpenAI GPT-4.1 round-trip)
- **Incremental**: results merge with existing findings — previous paragraphs stay highlighted
- True IntelliSense-speed (~100 ms) isn't achievable with LLM inference, but the debounced
  approach gives a smooth "advisor" experience

## Quick Start

### 1. Start the API server

```bash
cd src
pip install -r requirements.txt
python run_api.py --port 8000
```

### 2. Start the Add-in

```bash
cd word-addin
npm install
npm run dev          # serves on https://localhost:3000
```

### 3. Sideload in Word

1. Open Word (desktop or web)
2. Insert > My Add-ins > Upload My Add-in
3. Select `word-addin/manifest.xml`
4. Click the "Style Checker" button in the Home tab

## Project Structure

```
word-addin/
├── manifest.xml                  # Office Add-in XML manifest
├── package.json                  # Node.js dependencies
├── tsconfig.json                 # TypeScript config
├── webpack.config.js             # Build config (dev server on :3000)
├── assets/                       # Icons for ribbon
├── src/
│   ├── taskpane/
│   │   ├── taskpane.html         # Task pane markup
│   │   ├── taskpane.css          # Fluent UI-inspired styles
│   │   └── taskpane.ts           # Main logic: real-time check, rendering, fix/dismiss
│   ├── commands/
│   │   ├── commands.html         # Hidden iframe for ribbon commands
│   │   └── commands.ts           # Ribbon command handlers
│   ├── services/
│   │   ├── qualityService.ts     # API client (POST /api/check, /api/check-paragraph)
│   │   └── documentService.ts    # Office.js: OOXML extraction, highlighting, fixes
│   └── models/
│       └── types.ts              # TypeScript types matching AddinResponse
```

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/health` | GET | Health probe (rules loaded, uptime) |
| `/api/rules/summary` | GET | Rule statistics by type and severity |
| `/api/check` | POST | Full document check (all paragraphs) |
| `/api/check-paragraph` | POST | Single paragraph check (low-latency) |

## Fix Types

The agent classifies each violation with a `fix_type`:

| fix_type | fix_value example | Office.js action |
|----------|-------------------|------------------|
| `remove_formatting` | `"bold,italic"` | `range.font.bold = false` |
| `replace_text` | `"corrected text"` | `range.insertText(...)` |
| `apply_style` | `"O.N.E Author Body Text"` | `paragraph.style = ...` |
| `manual` | `""` | User must decide |

## Development

- API auto-reloads: `python run_api.py --reload`
- Add-in hot-reloads: `npm run dev` with webpack-dev-server
- Both run concurrently during development
