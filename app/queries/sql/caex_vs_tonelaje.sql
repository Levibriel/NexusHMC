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