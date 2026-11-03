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