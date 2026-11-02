from datetime import datetime
import math
import os
from typing import Any, cast
from app.services.plan_service import obtener_plan_flota
from app.core.database import get_db_connection
import pandas as pd
from psycopg2.extras import RealDictCursor

PARQUET_FLOTA_PATH = os.path.join("data", "plan_kpi_equipos.parquet")


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
    query = """
    WITH parametros AS (
        SELECT 
            %s::date AS p_fecha_inicio,
            %s::date AS p_fecha_fin,
            %s::text AS p_turno_input
    ),
    parametros_evaluados AS (
        SELECT 
            p_fecha_inicio,
            p_fecha_fin,
            CASE 
                WHEN p_fecha_fin > p_fecha_inicio THEN 'A+B'
                WHEN UPPER(p_turno_input) IN ('BOTH', 'A+B') THEN 'A+B'
                WHEN UPPER(p_turno_input) = 'INTER' THEN 'INTER'
                ELSE UPPER(p_turno_input) 
            END AS p_turno_real
        FROM parametros
    ),
    ventana_base AS (
        SELECT 
            CASE 
                WHEN p_turno_real = 'A'               THEN p_fecha_inicio + TIME '08:00:00'
                WHEN p_turno_real = 'B'               THEN p_fecha_inicio + TIME '20:00:00'
                WHEN p_turno_real IN ('A+B', 'INTER') THEN p_fecha_inicio + TIME '08:00:00'
            END AS f_ini_nom,
            CASE 
                WHEN p_turno_real = 'A'               THEN p_fecha_fin + TIME '20:00:00'
                WHEN p_turno_real = 'B'               THEN (p_fecha_fin + INTERVAL '1 day') + TIME '08:00:00'
                WHEN p_turno_real IN ('A+B', 'INTER') THEN (p_fecha_fin + INTERVAL '1 day') + TIME '08:00:00'
            END AS f_fin_nom
        FROM parametros_evaluados
    ),
    ventana AS (
        SELECT 
            f_ini_nom AS f_ini,
            LEAST(f_fin_nom, CURRENT_TIMESTAMP) AS f_fin,
            GREATEST(
                ROUND(
                    (EXTRACT(EPOCH FROM (LEAST(f_fin_nom, CURRENT_TIMESTAMP) - f_ini_nom)) / 3600.0)::numeric, 
                    2
                ),
                0.1
            ) AS h_nom_equipo
        FROM ventana_base
    ),
    eventos_carguio AS (
        SELECT 
            REPLACE(UPPER(TRIM(s.dim_name_equipment)), '-', '') AS equipo_id,
            UPPER(TRIM(COALESCE(s.dim_name_main_status, ''))) AS cat_estado,
            EXTRACT(EPOCH FROM (
                LEAST(COALESCE(s.fact_datetime_end_date, v.f_fin), v.f_fin) 
                - GREATEST(s.fact_datetime_start_date, v.f_ini)
            )) / 3600.0 AS duracion_hrs
        FROM public.status s
        CROSS JOIN ventana v
        WHERE REPLACE(UPPER(TRIM(s.dim_name_equipment)), '-', '') IN ('L02','L03', 'L04', 'L05', 'L06')
          AND s.dim_name_equipment <> 'RX181'
          AND s.fact_datetime_start_date < v.f_fin
          AND (s.fact_datetime_end_date > v.f_ini OR s.fact_datetime_end_date IS NULL)
    ),
    tiempos_carguio AS (
        SELECT 
            ec.equipo_id,
            v.h_nom_equipo AS hrs_nom,
            ROUND(SUM(CASE WHEN ec.cat_estado IN ('OPERATIVO', 'DEMORA', 'RESERVA') THEN ec.duracion_hrs ELSE 0 END)::numeric, 2) AS hrs_disp,
            ROUND(SUM(CASE WHEN ec.cat_estado IN ('OPERATIVO', 'DEMORA') THEN ec.duracion_hrs ELSE 0 END)::numeric, 2) AS hrs_op,
            ROUND(SUM(CASE WHEN ec.cat_estado = 'OPERATIVO' THEN ec.duracion_hrs ELSE 0 END)::numeric, 2) AS hrs_ef,
            ROUND(SUM(CASE WHEN ec.cat_estado = 'RESERVA' THEN ec.duracion_hrs ELSE 0 END)::numeric, 2) AS hrs_reserva
        FROM eventos_carguio ec
        CROSS JOIN ventana v
        WHERE ec.duracion_hrs > 0
        GROUP BY 
            ec.equipo_id, 
            v.h_nom_equipo
    ),
    tonelaje_carguio AS (
        SELECT 
            REPLACE(UPPER(TRIM(eq.dim_name_equipment)), '-', '') AS equipo_id,
            ROUND(SUM(COALESCE(cl.fact_float_tons_nominal, 0.0))::numeric, 1) AS ton_real
        FROM public.cyclehauling ch
        JOIN public.cycleloading cl 
            ON ch.id = cl.fk_cycle_hauling
        JOIN public.timemanager tm 
            ON ch.id = tm.id_relational
        JOIN dimensions.equipment eq 
            ON eq.dim_id_equipment = cl.dim_id_equipment_loading
        CROSS JOIN ventana v
        WHERE tm.fact_datetime_start_time_traveling_empty >= v.f_ini
          AND tm.fact_datetime_start_time_traveling_empty < v.f_fin
          AND REPLACE(UPPER(TRIM(eq.dim_name_equipment)), '-', '') IN ('L02', 'L03', 'L04', 'L05', 'L06')
        GROUP BY 
            REPLACE(UPPER(TRIM(eq.dim_name_equipment)), '-', '')
    )
    SELECT 
        COALESCE(tc.equipo_id, 'TOTAL CARGUIO') AS equipo,
        COALESCE(SUM(tc.hrs_nom), 0.0) AS hrs_nom,
        COALESCE(SUM(tc.hrs_disp), 0.0) AS hrs_disp,
        COALESCE(SUM(tc.hrs_op), 0.0) AS hrs_op,
        COALESCE(SUM(tc.hrs_ef), 0.0) AS hrs_ef,
        COALESCE(SUM(tc.hrs_reserva), 0.0) AS hrs_reserva,
        COALESCE(SUM(tg.ton_real), 0.0) AS ton_real,
        ROUND(COALESCE((SUM(tc.hrs_disp) / NULLIF(SUM(tc.hrs_nom), 0.0)) * 100.0, 0.0), 1) AS df_real_pct,
        ROUND(COALESCE((SUM(tc.hrs_ef) / NULLIF(SUM(tc.hrs_disp), 0.0)) * 100.0, 0.0), 1) AS utbd_real_pct,
        ROUND(COALESCE(SUM(tg.ton_real) / NULLIF(SUM(tc.hrs_ef), 0.0), 0.0), 1) AS rend_efectivo_real
    FROM tiempos_carguio tc
    LEFT JOIN tonelaje_carguio tg 
        ON tc.equipo_id = tg.equipo_id
    GROUP BY ROLLUP(tc.equipo_id)
    ORDER BY tc.equipo_id NULLS LAST;
    """

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
    query = """
    WITH parametros AS (
        SELECT 
            %s::date AS p_fecha_inicio,
            %s::date AS p_fecha_fin,
            %s::text AS p_turno_input
    ),
    parametros_evaluados AS (
        SELECT 
            p_fecha_inicio,
            p_fecha_fin,
            CASE 
                WHEN p_fecha_fin > p_fecha_inicio THEN 'A+B'
                WHEN UPPER(p_turno_input) IN ('BOTH', 'A+B') THEN 'A+B'
                WHEN UPPER(p_turno_input) = 'INTER' THEN 'INTER'
                ELSE UPPER(p_turno_input) 
            END AS p_turno_real
        FROM parametros
    ),
    ventana_base AS (
        SELECT 
            CASE 
                WHEN p_turno_real = 'A'               THEN p_fecha_inicio + TIME '08:00:00'
                WHEN p_turno_real = 'B'               THEN p_fecha_inicio + TIME '20:00:00'
                WHEN p_turno_real IN ('A+B', 'INTER') THEN p_fecha_inicio + TIME '08:00:00'
            END AS f_ini_nom,
            CASE 
                WHEN p_turno_real = 'A'               THEN p_fecha_fin + TIME '20:00:00'
                WHEN p_turno_real = 'B'               THEN (p_fecha_fin + INTERVAL '1 day') + TIME '08:00:00'
                WHEN p_turno_real IN ('A+B', 'INTER') THEN (p_fecha_fin + INTERVAL '1 day') + TIME '08:00:00'
            END AS f_fin_nom
        FROM parametros_evaluados
    ),
    ventana AS (
        SELECT 
            f_ini_nom AS f_ini,
            LEAST(f_fin_nom, CURRENT_TIMESTAMP) AS f_fin,
            GREATEST(
                ROUND(
                    (EXTRACT(EPOCH FROM (LEAST(f_fin_nom, CURRENT_TIMESTAMP) - f_ini_nom)) / 3600.0)::numeric, 
                    2
                ),
                0.1
            ) AS h_nom_equipo
        FROM ventana_base
    ),
    eventos_transporte AS (
        SELECT 
            REPLACE(UPPER(TRIM(s.dim_name_equipment)), '-', '') AS equipo_id,
            CASE 
                WHEN REPLACE(UPPER(TRIM(s.dim_name_equipment)), '-', '') IN (
                    'T11','T12','T13','T15','T16','T17','T18','T19','T20','T21','T23','T24'
                ) THEN 'FLOTA CAT 775'
                WHEN REPLACE(UPPER(TRIM(s.dim_name_equipment)), '-', '') IN ('T83','T86') THEN 'FLOTA CAT 777'
                ELSE 'OTRAS FLOTAS'
            END AS flota,
            UPPER(TRIM(COALESCE(s.dim_name_main_status, ''))) AS cat_estado,
            EXTRACT(EPOCH FROM (
                LEAST(COALESCE(s.fact_datetime_end_date, v.f_fin), v.f_fin) 
                - GREATEST(s.fact_datetime_start_date, v.f_ini)
            )) / 3600.0 AS duracion_hrs
        FROM public.status s
        CROSS JOIN ventana v
        WHERE s.fact_datetime_start_date < v.f_fin
          AND (s.fact_datetime_end_date > v.f_ini OR s.fact_datetime_end_date IS NULL)
          AND REPLACE(UPPER(TRIM(s.dim_name_equipment)), '-', '') IN (
              'T11','T12','T13','T15','T16','T17','T18','T19','T20','T21','T23','T24','T83','T86'
          )
    ),
    tiempos_equipos AS (
        SELECT 
            et.equipo_id,
            et.flota,
            v.h_nom_equipo AS hrs_nom,
            ROUND(SUM(CASE WHEN et.cat_estado IN ('OPERATIVO', 'DEMORA', 'RESERVA') THEN et.duracion_hrs ELSE 0 END)::numeric, 2) AS hrs_disp,
            ROUND(SUM(CASE WHEN et.cat_estado IN ('OPERATIVO', 'DEMORA') THEN et.duracion_hrs ELSE 0 END)::numeric, 2) AS hrs_op,
            ROUND(SUM(CASE WHEN et.cat_estado = 'OPERATIVO' THEN et.duracion_hrs ELSE 0 END)::numeric, 2) AS hrs_ef,
            ROUND(SUM(CASE WHEN et.cat_estado = 'RESERVA' THEN et.duracion_hrs ELSE 0 END)::numeric, 2) AS hrs_reserva
        FROM eventos_transporte et
        CROSS JOIN ventana v
        WHERE et.duracion_hrs > 0
        GROUP BY 
            et.equipo_id, 
            et.flota, 
            v.h_nom_equipo
    ),
    tiempos_flota AS (
        SELECT 
            te.flota,
            SUM(te.hrs_nom) AS hrs_nom,
            SUM(te.hrs_disp) AS hrs_disp,
            SUM(te.hrs_op) AS hrs_op,
            SUM(te.hrs_ef) AS hrs_ef,
            SUM(te.hrs_reserva) AS hrs_reserva
        FROM tiempos_equipos te
        GROUP BY te.flota
    ),
    tonelaje_transporte AS (
        SELECT 
            CASE 
                WHEN REPLACE(UPPER(TRIM(eq.dim_name_equipment)), '-', '') IN (
                    'T11','T12','T13','T15','T16','T17','T18','T19','T20','T21','T23','T24'
                ) THEN 'FLOTA CAT 775'
                WHEN REPLACE(UPPER(TRIM(eq.dim_name_equipment)), '-', '') IN ('T83','T86') THEN 'FLOTA CAT 777'
                ELSE 'OTRAS FLOTAS'
            END AS flota,
            ROUND(SUM(COALESCE(cl.fact_float_tons_nominal, 0.0))::numeric, 1) AS ton_real
        FROM public.cyclehauling ch
        JOIN public.cycleloading cl 
            ON ch.id = cl.fk_cycle_hauling
        JOIN public.timemanager tm 
            ON ch.id = tm.id_relational
        JOIN dimensions.equipment eq 
            ON eq.dim_id_equipment = ch.dim_id_equipment_hauling
        CROSS JOIN ventana v
        WHERE tm.fact_datetime_start_time_traveling_empty >= v.f_ini
          AND tm.fact_datetime_start_time_traveling_empty < v.f_fin
          AND REPLACE(UPPER(TRIM(eq.dim_name_equipment)), '-', '') IN (
              'T11','T12','T13','T15','T16','T17','T18','T19','T20','T21','T23','T24','T83','T86'
          )
        GROUP BY 1
    )
    SELECT 
        COALESCE(tf.flota, 'TOTAL TRANSPORTE') AS flota,
        COALESCE(SUM(tf.hrs_nom), 0.0) AS hrs_nom,
        COALESCE(SUM(tf.hrs_disp), 0.0) AS hrs_disp,
        COALESCE(SUM(tf.hrs_op), 0.0) AS hrs_op,
        COALESCE(SUM(tf.hrs_ef), 0.0) AS hrs_ef,
        COALESCE(SUM(tf.hrs_reserva), 0.0) AS hrs_reserva,
        COALESCE(SUM(tt.ton_real), 0.0) AS ton_real,
        ROUND(COALESCE((SUM(tf.hrs_disp) / NULLIF(SUM(tf.hrs_nom), 0.0)) * 100.0, 0.0), 1) AS df_real_pct,
        ROUND(COALESCE((SUM(tf.hrs_ef) / NULLIF(SUM(tf.hrs_disp), 0.0)) * 100.0, 0.0), 1) AS utbd_real_pct,
        ROUND(COALESCE(SUM(tt.ton_real) / NULLIF(SUM(tf.hrs_ef), 0.0), 0.0), 1) AS rend_efectivo_real
    FROM tiempos_flota tf
    LEFT JOIN tonelaje_transporte tt 
        ON tf.flota = tt.flota
    GROUP BY ROLLUP(tf.flota)
    ORDER BY tf.flota NULLS LAST;
    """

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