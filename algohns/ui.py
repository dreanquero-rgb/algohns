"""Shared Streamlit UI helpers and Algohns branding.

Keeps the gold / cyan / midnight palette inherited from Algohns V11 so the
Python dashboard feels continuous with the previous Cloudflare Worker UI.
"""
from __future__ import annotations

import streamlit as st

GOLD = "#E2B86B"
CYAN = "#38BDF8"
MIDNIGHT = "#070B13"
SLATE = "#0F172A"
INK = "#F8FAFC"

_CSS = f"""
<style>
:root {{
    --gold: {GOLD};
    --cyan: {CYAN};
    --midnight: {MIDNIGHT};
}}
.stApp {{
    background: radial-gradient(1200px 600px at 20% -10%, #10233a 0%, {MIDNIGHT} 55%);
}}
h1, h2, h3 {{ letter-spacing: .3px; }}
.algohns-badge {{
    display:inline-block; padding:2px 10px; border-radius:999px;
    background:linear-gradient(90deg, {GOLD}, {CYAN}); color:{MIDNIGHT};
    font-weight:700; font-size:.72rem; text-transform:uppercase;
}}
.algohns-card {{
    background: rgba(15,23,42,.65); border:1px solid rgba(56,189,248,.18);
    border-radius:16px; padding:18px 20px; margin-bottom:12px;
}}
div[data-testid="stMetricValue"] {{ color: {GOLD}; }}
.paper-lock {{
    background: rgba(226,184,107,.12); border:1px solid {GOLD};
    color:{GOLD}; padding:8px 14px; border-radius:12px; font-weight:600;
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
            "✏️ **Editing is live; running is off on this deployment.** "
            "Set `ALGO_ALLOW_CODE_EXEC=true` (in your local `.env` or the Docker "
            "environment) to run edited code. Public deployments keep it off so "
            "the page cannot be turned into an arbitrary-code console."
        )

    code = st.text_area(
        "code", value=st.session_state[state_key], height=height,
        key=f"__ta__::{key}", label_visibility="collapsed",
    )
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
