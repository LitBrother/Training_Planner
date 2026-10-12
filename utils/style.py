import streamlit as st

CSS = """
<style>
.block-container { padding-top: 2rem; max-width: 1200px; }
h1 { font-weight: 800; letter-spacing: -0.5px;
     background: linear-gradient(90deg, #00D4AA, #4F8CFF);
     -webkit-background-clip: text; -webkit-text-fill-color: transparent; }
[data-testid="stMetric"] { background: #1A1F2B; border: 1px solid #2A3142; border-radius: 14px;
     padding: 14px 18px; box-shadow: 0 4px 14px rgba(0,0,0,.25); transition: transform .15s; }
[data-testid="stMetric"]:hover { transform: translateY(-2px); border-color: #00D4AA; }
[data-testid="stMetricValue"] { font-weight: 700; }
.stButton > button, .stDownloadButton > button { border-radius: 10px; border: 1px solid #2A3142;
     transition: all .15s; }
.stButton > button:hover, .stDownloadButton > button:hover { border-color: #00D4AA; color: #00D4AA;
     transform: translateY(-1px); }
[data-testid="stForm"] { border-radius: 14px; border: 1px solid #2A3142; background: #151A24; }
[data-testid="stSidebar"] { background: #121722; border-right: 1px solid #2A3142; }
[data-testid="stDataFrame"] { border-radius: 12px; overflow: hidden; }
#MainMenu, footer { visibility: hidden; }
</style>
"""


def inject_css() -> None:
    st.markdown(CSS, unsafe_allow_html=True)