"""Interfaz Streamlit para el rastreador RFE."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pandas as pd
import streamlit as st

from rfe_rastreador.config import Settings
from rfe_rastreador.privacy import PrivacyManager
from rfe_rastreador.checkpoint import CheckpointManager
from rfe_rastreador.models import Categoria


# ------------------------------------------------------------------
# Utilidades
# ------------------------------------------------------------------


def _project_root() -> Path:
    """Raíz del proyecto (igual que Settings)."""
    return Path(__file__).resolve().parents[2]


def _list_checkpoints(state_dir: Path) -> list[dict]:
    """Lista carpetas de checkpoint bajo state_dir."""
    if not state_dir.exists():
        return []
    rows: list[dict] = []
    for carpeta in sorted(state_dir.iterdir()):
        if not carpeta.is_dir():
            continue
        nombre = carpeta.name
        rows.append(
            {
                "run_id": nombre,
                "path": carpeta,
                "resultados_jsonl": carpeta / "resultados.jsonl",
                "lineas_jsonl": 0,
            }
        )
        try:
            with open(carpeta / "resultados.jsonl", encoding="utf-8") as f:
                filas = sum(1 for _ in f)
            rows[-1]["lineas_jsonl"] = filas
        except Exception:
            pass
    return rows


def _resumen_categorias(jsonl_path: Path) -> dict[str, int]:
    """Cuenta categorías en un checkpoint JSONL."""
    conteos: dict[str, int] = {}
    if not jsonl_path.exists():
        return conteos
    with open(jsonl_path, encoding="utf-8") as f:
        for linea in f:
            try:
                d = json.loads(linea.strip())
                cat = d.get("categoria", "")
                if cat:
                    conteos[cat] = conteos.get(cat, 0) + 1
            except Exception:
                continue
    return conteos


# ------------------------------------------------------------------
# Progreso actual
# ------------------------------------------------------------------


def _obtener_progreso_actual(state_dir: Path) -> dict:
    """Lee el progreso del checkpoint con más avance."""
    checkpoints = _list_checkpoints(state_dir)
    if not checkpoints:
        return {
            "checkpoint_activo": None,
            "total_linhas": 0,
            "total_objetivo": 0,
            "categorias": {},
        }

    cp_activo = max(checkpoints, key=lambda c: c.get("lineas_jsonl", 0))

    categorias: dict[str, int] = {}
    if cp_activo["resultados_jsonl"] and cp_activo["resultados_jsonl"].exists():
        categorias = _resumen_categorias(cp_activo["resultados_jsonl"])

    # El total objetivo se toma del estado del propio checkpoint activo.
    total_objetivo = 0
    try:
        estado = json.loads(
            (cp_activo["path"] / "estado.json").read_text(encoding="utf-8")
        )
        total_objetivo = int(estado.get("total", 0) or 0)
    except Exception:
        total_objetivo = 0

    return {
        "checkpoint_activo": cp_activo["run_id"],
        "total_linhas": cp_activo["lineas_jsonl"],
        "total_objetivo": total_objetivo,
        "categorias": categorias,
    }


# ------------------------------------------------------------------
# Dashboard
# ------------------------------------------------------------------


def page_dashboard() -> None:
    st.title("📊 Rastreador RFE — Panel de control")

    settings = Settings.from_env()
    privacy = PrivacyManager(settings)
    state_dir = settings.state_dir

    # ------------------------------------------------------------------
    # Consentimiento
    # ------------------------------------------------------------------
    st.subheader("Consentimiento de privacidad")
    consent_status = privacy.consent.status()
    if consent_status is None:
        st.info(
            "No se ha otorgado consentimiento. Ejecute en la terminal del servidor:"
        )
        st.code(
            "python -m rfe_rastreador consent --grant --operator \"$(whoami)\"",
            language="bash",
        )
    else:
        st.success(
            f"Consentimiento vigente (v{consent_status.version}) — Operador: {consent_status.operator}"
        )

    # ------------------------------------------------------------------
    # Progreso de la corrida
    # ------------------------------------------------------------------
    st.subheader("Progreso actual de la corrida")

    progreso = _obtener_progreso_actual(state_dir)
    cp_activo = progreso["checkpoint_activo"]
    total_linhas = progreso["total_linhas"]
    total_objetivo = progreso.get("total_objetivo") or 86232
    categorias = progreso["categorias"]

    # Botón de refrescar
    col_refresh, _ = st.columns([1, 5])
    if col_refresh.button("🔄 Refrescar"):
        st.rerun()

    # Estado del contenedor Docker
    try:
        resultado = subprocess.run(
            ["docker", "compose", "ps", "--filter", "name=rfe-rastreador"],
            capture_output=True,
            text=True,
            cwd=str(_project_root()),
        )
        if resultado.returncode == 0 and "Up" in resultado.stdout:
            st.success("✅ El contenedor `rfe-rastreador` está **en ejecución**.")
        else:
            st.warning("⏸️ El contenedor `rfe-rastreador` está **detenido**.")
    except FileNotFoundError:
        st.info("CLI de Docker no disponible en este contenedor.")
    except Exception:
        st.info("No se pudo verificar el estado del contenedor Docker.")

    if total_linhas > 0:
        porcentaje = min(int(total_linhas * 100 / total_objetivo), 100)
        st.info(
            f"Progreso del checkpoint `{cp_activo}`: "
            f"**{total_linhas} / {total_objetivo} RFEs** "
            f"(**{porcentaje}%**) — {total_objetivo - total_linhas} pendientes"
        )
    else:
        st.info("Aún no hay RFEs procesados en el checkpoint.")

    if categorias:
        st.write("**Categorías encontradas en el checkpoint:**")
        df_cat = pd.DataFrame(
            list(categorias.items()), columns=["Categoria", "Total"]
        )
        st.bar_chart(df_cat.set_index("Categoria"))
    elif total_linhas == 0:
        st.info(
            "La corrida puede estar en curso o aún no iniciada."
        )

    # ------------------------------------------------------------------
    # Checkpoints guardados
    # ------------------------------------------------------------------
    st.subheader("Checkpoints guardados")
    checkpoints = _list_checkpoints(state_dir)
    if checkpoints:
        cp_names = [cp["run_id"] for cp in checkpoints]
        cp_seleccionado = st.selectbox(
            "Seleccionar checkpoint para ver detalles",
            options=cp_names,
            index=0 if cp_names else None,
        )
        if cp_seleccionado:
            cp_data = next(cp for cp in checkpoints if cp["run_id"] == cp_seleccionado)
            st.write(f"**Run ID:** {cp_seleccionado}")
            st.write(f"**Líneas JSONL:** {cp_data['lineas_jsonl']}")

            resumen = _resumen_categorias(cp_data["resultados_jsonl"])
            if resumen:
                st.write("**Categorías:**")
                df_cat = pd.DataFrame(
                    list(resumen.items()), columns=["Categoria", "Total"]
                )
                st.bar_chart(df_cat.set_index("Categoria"))
            else:
                st.info("No hay categorías contadas aún.")

            resumen_path = cp_data["path"] / "RESUMEN.xlsx"
            if resumen_path.exists():
                with open(resumen_path, "rb") as f:
                    st.download_button(
                        "Descargar RESUMEN.xlsx",
                        data=f.read(),
                        file_name="RESUMEN.xlsx",
                    )
            todos_path = cp_data["path"] / "TODOS.xlsx"
            if todos_path.exists():
                with open(todos_path, "rb") as f:
                    st.download_button(
                        "Descargar TODOS.xlsx",
                        data=f.read(),
                        file_name="TODOS.xlsx",
                    )
    else:
        st.info("No hay checkpoints guardados todavía.")

    # ------------------------------------------------------------------
    # Corrida limitada desde el navegador
    # ------------------------------------------------------------------
    st.markdown("---")
    st.subheader("Corrida limitada desde el navegador")
    st.info(
        "Esta corrida se ejecuta **dentro del contenedor Streamlit** y usa el "
        "volumen compartido `./data`. Úsala solo para pruebas pequeñas "
        "(límite bajo). Para corridas largas usa la terminal del servidor."
    )

    col1, col2 = st.columns([3, 1])
    rf = col1.text_input(
        "Ruta del Excel de entrada",
        value="/app/data/input/ParaConsulta.xlsx",
    )
    zonas = col2.text_input(
        "Zonas (coma-separadas)",
        value="1101,1111",
    )
    limit = st.number_input(
        "Límite de RFEs (0 = todas)", min_value=0, value=10
    )

    if st.button("Ejecutar corrida limitada"):
        if not Path(rf).exists():
            st.error(f"El archivo {rf} no existe dentro del contenedor.")
        else:
            cmd = [
                sys.executable,
                "-m",
                "rfe_rastreador",
                "run",
                "--input",
                rf,
                "--zonas",
                zonas,
                "--accept-privacy",
                "--operator",
                "streamlit_user",
            ]
            if limit > 0:
                cmd += ["--limit", str(limit)]

            st.info("Ejecutando... esto puede tardar varios minutos.")
            output_placeholder = st.empty()
            salida_acumulada = []

            def leer_salida(proc):
                for linea in proc.stdout:
                    salida_acumulada.append(linea)
                    output_placeholder.code("".join(salida_acumulada[-30:]))

            try:
                proc = subprocess.Popen(
                    cmd,
                    cwd=str(_project_root()),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                )
                leer_salida(proc)
                proc.wait()

                if proc.returncode == 0:
                    st.success("✅ Corrida limitada completada.")
                    st.code("".join(salida_acumulada[-50:]))
                    st.rerun()
                else:
                    st.error("❌ La corrida falló.")
                    st.code("".join(salida_acumulada[-50:]))
            except Exception as e:
                st.error(f"Error al ejecutar: {e}")


# ------------------------------------------------------------------
# Acerca de
# ------------------------------------------------------------------


def page_about() -> None:
    st.title("ℹ️ Acerca de")
    st.markdown(
        """
        **Rastreador RFE** — Interfaz Streamlit.

        Permite:
        * Consultar el estado de corridas anteriores (checkpoints, categorías).
        * Descargar los archivos Excel generados (RESUMEN, TODOS, por categoría).
        * Ejecutar una corrida limitada desde el navegador.
        * Ver el estado de consentimiento de privacidad.

        La corrida principal se ejecuta dentro del contenedor Docker
        `rfe-rastreador` y usa los mismos permisos y configuración que la
        CLI (`RFE_ZONAS`, `RFE_DIR_MODE`, `RFE_FILE_MODE`, `RETRY_ERRORS`,
        etc.).

        Para ver los logs en tiempo real, use la terminal del servidor:
        ```
        docker compose logs -f rfe-rastreador
        ```
        """
    )


# ------------------------------------------------------------------
# Entry point
# ------------------------------------------------------------------


def main() -> None:
    st.set_page_config(page_title="Rastreador RFE", layout="wide")

    st.sidebar.title("Menú")
    pagina = st.sidebar.radio(
        "Ir a", ["Dashboard", "Acerca de"], label_visibility="collapsed"
    )

    if pagina == "Dashboard":
        page_dashboard()
    else:
        page_about()


if __name__ == "__main__":
    main()
