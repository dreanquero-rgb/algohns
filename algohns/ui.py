"""Shared Streamlit UI helpers and Algohns branding.

Keeps the gold / cyan / midnight palette inherited from Algohns V11 so the
Python dashboard feels continuous with the previous Cloudflare Worker UI.
"""
from __future__ import annotations

import urllib.parse

import streamlit as st

# Brand palette: black terminal with red/green accents.
# Names kept as GOLD/CYAN for backward-compatible imports; GOLD is now the
# up/green accent and CYAN the down/red accent.
GOLD = "#16C784"   # green (up / positive / primary accent)
CYAN = "#EA3943"   # red (down / negative)
GREEN = GOLD
RED = CYAN
GOLD_LEAF = "#D4AF37"   # metallic gold — the bull & bear motif
MIDNIGHT = "#000000"
SLATE = "#0E0F10"
INK = "#F5F7F8"

# Gold bull (horned head) + bear (round-eared head) silhouettes, tiled faintly
# across the whole site as a dark-background watermark. Built as a URL-encoded
# SVG data URI so it works as a plain CSS background layer on every page.
_BULLBEAR_TILE = (
    "<svg xmlns='http://www.w3.org/2000/svg' width='340' height='210' "
    "viewBox='0 0 340 210'>"
    f"<g fill='{GOLD_LEAF}' fill-opacity='0.065'>"
    # --- bull head (horns up, tapering muzzle) ---
    "<g transform='translate(26,54)'>"
    "<path d='M50 30 C40 13 19 9 5 16 C18 20 31 27 35 41 C40 34 45 31 50 31 Z'/>"
    "<path d='M50 30 C60 13 81 9 95 16 C82 20 69 27 65 41 C60 34 55 31 50 31 Z'/>"
    "<ellipse cx='27' cy='45' rx='9' ry='5' transform='rotate(-20 27 45)'/>"
    "<ellipse cx='73' cy='45' rx='9' ry='5' transform='rotate(20 73 45)'/>"
    "<path d='M32 39 C38 34 62 34 68 39 C73 55 60 82 50 88 C40 82 27 55 32 39 Z'/>"
    "</g>"
    # --- bear head (round, small ears, snout) ---
    "<g transform='translate(206,56)'>"
    "<circle cx='27' cy='20' r='10'/><circle cx='73' cy='20' r='10'/>"
    "<circle cx='50' cy='52' r='32'/><ellipse cx='50' cy='66' rx='13' ry='10'/>"
    "</g>"
    "</g></svg>"
)
_BULLBEAR_URI = urllib.parse.quote(_BULLBEAR_TILE, safe="")

_CSS = f"""
<style>
:root {{
    --green: {GREEN};
    --red: {RED};
    --gold: {GOLD};
    --cyan: {CYAN};
    --midnight: {MIDNIGHT};
}}
.stApp {{
    background-color: {MIDNIGHT};
    background-image:
        url("data:image/svg+xml,{_BULLBEAR_URI}"),
        radial-gradient(1100px 560px at 18% -12%, #17120a 0%, {MIDNIGHT} 60%);
    background-repeat: repeat, no-repeat;
    background-size: 340px 210px, cover;
    background-position: center top, center;
    background-attachment: fixed, fixed;
}}
h1, h2, h3 {{ letter-spacing: .3px; color: {INK}; }}
.algohns-badge {{
    display:inline-block; padding:2px 10px; border-radius:999px;
    background:linear-gradient(90deg, {GREEN}, {RED}); color:#000000;
    font-weight:700; font-size:.72rem; text-transform:uppercase;
}}
.algohns-card {{
    background: rgba(14,15,16,.85); border:1px solid rgba(22,199,132,.28);
    border-radius:16px; padding:18px 20px; margin-bottom:12px;
}}
.algohns-card:hover {{ border-color: rgba(22,199,132,.55); }}
div[data-testid="stMetricValue"] {{ color: {GREEN}; }}
.paper-lock {{
    background: rgba(22,199,132,.12); border:1px solid {GREEN};
    color:{GREEN}; padding:8px 14px; border-radius:12px; font-weight:600;
}}
</style>
"""


def inject_theme() -> None:
    """Apply the Algohns theme (call once per page)."""
    st.markdown(_CSS, unsafe_allow_html=True)


def header(title: str, subtitle: str = "", badge: str = "") -> None:
    inject_theme()
    if badge:
        st.markdown(f'<span class="algohns-badge">{badge}</span>', unsafe_allow_html=True)
    st.title(title)
    if subtitle:
        st.caption(subtitle)


def paper_lock_banner() -> None:
    st.markdown(
        '<div class="paper-lock">🔒 PAPER TRADING ONLY — real-money execution is locked platform-wide.</div>',
        unsafe_allow_html=True,
    )


def dependency_notice(exc: Exception) -> None:
    """Render an actionable message when an optional dependency is missing."""
    st.warning(f"⚙️ {exc}")
    st.caption("Install the extra listed above, then rerun this page.")


# ---------------------------------------------------------------------------
# Source viewer
#
# The platform is meant to be read, not just run: a reviewer should be able to
# see the exact code that produced a number without leaving the page. These
# helpers pull live source with `inspect`, so what is displayed is always the
# code that actually ran — it cannot drift from a pasted copy.
# ---------------------------------------------------------------------------
def code_panel(
    targets,
    *,
    title: str = "Source code",
    intro: str = "",
    expanded: bool = False,
    filename: str = "algohns_source.py",
) -> None:
    """Render the real source of modules/classes/functions in an expander.

    `targets` is a single object or a list of (label, object) pairs. Objects may
    be modules, classes or functions — anything `inspect.getsource` accepts.
    """
    import inspect

    if not isinstance(targets, (list, tuple)):
        targets = [(getattr(targets, "__name__", "source"), targets)]

    blocks: list[tuple[str, str]] = []
    for item in targets:
        label, obj = item if isinstance(item, (list, tuple)) else (
            getattr(item, "__name__", "source"), item)
        # Memoise per session: `inspect.getsource` re-reads and re-parses the
        # file, and Streamlit re-runs the whole script on every interaction, so
        # an uncached panel would re-read every module on each widget change.
        key = (f"_src::{getattr(obj, '__module__', '')}::"
               f"{getattr(obj, '__qualname__', getattr(obj, '__name__', repr(obj)))}")
        if key not in st.session_state:
            try:
                st.session_state[key] = inspect.getsource(obj)
            except (OSError, TypeError) as exc:  # pragma: no cover - defensive
                st.session_state[key] = f"# source unavailable: {exc}"
        blocks.append((label, st.session_state[key]))

    with st.expander(f"🐍 {title}", expanded=expanded):
        if intro:
            st.caption(intro)
        total = sum(len(src.splitlines()) for _, src in blocks)
        st.caption(f"{len(blocks)} unit(s) · {total} lines · pulled live with `inspect`.")
        if len(blocks) == 1:
            st.code(blocks[0][1], language="python", line_numbers=True)
        else:
            for tab, (label, src) in zip(st.tabs([b[0] for b in blocks]), blocks):
                with tab:
                    st.code(src, language="python", line_numbers=True)
        joined = "\n\n\n".join(
            f"# {'=' * 74}\n# {label}\n# {'=' * 74}\n{src}" for label, src in blocks
        )
        st.download_button(
            "⬇️ Download this source", joined.encode(), file_name=filename,
            mime="text/x-python", key=f"dl_{filename}_{abs(hash(title))}",
        )


# ---------------------------------------------------------------------------
# Live, editable code cell
#
# The read-only `code_panel` above shows the engine; this is the opposite side
# of the same idea — a cell the user can edit and run themselves, without going
# through anyone. Execution is opt-in (ALGO_ALLOW_CODE_EXEC) and sandboxed, so
# the editor is always live but "Run" is only active where it is safe to be:
# your own machine / Docker, never a public deployment.
# ---------------------------------------------------------------------------
def _code_input(value: str, *, key: str, height: int = 340) -> str:
    """A real, editable code editor (Ace) with a plain-textarea fallback.

    streamlit-ace gives a syntax-highlighted, unmistakably-editable editor. If
    it is not installed (or fails to load) it falls back to a tall text area,
    which is still fully editable — the point is that the code on screen is
    something the user can change, not a read-only listing.
    """
    try:
        from streamlit_ace import st_ace

        out = st_ace(
            value=value, language="python", theme="tomorrow_night",
            keybinding="vscode", font_size=13, tab_size=4, show_gutter=True,
            wrap=False, auto_update=False, min_lines=12, height=height,
            key=f"__ace__::{key}",
        )
        return out if out is not None else value
    except Exception:  # noqa: BLE001 - component missing → editable textarea
        return st.text_area(
            "code", value=value, height=height, key=f"__ta__::{key}",
            label_visibility="collapsed",
        )


def code_editor(
    seed_code: str,
    context_factory,
    *,
    result_var: str = "result",
    render_result=None,
    title: str = "Edit & run",
    intro: str = "",
    key: str,
    filename: str = "algohns_edit.py",
    height: int = 340,
) -> None:
    """Render an editable, runnable code cell.

    ``seed_code`` pre-fills the editor. ``context_factory`` is a zero-argument
    callable returning the objects to inject (called only on Run, so heavy
    objects are not rebuilt on every widget change). After a run the value of
    ``result_var`` is passed to ``render_result(value)`` if given, else shown
    with ``st.write``.
    """
    from algohns.config import get_settings
    from algohns.modules.code_sandbox import SandboxError, run_user_code

    st.markdown(f"**{title}**")
    if intro:
        st.caption(intro)

    state_key = f"__editor__::{key}"
    if state_key not in st.session_state:
        st.session_state[state_key] = seed_code

    enabled = get_settings().allow_code_exec
    if not enabled:
        st.info(
            "✏️ **You can type in the editor below; the ▶ Run button is off on "
            "this deployment.** Set `ALGO_ALLOW_CODE_EXEC=true` (local `.env` or "
            "the Docker/Streamlit environment) to run edited code. Public "
            "deployments keep running off so the page can't become a code console."
        )

    code = _code_input(st.session_state[state_key], key=key, height=height)
    st.session_state[state_key] = code

    cols = st.columns([1, 1, 3])
    run = cols[0].button("▶ Run", key=f"__run__::{key}", type="primary",
                         disabled=not enabled)
    if cols[1].button("↩ Reset", key=f"__reset__::{key}"):
        st.session_state[state_key] = seed_code
        st.rerun()
    cols[2].download_button(
        "⬇️ Download", code.encode(), file_name=filename,
        mime="text/x-python", key=f"__dl__::{key}",
    )

    if run:
        try:
            ctx = context_factory()
            out = run_user_code(code, ctx, result_var=result_var)
        except SandboxError as exc:
            st.error(f"⚠️ {exc}")
            return
        if out.stdout.strip():
            st.code(out.stdout, language="text")
        if out.result is None:
            st.warning(
                f"The code ran but never set a `{result_var}` variable, so there "
                "is nothing to display. Assign your output to "
                f"`{result_var}` and run again."
            )
        elif render_result is not None:
            render_result(out.result)
        else:
            st.write(out.result)
