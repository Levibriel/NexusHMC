import calendar
import io
import re
from typing import BinaryIO, Union
import pandas as pd
from pypdf import PdfReader


def parsear_plan_pdf(
    origen_pdf: Union[str, BinaryIO, bytes]
) -> tuple[str, list[tuple[str, float]]]:
    """Retorna (periodo, semanas_extraidas) desde el PDF."""
    if isinstance(origen_pdf, bytes):
        stream = io.BytesIO(origen_pdf)
    elif isinstance(origen_pdf, str):
        stream = open(origen_pdf, "rb")
    else:
        stream = origen_pdf
    try:
        reader = PdfReader(stream)
        texto = ""
        for pagina in reader.pages:
            texto += (pagina.extract_text() or "") + "\n"
    finally:
        if isinstance(origen_pdf, str):
            stream.close()

    meses_map = {
        "enero": "01",
        "febrero": "02",
        "marzo": "03",
        "abril": "04",
        "mayo": "05",
        "junio": "06",
        "julio": "07",
        "agosto": "08",
        "septiembre": "09",
        "octubre": "10",
        "noviembre": "11",
        "diciembre": "12",
    }
    periodo = None
    match_periodo = re.search(
        r"(enero|febrero|marzo|abril|mayo|junio|julio|agosto|septiembre|octubre|noviembre|diciembre)\s*(\d{4})",
        texto,
        re.IGNORECASE,
    )
    if match_periodo:
        mes_str = match_periodo.group(1).lower()
        anio_str = match_periodo.group(2)
        periodo = f"{anio_str}-{meses_map[mes_str]}"
    else:
        match_alt = re.search(r"20\d{2}-(0[1-9]|1[0-2])", texto)
        if match_alt:
            periodo = match_alt.group(0)

    if not periodo:
        raise ValueError("No se pudo detectar el mes y año en el contenido del PDF.")

    dict_semanas = {}
    for linea in texto.split("\n"):
        linea_limpia = linea.strip()
        match_sem = re.match(r"^(SEM\s*[1-5])\b", linea_limpia, re.IGNORECASE)
        if match_sem:
            sem_id = match_sem.group(1).replace(" ", "").upper()
            tokens = re.findall(r"[\d]+(?:[.,]\d+)?", linea_limpia)
            if tokens:
                ultimo_num_str = tokens[-1]
                if "," in ultimo_num_str and "." in ultimo_num_str:
                    clean_num = (
                        ultimo_num_str.replace(".", "").replace(",", ".")
                        if ultimo_num_str.rfind(",") > ultimo_num_str.rfind(".")
                        else ultimo_num_str.replace(",", "")
                    )
                elif "," in ultimo_num_str:
                    partes = ultimo_num_str.split(",")
                    clean_num = (
                        ultimo_num_str.replace(",", "")
                        if len(partes[-1]) == 3
                        else ultimo_num_str.replace(",", ".")
                    )
                elif "." in ultimo_num_str:
                    partes = ultimo_num_str.split(".")
                    clean_num = (
                        ultimo_num_str.replace(".", "")
                        if len(partes[-1]) == 3
                        else ultimo_num_str
                    )
                else:
                    clean_num = ultimo_num_str

                try:
                    ton_val = float(clean_num)
                    if ton_val < 1000.0:
                        ton_val = ton_val * 1000.0
                    if sem_id not in dict_semanas or ton_val > dict_semanas[sem_id]:
                        dict_semanas[sem_id] = ton_val
                except ValueError:
                    continue

    semanas_extraidas = sorted(dict_semanas.items(), key=lambda x: x[0])
    if not semanas_extraidas:
        raise ValueError(f"No se detectaron tablas semanales válidas en el PDF para {periodo}.")

    return periodo, semanas_extraidas


def parsear_flota_plan_pdf(
    origen_pdf: Union[str, BinaryIO, bytes], periodo: str
) -> pd.DataFrame:
    """Extrae la DF diaria por camión desde la Sección 3.b y las métricas de flota desde la Sección 3.c."""
    if isinstance(origen_pdf, bytes):
        stream = io.BytesIO(origen_pdf)
    elif isinstance(origen_pdf, str):
        stream = open(origen_pdf, "rb")
    else:
        stream = origen_pdf
    try:
        reader = PdfReader(stream)
        texto = ""
        for pagina in reader.pages:
            t = pagina.extract_text() or ""
            if any(k in t.lower() for k in ["3.b", "3.c", "disponibilidad", "requerimiento", "cargadores", "caex"]):
                texto += t + "\n"
    finally:
        if isinstance(origen_pdf, str):
            stream.close()

    anio, mes = map(int, periodo.split("-"))
    num_dias = calendar.monthrange(anio, mes)[1]

    def limpiar_num(v_str: str) -> float:
        s = v_str.replace("%", "").strip()
        if not s:
            return 0.0
        if "," in s and "." in s:
            s = (
                s.replace(".", "").replace(",", ".")
                if s.rfind(",") > s.rfind(".")
                else s.replace(",", "")
            )
        elif "," in s:
            s = (
                s.replace(",", "")
                if len(s.split(",")[-1]) == 3
                else s.replace(",", ".")
            )
        elif "." in s:
            s = (
                s.replace(".", "")
                if len(s.split(".")[-1]) == 3
                else s
            )
        try:
            return float(s)
        except ValueError:
            return 0.0

    lineas = [l.strip() for l in texto.split("\n") if l.strip()]

    # --- Extraer matriz de Disponibilidad Física (DF) individual desde la Sección 3.b ---
    mapa_df_3b: dict[str, list[float]] = {}
    for l in lineas:
        tokens = l.split()
        if not tokens:
            continue
        eq_id_clean = tokens[0].replace("-", "").upper()
        if re.match(r"^(T\d+|L\d+)$", eq_id_clean):
            nums_raw = re.findall(r"[\d]+(?:[.,]\d+)?%?", l[len(tokens[0]):])
            nums = [limpiar_num(x) for x in nums_raw]
            nums_frac = [n / 100.0 if n > 1.0 else n for n in nums]
            if nums_frac:
                if eq_id_clean not in mapa_df_3b:
                    mapa_df_3b[eq_id_clean] = []
                mapa_df_3b[eq_id_clean].extend(nums_frac)

    # --- Leer Sección 3.c (Métricas de Flota) ---
    def leer_tabla_3c(tag_equipo: str, stop_tags: list[str]) -> dict:
        datos = {"DF": [], "UTBD": [], "REND": [], "TONS": [], "FLOTA": []}
        capturando = False
        for l in lineas:
            if re.search(rf"\b{re.escape(tag_equipo)}\b", l, re.IGNORECASE):
                if tag_equipo.startswith("L") and l.startswith("D"):
                    continue
                capturando = True
                continue
            if capturando and any(re.search(rf"\b{re.escape(st)}\b", l, re.IGNORECASE) for st in stop_tags):
                capturando = False
                continue
            if capturando:
                tokens = l.split()
                if not tokens:
                    continue
                etiqueta = re.sub(r"[^a-zA-Z]", "", tokens[0].upper())
                tipo = None
                if etiqueta in ["DF", "DISP", "DISPONIBILIDAD"]:
                    tipo = "DF"
                elif etiqueta in ["UTBD", "UEBD", "USO", "UTILIZACION"]:
                    tipo = "UTBD"
                elif etiqueta in ["REND", "RENDIMIENTO"]:
                    tipo = "REND"
                elif etiqueta in ["TONS", "TON", "TONELADAS"]:
                    tipo = "TONS"
                elif "FLOTA" in l.upper() or "CAEX" in l.upper():
                    tipo = "FLOTA"

                if tipo:
                    nums_raw = re.findall(r"[\d]+(?:[.,]\d+)?%?", l[len(tokens[0]):])
                    nums = [limpiar_num(x) for x in nums_raw]
                    if nums:
                        datos[tipo].extend(nums)
        return datos

    l02_data = leer_tabla_3c("L02", ["L03", "L04", "L05", "L06", "CAT 775"])
    l03_data = leer_tabla_3c("L03", ["L04", "L05", "L06", "CAT 775"])
    l04_data = leer_tabla_3c("L04", ["L03", "L05", "L06", "CAT 775"])
    l05_data = leer_tabla_3c("L05", ["L03", "L04", "L06", "CAT 775"])
    l06_data = leer_tabla_3c("L06", ["L03", "L04", "L05", "CAT 775", "CAEX 775"])
    c775_data = leer_tabla_3c("CAT 775", ["CAT 777", "Perforadoras", "TOTAL TRANSPORTE"])
    c777_data = leer_tabla_3c("CAT 777", ["Perforadoras", "TOTAL TRANSPORTE", "3.d", "7.- RIESGO"])

    filas = []
    camiones_775_nominales = [f"T{i}" for i in range(11, 25) if i not in (14, 22)]

    for d in range(1, num_dias + 1):
        idx = d - 1
        fecha_str = f"{anio}-{mes:02d}-{d:02d}"

        # --- A. CARGUÍO (L03, L04, L05, L06) ---
        cargadores = [
            ("L02", "988K", l02_data, 0.0, 0.0, 0.0),
            ("L03", "WA600", l03_data, 0.89, 0.803, 512.0),
            ("L04", "CAT 992", l04_data, 0.83, 0.856, 754.0),
            ("L05", "CAT 992", l05_data, 0.88, 0.856, 786.0),
            ("L06", "CAT 992", l06_data, 0.85, 0.856, 800.0),
        ]
        for eq_id, modelo, d_carg, df_def, utbd_def, rend_def in cargadores:
            raw_df = (
                mapa_df_3b[eq_id][idx]
                if eq_id in mapa_df_3b and idx < len(mapa_df_3b[eq_id])
                else (d_carg["DF"][idx] if idx < len(d_carg["DF"]) else df_def)
            )
            df_val = raw_df / 100.0 if raw_df > 1.0 else raw_df
            raw_utbd = d_carg["UTBD"][idx] if idx < len(d_carg["UTBD"]) else utbd_def
            utbd_val = raw_utbd / 100.0 if raw_utbd > 1.0 else raw_utbd
            rend_val = d_carg["REND"][idx] if idx < len(d_carg["REND"]) else rend_def
            raw_ton = d_carg["TONS"][idx] if idx < len(d_carg["TONS"]) else 0.0

            hrs_nom = 24.0
            hrs_disp = round(hrs_nom * df_val, 2)
            hrs_op = round(hrs_disp * utbd_val, 2)
            ton_val = (
                round(hrs_op * rend_val, 2)
                if raw_ton < 100.0 and hrs_op > 0
                else round(raw_ton, 2)
            )

            filas.append({
                "periodo": periodo,
                "fecha": fecha_str,
                "equipo_id": eq_id,
                "tipo_flota": "carguio",
                "modelo": modelo,
                "hrs_nom_plan": hrs_nom,
                "df_plan": round(df_val, 4),
                "hrs_disp_plan": hrs_disp,
                "utbd_plan": round(utbd_val, 4),
                "hrs_op_plan": hrs_op,
                "rend_plan": round(rend_val, 2),
                "ton_plan": ton_val,
            })

        def get_df_3b(eq_id: str, df_fallback: float) -> float:
            return (
                mapa_df_3b[eq_id][idx]
                if eq_id in mapa_df_3b and idx < len(mapa_df_3b[eq_id])
                else df_fallback
            )

        # --- B. TRANSPORTE CAT 775 ---
        raw_df_775 = c775_data["DF"][idx] if idx < len(c775_data["DF"]) else 0.75
        df_775_base = raw_df_775 / 100.0 if raw_df_775 > 1.0 else raw_df_775
        raw_utbd_775 = c775_data["UTBD"][idx] if idx < len(c775_data["UTBD"]) else 0.83
        utbd_775 = raw_utbd_775 / 100.0 if raw_utbd_775 > 1.0 else raw_utbd_775
        rend_775 = c775_data["REND"][idx] if idx < len(c775_data["REND"]) else 210.0
        raw_tons_775 = c775_data["TONS"][idx] if idx < len(c775_data["TONS"]) else 0.0

        activos_775 = [eq for eq in camiones_775_nominales if get_df_3b(eq, df_775_base) > 0.0]
        n_activos_775 = len(activos_775) if len(activos_775) > 0 else 1

        for eq_id in camiones_775_nominales:
            df_eq = get_df_3b(eq_id, df_775_base)
            hrs_disp_eq = round(24.0 * df_eq, 2)
            hrs_op_eq = round(hrs_disp_eq * utbd_775, 2)
            ton_camion_775 = (
                0.0
                if (df_eq == 0.0 or utbd_775 == 0.0 or hrs_op_eq == 0.0)
                else round(raw_tons_775 / n_activos_775, 2)
            )

            filas.append({
                "periodo": periodo,
                "fecha": fecha_str,
                "equipo_id": eq_id,
                "tipo_flota": "transporte",
                "modelo": "CAT 775",
                "hrs_nom_plan": 24.0,
                "df_plan": round(df_eq, 4),
                "hrs_disp_plan": hrs_disp_eq,
                "utbd_plan": round(utbd_775, 4),
                "hrs_op_plan": hrs_op_eq,
                "rend_plan": round(rend_775, 2),
                "ton_plan": ton_camion_775,
            })

        # --- C. TRANSPORTE CAT 777 ---
        raw_df_777 = c777_data["DF"][idx] if idx < len(c777_data["DF"]) else 0.90
        df_777_base = raw_df_777 / 100.0 if raw_df_777 > 1.0 else raw_df_777
        raw_utbd_777 = c777_data["UTBD"][idx] if idx < len(c777_data["UTBD"]) else 0.85
        utbd_777 = raw_utbd_777 / 100.0 if raw_utbd_777 > 1.0 else raw_utbd_777
        rend_777 = c777_data["REND"][idx] if idx < len(c777_data["REND"]) else 290.0
        raw_tons_777 = c777_data["TONS"][idx] if idx < len(c777_data["TONS"]) else 0.0

        activos_777 = [eq for eq in ["T83", "T86"] if get_df_3b(eq, df_777_base) > 0.0]
        n_activos_777 = len(activos_777) if len(activos_777) > 0 else 1

        for eq_id in ["T83", "T86"]:
            df_eq = get_df_3b(eq_id, df_777_base)
            hrs_disp_eq = round(24.0 * df_eq, 2)
            hrs_op_eq = round(hrs_disp_eq * utbd_777, 2)
            ton_camion_777 = (
                0.0
                if (df_eq == 0.0 or utbd_777 == 0.0 or hrs_op_eq == 0.0)
                else round(raw_tons_777 / n_activos_777, 2)
            )

            filas.append({
                "periodo": periodo,
                "fecha": fecha_str,
                "equipo_id": eq_id,
                "tipo_flota": "transporte",
                "modelo": "CAT 777",
                "hrs_nom_plan": 24.0,
                "df_plan": round(df_eq, 4),
                "hrs_disp_plan": hrs_disp_eq,
                "utbd_plan": round(utbd_777, 4),
                "hrs_op_plan": hrs_op_eq,
                "rend_plan": round(rend_777, 2),
                "ton_plan": ton_camion_777,
            })

    return pd.DataFrame(filas)