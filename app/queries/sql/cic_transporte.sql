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