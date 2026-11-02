from app.core.database import get_db_connection
from app.services.plan_service import obtener_plan_semana_y_mes


def fetch_hourly_tonnage(target_date: str, target_shift: str):
    query = """
    WITH params AS (
        SELECT %s::date AS fecha_input, %s::text AS turno_input
    ),
    shift_boundaries AS (
        SELECT 
            p.fecha_input, 
            p.turno_input, 
            CASE 
                WHEN p.turno_input = 'A' THEN p.fecha_input + TIME '08:00:00' 
                ELSE p.fecha_input + TIME '20:00:00' 
            END AS shift_start, 
            CASE 
                WHEN p.turno_input = 'A' THEN p.fecha_input + TIME '20:00:00' 
                ELSE p.fecha_input + INTERVAL '1 day' + TIME '08:00:00' 
            END AS shift_end 
        FROM params p
    ),
    shift_hours AS (
        SELECT 
            sb.fecha_input, 
            sb.turno_input, 
            ROW_NUMBER() OVER (ORDER BY gs) AS hora_idx, 
            gs AS hora_inicio_intervalo, 
            gs + INTERVAL '1 hour' AS hora_fin_intervalo, 
            TO_CHAR(gs, 'HH24:00') || ' - ' || TO_CHAR(gs + INTERVAL '1 hour', 'HH24:00') AS rango_horario 
        FROM shift_boundaries sb 
        CROSS JOIN LATERAL generate_series(sb.shift_start, sb.shift_end - INTERVAL '1 hour', INTERVAL '1 hour') gs
    ),
    viajes_reales AS (
        SELECT 
            DATE_TRUNC('hour', m.fact_datetime_start_time_traveling_empty) AS hora_truncada, 
            c.id AS cycle_id, 
            COALESCE(l.fact_float_tons_nominal, 0) AS toneladas, 
            CASE 
                WHEN UPPER(COALESCE(loc.dim_name_location_load, '')) LIKE '%%STOCK%%' 
                  OR UPPER(COALESCE(loc.dim_name_location_load, '')) LIKE '%%REM%%' 
                THEN COALESCE(l.fact_float_tons_nominal, 0) 
                ELSE 0 
            END AS toneladas_remanejo 
        FROM public.cyclehauling c 
        JOIN public.cycleloading l ON c.id = l.fk_cycle_hauling 
        JOIN public.timemanager m ON c.id = m.id_relational 
        JOIN dimensions.equipment e ON e.dim_id_equipment = l.dim_id_equipment_loading 
        LEFT JOIN dimensions.locationload loc ON loc.dim_id_location_load = l.dim_id_location_load 
        JOIN shift_boundaries sb ON m.fact_datetime_start_time_traveling_empty >= sb.shift_start 
                                AND m.fact_datetime_start_time_traveling_empty < sb.shift_end 
        WHERE e.dim_name_equipment NOT IN ('RX181', 'NG02')
    )
    SELECT 
        sh.hora_idx, 
        sh.rango_horario, 
        COUNT(vr.cycle_id) AS total_viajes, 
        ROUND(COALESCE(SUM(vr.toneladas), 0)::numeric, 0) AS tonelaje_real, 
        ROUND(COALESCE(SUM(vr.toneladas_remanejo), 0)::numeric, 0) AS tonelaje_remanejo 
    FROM shift_hours sh 
    LEFT JOIN viajes_reales vr ON vr.hora_truncada = sh.hora_inicio_intervalo 
    GROUP BY sh.hora_idx, sh.rango_horario, sh.hora_inicio_intervalo 
    ORDER BY sh.hora_idx;
    """
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(query, (target_date, target_shift))
            return cur.fetchall()

def fetch_real_tonnage_range(fecha_inicio: str, fecha_fin: str) -> float:
    """Consulta PostgreSQL para el tonelaje real acumulado en un rango de fechas."""
    query = """
    SELECT COALESCE(SUM(l.fact_float_tons_nominal), 0) AS total_ton
    FROM public.cyclehauling c
    JOIN public.cycleloading l ON c.id = l.fk_cycle_hauling
    JOIN public.timemanager m ON c.id = m.id_relational
    JOIN dimensions.equipment e ON e.dim_id_equipment = l.dim_id_equipment_loading
    WHERE m.fact_datetime_start_time_traveling_empty >= (%s::date + TIME '08:00:00')
      AND m.fact_datetime_start_time_traveling_empty < (%s::date + INTERVAL '1 day' + TIME '08:00:00')
      AND e.dim_name_equipment NOT IN ('RX181','NG02');
    """
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(query, (fecha_inicio, fecha_fin))
            row = cur.fetchone()
            return float(row[0]) if row else 0.0

