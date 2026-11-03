# app/api/v1/horahora.py
from datetime import date, datetime, time, timedelta
from fastapi import APIRouter, Query
from app.services.horahora_service import (
    obtener_plan_turno,
    fetch_hourly_tonnage,
    consultar_carguio_sic,
    consultar_transporte_sic,
    fetch_fleet_status_summary,
    fetch_hourly_trucks_vs_tonnage,
    fetch_asarco_timeline,
    consultar_acumulados_semanames,
)

router = APIRouter(prefix="/api/v1", tags=["Monitoreo Hora a Hora"])


def _evaluar_bloque_turno(fecha_str: str, turno_str: str, ahora: datetime):
    plan_info = obtener_plan_turno(fecha_str, turno_str)
    meta_turno = int(round(plan_info["ton_plan_turno"]))
    
    # Curva horaria planificada en enteros con cuadratura al final
    vector_plan = [int(round(x)) for x in plan_info["perfil_horario"]]
    diferencia_enteros = meta_turno - sum(vector_plan)
    if vector_plan:
        vector_plan[-1] += diferencia_enteros

    semana = plan_info["semana"]
    datos_reales = fetch_hourly_tonnage(fecha_str, turno_str)

    fecha_eval = datetime.strptime(fecha_str, "%Y-%m-%d").date()
    if turno_str == "A":
        dt_inicio = datetime.combine(fecha_eval, time(8, 0, 0))
        dt_fin_op = datetime.combine(fecha_eval, time(19, 45, 0))
        dt_fin_nom = datetime.combine(fecha_eval, time(20, 0, 0))
    else:
        dt_inicio = datetime.combine(fecha_eval, time(20, 0, 0))
        dt_fin_op = datetime.combine(
            fecha_eval + timedelta(days=1), time(7, 45, 0)
        )
        dt_fin_nom = datetime.combine(
            fecha_eval + timedelta(days=1), time(8, 0, 0)
        )

    if ahora < dt_inicio:
        estado_turno = "FUTURO"
        horas_transc = 0
        seg_rest = 11.75 * 3600
        horas_exactas = 0.0
    elif ahora >= dt_fin_nom:
        estado_turno = "CERRADO"
        horas_transc = 12
        seg_rest = 0.0
        horas_exactas = 11.75
    else:
        estado_turno = "EN_CURSO"
        seg_desde_inicio = (ahora - dt_inicio).total_seconds()
        horas_transc = min(12, int(seg_desde_inicio // 3600) + 1)
        horas_exactas = max(0.1, seg_desde_inicio / 3600.0)
        seg_rest = (
            0.0 if ahora >= dt_fin_op else (dt_fin_op - ahora).total_seconds()
        )

    horas_rest_dec = max(0.0, seg_rest / 3600.0)

    series_real = []
    series_minado = []
    series_remanejo = []
    labels_hora = []
    
    acc_real = 0
    acc_minado = 0
    acc_remanejo = 0

    for fila in datos_reales:
        if isinstance(fila, dict):
            h_idx = fila.get("hora_idx")
            rango = fila.get("rango_horario")
            t_min = float(fila.get("tonelaje_minado") or fila.get("ton_minado") or 0)
            t_rem = float(fila.get("tonelaje_remanejo") or fila.get("ton_remanejo") or 0)
            t_real = float(fila.get("tonelaje_real") or (t_min + t_rem))
        elif len(fila) >= 6:
            h_idx = fila[0]
            rango = fila[1]
            t_min = float(fila[3]) if fila[3] is not None else 0.0
            t_rem = float(fila[4]) if fila[4] is not None else 0.0
            t_real = float(fila[5]) if fila[5] is not None else (t_min + t_rem)
        else:
            h_idx = fila[0]
            rango = fila[1]
            t_real = float(fila[3]) if fila[3] is not None else 0.0
            t_min = t_real
            t_rem = 0.0

        int_min = int(round(t_min))
        int_rem = int(round(t_rem))
        int_real = int(round(t_real))

        labels_hora.append(rango)

        if estado_turno != "CERRADO" and h_idx > horas_transc and int_real == 0:
            series_minado.append(None)
            series_remanejo.append(None)
            series_real.append(None)
        else:
            series_minado.append(int_min)
            series_remanejo.append(int_rem)
            series_real.append(int_real)

        acc_minado += int_min
        acc_remanejo += int_rem
        acc_real += int_real

    return {
        "fecha": fecha_str,
        "turno": turno_str,
        "semana": semana,
        "vector_plan": vector_plan,
        "series_real": series_real,
        "series_minado": series_minado,
        "series_remanejo": series_remanejo,
        "labels_hora": labels_hora,
        "meta_turno": meta_turno,
        "estado_turno": estado_turno,
        "horas_transc": horas_transc,
        "horas_exactas": horas_exactas,
        "horas_rest_dec": horas_rest_dec,
        "acc_real": acc_real,
        "acc_minado": acc_minado,
        "acc_remanejo": acc_remanejo,
    }

@router.get("/horahora/balance")
def get_balance_sic(
    fecha: str = Query(default="2026-10-06"), turno: str = Query(default="A")
):
    ahora = datetime.now()
    fecha_dt = datetime.strptime(fecha, "%Y-%m-%d").date()
    hora_actualizacion = ahora.strftime("%H:%M:%S")

    # Acumulados de Semana y Mes (Tarjeta 4)
    acumulado_semanames = consultar_acumulados_semanames(fecha)

    if turno in ["A", "B"]:
        b = _evaluar_bloque_turno(fecha, turno, ahora)
        labels = b["labels_hora"]
        p_series = b["vector_plan"]
        r_series = b["series_real"]
        min_series = b["series_minado"]
        rem_series = b["series_remanejo"]

        plan_hora = (
            sum(p_series[: b["horas_transc"]]) if b["horas_transc"] > 0 else 0
        )
        acc_real = b["acc_real"]
        cumpl_h = (
            int(round(acc_real / plan_hora * 100)) if plan_hora > 0 else 0
        )
        desvio_h = acc_real - plan_hora

        if b["estado_turno"] == "CERRADO":
            proy = acc_real
            run_rate = int(round(acc_real / 11.75)) if acc_real > 0 else 0
        elif b["estado_turno"] == "FUTURO":
            proy = 0
            run_rate = 0
        else:
            run_rate = (
                int(round(acc_real / b["horas_exactas"]))
                if b["horas_exactas"] > 0
                else 0
            )
            proy = acc_real + int(round(run_rate * b["horas_rest_dec"]))

        cumpl_p = (
            int(round(proy / b["meta_turno"] * 100))
            if b["meta_turno"] > 0
            else 0
        )

        return {
            "fecha": fecha,
            "turno": turno,
            "semana": b["semana"],
            "estado_operacional": b["estado_turno"],
            "ultima_actualizacion": hora_actualizacion,
            "horas_labels": labels,
            "plan_series": p_series,
            "real_series": r_series,
            "minado_series": min_series,
            "remanejo_series": rem_series,
            "kpis": {
                "horas_transcurridas": b["horas_transc"],
                "total_horas": 12,
                "ton_real_acum": acc_real,
                "ton_real_minado": b["acc_minado"],
                "ton_real_remanejo": b["acc_remanejo"],
                "ton_plan_hora": plan_hora,
                "cumpl_hora": cumpl_h,
                "desvio_hora": desvio_h,
                "meta_total": b["meta_turno"],
                "run_rate": run_rate,
                "proyeccion": proy,
                "cumpl_proyeccion": cumpl_p,
                "desvio_proyeccion": proy - b["meta_turno"],
                "semana_tag": acumulado_semanames.get("semana", "SEM1"),
                "ton_real_semana": acumulado_semanames.get("ton_real_semana", 0),
                "ton_plan_semana": acumulado_semanames.get("ton_plan_semana", 0),
                "ton_real_mes": acumulado_semanames.get("ton_real_mes", 0),
                "ton_plan_mes": acumulado_semanames.get("ton_plan_mes", 0),
                "cumpl_mes": acumulado_semanames.get("cumpl_mes", 0),
            },
        }

    if turno == "BOTH":
        b1 = _evaluar_bloque_turno(fecha, "A", ahora)
        b2 = _evaluar_bloque_turno(fecha, "B", ahora)
        etiqueta_turno = "Turno A + B"
    else:  # INTER
        fecha_ayer = (fecha_dt - timedelta(days=1)).strftime("%Y-%m-%d")
        b1 = _evaluar_bloque_turno(fecha_ayer, "B", ahora)
        b2 = _evaluar_bloque_turno(fecha, "A", ahora)
        etiqueta_turno = "Turno B (Ayer) + Turno A (Hoy)"

    labels = b1["labels_hora"] + b2["labels_hora"]
    p_series = b1["vector_plan"] + b2["vector_plan"]
    r_series = b1["series_real"] + b2["series_real"]
    min_series = b1["series_minado"] + b2["series_minado"]
    rem_series = b1["series_remanejo"] + b2["series_remanejo"]

    horas_transc = b1["horas_transc"] + b2["horas_transc"]
    acc_real = b1["acc_real"] + b2["acc_real"]
    acc_minado = b1["acc_minado"] + b2["acc_minado"]
    acc_remanejo = b1["acc_remanejo"] + b2["acc_remanejo"]

    meta_total = b1["meta_turno"] + b2["meta_turno"]
    plan_hora = sum(p_series[:horas_transc]) if horas_transc > 0 else 0
    cumpl_h = int(round(acc_real / plan_hora * 100)) if horas_transc > 0 else 0
    desvio_h = acc_real - plan_hora

    if b2["estado_turno"] == "CERRADO":
        proy = acc_real
        run_rate = int(round(acc_real / 23.5)) if acc_real > 0 else 0
        estado_global = "CERRADO"
    else:
        total_exactas = b1["horas_exactas"] + b2["horas_exactas"]
        run_rate = (
            int(round(acc_real / total_exactas)) if total_exactas > 0 else 0
        )
        proy = acc_real + int(round(run_rate * b2["horas_rest_dec"]))
        estado_global = "EN_CURSO"

    cumpl_p = int(round(proy / meta_total * 100)) if meta_total > 0 else 0

    return {
        "fecha": fecha,
        "turno": etiqueta_turno,
        "semana": b2["semana"],
        "estado_operacional": estado_global,
        "ultima_actualizacion": hora_actualizacion,
        "horas_labels": labels,
        "plan_series": p_series,
        "real_series": r_series,
        "minado_series": min_series,
        "remanejo_series": rem_series,
        "kpis": {
            "horas_transcurridas": horas_transc,
            "total_horas": 24,
            "ton_real_acum": acc_real,
            "ton_real_minado": acc_minado,
            "ton_real_remanejo": acc_remanejo,
            "ton_plan_hora": plan_hora,
            "cumpl_hora": cumpl_h,
            "desvio_hora": desvio_h,
            "meta_total": meta_total,
            "run_rate": run_rate,
            "proyeccion": proy,
            "cumpl_proyeccion": cumpl_p,
            "desvio_proyeccion": proy - meta_total,
            "semana_tag": acumulado_semanames.get("semana", "SEM1"),
            "ton_real_semana": acumulado_semanames.get("ton_real_semana", 0),
            "ton_plan_semana": acumulado_semanames.get("ton_plan_semana", 0),
            "ton_real_mes": acumulado_semanames.get("ton_real_mes", 0),
            "ton_plan_mes": acumulado_semanames.get("ton_plan_mes", 0),
            "cumpl_mes": acumulado_semanames.get("cumpl_mes", 0),
        },
    }

@router.get("/horahora/carguio")
def get_carguio_sic(
    fecha: str = Query(default="2026-10-20"),
    turno: str = Query(default="A"),
):
    try:
        data = consultar_carguio_sic(fecha, turno)
        return {"status": "success", "fecha": fecha, "turno": turno, "data": data}
    except Exception as e:
        return {"status": "error", "detail": str(e)}

@router.get("/horahora/transporte")
def get_transporte_sic(
    fecha: str = Query(default="2026-10-20"),
    turno: str = Query(default="A"),
):
    try:
        data = consultar_transporte_sic(fecha, turno)
        return {"status": "success", "fecha": fecha, "turno": turno, "data": data}
    except Exception as e:
        return {"status": "error", "detail": str(e)}

@router.get("/horahora/semanames")
def get_acumulados_semanames(
    fecha: str = Query(default="2026-10-20"),
):
    try:
        data = consultar_acumulados_semanames(fecha)
        return {"status": "success", "fecha": fecha, "data": data}
    except Exception as e:
        return {"status": "error", "detail": str(e)}

@router.get("/horahora/fleetstatus")
def get_fleet_status(
    fecha: str = Query(default="2026-10-06"),
    turno: str = Query(default="A"),
):
    try:
        data = fetch_fleet_status_summary(fecha, turno)
        return {"status": "ok", "data": data}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@router.get("/horahora/caex-vs-ton")
def get_caex_vs_ton(
    fecha: str = Query(default="2026-10-06"),
    turno: str = Query(default="A"),
):
    try:
        data = fetch_hourly_trucks_vs_tonnage(fecha, turno)
        return {"status": "ok", "data": data}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@router.get("/horahora/statustimeline")
def get_fleet_timeline(
    fecha: str = Query(default="2026-10-06"),
    turno: str = Query(default="A"),
):
    try:
        data = fetch_asarco_timeline(fecha, turno)
        return {"status": "ok", "data": data}
    except Exception as e:
        return {"status": "error", "message": str(e)}