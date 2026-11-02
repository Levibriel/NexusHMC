import unicodedata
from datetime import datetime, time, timedelta
from app.core.database import get_db_connection

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

    query_optimizada = """
    WITH horas_turno AS (
        SELECT generate_series(0, 11) AS h_idx
    ),
    bloques AS (
        SELECT 
            h_idx,
            %s + (h_idx * INTERVAL '1 hour') AS h_inicio,
            %s + ((h_idx + 1) * INTERVAL '1 hour') AS h_fin
        FROM horas_turno
    ),
    tonelaje_hora AS (
        SELECT 
            b.h_idx,
            COALESCE(SUM(l.fact_float_tons_nominal), 0.0) AS ton_real
        FROM bloques b
        LEFT JOIN public.timemanager m 
            ON m.fact_datetime_start_time_traveling_empty >= b.h_inicio 
           AND m.fact_datetime_start_time_traveling_empty < b.h_fin
        LEFT JOIN public.cyclehauling c ON c.id = m.id_relational
        LEFT JOIN public.cycleloading l ON c.id = l.fk_cycle_hauling
        GROUP BY b.h_idx
    ),
    eventos_turno AS (
        SELECT 
            b.h_idx,
            REPLACE(UPPER(TRIM(s.dim_name_equipment)), '-', '') AS eq,
            UPPER(TRIM(COALESCE(s.dim_name_main_status, ''))) AS cat,
            ROW_NUMBER() OVER (
                PARTITION BY b.h_idx, REPLACE(UPPER(TRIM(s.dim_name_equipment)), '-', '')
                ORDER BY s.fact_datetime_start_date DESC
            ) as rn
        FROM bloques b
        JOIN public.status s 
            ON s.fact_datetime_start_date < b.h_fin
           AND (s.fact_datetime_end_date > b.h_inicio OR s.fact_datetime_end_date IS NULL)
        WHERE REPLACE(UPPER(TRIM(s.dim_name_equipment)), '-', '') IN %s
          AND s.dim_name_equipment <> 'RX181'
    ),
    estados_hora AS (
        SELECT 
            h_idx,
            COUNT(DISTINCT eq) FILTER (
                WHERE cat NOT ILIKE '%%MANTEN%%' AND cat NOT ILIKE '%%FUERA%%'
            ) AS caex_disponibles,
            COUNT(DISTINCT eq) FILTER (
                WHERE cat ILIKE '%%OPERAT%%' OR cat ILIKE '%%OPERAC%%'
            ) AS caex_operativos
        FROM eventos_turno
        WHERE rn = 1
        GROUP BY h_idx
    )
    SELECT 
        b.h_idx + 1 AS hora_operacional,
        TO_CHAR(b.h_inicio, 'HH24:MI') || ' - ' || TO_CHAR(b.h_fin, 'HH24:MI') AS tramo,
        b.h_inicio,
        COALESCE(eh.caex_disponibles, 0) AS caex_disponibles,
        COALESCE(eh.caex_operativos, 0) AS caex_operativos,
        ROUND(COALESCE(th.ton_real, 0.0)::numeric, 1) AS tonelaje
    FROM bloques b
    LEFT JOIN tonelaje_hora th ON th.h_idx = b.h_idx
    LEFT JOIN estados_hora eh ON eh.h_idx = b.h_idx
    ORDER BY b.h_idx;
    """

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

    query_timeline_gantt = """
    WITH raw_events AS (
        SELECT 
            REPLACE(UPPER(TRIM(s.dim_name_equipment)), '-', '') AS eq,
            UPPER(TRIM(COALESCE(s.dim_name_main_status, 'OPERACIONAL'))) AS categoria_raw,
            COALESCE(s.dim_name_detail_status, 'OPERACION NORMAL') AS motivo,
            GREATEST(s.fact_datetime_start_date, %s) AS inicio_efectivo,
            LEAST(COALESCE(s.fact_datetime_end_date, %s), %s) AS fin_efectivo
        FROM public.status s
        WHERE REPLACE(UPPER(TRIM(s.dim_name_equipment)), '-', '') IN %s
          AND s.dim_name_equipment <> 'RX181'
          AND s.fact_datetime_start_date < %s
          AND (s.fact_datetime_end_date > %s OR s.fact_datetime_end_date IS NULL)
    )
    SELECT 
        eq,
        categoria_raw,
        motivo,
        inicio_efectivo,
        fin_efectivo,
        ROUND((EXTRACT(EPOCH FROM (inicio_efectivo - %s)) / 3600.0)::numeric, 3) AS start_h,
        ROUND((EXTRACT(EPOCH FROM (fin_efectivo - %s)) / 3600.0)::numeric, 3) AS end_h,
        ROUND((EXTRACT(EPOCH FROM (fin_efectivo - inicio_efectivo)) / 3600.0)::numeric, 3) AS duracion_hrs
    FROM raw_events
    WHERE fin_efectivo > inicio_efectivo
    ORDER BY eq, inicio_efectivo ASC;
    """

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

    query = """
    WITH ultimos_estados AS (
        SELECT 
            REPLACE(UPPER(TRIM(s.dim_name_equipment)), '-', '') AS eq_clean,
            COALESCE(s.dim_name_main_status, '') AS categoria_asarco,
            COALESCE(s.dim_name_detail_status, s.dim_name_main_status, 'FALLA NO ESPECIFICADA') AS razon_falla,
            ROW_NUMBER() OVER (
                PARTITION BY REPLACE(UPPER(TRIM(s.dim_name_equipment)), '-', '') 
                ORDER BY s.fact_datetime_start_date DESC
            ) AS rn
        FROM public.status s
        WHERE s.fact_datetime_start_date <= %s
          AND s.dim_name_equipment <> 'RX181'
    )
    SELECT eq_clean, categoria_asarco, razon_falla
    FROM ultimos_estados
    WHERE rn = 1;
    """

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