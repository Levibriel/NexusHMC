import os
import pandas as pd

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


def obtener_plan_turno(fecha: str, turno: str = "A") -> dict:
    """Función consumida por _evaluar_bloque_turno en main.py.
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