"""🧩 Code Studio — view & edit the engine source in the browser (private).

A VSCode-style page over a curated set of engine modules (backtest, strategy
lab, HFT strategies, world simulation, bond calculator). Edits are saved as
durable overrides, validated before saving, and — on this private instance —
take effect on the live pages that import the engine. The shipped source is
never overwritten, and "Ripristina originale" brings it back.

The Alpaca execution engine and anything that handles the API keys are
deliberately NOT editable here.
"""
from __future__ import annotations

import streamlit as st

from algohns.config import get_settings
from algohns.core import code_store as cs
from algohns.ui import header

header(
    "Code Studio",
    "Modifica i motori in Python come pagine VSCode — valida, esegui, salva, ripristina.",
    "🧩 Developer",
)

settings = get_settings()
if not settings.allow_code_exec:
    st.warning("Esecuzione del codice disattivata. Fai **login** con il codice "
               "creatore per abilitarla (serve per 'Esegui' e per applicare gli "
               "edit alle pagine live).")

labels = cs.labels()
key = st.selectbox("Modulo motore", list(labels), format_func=lambda k: labels[k])

modified = cs.has_override(key)
st.caption(
    (f"🟡 **Modificato** — override attivo · `algohns/modules/{key}.py`"
     if modified else
     f"⚪ Originale (come da repo) · `algohns/modules/{key}.py`"))

# A per-module version counter: bumping it makes a fresh text_area widget that
# re-seeds from `value=` (used to reload / revert). We never mutate a widget's
# own session_state key after it is drawn — Streamlit forbids that.
vkey = f"codestudio_ver_{key}"
st.session_state.setdefault(vkey, 0)
ed_key = f"codestudio_src_{key}_{st.session_state[vkey]}"

edited = st.text_area("Codice", value=cs.current_source(key), key=ed_key,
                      height=540, label_visibility="collapsed")

b = st.columns(5)
if b[0].button("💾 Salva", type="primary", use_container_width=True):
    ok, err = cs.save_override(key, edited)
    if ok:
        st.success("Salvato. Attivo al prossimo caricamento delle pagine che "
                   "usano questo motore.")
        st.rerun()
    else:
        st.error(f"Non salvato — {err}")

if b[1].button("✓ Valida", use_container_width=True):
    ok, err = cs.validate(edited)
    if ok:
        st.success("Sintassi OK.")
    else:
        st.error(err)

if b[2].button("▶ Esegui (isolato)", use_container_width=True,
               disabled=not settings.allow_code_exec):
    try:
        mod = cs.load_module(key, source=edited)
        pub = [n for n in vars(mod) if not n.startswith("_")]
        st.success(f"Caricato senza errori — {len(pub)} oggetti pubblici.")
        st.code(", ".join(pub[:50]) or "(nessuno)")
    except Exception as exc:  # noqa: BLE001
        st.error(f"Errore di esecuzione: {type(exc).__name__}: {exc}")

if b[3].button("↻ Ricarica", use_container_width=True,
               help="Scarta le modifiche non salvate in questo editor"):
    st.session_state[vkey] += 1  # fresh widget re-seeds from current source
    st.rerun()

if modified and b[4].button("↩ Ripristina", use_container_width=True,
                            help="Elimina l'override e torna al codice del repo"):
    cs.revert(key)
    st.session_state[vkey] += 1  # re-seed the editor from the shipped source
    st.success("Ripristinato il codice originale.")
    st.rerun()

st.divider()
with st.expander("Come funziona (e perché è sicuro)"):
    st.markdown(
        "- Il file `.py` del repo **non viene mai sovrascritto**: le tue "
        "modifiche sono salvate come *override* separato e durevole.\n"
        "- **Valida** fa un controllo di sintassi; **Esegui** carica il codice "
        "in un modulo isolato: se c'è un errore lo vedi qui e **non** rompe il "
        "resto dell'app.\n"
        "- Al **Salva**, l'override viene attivato al prossimo run delle pagine "
        "che importano quel motore (es. Backtest, World Simulation, Bond).\n"
        "- **Ripristina** cancella l'override e torna al codice del repo.\n"
        "- Questa pagina è dietro al **login**: nessun visitatore anonimo può "
        "vederla o eseguire codice. Il motore che tocca le chiavi Alpaca **non** "
        "è editabile da qui, di proposito."
    )
