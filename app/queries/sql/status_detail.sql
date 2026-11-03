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