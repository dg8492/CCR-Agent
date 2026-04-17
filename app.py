import os
import sys
import json
import uuid
from datetime import datetime
from flask import Flask, request, jsonify, session, send_from_directory, redirect, Response, stream_with_context
from anthropic import Anthropic
from dotenv import load_dotenv
import requests

# Import duckduckgo_search once at module level so import errors surface at startup
try:
    from duckduckgo_search import DDGS as _DDGS
    _DDGS_AVAILABLE = True
except Exception as _e:
    _DDGS_AVAILABLE = False
    print(f"[WARN] duckduckgo_search unavailable: {_e}")

# Load config — tries _config first (packaged builds), falls back to .env (dev)
load_dotenv('_config')
load_dotenv('.env')

# Resolve paths — handle both normal Python and PyInstaller frozen bundle
if getattr(sys, 'frozen', False):
    EXE_DIR = os.path.dirname(sys.executable)   # folder containing Meridian.exe / Meridian.app
    BUNDLE_DIR = sys._MEIPASS                    # folder where PyInstaller unpacks bundled files
else:
    EXE_DIR = os.path.dirname(os.path.abspath(__file__))
    BUNDLE_DIR = EXE_DIR

UI_DIR = os.path.join(BUNDLE_DIR, 'ui')

# chdir to exe location so .env, clients.json, docs/ are found next to the executable
os.chdir(EXE_DIR)

app = Flask(__name__, static_folder=UI_DIR)
app.secret_key = os.environ.get('SECRET_KEY', 'ccr-dev-change-in-prod')
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['SESSION_COOKIE_SECURE'] = False

from werkzeug.middleware.proxy_fix import ProxyFix
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

claude = Anthropic(api_key=os.environ.get('ANTHROPIC_API_KEY'))

# ─── Load documents at startup ────────────────────────────────────────────────

from document_loader import load_all_documents, build_search_index, search_documents

print("Loading CCR research documents...")
DOCUMENTS = load_all_documents()
BM25_INDEX, CHUNKS = build_search_index(DOCUMENTS)

# ─── Load client tokens ───────────────────────────────────────────────────────

def load_clients():
    env_val = os.environ.get('CLIENTS_JSON')
    if env_val:
        return json.loads(env_val)
    if os.path.exists('clients.json'):
        with open('clients.json') as f:
            return json.load(f)
    return {}

CLIENTS = load_clients()

# In-memory conversation store: {session_id: [{role, content}]}
conversations = {}

# Per-session request counter for rate limiting
request_counts = {}
MAX_REQUESTS_PER_SESSION = 20

# ─── Tools ────────────────────────────────────────────────────────────────────

TOOLS = [
    {
        "name": "search_ccr_documents",
        "description": (
            "Search Catalyst Capital Research's proprietary research documents. "
            "Use for questions about CCR's analysis, financial models (e.g., LCOE, WACC), "
            "investment theses, sector reports (nuclear/SMR, battery storage, solar, wind), "
            "regulatory analysis (NRC Part 53, IRA), and CCR team findings."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query for CCR research"}
            },
            "required": ["query"]
        }
    },
    {
        "name": "get_eia_energy_data",
        "description": (
            "Fetch live data from the U.S. Energy Information Administration (EIA). "
            "Available datasets: "
            "renewable_capacity (solar/wind/hydro/geo installed MW), "
            "electricity_generation (generation by fuel type: solar, wind, nuclear, coal, gas), "
            "natural_gas_prices (Henry Hub and city-gate monthly prices), "
            "total_energy_overview (U.S. production/consumption totals), "
            "nuclear_outages (current/upcoming nuclear plant outage capacity, weekly), "
            "short_term_outlook (EIA STEO: near-term price and supply forecasts for oil/gas/electricity), "
            "petroleum_prices (WTI and Brent crude oil spot prices), "
            "coal_production (U.S. coal production by region and type), "
            "international_renewables (global renewable energy capacity and generation by country)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "data_type": {
                    "type": "string",
                    "enum": [
                        "renewable_capacity",
                        "electricity_generation",
                        "natural_gas_prices",
                        "total_energy_overview",
                        "nuclear_outages",
                        "short_term_outlook",
                        "petroleum_prices",
                        "coal_production",
                        "international_renewables"
                    ],
                    "description": "Type of EIA data to fetch"
                }
            },
            "required": ["data_type"]
        }
    },
    {
        "name": "search_energy_news",
        "description": (
            "Sweep current news for an energy sector. Sectors: "
            "'nuclear' (SMR, NRC, uranium), 'solar' (projects, IRA, tariffs, perovskite), "
            "'hydro_wind_storage' (offshore wind, geothermal, long-duration storage), "
            "'financial' (earnings, ETFs, bonds, VC/PE, DOE LPO), "
            "'science' (fusion, batteries, hydrogen, DAC, publications). "
            "Use 'all' for a full sweep. Optional 'focus' to narrow results."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "sector": {
                    "type": "string",
                    "enum": ["nuclear", "solar", "hydro_wind_storage", "financial", "science", "all"],
                    "description": "Energy sector to sweep for latest news"
                },
                "focus": {
                    "type": "string",
                    "description": "Optional: narrow the search to a specific company, topic, or event"
                }
            },
            "required": ["sector"]
        }
    },
    {
        "name": "web_search",
        "description": "Search the web for a specific company, project, filing, or research question.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Specific search query"}
            },
            "required": ["query"]
        }
    }
]

# ─── Sector query map (from CCR intelligence brief) ───────────────────────────

SECTOR_QUERIES = {
    "nuclear": [
        "SMR small modular reactor NuScale TerraPower Kairos Oklo X-energy NRC 2026",
        "uranium fuel supply Centrus Urenco Cameco enrichment contracts 2026",
        "nuclear utility Southern Company Vogtle Duke Constellation expansion 2026",
    ],
    "solar": [
        "utility-scale solar project IRA tariff AD CVD Section 201 2026",
        "perovskite tandem solar efficiency record commercialization 2026",
        "First Solar SunPower Enphase SolarEdge grid interconnection backlog 2026",
    ],
    "hydro_wind_storage": [
        "offshore wind Orsted Equinor Dominion permitting PPA cancellation 2026",
        "geothermal Fervo Quaise pumped hydro storage DOE 2026",
        "long duration energy storage Form Energy Ambri commercial deployment 2026",
    ],
    "financial": [
        "clean energy earnings NextEra Constellation Vistra NuScale Centrus Q4 2025 Q1 2026",
        "green bond clean energy M&A venture capital private equity deal 2026",
        "DOE loan program LPO clean energy financing analyst upgrade downgrade 2026",
    ],
    "science": [
        "fusion energy Commonwealth Fusion SPARC Helion TAE milestone 2026",
        "solid state battery sodium ion grid storage breakthrough 2026",
        "green hydrogen electrolyzer H2Hub carbon capture DAC Climeworks 2026",
    ],
}

# ─── Tool handlers ────────────────────────────────────────────────────────────

def handle_tool(name, inputs):
    if name == 'search_ccr_documents':
        results = search_documents(inputs['query'], BM25_INDEX, CHUNKS, top_k=5)
        if not results:
            return f"No relevant sections found in CCR research for: '{inputs['query']}'"
        out = f"**CCR Research — '{inputs['query']}':**\n\n"
        for i, r in enumerate(results, 1):
            out += f"**[{i}] {r['source']}**\n{r['text']}\n\n---\n\n"
        return out

    elif name == 'get_eia_energy_data':
        return fetch_eia_data(inputs['data_type'])

    elif name == 'search_energy_news':
        sector = inputs.get('sector', 'all')
        focus  = inputs.get('focus', '').strip()
        try:
            if not _DDGS_AVAILABLE:
                return "News search unavailable: duckduckgo_search could not be loaded."
            if sector == 'all':
                queries = [qs[0] for qs in SECTOR_QUERIES.values()]
            else:
                queries = SECTOR_QUERIES.get(sector, [])[:2]  # max 2 queries per sector
            if focus:
                queries = [f"{focus} {queries[0]}"] if queries else [focus]

            label = sector.replace('_', ' ').title()
            out = f"**Energy News — {label}**\n\n"
            seen = set()
            with _DDGS() as ddgs:
                for q in queries:
                    for r in ddgs.text(q, max_results=3):
                        url = r.get('href', '')
                        if url in seen:
                            continue
                        seen.add(url)
                        body = (r.get('body') or '')[:200]  # truncate to keep context lean
                        out += f"**{r.get('title', '')}**\n{body}\n{url}\n\n"
            return out if seen else "No results found for this sector."
        except Exception as e:
            return f"Energy news search error: {e}"

    elif name == 'web_search':
        try:
            if not _DDGS_AVAILABLE:
                return "Web search unavailable: duckduckgo_search could not be loaded."
            results = []
            with _DDGS() as ddgs:
                for r in ddgs.text(inputs['query'], max_results=6):
                    results.append(r)
            if not results:
                return "No web results found."
            out = f"**Web Search — '{inputs['query']}':**\n\n"
            for r in results:
                body = (r.get('body') or '')[:300]  # truncate to keep context lean
                out += f"**{r.get('title', '')}**\n{body}\n🔗 {r.get('href', '')}\n\n"
            return out
        except Exception as e:
            return f"Web search error: {e}"

    return f"Unknown tool: {name}"


def fetch_eia_data(data_type):
    api_key = os.environ.get('EIA_API_KEY', '')
    if not api_key:
        return "EIA_API_KEY not configured. Add it to your .env file."

    base = 'https://api.eia.gov/v2'

    configs = {
        # ── Electricity ───────────────────────────────────────────────────────
        'renewable_capacity': {
            'url': f"{base}/electricity/electric-power-operational-data/data/",
            'params': [
                ('api_key', api_key), ('data[0]', 'nameplate-capacity-mw'),
                ('facets[fueltypeid][]', 'SUN'), ('facets[fueltypeid][]', 'WND'),
                ('facets[fueltypeid][]', 'HYC'), ('facets[fueltypeid][]', 'GEO'),
                ('facets[sectorid][]', '99'), ('frequency', 'annual'),
                ('sort[0][column]', 'period'), ('sort[0][direction]', 'desc'), ('length', '20')
            ]
        },
        'electricity_generation': {
            'url': f"{base}/electricity/electric-power-operational-data/data/",
            'params': [
                ('api_key', api_key), ('data[0]', 'generation'),
                ('facets[fueltypeid][]', 'SUN'), ('facets[fueltypeid][]', 'WND'),
                ('facets[fueltypeid][]', 'NUC'), ('facets[fueltypeid][]', 'COW'),
                ('facets[fueltypeid][]', 'NG'), ('facets[sectorid][]', '99'),
                ('frequency', 'annual'), ('sort[0][column]', 'period'),
                ('sort[0][direction]', 'desc'), ('length', '30')
            ]
        },
        # ── Natural Gas ───────────────────────────────────────────────────────
        'natural_gas_prices': {
            'url': f"{base}/natural-gas/pri/sum/data/",
            'params': [
                ('api_key', api_key), ('data[0]', 'value'),
                ('frequency', 'monthly'), ('sort[0][column]', 'period'),
                ('sort[0][direction]', 'desc'), ('length', '12')
            ]
        },
        # ── Total Energy ──────────────────────────────────────────────────────
        'total_energy_overview': {
            'url': f"{base}/total-energy/data/",
            'params': [
                ('api_key', api_key), ('data[0]', 'value'),
                ('facets[msn][]', 'REPRBUS'), ('facets[msn][]', 'GETCBUS'),
                ('facets[msn][]', 'TEPRBUS'),
                ('frequency', 'annual'), ('sort[0][column]', 'period'),
                ('sort[0][direction]', 'desc'), ('length', '10')
            ]
        },
        # ── Nuclear Outages ───────────────────────────────────────────────────
        # Weekly snapshot of how much nuclear capacity is offline (planned + unplanned)
        'nuclear_outages': {
            'url': f"{base}/nuclear-outages/us-nuclear-outages/data/",
            'params': [
                ('api_key', api_key),
                ('data[0]', 'outage'), ('data[1]', 'capacity'),
                ('frequency', 'weekly'),
                ('sort[0][column]', 'period'), ('sort[0][direction]', 'desc'),
                ('length', '12')
            ]
        },
        # ── Short-Term Energy Outlook (STEO) ──────────────────────────────────
        # EIA's near-term forecasts for oil, gas, electricity prices and supply
        'short_term_outlook': {
            'url': f"{base}/steo/data/",
            'params': [
                ('api_key', api_key), ('data[0]', 'value'),
                # WTI crude, Henry Hub gas, avg electricity retail price forecasts
                ('facets[seriesId][]', 'WTIPUUS'),   # WTI crude $/bbl
                ('facets[seriesId][]', 'NGHHUUS'),   # Henry Hub $/MMBtu
                ('facets[seriesId][]', 'EPLPJC'),    # Electricity retail ¢/kWh
                ('facets[seriesId][]', 'AWHFPUS'),   # World crude oil price
                ('frequency', 'monthly'),
                ('sort[0][column]', 'period'), ('sort[0][direction]', 'desc'),
                ('length', '24')
            ]
        },
        # ── Petroleum Prices ──────────────────────────────────────────────────
        # WTI and Brent spot prices (weekly)
        'petroleum_prices': {
            'url': f"{base}/petroleum/pri/spt/data/",
            'params': [
                ('api_key', api_key), ('data[0]', 'value'),
                ('frequency', 'weekly'),
                ('sort[0][column]', 'period'), ('sort[0][direction]', 'desc'),
                ('length', '16')
            ]
        },
        # ── Coal Production ───────────────────────────────────────────────────
        'coal_production': {
            'url': f"{base}/coal/production/data/",
            'params': [
                ('api_key', api_key), ('data[0]', 'value'),
                ('frequency', 'quarterly'),
                ('sort[0][column]', 'period'), ('sort[0][direction]', 'desc'),
                ('length', '16')
            ]
        },
        # ── International Renewables ──────────────────────────────────────────
        'international_renewables': {
            'url': f"{base}/international/data/",
            'params': [
                ('api_key', api_key), ('data[0]', 'value'),
                # Renewable electricity generation (series 2) and capacity (series 29/30)
                ('facets[activityId][]', '2'),       # Electricity generation
                ('facets[productId][]', '8'),        # Wind
                ('facets[productId][]', '16'),       # Solar
                ('frequency', 'annual'),
                ('sort[0][column]', 'period'), ('sort[0][direction]', 'desc'),
                ('length', '30')
            ]
        },
    }

    cfg = configs.get(data_type)
    if not cfg:
        return f"Unknown EIA data type: {data_type}"

    try:
        resp = requests.get(cfg['url'], params=cfg['params'], timeout=15)
        if resp.status_code != 200:
            return f"EIA API returned HTTP {resp.status_code} for `{data_type}`."
        data = resp.json()
        records = data.get('response', {}).get('data', [])
        total = data.get('response', {}).get('total', 0)

        if not records:
            # Surface any API error message
            error_msg = data.get('response', {}).get('description', '') or str(data)
            return f"EIA returned no records for `{data_type}`. {error_msg}"

        label = data_type.replace('_', ' ').title()
        out = f"**EIA Data: {label}** (total available: {total})\n\n```\n"
        keys = list(records[0].keys())
        out += " | ".join(keys) + "\n" + "-" * 70 + "\n"
        for row in records[:20]:
            out += " | ".join(str(row.get(k, '')) for k in keys) + "\n"
        out += "```\n"
        return out

    except Exception as e:
        return f"EIA API error: {e}"


# ─── Agent loop ────────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are Meridian — Catalyst Capital Research's proprietary AI research platform for institutional energy transition investors (family offices, hedge funds, PE/VC).

**Tools:**
- `search_ccr_documents` — CCR proprietary reports, LCOE models, investment theses, regulatory analysis
- `get_eia_energy_data` — Live U.S. energy data (capacity, generation, prices, nuclear outages)
- `search_energy_news` — Current news across 5 sectors: nuclear, solar, hydro_wind_storage, financial, science
- `web_search` — Targeted single-topic lookups

**5 Coverage Sectors:**
1. Nuclear/SMR — NuScale, TerraPower, Kairos, Oklo, Centrus, Urenco, Cameco; NRC/DOE activity; uranium supply
2. Solar — Utility-scale pipeline, IRA/tariff policy, perovskite R&D; First Solar, Enphase, SolarEdge
3. Wind/Hydro/Storage — Offshore wind (Orsted, Equinor), geothermal (Fervo), long-duration storage (Form Energy, Ambri)
4. Financial — Earnings (NextEra, Constellation, Vistra, NuScale), ETFs, green bonds, DOE LPO, VC/PE deals
5. Science — Fusion (CFS SPARC, Helion), solid-state batteries, green hydrogen, DAC; Nature Energy/Joule/arXiv

**Guidelines:**
- Use 1-2 tools maximum, then synthesize immediately. Do not chain more than 2 tool calls.
- Cite sources explicitly. Lead with data.
- Use `search_energy_news` for broad sector questions; `web_search` for precise lookups.
- CCR positions (e.g., avoid SMR hardware, own fuel cycle — Centrus/Urenco) are CCR's analytical conclusions.
- Institutional tone. Flag research vs. investment advice when relevant.

Today: {date}"""


TOOL_STATUS = {
    'search_ccr_documents': 'Searching CCR research...',
    'get_eia_energy_data':   'Fetching EIA data...',
    'search_energy_news':    'Sweeping energy news...',
    'web_search':            'Searching the web...',
}


MAX_HISTORY = 40  # max messages stored per session (20 exchanges); trims oldest to prevent memory bloat

def stream_agent(session_id, user_message):
    """SSE generator: emits status events during tool calls, full text at end."""
    if session_id not in conversations:
        conversations[session_id] = []

    conversations[session_id].append({"role": "user", "content": user_message})

    # Trim oldest messages if conversation grows too long
    if len(conversations[session_id]) > MAX_HISTORY:
        conversations[session_id] = conversations[session_id][-MAX_HISTORY:]

    working = [{"role": m["role"], "content": m["content"]} for m in conversations[session_id]]
    system = SYSTEM_PROMPT.format(date=datetime.now().strftime('%B %d, %Y'))

    tool_calls_made = 0
    MAX_TOOL_CALLS = 2  # hard cap — after this, no more tools offered

    for _ in range(8):
        # Once tool cap is reached, call without tools to force synthesis
        kwargs = dict(
            model="claude-haiku-4-5-20251001",
            max_tokens=3000,
            system=system,
            messages=working
        )
        if tool_calls_made < MAX_TOOL_CALLS:
            kwargs['tools'] = TOOLS

        try:
            response = claude.messages.create(**kwargs)
        except Exception as e:
            err = f"Research service temporarily unavailable. Please try again in a moment. (Error: {type(e).__name__})"
            conversations[session_id].append({"role": "assistant", "content": err})
            yield f"data: {json.dumps({'type': 'text', 'text': err})}\n\n"
            yield f"data: {json.dumps({'type': 'done'})}\n\n"
            return

        if response.stop_reason == 'end_turn':
            text = ''.join(b.text for b in response.content if hasattr(b, 'text'))
            conversations[session_id].append({"role": "assistant", "content": text})
            yield f"data: {json.dumps({'type': 'text', 'text': text})}\n\n"
            yield f"data: {json.dumps({'type': 'done'})}\n\n"
            return

        elif response.stop_reason == 'tool_use':
            working.append({"role": "assistant", "content": response.content})
            tool_results = []
            for block in response.content:
                if block.type == 'tool_use':
                    tool_calls_made += 1
                    yield f"data: {json.dumps({'type': 'status', 'text': TOOL_STATUS.get(block.name, 'Analyzing...')})}\n\n"
                    result = handle_tool(block.name, block.input)
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": str(result)
                    })
            working.append({"role": "user", "content": tool_results})
        else:
            break

    # Fallback if loop exhausted without end_turn
    text = "I wasn't able to complete that research in one pass. Try narrowing your question to a specific sector or topic."
    conversations[session_id].append({"role": "assistant", "content": text})
    yield f"data: {json.dumps({'type': 'text', 'text': text})}\n\n"
    yield f"data: {json.dumps({'type': 'done'})}\n\n"


# ─── Routes ───────────────────────────────────────────────────────────────────

def no_cache(response):
    response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate'
    response.headers['Pragma'] = 'no-cache'
    return response

@app.route('/')
def index():
    if session.get('authenticated'):
        return redirect('/dashboard')
    return no_cache(send_from_directory(UI_DIR, 'login.html'))

@app.route('/chat')
def chat_page():
    if not session.get('authenticated'):
        return redirect('/')
    return no_cache(send_from_directory(UI_DIR, 'chat.html'))

@app.route('/dashboard')
def dashboard():
    if not session.get('authenticated'):
        return redirect('/')
    return no_cache(send_from_directory(UI_DIR, 'dashboard.html'))

@app.route('/embed')
def embed():
    if not session.get('authenticated'):
        return '<body style="margin:0;background:#fee;display:flex;align-items:center;justify-content:center;height:100vh;font-family:sans-serif"><p style="font-size:1.1rem;color:#c00">Session expired — <a href="/">log in again</a></p></body>', 401
    return no_cache(send_from_directory(UI_DIR, 'chat.html'))

@app.route('/logo.png')
def logo():
    return send_from_directory(UI_DIR, 'logo.png')

@app.route('/api/me')
def me():
    if not session.get('authenticated'):
        return jsonify({'error': 'Unauthorized'}), 401
    return jsonify({'client': session.get('client_name', 'Client')})

@app.route('/api/auth', methods=['POST'])
def auth():
    token = (request.json or {}).get('token', '').strip()
    if token in CLIENTS:
        session['authenticated'] = True
        session['client_name'] = CLIENTS[token]
        session['session_id'] = str(uuid.uuid4())
        conversations[session['session_id']] = []
        return jsonify({'success': True, 'client': CLIENTS[token]})
    return jsonify({'success': False, 'error': 'Invalid access token'}), 401

@app.route('/api/chat', methods=['POST'])
def chat_api():
    if not session.get('authenticated'):
        return jsonify({'error': 'Unauthorized'}), 401
    message = (request.json or {}).get('message', '').strip()
    if not message:
        return jsonify({'error': 'Empty message'}), 400
    sid = session.get('session_id')
    if not sid:
        return jsonify({'error': 'Session expired, please log in again'}), 401
    if request_counts.get(sid, 0) >= MAX_REQUESTS_PER_SESSION:
        return jsonify({'error': 'You\'ve reached the request limit for this session. Click "New Chat" to continue.'}), 429
    request_counts[sid] = request_counts.get(sid, 0) + 1

    def generate():
        yield from stream_agent(sid, message)

    return Response(
        stream_with_context(generate()),
        mimetype='text/event-stream',
        headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'}
    )

@app.route('/api/new_conversation', methods=['POST'])
def new_conversation():
    if not session.get('authenticated'):
        return jsonify({'error': 'Unauthorized'}), 401
    sid = session.get('session_id')
    if sid:
        conversations[sid] = []
        request_counts[sid] = 0
    return jsonify({'success': True})

@app.route('/api/logout', methods=['POST'])
def logout():
    sid = session.get('session_id')
    if sid:
        conversations.pop(sid, None)
        request_counts.pop(sid, None)
    session.clear()
    return jsonify({'success': True})

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5051))
    import threading, webbrowser
    threading.Timer(1.5, lambda: webbrowser.open(f'http://localhost:{port}')).start()
    app.run(debug=False, host='0.0.0.0', port=port, threaded=True)
