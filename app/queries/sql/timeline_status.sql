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