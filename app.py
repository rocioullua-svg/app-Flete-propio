# -*- coding: utf-8 -*-
# app.py - Panel Web de Logística con Streamlit
import pandas as pd
import streamlit as st
import requests
from requests.auth import HTTPBasicAuth
import json
from pathlib import Path
import re
from datetime import datetime

# ====== CONFIGURACIÓN DE LA PÁGINA WEB ======
st.set_page_config(page_title="PFlete propio", page_icon="🚀", layout="wide")

# ====== CREDENCIALES Y VARIABLES ======
ZIPNOVA_KEY = "420189e6-86bc-4ac1-9cf5-082fb3e0b284"
ZIPNOVA_SECRET = "3d8022da-ed44-4539-ac64-46b62be0bd93"
ZIPNOVA_ACCOUNT_ID = "3521"
ZIPNOVA_DOMAIN = "https://api.zipnova.com.ar"
OWN_FLEET_CARRIER_ID = 7
STATUS_CANDIDATES = ["en_camino", "En camino", "in_transit", "in-transit"]

FILE_DISPATCHED = Path(".dispatched_zipnova.json")
FILE_DELIVERED = Path(".delivered_zipnova.json")

LIGHTDATA_INSTANCES = [
    {
        "name": "snowflex",
        "url": "https://snowflex.lightdata.app/api/v1/",
        "token": "BiU5cl31800701521682066670739526344677672713392822783260776481613964891856136668488482466189082353825911406590869705572361599576"
    },
    {
        "name": "flexit",
        "url": "https://flexit.lightdata.app/api/v1/",
        "token": "3Gb08IhB798107725572353656100489086727262214465873285461247903004721674312439792432336326884202814258602124946180668275697972127"
    }
]

# ====== FUNCIONES DE MEMORIA INTERNA ======
def _load_memory(path: Path) -> set:
    if path.exists():
        try: return set(json.loads(path.read_text()))
        except Exception: pass
    return set()

def _save_memory(path: Path, ids: set):
    path.write_text(json.dumps(list(ids), ensure_ascii=False, indent=2))

def _uniq_key(obj):
    return obj.get("external_id") or str(obj.get("id"))

# ====== FUNCIONES DE FORMATO ======
def _fmt_fecha_dd_mm_yyyy(created_at_iso: str) -> str:
    s = (created_at_iso or "").strip().replace("Z", "")
    if "+" in s: s = s.split("+")[0]
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try: return datetime.strptime(s[:19], fmt).strftime("%d/%m/%Y")
        except Exception: continue
    return datetime.now().strftime("%d/%m/%Y")

def _ensure_numero(street_number: str, street: str) -> str:
    num = (street_number or "").strip()
    if not num:
        m = re.search(r"\b(\d+)\b", (street or ""))
        if m: num = m.group(1)
    return str(num) if num else "1"

def _ensure_email(email: str, idenvio: str) -> str:
    e = (email or "").strip()
    if e: return e
    base = re.sub(r"[^a-zA-Z0-9._-]", "", str(idenvio)) or "envio"
    return f"{base.lower()}@example.local"

def map_zipnova_to_lightdata(detail: dict):
    dest = detail.get("destination", {}) or {}
    idenvio = detail.get("external_id") or f"zipnova-{detail.get('id')}"
    nombre_cliente = (dest.get("name") or "").strip()
    nombre_cliente = (nombre_cliente + " AP") if nombre_cliente else "AP"
    
    return {
        "idenvio": str(idenvio), "email": _ensure_email(dest.get("email"), idenvio), "destinatario": nombre_cliente,
        "telefono": str(dest.get("phone") or ""), "calle": str(dest.get("street") or ""), "numero": _ensure_numero(dest.get("street_number"), dest.get("street")),
        "floor": str(dest.get("street_extras") or ""), "localidad": str(dest.get("city") or ""), "cp": str(dest.get("zipcode") or ""),
        "provincia": str(dest.get("state") or ""), "delivery_preference": "R", "shipment_id": str(detail.get("id")),
        "fechaVenta": _fmt_fecha_dd_mm_yyyy(detail.get("created_at")), "peso": str(detail.get("total_weight") or ""),
        "valor_declarado": str(detail.get("declared_value") or ""), "obs": "", "destination_comments": "", "latitud": "", "longitud": "", "logistica_inversa": "", "total_a_cobrar": ""
    }

# ====== FUNCIONES DE CONEXIÓN APIS ======
def list_ready_to_ship():
    url = f"{ZIPNOVA_DOMAIN}/v2/shipments"
    params = {"account_id": ZIPNOVA_ACCOUNT_ID, "status": "ready_to_ship", "per_page": 50}
    r = requests.get(url, params=params, auth=HTTPBasicAuth(ZIPNOVA_KEY, ZIPNOVA_SECRET), timeout=30)
    r.raise_for_status()
    return r.json().get("data", [])

def get_detail(shipment_id: int):
    url = f"{ZIPNOVA_DOMAIN}/v2/shipments/{shipment_id}"
    r = requests.get(url, auth=HTTPBasicAuth(ZIPNOVA_KEY, ZIPNOVA_SECRET), timeout=30)
    return r.json()

def update_zipnova_status_in_transit(shipment_id: int):
    url = f"{ZIPNOVA_DOMAIN}/v2/shipments/{shipment_id}/tracking"
    payload = {"status": "in_transit", "comment": "Despachado via App Web Local"}
    r = requests.post(url, auth=HTTPBasicAuth(ZIPNOVA_KEY, ZIPNOVA_SECRET), json=payload, timeout=30)
    return r.ok

def lightdata_insert(entries, instance: dict):
    files = {"tk": (None, instance["token"]), "ac": (None, "insert"), "data": (None, json.dumps(entries, ensure_ascii=False))}
    r = requests.post(instance["url"], files=files, timeout=60)
    return r.json()

def _is_ok(response, idx):
    try: r = response[idx] if isinstance(response, list) else response
    except Exception: r = {}
    return str(r.get("estado", "")).lower() in ["true", "1"]

def list_in_transit(max_pages=10, per_page=50) -> list:
    url = f"{ZIPNOVA_DOMAIN}/v2/shipments"
    out = []
    ids_vistos = set()
    for status_val in STATUS_CANDIDATES:
        params = {"account_id": ZIPNOVA_ACCOUNT_ID, "status": status_val, "per_page": per_page}
        try:
            for page in range(1, max_pages + 1):
                params["page"] = page
                r = requests.get(url, params=params, auth=HTTPBasicAuth(ZIPNOVA_KEY, ZIPNOVA_SECRET), timeout=30)
                if not r.ok: break
                j = r.json()
                for s in j.get("data", []):
                    estado_actual = str(s.get("status") or s.get("state") or "").strip().lower()
                    # Agregué que también ignore los que ya están en no entregado
                    if estado_actual in ["delivered", "entregado", "not_delivered", "no entregado"]: continue
                    
                    carrier_id = str(s.get("carrier_id") or s.get("carrier", {}).get("id") or "")
                    carrier_nom = str(s.get("carrier", {}).get("name") or s.get("carrier_name") or "").strip().lower()
                    sid = str(s.get("id") or "")
                    
                    if (carrier_id == str(OWN_FLEET_CARRIER_ID) or "flete" in carrier_nom or "propio" in carrier_nom) and sid not in ids_vistos:
                        out.append(s)
                        ids_vistos.add(sid)
                if not j.get("links", {}).get("next"): break
            if out: break
        except Exception: pass
    return out

def update_zipnova_delivered(shipment_id, comment="Entregado (Cruce con Excel LightData)") -> bool:
    url = f"{ZIPNOVA_DOMAIN}/v2/shipments/{shipment_id}/tracking"
    payload = {"status": "delivered", "comment": comment}
    try: 
        return requests.post(url, auth=HTTPBasicAuth(ZIPNOVA_KEY, ZIPNOVA_SECRET), json=payload, timeout=15).ok
    except Exception: 
        return False

def update_zipnova_not_delivered(shipment_id, comment="No entregado (Cruce con Excel LightData)") -> bool:
    url = f"{ZIPNOVA_DOMAIN}/v2/shipments/{shipment_id}/tracking"
    # Nota: Si en Zipnova en vez de 'not_delivered' utilizan otra palabra clave en su sistema, cámbialo en la línea de abajo.
    payload = {"status": "not_delivered", "comment": comment}
    try: 
        return requests.post(url, auth=HTTPBasicAuth(ZIPNOVA_KEY, ZIPNOVA_SECRET), json=payload, timeout=15).ok
    except Exception: 
        return False


# ====== INTERFAZ GRÁFICA ======
st.title("🚚 FLETE PROPIO")
st.subheader("Zipnova, Snowflex y Flexit")

if "pedidos" not in st.session_state:
    st.session_state.pedidos = []

col1, col2 = st.columns(2)

# --- COLUMNA 1: CREAR ETIQUETAS ---
with col1:
    st.markdown("### 📦 1. Crear Etiquetas")
    
    if st.button("🔎 Buscar Pedidos Pendientes", use_container_width=True):
        with st.spinner("Buscando pedidos en Zipnova..."):
            dispatched = _load_memory(FILE_DISPATCHED)
            try:
                base = list_ready_to_ship()
                validos = []
                for s in base:
                    if _uniq_key(s) in dispatched: continue
                    det = get_detail(s["id"])
                    carrier = det.get("carrier") or {}
                    if str(carrier.get("id")) == str(OWN_FLEET_CARRIER_ID):
                        validos.append(det)
                
                st.session_state.pedidos = validos
                if validos:
                    st.success(f"¡Se encontraron {len(validos)} paquetes listos para Flete Propio!")
                else:
                    st.info("No hay pedidos nuevos pendientes de flete propio en Zipnova.")
            except Exception as e:
                st.error(f"Error de conexión: {e}")

    if st.button("🔍 Crear etiquetas y despachar", type="primary", use_container_width=True):
        if not st.session_state.pedidos:
            st.warning("⚠️ Primero debes hacer clic en 'Buscar Pedidos Pendientes'.")
        else:
            with st.spinner("Procesando altas en Snowflex y Flexit..."):
                dispatched = _load_memory(FILE_DISPATCHED)
                entries = [map_zipnova_to_lightdata(d) for d in st.session_state.pedidos]
                
                respuestas = {}
                for inst in LIGHTDATA_INSTANCES:
                    try:
                        respuestas[inst["name"]] = lightdata_insert(entries, inst)
                        st.write(f"🔹 Conexión exitosa con {inst['name'].upper()}.")
                    except Exception as e:
                        respuestas[inst["name"]] = None
                        st.error(f"Error crítico en {inst['name']}: {e}")

                ok_count = 0
                for idx, d in enumerate(st.session_state.pedidos):
                    envio_id = d["id"]
                    ok_snow = _is_ok(respuestas.get("snowflex"), idx)
                    ok_flex = _is_ok(respuestas.get("flexit"), idx)

                    if ok_snow and ok_flex:
                        if update_zipnova_status_in_transit(envio_id):
                            dispatched.add(_uniq_key(d))
                            ok_count += 1
                            st.write(f"✅ Pedido #{envio_id} pasado a 'En Camino'.")
                    else:
                        st.warning(f"⚠️ Pedido #{envio_id} omitido (Falló alta en LightData).")
                
                _save_memory(FILE_DISPATCHED, dispatched)
                st.success(f"🎉 ¡Proceso terminado! {ok_count} pedidos sincronizados completamente.")
                st.session_state.pedidos = []

# --- COLUMNA 2: ACTUALIZAR ENTREGADOS Y NO ENTREGADOS ---
with col2:
    st.markdown("### 📥 2. Actualizar Envíos")
    
    btn_actualizar = st.button("✔️ Actualizar Estados (Excel)", use_container_width=True)
    archivo_datos = st.file_uploader("Sube tu Excel de LightData aquí", type=["xls", "xlsx"])
    
    if btn_actualizar:
        if archivo_datos is None:
            st.warning("⚠️ Por favor, sube el archivo Excel en el recuadro de abajo antes de presionar el botón.")
        else:
            with st.spinner("Descargando paquetes 'En camino' y cruzando datos..."):
                try:
                    # Aumentamos a 20 páginas (1000 envíos) para que no se escape ninguno
                    envios_zipnova = list_in_transit(max_pages=20, per_page=50)
                    if not envios_zipnova:
                        st.info("No hay envíos en camino en Zipnova para revisar.")
                    else:
                        # Leer como 'dtype=str' evita que los IDs de ML se rompan en notación científica
                        df = pd.read_excel(archivo_datos, dtype=str)
                        df['Estado_Limpio'] = df['Estado'].astype(str).str.lower().str.strip()
                        
                        # FILTROS: Separamos "no entregado" de "entregado"
                        mask_no_entregado = df['Estado_Limpio'].str.contains('no entregado', case=False, na=False)
                        mask_entregado = df['Estado_Limpio'].str.contains('entregado', case=False, na=False) & ~mask_no_entregado
                        
                        df_entregados = df[mask_entregado]
                        df_no_entregados = df[mask_no_entregado]
                        
                        # Función auxiliar para extraer IDs
                        def obtener_ids(dataframe):
                            ids = set()
                            if 'Número Tracking' in dataframe.columns:
                                ids.update(dataframe['Número Tracking'].dropna().astype(str).str.strip().str.lower().tolist())
                            col_ml = next((c for c in dataframe.columns if 'id' in c.lower() and 'venta' in c.lower() and 'ml' in c.lower()), 'ID venta ML')
                            if col_ml in dataframe.columns:
                                lista_ml = dataframe[col_ml].dropna().astype(str).str.replace(r'\.0$', '', regex=True).str.strip().str.lower().tolist()
                                ids.update(lista_ml)
                            return ids

                        ids_entregados = obtener_ids(df_entregados)
                        ids_no_entregados = obtener_ids(df_no_entregados)
                        
                        if not ids_entregados and not ids_no_entregados:
                            st.error("❌ No se encontró la columna 'Número Tracking' ni 'ID venta ML' en tu Excel.")
                        else:
                            ok_count_entregados = 0
                            ok_count_no_entregados = 0
                            
                            for envio in envios_zipnova:
                                zipnova_id = str(envio.get("id")).strip().lower()
                                candidatos = {
                                    zipnova_id,
                                    f"zipnova-{zipnova_id}",
                                    str(envio.get("external_id") or "").strip().lower(),
                                    str(envio.get("external_reference") or "").strip().lower(),
                                    str(envio.get("order_number") or "").strip().lower()
                                }
                                candidatos.discard("")
                                    
                                match_entregado = any(c in ids_entregados for c in candidatos)
                                match_no_entregado = any(c in ids_no_entregados for c in candidatos)
                                        
                                if match_entregado:
                                    if update_zipnova_delivered(envio["id"]):
                                        ok_count_entregados += 1
                                        st.write(f"✅ Pedido #{envio['id']} marcado como Entregado.")
                                    else:
                                        st.warning(f"⚠️ Falló la actualización (Entregado) para #{envio['id']}.")
                                
                                elif match_no_entregado:
                                    if update_zipnova_not_delivered(envio["id"]):
                                        ok_count_no_entregados += 1
                                        st.write(f"❌ Pedido #{envio['id']} marcado como No Entregado.")
                                    else:
                                        st.warning(f"⚠️ Falló la actualización (No Entregado) para #{envio['id']}.")
                                        
                            if ok_count_entregados > 0 or ok_count_no_entregados > 0:
                                st.success(f"🎉 ¡TAREA FINALIZADA! Se actualizaron {ok_count_entregados} a 'Entregado' y {ok_count_no_entregados} a 'No Entregado'.")
                            else:
                                st.info("👍 No se encontraron coincidencias nuevas para actualizar.")
                                
                except Exception as e:
                    st.error(f"❌ Error procesando el archivo: {e}")

# --- TABLA DE VISTA PREVIA ---
if st.session_state.pedidos:
    st.write("---")
    st.write("### 📋 Vista previa de paquetes listos para despachar:")
    datos_tabla = []
    for p in st.session_state.pedidos:
        datos_tabla.append({
            "ID Zipnova": p.get("id"),
            "Cliente": p.get("destination", {}).get("name"),
            "Localidad": p.get("destination", {}).get("city"),
            "Fecha": _fmt_fecha_dd_mm_yyyy(p.get("created_at"))
        })
    st.table(pd.DataFrame(datos_tabla))
