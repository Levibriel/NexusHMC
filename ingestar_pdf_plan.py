import argparse
from datetime import date, timedelta
import glob
import os
from app.services.pdf_parser import parsear_flota_plan_pdf, parsear_plan_pdf
import pandas as pd

PARQUET_PATH = os.path.join("data", "planes_raw.parquet")
PARQUET_FLOTA_PATH = os.path.join("data", "plan_kpi_equipos.parquet")
CARPETA_PDF = os.path.join("data", "planes_pdf")

def sincronizar_dias_mes(
    periodo: str,
    semanas: list[tuple[str, float]],
    remanejo_diario: float = 0.0,
) -> pd.DataFrame:
    anio, mes = map(int, periodo.split("-"))
    primer_dia = date(anio, mes, 1)
    if mes == 12:
        ultimo_dia = date(anio + 1, 1, 1) - timedelta(days=1)
    else:
        ultimo_dia = date(anio, mes + 1, 1) - timedelta(days=1)

    dict_ton = dict(semanas)
    max_semanas = len(dict_ton)

    calendario = {}
    curr = primer_dia
    sem_idx = 1
    while curr <= ultimo_dia:
        sem_key = min(sem_idx, max_semanas)
        sem_tag = f"SEM{sem_key}"
        if sem_tag not in calendario:
            calendario[sem_tag] = []
        calendario[sem_tag].append(curr)
        if curr.weekday() == 6:
            sem_idx += 1
        curr += timedelta(days=1)

    filas = []
    for sem_id, lista_dias in calendario.items():
        ton_sem_pdf = dict_ton.get(sem_id)
        if ton_sem_pdf is None:
            raise KeyError(
                f"La semana {sem_id} requerida por el calendario no se encontró en el PDF."
            )
        n_dias = len(lista_dias)
        ton_dia_pdf = ton_sem_pdf / n_dias
        ton_dia_final = round(ton_dia_pdf + remanejo_diario, 2)
        ton_sem_final = round(ton_sem_pdf + (remanejo_diario * n_dias), 2)

        for d in lista_dias:
            filas.append({
                "periodo": periodo,
                "fecha": d.strftime("%Y-%m-%d"),
                "semana": sem_id,
                "fase": "Fase 15 B",
                "dias_semana": n_dias,
                "ton_plan_semana": ton_sem_final,
                "ton_plan_dia": ton_dia_final,
            })

    return pd.DataFrame(filas)

def guardar_en_parquet(df_nuevo: pd.DataFrame):
    os.makedirs("data", exist_ok=True)
    if os.path.exists(PARQUET_PATH):
        df_existente = pd.read_parquet(PARQUET_PATH)
        df_final = pd.concat([
            df_existente[~df_existente["fecha"].isin(df_nuevo["fecha"])],
            df_nuevo,
        ])
        df_final = df_final.sort_values(by="fecha").reset_index(drop=True)
    else:
        df_final = df_nuevo.sort_values(by="fecha").reset_index(drop=True)
    df_final.to_parquet(PARQUET_PATH, engine="pyarrow", index=False)
    print(
        f" [OK] Parquet tonelaje actualizado exitosamente en {PARQUET_PATH}. Registros: {len(df_final)}"
    )


def guardar_flota_en_parquet(df_nuevo: pd.DataFrame):
    os.makedirs("data", exist_ok=True)
    if os.path.exists(PARQUET_FLOTA_PATH):
        df_existente = pd.read_parquet(PARQUET_FLOTA_PATH)
        filtro_existente = ~(
            df_existente["fecha"].isin(df_nuevo["fecha"])
            & df_existente["equipo_id"].isin(df_nuevo["equipo_id"])
        )
        df_final = pd.concat([df_existente[filtro_existente], df_nuevo])
        df_final = df_final.sort_values(by=["fecha", "equipo_id"]).reset_index(
            drop=True
        )
    else:
        df_final = df_nuevo.sort_values(by=["fecha", "equipo_id"]).reset_index(
            drop=True
        )
    df_final.to_parquet(PARQUET_FLOTA_PATH, engine="pyarrow", index=False)
    print(
        f" [OK] Parquet flota actualizado exitosamente en {PARQUET_FLOTA_PATH}. Registros: {len(df_final)}"
    )


def procesar_archivos():
    os.makedirs(CARPETA_PDF, exist_ok=True)
    parser = argparse.ArgumentParser(
        description="Ingesta de planes mensuales PDF a Parquet"
    )
    parser.add_argument(
        "--pdf", type=str, help="Ruta al archivo PDF específico"
    )
    parser.add_argument(
        "--remanejo",
        type=float,
        default=None,
        help="Valor diario de remanejo en Ton/día",
    )
    args = parser.parse_args()

    if args.pdf and os.path.exists(args.pdf):
        pdfs_encontrados = [args.pdf]
    else:
        pdfs_encontrados = glob.glob(os.path.join(CARPETA_PDF, "*.pdf"))

    if not pdfs_encontrados:
        print(
            f"No se encontraron archivos PDF en '{CARPETA_PDF}'. Coloque los PDFs en esa ruta o use '--pdf ruta.pdf'."
        )
        return

    for ruta_pdf in pdfs_encontrados:
        print("\n==========================================")
        print(f"Procesando: {ruta_pdf}")
        try:
            periodo, semanas = parsear_plan_pdf(ruta_pdf)
            print(
                f" -> Período detectado: {periodo} | Semanas leídas: {len(semanas)}"
            )

            remanejo_diario = args.remanejo
            if remanejo_diario is None:
                inp = input(
                    f"Ingrese el valor de remanejo diario en Ton/día para el período {periodo} (0 si no aplica): "
                )
                try:
                    remanejo_diario = (
                        float(inp.strip().replace(",", "."))
                        if inp.strip()
                        else 0.0
                    )
                except ValueError:
                    print(
                        "Entrada no válida. Se utilizará 0.0 Ton/día de remanejo."
                    )
                    remanejo_diario = 0.0

            print(
                f" -> Remanejo diario aplicado: {remanejo_diario:,.2f} Ton/día"
            )

            df_dias = sincronizar_dias_mes(periodo, semanas, remanejo_diario)
            guardar_en_parquet(df_dias)

            df_flota = parsear_flota_plan_pdf(ruta_pdf, periodo)
            guardar_flota_en_parquet(df_flota)

        except Exception as e:
            print(f" [Error al procesar {ruta_pdf}]: {e}")


if __name__ == "__main__":
    procesar_archivos()