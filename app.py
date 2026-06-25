import base64
import hashlib
import html
import json
import re
import sqlite3
import uuid
from datetime import date, datetime, timedelta
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd
import qrcode
import streamlit as st
import streamlit.components.v1 as components
from supabase import Client, create_client

# ============================================================
# STERITRACE CABINET — application originale de traçabilité
# ============================================================
# Cette application est une base complète, personnalisable, pour cabinet dentaire.
# Elle n'est pas une copie d'un produit existant et ne reprend aucun actif propriétaire.
# Elle aide à tracer les cycles, lots, étiquettes et l'utilisation patient.
# Elle ne garantit pas à elle seule une conformité réglementaire : à valider avec votre référent qualité.

APP_NAME = "SteriTrace Cabinet"
APP_TAGLINE = "Traçabilité stérilisation, lots, QR codes et dossiers patient"
LOCAL_DB_PATH = Path("/tmp/steritrace_cabinet.sqlite3")

DEFAULT_OPERATORS = {
    # Codes de démonstration. Remplacez-les par vos propres codes avant usage réel.
    "1234": {"name": "Dr Sébastien", "role": "Praticien"},
    "5678": {"name": "Assistante AD1", "role": "Assistante dentaire"},
    "9012": {"name": "Responsable stérilisation", "role": "Référent qualité"},
}

DEFAULT_AUTOCLAVES = {
    "Autoclave Classe B — Salle stérilisation": {
        "serial": "SN-CLB-2026-001",
        "brand": "À renseigner",
        "location": "Stérilisation",
    },
    "DAC Universal — Rotatifs": {
        "serial": "SN-DAC-2026-001",
        "brand": "À renseigner",
        "location": "Stérilisation",
    },
}

DEFAULT_DEVICES = [
    {"famille": "Examen", "dispositif": "Miroir + sonde + précelles", "quantite": 0, "conditionnement": "Sachet simple"},
    {"famille": "Anesthésie", "dispositif": "Seringue d'anesthésie", "quantite": 0, "conditionnement": "Sachet simple"},
    {"famille": "Rotatifs", "dispositif": "Contre-angle bague rouge", "quantite": 0, "conditionnement": "Double sachet"},
    {"famille": "Rotatifs", "dispositif": "Turbine", "quantite": 0, "conditionnement": "Double sachet"},
    {"famille": "Chirurgie", "dispositif": "Kit chirurgie implantaire", "quantite": 0, "conditionnement": "Double sachet"},
    {"famille": "Cassettes", "dispositif": "Cassette restauration composite", "quantite": 0, "conditionnement": "Sachet simple"},
    {"famille": "Chirurgie", "dispositif": "Daviers d'extraction", "quantite": 0, "conditionnement": "Double sachet"},
]

CYCLE_TYPES = [
    "Charge instruments — 134°C / 18 min",
    "Charge textiles / champs — programme validé cabinet",
    "Rotatifs — cycle dédié",
    "Test Hélix",
    "Test Bowie-Dick",
    "Cycle de maintenance / qualification",
]

PACKAGING_RULES = {
    "Sachet simple": 60,
    "Double sachet": 180,
    "Cassette / conteneur validé": 90,
    "Sans stockage — utilisation immédiate": 0,
}

STATUS_OK = "Conforme"
STATUS_WARN = "À surveiller"
STATUS_BLOCK = "Non conforme"

# ============================================================
# Page, style et utilitaires UI
# ============================================================

st.set_page_config(page_title=APP_NAME, page_icon="🦷", layout="wide")

CUSTOM_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800&display=swap');
:root{
    --bg:#f7f8fb;
    --panel:#ffffff;
    --ink:#111827;
    --muted:#64748b;
    --line:#e5e7eb;
    --primary:#2563eb;
    --primary-dark:#1d4ed8;
    --success:#059669;
    --warning:#d97706;
    --danger:#dc2626;
    --soft-blue:#eff6ff;
}
html, body, [class*="css"] { font-family:'Inter', system-ui, -apple-system, BlinkMacSystemFont, sans-serif; }
.stApp { background: radial-gradient(circle at 10% 0%, #eef5ff 0, #f7f8fb 35%, #f7f8fb 100%); color: var(--ink); }
[data-testid="stHeader"], footer, #MainMenu { display:none !important; }
.block-container { padding-top: 1.4rem !important; max-width: 1220px !important; }
h1,h2,h3,h4 { letter-spacing:-.03em; color:var(--ink); }
p, label, span { color:var(--muted); }
.hero {
    background: linear-gradient(135deg, #ffffff 0%, #eff6ff 100%);
    border:1px solid var(--line); border-radius:24px; padding:24px 28px; margin-bottom:18px;
    box-shadow:0 18px 45px rgba(15,23,42,.06);
    display:flex; align-items:center; justify-content:space-between; gap:18px;
}
.brand { display:flex; align-items:center; gap:14px; }
.brand-icon { width:48px; height:48px; border-radius:16px; display:grid; place-items:center; background:#111827; color:#fff; font-size:24px; }
.brand-title { font-weight:800; font-size:25px; color:#0f172a; line-height:1; }
.brand-subtitle { margin-top:5px; font-size:13px; color:#64748b; }
.pill { display:inline-flex; align-items:center; gap:7px; padding:8px 12px; border-radius:999px; background:#fff; border:1px solid var(--line); color:#334155; font-size:12px; font-weight:700; }
.nav-wrap { background:#fff; border:1px solid var(--line); border-radius:18px; padding:10px; margin-bottom:18px; box-shadow:0 8px 24px rgba(15,23,42,.035); }
.stButton > button {
    border:1px solid #dbe3ef !important; border-radius:12px !important; background:#ffffff !important;
    color:#0f172a !important; font-weight:700 !important; height:44px !important;
    box-shadow:none !important; transition:all .16s ease !important;
}
.stButton > button:hover { transform:translateY(-1px); border-color:#93c5fd !important; background:#eff6ff !important; }
button[kind="primary"], .stButton > button[data-baseweb="button"]:focus { outline: none !important; }
.card { background:#fff; border:1px solid var(--line); border-radius:22px; padding:22px; box-shadow:0 10px 35px rgba(15,23,42,.045); margin-bottom:18px; }
.card-tight { padding:16px; }
.metric-grid { display:grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap:14px; margin-bottom:18px; }
.metric-card { background:#fff; border:1px solid var(--line); border-radius:20px; padding:18px; box-shadow:0 10px 28px rgba(15,23,42,.04); }
.metric-card .label { font-size:12px; color:#64748b; font-weight:800; text-transform:uppercase; letter-spacing:.06em; }
.metric-card .value { font-size:31px; color:#0f172a; font-weight:800; margin-top:6px; }
.metric-card .hint { font-size:12px; color:#94a3b8; margin-top:4px; }
.badge { display:inline-flex; align-items:center; padding:5px 10px; border-radius:999px; font-size:12px; font-weight:800; }
.badge-ok { background:#ecfdf5; color:#047857; border:1px solid #a7f3d0; }
.badge-warn { background:#fffbeb; color:#b45309; border:1px solid #fde68a; }
.badge-block { background:#fef2f2; color:#b91c1c; border:1px solid #fecaca; }
.badge-neutral { background:#f1f5f9; color:#334155; border:1px solid #e2e8f0; }
.step-label { font-size:12px; text-transform:uppercase; font-weight:800; letter-spacing:.06em; color:#64748b; }
.thermal-label {
    width:380px; background:#fff; color:#000; border:1px dashed #94a3b8; border-radius:10px;
    padding:16px; margin:0 auto; font-family:Arial, Helvetica, sans-serif;
}
.thermal-title { text-align:center; border-bottom:2px solid #000; padding-bottom:6px; margin-bottom:10px; font-weight:800; font-size:13px; letter-spacing:.04em; }
.thermal-small { color:#111; font-size:11px; line-height:1.35; }
.print-sheet { background:#fff; border:1px solid var(--line); border-radius:18px; padding:24px; }
.a4 {
    background:#fff; color:#111827; border:1px solid #e5e7eb; border-radius:18px; padding:38px;
    max-width:880px; margin:0 auto; box-shadow:0 12px 35px rgba(15,23,42,.04);
}
.a4 h2 { font-size:21px; margin:0; }
.a4 table { width:100%; border-collapse:collapse; }
.a4 th { text-align:left; padding:10px; background:#f8fafc; color:#475569; font-size:11px; text-transform:uppercase; border-bottom:1px solid #e5e7eb; }
.a4 td { padding:11px 10px; color:#1f2937; font-size:12px; border-bottom:1px solid #f1f5f9; vertical-align:top; }
.notice { border-left:4px solid #2563eb; background:#eff6ff; border-radius:14px; padding:12px 14px; color:#334155; font-size:13px; }
@media print {
    .hero, .nav-wrap, .stButton, [data-testid="stSidebar"], [data-testid="stToolbar"], iframe, .no-print { display:none !important; }
    .block-container { max-width:100% !important; padding:0 !important; }
    .stApp { background:#fff !important; }
    .card, .a4, .print-sheet, .thermal-label { border:none !important; box-shadow:none !important; border-radius:0 !important; }
}
@media (max-width: 900px) { .metric-grid{ grid-template-columns:repeat(2, minmax(0, 1fr)); } .hero{flex-direction:column; align-items:flex-start;} }
</style>
"""
st.markdown(CUSTOM_CSS, unsafe_allow_html=True)


def esc(value: Any) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def to_iso_date(value: Any) -> str:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if value is None:
        return ""
    return str(value)[:10]


def parse_date(value: Any) -> Optional[date]:
    if isinstance(value, date):
        return value
    if isinstance(value, datetime):
        return value.date()
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).date()
    except Exception:
        try:
            return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
        except Exception:
            return None


def days_until(value: Any) -> Optional[int]:
    d = parse_date(value)
    if not d:
        return None
    return (d - date.today()).days


def status_badge(status: str) -> str:
    status = status or STATUS_WARN
    klass = "badge-neutral"
    if status == STATUS_OK:
        klass = "badge-ok"
    elif status == STATUS_WARN:
        klass = "badge-warn"
    elif status == STATUS_BLOCK:
        klass = "badge-block"
    return f'<span class="badge {klass}">{esc(status)}</span>'


def metric_card(label: str, value: Any, hint: str = "") -> str:
    return f"""
    <div class="metric-card">
        <div class="label">{esc(label)}</div>
        <div class="value">{esc(value)}</div>
        <div class="hint">{esc(hint)}</div>
    </div>
    """


def render_header(db_mode: str):
    st.markdown(
        f"""
        <div class="hero">
            <div class="brand">
                <div class="brand-icon">🦷</div>
                <div>
                    <div class="brand-title">{APP_NAME}</div>
                    <div class="brand-subtitle">{APP_TAGLINE}</div>
                </div>
            </div>
            <div style="display:flex; gap:10px; flex-wrap:wrap; justify-content:flex-end;">
                <span class="pill">🗄️ Stockage : {esc(db_mode)}</span>
                <span class="pill">🔐 Mode cabinet</span>
                <span class="pill">🖨️ Étiquettes imprimables</span>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def nav_button(label: str, key: str, page_name: str):
    if st.button(label, key=key, use_container_width=True):
        st.session_state.page = page_name
        st.rerun()


def print_button(label: str = "Imprimer"):
    components.html(
        f"""
        <button onclick="window.parent.print()" style="
            width:100%; height:46px; border:0; border-radius:12px; background:#111827; color:white;
            font-family:Inter, Arial, sans-serif; font-weight:800; cursor:pointer; font-size:14px;">
            🖨️ {esc(label)}
        </button>
        """,
        height=58,
    )


def qr_data_uri(payload: str, box_size: int = 5) -> str:
    qr = qrcode.QRCode(version=None, box_size=box_size, border=1)
    qr.add_data(payload)
    qr.make(fit=True)
    image = qr.make_image(fill_color="black", back_color="white")
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("utf-8")


def lot_number_from_payload(value: str) -> str:
    value = (value or "").strip()
    if not value:
        return ""
    # JSON QR payload generated by this app
    try:
        obj = json.loads(value)
        if isinstance(obj, dict):
            return str(obj.get("lot") or obj.get("lot_number") or "").strip()
    except Exception:
        pass
    # Legacy format : LOT:xxx|DLU:yyyy-mm-dd
    legacy = re.search(r"LOT:([^|\s]+)", value, flags=re.I)
    if legacy:
        return legacy.group(1).strip()
    # Direct lot value
    direct = re.search(r"([A-Z]{2,6}-[0-9]{8}-[0-9]{4}-[A-Z0-9_-]+)", value, flags=re.I)
    if direct:
        return direct.group(1).upper()
    return value


def generate_lot_number(cycle_number: str) -> str:
    clean_cycle = re.sub(r"[^A-Za-z0-9_-]", "", cycle_number or "CYCLE")[:14].upper()
    suffix = uuid.uuid4().hex[:5].upper()
    return f"SC-{datetime.now().strftime('%Y%m%d-%H%M')}-{clean_cycle}-{suffix}"


def hash_operator_code(code: str) -> str:
    return hashlib.sha256((code + "::steritrace").encode("utf-8")).hexdigest()[:12]


# ============================================================
# Données : Supabase ou SQLite local de démonstration
# ============================================================

@st.cache_resource(show_spinner=False)
def get_supabase_client() -> Optional[Client]:
    try:
        url = st.secrets.get("SUPABASE_URL", "")
        key = st.secrets.get("SUPABASE_KEY", "")
    except Exception:
        url, key = "", ""
    if not url or not key:
        return None
    try:
        return create_client(url, key)
    except Exception:
        return None


@st.cache_resource(show_spinner=False)
def get_local_db() -> sqlite3.Connection:
    conn = sqlite3.connect(str(LOCAL_DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS sterilization_cycles (
            id TEXT PRIMARY KEY,
            created_at TEXT NOT NULL,
            lot_number TEXT UNIQUE NOT NULL,
            operator_name TEXT NOT NULL,
            operator_role TEXT,
            autoclave_name TEXT NOT NULL,
            autoclave_serial TEXT,
            cycle_number TEXT NOT NULL,
            cycle_type TEXT NOT NULL,
            process_date TEXT,
            packaging_mode TEXT,
            dlu_date TEXT,
            devices TEXT,
            quantity INTEGER,
            indicators TEXT,
            status TEXT,
            notes TEXT,
            qr_payload TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS patient_traceability_records (
            id TEXT PRIMARY KEY,
            created_at TEXT NOT NULL,
            patient_name TEXT NOT NULL,
            patient_external_id TEXT,
            care_date TEXT,
            practitioner TEXT,
            act TEXT,
            room TEXT,
            lot_numbers TEXT,
            cycles_snapshot TEXT,
            status TEXT,
            notes TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS audit_events (
            id TEXT PRIMARY KEY,
            created_at TEXT NOT NULL,
            event_type TEXT NOT NULL,
            actor TEXT,
            target TEXT,
            payload TEXT
        )
        """
    )
    conn.commit()
    return conn


SUPABASE = get_supabase_client()
DB_MODE = "Supabase" if SUPABASE else "SQLite local démo"


def _jsonify_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    output = dict(payload)
    for key in ["devices", "indicators", "lot_numbers", "cycles_snapshot", "payload"]:
        if key in output and not isinstance(output[key], str):
            output[key] = json.dumps(output[key], ensure_ascii=False)
    return output


def _parse_row(row: Dict[str, Any]) -> Dict[str, Any]:
    obj = dict(row)
    for key in ["devices", "indicators", "lot_numbers", "cycles_snapshot", "payload"]:
        if isinstance(obj.get(key), str):
            try:
                obj[key] = json.loads(obj[key])
            except Exception:
                pass
    return obj


def db_insert(table: str, payload: Dict[str, Any]) -> bool:
    if SUPABASE:
        SUPABASE.table(table).insert(payload).execute()
        return True
    conn = get_local_db()
    p = _jsonify_payload(payload)
    cols = list(p.keys())
    sql = f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join(['?'] * len(cols))})"
    conn.execute(sql, [p[c] for c in cols])
    conn.commit()
    return True


def db_select_cycles(limit: int = 300) -> List[Dict[str, Any]]:
    if SUPABASE:
        data = SUPABASE.table("sterilization_cycles").select("*").order("created_at", desc=True).limit(limit).execute().data
        return data or []
    rows = get_local_db().execute(
        "SELECT * FROM sterilization_cycles ORDER BY created_at DESC LIMIT ?", (limit,)
    ).fetchall()
    return [_parse_row(dict(r)) for r in rows]


def db_get_cycle(lot_number: str) -> Optional[Dict[str, Any]]:
    lot_number = lot_number_from_payload(lot_number)
    if not lot_number:
        return None
    if SUPABASE:
        data = SUPABASE.table("sterilization_cycles").select("*").eq("lot_number", lot_number).limit(1).execute().data
        return data[0] if data else None
    row = get_local_db().execute("SELECT * FROM sterilization_cycles WHERE lot_number = ?", (lot_number,)).fetchone()
    return _parse_row(dict(row)) if row else None


def db_select_patient_records(limit: int = 200) -> List[Dict[str, Any]]:
    if SUPABASE:
        data = SUPABASE.table("patient_traceability_records").select("*").order("created_at", desc=True).limit(limit).execute().data
        return data or []
    rows = get_local_db().execute(
        "SELECT * FROM patient_traceability_records ORDER BY created_at DESC LIMIT ?", (limit,)
    ).fetchall()
    return [_parse_row(dict(r)) for r in rows]


def audit(event_type: str, actor: str = "", target: str = "", payload: Optional[Dict[str, Any]] = None):
    try:
        db_insert(
            "audit_events",
            {
                "id": str(uuid.uuid4()),
                "created_at": now_iso(),
                "event_type": event_type,
                "actor": actor,
                "target": target,
                "payload": payload or {},
            },
        )
    except Exception:
        # L'audit ne doit pas bloquer la saisie métier.
        pass


# ============================================================
# Documents imprimables
# ============================================================

def make_qr_payload(cycle: Dict[str, Any]) -> str:
    payload = {
        "app": "SteriTrace Cabinet",
        "lot": cycle["lot_number"],
        "cycle": cycle["cycle_number"],
        "dlu": cycle["dlu_date"],
        "status": cycle["status"],
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def label_html(cycle: Dict[str, Any], device_name: str = "Lot stérile") -> str:
    payload = cycle.get("qr_payload") or make_qr_payload(cycle)
    qr_uri = qr_data_uri(payload, box_size=4)
    dlu = parse_date(cycle.get("dlu_date"))
    dlu_txt = dlu.strftime("%d/%m/%Y") if dlu else "—"
    return f"""
    <div class="thermal-label">
        <div class="thermal-title">TRAÇABILITÉ STÉRILISATION</div>
        <table style="width:100%; border-collapse:collapse;">
            <tr>
                <td style="vertical-align:top; width:70%;">
                    <div style="font-size:13px; font-weight:800; color:#000;">{esc(device_name)}</div>
                    <div class="thermal-small">
                        <b>Lot :</b> {esc(cycle.get('lot_number'))}<br>
                        <b>DLU :</b> {esc(dlu_txt)}<br>
                        <b>Cycle :</b> {esc(cycle.get('cycle_number'))}<br>
                        <b>Autoclave :</b> {esc(cycle.get('autoclave_name'))}<br>
                        <b>Opérateur :</b> {esc(cycle.get('operator_name'))}
                    </div>
                </td>
                <td style="vertical-align:top; text-align:right; width:30%;">
                    <img src="{qr_uri}" style="width:86px; height:86px;" />
                </td>
            </tr>
        </table>
    </div>
    """


def labels_sheet_html(cycle: Dict[str, Any]) -> str:
    devices = cycle.get("devices") or []
    labels = []
    for device in devices:
        qty = int(device.get("quantite") or device.get("quantity") or 1)
        name = device.get("dispositif") or device.get("device") or "Dispositif"
        for idx in range(max(1, qty)):
            suffix = f" — {idx + 1}/{qty}" if qty > 1 else ""
            labels.append(label_html(cycle, f"{name}{suffix}"))
    if not labels:
        labels = [label_html(cycle)]
    return f"""
    <html><head><meta charset="utf-8"><title>Étiquettes {esc(cycle.get('lot_number'))}</title>
    <style>
    body{{font-family:Arial, sans-serif; margin:0; padding:18px;}}
    .grid{{display:grid; grid-template-columns:repeat(2, 390px); gap:14px;}}
    .thermal-label{{width:360px; border:1px dashed #999; padding:12px; break-inside:avoid; color:#000;}}
    .thermal-title{{text-align:center; border-bottom:2px solid #000; padding-bottom:6px; margin-bottom:10px; font-weight:800; font-size:12px; letter-spacing:.04em;}}
    .thermal-small{{font-size:11px; line-height:1.35; color:#111;}}
    @media print{{button{{display:none}} .thermal-label{{border:0}} body{{padding:0}}}}
    </style></head><body>
    <button onclick="window.print()" style="margin-bottom:12px; padding:10px 16px; font-weight:700;">Imprimer</button>
    <div class="grid">{''.join(labels)}</div>
    </body></html>
    """


def patient_record_html(record: Dict[str, Any], cycles: List[Dict[str, Any]]) -> str:
    rows = ""
    for cycle in cycles:
        if cycle:
            devices = cycle.get("devices") or []
            device_txt = ", ".join([str(d.get("dispositif") or d.get("device") or "") for d in devices])[:220]
            rows += f"""
            <tr>
                <td><b>{esc(cycle.get('lot_number'))}</b><br><small>{esc(device_txt)}</small></td>
                <td>{esc(cycle.get('autoclave_name'))}<br><small>S/N {esc(cycle.get('autoclave_serial'))}</small></td>
                <td>{esc(cycle.get('cycle_number'))}<br><small>{esc(cycle.get('cycle_type'))}</small></td>
                <td>{esc(cycle.get('operator_name'))}</td>
                <td>{status_badge(cycle.get('status'))}</td>
            </tr>
            """
        else:
            rows += """
            <tr><td colspan="5" style="color:#b91c1c; font-weight:800;">Lot absent de la base : contrôle manuel obligatoire</td></tr>
            """
    created = parse_date(record.get("created_at")) or date.today()
    return f"""
    <div class="a4">
        <div style="display:flex; justify-content:space-between; gap:20px; border-bottom:2px solid #e5e7eb; padding-bottom:18px; margin-bottom:22px;">
            <div>
                <h2>Fiche de traçabilité matériel — dossier patient</h2>
                <p style="margin:5px 0 0; font-size:12px; color:#64748b;">Document généré par SteriTrace Cabinet</p>
            </div>
            <div style="text-align:right; font-size:12px; color:#475569;">
                <b>Date édition :</b> {esc(created.strftime('%d/%m/%Y'))}<br>
                <b>Statut :</b> {status_badge(record.get('status'))}
            </div>
        </div>
        <table style="margin-bottom:22px; background:#f8fafc; border:1px solid #e5e7eb; border-radius:12px; overflow:hidden;">
            <tr>
                <td><b>Patient :</b> {esc(record.get('patient_name'))}</td>
                <td><b>ID dossier :</b> {esc(record.get('patient_external_id') or '—')}</td>
            </tr>
            <tr>
                <td><b>Date soin :</b> {esc(record.get('care_date') or '—')}</td>
                <td><b>Praticien :</b> {esc(record.get('practitioner') or '—')}</td>
            </tr>
            <tr>
                <td><b>Acte :</b> {esc(record.get('act') or '—')}</td>
                <td><b>Salle :</b> {esc(record.get('room') or '—')}</td>
            </tr>
        </table>
        <h4 style="margin-bottom:8px; color:#334155;">Lots et dispositifs utilisés</h4>
        <table>
            <thead><tr><th>Lot / dispositifs</th><th>Stérilisateur</th><th>Cycle</th><th>Libération</th><th>Validation</th></tr></thead>
            <tbody>{rows}</tbody>
        </table>
        <div style="display:flex; justify-content:space-between; margin-top:54px; font-size:12px;">
            <div><b>Signature praticien</b><br><br><br>______________________________</div>
            <div style="text-align:right; color:#64748b; max-width:320px;">À archiver dans le dossier patient selon votre procédure interne. Les données doivent être protégées conformément à votre politique RGPD.</div>
        </div>
    </div>
    """


def full_patient_record_html(record: Dict[str, Any], cycles: List[Dict[str, Any]]) -> str:
    body = patient_record_html(record, cycles)
    return f"""
    <html><head><meta charset="utf-8"><title>Fiche patient</title>
    <style>{CUSTOM_CSS.replace('<style>', '').replace('</style>', '')}</style></head>
    <body><button onclick="window.print()" style="margin:16px; padding:10px 16px; font-weight:700;">Imprimer</button>{body}</body></html>
    """


# ============================================================
# État de session
# ============================================================

if "page" not in st.session_state:
    st.session_state.page = "dashboard"
if "wizard_step" not in st.session_state:
    st.session_state.wizard_step = 1
if "wizard" not in st.session_state:
    st.session_state.wizard = {}
if "patient_basket" not in st.session_state:
    st.session_state.patient_basket = []
if "last_cycle_saved" not in st.session_state:
    st.session_state.last_cycle_saved = None

render_header(DB_MODE)

st.markdown('<div class="nav-wrap">', unsafe_allow_html=True)
nav_cols = st.columns(5)
with nav_cols[0]:
    nav_button("📊 Dashboard", "nav_dashboard", "dashboard")
with nav_cols[1]:
    nav_button("➕ Nouveau cycle", "nav_cycle", "cycle")
with nav_cols[2]:
    nav_button("🔎 Rechercher un lot", "nav_search", "search")
with nav_cols[3]:
    nav_button("📄 Dossier patient", "nav_patient", "patient")
with nav_cols[4]:
    nav_button("⚙️ Paramètres", "nav_settings", "settings")
st.markdown('</div>', unsafe_allow_html=True)

# ============================================================
# Pages
# ============================================================


def page_dashboard():
    st.markdown("### Vue d'ensemble")
    try:
        cycles = db_select_cycles()
        records = db_select_patient_records()
    except Exception as exc:
        st.error("Impossible de lire la base. Vérifiez que le schéma Supabase est installé ou utilisez le mode local.")
        st.exception(exc)
        return

    total = len(cycles)
    ok = sum(1 for c in cycles if c.get("status") == STATUS_OK)
    expired = sum(1 for c in cycles if (days_until(c.get("dlu_date")) is not None and days_until(c.get("dlu_date")) < 0))
    soon = sum(1 for c in cycles if (days_until(c.get("dlu_date")) is not None and 0 <= days_until(c.get("dlu_date")) <= 14))
    st.markdown(
        f"""
        <div class="metric-grid">
            {metric_card('Cycles enregistrés', total, 'Lots de stérilisation tracés')}
            {metric_card('Cycles conformes', ok, 'Selon les validations saisies')}
            {metric_card('DLU ≤ 14 jours', soon, 'Lots à consommer ou contrôler')}
            {metric_card('Fiches patient', len(records), 'Traçabilité liée au soin')}
        </div>
        """,
        unsafe_allow_html=True,
    )

    if expired:
        st.warning(f"{expired} lot(s) ont une DLU dépassée. Ne les utilisez pas sans contrôle selon votre procédure interne.")

    left, right = st.columns([2, 1])
    with left:
        st.markdown('<div class="card">', unsafe_allow_html=True)
        st.markdown("#### Journal des derniers cycles")
        if cycles:
            df = pd.DataFrame(cycles)
            keep = ["created_at", "lot_number", "operator_name", "autoclave_name", "cycle_number", "cycle_type", "dlu_date", "quantity", "status"]
            for k in keep:
                if k not in df.columns:
                    df[k] = ""
            df = df[keep].copy()
            df["jours_dlu"] = df["dlu_date"].apply(lambda x: days_until(x) if days_until(x) is not None else "")
            df.columns = ["Créé le", "Lot", "Opérateur", "Stérilisateur", "N° cycle", "Programme", "DLU", "Qté", "Statut", "Jours DLU"]
            st.dataframe(df.head(60), use_container_width=True, hide_index=True)
        else:
            st.info("Aucun cycle pour le moment. Commencez par créer un nouveau cycle.")
        st.markdown('</div>', unsafe_allow_html=True)

    with right:
        st.markdown('<div class="card card-tight">', unsafe_allow_html=True)
        st.markdown("#### Activité récente")
        if cycles:
            chart_df = pd.DataFrame(cycles)
            chart_df["jour"] = chart_df["created_at"].astype(str).str[:10]
            daily = chart_df.groupby("jour").size().tail(14)
            st.bar_chart(daily)
        else:
            st.caption("Le graphique apparaîtra après les premiers lots.")
        st.markdown("---")
        st.markdown("#### État système")
        st.markdown(f"- Stockage : **{DB_MODE}**")
        st.markdown("- QR codes : **actifs**")
        st.markdown("- Impression : **navigateur / PDF**")
        st.markdown('</div>', unsafe_allow_html=True)


def require_operator_step():
    st.markdown('<div class="card">', unsafe_allow_html=True)
    st.markdown("#### Étape 1/4 — Identification de l'opérateur")
    st.caption("Chaque libération de charge doit être rattachée à une personne identifiée.")
    code = st.text_input("Code opérateur", type="password", placeholder="Ex. 1234", key="operator_code")
    c1, c2 = st.columns([1, 2])
    with c1:
        if st.button("Valider l'identité", type="primary", use_container_width=True):
            if code in DEFAULT_OPERATORS:
                operator = DEFAULT_OPERATORS[code]
                st.session_state.wizard["operator"] = {
                    "code_hash": hash_operator_code(code),
                    "name": operator["name"],
                    "role": operator["role"],
                }
                st.session_state.wizard_step = 2
                st.rerun()
            else:
                st.error("Code inconnu. Vérifiez le code de l'opérateur.")
    with c2:
        with st.expander("Codes de démonstration"):
            st.write("1234 → Dr Sébastien")
            st.write("5678 → Assistante AD1")
            st.write("9012 → Responsable stérilisation")
    st.markdown('</div>', unsafe_allow_html=True)


def cycle_params_step():
    st.markdown('<div class="card">', unsafe_allow_html=True)
    st.markdown("#### Étape 2/4 — Paramètres du cycle")
    operator = st.session_state.wizard.get("operator", {})
    st.info(f"Opérateur authentifié : {operator.get('name')} — {operator.get('role')}")

    col1, col2 = st.columns(2)
    with col1:
        autoclave_name = st.selectbox("Stérilisateur / appareil", list(DEFAULT_AUTOCLAVES.keys()))
        cycle_number = st.text_input("Numéro du cycle affiché par la machine", placeholder="Ex. 1452")
        process_date = st.date_input("Date du cycle", value=date.today())
    with col2:
        cycle_type = st.selectbox("Programme / type de cycle", CYCLE_TYPES)
        load_status = st.selectbox("Résultat de libération", [STATUS_OK, STATUS_WARN, STATUS_BLOCK])
        notes = st.text_area("Notes / incident / référence ticket", placeholder="Optionnel")

    st.markdown("##### Contrôles et indicateurs")
    i1, i2, i3, i4 = st.columns(4)
    with i1:
        physical = st.selectbox("Paramètres physiques", ["OK", "À vérifier", "Non conforme"])
    with i2:
        chemical = st.selectbox("Indicateur chimique", ["OK", "À vérifier", "Non conforme", "Non applicable"])
    with i3:
        helix = st.selectbox("Test Hélix", ["OK", "À vérifier", "Non conforme", "Non applicable"])
    with i4:
        bowie = st.selectbox("Bowie-Dick", ["OK", "À vérifier", "Non conforme", "Non applicable"])

    back, next_col = st.columns(2)
    with back:
        if st.button("← Retour", use_container_width=True):
            st.session_state.wizard_step = 1
            st.rerun()
    with next_col:
        if st.button("Continuer vers la composition", type="primary", use_container_width=True):
            if not cycle_number.strip():
                st.error("Le numéro de cycle est indispensable.")
            else:
                auto = DEFAULT_AUTOCLAVES[autoclave_name]
                st.session_state.wizard.update(
                    {
                        "autoclave_name": autoclave_name,
                        "autoclave_serial": auto["serial"],
                        "cycle_number": cycle_number.strip(),
                        "cycle_type": cycle_type,
                        "process_date": process_date.isoformat(),
                        "status": load_status,
                        "notes": notes,
                        "indicators": {
                            "physical_parameters": physical,
                            "chemical_indicator": chemical,
                            "helix_test": helix,
                            "bowie_dick": bowie,
                        },
                    }
                )
                if any(x == "Non conforme" for x in [physical, chemical, helix, bowie]) and load_status == STATUS_OK:
                    st.session_state.wizard["status"] = STATUS_WARN
                st.session_state.wizard_step = 3
                st.rerun()
    st.markdown('</div>', unsafe_allow_html=True)


def composition_step():
    st.markdown('<div class="card">', unsafe_allow_html=True)
    st.markdown("#### Étape 3/4 — Composition de la charge")
    st.caption("Indiquez les dispositifs présents. Les lignes à quantité 0 seront ignorées.")

    initial = st.session_state.wizard.get("device_editor") or DEFAULT_DEVICES
    df = pd.DataFrame(initial)
    edited = st.data_editor(
        df,
        use_container_width=True,
        num_rows="dynamic",
        hide_index=True,
        column_config={
            "famille": st.column_config.TextColumn("Famille"),
            "dispositif": st.column_config.TextColumn("Dispositif"),
            "quantite": st.column_config.NumberColumn("Qté", min_value=0, step=1),
            "conditionnement": st.column_config.SelectboxColumn(
                "Conditionnement", options=list(PACKAGING_RULES.keys())
            ),
        },
        key="device_editor_widget",
    )

    col1, col2, col3 = st.columns(3)
    with col1:
        packaging_mode = st.selectbox("Règle DLU principale", list(PACKAGING_RULES.keys()))
    with col2:
        days = PACKAGING_RULES[packaging_mode]
        default_dlu = date.today() + timedelta(days=days)
        dlu_date = st.date_input("DLU calculée / ajustable", value=default_dlu)
    with col3:
        storage = st.selectbox("Stockage prévu", ["Armoire fermée", "Salle de soins", "Bloc / chirurgie", "Utilisation immédiate"])

    devices = edited.fillna("").to_dict(orient="records")
    selected = []
    for d in devices:
        try:
            qty = int(d.get("quantite") or 0)
        except Exception:
            qty = 0
        if qty > 0 and str(d.get("dispositif", "")).strip():
            item = dict(d)
            item["quantite"] = qty
            selected.append(item)
    quantity = sum(int(d.get("quantite", 0)) for d in selected)

    st.markdown(f"<div class='notice'>Charge préparée : <b>{quantity}</b> dispositif(s) sur <b>{len(selected)}</b> ligne(s).</div>", unsafe_allow_html=True)

    back, next_col = st.columns(2)
    with back:
        if st.button("← Retour paramètres", use_container_width=True):
            st.session_state.wizard["device_editor"] = devices
            st.session_state.wizard_step = 2
            st.rerun()
    with next_col:
        if st.button("Prévisualiser le lot", type="primary", use_container_width=True):
            if not selected:
                st.error("Ajoutez au moins un dispositif avec une quantité supérieure à 0.")
            else:
                st.session_state.wizard.update(
                    {
                        "device_editor": devices,
                        "devices": selected,
                        "quantity": quantity,
                        "packaging_mode": packaging_mode,
                        "dlu_date": dlu_date.isoformat(),
                        "storage": storage,
                    }
                )
                if "lot_number" not in st.session_state.wizard:
                    st.session_state.wizard["lot_number"] = generate_lot_number(st.session_state.wizard.get("cycle_number", ""))
                st.session_state.wizard_step = 4
                st.rerun()
    st.markdown('</div>', unsafe_allow_html=True)


def review_step():
    st.markdown('<div class="card">', unsafe_allow_html=True)
    st.markdown("#### Étape 4/4 — Validation, étiquettes et enregistrement")
    w = st.session_state.wizard
    operator = w.get("operator", {})
    cycle = {
        "id": str(uuid.uuid4()),
        "created_at": now_iso(),
        "lot_number": w.get("lot_number") or generate_lot_number(w.get("cycle_number", "")),
        "operator_name": operator.get("name", ""),
        "operator_role": operator.get("role", ""),
        "autoclave_name": w.get("autoclave_name", ""),
        "autoclave_serial": w.get("autoclave_serial", ""),
        "cycle_number": w.get("cycle_number", ""),
        "cycle_type": w.get("cycle_type", ""),
        "process_date": w.get("process_date", date.today().isoformat()),
        "packaging_mode": w.get("packaging_mode", ""),
        "dlu_date": w.get("dlu_date", ""),
        "devices": w.get("devices", []),
        "quantity": int(w.get("quantity") or 0),
        "indicators": w.get("indicators", {}),
        "status": w.get("status", STATUS_WARN),
        "notes": w.get("notes", ""),
        "qr_payload": "",
    }
    cycle["qr_payload"] = make_qr_payload(cycle)

    c1, c2 = st.columns([1, 1])
    with c1:
        st.markdown("##### Aperçu étiquette")
        st.markdown(label_html(cycle), unsafe_allow_html=True)
        st.download_button(
            "Télécharger les étiquettes HTML",
            labels_sheet_html(cycle),
            file_name=f"etiquettes_{cycle['lot_number']}.html",
            mime="text/html",
            use_container_width=True,
        )
        print_button("Imprimer depuis la page")
    with c2:
        st.markdown("##### Résumé du lot")
        st.markdown(f"**Lot :** `{cycle['lot_number']}`")
        st.markdown(f"**Statut :** {status_badge(cycle['status'])}", unsafe_allow_html=True)
        st.markdown(f"**Opérateur :** {esc(cycle['operator_name'])}")
        st.markdown(f"**Stérilisateur :** {esc(cycle['autoclave_name'])} — S/N {esc(cycle['autoclave_serial'])}")
        st.markdown(f"**Cycle :** {esc(cycle['cycle_number'])} — {esc(cycle['cycle_type'])}")
        st.markdown(f"**DLU :** {esc(cycle['dlu_date'])}")
        st.markdown(f"**Quantité :** {cycle['quantity']} dispositif(s)")
        st.json({"indicators": cycle["indicators"], "devices": cycle["devices"]}, expanded=False)

    st.markdown("---")
    back, save, reset = st.columns(3)
    with back:
        if st.button("← Modifier la composition", use_container_width=True):
            st.session_state.wizard_step = 3
            st.rerun()
    with save:
        if st.button("🔒 Clôturer et enregistrer", type="primary", use_container_width=True):
            try:
                existing = db_get_cycle(cycle["lot_number"])
                if existing:
                    st.error("Ce numéro de lot existe déjà. Régénérez un lot ou recommencez le cycle.")
                    return
                db_insert("sterilization_cycles", cycle)
                audit("cycle_created", actor=cycle["operator_name"], target=cycle["lot_number"], payload={"status": cycle["status"]})
                st.session_state.last_cycle_saved = cycle
                st.session_state.wizard = {}
                st.session_state.wizard_step = 1
                st.success("Lot enregistré. Les étiquettes peuvent être imprimées ou téléchargées.")
                st.rerun()
            except Exception as exc:
                st.error("Échec d'enregistrement. Si vous utilisez Supabase, installez d'abord le schéma SQL fourni.")
                st.exception(exc)
    with reset:
        if st.button("Annuler le cycle", use_container_width=True):
            st.session_state.wizard = {}
            st.session_state.wizard_step = 1
            st.rerun()
    st.markdown('</div>', unsafe_allow_html=True)


def page_cycle():
    st.markdown("### Nouveau cycle de stérilisation")
    labels = ["1. Opérateur", "2. Cycle", "3. Charge", "4. Validation"]
    cols = st.columns(4)
    for i, col in enumerate(cols, start=1):
        with col:
            active = "badge-ok" if st.session_state.wizard_step >= i else "badge-neutral"
            st.markdown(f"<span class='badge {active}'>{labels[i-1]}</span>", unsafe_allow_html=True)
    st.progress(st.session_state.wizard_step / 4)

    if st.session_state.last_cycle_saved:
        with st.expander("Dernier lot enregistré"):
            c = st.session_state.last_cycle_saved
            st.markdown(f"Lot : `{c['lot_number']}` — DLU : **{c['dlu_date']}**")
            st.download_button(
                "Télécharger à nouveau les étiquettes",
                labels_sheet_html(c),
                file_name=f"etiquettes_{c['lot_number']}.html",
                mime="text/html",
            )

    if st.session_state.wizard_step == 1:
        require_operator_step()
    elif st.session_state.wizard_step == 2:
        cycle_params_step()
    elif st.session_state.wizard_step == 3:
        composition_step()
    else:
        review_step()


def page_search():
    st.markdown("### Rechercher / contrôler un lot")
    st.markdown('<div class="card">', unsafe_allow_html=True)
    raw = st.text_input("Lot ou contenu QR code", placeholder="Collez le numéro de lot ou scannez le QR code")
    lot = lot_number_from_payload(raw)
    if raw:
        st.caption(f"Lot détecté : {lot}")
    if st.button("Rechercher", type="primary", use_container_width=True) or raw:
        if lot:
            try:
                cycle = db_get_cycle(lot)
            except Exception as exc:
                st.error("Recherche impossible : problème d'accès à la base.")
                st.exception(exc)
                cycle = None
            if cycle:
                remaining = days_until(cycle.get("dlu_date"))
                if remaining is not None and remaining < 0:
                    st.error(f"DLU dépassée depuis {abs(remaining)} jour(s).")
                elif remaining is not None and remaining <= 14:
                    st.warning(f"DLU proche : {remaining} jour(s) restant(s).")
                else:
                    st.success("Lot trouvé.")
                c1, c2 = st.columns([1, 1])
                with c1:
                    st.markdown(f"**Lot :** `{cycle.get('lot_number')}`")
                    st.markdown(f"**Statut :** {status_badge(cycle.get('status'))}", unsafe_allow_html=True)
                    st.markdown(f"**DLU :** {cycle.get('dlu_date')}")
                    st.markdown(f"**Opérateur :** {cycle.get('operator_name')}")
                    st.markdown(f"**Cycle :** {cycle.get('cycle_number')} — {cycle.get('cycle_type')}")
                    st.markdown(f"**Autoclave :** {cycle.get('autoclave_name')} — S/N {cycle.get('autoclave_serial')}")
                with c2:
                    st.markdown(label_html(cycle), unsafe_allow_html=True)
                    st.download_button(
                        "Télécharger l'étiquette",
                        labels_sheet_html(cycle),
                        file_name=f"etiquette_{cycle.get('lot_number')}.html",
                        mime="text/html",
                        use_container_width=True,
                    )
                st.markdown("##### Composition")
                st.dataframe(pd.DataFrame(cycle.get("devices") or []), use_container_width=True, hide_index=True)
            else:
                st.error("Aucun lot trouvé. Contrôle manuel obligatoire avant utilisation.")
    st.markdown('</div>', unsafe_allow_html=True)


def page_patient():
    st.markdown("### Dossier patient et liaison des lots")
    st.markdown('<div class="notice no-print">Attention : vous saisissez potentiellement des données personnelles de santé. Configurez Supabase, les droits, la sauvegarde et votre politique RGPD avant un usage réel.</div>', unsafe_allow_html=True)
    st.markdown(" ")
    st.markdown('<div class="card">', unsafe_allow_html=True)
    c1, c2 = st.columns(2)
    with c1:
        patient_name = st.text_input("Nom / identifiant patient", placeholder="Ex. Madame Marie Dupont")
        patient_external_id = st.text_input("ID dossier patient", placeholder="Optionnel")
        care_date = st.date_input("Date du soin", value=date.today())
    with c2:
        practitioner = st.text_input("Praticien", placeholder="Ex. Dr ...")
        act = st.text_input("Acte réalisé", placeholder="Ex. Pose implant / extraction / omnipratique")
        room = st.text_input("Salle", placeholder="Ex. Salle 1")
    st.markdown("---")
    scan_col, basket_col = st.columns([1, 1])
    with scan_col:
        raw_lot = st.text_input("Scanner / saisir un lot", placeholder="SC-YYYYMMDD-...")
        if st.button("Ajouter le lot au dossier", use_container_width=True):
            lot = lot_number_from_payload(raw_lot)
            if lot and lot not in st.session_state.patient_basket:
                st.session_state.patient_basket.append(lot)
                st.rerun()
            elif lot:
                st.info("Lot déjà ajouté.")
            else:
                st.error("Saisissez un lot.")
    with basket_col:
        st.markdown("##### Lots sélectionnés")
        if not st.session_state.patient_basket:
            st.caption("Aucun lot ajouté.")
        else:
            for lot in st.session_state.patient_basket:
                st.code(lot)
            if st.button("Vider la sélection", use_container_width=True):
                st.session_state.patient_basket = []
                st.rerun()
    st.markdown('</div>', unsafe_allow_html=True)

    if st.session_state.patient_basket:
        cycles = [db_get_cycle(lot) for lot in st.session_state.patient_basket]
        has_missing = any(c is None for c in cycles)
        has_blocked = any(c and c.get("status") == STATUS_BLOCK for c in cycles)
        has_expired = any(c and days_until(c.get("dlu_date")) is not None and days_until(c.get("dlu_date")) < 0 for c in cycles)
        record_status = STATUS_BLOCK if (has_missing or has_blocked or has_expired) else STATUS_OK
        record = {
            "id": str(uuid.uuid4()),
            "created_at": now_iso(),
            "patient_name": patient_name or "Patient non renseigné",
            "patient_external_id": patient_external_id,
            "care_date": care_date.isoformat(),
            "practitioner": practitioner,
            "act": act,
            "room": room,
            "lot_numbers": list(st.session_state.patient_basket),
            "cycles_snapshot": cycles,
            "status": record_status,
            "notes": "",
        }
        st.markdown(patient_record_html(record, cycles), unsafe_allow_html=True)
        print_button("Imprimer la fiche patient")
        st.download_button(
            "Télécharger la fiche patient HTML",
            full_patient_record_html(record, cycles),
            file_name=f"fiche_patient_{datetime.now().strftime('%Y%m%d_%H%M')}.html",
            mime="text/html",
            use_container_width=True,
        )
        if st.button("Enregistrer la fiche dans la base", type="primary", use_container_width=True):
            if not patient_name.strip():
                st.error("Renseignez au minimum le nom ou l'identifiant patient.")
            else:
                try:
                    db_insert("patient_traceability_records", record)
                    audit("patient_record_created", actor=practitioner, target=patient_name, payload={"lots": record["lot_numbers"]})
                    st.success("Fiche patient enregistrée.")
                    st.session_state.patient_basket = []
                    st.rerun()
                except Exception as exc:
                    st.error("Échec de sauvegarde. Vérifiez le schéma Supabase.")
                    st.exception(exc)


def page_settings():
    st.markdown("### Paramètres et installation")
    st.markdown('<div class="card">', unsafe_allow_html=True)
    st.markdown("#### Configuration actuelle")
    st.markdown(f"- Stockage actif : **{DB_MODE}**")
    st.markdown(f"- Base locale de démonstration : `{LOCAL_DB_PATH}`")
    st.markdown("- Produit : application originale, personnalisable au cabinet")
    st.markdown('</div>', unsafe_allow_html=True)

    left, right = st.columns(2)
    with left:
        st.markdown('<div class="card">', unsafe_allow_html=True)
        st.markdown("#### Opérateurs démo")
        st.dataframe(pd.DataFrame([{"code": k, **v} for k, v in DEFAULT_OPERATORS.items()]), use_container_width=True, hide_index=True)
        st.markdown("#### Stérilisateurs")
        st.dataframe(pd.DataFrame([{ "nom": k, **v } for k, v in DEFAULT_AUTOCLAVES.items()]), use_container_width=True, hide_index=True)
        st.markdown('</div>', unsafe_allow_html=True)
    with right:
        st.markdown('<div class="card">', unsafe_allow_html=True)
        st.markdown("#### Secrets Streamlit")
        st.code('''SUPABASE_URL="https://xxxx.supabase.co"\nSUPABASE_KEY="votre_anon_key_ou_service_role_selon_votre_architecture"''', language="toml")
        st.markdown("#### Variables à adapter")
        st.write("Modifiez `DEFAULT_OPERATORS`, `DEFAULT_AUTOCLAVES`, `DEFAULT_DEVICES` et `PACKAGING_RULES` dans `app.py`.")
        st.markdown('</div>', unsafe_allow_html=True)

    st.markdown('<div class="card">', unsafe_allow_html=True)
    st.markdown("#### Schéma Supabase attendu")
    st.code(SUPABASE_SCHEMA_SQL, language="sql")
    st.markdown('</div>', unsafe_allow_html=True)


SUPABASE_SCHEMA_SQL = r'''
-- À exécuter dans Supabase SQL Editor avant déploiement.
create extension if not exists "pgcrypto";

create table if not exists public.sterilization_cycles (
  id uuid primary key default gen_random_uuid(),
  created_at timestamptz not null default now(),
  lot_number text not null unique,
  operator_name text not null,
  operator_role text,
  autoclave_name text not null,
  autoclave_serial text,
  cycle_number text not null,
  cycle_type text not null,
  process_date date,
  packaging_mode text,
  dlu_date date,
  devices jsonb not null default '[]'::jsonb,
  quantity integer not null default 0,
  indicators jsonb not null default '{}'::jsonb,
  status text not null default 'À surveiller',
  notes text,
  qr_payload text
);

create table if not exists public.patient_traceability_records (
  id uuid primary key default gen_random_uuid(),
  created_at timestamptz not null default now(),
  patient_name text not null,
  patient_external_id text,
  care_date date,
  practitioner text,
  act text,
  room text,
  lot_numbers jsonb not null default '[]'::jsonb,
  cycles_snapshot jsonb not null default '[]'::jsonb,
  status text not null default 'À surveiller',
  notes text
);

create table if not exists public.audit_events (
  id uuid primary key default gen_random_uuid(),
  created_at timestamptz not null default now(),
  event_type text not null,
  actor text,
  target text,
  payload jsonb not null default '{}'::jsonb
);

create index if not exists idx_sterilization_cycles_lot on public.sterilization_cycles(lot_number);
create index if not exists idx_sterilization_cycles_created_at on public.sterilization_cycles(created_at desc);
create index if not exists idx_patient_records_created_at on public.patient_traceability_records(created_at desc);

-- Sécurité : exemple simple. À ajuster selon votre architecture d'authentification.
alter table public.sterilization_cycles enable row level security;
alter table public.patient_traceability_records enable row level security;
alter table public.audit_events enable row level security;

-- Pour un prototype avec clé anon, vous pouvez temporairement ouvrir les droits ci-dessous.
-- Pour un usage réel, remplacez par des politiques authentifiées strictes.
drop policy if exists "prototype_read_cycles" on public.sterilization_cycles;
create policy "prototype_read_cycles" on public.sterilization_cycles for select using (true);
drop policy if exists "prototype_insert_cycles" on public.sterilization_cycles;
create policy "prototype_insert_cycles" on public.sterilization_cycles for insert with check (true);

drop policy if exists "prototype_read_patient_records" on public.patient_traceability_records;
create policy "prototype_read_patient_records" on public.patient_traceability_records for select using (true);
drop policy if exists "prototype_insert_patient_records" on public.patient_traceability_records;
create policy "prototype_insert_patient_records" on public.patient_traceability_records for insert with check (true);

drop policy if exists "prototype_read_audit" on public.audit_events;
create policy "prototype_read_audit" on public.audit_events for select using (true);
drop policy if exists "prototype_insert_audit" on public.audit_events;
create policy "prototype_insert_audit" on public.audit_events for insert with check (true);
'''

# Dispatch final
try:
    if st.session_state.page == "dashboard":
        page_dashboard()
    elif st.session_state.page == "cycle":
        page_cycle()
    elif st.session_state.page == "search":
        page_search()
    elif st.session_state.page == "patient":
        page_patient()
    elif st.session_state.page == "settings":
        page_settings()
except Exception as exc:
    st.error("Une erreur non prévue est survenue.")
    st.exception(exc)
