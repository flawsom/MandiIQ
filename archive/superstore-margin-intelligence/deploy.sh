#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────
# 🌾 MandiIQ — One-Command Local Setup  (PRD v6)
#
# Usage:
#   chmod +x deploy.sh && ./deploy.sh
#   ./deploy.sh --with-ndvi               # Also install NDVI satellite dependencies
#   ./deploy.sh --with-ai                 # Also install AI orchestrator deps (Phase 11)
#   ./deploy.sh --with-ndvi --with-ai     # Install everything
#   ./deploy.sh Tomato                    # Test a different commodity
#   ./deploy.sh "Potato" "Maharashtra"   # Test with state filter
#   ./deploy.sh --api-key=YOUR_KEY        # Non-interactive mode
#
# What it does:
#   1. Checks Python version (3.10+)
#   2. Creates .env with DATA_GOV_IN_API_KEY + OPENROUTER_API_KEY (prompts if not set)
#   3. Creates data directory for DuckDB
#   4. Installs pip dependencies from mandi_rdd/requirements.txt
#   5a. (Optional) Installs Google Earth Engine API + NDVI deps
#   5b. (Optional) Installs AI orchestrator deps (OpenRouter / openai SDK)
#   6. Runs the Phase 1 static proof (go/no-go gate)
#   7. Prints next steps — launch dashboard, API, "Ask MandiIQ" chat
#
# For Windows: run in Git Bash, WSL, or the bash shell.
# ─────────────────────────────────────────────────────────────────────

set -euo pipefail

# ── Config ──
WITH_NDVI=false
WITH_AI=false
API_KEY=""
OPENROUTER_KEY=""
# Collect all non-flag positional args
POSITIONAL=()
for arg in "$@"; do
    case "$arg" in
        --with-ndvi) WITH_NDVI=true ;;
        --with-ai) WITH_AI=true ;;
        --api-key=*) API_KEY="${arg#*=}" ;;
        --api-key) echo -e "${RED}  ❌ Use --api-key=VALUE format${NC}"; exit 1 ;;
        --openrouter-key=*) OPENROUTER_KEY="${arg#*=}" ;;
        --openrouter-key) echo -e "${RED}  ❌ Use --openrouter-key=VALUE format${NC}"; exit 1 ;;
        *) POSITIONAL+=("$arg") ;;
    esac
done

COMMODITY="${POSITIONAL[0]:-Onion}"
STATE="${POSITIONAL[1]:-}"

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$PROJECT_ROOT/.env"
REQUIREMENTS="$PROJECT_ROOT/mandi_rdd/requirements.txt"
DATA_DIR="$PROJECT_ROOT/mandi_rdd/data"
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

echo ""
echo -e "${CYAN}╔══════════════════════════════════════════════════════╗${NC}"
echo -e "${CYAN}║  🌾  MandiIQ — Price Intelligence System Setup      ║${NC}"
echo -e "${CYAN}║  ${YELLOW}Commodity:${NC} $COMMODITY${NC}                        ║"
echo -e "${CYAN}╚══════════════════════════════════════════════════════╝${NC}"
echo ""

# ── Step 1: Check Python version ──
echo -e "${YELLOW}[1/6]${NC} Checking Python version..."
PYTHON=$(command -v python3 || command -v python || true)
if [ -z "$PYTHON" ]; then
    echo -e "${RED}  ❌ Python not found! Install Python 3.12+ from python.org${NC}"
    exit 1
fi
PY_VERSION=$("$PYTHON" --version 2>&1 | grep -oP '\d+\.\d+' | head -1 || echo "0")
PY_MAJOR=$(echo "$PY_VERSION" | cut -d. -f1)
PY_MINOR=$(echo "$PY_VERSION" | cut -d. -f2)
if [ "$PY_MAJOR" -lt 3 ] || { [ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -lt 10 ]; }; then
    echo -e "${RED}  ❌ Python 3.10+ required (found $PY_VERSION). Install from python.org${NC}"
    exit 1
fi
echo -e "${GREEN}  ✅ Python $PY_VERSION found at $PYTHON${NC}"

# ── Step 2: Create .env file ──
echo ""
echo -e "${YELLOW}[2/6]${NC} Setting up environment variables..."

# Helper: write/update a key=value in .env using Python (handles special chars safely)
env_set() {
    local key="$1" val="$2"
    "$PYTHON" -c "
import re, sys
path = '$ENV_FILE'
key = sys.argv[1]
val = sys.argv[2]
try:
    with open(path) as f: content = f.read()
    if re.search(r'^' + key + r'=', content, re.M):
        content = re.sub(r'^' + key + r'=.*', key + '=' + val, content, flags=re.M)
    else:
        content += key + '=' + val + '\n'
    with open(path, 'w') as f: f.write(content)
except FileNotFoundError:
    with open(path, 'w') as f: f.write(key + '=' + val + '\n')
" "$key" "$val" 2>/dev/null
}

# ── DATA_GOV_IN_API_KEY ──
if [ -n "$API_KEY" ]; then
    export DATA_GOV_IN_API_KEY="$API_KEY"
    if [ -f "$ENV_FILE" ]; then
        env_set "DATA_GOV_IN_API_KEY" "$API_KEY"
    else
        echo "DATA_GOV_IN_API_KEY=$API_KEY" > "$ENV_FILE"
    fi
    echo -e "  ${GREEN}✅ data.gov.in API key set from --api-key flag${NC}"
elif grep -q "^DATA_GOV_IN_API_KEY=" "$ENV_FILE" 2>/dev/null; then
    echo -e "  ${GREEN}✅ DATA_GOV_IN_API_KEY already set in .env${NC}"
    set -a; source "$ENV_FILE" 2>/dev/null || true; set +a
else
    echo ""
    echo "  You need a data.gov.in API key to fetch live mandi prices."
    echo "  Get a free key at: https://api.data.gov.in/manage"
    echo "  (Or press Enter to use the public demo key — rate-limited)"
    echo ""
    read -r -p "  Enter your API key (or press Enter for demo key): " USER_KEY
    if [ -z "$USER_KEY" ]; then
        USER_KEY="579b464db66ec23bdd000001cdd3946e44ce4aad7209ff7b23ac571b"
        echo -e "  ${YELLOW}ℹ️  Using public demo key (rate-limited ~100 req/day)${NC}"
    fi
    if [ -f "$ENV_FILE" ]; then
        env_set "DATA_GOV_IN_API_KEY" "$USER_KEY"
    else
        # Create new .env with template
        cat > "$ENV_FILE" << EOF
# MandiIQ — Environment Variables
# Generated by deploy.sh on $(date)

# data.gov.in API key for mandi prices + rainfall
DATA_GOV_IN_API_KEY=$USER_KEY

# ── Phase 11: AI Orchestrator (OpenRouter — free-tier multi-model routing) ──
# Get a free API key from https://openrouter.ai/keys (no card required)
# Routes across free models with circuit-breaker fallback. Without this,
# the "Ask MandiIQ" chat panel shows a graceful message.
OPENROUTER_API_KEY=
EOF
    fi
    echo -e "  ${GREEN}✅ DATA_GOV_IN_API_KEY set in .env${NC}"
    export DATA_GOV_IN_API_KEY="$USER_KEY"
fi

# ── OPENROUTER_API_KEY (Phase 11 — AI Orchestrator, free-tier multi-model routing) ──
if [ -n "$OPENROUTER_KEY" ]; then
    export OPENROUTER_API_KEY="$OPENROUTER_KEY"
    env_set "OPENROUTER_API_KEY" "$OPENROUTER_KEY"
    echo -e "  ${GREEN}✅ OpenRouter API key set from --openrouter-key flag${NC}"
elif grep -q "^OPENROUTER_API_KEY=" "$ENV_FILE" 2>/dev/null; then
    echo -e "  ${GREEN}✅ OPENROUTER_API_KEY already set in .env${NC}"
    set -a; source "$ENV_FILE" 2>/dev/null || true; set +a
elif [ -f "$ENV_FILE" ] && ! grep -q "^OPENROUTER_API_KEY=" "$ENV_FILE" 2>/dev/null; then
    # Prompt only if --with-ai is set
    if [ "$WITH_AI" = true ]; then
        echo ""
        echo "  ${CYAN}Phase 11:${NC} AI Orchestrator needs an OpenRouter API key (free, no card)."
        echo "  Get one at: https://openrouter.ai/keys"
        echo "  (Or pass: ${CYAN}./deploy.sh --openrouter-key=YOUR_KEY --with-ai${NC})"
        echo ""
        read -r -p "  Enter your OpenRouter API key (or press Enter to skip): " USER_OPENROUTER
        if [ -n "$USER_OPENROUTER" ]; then
            env_set "OPENROUTER_API_KEY" "$USER_OPENROUTER"
            export OPENROUTER_API_KEY="$USER_OPENROUTER"
            echo -e "  ${GREEN}✅ OPENROUTER_API_KEY set${NC}"
        fi
    fi
fi

# ── Step 3: Create data directory ──
echo ""
echo -e "${YELLOW}[3/6]${NC} Creating data directories..."
mkdir -p "$DATA_DIR"
echo -e "  ${GREEN}✅ $DATA_DIR ready${NC}"

# ── Step 4: Install Python dependencies ──
echo ""
echo -e "${YELLOW}[4/6]${NC} Installing Python dependencies..."
if [ ! -f "$REQUIREMENTS" ]; then
    echo -e "${RED}  ❌ Requirements file not found at $REQUIREMENTS${NC}"
    exit 1
fi
echo -e "  Installing from $REQUIREMENTS (this may take 2-5 minutes)..."
"$PYTHON" -m pip install --upgrade pip -q 2>/dev/null || true
if "$PYTHON" -m pip install -r "$REQUIREMENTS" -q 2>&1; then
    echo -e "  ${GREEN}✅ Core dependencies installed${NC}"
else
    echo -e "  ${YELLOW}⚠️  Core install had warnings. Installing prophet separately...${NC}"
    # Install prophet first (often the troublemaker), then the rest
    "$PYTHON" -m pip install prophet 2>&1 | tail -3 || true
    "$PYTHON" -m pip install -r "$REQUIREMENTS" 2>&1 | tail -3 || true
fi

# ── Step 4b: Build flip-board frontend (optional, skipped if dist/ exists) ──
FRONTEND_DIR="$PROJECT_ROOT/mandi_rdd/dashboard/frontend"
if [ -d "$FRONTEND_DIR/dist" ]; then
    echo -e "  ${GREEN}✅ Pre-built flip-board bundle found — skipping npm build${NC}"
else
    echo -e "${YELLOW}[4b/6]${NC} Building flip-board frontend component (Node.js)..."
    if command -v npm >/dev/null 2>&1; then
        (cd "$FRONTEND_DIR" && npm install && npm run build) \
            && echo -e "  ${GREEN}✅ Flip-board bundle built${NC}" \
            || echo -e "  ${YELLOW}⚠️  Frontend build failed — dashboard will use st.metric fallback${NC}"
    else
        echo -e "  ${YELLOW}⚠️  npm not found — skipping flip-board build (dashboard uses st.metric fallback)${NC}"
    fi
fi

# ── Step 5a (Optional): Install NDVI / Earth Engine deps ──
if [ "$WITH_NDVI" = true ]; then
    echo ""
    echo -e "${YELLOW}[5a/6]${NC} Installing satellite NDVI dependencies (Phase 10)..."
    echo -e "  Installing earthengine-api + geospatial packages..."

    if "$PYTHON" -m pip install earthengine-api geemap geopandas rasterio shapely -q 2>&1; then
        echo -e "  ${GREEN}✅ NDVI dependencies installed${NC}"
        echo -e "  "
        echo -e "  ${YELLOW}ℹ️  Google Earth Engine authentication required:${NC}"
        echo -e "  Run this command to authenticate:"
        echo -e "  ${CYAN}earthengine authenticate${NC}"
        echo -e "  (or set GOOGLE_APPLICATION_CREDENTIALS in .env for headless auth)"
        echo -e "  "
        echo -e "  Then verify with:"
        echo -e "  ${CYAN}python -c 'import ee; ee.Initialize(); print(\"EE ready\")'${NC}"
    else
        echo -e "  ${YELLOW}⚠️  Some NDVI packages failed to install. See errors above.${NC}"
        echo -e "  You can install them later:"
        echo -e "  ${CYAN}pip install earthengine-api geemap geopandas rasterio shapely${NC}"
    fi
fi

# ── Step 5b (Optional): Install AI Orchestrator deps ──
if [ "$WITH_AI" = true ]; then
    echo ""
    echo -e "${YELLOW}[5b/6]${NC} Installing AI orchestrator dependencies (Phase 11)..."
    echo -e "  Installing openai SDK (OpenRouter-compatible)..."

    if "$PYTHON" -m pip install openai -q 2>&1; then
        echo -e "  ${GREEN}✅ openai SDK installed (OpenRouter-compatible)${NC}"
        echo -e "  "
        echo -e "  ${YELLOW}ℹ️  Set OPENROUTER_API_KEY in .env for the AI orchestrator:${NC}"
        echo -e "  Get a free key (no card) at: ${CYAN}https://openrouter.ai/keys${NC}"
        echo -e "  The orchestrator routes across free-tier models with circuit-breaker fallback."
    else
        echo -e "  ${YELLOW}⚠️  openai SDK install failed. Install later:${NC}"
        echo -e "  ${CYAN}pip install openai${NC}"
    fi
fi

# ── Step 6: Run Phase 1 static proof ──
echo ""
echo -e "${YELLOW}[6/6]${NC} Running Phase 1 static proof (go/no-go gate)..."
echo -e "  Testing commodity: ${CYAN}$COMMODITY${NC}"
echo ""

cd "$PROJECT_ROOT"
if [ -n "$STATE" ]; then
    "$PYTHON" -m mandi_rdd.analysis.static_proof --commodity "$COMMODITY" --state "$STATE"
else
    "$PYTHON" -m mandi_rdd.analysis.static_proof --commodity "$COMMODITY"
fi

EXIT_CODE=$?
echo ""
echo -e "${CYAN}════════════════════════════════════════════════════════${NC}"
if [ $EXIT_CODE -eq 0 ]; then
    echo -e "${GREEN}  ✅ GO decision — proceed to the full pipeline!${NC}"
else
    echo -e "${RED}  ❌ NO-GO decision — the -19% cutoff may not explain $COMMODITY prices.${NC}"
    echo -e "     Try a different commodity: ${YELLOW}./deploy.sh Tomato${NC}"
fi
echo -e "${CYAN}════════════════════════════════════════════════════════${NC}"
echo ""
echo -e "${GREEN}  🚀  Next steps:${NC}"
echo ""
echo -e "  ${CYAN}1.${NC} Run the full nightly pipeline:"
echo -e "     ${YELLOW}python -m mandi_rdd.ingestion.scheduler${NC}"
echo ""
echo -e "  ${CYAN}2.${NC} Launch the dashboard:"
echo -e "     ${YELLOW}streamlit run mandi_rdd/dashboard/app.py${NC}"
echo ""
echo -e "  ${CYAN}3.${NC} Start the API server:"
echo -e "     ${YELLOW}uvicorn mandi_rdd.api.main:app --reload${NC}"
echo ""
echo -e "  ${CYAN}4.${NC} Run tests:"
echo -e "     ${YELLOW}pytest mandi_rdd/tests/ -v${NC}"

if [ "$WITH_NDVI" = true ]; then
    echo ""
    echo -e "  ${CYAN}5.${NC} Authenticate Google Earth Engine:"
    echo -e "     ${YELLOW}earthengine authenticate${NC}"
    echo ""
    echo -e "  ${CYAN}6.${NC} Test NDVI data pull:"
    echo -e "     ${YELLOW}python -c 'import ee; ee.Initialize(); print(\"EE ready\")'${NC}"
    echo ""
    echo -e "  ${CYAN}7.${NC} Run Phase 10 NDVI ingestion (when implemented):"
    echo -e "     ${YELLOW}python -m mandi_rdd.ingestion.fetch_ndvi${NC}"
fi

if [ "$WITH_AI" = true ]; then
    echo ""
    echo -e "  ${CYAN}8.${NC} Try the 'Ask MandiIQ' AI chat:"
    echo -e "     Open the dashboard (step 2) and find the chat panel on the Executive Overview page."
    echo ""
    echo -e "  ${CYAN}9.${NC} Or query the AI orchestrator API directly:"
    echo -e "     ${YELLOW}curl -X POST http://localhost:8000/ask -H 'Content-Type: application/json' -d '{\"query\":\"Should I lock in onion procurement in Nashik?\"}'${NC}"
fi

echo ""
echo -e "${CYAN}════════════════════════════════════════════════════════${NC}"
echo -e "${GREEN}  📘  PRD v6 — Full feature set (OpenRouter multi-model routing):${NC}"
echo -e "  ${CYAN}•${NC} Phases 1-4: Causal RDD + robustness + FE cross-check  ${GREEN}✅${NC}"
echo -e "  ${CYAN}•${NC} Phases 5-6: Prophet vs LSTM + XGBoost classifier      ${GREEN}✅${NC}"
echo -e "  ${CYAN}•${NC} Phase 7: Prescriptive Procurement Advisor             ${GREEN}✅${NC}"
echo -e "  ${CYAN}•${NC} Phase 8: 5-page dashboard                            ${GREEN}✅${NC}"
echo -e "  ${CYAN}•${NC} Phase 9: Tests + CI + Docker + deployment             ${GREEN}✅${NC}"
if [ "$WITH_NDVI" = true ]; then
    echo -e "  ${CYAN}•${NC} Phase 10: Satellite NDVI layer (installing now)     ${YELLOW}⏳${NC}"
else
    echo -e "  ${CYAN}•${NC} Phase 10: Satellite NDVI layer (optional)           ${YELLOW}skip${NC}"
    echo -e "     Re-run with: ${CYAN}./deploy.sh --with-ndvi${NC}"
fi
if [ "$WITH_AI" = true ]; then
    echo -e "  ${CYAN}•${NC} Phase 11: AI orchestrator + chat-first UX            ${YELLOW}⏳${NC}"
else
    echo -e "  ${CYAN}•${NC} Phase 11: AI orchestrator + chat-first UX            ${YELLOW}skip${NC}"
    echo -e "     Re-run with: ${CYAN}./deploy.sh --with-ai${NC}"
fi
echo -e "${CYAN}════════════════════════════════════════════════════════${NC}"
echo ""
