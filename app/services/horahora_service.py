import pandas as pd
import os
from app.core.database import get_db_connection
from app.queries.loader import get_query
from typing import Any, cast
import math
from psycopg2.extras import RealDictCursor
import unicodedata
from datetime import datetime, time, timedelta


PARQUET_PATH = os.path.join("data", "planes_raw.parquet")
PARQUET_FLOTA_PATH = os.path.join("data", "plan_kpi_equipos.parquet")

# Factores de la Regla Gaviota para distribución horaria de 12 horas
F1 = 0.85
F_REL = 0.80
F12 = 0.90
F_PROD = 1.00 + (0.85 / 7.0)

FACTORES_HORARIOS = [
    F1,
    F_PROD,
    F_PROD,
    F_PROD,
    F_PROD,
    F_REL,
    F_REL,
    F_REL,
    F_PROD,
    F_PROD,
    F_PROD,
    F12,
]

# Flota nominal presupuestada oficial Michilla (12 Cat 775 + 2 Cat 777 = 14 CAEX)
FLOTA_CAEX = {
    'T11', 'T12', 'T13', 'T15', 'T16', 'T17', 'T18', 'T19', 'T20', 'T21', 'T23', 'T24',
    'T83', 'T86'
}
# 5 Cargadores Frontales
FLOTA_CARGADORES = {'L02', 'L03', 'L04', 'L05', 'L06'}


def normalizar_texto(texto: str) -> str:
    """Elimina acentos y espacios, pasando a mayúsculas."""
    if not texto:
        return ""
    texto_norm = unicodedata.normalize('NFKD', texto)
    return ''.join(c for c in texto_norm if not unicodedata.combining(c)).strip().upper()

def consultar_acumulados_semanames(fecha_str: str) -> dict:
    """Servicio principal para alimentar la Tarjeta 4."""
    plan_info = obtener_plan_semana_y_mes(fecha_str)
    if not plan_info["periodo"]:
        return {
            "semana": "SEM1",
            "ton_real_semana": 0, "ton_plan_semana": 0,
            "ton_real_mes": 0, "ton_plan_mes": 0,
            "cumpl_mes": 0
        }
    
    real_semana = fetch_real_tonnage_range(plan_info["fecha_min_semana"], fecha_str)
    real_mes = fetch_real_tonnage_range(plan_info["fecha_min_mes"], fecha_str)
    
    cumpl_mes = int(round((real_mes / plan_info["ton_plan_mes"]) * 100)) if plan_info["ton_plan_mes"] > 0 else 0
    
    return {
        "semana": plan_info["semana"],
        "ton_real_semana": int(round(real_semana)),
        "ton_plan_semana": int(round(plan_info["ton_plan_semana"])),
        "ton_real_mes": int(round(real_mes)),
        "ton_plan_mes": int(round(plan_info["ton_plan_mes"])),
        "cumpl_mes": cumpl_mes
    }

def obtener_plan_turno(fecha: str, turno: str = "A") -> dict:
    """Función consumida por _evaluar_bloque_turno
    Calcula el perfil horario con la Regla Gaviota y retorna las metas del turno.
    """
    if not os.path.exists(PARQUET_PATH):
        raise FileNotFoundError(
            f"Repositorio histórico no encontrado en {PARQUET_PATH}"
        )

    df = pd.read_parquet(PARQUET_PATH, filters=[("fecha", "==", fecha)])
    if df.empty:
        raise ValueError(
            f"No existe planificación cargada para la fecha {fecha}"
        )

    fila = df.iloc[0]
    ton_dia = float(fila["ton_plan_dia"])
    ton_turno = round(ton_dia / 2.0, 2)

    # Extracción segura de remanejo y minado del plan
    ton_remanejo_dia = (
        float(fila.get("ton_remanejo_dia", 0.0))
        if "ton_remanejo_dia" in fila
        else 0.0
    )
    ton_remanejo_turno = round(ton_remanejo_dia / 2.0, 2)
    ton_minado_turno = max(0.0, round(ton_turno - ton_remanejo_turno, 2))

    t_base = ton_turno / 12.0
    vector_horas = [round(t_base * f, 2) for f in FACTORES_HORARIOS]

    # Cuadratura de redondeo sobre la última hora
    diferencia = round(ton_turno - sum(vector_horas), 2)
    vector_horas[-1] = round(vector_horas[-1] + diferencia, 2)

    return {
        "periodo": str(fila["periodo"]),
        "fecha": str(fila["fecha"]),
        "semana": str(fila["semana"]),
        "fase": str(fila["fase"]),
        "ton_plan_dia": ton_dia,
        "turno": turno,
        "ton_plan_turno": ton_turno,
        "ton_remanejo_plan_dia": ton_remanejo_dia,
        "ton_remanejo_plan_turno": ton_remanejo_turno,
        "ton_minado_plan_turno": ton_minado_turno,
        "t_base": round(t_base, 2),
        "perfil_horario": vector_horas,
    }

def obtener_plan_flota(
    fecha_str: str, turno_str: str
) -> dict[str, dict[str, float]]:
    """Función consumida por sic_flota_service.py.
    Aplica factor 0.5 (12h) para turnos 'A' o 'B' y 1.0 (24h) para 'A+B' o 'BOTH'.
    Si df_plan_pct == 0.0 o utbd_plan_pct == 0.0, ton_plan se fuerza a 0.0 t.
    """
    if not os.path.exists(PARQUET_FLOTA_PATH):
        return {}

    df_plan = pd.read_parquet(PARQUET_FLOTA_PATH)
    df_fecha = df_plan[df_plan["fecha"].astype(str) == fecha_str]

    if df_fecha.empty:
        return {}

    factor_turno = 0.5 if str(turno_str).upper() in ["A", "B"] else 1.0

    plan_dict = {}
    for _, row in df_fecha.iterrows():
        eq_id = str(row["equipo_id"]).replace("-", "").upper()

        hrs_nom = float(row.get("hrs_nom_plan", 24.0)) * factor_turno
        hrs_disp = float(row.get("hrs_disp_plan", 0.0)) * factor_turno
        hrs_op = float(row.get("hrs_op_plan", 0.0)) * factor_turno
        rend_plan = float(row.get("rend_plan", 0.0))

        raw_df = float(row.get("df_plan", 0.0))
        raw_utbd = float(row.get("utbd_plan", 0.0))

        df_pct = round(raw_df * 100.0 if raw_df <= 1.0 else raw_df, 1)
        utbd_pct = round(raw_utbd * 100.0 if raw_utbd <= 1.0 else raw_utbd, 1)

        if df_pct == 0.0 or utbd_pct == 0.0 or hrs_op == 0.0:
            ton_plan = 0.0
        else:
            ton_plan = round(float(row.get("ton_plan", 0.0)) * factor_turno, 1)

        plan_dict[eq_id] = {
            "hrs_nom_plan": hrs_nom,
            "df_plan_pct": df_pct,
            "hrs_disp_plan": hrs_disp,
            "utbd_plan_pct": utbd_pct,
            "hrs_op_plan": hrs_op,
            "rend_plan": rend_plan,
            "ton_plan": ton_plan,
        }

    return plan_dict

def obtener_plan_semana_y_mes(fecha_str: str) -> dict:
    """Extrae las metas acumuladas de semana y mes desde planes_raw.parquet."""
    if not os.path.exists(PARQUET_PATH):
        return {"ton_plan_semana": 0.0, "ton_plan_mes": 0.0, "semana": "SEM1", "periodo": ""}
    
    df = pd.read_parquet(PARQUET_PATH)
    df_fecha = df[df["fecha"].astype(str) == fecha_str]
    if df_fecha.empty:
        return {"ton_plan_semana": 0.0, "ton_plan_mes": 0.0, "semana": "SEM1", "periodo": ""}
    
    fila = df_fecha.iloc[0]
    periodo_val = str(fila["periodo"])
    semana_val = str(fila["semana"])
    
    df_periodo = df[df["periodo"].astype(str) == periodo_val]
    ton_plan_mes = float(df_periodo["ton_plan_dia"].sum())
    
    df_semana = df_periodo[df_periodo["semana"].astype(str) == semana_val]
    ton_plan_semana = float(df_semana["ton_plan_dia"].sum())
    
    fecha_min_semana = str(df_semana["fecha"].min())
    fecha_min_mes = str(df_periodo["fecha"].min())
    
    return {
        "periodo": periodo_val,
        "semana": semana_val,
        "ton_plan_semana": round(ton_plan_semana, 1),
        "ton_plan_mes": round(ton_plan_mes, 1),
        "fecha_min_semana": fecha_min_semana,
        "fecha_min_mes": fecha_min_mes,
    }

def fetch_hourly_tonnage(target_date: str, target_shift: str):
    query = get_query("tonelaje_hora")
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(query, (target_date, target_shift))
            return cur.fetchall()

def fetch_real_tonnage_range(fecha_inicio: str, fecha_fin: str) -> float:
    query = get_query("tonelaje_periodo")
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(query, (fecha_inicio, fecha_fin))
            row = cur.fetchone()
            return float(row[0]) if row else 0.0

def safe_float(val: Any, default: float = 0.0) -> float:
    """Convierte cualquier valor numérico a float de manera segura,

    reemplazando None, NaN e Inf por default.
    """
    if val is None:
        return default
    try:
        f = float(val)
        if math.isnan(f) or math.isinf(f):
            return default
        return f
    except (ValueError, TypeError):
        return default

def consultar_carguio_sic(
    fecha_str: str, turno_str: str
) -> list[dict[str, Any]]:
    """Consulta los datos reales de Carguío (L02, L03, L04, L05, L06) desde

    PostgreSQL y los cruza con las metas Parquet.
    """
    query = get_query("cic_carguio")

    plan_dict = obtener_plan_flota(fecha_str, turno_str)
    real_dict = {}

    try:
        with get_db_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(query, (fecha_str, fecha_str, turno_str))
                for f in cur.fetchall():
                    real_dict[str(f["equipo"])] = f
    except Exception as e:
        print(f"Error al consultar PostgreSQL Carguío: {e}")

    equipos_carguio_keys = ["L02", "L03", "L04", "L05", "L06"]

    # Metas planificadas acumuladas para el total
    ton_plan_total_carguio = sum(
        plan_dict.get(k, {}).get("ton_plan", 0.0) for k in equipos_carguio_keys
    )
    df_plan_prom_carguio = (
        sum(
            plan_dict.get(k, {}).get("df_plan_pct", 0.0)
            for k in equipos_carguio_keys
        )
        / len(equipos_carguio_keys)
        if equipos_carguio_keys
        else 0.0
    )
    utbd_plan_prom_carguio = (
        sum(
            plan_dict.get(k, {}).get("utbd_plan_pct", 0.0)
            for k in equipos_carguio_keys
        )
        / len(equipos_carguio_keys)
        if equipos_carguio_keys
        else 0.0
    )
    rend_plan_prom_carguio = (
        sum(
            plan_dict.get(k, {}).get("rend_plan", 0.0)
            for k in equipos_carguio_keys
        )
        / len(equipos_carguio_keys)
        if equipos_carguio_keys
        else 0.0
    )

    resultado: list[dict[str, Any]] = []

    # Mapear cada equipo individual
    tot_hrs_nom = 0.0
    tot_hrs_disp = 0.0
    tot_hrs_op = 0.0
    tot_hrs_ef = 0.0
    tot_hrs_res = 0.0
    tot_ton_real = 0.0

    for eq in equipos_carguio_keys:
        f = real_dict.get(eq, {})
        p = plan_dict.get(eq, {})

        hrs_nom = safe_float(f.get("hrs_nom", 12.0))
        hrs_disp = safe_float(f.get("hrs_disp", 0.0))
        hrs_op = safe_float(f.get("hrs_op", 0.0))
        hrs_ef = safe_float(f.get("hrs_ef", 0.0))
        hrs_res = safe_float(f.get("hrs_reserva", 0.0))
        ton_real = safe_float(f.get("ton_real", 0.0))

        tot_hrs_nom += hrs_nom
        tot_hrs_disp += hrs_disp
        tot_hrs_op += hrs_op
        tot_hrs_ef += hrs_ef
        tot_hrs_res += hrs_res
        tot_ton_real += ton_real

        df_real_pct = safe_float(
            f.get(
                "df_real_pct",
                round((hrs_disp / hrs_nom * 100.0), 1) if hrs_nom > 0 else 0.0,
            )
        )
        utbd_real_pct = safe_float(
            f.get(
                "utbd_real_pct",
                round((hrs_ef / hrs_disp * 100.0), 1) if hrs_disp > 0 else 0.0,
            )
        )
        rend_real = safe_float(
            f.get(
                "rend_efectivo_real",
                round((ton_real / hrs_ef), 1) if hrs_ef > 0 else 0.0,
            )
        )

        ton_plan = safe_float(p.get("ton_plan", 0.0))
        df_plan = safe_float(p.get("df_plan_pct", 0.0))
        utbd_plan = safe_float(p.get("utbd_plan_pct", 0.0))
        rend_plan = safe_float(p.get("rend_plan", 0.0))

        if ton_plan > 0:
            cumpl_pct = round((ton_real / ton_plan * 100.0), 1)
        else:
            cumpl_pct = 100.0 if ton_real > 0 else 0.0

        desvio_ton = round(ton_real - ton_plan, 1)

        resultado.append({
            "equipo": eq,
            "hrs_nom": hrs_nom,
            "hrs_disp_real": hrs_disp,
            "hrs_op_real": hrs_op,
            "hrs_ef_real": hrs_ef,
            "hrs_reserva_real": hrs_res,
            "df_real_pct": df_real_pct,
            "df_plan_pct": df_plan,
            "utbd_real_pct": utbd_real_pct,
            "utbd_plan_pct": utbd_plan,
            "rend_real": rend_real,
            "rend_plan": rend_plan,
            "ton_real": ton_real,
            "ton_plan": ton_plan,
            "cumpl_pct": cumpl_pct,
            "desvio_ton": desvio_ton,
        })

    # Fila final de TOTAL CARGUIO
    tot_f = real_dict.get("TOTAL CARGUIO", {})
    df_tot_real = safe_float(
        tot_f.get(
            "df_real_pct",
            round((tot_hrs_disp / tot_hrs_nom * 100.0), 1) if tot_hrs_nom > 0 else 0.0,
        )
    )
    utbd_tot_real = safe_float(
        tot_f.get(
            "utbd_real_pct",
            round((tot_hrs_ef / tot_hrs_disp * 100.0), 1) if tot_hrs_disp > 0 else 0.0,
        )
    )
    rend_tot_real = safe_float(
        tot_f.get(
            "rend_efectivo_real",
            round((tot_ton_real / tot_hrs_ef), 1) if tot_hrs_ef > 0 else 0.0,
        )
    )

    if ton_plan_total_carguio > 0:
        cumpl_tot_pct = round((tot_ton_real / ton_plan_total_carguio * 100.0), 1)
    else:
        cumpl_tot_pct = 100.0 if tot_ton_real > 0 else 0.0

    desvio_tot_ton = round(tot_ton_real - ton_plan_total_carguio, 1)

    resultado.append({
        "equipo": "TOTAL CARGUIO",
        "hrs_nom": tot_hrs_nom,
        "hrs_disp_real": tot_hrs_disp,
        "hrs_op_real": tot_hrs_op,
        "hrs_ef_real": tot_hrs_ef,
        "hrs_reserva_real": tot_hrs_res,
        "df_real_pct": df_tot_real,
        "df_plan_pct": round(df_plan_prom_carguio, 1),
        "utbd_real_pct": utbd_tot_real,
        "utbd_plan_pct": round(utbd_plan_prom_carguio, 1),
        "rend_real": rend_tot_real,
        "rend_plan": round(rend_plan_prom_carguio, 1),
        "ton_real": tot_ton_real,
        "ton_plan": round(ton_plan_total_carguio, 1),
        "cumpl_pct": cumpl_tot_pct,
        "desvio_ton": desvio_tot_ton,
    })

    return resultado

def consultar_transporte_sic(
    fecha_str: str, turno_str: str
) -> list[dict[str, Any]]:
    """Consulta los datos reales de Transporte (14 CAEX nominales) desde

    PostgreSQL y los cruza con las metas Parquet.
    """
    query = get_query("cic_transporte")

    plan_dict = obtener_plan_flota(fecha_str, turno_str)
    real_dict = {}

    try:
        with get_db_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(query, (fecha_str, fecha_str, turno_str))
                for f in cur.fetchall():
                    real_dict[str(f["flota"])] = f
    except Exception as e:
        print(f"Error al consultar PostgreSQL Transporte: {e}")

    caex_775_keys = [
        f"T{i}" for i in range(11, 25) if f"T{i}" not in ["T14", "T22"]
    ]
    caex_777_keys = ["T83", "T86"]
    todos_caex_keys = caex_775_keys + caex_777_keys

    resultado: list[dict[str, Any]] = []
    flotas_lista = ["FLOTA CAT 775", "FLOTA CAT 777", "TOTAL TRANSPORTE"]

    for flota_nombre in flotas_lista:
        f = real_dict.get(flota_nombre, {})

        if flota_nombre == "FLOTA CAT 775":
            caex_keys = caex_775_keys
        elif flota_nombre == "FLOTA CAT 777":
            caex_keys = caex_777_keys
        else:
            caex_keys = todos_caex_keys

        ton_plan_total = sum(
            plan_dict.get(k, {}).get("ton_plan", 0.0) for k in caex_keys
        )
        df_plan_prom = (
            sum(plan_dict.get(k, {}).get("df_plan_pct", 0.0) for k in caex_keys)
            / len(caex_keys)
            if caex_keys
            else 0.0
        )
        utbd_plan_prom = (
            sum(
                plan_dict.get(k, {}).get("utbd_plan_pct", 0.0) for k in caex_keys
            )
            / len(caex_keys)
            if caex_keys
            else 0.0
        )
        rend_plan_prom = (
            sum(plan_dict.get(k, {}).get("rend_plan", 0.0) for k in caex_keys)
            / len(caex_keys)
            if caex_keys
            else 0.0
        )

        hrs_nom = safe_float(f.get("hrs_nom", len(caex_keys) * 12.0))
        hrs_disp = safe_float(f.get("hrs_disp", 0.0))
        hrs_op = safe_float(f.get("hrs_op", 0.0))
        hrs_ef = safe_float(f.get("hrs_ef", 0.0))
        hrs_res = safe_float(f.get("hrs_reserva", 0.0))
        ton_real = safe_float(f.get("ton_real", 0.0))

        df_real_pct = safe_float(
            f.get(
                "df_real_pct",
                round((hrs_disp / hrs_nom * 100.0), 1) if hrs_nom > 0 else 0.0,
            )
        )
        utbd_real_pct = safe_float(
            f.get(
                "utbd_real_pct",
                round((hrs_ef / hrs_disp * 100.0), 1) if hrs_disp > 0 else 0.0,
            )
        )
        rend_real = safe_float(
            f.get(
                "rend_efectivo_real",
                round((ton_real / hrs_ef), 1) if hrs_ef > 0 else 0.0,
            )
        )

        if ton_plan_total > 0:
            cumpl_pct = round((ton_real / ton_plan_total * 100.0), 1)
        else:
            cumpl_pct = 100.0 if ton_real > 0 else 0.0

        desvio_ton = round(ton_real - ton_plan_total, 1)

        resultado.append({
            "flota": flota_nombre,
            "hrs_nom": hrs_nom,
            "hrs_disp_real": hrs_disp,
            "hrs_op_real": hrs_op,
            "hrs_ef_real": hrs_ef,
            "hrs_reserva_real": hrs_res,
            "df_real_pct": df_real_pct,
            "df_plan_pct": round(df_plan_prom, 1),
            "utbd_real_pct": utbd_real_pct,
            "utbd_plan_pct": round(utbd_plan_prom, 1),
            "rend_real": rend_real,
            "rend_plan": round(rend_plan_prom, 1),
            "ton_real": ton_real,
            "ton_plan": round(ton_plan_total, 1),
            "cumpl_pct": cumpl_pct,
            "desvio_ton": desvio_ton,
        })

    return resultado

def fetch_hourly_trucks_vs_tonnage(target_date: str, target_shift: str):
    """
    OPTIMIZADO (Single Query Execution):
    Calcula hora a hora (1 a 12) en una sola pasada a la BD.
    Aplica corte a la hora actual si el turno está en curso:
    - Las horas futuras retornan caex_activos = None y caex_operativos = None,
      haciendo que Chart.js corte limpiamente las líneas sin proyectar hacia el futuro.
    """
    fecha_dt = datetime.strptime(target_date, "%Y-%m-%d").date()
    ahora = datetime.now()

    if target_shift == "A":
        inicio_turno = datetime.combine(fecha_dt, time(8, 0, 0))
        fin_turno = datetime.combine(fecha_dt, time(20, 0, 0))
    elif target_shift == "B":
        inicio_turno = datetime.combine(fecha_dt, time(20, 0, 0))
        fin_turno = datetime.combine(fecha_dt + timedelta(days=1), time(8, 0, 0))
    else:
        inicio_turno = datetime.combine(fecha_dt, time(8, 0, 0))
        fin_turno = datetime.combine(fecha_dt + timedelta(days=1), time(8, 0, 0))

    turno_en_curso = (inicio_turno <= ahora < fin_turno)
    caex_tuple = tuple(FLOTA_CAEX)

    query_optimizada = get_query("caex_vs_tonelaje")

    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(query_optimizada, (inicio_turno, inicio_turno, caex_tuple))
            rows = cur.fetchall()

    res = []
    for r in rows:
        h_op = r[0]
        tramo = r[1]
        h_inicio = r[2]
        caex_disp = int(r[3])
        caex_oper = int(r[4])
        tonelaje = float(r[5])

        if turno_en_curso and h_inicio > ahora:
            disp_val = None
            oper_val = None
            ton_val = None
        else:
            disp_val = caex_disp
            oper_val = caex_oper
            ton_val = tonelaje

        res.append({
            "hora": h_op,
            "tramo": tramo,
            "caex_activos": disp_val,
            "caex_operativos": oper_val,
            "tonelaje": ton_val
        })

    return res

def fetch_asarco_timeline(target_date: str, target_shift: str):
    """
    OPTIMIZADO (Single Query Execution - Gantt Real):
    Retorna la secuencia cronológica de eventos ASARCO por equipo para el turno.
    Cada evento incluye su rango horario relativo [start_h, end_h] (0.0 a 12.0 hrs)
    y su motivo, permitiendo graficar un diagrama de Gantt flotante exacto.
    """
    fecha_dt = datetime.strptime(target_date, "%Y-%m-%d").date()
    ahora = datetime.now()

    if target_shift == "A":
        inicio_turno = datetime.combine(fecha_dt, time(8, 0, 0))
        fin_turno = datetime.combine(fecha_dt, time(20, 0, 0))
    elif target_shift == "B":
        inicio_turno = datetime.combine(fecha_dt, time(20, 0, 0))
        fin_turno = datetime.combine(fecha_dt + timedelta(days=1), time(8, 0, 0))
    else:
        inicio_turno = datetime.combine(fecha_dt, time(8, 0, 0))
        fin_turno = datetime.combine(fecha_dt + timedelta(days=1), time(8, 0, 0))

    tope_evaluacion = min(ahora, fin_turno) if inicio_turno <= ahora < fin_turno else fin_turno
    equipos_ordenados = list(FLOTA_CARGADORES) + list(FLOTA_CAEX)

    query_timeline_gantt = get_query("timeline_status")

    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                query_timeline_gantt,
                (
                    inicio_turno, tope_evaluacion, tope_evaluacion,
                    tuple(equipos_ordenados),
                    tope_evaluacion, inicio_turno,
                    inicio_turno, inicio_turno
                )
            )
            filas = cur.fetchall()

    timeline = {eq: [] for eq in equipos_ordenados}

    for fila in filas:
        eq = fila[0]
        cat_raw = normalizar_texto(fila[1])
        motivo = fila[2]
        inicio_ef = fila[3]
        start_h = float(fila[5])
        end_h = float(fila[6])
        duracion = float(fila[7])

        if duracion <= 0:
            continue

        if "OPERAC" in cat_raw or "OPERAT" in cat_raw:
            categoria_final = "OPERACIONAL"
        elif "DEMOR" in cat_raw:
            categoria_final = "DEMORA"
        elif "MANTEN" in cat_raw:
            categoria_final = "MANTENCION"
        elif "RESERV" in cat_raw:
            categoria_final = "RESERVA"
        else:
            categoria_final = "OPERACIONAL"

        if eq in timeline:
            timeline[eq].append({
                "categoria": categoria_final,
                "motivo": motivo,
                "hora_inicio": inicio_ef.strftime("%H:%M") if hasattr(inicio_ef, "strftime") else str(inicio_ef),
                "start_h": max(0.0, start_h),
                "end_h": min(12.0, end_h),
                "duracion": duracion
            })

    return timeline

def fetch_fleet_status_summary(target_date: str, target_shift: str):

    """
    Retorna disponibilidad oficial de flota.
    Criterio estricto: Un equipo está fuera SOLO si dim_name_main_status = 'MANTENCION' / 'MANTENCIÓN'.
    Descarta 'FUERA DE PLAN'.
    """
    fecha_dt = datetime.strptime(target_date, "%Y-%m-%d").date()
    ahora = datetime.now()

    if target_shift == "A":
        inicio_turno = datetime.combine(fecha_dt, time(8, 0, 0))
        fin_nominal = datetime.combine(fecha_dt, time(20, 0, 0))
    elif target_shift == "B":
        inicio_turno = datetime.combine(fecha_dt, time(20, 0, 0))
        fin_nominal = datetime.combine(fecha_dt + timedelta(days=1), time(8, 0, 0))
    else:  # BOTH / INTER
        inicio_turno = datetime.combine(fecha_dt, time(8, 0, 0))
        fin_nominal = datetime.combine(fecha_dt + timedelta(days=1), time(8, 0, 0))

    punto_evaluacion = min(ahora, fin_nominal) if ahora > inicio_turno else fin_nominal

    query = get_query("status_detail")

    caex_disp = 0
    carg_disp = 0
    fuera_servicio = 0
    equipos_detenidos = []

    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(query, (punto_evaluacion,))
            filas = cur.fetchall()

    estados = {r[0]: (normalizar_texto(r[1]), r[2].strip().upper()) for r in filas}

    # 1. Evaluar Camiones CAEX (14 Unidades)
    for caex in sorted(list(FLOTA_CAEX)):
        categoria_norm, razon = estados.get(caex, ("OPERACIONAL", "OPERATIVO"))
        
        if "FUERA DE PLAN" in categoria_norm:
            continue

        if categoria_norm in ("MANTENCION", "MANTENIMIENTO"):
            fuera_servicio += 1
            equipos_detenidos.append({
                "equipo": caex,
                "razon": razon,
                "tipo": "CAEX"
            })
        else:
            caex_disp += 1

    # 2. Evaluar Cargadores Frontales (5 Unidades)
    for carg in sorted(list(FLOTA_CARGADORES)):
        categoria_norm, razon = estados.get(carg, ("OPERACIONAL", "OPERATIVO"))
        
        if "FUERA DE PLAN" in categoria_norm:
            continue

        if categoria_norm in ("MANTENCION", "MANTENIMIENTO"):
            fuera_servicio += 1
            equipos_detenidos.append({
                "equipo": carg,
                "razon": razon,
                "tipo": "CARGADOR"
            })
        else:
            carg_disp += 1

    return {
        "caex_disponibles": caex_disp,
        "caex_total": len(FLOTA_CAEX),              # 14 fijos
        "cargadores_disponibles": carg_disp,
        "cargadores_total": len(FLOTA_CARGADORES),  # 5 fijos
        "fuera_servicio": fuera_servicio,
        "equipos_detenidos": equipos_detenidos
    }