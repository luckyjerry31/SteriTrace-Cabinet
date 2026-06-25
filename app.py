import base64
import json
import sqlite3
from datetime import date, datetime, timedelta
from io import BytesIO
from typing import Any, Dict, List, Optional

import pandas as pd
import qrcode
import streamlit as st
import streamlit.components.v1 as components

try:
    from supabase import create_client
except Exception:  # pragma: no cover
    create_client = None


# =========================================================
# CONFIGURATION CABINET
# =========================================================
APP_NAME = "SteriTrace"
APP_SUBTITLE = "Traçabilité stérilisation, lots et dossiers patients"

OPERATORS = {
    "1234": "Dr Sébastien",
    "5678": "Assistante AD1",
    "9012": "Manon Moreau",
}

STERILIZERS = {
    "Autoclave 1": "SN-AUTO-001",
    "Autoclave 2": "SN-AUTO-002",
    "DAC Universal": "SN-DAC-001",
}

DEVICES = [
    {"name": "Contre-angle", "category": "Rotatifs", "shelf_days": 90, "favorite": True},
    {"name": "Curette", "category": "Soins", "shelf_days": 90, "favorite": True},
    {"name": "K7 soins", "category": "Soins", "shelf_days": 90, "favorite": True},
    {"name": "Miroir", "category": "Soins", "shelf_days": 90, "favorite": True},
    {"name": "Fraise", "category": "Rotatifs", "shelf_days": 90, "favorite": False},
    {"name": "Sonde", "category": "Soins", "shelf_days": 90, "favorite": False},
    {"name": "Précelles", "category": "Soins", "shelf_days": 90, "favorite": False},
    {"name": "Davier", "category": "Chirurgie", "shelf_days": 90, "favorite": False},
    {"name": "Turbine", "category": "Rotatifs", "shelf_days": 90, "favorite": False},
    {"name": "Cassette chirurgie", "category": "Chirurgie", "shelf_days": 180, "favorite": False},
]

CYCLE_TYPES = [
    "Prion 134°C - 18 min",
    "Helix",
    "Vacuum",
    "Bowie-Dick",
]


# =========================================================
# STREAMLIT SETUP
# =========================================================
st.set_page_config(
    page_title=APP_NAME,
    page_icon="🛡️",
    layout="wide",
    initial_sidebar_state="expanded",
)


# =========================================================
# UTILITAIRES
# =========================================================
def clean_supabase_url(url: str) -> str:
    url = (url or "").strip()
    if "/rest/v1" in url:
        url = url.split("/rest/v1")[0]
    return url.rstrip("/")


def now_iso() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def today_iso() -> str:
    return date.today().isoformat()


def current_month_label() -> str:
    return datetime.now().strftime("%B %Y")


def fr_date(value: Any) -> str:
    if not value:
        return ""
    text = str(value)
    try:
        if "T" in text:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        elif " " in text:
            dt = datetime.strptime(text[:19], "%Y-%m-%d %H:%M:%S")
        else:
            dt = datetime.strptime(text[:10], "%Y-%m-%d")
        return dt.strftime("%d/%m/%Y")
    except Exception:
        return text


def fr_datetime(value: Any) -> str:
    if not value:
        return ""
    text = str(value)
    try:
        if "T" in text:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        else:
            dt = datetime.strptime(text[:19], "%Y-%m-%d %H:%M:%S")
        return dt.strftime("%d/%m/%Y %H:%M")
    except Exception:
        return text


def make_qr_base64(payload: str) -> str:
    qr = qrcode.QRCode(version=2, error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=4, border=1)
    qr.add_data(payload)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("utf-8")


def safe_json_load(value: Any, default: Any) -> Any:
    if value is None:
        return default
    if isinstance(value, (list, dict)):
        return value
    try:
        return json.loads(value)
    except Exception:
        return default


def device_by_name(name: str) -> Dict[str, Any]:
    for device in DEVICES:
        if device["name"] == name:
            return device
    return {"name": name, "category": "Autre", "shelf_days": 90, "favorite": False}


def add_to_cart(device_name: str, qty: int = 1) -> None:
    cart = st.session_state.setdefault("cart", {})
    cart[device_name] = int(cart.get(device_name, 0)) + int(qty)


def remove_from_cart(device_name: str) -> None:
    cart = st.session_state.setdefault("cart", {})
    if device_name in cart:
        del cart[device_name]


def cart_items() -> List[Dict[str, Any]]:
    items = []
    for name, qty in st.session_state.get("cart", {}).items():
        device = device_by_name(name)
        items.append({"name": name, "qty": int(qty), "category": device["category"], "shelf_days": device["shelf_days"]})
    return items


def cart_total() -> int:
    return sum(item["qty"] for item in cart_items())


def render_print_button(label: str = "Imprimer") -> None:
    components.html(
        f"""
        <button onclick="window.print()" style="
            width:100%;
            background:#174f96;
            color:white;
            border:none;
            padding:12px 18px;
            border-radius:8px;
            font-weight:700;
            font-size:13px;
            cursor:pointer;">
            🖨️ {label}
        </button>
        """,
        height=52,
    )


def render_download_html(filename: str, html: str, label: str) -> None:
    b64 = base64.b64encode(html.encode("utf-8")).decode("utf-8")
    components.html(
        f"""
        <a download="{filename}" href="data:text/html;base64,{b64}" style="
            display:block;text-align:center;text-decoration:none;
            width:100%;background:#f8fafc;color:#174f96;border:1px solid #dbe5f0;
            padding:11px 18px;border-radius:8px;font-weight:700;font-size:13px;">
            ⬇️ {label}
        </a>
        """,
        height=52,
    )


# =========================================================
# STOCKAGE SUPABASE / SQLITE
# =========================================================
class Storage:
    def __init__(self) -> None:
        self.mode = "SQLite local démo"
        self.error = None
        self.client = None
        self.conn = None

        supabase_url = ""
        supabase_key = ""
        try:
            supabase_url = clean_supabase_url(st.secrets.get("SUPABASE_URL", ""))
            supabase_key = st.secrets.get("SUPABASE_KEY", "")
        except Exception:
            pass

        if create_client and supabase_url and supabase_key:
            try:
                self.client = create_client(supabase_url, supabase_key)
                self.client.table("sterilization_events").select("id").limit(1).execute()
                self.mode = "Supabase"
            except Exception as exc:
                self.error = str(exc)
                self.client = None

        if self.client is None:
            self.conn = sqlite3.connect("steritrace_v3_local.db", check_same_thread=False)
            self.conn.row_factory = sqlite3.Row
            self._init_sqlite()

    def _init_sqlite(self) -> None:
        cur = self.conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS sterilization_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_type TEXT,
                event_label TEXT,
                event_date TEXT,
                operator_name TEXT,
                sterilizer_name TEXT,
                sterilizer_serial TEXT,
                cycle_number TEXT,
                cycle_type TEXT,
                lot_number TEXT UNIQUE,
                lot_short TEXT,
                dlu_date TEXT,
                packaging_type TEXT,
                identification_mode TEXT,
                devices_json TEXT,
                devices_count INTEGER,
                checks_json TEXT,
                status TEXT,
                created_at TEXT
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS patient_traceability (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                patient_ref TEXT,
                act_label TEXT,
                practitioner_name TEXT,
                lot_number TEXT,
                lot_short TEXT,
                used_at TEXT,
                notes TEXT,
                created_at TEXT
            )
            """
        )
        self.conn.commit()

    def list_events(self, limit: int = 500) -> List[Dict[str, Any]]:
        if self.client:
            res = self.client.table("sterilization_events").select("*").order("created_at", desc=True).limit(limit).execute()
            rows = res.data or []
        else:
            rows = [dict(r) for r in self.conn.execute(
                "SELECT * FROM sterilization_events ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()]
        for row in rows:
            row["devices_json"] = safe_json_load(row.get("devices_json"), [])
            row["checks_json"] = safe_json_load(row.get("checks_json"), {})
        return rows

    def insert_event(self, payload: Dict[str, Any]) -> None:
        if self.client:
            self.client.table("sterilization_events").insert(payload).execute()
            return
        local = dict(payload)
        local["devices_json"] = json.dumps(local.get("devices_json", []), ensure_ascii=False)
        local["checks_json"] = json.dumps(local.get("checks_json", {}), ensure_ascii=False)
        cols = list(local.keys())
        placeholders = ",".join(["?"] * len(cols))
        self.conn.execute(
            f"INSERT INTO sterilization_events ({','.join(cols)}) VALUES ({placeholders})",
            [local[c] for c in cols],
        )
        self.conn.commit()

    def find_event_by_lot(self, value: str) -> Optional[Dict[str, Any]]:
        value = (value or "").strip()
        if not value:
            return None
        if self.client:
            res = self.client.table("sterilization_events").select("*").or_(
                f"lot_number.eq.{value},lot_short.eq.{value}"
            ).limit(1).execute()
            rows = res.data or []
            if not rows:
                return None
            row = rows[0]
        else:
            dbrow = self.conn.execute(
                "SELECT * FROM sterilization_events WHERE lot_number=? OR lot_short=? LIMIT 1",
                (value, value),
            ).fetchone()
            if not dbrow:
                return None
            row = dict(dbrow)
        row["devices_json"] = safe_json_load(row.get("devices_json"), [])
        row["checks_json"] = safe_json_load(row.get("checks_json"), {})
        return row

    def next_sequence(self) -> int:
        count = len(self.list_events(limit=2000))
        return count + 1

    def insert_patient_link(self, payload: Dict[str, Any]) -> None:
        if self.client:
            self.client.table("patient_traceability").insert(payload).execute()
            return
        cols = list(payload.keys())
        placeholders = ",".join(["?"] * len(cols))
        self.conn.execute(
            f"INSERT INTO patient_traceability ({','.join(cols)}) VALUES ({placeholders})",
            [payload[c] for c in cols],
        )
        self.conn.commit()

    def list_patient_links(self, limit: int = 200) -> List[Dict[str, Any]]:
        if self.client:
            res = self.client.table("patient_traceability").select("*").order("created_at", desc=True).limit(limit).execute()
            return res.data or []
        return [dict(r) for r in self.conn.execute(
            "SELECT * FROM patient_traceability ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()]


storage = Storage()


# =========================================================
# SESSION STATE
# =========================================================
if "menu" not in st.session_state:
    st.session_state.menu = "Dashboard"
if "label_step" not in st.session_state:
    st.session_state.label_step = 1
if "cart" not in st.session_state:
    st.session_state.cart = {}
if "draft" not in st.session_state:
    st.session_state.draft = {}
if "patient_cart" not in st.session_state:
    st.session_state.patient_cart = []


def reset_label_workflow() -> None:
    st.session_state.label_step = 1
    st.session_state.cart = {}
    st.session_state.draft = {}


# =========================================================
# CSS DESIGN INSPIRÉ SAAS MÉDICAL
# =========================================================
st.markdown(
    """
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap');

    :root {
        --blue:#174f96;
        --blue2:#2563eb;
        --green:#16a34a;
        --border:#dfe8f3;
        --muted:#637083;
        --light:#f7faff;
        --soft:#eef6ff;
        --ink:#0f172a;
    }

    html, body, [class*="css"] { font-family:'Inter', sans-serif; }
    .stApp { background:#f8fbff; color:var(--ink); }
    [data-testid="stHeader"], #MainMenu, footer { display:none!important; }
    .block-container { max-width:1220px!important; padding:18px 24px 60px 24px!important; }

    [data-testid="stSidebar"] {
        background:#ffffff;
        border-right:1px solid #e4edf7;
        box-shadow: 2px 0 18px rgba(15,23,42,0.03);
    }
    [data-testid="stSidebar"] .block-container { padding-top:18px!important; padding-left:16px!important; padding-right:14px!important; }

    h1, h2, h3 { letter-spacing:-0.03em; color:#13243a; }
    h1 { font-size:26px!important; font-weight:800!important; margin-bottom:3px!important; }
    h2 { font-size:20px!important; font-weight:800!important; }
    h3 { font-size:16px!important; font-weight:750!important; }
    p, li, label, span { color:#536579; }

    .app-logo { display:flex; align-items:center; gap:10px; margin-bottom:18px; }
    .app-logo .mark { width:36px; height:36px; border-radius:10px; background:#174f96; display:flex; align-items:center; justify-content:center; color:#fff; font-weight:800; }
    .app-logo .name { font-size:18px; font-weight:800; color:#13243a; }
    .app-logo .sub { font-size:11px; color:#7c8da3; margin-top:-2px; }

    .sidebar-small-title { font-size:10px; color:#a0aec0; font-weight:800; letter-spacing:.08em; text-transform:uppercase; margin:18px 0 8px; }
    .side-muted { font-size:12px; padding:8px 10px; border-radius:8px; color:#9aa9bb; display:flex; justify-content:space-between; }
    .side-disclaimer { font-size:10px; line-height:1.45; color:#8493a6; background:#f8fafc; border:1px solid #e6eef8; border-radius:10px; padding:10px; margin-top:18px; }

    .hero-title { display:flex; align-items:center; gap:10px; }
    .page-subtitle { color:#6b7c90; font-size:13px; margin-top:-2px; margin-bottom:18px; }
    .content-card { background:#fff; border:1px solid var(--border); border-radius:10px; padding:18px; box-shadow:0 8px 26px rgba(15,23,42,.03); margin-bottom:14px; }
    .flat-card { background:#fff; border:1px solid var(--border); border-radius:8px; padding:14px; margin-bottom:10px; }
    .blue-card { background:#eff6ff; border:1px solid #d8e9ff; border-radius:10px; padding:14px; }
    .kpi-row { display:flex; gap:0; border:1px solid var(--border); border-radius:8px; overflow:hidden; background:#fff; margin-bottom:12px; }
    .kpi-pill { flex:1; padding:11px 14px; border-right:1px solid #edf2f7; font-size:13px; color:#475569; }
    .kpi-pill:last-child { border-right:none; }
    .kpi-pill strong { color:#13243a; font-weight:800; }
    .metric-dot { display:inline-flex; width:18px; height:18px; border-radius:999px; align-items:center; justify-content:center; background:#edf6ff; color:#174f96; margin-right:6px; font-size:11px; }
    .green-dot { background:#ecfdf3; color:#16a34a; }
    .muted-dot { background:#f3f6fa; color:#64748b; }

    .two-grid { display:grid; grid-template-columns:1fr 1fr; gap:12px; }
    .three-grid { display:grid; grid-template-columns:repeat(3,1fr); gap:12px; }
    .tag { display:inline-flex; align-items:center; gap:5px; border:1px solid #dbeafe; color:#174f96; background:#f0f7ff; padding:4px 9px; border-radius:999px; font-size:12px; font-weight:700; margin-right:5px; }
    .tag.green { color:#15803d; border-color:#bbf7d0; background:#f0fdf4; }
    .tag.gray { color:#64748b; border-color:#e2e8f0; background:#f8fafc; }
    .small-muted { color:#7b8ca3; font-size:12px; }

    .stepper { display:flex; align-items:center; gap:8px; margin:8px 0 18px; }
    .step { flex:1; display:flex; align-items:center; gap:7px; font-size:12px; font-weight:800; color:#64748b; }
    .step-line { height:2px; background:#bfd3ea; flex:1; }
    .step .num { width:22px; height:22px; border-radius:999px; display:inline-flex; align-items:center; justify-content:center; border:1px solid #c9d8ea; background:#fff; color:#64748b; }
    .step.active .num { background:#174f96; border-color:#174f96; color:#fff; }
    .step.active { color:#174f96; }

    .device-card { background:#fff; border:1px solid #e2eaf4; border-radius:8px; padding:11px; min-height:84px; }
    .device-name { font-weight:800; color:#13243a; font-size:13px; }
    .device-sub { font-size:11px; color:#7b8ca3; margin-bottom:8px; }
    .selection-card { background:#fff; border:1px solid #dfe8f3; border-radius:10px; padding:16px; position:sticky; top:12px; }
    .cart-row { display:flex; align-items:center; justify-content:space-between; gap:10px; padding:8px 0; border-bottom:1px solid #eef2f7; }
    .cart-row:last-child { border-bottom:none; }

    .label-grid { display:grid; grid-template-columns:repeat(2, minmax(260px, 1fr)); gap:12px; }
    .qr-label { background:white; border:1px solid #dfe8f3; border-radius:8px; padding:10px; min-height:128px; display:flex; justify-content:space-between; gap:8px; color:#111827; }
    .qr-label-title { font-size:12px; font-weight:900; color:#0f172a; margin-bottom:4px; }
    .qr-label-text { font-size:9.5px; line-height:1.4; color:#111827; }
    .qr-brand { display:flex; align-items:center; justify-content:flex-end; gap:4px; font-size:11px; font-weight:800; color:#13243a; margin-bottom:5px; }
    .recap-panel { background:white; border:1px solid #dfe8f3; border-radius:10px; padding:16px; }
    .manual-mark { background:#174f96; color:white; border-radius:10px; padding:18px; margin-bottom:12px; }
    .manual-mark .big { font-size:28px; font-weight:900; color:white; line-height:1.2; }
    .manual-mark .subtext { font-size:12px; color:#dbeafe; margin-top:8px; }

    .doc-page { background:#fff; border:1px solid #dfe8f3; border-radius:8px; padding:0; overflow:hidden; box-shadow:0 10px 32px rgba(15,23,42,.05); }
    .doc-header { padding:18px 22px; border-bottom:1px solid #dfe8f3; display:flex; justify-content:space-between; align-items:flex-start; }
    .doc-band { height:34px; background:#174f96; color:white; font-size:12px; font-weight:800; display:flex; align-items:center; padding:0 22px; text-transform:uppercase; letter-spacing:.04em; }
    .doc-body { padding:22px; }
    .doc-table { width:100%; border-collapse:collapse; font-size:12px; }
    .doc-table th { text-align:left; color:#64748b; background:#f8fafc; border-bottom:1px solid #dfe8f3; padding:8px; font-size:11px; text-transform:uppercase; }
    .doc-table td { border-bottom:1px solid #edf2f7; padding:8px; color:#24364b; }

    .stButton > button { border-radius:8px!important; border:1px solid #cfe0f3!important; background:#fff!important; color:#174f96!important; font-weight:800!important; font-size:12px!important; min-height:38px; }
    .stButton > button:hover { border-color:#174f96!important; color:#174f96!important; background:#f1f7ff!important; }
    div[data-testid="stForm"] { border:0!important; padding:0!important; }
    div[data-testid="stDataFrame"] { border-radius:10px!important; overflow:hidden; }

    @media print {
        [data-testid="stSidebar"], [data-testid="stHeader"], #MainMenu, footer, .no-print, .stButton, .stDownloadButton { display:none!important; }
        .block-container { max-width:100%!important; padding:0!important; }
        .stApp { background:white!important; }
        .content-card, .flat-card, .doc-page { box-shadow:none!important; border:none!important; }
        .label-grid { grid-template-columns:repeat(2, 1fr); }
    }
    </style>
    """,
    unsafe_allow_html=True,
)


# =========================================================
# SIDEBAR
# =========================================================
with st.sidebar:
    st.markdown(
        f"""
        <div class="app-logo">
            <div class="mark">ST</div>
            <div>
                <div class="name">{APP_NAME}</div>
                <div class="sub">cabinet connecté</div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    menu = st.radio(
        "",
        ["Dashboard", "Étiquettes", "Dossiers stérilisation", "Dossiers traçabilité"],
        index=["Dashboard", "Étiquettes", "Dossiers stérilisation", "Dossiers traçabilité"].index(st.session_state.menu)
        if st.session_state.menu in ["Dashboard", "Étiquettes", "Dossiers stérilisation", "Dossiers traçabilité"] else 0,
        label_visibility="collapsed",
    )
    st.session_state.menu = menu

    st.markdown('<div class="sidebar-small-title">Configuration</div>', unsafe_allow_html=True)
    st.markdown('<div class="side-muted">🌡️ Stérilisateurs <span>🔒</span></div>', unsafe_allow_html=True)
    st.markdown('<div class="side-muted">👥 Opérateurs <span>🔒</span></div>', unsafe_allow_html=True)
    st.markdown('<div class="side-muted">📦 Dispositifs <span>🔒</span></div>', unsafe_allow_html=True)

    st.markdown('<div class="sidebar-small-title">Besoin d’en savoir plus ?</div>', unsafe_allow_html=True)
    st.markdown('<div class="side-muted">⬇️ Guide de stérilisation</div>', unsafe_allow_html=True)
    st.markdown('<div class="side-muted">⚖️ Réglementations</div>', unsafe_allow_html=True)

    st.markdown('<div class="sidebar-small-title">Une question ?</div>', unsafe_allow_html=True)
    st.markdown('<div class="side-muted">❔ FAQ</div>', unsafe_allow_html=True)
    st.markdown('<div class="side-muted">🎧 Contactez le support</div>', unsafe_allow_html=True)

    st.markdown(
        """
        <div class="side-disclaimer">
        🟠 SteriTrace est un outil d’aide à la traçabilité. Il ne remplace pas la procédure qualité du cabinet ni une validation réglementaire.
        </div>
        """,
        unsafe_allow_html=True,
    )


# =========================================================
# COMPOSANTS VISUELS
# =========================================================
def page_header(icon: str, title: str, subtitle: str) -> None:
    st.markdown(
        f"""
        <div class="hero-title"><h1>{icon} {title}</h1></div>
        <div class="page-subtitle">{subtitle}</div>
        """,
        unsafe_allow_html=True,
    )


def status_tag(text: str, color: str = "blue") -> str:
    cls = "green" if color == "green" else "gray" if color == "gray" else ""
    return f'<span class="tag {cls}">{text}</span>'


def stepper(current: int) -> None:
    labels = ["Date & Opérateur", "Stérilisateur & Cycle", "Dispositifs", "Récapitulatif"]
    html = '<div class="stepper">'
    for idx, label in enumerate(labels, 1):
        active = "active" if idx <= current else ""
        html += f'<div class="step {active}"><span class="num">{"✓" if idx < current else idx}</span>{label}</div>'
        if idx < len(labels):
            html += '<div class="step-line"></div>'
    html += '</div>'
    st.markdown(html, unsafe_allow_html=True)


def cycle_doc_html(event: Dict[str, Any]) -> str:
    devices = event.get("devices_json", []) or []
    checks = event.get("checks_json", {}) or {}
    devices_rows = "".join(
        f"<tr><td>{d.get('name','')}</td><td>{d.get('category','')}</td><td>{d.get('qty','')}</td></tr>"
        for d in devices
    ) or '<tr><td colspan="3">Aucun dispositif renseigné</td></tr>'
    checks_rows = "".join(
        f"<tr><td>{label}</td><td>{'Conforme' if bool(value) else 'Non conforme'}</td></tr>"
        for label, value in checks.items()
    )
    return f"""
    <div class="doc-page">
        <div class="doc-header">
            <div>
                <div style="font-size:18px;font-weight:900;color:#13243a;">{APP_NAME}</div>
                <div style="font-size:11px;color:#64748b;">Cabinet dentaire · dossier de stérilisation</div>
            </div>
            <div style="font-size:11px;color:#64748b;text-align:right;">
                Édité le {datetime.now().strftime('%d/%m/%Y')}<br>
                Stockage : {storage.mode}
            </div>
        </div>
        <div class="doc-band">Dossier de stérilisation</div>
        <div class="doc-body">
            <div class="three-grid">
                <div class="flat-card"><div class="small-muted">Lot court</div><h2>{event.get('lot_short','')}</h2></div>
                <div class="flat-card"><div class="small-muted">Lot complet</div><h2 style="font-size:16px!important;">{event.get('lot_number','')}</h2></div>
                <div class="flat-card"><div class="small-muted">DLU</div><h2>{fr_date(event.get('dlu_date'))}</h2></div>
            </div>
            <table class="doc-table" style="margin-top:14px;">
                <tr><th>Date</th><td>{fr_date(event.get('event_date'))}</td><th>Opérateur</th><td>{event.get('operator_name','')}</td></tr>
                <tr><th>Stérilisateur</th><td>{event.get('sterilizer_name','')}</td><th>N° série</th><td>{event.get('sterilizer_serial','')}</td></tr>
                <tr><th>Cycle</th><td>{event.get('cycle_type','')}</td><th>N° cycle</th><td>{event.get('cycle_number','')}</td></tr>
                <tr><th>Conditionnement</th><td>{event.get('packaging_type','')}</td><th>Mode</th><td>{event.get('identification_mode','')}</td></tr>
            </table>
            <h3 style="margin-top:22px;">Dispositifs</h3>
            <table class="doc-table"><thead><tr><th>Nom</th><th>Catégorie</th><th>Quantité</th></tr></thead><tbody>{devices_rows}</tbody></table>
            <h3 style="margin-top:22px;">Contrôles de libération</h3>
            <table class="doc-table"><thead><tr><th>Contrôle</th><th>Résultat</th></tr></thead><tbody>{checks_rows}</tbody></table>
            <div style="margin-top:28px;border:1px solid #dfe8f3;border-radius:8px;padding:14px;min-height:80px;">
                <strong>Signature / visa :</strong>
            </div>
        </div>
    </div>
    """


def patient_doc_html(patient_ref: str, act_label: str, practitioner: str, lots: List[Dict[str, Any]], notes: str = "") -> str:
    rows = "".join(
        f"<tr><td>{lot.get('lot_short','')}</td><td>{lot.get('lot_number','')}</td><td>{lot.get('sterilizer_name','')}</td><td>{lot.get('cycle_type','')}</td><td>{fr_date(lot.get('dlu_date'))}</td></tr>"
        for lot in lots
    )
    return f"""
    <div class="doc-page">
        <div class="doc-header">
            <div>
                <div style="font-size:18px;font-weight:900;color:#13243a;">{APP_NAME}</div>
                <div style="font-size:11px;color:#64748b;">Cabinet dentaire · dossier de traçabilité</div>
            </div>
            <div style="font-size:11px;color:#64748b;text-align:right;">
                Édité le {datetime.now().strftime('%d/%m/%Y')}<br>
                Réf. patient : {patient_ref}
            </div>
        </div>
        <div class="doc-band">Dossier de traçabilité</div>
        <div class="doc-body">
            <table class="doc-table">
                <tr><th>Patient / dossier</th><td>{patient_ref}</td><th>Praticien</th><td>{practitioner}</td></tr>
                <tr><th>Acte</th><td colspan="3">{act_label}</td></tr>
            </table>
            <h3 style="margin-top:22px;">Lots utilisés</h3>
            <table class="doc-table">
                <thead><tr><th>Lot court</th><th>Lot complet</th><th>Stérilisateur</th><th>Cycle</th><th>DLU</th></tr></thead>
                <tbody>{rows}</tbody>
            </table>
            <h3 style="margin-top:22px;">Commentaire</h3>
            <div style="border:1px solid #dfe8f3;border-radius:8px;min-height:90px;padding:12px;color:#334155;">{notes}</div>
        </div>
    </div>
    """


# =========================================================
# PAGE DASHBOARD
# =========================================================
def page_dashboard() -> None:
    page_header("▦", "Dashboard", "Retrouvez vos cycles de stérilisation et les étiquettes associées.")
    events = storage.list_events(limit=500)
    month_prefix = datetime.now().strftime("%Y-%m")
    month_events = [e for e in events if str(e.get("event_date", e.get("created_at", ""))).startswith(month_prefix)]
    today_events = [e for e in events if str(e.get("event_date", e.get("created_at", ""))).startswith(today_iso())]

    cycles = [e for e in month_events if e.get("event_type") == "sterilization"]
    tests = [e for e in month_events if e.get("event_type") == "test"]
    device_count = sum(int(e.get("devices_count") or 0) for e in cycles)

    st.markdown(f'<div class="mini-title">Infos du mois · <span class="small-muted">{current_month_label()}</span></div>', unsafe_allow_html=True)
    st.markdown(
        f"""
        <div class="kpi-row">
            <div class="kpi-pill"><span class="metric-dot">↻</span><strong>{len(month_events)}</strong> Cycles</div>
            <div class="kpi-pill"><span class="metric-dot">🛡</span><strong>{len(cycles)}</strong> Stérilisations</div>
            <div class="kpi-pill"><span class="metric-dot green-dot">⊙</span><strong>{len(tests)}</strong> Tests</div>
            <div class="kpi-pill"><span class="metric-dot">◈</span><strong>{device_count}</strong> Dispositifs stérilisés</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    # Top cards
    op_counts: Dict[str, int] = {}
    dev_counts: Dict[str, int] = {}
    for e in cycles:
        op = e.get("operator_name") or "—"
        op_counts[op] = op_counts.get(op, 0) + 1
        for d in e.get("devices_json", []) or []:
            name = d.get("name", "—")
            dev_counts[name] = dev_counts.get(name, 0) + int(d.get("qty", 0))
    top_op = max(op_counts.items(), key=lambda x: x[1]) if op_counts else ("Aucun", 0)
    top_devices = sorted(dev_counts.items(), key=lambda x: x[1], reverse=True)[:3]
    if not top_devices:
        top_devices = [("Aucun dispositif", 0)]

    top_devices_html = "".join(f"<li><strong>{name}</strong> · {qty} étiquettes</li>" for name, qty in top_devices)
    st.markdown(
        f"""
        <div class="two-grid">
            <div class="flat-card">
                <div class="small-muted">👤 Opérateur le plus actif</div>
                <div style="font-weight:900;color:#13243a;margin-top:8px;">{top_op[0]} · {top_op[1]} cycles</div>
            </div>
            <div class="flat-card">
                <div class="small-muted">☆ Top dispositifs</div>
                <ol style="margin:8px 0 0 18px;padding:0;color:#13243a;font-size:13px;">{top_devices_html}</ol>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    st.markdown("### Cycles du jour")
    today_cycles = [e for e in today_events if e.get("event_type") == "sterilization"]
    today_tests = [e for e in today_events if e.get("event_type") == "test"]
    today_devices = sum(int(e.get("devices_count") or 0) for e in today_cycles)
    st.markdown(
        f"""
        <div class="kpi-row">
            <div class="kpi-pill"><span class="metric-dot">↻</span><strong>{len(today_events)}</strong> Cycles</div>
            <div class="kpi-pill"><span class="metric-dot">🛡</span><strong>{len(today_cycles)}</strong> Stérilisations</div>
            <div class="kpi-pill"><span class="metric-dot green-dot">⊙</span><strong>{len(today_tests)}</strong> Tests</div>
            <div class="kpi-pill"><span class="metric-dot">◈</span><strong>{today_devices}</strong> Dispositifs stérilisés</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    if not today_events:
        st.info("Aucun cycle enregistré aujourd’hui. Créez un test ou une stérilisation depuis Étiquettes.")
    else:
        by_auto: Dict[str, List[Dict[str, Any]]] = {}
        for e in today_events:
            by_auto.setdefault(e.get("sterilizer_name", "Autoclave"), []).append(e)
        for auto, rows in by_auto.items():
            tests_html = "".join(
                f'<div style="font-size:12px;margin-top:8px;">{fr_datetime(e.get("created_at"))[-5:]} &nbsp; {status_tag(e.get("cycle_type","Test"), "green")}</div>'
                for e in rows if e.get("event_type") == "test"
            )
            cycles_html = "".join(
                f'<div style="font-size:12px;margin-top:8px;">{fr_datetime(e.get("created_at"))[-5:]} &nbsp; {status_tag(e.get("cycle_type","Cycle"), "gray")} Lot {e.get("lot_short", "")}</div>'
                for e in rows if e.get("event_type") == "sterilization"
            )
            block_html = f'<div class="blue-card"><strong>♨️ {auto}</strong>'
            if tests_html:
                block_html += '<div class="small-muted" style="margin-top:12px;font-weight:800;">CYCLES DE TEST</div>' + tests_html
            if cycles_html:
                block_html += '<div class="small-muted" style="margin-top:12px;font-weight:800;">CYCLES DE STÉRILISATION</div>' + cycles_html
            block_html += '</div><br>'
            st.markdown(block_html, unsafe_allow_html=True)

    if storage.error:
        st.warning("Supabase n’est pas encore utilisable avec le nouveau schéma. L’application utilise SQLite local. Lancez le fichier SQL V3 dans Supabase puis redémarrez.")


# =========================================================
# PAGE ÉTIQUETTES / CYCLE
# =========================================================
def page_labels() -> None:
    page_header("🏷️", "Étiquettes", "Générez un cycle, son dossier, son QR unique et le marquage sachet simplifié.")
    stepper(st.session_state.label_step)

    # Navigation buttons for steps are inside each section
    step = st.session_state.label_step

    if step == 1:
        st.markdown("### Date & Opérateur")
        c1, c2 = st.columns(2)
        with c1:
            event_date = st.date_input("Date du cycle", value=date.today())
        with c2:
            operator_mode = st.radio("Identification", ["Code opérateur", "Sélection manuelle"], horizontal=True)
            if operator_mode == "Code opérateur":
                code = st.text_input("Code opérateur", type="password")
                operator = OPERATORS.get(code, "")
                if code and not operator:
                    st.error("Code inconnu.")
                if operator:
                    st.success(f"Opérateur : {operator}")
            else:
                operator = st.selectbox("Opérateur", list(OPERATORS.values()))
        b1, b2 = st.columns([1, 1])
        with b2:
            if st.button("Suivant ›"):
                if not operator:
                    st.error("Choisissez ou authentifiez un opérateur.")
                else:
                    st.session_state.draft.update({"event_date": event_date.isoformat(), "operator_name": operator})
                    st.session_state.label_step = 2
                    st.rerun()

    elif step == 2:
        st.markdown("### Stérilisateur & Cycle")
        c1, c2 = st.columns(2)
        with c1:
            sterilizer = st.selectbox("Stérilisateur", list(STERILIZERS.keys()))
            cycle_type = st.selectbox("Type de cycle", CYCLE_TYPES)
            cycle_number = st.text_input("Numéro de cycle machine", placeholder="Ex : 67")
        with c2:
            packaging = st.radio("Conditionnement", ["Sachet simple — DLU 3 mois", "Double emballage / cassette — DLU 6 mois"])
            identification_mode = st.radio(
                "Mode d’identification",
                ["Marquage manuel recommandé : LOT court + DLU", "Étiquettes QR individuelles"],
            )

        is_test = cycle_type in ["Helix", "Vacuum", "Bowie-Dick"]
        if is_test:
            st.info("Ce cycle sera enregistré comme test. Il n’y aura pas de DLU ni de dispositifs obligatoires.")
        else:
            st.markdown("#### Minimum à cocher avant lancement")
            ck1 = st.checkbox("Cycle Prion 134°C / 18 min sélectionné")
            ck2 = st.checkbox("Charge homogène et sachets correctement disposés")
            ck3 = st.checkbox("Stérilisateur identifié")

        b1, b2 = st.columns([1, 1])
        with b1:
            if st.button("‹ Retour"):
                st.session_state.label_step = 1
                st.rerun()
        with b2:
            if st.button("Suivant ›"):
                if not cycle_number:
                    st.error("Le numéro de cycle est obligatoire.")
                elif not is_test and not all([ck1, ck2, ck3]):
                    st.error("Cochez le minimum avant de continuer.")
                else:
                    st.session_state.draft.update({
                        "sterilizer_name": sterilizer,
                        "sterilizer_serial": STERILIZERS[sterilizer],
                        "cycle_type": cycle_type,
                        "cycle_number": cycle_number,
                        "packaging_type": packaging,
                        "identification_mode": identification_mode,
                        "event_type": "test" if is_test else "sterilization",
                    })
                    if is_test:
                        st.session_state.label_step = 4
                    else:
                        st.session_state.label_step = 3
                    st.rerun()

    elif step == 3:
        left, right = st.columns([2.1, 1])
        with left:
            st.markdown("### Dispositifs")
            st.markdown('<div class="small-muted">Sélectionnez les dispositifs présents dans la charge. Le mode recommandé reste : LOT court + DLU sur sachet.</div>', unsafe_allow_html=True)

            st.markdown("#### ❤️ Favoris")
            favs = [d for d in DEVICES if d.get("favorite")]
            fav_cols = st.columns(min(4, len(favs)))
            for idx, device in enumerate(favs):
                with fav_cols[idx % len(fav_cols)]:
                    st.markdown(
                        f"""
                        <div class="device-card">
                            <div class="device-name">{device['name']}</div>
                            <div class="device-sub">{device['category']} · {device['shelf_days']} jours</div>
                        </div>
                        """,
                        unsafe_allow_html=True,
                    )
                    if st.button("+ Ajouter", key=f"fav_{device['name']}"):
                        add_to_cart(device["name"])
                        st.rerun()

            st.markdown("#### Catalogue")
            query = st.text_input("Rechercher", placeholder="Rechercher…")
            category = st.selectbox("Catégorie", ["Toutes les catégories"] + sorted(set(d["category"] for d in DEVICES)))
            filtered = DEVICES
            if query:
                filtered = [d for d in filtered if query.lower() in d["name"].lower()]
            if category != "Toutes les catégories":
                filtered = [d for d in filtered if d["category"] == category]

            for device in filtered:
                cols = st.columns([2, 1, 1, 1])
                with cols[0]:
                    st.write(f"**{device['name']}**")
                with cols[1]:
                    st.caption(device["category"])
                with cols[2]:
                    st.caption(f"DLU {device['shelf_days']} j")
                with cols[3]:
                    if st.button("+", key=f"add_{device['name']}"):
                        add_to_cart(device["name"])
                        st.rerun()

        with right:
            st.markdown("### Sélection")
            items = cart_items()
            st.caption(f"{cart_total()} éléments dans la charge")
            if not items:
                st.info("Aucun dispositif sélectionné.")
            else:
                for item in items:
                    st.write(f"**{item['name']}**  ")
                    cminus, cnum, cplus, cdel = st.columns([1, 1.2, 1, 1])
                    with cminus:
                        if st.button("−", key=f"minus_{item['name']}"):
                            st.session_state.cart[item["name"]] = max(0, st.session_state.cart[item["name"]] - 1)
                            if st.session_state.cart[item["name"]] == 0:
                                remove_from_cart(item["name"])
                            st.rerun()
                    with cnum:
                        st.write(str(item["qty"]))
                    with cplus:
                        if st.button("+", key=f"plus_{item['name']}"):
                            add_to_cart(item["name"])
                            st.rerun()
                    with cdel:
                        if st.button("🗑", key=f"del_{item['name']}"):
                            remove_from_cart(item["name"])
                            st.rerun()
            st.markdown("<hr>", unsafe_allow_html=True)
            b1, b2 = st.columns(2)
            with b1:
                if st.button("‹ Retour", key="device_back"):
                    st.session_state.label_step = 2
                    st.rerun()
            with b2:
                if st.button("Suivant ›", key="device_next"):
                    if cart_total() <= 0:
                        st.error("Ajoutez au moins un dispositif.")
                    else:
                        st.session_state.label_step = 4
                        st.rerun()

    elif step == 4:
        draft = st.session_state.draft
        event_type = draft.get("event_type", "sterilization")

        if "lot_number" not in draft:
            seq = storage.next_sequence()
            draft["lot_short"] = f"{seq:04d}"
            draft["lot_number"] = f"STE-{datetime.now().strftime('%y%m%d')}-{seq:04d}"
            st.session_state.draft = draft

        if event_type == "sterilization":
            days = 180 if "Double" in draft.get("packaging_type", "") else 90
            dlu = datetime.strptime(draft["event_date"], "%Y-%m-%d") + timedelta(days=days)
            dlu_iso = dlu.date().isoformat()
        else:
            dlu_iso = None

        qr_payload = json.dumps(
            {
                "app": APP_NAME,
                "lot": draft.get("lot_number"),
                "lot_court": draft.get("lot_short"),
                "cycle": draft.get("cycle_number"),
                "type": draft.get("cycle_type"),
                "dlu": dlu_iso,
            },
            ensure_ascii=False,
        )
        qr_base64 = make_qr_base64(qr_payload)

        st.markdown("### Récapitulatif")
        left, right = st.columns([1.4, 1])

        with left:
            if event_type == "sterilization" and "Marquage manuel" in draft.get("identification_mode", ""):
                st.markdown(
                    f"""
                    <div class="manual-mark">
                        <div style="font-size:12px;color:#dbeafe;font-weight:700;">À MARQUER SUR CHAQUE SACHET</div>
                        <div class="big">LOT {draft.get('lot_short')}</div>
                        <div class="big">DLU {fr_date(dlu_iso)}</div>
                        <div class="subtext">Le QR complet reste archivé sur le dossier du cycle. Pas besoin de coller une étiquette sur chaque sachet.</div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )

            if event_type == "sterilization" and "Étiquettes QR" in draft.get("identification_mode", ""):
                st.markdown("#### Aperçu des étiquettes")
                labels_html = '<div class="label-grid">'
                for item in cart_items():
                    for n in range(item["qty"]):
                        labels_html += f"""
                        <div class="qr-label">
                            <div>
                                <div class="qr-label-title">{item['name']}</div>
                                <div class="qr-label-text">
                                    STE : {draft.get('lot_number')}<br>
                                    DLU : {fr_date(dlu_iso)}<br>
                                    Autoclave : {draft.get('sterilizer_name')}<br>
                                    Type : {draft.get('cycle_type')}<br>
                                    Cycle : {draft.get('cycle_number')}<br>
                                    Lot : {draft.get('lot_short')}<br>
                                    Op. : {draft.get('operator_name')}
                                </div>
                            </div>
                            <div style="text-align:right;">
                                <div class="qr-brand">○ {APP_NAME}</div>
                                <img src="data:image/png;base64,{qr_base64}" style="width:74px;height:74px;"/>
                            </div>
                        </div>
                        """
                labels_html += '</div>'
                st.markdown(labels_html, unsafe_allow_html=True)

            if event_type == "test":
                st.markdown(
                    f"""
                    <div class="manual-mark">
                        <div style="font-size:12px;color:#dbeafe;font-weight:700;">CYCLE DE TEST</div>
                        <div class="big">{draft.get('cycle_type')}</div>
                        <div class="subtext">Cycle test enregistré dans le registre du jour.</div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )

        with right:
            st.markdown("#### ✓ Récapitulatif")
            st.markdown(
                f"""
                <div class="flat-card">
                    <div class="small-muted">Date</div>{fr_date(draft.get('event_date'))}<br>
                    <div class="small-muted">Opérateur</div>{draft.get('operator_name')}<br>
                    <div class="small-muted">Stérilisateur</div>{draft.get('sterilizer_name')}<br>
                    <div class="small-muted">Cycle</div>{draft.get('cycle_type')} · n°{draft.get('cycle_number')}<br>
                    <div class="small-muted">Lot</div>{draft.get('lot_number')} / {draft.get('lot_short')}
                </div>
                """,
                unsafe_allow_html=True,
            )
            if event_type == "sterilization":
                st.write("**Dispositifs**")
                for item in cart_items():
                    st.write(f"{item['name']} × {item['qty']}")
            st.image(f"data:image/png;base64,{qr_base64}", width=140)

        st.markdown("#### Contrôles minimums")
        c1, c2 = st.columns(2)
        with c1:
            ticket_ok = st.checkbox("Ticket / rapport cycle conforme")
            temp_ok = st.checkbox("Température et durée conformes")
            dry_ok = st.checkbox("Sachets secs") if event_type == "sterilization" else True
        with c2:
            seal_ok = st.checkbox("Soudures intactes") if event_type == "sterilization" else True
            indicator_ok = st.checkbox("Indicateurs / test virés")
            released_ok = st.checkbox("Charge libérée / test validé")

        b1, b2, b3 = st.columns([1, 1, 1])
        with b1:
            if st.button("‹ Retour"):
                st.session_state.label_step = 3 if event_type == "sterilization" else 2
                st.rerun()
        with b2:
            render_print_button("Imprimer")
        with b3:
            if st.button("Enregistrer"):
                checks = {
                    "Ticket / rapport cycle conforme": bool(ticket_ok),
                    "Température et durée conformes": bool(temp_ok),
                    "Sachets secs": bool(dry_ok),
                    "Soudures intactes": bool(seal_ok),
                    "Indicateurs / test virés": bool(indicator_ok),
                    "Charge libérée / test validé": bool(released_ok),
                }
                if not all(checks.values()):
                    st.error("Toutes les cases de validation doivent être cochées.")
                else:
                    items = cart_items() if event_type == "sterilization" else []
                    payload = {
                        "event_type": event_type,
                        "event_label": draft.get("cycle_type"),
                        "event_date": draft.get("event_date"),
                        "operator_name": draft.get("operator_name"),
                        "sterilizer_name": draft.get("sterilizer_name"),
                        "sterilizer_serial": draft.get("sterilizer_serial"),
                        "cycle_number": draft.get("cycle_number"),
                        "cycle_type": draft.get("cycle_type"),
                        "lot_number": draft.get("lot_number"),
                        "lot_short": draft.get("lot_short"),
                        "dlu_date": dlu_iso,
                        "packaging_type": draft.get("packaging_type"),
                        "identification_mode": draft.get("identification_mode"),
                        "devices_json": items,
                        "devices_count": sum(i["qty"] for i in items),
                        "checks_json": checks,
                        "status": "conforme",
                        "created_at": now_iso(),
                    }
                    storage.insert_event(payload)
                    st.success("Enregistré avec succès.")
                    reset_label_workflow()
                    st.rerun()


# =========================================================
# PAGE DOSSIERS STÉRILISATION
# =========================================================
def page_sterilization_files() -> None:
    page_header("📄", "Dossiers stérilisation", "Consultez, imprimez et archivez vos dossiers de cycles.")
    events = storage.list_events(limit=500)
    if not events:
        st.info("Aucun dossier pour le moment.")
        return

    col1, col2 = st.columns([1.2, 2])
    with col1:
        st.markdown("### Recherche")
        search = st.text_input("Lot court ou complet", placeholder="Ex : 0001 ou STE-...")
        selected = None
        if search:
            selected = storage.find_event_by_lot(search)
            if not selected:
                st.error("Lot introuvable.")
        options = [f"{e.get('lot_short','')} · {e.get('cycle_type','')} · {fr_date(e.get('event_date'))}" for e in events]
        if not selected:
            idx = st.selectbox("Ou choisir un dossier", range(len(events)), format_func=lambda i: options[i])
            selected = events[idx]

        df = pd.DataFrame([
            {
                "Date": fr_date(e.get("event_date")),
                "Lot": e.get("lot_short"),
                "Type": e.get("cycle_type"),
                "Autoclave": e.get("sterilizer_name"),
                "Statut": e.get("status"),
            }
            for e in events
        ])
        st.download_button("⬇️ Export CSV", data=df.to_csv(index=False).encode("utf-8"), file_name="registre_sterilisation.csv", mime="text/csv")

    with col2:
        if selected:
            html = cycle_doc_html(selected)
            st.markdown(html, unsafe_allow_html=True)
            b1, b2 = st.columns(2)
            with b1:
                render_print_button("Imprimer le dossier")
            with b2:
                render_download_html(f"dossier_{selected.get('lot_short','lot')}.html", html, "Télécharger HTML")


# =========================================================
# PAGE DOSSIERS TRAÇABILITÉ PATIENT
# =========================================================
def page_patient_files() -> None:
    page_header("📋", "Dossiers traçabilité", "Reliez les lots ouverts à un dossier patient ou un identifiant interne.")

    c1, c2, c3 = st.columns(3)
    with c1:
        patient_ref = st.text_input("Référence patient / dossier", value="PAT-2026-001")
    with c2:
        act_label = st.text_input("Acte", value="Soin / chirurgie")
    with c3:
        practitioner = st.text_input("Praticien", value="Dr Sébastien")

    c4, c5 = st.columns([2, 1])
    with c4:
        lot_value = st.text_input("Scanner ou saisir le lot", placeholder="0001 ou STE-...")
    with c5:
        st.write("")
        if st.button("Ajouter le lot"):
            event = storage.find_event_by_lot(lot_value)
            if not event:
                st.error("Lot introuvable.")
            else:
                if not any(e.get("lot_number") == event.get("lot_number") for e in st.session_state.patient_cart):
                    st.session_state.patient_cart.append(event)
                st.rerun()

    notes = st.text_area("Commentaire", placeholder="Optionnel")

    if st.session_state.patient_cart:
        st.markdown("### Lots ajoutés")
        df = pd.DataFrame([
            {
                "Lot court": e.get("lot_short"),
                "Lot complet": e.get("lot_number"),
                "Cycle": e.get("cycle_type"),
                "Autoclave": e.get("sterilizer_name"),
                "DLU": fr_date(e.get("dlu_date")),
            }
            for e in st.session_state.patient_cart
        ])
        st.dataframe(df, use_container_width=True, hide_index=True)

        csave, cclear = st.columns(2)
        with csave:
            if st.button("Enregistrer la fiche"):
                for event in st.session_state.patient_cart:
                    storage.insert_patient_link({
                        "patient_ref": patient_ref,
                        "act_label": act_label,
                        "practitioner_name": practitioner,
                        "lot_number": event.get("lot_number"),
                        "lot_short": event.get("lot_short"),
                        "used_at": now_iso(),
                        "notes": notes,
                        "created_at": now_iso(),
                    })
                st.success("Fiche patient enregistrée.")
        with cclear:
            if st.button("Vider la sélection"):
                st.session_state.patient_cart = []
                st.rerun()
    else:
        st.info("Aucun lot ajouté.")


    if st.session_state.patient_cart:
        html = patient_doc_html(patient_ref, act_label, practitioner, st.session_state.patient_cart, notes)
        st.markdown(html, unsafe_allow_html=True)
        b1, b2 = st.columns(2)
        with b1:
            render_print_button("Imprimer la fiche")
        with b2:
            render_download_html(f"tracabilite_{patient_ref}.html", html, "Télécharger HTML")

    links = storage.list_patient_links(limit=50)
    if links:
        st.markdown("### Dernières fiches enregistrées")
        st.dataframe(pd.DataFrame(links), use_container_width=True, hide_index=True)


# =========================================================
# ROUTER
# =========================================================
if st.session_state.menu == "Dashboard":
    page_dashboard()
elif st.session_state.menu == "Étiquettes":
    page_labels()
elif st.session_state.menu == "Dossiers stérilisation":
    page_sterilization_files()
elif st.session_state.menu == "Dossiers traçabilité":
    page_patient_files()
