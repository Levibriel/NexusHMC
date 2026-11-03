import sys 
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent 
if str(ROOT_DIR) not in sys.path: sys.path.insert(0, str(ROOT_DIR))

import os
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from app.api.v1.horahora import router as horahora_router

app = FastAPI(
    title="Nexus HMC API",
    description="Centro de Control Operacional - HMC Michilla",
    version="1.0.0",
)

# Inclusión del router del Módulo Monitoreo Hora a Hora
app.include_router(horahora_router)

static_dir = (
    os.path.join("app", "static")
    if os.path.exists(os.path.join("app", "static"))
    else "static"
)
app.mount("/static", StaticFiles(directory=static_dir), name="static")


@app.get("/")
async def serve_index():
    ruta_html = os.path.join(static_dir, "index.html")
    return FileResponse(ruta_html)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)