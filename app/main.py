from datetime import date, datetime, time, timedelta
import os
from pathlib import Path
import sys
from fastapi import FastAPI, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.fleet_service import (
    fetch_asarco_timeline,
    fetch_fleet_status_summary,
    fetch_hourly_trucks_vs_tonnage,
)
from app.services.plan_service import obtener_plan_semana_y_mes, obtener_plan_turno
from app.services.production_service import fetch_hourly_tonnage, fetch_real_tonnage_range
from app.services.sic_flota_service import (
    consultar_carguio_sic,
    consultar_transporte_sic,
)

app = FastAPI(title="Nexus HMC API")


@app.get("/")
async def serve_index():
    ruta_html = os.path.join("app", "static", "index.html")
    if not os.path.exists(ruta_html):
        ruta_html = os.path.join("static", "index.html")
    return FileResponse(ruta_html)


def _evaluar_bloque_turno(fecha_str: str, turno_str: str, ahora: datetime):
    plan_info = obtener_plan_turno(fecha_str, turno_str)
    vector_plan = [int(round(x)) for x in plan_info["perfil_horario"]]
    meta_turno = int(round(plan_info["ton_plan_turno"]))

    # AJUSTE: Cuadratura de enteros para que la suma horaria calce exacto con meta_turno 
    diferencia_int = meta_turno - sum(vector_plan) 
    if vector_plan: 
        vector_plan[-1] += diferencia_int

    semana = plan_info["semana"]

    datos_reales = fetch_hourly_tonnage(fecha_str, turno_str)
    fecha_eval = datetime.strptime(fecha_str, "%Y-%m-%d").date()

    if turno_str == "A":
        dt_inicio = datetime.combine(fecha_eval, time(8, 0, 0))
        dt_fin_op = datetime.combine(fecha_eval, time(19, 45, 0))
        dt_fin_nom = datetime.combine(fecha_eval, time(20, 0, 0))
    else:
        dt_inicio = datetime.combine(fecha_eval, time(20, 0, 0))
        dt_fin_op = datetime.combine(fecha_eval + timedelta(days=1), time(7, 45, 0))
        dt_fin_nom = datetime.combine(fecha_eval + timedelta(days=1), time(8, 0, 0))

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
        seg_rest = 0.0 if ahora >= dt_fin_op else (dt_fin_op - ahora).total_seconds()

    horas_rest_dec = max(0.0, seg_rest / 3600.0)

    series_real = []
    labels_hora = []
    acc_real = 0
    acc_remanejo = 0

    for fila in datos_reales:
        h_idx, rango, _, ton_r, ton_rem = fila
        t_int = int(round(float(ton_r))) if ton_r is not None else 0
        rem_int = int(round(float(ton_rem))) if ton_rem is not None else 0

        labels_hora.append(rango)

        if estado_turno != "CERRADO" and h_idx > horas_transc and t_int == 0:
            series_real.append(None)
        else:
            series_real.append(t_int)

        acc_real += t_int
        acc_remanejo += rem_int

    acc_minado = max(0, acc_real - acc_remanejo)

    return {
        "fecha": fecha_str,
        "turno": turno_str,
        "semana": semana,
        "vector_plan": vector_plan,
        "series_real": series_real,
        "labels_hora": labels_hora,
        "meta_turno": meta_turno,
        "estado_turno": estado_turno,
        "horas_transc": horas_transc,
        "horas_exactas": horas_exactas,
        "horas_rest_dec": horas_rest_dec,
        "acc_real": acc_real,
        "acc_remanejo": acc_remanejo,
        "acc_minado": acc_minado,
    }

def consultar_acumulados_tarjeta4(fecha_str: str) -> dict:
    """Servicio principal para alimentar la Tarjeta 4."""
    plan_info = obtener_plan_semana_y_mes(fecha_str)
    if not plan_info["periodo"]:
        return {
            "semana": "SEM1",
            "ton_real_semana": 0, "ton_plan_semana": 0,
            "ton_real_mes": 0, "ton_plan_mes": 0,
            "cumpl_mes": 0
        }
    
    real_semana = fetch_real_tonnage_range(plan_info["fecha_min_semana"], fecha_str)
    real_mes = fetch_real_tonnage_range(plan_info["fecha_min_mes"], fecha_str)
    
    cumpl_mes = int(round((real_mes / plan_info["ton_plan_mes"]) * 100)) if plan_info["ton_plan_mes"] > 0 else 0
    
    return {
        "semana": plan_info["semana"],
        "ton_real_semana": int(round(real_semana)),
        "ton_plan_semana": int(round(plan_info["ton_plan_semana"])),
        "ton_real_mes": int(round(real_mes)),
        "ton_plan_mes": int(round(plan_info["ton_plan_mes"])),
        "cumpl_mes": cumpl_mes
    }

@app.get("/api/v1/sic/balance")
def get_balance_sic(
    fecha: str = Query(default="2026-10-06"),
    turno: str = Query(default="A"),
):
    ahora = datetime.now()
    fecha_dt = datetime.strptime(fecha, "%Y-%m-%d").date()
    hora_actualizacion = ahora.strftime("%H:%M:%S")

    # 1. Obtener acumulados de la Tarjeta 4 para la fecha consultada 
    acumulados_t4 = consultar_acumulados_tarjeta4(fecha)

    if turno in ["A", "B"]:
        b = _evaluar_bloque_turno(fecha, turno, ahora)
        labels = b["labels_hora"]
        p_series = b["vector_plan"]
        r_series = b["series_real"]
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
            "kpis": {
                "horas_transcurridas": b["horas_transc"],
                "total_horas": 12,
                "ton_real_acum": acc_real,
                "ton_remanejo": b["acc_remanejo"],
                "ton_minado": b["acc_minado"],
                "ton_plan_hora": plan_hora,
                "cumpl_hora": cumpl_h,
                "desvio_hora": desvio_h,
                "meta_total": b["meta_turno"],
                "run_rate": run_rate,
                "proyeccion": proy,
                "cumpl_proyeccion": cumpl_p,
                "desvio_proyeccion": proy - b["meta_turno"],
                # --- NUEVOS CAMPOS TARJETA 4 ---
                "semana_tag": acumulados_t4["semana"],
                "ton_real_semana": acumulados_t4["ton_real_semana"],
                "ton_plan_semana": acumulados_t4["ton_plan_semana"],
                "ton_real_mes": acumulados_t4["ton_real_mes"],
                "ton_plan_mes": acumulados_t4["ton_plan_mes"],
                "cumpl_mes": acumulados_t4["cumpl_mes"]
            },
        }

    # Modos Combinados (24 Horas)
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
    horas_transc = b1["horas_transc"] + b2["horas_transc"]
    acc_real = b1["acc_real"] + b2["acc_real"]
    acc_remanejo = b1["acc_remanejo"] + b2["acc_remanejo"]
    acc_minado = max(0, acc_real - acc_remanejo)
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
        "kpis": {
            "horas_transcurridas": horas_transc,
            "total_horas": 24,
            "ton_real_acum": acc_real,
            "ton_remanejo": acc_remanejo,
            "ton_minado": acc_minado,
            "ton_plan_hora": plan_hora,
            "cumpl_hora": cumpl_h,
            "desvio_hora": desvio_h,
            "meta_total": meta_total,
            "run_rate": run_rate,
            "proyeccion": proy,
            "cumpl_proyeccion": cumpl_p,
            "desvio_proyeccion": proy - meta_total,
            # --- NUEVOS CAMPOS TARJETA 4 ---
            "semana_tag": acumulados_t4["semana"],
            "ton_real_semana": acumulados_t4["ton_real_semana"],
            "ton_plan_semana": acumulados_t4["ton_plan_semana"],
            "ton_real_mes": acumulados_t4["ton_real_mes"],
            "ton_plan_mes": acumulados_t4["ton_plan_mes"],
            "cumpl_mes": acumulados_t4["cumpl_mes"]
        },
    }


@app.get("/api/v1/sic/carguio")
def get_carguio_sic(
    fecha: str = Query(default="2026-10-20"),
    turno: str = Query(default="A"),
):
    try:
        data = consultar_carguio_sic(fecha, turno)
        return {"status": "success", "fecha": fecha, "turno": turno, "data": data}
    except Exception as e:
        return {"status": "error", "detail": str(e)}


@app.get("/api/v1/sic/transporte")
def get_transporte_sic(
    fecha: str = Query(default="2026-10-20"),
    turno: str = Query(default="A"),
):
    try:
        data = consultar_transporte_sic(fecha, turno)
        return {"status": "success", "fecha": fecha, "turno": turno, "data": data}
    except Exception as e:
        return {"status": "error", "detail": str(e)}


@app.get("/api/v1/fleet/status")
def get_fleet_status(
    fecha: str = Query(default="2026-10-06"),
    turno: str = Query(default="A"),
):
    try:
        data = fetch_fleet_status_summary(fecha, turno)
        return {"status": "ok", "data": data}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.get("/api/v1/fleet/caex-vs-ton")
def get_caex_vs_ton(
    fecha: str = Query(default="2026-10-06"),
    turno: str = Query(default="A"),
):
    try:
        data = fetch_hourly_trucks_vs_tonnage(fecha, turno)
        return {"status": "ok", "data": data}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.get("/api/v1/fleet/timeline")
def get_fleet_timeline(
    fecha: str = Query(default="2026-10-06"),
    turno: str = Query(default="A"),
):
    try:
        data = fetch_asarco_timeline(fecha, turno)
        return {"status": "ok", "data": data}
    except Exception as e:
        return {"status": "error", "message": str(e)}


static_dir = os.path.join("app", "static")
if not os.path.exists(static_dir):
    static_dir = "static"

app.mount("/static", StaticFiles(directory=static_dir), name="static")

if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)