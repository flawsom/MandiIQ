"""
MandiIQ — Ask MandiIQ Full Page.

Expanded chat interface with conversation history.
Dedicated route for in-depth Q&A sessions.

Design: crosshair chat cards, interpretation boxes,
section headers, consistent monochrome-lime palette.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent))

import os
import streamlit as st
from mandi_rdd.dashboard.theme import inject_theme, TURMERIC, RUST, SAGE, SLATE, MUTED, FAINT, get_api_base

API_BASE = get_api_base()


def render():
    inject_theme()

    # ── Hero Header ──
    st.markdown("""
        <div class="page-hero" style="margin-bottom:2rem;">
          <div>
            <div style="font-family:'IBM Plex Mono',monospace;font-size:0.75rem;color:#d7ff00;text-transform:uppercase;letter-spacing:0.1em;margin-bottom:0.5rem;">
              AI Procurement Chat
            </div>
            <h1 style="font-family:'Space Grotesk',system-ui,sans-serif;font-weight:300;font-size:clamp(1.6rem,3vw,2.4rem);color:#ffffff;letter-spacing:0.03em;text-transform:uppercase;margin-bottom:0.5rem;">
              Ask MandiIQ — <span style="font-weight:600;color:#d7ff00;">Full Chat</span>
            </h1>
            <p style="color:#7e7e7e;max-width:680px;line-height:1.7;font-size:0.9rem;">
                Ask procurement questions in plain English. Answers are grounded in live data —
                real mandi prices, rainfall, and NDVI from official sources. No speculation, no mock data.
            </p>
          </div>
        </div>
    """, unsafe_allow_html=True)

    # ── Check API availability ──
    api_available = False
    api_error = None
    try:
        import requests
        resp = requests.get(f"{API_BASE}/health", timeout=3)
        if resp.status_code == 200:
            api_available = True
        else:
            api_error = f"API server returned status {resp.status_code}"
    except ImportError:
        api_error = "`requests` library not installed. Run: `pip install requests`"
    except Exception as e:
        api_error = f"Cannot reach API at `{API_BASE}`. Start with: `uvicorn mandi_rdd.api.main:app --reload`"

    if not api_available:
        st.markdown(
            '<div class="interpretation-box insig-box">'
            f'ℹ️ API server not reachable. {api_error}</div>',
            unsafe_allow_html=True,
        )

    # ── Check AI Provider ──
    gemini_key = bool(os.environ.get("GEMINI_API_KEY"))
    openrouter_key = bool(os.environ.get("OPENROUTER_API_KEY"))

    if not (gemini_key or openrouter_key):
        st.markdown(
            '<div class="interpretation-box insig-box">'
            'ℹ️ No LLM key in this app\'s env. The API server handles the model call — '
            'if Render has GEMINI_API_KEY/OPENROUTER_API_KEY set, chat still works.'
            '</div>',
            unsafe_allow_html=True,
        )

    # ── Chat Interface ──
    if "chat_history" not in st.session_state:
        st.session_state.chat_history = []

    # Display chat history
    for entry in st.session_state.chat_history:
        _render_chat_entry(entry)

    # Input area with section label
    st.markdown("<hr style='border-color:rgba(255,255,255,0.07);margin:1.5rem 0;'>", unsafe_allow_html=True)
    st.markdown("""
        <div style="margin-top:0.5rem;">
          <div style="font-family:'IBM Plex Mono',monospace;font-size:0.75rem;color:#d7ff00;text-transform:uppercase;letter-spacing:0.08em;margin-bottom:0.5rem;">
            Your Question
          </div>
        </div>
    """, unsafe_allow_html=True)

    col1, col2 = st.columns([6, 1])
    with col1:
        query = st.text_area(
            "Type your question",
            placeholder="e.g. Should I lock in onion procurement in Nashik next month? What's the price trend?",
            height=100,
            label_visibility="collapsed",
        )
    with col2:
        ask = st.button("Ask", type="primary", use_container_width=True)
        clear = st.button("Clear", use_container_width=True)

    if clear:
        st.session_state.chat_history = []
        st.rerun()

    if ask and query.strip():
        _send_query(query.strip())


def _send_query(query: str):
    """Send query to API and store response."""
    import requests

    with st.spinner("Thinking..."):
        try:
            resp = requests.post(
                f"{API_BASE}/ask",
                json={"query": query, "commodity": None},
                timeout=60,
            )

            if resp.status_code == 200:
                result = resp.json()
            else:
                try:
                    detail = resp.json()
                except Exception:
                    detail = {"error": f"HTTP {resp.status_code}"}
                result = {
                    "query": query,
                    "answer": f"API error: {detail.get('detail', detail.get('error', resp.status_code))}",
                    "model_used": None,
                    "error": True,
                }

            st.session_state.chat_history.append(result)
            st.rerun()

        except requests.exceptions.Timeout:
            st.session_state.chat_history.append({
                "query": query,
                "answer": "Request timed out. The model may be slow. Try again.",
                "model_used": None,
                "error": True,
            })
            st.rerun()
        except Exception as e:
            st.session_state.chat_history.append({
                "query": query,
                "answer": f"Error: {e}",
                "model_used": None,
                "error": True,
            })
            st.rerun()


def _render_chat_entry(entry: dict):
    """Render a single chat entry with crosshair panels and styling."""
    query = entry.get("query", "")
    answer = entry.get("answer", "No answer generated.")
    model_used = entry.get("model_used")
    error = entry.get("error", False)

    # User query — subtle right-aligned pill
    st.markdown(f"""
        <div style="margin:1rem 0 0.5rem;display:flex;justify-content:flex-end;">
            <div style="padding:0.6rem 1rem;background:rgba(215,255,0,0.04);border:1px solid rgba(215,255,0,0.1);border-radius:8px;display:inline-block;max-width:80%;">
                <span style="color:{FAINT};font-family:'IBM Plex Mono',monospace;font-size:0.7rem;display:block;margin-bottom:0.25rem;">
                    YOU
                </span>
                <span style="color:#ffffff;font-size:0.9rem;">{query}</span>
            </div>
        </div>
    """, unsafe_allow_html=True)

    # Answer box — crosshair panel with lime corner markers
    error_style = "border-left:3px solid #D9663B;" if error else ""
    st.markdown(f"""
        <div class="crosshair-panel" style="padding:1.2rem;line-height:1.7;font-size:0.9rem;{error_style}">
            <div style="font-family:'IBM Plex Mono',monospace;font-size:0.7rem;color:#7e7e7e;margin-bottom:0.4rem;text-transform:uppercase;letter-spacing:0.06em;">
                ASSISTANT{' — Error' if error else ''}
            </div>
            {answer}
        </div>
    """, unsafe_allow_html=True)

    # Model served metadata — sleek inline badge
    if model_used:
        st.markdown(f"""
            <div style="display:flex;justify-content:flex-end;margin-top:-0.2rem;margin-bottom:1.5rem;">
                <span style="background:rgba(255,255,255,0.03);border:1px solid rgba(255,255,255,0.07);border-radius:4px;padding:0.2rem 0.6rem;font-family:'IBM Plex Mono',monospace;font-size:0.65rem;color:#7e7e7e;">
                    served by <span style="color:#d7ff00;">{model_used}</span>
                </span>
            </div>
        """, unsafe_allow_html=True)

