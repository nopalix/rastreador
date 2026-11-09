# Contexto del Proyecto: Automatización y Análisis Educativo (SASA/SIGA/SIGED)

## 1. Objetivo
Procesar un archivo XLS con RFE's para validar su situación académica, aplicando reglas de negocio y filtrando registros para contacto posterior (WhatsApp/Visita). Ejecutarse en contenedor Docker sobre Ubuntu Server con persistencia de estado.

## 2. Definiciones de Sistemas
- **SIGA (PEC):** Almacena registros modelo PEC (2016-2024). La consulta real es por RFE en el módulo Figuras; el detalle de Participantes muestra `Nivel` y calificación.
- **SASA (MEV):** Almacena RFE's de ambos modelos. Niveles de primaria: *Inicial* e *Intermedio*. Nivel secundaria: *Avanzado* o *Secundaria*.
- **SIGED:** Consulta de antecedentes de escolarizados.

## 3. Reglas de Negocio (Prioridad de Evaluación)
1. **Basura (Excluir):** Calificación $\ge 6$ y nivel `Avanzado` o `Secundaria` (SASA), o `Secundaria` (SIGA).
2. **Continuidad:** Calificación $\ge 6$ en SIGA (`Primaria`) Y nivel `Inicial`/`Intermedio` con calificación aprobatoria en SASA (MEV).
3. **No Acreditado:** Calificación en rango $0 < Calificación < 6$ (evaluar primero la calificación por posibles niveles vacíos en SIGA).
4. **No Presentado:** Tienen nivel asignado pero calificación vacía o en cero ($\emptyset$).

## 4. Alcance operativo
- Solo se consultan los RFE cuya `CVE ZONA` sea `1101` o `1111` (configurable con `RFE_ZONAS` y `RFE_ZONE_COLUMN`). El resto del Excel se descarta antes de consultar SASA/SIGA.
- Para cada RFE seleccionado se consultan SASA y SIGA; SIGED permanece deshabilitado.
- Los checkpoints y salidas incluyen las zonas en el `run_id` para no mezclar corridas con filtros distintos.
- Los errores transitorios no se consideran definitivos: se reintentan al reanudar y una vez al final de la corrida.
