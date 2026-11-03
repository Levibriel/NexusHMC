SELECT COALESCE(SUM(l.fact_float_tons_nominal), 0) AS total_ton
    FROM public.cyclehauling c
    JOIN public.cycleloading l ON c.id = l.fk_cycle_hauling
    JOIN public.timemanager m ON c.id = m.id_relational
    JOIN dimensions.equipment e ON e.dim_id_equipment = l.dim_id_equipment_loading
    WHERE m.fact_datetime_start_time_traveling_empty >= (%s::date + TIME '08:00:00')
      AND m.fact_datetime_start_time_traveling_empty < (%s::date + INTERVAL '1 day' + TIME '08:00:00')
      AND e.dim_name_equipment NOT IN ('RX181','NG02');