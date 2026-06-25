import os
import json
import base64
import sqlite3
from io import BytesIO
from datetime import datetime, timedelta, date

import pandas as pd
import qrcode
import streamlit as st
import streamlit.components.v1 as components

try:
    from supabase import create_client
except Exception:
    create_client = None


# =========================================================
# CONFIG
# =========================================================
APP_TITLE = "SteriTrace Cabinet"
OPERATORS = {
    "1234": "Dr Sébastien",
    "5678": "Assistante AD1",
}
AUTOCLAVES = {
    "Autoclave Euronda Classe B": "SN-EURONDA-2026",
    "Dac Universal (Rotatifs)": "SN-DAC-2026",
}
MEDICAL_DEVICES = [
    "[Instruments] Miroir + Sonde + Précelles",
    "[Instruments] Seringue d'anesthésie",
    "[Rotatifs] Contre-angle bague rouge",
    "[Rotatifs] Turbine",
    "[Chirurgie] Kit implantologie",
    "[Chirurgie] Daviers d'extraction",
    "[Cassettes] Cassette restauration",
    "[Endodontie] Set endo",
]


# =========================================================
# APP
# =========================================================
st.set_page_config(
    page_title=APP_TITLE,
    page_icon="🛡️",
    layout="wide",
    initial_sidebar_state="collapsed",
)


# =========================================================
# HELPERS
# =========================================================
def normalize_supabase_url(url: str) -> str:
    if not url:
        return url
    url = url.strip()
    if url.endswith("/"):
        url = url[:-1]
    if "/rest/v1" in url:
        url = url.split("/rest/v1")[0]
    return url


def today_str():
    return datetime.now().strftime("%Y-%m-%d")


def now_iso():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def current_week_key():
    today = date.today()
    year, week_num, _ = today.isocalendar()
    return f"{year}-W{week_num:02d}"


def bool_to_int(value):
    return 1 if value else 0


def int_to_bool(value):
    return bool(value) if value is not None else False


def make_qr_base64(text: str) -> str:
    qr = qrcode.QRCode(version=1, box_size=4, border=1)
    qr.add_data(text)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buffer = BytesIO()
    img.save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode()


def render_print_button(label="Imprimer"):
    components.html(
        f"""
        <button onclick="window.print()" style="
            width:100%;
            border:none;
            padding:14px 18px;
            border-radius:12px;
            background:#0f172a;
            color:white;
            font-weight:600;
            font-size:14px;
            cursor:pointer;
            box-shadow:0 10px 24px rgba(15,23,42,0.12);
        ">
            🖨️ {label}
        </button>
        """,
        height=60,
    )


def reset_cycle_state():
    st.session_state.step = 1
    st.session_state.operator_name = None
    st.session_state.cycle_data = {}
    st.session_state.selected_devices = []
    st.session_state.calculated_dlu = None
    st.session_state.generated_lot_full = None
    st.session_state.generated_lot_short = None


# =========================================================
# DATABASE LAYER
# =========================================================
class Storage:
    def __init__(self):
        self.mode = "sqlite"
        self.error = None
        self.supabase = None

        supabase_url = None
        supabase_key = None

        try:
            supabase_url = normalize_supabase_url(st.secrets.get("SUPABASE_URL", ""))
            supabase_key = st.secrets.get("SUPABASE_KEY", "")
        except Exception:
            pass

        if create_client and supabase_url and supabase_key:
            try:
                self.supabase = create_client(supabase_url, supabase_key)
                # test léger
                self.supabase.table("sterilization_cycles").select("id").limit(1).execute()
                self.mode = "supabase"
            except Exception as e:
                self.error = str(e)
                self.mode = "sqlite"

        if self.mode == "sqlite":
            self.conn = sqlite3.connect("steritrace_local.db", check_same_thread=False)
            self.conn.row_factory = sqlite3.Row
            self.init_sqlite()

    def init_sqlite(self):
        cur = self.conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS sterilization_cycles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                lot_number TEXT UNIQUE,
                lot_short TEXT,
                operator_name TEXT,
                autoclave_name TEXT,
                serial_number TEXT,
                cycle_number TEXT,
                cycle_type TEXT,
                devices TEXT,
                packaging_type TEXT,
                dlu_date TEXT,
                precheck_helix_ok INTEGER,
                precheck_vacuum_ok INTEGER,
                ticket_ok INTEGER,
                temp_ok INTEGER,
                duration_ok INTEGER,
                pressure_ok INTEGER,
                dry_ok INTEGER,
                seal_ok INTEGER,
                indicator_ok INTEGER,
                prion_ok INTEGER,
                released_ok INTEGER,
                created_at TEXT
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS daily_controls (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                control_date TEXT UNIQUE,
                helix_ok INTEGER,
                operator_name TEXT,
                notes TEXT,
                created_at TEXT
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS weekly_controls (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                week_key TEXT UNIQUE,
                vacuum_ok INTEGER,
                operator_name TEXT,
                notes TEXT,
                created_at TEXT
            )
            """
        )
        self.conn.commit()

    # ---------- Controls ----------
    def save_daily_control(self, control_date, helix_ok, operator_name, notes=""):
        payload = {
            "control_date": control_date,
            "helix_ok": bool(helix_ok),
            "operator_name": operator_name,
            "notes": notes,
            "created_at": now_iso(),
        }
        if self.mode == "supabase":
            existing = self.supabase.table("daily_controls").select("*").eq("control_date", control_date).execute()
            if existing.data:
                row_id = existing.data[0]["id"]
                self.supabase.table("daily_controls").update(payload).eq("id", row_id).execute()
            else:
                self.supabase.table("daily_controls").insert(payload).execute()
        else:
            cur = self.conn.cursor()
            cur.execute(
                """
                INSERT INTO daily_controls(control_date, helix_ok, operator_name, notes, created_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(control_date) DO UPDATE SET
                    helix_ok=excluded.helix_ok,
                    operator_name=excluded.operator_name,
                    notes=excluded.notes,
                    created_at=excluded.created_at
                """,
                (control_date, bool_to_int(helix_ok), operator_name, notes, now_iso()),
            )
            self.conn.commit()

    def get_daily_control(self, control_date):
        if self.mode == "supabase":
            res = self.supabase.table("daily_controls").select("*").eq("control_date", control_date).limit(1).execute()
            return res.data[0] if res.data else None
        cur = self.conn.cursor()
        row = cur.execute("SELECT * FROM daily_controls WHERE control_date=?", (control_date,)).fetchone()
        return dict(row) if row else None

    def save_weekly_control(self, week_key, vacuum_ok, operator_name, notes=""):
        payload = {
            "week_key": week_key,
            "vacuum_ok": bool(vacuum_ok),
            "operator_name": operator_name,
            "notes": notes,
            "created_at": now_iso(),
        }
        if self.mode == "supabase":
            existing = self.supabase.table("weekly_controls").select("*").eq("week_key", week_key).execute()
            if existing.data:
                row_id = existing.data[0]["id"]
                self.supabase.table("weekly_controls").update(payload).eq("id", row_id).execute()
            else:
                self.supabase.table("weekly_controls").insert(payload).execute()
        else:
            cur = self.conn.cursor()
            cur.execute(
                """
                INSERT INTO weekly_controls(week_key, vacuum_ok, operator_name, notes, created_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(week_key) DO UPDATE SET
                    vacuum_ok=excluded.vacuum_ok,
                    operator_name=excluded.operator_name,
                    notes=excluded.notes,
                    created_at=excluded.created_at
                """,
                (week_key, bool_to_int(vacuum_ok), operator_name, notes, now_iso()),
            )
            self.conn.commit()

    def get_weekly_control(self, week_key):
        if self.mode == "supabase":
            res = self.supabase.table("weekly_controls").select("*").eq("week_key", week_key).limit(1).execute()
            return res.data[0] if res.data else None
        cur = self.conn.cursor()
        row = cur.execute("SELECT * FROM weekly_controls WHERE week_key=?", (week_key,)).fetchone()
        return dict(row) if row else None

    # ---------- Cycles ----------
    def list_cycles(self, limit=100):
        if self.mode == "supabase":
            query = self.supabase.table("sterilization_cycles").select("*").order("created_at", desc=True)
            if limit:
                query = query.limit(limit)
            res = query.execute()
            return res.data if res.data else []
        cur = self.conn.cursor()
        if limit:
            rows = cur.execute(
                "SELECT * FROM sterilization_cycles ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        else:
            rows = cur.execute(
                "SELECT * FROM sterilization_cycles ORDER BY created_at DESC"
            ).fetchall()
        return [dict(r) for r in rows]

    def count_cycles(self):
        if self.mode == "supabase":
            res = self.supabase.table("sterilization_cycles").select("id").execute()
            return len(res.data) if res.data else 0
        cur = self.conn.cursor()
        row = cur.execute("SELECT COUNT(*) as n FROM sterilization_cycles").fetchone()
        return row["n"] if row else 0

    def next_lot_sequence(self):
        return self.count_cycles() + 1

    def insert_cycle(self, payload: dict):
        if self.mode == "supabase":
            self.supabase.table("sterilization_cycles").insert(payload).execute()
        else:
            cur = self.conn.cursor()
            cur.execute(
                """
                INSERT INTO sterilization_cycles(
                    lot_number, lot_short, operator_name, autoclave_name, serial_number,
                    cycle_number, cycle_type, devices, packaging_type, dlu_date,
                    precheck_helix_ok, precheck_vacuum_ok, ticket_ok, temp_ok,
                    duration_ok, pressure_ok, dry_ok, seal_ok, indicator_ok,
                    prion_ok, released_ok, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    payload["lot_number"],
                    payload["lot_short"],
                    payload["operator_name"],
                    payload["autoclave_name"],
                    payload["serial_number"],
                    payload["cycle_number"],
                    payload["cycle_type"],
                    payload["devices"],
                    payload["packaging_type"],
                    payload["dlu_date"],
                    bool_to_int(payload["precheck_helix_ok"]),
                    bool_to_int(payload["precheck_vacuum_ok"]),
                    bool_to_int(payload["ticket_ok"]),
                    bool_to_int(payload["temp_ok"]),
                    bool_to_int(payload["duration_ok"]),
                    bool_to_int(payload["pressure_ok"]),
                    bool_to_int(payload["dry_ok"]),
                    bool_to_int(payload["seal_ok"]),
                    bool_to_int(payload["indicator_ok"]),
                    bool_to_int(payload["prion_ok"]),
                    bool_to_int(payload["released_ok"]),
                    payload["created_at"],
                ),
            )
            self.conn.commit()

    def find_cycle(self, value: str):
        value = value.strip()
        if not value:
            return None

        if self.mode == "supabase":
            res1 = self.supabase.table("sterilization_cycles").select("*").eq("lot_number", value).limit(1).execute()
            if res1.data:
                return res1.data[0]
            res2 = self.supabase.table("sterilization_cycles").select("*").eq("lot_short", value).limit(1).execute()
            if res2.data:
                return res2.data[0]
            return None

        cur = self.conn.cursor()
        row = cur.execute(
            """
            SELECT * FROM sterilization_cycles
            WHERE lot_number=? OR lot_short=?
            LIMIT 1
            """,
            (value, value),
        ).fetchone()
        return dict(row) if row else None


storage = Storage()


# =========================================================
# SESSION STATE
# =========================================================
if "menu" not in st.session_state:
    st.session_state.menu = "Dashboard"
if "step" not in st.session_state:
    st.session_state.step = 1
if "operator_name" not in st.session_state:
    st.session_state.operator_name = None
if "cycle_data" not in st.session_state:
    st.session_state.cycle_data = {}
if "selected_devices" not in st.session_state:
    st.session_state.selected_devices = []
if "calculated_dlu" not in st.session_state:
    st.session_state.calculated_dlu = None
if "generated_lot_full" not in st.session_state:
    st.session_state.generated_lot_full = None
if "generated_lot_short" not in st.session_state:
    st.session_state.generated_lot_short = None
if "patient_basket" not in st.session_state:
    st.session_state.patient_basket = []


# =========================================================
# DESIGN
# =========================================================
st.markdown(
    """
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800&display=swap');

    html, body, [class*="css"] {
        font-family: 'Inter', sans-serif;
    }

    .stApp {
        background:
            radial-gradient(circle at top left, rgba(59,130,246,0.05), transparent 30%),
            linear-gradient(180deg, #f8fbff 0%, #f4f7fb 100%);
    }

    [data-testid="stHeader"], #MainMenu, footer {
        display: none !important;
    }

    .block-container {
        max-width: 1180px !important;
        padding-top: 24px !important;
        padding-bottom: 60px !important;
    }

    .topbar {
        background: rgba(255,255,255,0.88);
        backdrop-filter: blur(10px);
        border: 1px solid rgba(226,232,240,0.9);
        border-radius: 22px;
        padding: 18px 22px;
        display:flex;
        align-items:center;
        justify-content:space-between;
        margin-bottom: 20px;
        box-shadow: 0 8px 40px rgba(15,23,42,0.05);
    }

    .brand-title {
        font-size: 20px;
        font-weight: 800;
        color: #0f172a;
        letter-spacing: -0.03em;
    }

    .brand-sub {
        font-size: 12px;
        color: #64748b;
        margin-top: 3px;
    }

    .status-pill {
        display:inline-flex;
        align-items:center;
        gap:8px;
        border-radius:999px;
        padding:8px 12px;
        font-size:12px;
        font-weight:700;
        background:#eff6ff;
        color:#1d4ed8;
        border:1px solid #dbeafe;
    }

    .card {
        background: rgba(255,255,255,0.94);
        border: 1px solid #e5edf5;
        border-radius: 22px;
        padding: 24px;
        box-shadow: 0 12px 40px rgba(15,23,42,0.05);
        margin-bottom: 18px;
    }

    .section-title {
        font-size: 24px;
        font-weight: 800;
        color: #0f172a;
        letter-spacing: -0.03em;
        margin-bottom: 6px;
    }

    .section-sub {
        color: #64748b;
        font-size: 14px;
        margin-bottom: 20px;
    }

    .mini-title {
        font-size: 15px;
        font-weight: 700;
        color: #0f172a;
        margin-bottom: 6px;
    }

    .kpi {
        background: linear-gradient(180deg, #ffffff 0%, #f8fbff 100%);
        border: 1px solid #e5edf5;
        border-radius: 20px;
        padding: 20px;
        box-shadow: 0 12px 28px rgba(15,23,42,0.04);
    }

    .kpi-label {
        color:#64748b;
        font-size:12px;
        text-transform:uppercase;
        letter-spacing:0.06em;
        font-weight:700;
    }

    .kpi-value {
        color:#0f172a;
        font-size:32px;
        font-weight:800;
        letter-spacing:-0.03em;
        margin-top:8px;
    }

    .soft-box {
        background:#f8fbff;
        border:1px solid #e6eef8;
        border-radius:16px;
        padding:16px;
    }

    .success-chip {
        display:inline-block;
        border-radius:999px;
        padding:6px 10px;
        background:#ecfdf3;
        color:#047857;
        border:1px solid #d1fae5;
        font-size:12px;
        font-weight:700;
    }

    .warn-chip {
        display:inline-block;
        border-radius:999px;
        padding:6px 10px;
        background:#fff7ed;
        color:#c2410c;
        border:1px solid #fed7aa;
        font-size:12px;
        font-weight:700;
    }

    .mark-box {
        background: #0f172a;
        color: white;
        border-radius: 18px;
        padding: 18px;
        line-height: 1.8;
    }

    .mark-box strong {
        color: #93c5fd;
    }

    .report-box {
        background: white;
        border: 1px solid #e2e8f0;
        border-radius: 18px;
        padding: 24px;
    }

    .stButton > button {
        width: 100%;
        border: none !important;
        border-radius: 14px !important;
        padding: 12px 18px !important;
        font-weight: 700 !important;
        background: linear-gradient(180deg, #1d4ed8 0%, #2563eb 100%) !important;
        color: white !important;
        box-shadow: 0 12px 24px rgba(37,99,235,0.18) !important;
    }

    .stDownloadButton > button {
        width: 100%;
        border-radius: 14px !important;
        font-weight: 700 !important;
    }

    div[data-testid="stMetric"] {
        background: white;
        border:1px solid #e5edf5;
        padding:18px;
        border-radius:18px;
    }

    @media print {
        [data-testid="stHeader"], #MainMenu, footer,
        .no-print, .topbar, .stButton, .stDownloadButton {
            display:none !important;
        }
        .block-container {
            max-width: 100% !important;
            padding: 0 !important;
            margin: 0 !important;
        }
        .stApp {
            background: white !important;
        }
        .card, .report-box {
            box-shadow:none !important;
            border:none !important;
        }
    }
    </style>
    """,
    unsafe_allow_html=True,
)


# =========================================================
# HEADER
# =========================================================
status_text = "Supabase" if storage.mode == "supabase" else "SQLite local démo"

st.markdown(
    f"""
    <div class="topbar">
        <div>
            <div class="brand-title">🛡️ SteriTrace Cabinet</div>
            <div class="brand-sub">Traçabilité simplifiée des cycles de stérilisation</div>
        </div>
        <div class="status-pill">● Stockage : {status_text}</div>
    </div>
    """,
    unsafe_allow_html=True,
)


# =========================================================
# NAV
# =========================================================
nav1, nav2, nav3, nav4 = st.columns(4)
with nav1:
    if st.button("📊 Dashboard"):
        st.session_state.menu = "Dashboard"
with nav2:
    if st.button("🧪 Contrôles"):
        st.session_state.menu = "Controles"
with nav3:
    if st.button("➕ Nouveau cycle"):
        st.session_state.menu = "Cycle"
with nav4:
    if st.button("👤 Traçabilité patient"):
        st.session_state.menu = "Patient"


# =========================================================
# PAGE: DASHBOARD
# =========================================================
if st.session_state.menu == "Dashboard":
    cycles = storage.list_cycles(limit=100)
    total_cycles = len(cycles)
    today_cycles = len([c for c in cycles if str(c.get("created_at", "")).startswith(datetime.now().strftime("%Y-%m-%d"))])

    daily_control = storage.get_daily_control(today_str())
    weekly_control = storage.get_weekly_control(current_week_key())

    c1, c2, c3, c4 = st.columns(4)
    with c1:
        st.markdown(
            f"""
            <div class="kpi">
                <div class="kpi-label">Cycles enregistrés</div>
                <div class="kpi-value">{total_cycles}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    with c2:
        st.markdown(
            f"""
            <div class="kpi">
                <div class="kpi-label">Cycles du jour</div>
                <div class="kpi-value">{today_cycles}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    with c3:
        helix_state = "OK" if (daily_control and daily_control.get("helix_ok")) else "À faire"
        st.markdown(
            f"""
            <div class="kpi">
                <div class="kpi-label">Test Helix du jour</div>
                <div class="kpi-value" style="font-size:26px;">{helix_state}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    with c4:
        vacuum_state = "OK" if (weekly_control and weekly_control.get("vacuum_ok")) else "À faire"
        st.markdown(
            f"""
            <div class="kpi">
                <div class="kpi-label">Test de vide semaine</div>
                <div class="kpi-value" style="font-size:26px;">{vacuum_state}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    st.markdown('<div class="card">', unsafe_allow_html=True)
    st.markdown('<div class="section-title">Journal des cycles</div>', unsafe_allow_html=True)
    st.markdown('<div class="section-sub">Historique récent des lots libérés et archivés.</div>', unsafe_allow_html=True)

    if cycles:
        df = pd.DataFrame(cycles)
        display_cols = [
            "lot_short",
            "lot_number",
            "operator_name",
            "autoclave_name",
            "cycle_number",
            "cycle_type",
            "dlu_date",
            "created_at",
        ]
        df = df[[c for c in display_cols if c in df.columns]]
        rename_map = {
            "lot_short": "Lot court",
            "lot_number": "Lot complet",
            "operator_name": "Opérateur",
            "autoclave_name": "Autoclave",
            "cycle_number": "N° cycle",
            "cycle_type": "Programme",
            "dlu_date": "DLU",
            "created_at": "Créé le",
        }
        df = df.rename(columns=rename_map)
        st.dataframe(df, use_container_width=True, hide_index=True)
    else:
        st.info("Aucun cycle enregistré pour le moment.")
    st.markdown("</div>", unsafe_allow_html=True)


# =========================================================
# PAGE: CONTROLES
# =========================================================
elif st.session_state.menu == "Controles":
    st.markdown('<div class="card">', unsafe_allow_html=True)
    st.markdown('<div class="section-title">Contrôles minimums</div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="section-sub">On enregistre ici le strict minimum pratique : Helix du jour + test de vide hebdomadaire.</div>',
        unsafe_allow_html=True,
    )

    today_control = storage.get_daily_control(today_str())
    week_control = storage.get_weekly_control(current_week_key())

    col_a, col_b = st.columns(2)

    with col_a:
        st.markdown('<div class="soft-box">', unsafe_allow_html=True)
        st.markdown("### 🧪 Test Helix du jour")
        with st.form("daily_helix_form"):
            helix_ok = st.checkbox(
                "Test Helix conforme",
                value=bool(today_control.get("helix_ok")) if today_control else False,
            )
            helix_operator = st.text_input(
                "Réalisé par",
                value=today_control.get("operator_name", "") if today_control else "",
            )
            helix_notes = st.text_area(
                "Notes",
                value=today_control.get("notes", "") if today_control else "",
                placeholder="Optionnel",
            )
            submit_helix = st.form_submit_button("Enregistrer le test Helix")
            if submit_helix:
                storage.save_daily_control(today_str(), helix_ok, helix_operator, helix_notes)
                st.success("Test Helix enregistré.")
                st.rerun()
        st.markdown("</div>", unsafe_allow_html=True)

    with col_b:
        st.markdown('<div class="soft-box">', unsafe_allow_html=True)
        st.markdown("### 🧪 Test de vide hebdomadaire")
        with st.form("weekly_vacuum_form"):
            vacuum_ok = st.checkbox(
                "Test de vide conforme",
                value=bool(week_control.get("vacuum_ok")) if week_control else False,
            )
            vacuum_operator = st.text_input(
                "Réalisé par",
                value=week_control.get("operator_name", "") if week_control else "",
            )
            vacuum_notes = st.text_area(
                "Notes",
                value=week_control.get("notes", "") if week_control else "",
                placeholder="Optionnel",
            )
            submit_vacuum = st.form_submit_button("Enregistrer le test de vide")
            if submit_vacuum:
                storage.save_weekly_control(current_week_key(), vacuum_ok, vacuum_operator, vacuum_notes)
                st.success("Test de vide enregistré.")
                st.rerun()
        st.markdown("</div>", unsafe_allow_html=True)

    st.markdown("</div>", unsafe_allow_html=True)


# =========================================================
# PAGE: CYCLE
# =========================================================
elif st.session_state.menu == "Cycle":
    daily_control = storage.get_daily_control(today_str())
    weekly_control = storage.get_weekly_control(current_week_key())

    step = st.session_state.step

    st.markdown('<div class="card">', unsafe_allow_html=True)
    st.markdown(f'<div class="section-title">Nouveau cycle — étape {step}/4</div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="section-sub">Workflow simplifié : on coche le minimum utile, puis l’application édite tout automatiquement.</div>',
        unsafe_allow_html=True,
    )

    progress_map = {1: 0.25, 2: 0.5, 3: 0.75, 4: 1.0}
    st.progress(progress_map.get(step, 0.25))

    # Step 1
    if step == 1:
        st.markdown("### 1. Identification")
        code = st.text_input("Code opérateur", type="password")
        if st.button("Authentifier et continuer"):
            if code in OPERATORS:
                st.session_state.operator_name = OPERATORS[code]
                st.session_state.step = 2
                st.rerun()
            else:
                st.error("Code opérateur invalide.")

    # Step 2
    elif step == 2:
        st.markdown("### 2. Contrôles préalables + paramètres")

        c1, c2 = st.columns(2)
        with c1:
            if daily_control and daily_control.get("helix_ok"):
                st.markdown('<span class="success-chip">Helix du jour enregistré</span>', unsafe_allow_html=True)
            else:
                st.markdown('<span class="warn-chip">Helix du jour non enregistré</span>', unsafe_allow_html=True)

        with c2:
            if weekly_control and weekly_control.get("vacuum_ok"):
                st.markdown('<span class="success-chip">Test de vide hebdo enregistré</span>', unsafe_allow_html=True)
            else:
                st.markdown('<span class="warn-chip">Test de vide hebdo non enregistré</span>', unsafe_allow_html=True)

        st.markdown("---")
        pre_helix = st.checkbox("☑ Je confirme que le test Helix du jour est conforme")
        pre_vacuum = st.checkbox("☑ Je confirme que le test de vide hebdomadaire est conforme")
        check_load = st.checkbox("☑ Charge homogène et correctement disposée")
        check_pack = st.checkbox("☑ Sachets correctement préparés avant cycle")

        col1, col2 = st.columns(2)
        with col1:
            selected_machine = st.selectbox("Autoclave", list(AUTOCLAVES.keys()))
            cycle_number = st.text_input("Numéro de cycle machine", placeholder="Ex : 1452")
        with col2:
            cycle_type = st.selectbox(
                "Programme",
                [
                    "Prion 134°C - 18 min",
                    "Test Helix",
                    "Test Bowie-Dick",
                ],
                index=0,
            )

        colb1, colb2 = st.columns(2)
        with colb1:
            if st.button("Retour"):
                st.session_state.step = 1
                st.rerun()
        with colb2:
            if st.button("Continuer vers la composition"):
                if not cycle_number:
                    st.error("Le numéro de cycle machine est obligatoire.")
                elif not (pre_helix and pre_vacuum and check_load and check_pack):
                    st.error("Merci de cocher tous les contrôles minimums avant de continuer.")
                else:
                    st.session_state.cycle_data = {
                        "autoclave_name": selected_machine,
                        "serial_number": AUTOCLAVES[selected_machine],
                        "cycle_number": cycle_number,
                        "cycle_type": cycle_type,
                        "precheck_helix_ok": True,
                        "precheck_vacuum_ok": True,
                    }
                    st.session_state.step = 3
                    st.rerun()

    # Step 3
    elif step == 3:
        st.markdown("### 3. Composition et conditionnement")

        devices = st.multiselect(
            "Dispositifs stérilisés",
            MEDICAL_DEVICES,
            default=st.session_state.selected_devices,
        )

        packaging_type = st.radio(
            "Type d’emballage",
            [
                "Sachet simple — DLU 3 mois",
                "Double ensachage / cassette — DLU 6 mois",
            ],
            index=0,
        )

        days = 90 if "3 mois" in packaging_type else 180
        dlu_date = datetime.now() + timedelta(days=days)

        st.markdown(
            f"""
            <div class="soft-box">
                <div class="mini-title">DLU calculée automatiquement</div>
                <div>La DLU proposée est : <strong>{dlu_date.strftime("%d/%m/%Y")}</strong></div>
            </div>
            """,
            unsafe_allow_html=True,
        )

        colb1, colb2 = st.columns(2)
        with colb1:
            if st.button("Retour aux paramètres"):
                st.session_state.step = 2
                st.rerun()
        with colb2:
            if st.button("Continuer vers la validation"):
                st.session_state.selected_devices = devices if devices else ["Instrumentation générale"]
                st.session_state.calculated_dlu = dlu_date
                st.session_state.cycle_data["packaging_type"] = packaging_type
                st.session_state.step = 4
                st.rerun()

    # Step 4
    elif step == 4:
        st.markdown("### 4. Validation post-cycle")

        ticket_ok = st.checkbox("☑ Ticket autoclave conforme")
        temp_ok = st.checkbox("☑ Température atteinte (134°C min)")
        duration_ok = st.checkbox("☑ Durée plateau conforme (18 min min)")
        pressure_ok = st.checkbox("☑ Pression conforme")
        dry_ok = st.checkbox("☑ Sachets secs")
        seal_ok = st.checkbox("☑ Soudures intactes")
        indicator_ok = st.checkbox("☑ Indicateurs de passage virés")
        prion_ok = st.checkbox("☑ Test prion / classe 6 conforme")
        released_ok = st.checkbox("☑ Charge libérée")

        if not st.session_state.generated_lot_full or not st.session_state.generated_lot_short:
            seq = storage.next_lot_sequence()
            st.session_state.generated_lot_full = f"LOT-{datetime.now().strftime('%Y%m%d')}-{seq:04d}"
            st.session_state.generated_lot_short = f"{seq:04d}"

        lot_full = st.session_state.generated_lot_full
        lot_short = st.session_state.generated_lot_short
        dlu_value = st.session_state.calculated_dlu.strftime("%d/%m/%Y")

        qr_payload = json.dumps(
            {
                "lot_number": lot_full,
                "lot_short": lot_short,
                "dlu_date": st.session_state.calculated_dlu.strftime("%Y-%m-%d"),
                "cycle_number": st.session_state.cycle_data["cycle_number"],
                "autoclave_name": st.session_state.cycle_data["autoclave_name"],
            },
            ensure_ascii=False,
        )
        qr_base64 = make_qr_base64(qr_payload)

        st.markdown("<br>", unsafe_allow_html=True)
        c_left, c_right = st.columns([1.1, 0.9])

        with c_left:
            st.markdown(
                f"""
                <div class="mark-box">
                    <div style="font-size:13px; opacity:0.85;">OPTION 1 — marquage manuel simplifié</div>
                    <div style="font-size:24px; font-weight:800; margin-top:8px;">À marquer sur chaque sachet</div>
                    <div style="margin-top:12px; font-size:18px;">
                        <strong>LOT {lot_short}</strong><br>
                        <strong>DLU {dlu_value}</strong>
                    </div>
                    <div style="margin-top:12px; font-size:13px; opacity:0.9;">
                        Pas d’étiquette QR sur chaque sachet.<br>
                        Le QR code complet est conservé sur le dossier du cycle.
                    </div>
                </div>
                """,
                unsafe_allow_html=True,
            )

        with c_right:
            st.markdown(
                f"""
                <div class="report-box">
                    <div style="display:flex; justify-content:space-between; align-items:flex-start; gap:12px;">
                        <div>
                            <div style="font-size:20px; font-weight:800; color:#0f172a;">Dossier du cycle</div>
                            <div style="font-size:13px; color:#64748b;">Traçabilité et archivage</div>
                            <div style="margin-top:12px; line-height:1.8; font-size:14px; color:#0f172a;">
                                <strong>Lot complet :</strong> {lot_full}<br>
                                <strong>Lot court :</strong> {lot_short}<br>
                                <strong>DLU :</strong> {dlu_value}<br>
                                <strong>Cycle machine :</strong> {st.session_state.cycle_data["cycle_number"]}<br>
                                <strong>Autoclave :</strong> {st.session_state.cycle_data["autoclave_name"]}<br>
                                <strong>Libération :</strong> {st.session_state.operator_name}
                            </div>
                        </div>
                        <div>
                            <img src="data:image/png;base64,{qr_base64}" style="width:120px; height:120px;" />
                        </div>
                    </div>
                </div>
                """,
                unsafe_allow_html=True,
            )

        st.markdown("<br>", unsafe_allow_html=True)
        render_print_button("Imprimer le dossier du cycle")

        st.markdown("<br>", unsafe_allow_html=True)
        c1, c2, c3 = st.columns(3)
        with c1:
            if st.button("Retour à la composition"):
                st.session_state.step = 3
                st.rerun()
        with c2:
            ready = all([ticket_ok, temp_ok, duration_ok, pressure_ok, dry_ok, seal_ok, indicator_ok, prion_ok, released_ok])
            if st.button("Enregistrer le lot"):
                if not ready:
                    st.error("Toutes les cases de validation doivent être cochées pour enregistrer le lot.")
                else:
                    payload = {
                        "lot_number": lot_full,
                        "lot_short": lot_short,
                        "operator_name": st.session_state.operator_name,
                        "autoclave_name": st.session_state.cycle_data["autoclave_name"],
                        "serial_number": st.session_state.cycle_data["serial_number"],
                        "cycle_number": st.session_state.cycle_data["cycle_number"],
                        "cycle_type": st.session_state.cycle_data["cycle_type"],
                        "devices": ", ".join(st.session_state.selected_devices),
                        "packaging_type": st.session_state.cycle_data["packaging_type"],
                        "dlu_date": st.session_state.calculated_dlu.strftime("%Y-%m-%d"),
                        "precheck_helix_ok": True,
                        "precheck_vacuum_ok": True,
                        "ticket_ok": ticket_ok,
                        "temp_ok": temp_ok,
                        "duration_ok": duration_ok,
                        "pressure_ok": pressure_ok,
                        "dry_ok": dry_ok,
                        "seal_ok": seal_ok,
                        "indicator_ok": indicator_ok,
                        "prion_ok": prion_ok,
                        "released_ok": released_ok,
                        "created_at": now_iso(),
                    }
                    storage.insert_cycle(payload)
                    st.success(f"Lot enregistré : {lot_full} (marquage sachet : LOT {lot_short})")
                    reset_cycle_state()
                    st.rerun()
        with c3:
            if st.button("Annuler le cycle"):
                reset_cycle_state()
                st.rerun()

    st.markdown("</div>", unsafe_allow_html=True)


# =========================================================
# PAGE: PATIENT
# =========================================================
elif st.session_state.menu == "Patient":
    st.markdown('<div class="card">', unsafe_allow_html=True)
    st.markdown('<div class="section-title">Traçabilité patient</div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="section-sub">On peut saisir ou scanner un lot. Le scan n’est pas obligatoire : la saisie du lot court suffit.</div>',
        unsafe_allow_html=True,
    )

    col1, col2 = st.columns(2)
    with col1:
        patient_ref = st.text_input("Référence patient / dossier", value="PAT-2026-001")
    with col2:
        patient_act = st.text_input("Acte", value="Soin / chirurgie / endodontie")

    st.markdown("---")
    entry1, entry2 = st.columns([2, 1])
    with entry1:
        lot_input = st.text_input("Saisir ou scanner le lot", placeholder="Ex : 0001 ou LOT-20260625-0001")
    with entry2:
        if st.button("Ajouter au dossier"):
            if lot_input:
                cycle = storage.find_cycle(lot_input)
                if cycle:
                    # éviter doublons
                    exists = any(item["lot_number"] == cycle["lot_number"] for item in st.session_state.patient_basket)
                    if not exists:
                        st.session_state.patient_basket.append(cycle)
                    st.rerun()
                else:
                    st.error("Lot introuvable.")

    if st.session_state.patient_basket:
        st.markdown("### Lots liés au dossier")
        rows = []
        for item in st.session_state.patient_basket:
            rows.append(
                {
                    "Lot court": item.get("lot_short"),
                    "Lot complet": item.get("lot_number"),
                    "Autoclave": item.get("autoclave_name"),
                    "N° cycle": item.get("cycle_number"),
                    "DLU": item.get("dlu_date"),
                    "Libéré par": item.get("operator_name"),
                }
            )
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

        if st.button("Vider la sélection"):
            st.session_state.patient_basket = []
            st.rerun()

        rows_html = ""
        for item in st.session_state.patient_basket:
            rows_html += f"""
            <tr>
                <td>{item.get('lot_short', '')}</td>
                <td>{item.get('lot_number', '')}</td>
                <td>{item.get('autoclave_name', '')}</td>
                <td>{item.get('cycle_number', '')}</td>
                <td>{item.get('operator_name', '')}</td>
                <td>{item.get('dlu_date', '')}</td>
            </tr>
            """

        report_html = f"""
        <div class="report-box">
            <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:20px;">
                <div>
                    <div style="font-size:20px;font-weight:800;color:#0f172a;">Fiche de traçabilité patient</div>
                    <div style="font-size:13px;color:#64748b;">Document d’archivage clinique</div>
                </div>
                <div style="font-size:12px;color:#475569;text-align:right;">
                    Date : {datetime.now().strftime("%d/%m/%Y")}<br>
                    Réf. patient : {patient_ref}
                </div>
            </div>

            <div style="margin-top:18px;padding:14px 16px;border-radius:14px;background:#f8fbff;border:1px solid #e6eef8;">
                <strong>Référence patient :</strong> {patient_ref}<br>
                <strong>Acte :</strong> {patient_act}
            </div>

            <table style="width:100%;border-collapse:collapse;margin-top:18px;font-size:13px;">
                <thead>
                    <tr>
                        <th style="text-align:left;padding:10px;border-bottom:1px solid #e2e8f0;">Lot court</th>
                        <th style="text-align:left;padding:10px;border-bottom:1px solid #e2e8f0;">Lot complet</th>
                        <th style="text-align:left;padding:10px;border-bottom:1px solid #e2e8f0;">Autoclave</th>
                        <th style="text-align:left;padding:10px;border-bottom:1px solid #e2e8f0;">Cycle</th>
                        <th style="text-align:left;padding:10px;border-bottom:1px solid #e2e8f0;">Libération</th>
                        <th style="text-align:left;padding:10px;border-bottom:1px solid #e2e8f0;">DLU</th>
                    </tr>
                </thead>
                <tbody>
                    {rows_html}
                </tbody>
            </table>

            <div style="margin-top:28px;font-size:12px;color:#64748b;">
                Document édité automatiquement à partir des lots enregistrés dans SteriTrace Cabinet.
            </div>
        </div>
        """

        st.markdown(report_html, unsafe_allow_html=True)
        st.markdown("<br>", unsafe_allow_html=True)
        render_print_button("Imprimer la fiche patient")
    else:
        st.info("Aucun lot ajouté pour ce dossier patient.")

    st.markdown("</div>", unsafe_allow_html=True)
