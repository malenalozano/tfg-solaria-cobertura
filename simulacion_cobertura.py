"""
Simulación horaria: ¿qué parte del consumo de un centro de datos cubren
el sol y las baterías del contrato Solaria–Merlin 2? (Anexo 1, Figura 4)

Uso:  python simulacion_cobertura.py
Salida: resultados por pantalla, resultados/cobertura_horaria_2025.csv
        y resultados/figura4_junio_diciembre.png
"""
import csv
import math
import datetime as dt
from pathlib import Path

# ---------------------------------------------------------------- Supuestos
ANIO = 2025
SOLAR_MW = 426            # potencia máxima de la planta (contrato Merlin 2)
HORAS_EQUIVALENTES = 1915 # Solaria: producción anual = potencia × 1.915 h
BATERIA_MWH = 600         # capacidad de las baterías (contrato Merlin 2)
BATERIA_MW = 150          # potencia de carga/descarga (supuesto: baterías de 4 h)
RENDIMIENTO = 0.90        # de cada 100 MWh guardados se devuelven 90
CONEXION_MW = 213         # acceso y conexión del centro (contrato Merlin 2)
MW_CONEXION_POR_MW_IT = 1.5   # apartado 4.1
EXTRA_REFRIGERACION = 0.15    # máximo del proyecto de real decreto (PUE 1,15)
UMBRAL_HORA = 0.80        # exigencia del 80 % en cada hora (proyecto de RD)
LAT, LON = 40.4, -3.7     # punto de referencia para saber si hay sol (Madrid)

CONSUMO_MW = CONEXION_MW / MW_CONEXION_POR_MW_IT * (1 + EXTRA_REFRIGERACION)  # ≈ 163,3

RAIZ = Path(__file__).parent
DATOS = RAIZ / "datos" / "datos_horarios_2022_2026.csv"
SALIDA = RAIZ / "resultados"


# ---------------------------------------------------------------- Altura del sol
def altura_sol(t_utc: dt.datetime) -> float:
    """Altura del sol sobre el horizonte (grados), fórmulas de la NOAA."""
    n = t_utc.timetuple().tm_yday
    h = t_utc.hour + t_utc.minute / 60
    g = 2 * math.pi / 365 * (n - 1 + (h - 12) / 24)
    dec = (0.006918 - 0.399912 * math.cos(g) + 0.070257 * math.sin(g)
           - 0.006758 * math.cos(2 * g) + 0.000907 * math.sin(2 * g)
           - 0.002697 * math.cos(3 * g) + 0.00148 * math.sin(3 * g))
    eot = 229.18 * (0.000075 + 0.001868 * math.cos(g) - 0.032077 * math.sin(g)
                    - 0.014615 * math.cos(2 * g) - 0.040849 * math.sin(2 * g))
    ang_horario = math.radians((h * 60 + eot + 4 * LON) / 4 - 180)
    lat = math.radians(LAT)
    return math.degrees(math.asin(math.sin(lat) * math.sin(dec)
                                  + math.cos(lat) * math.cos(dec) * math.cos(ang_horario)))


def desfase_utc(dia: dt.date) -> int:
    """Hora peninsular: UTC+2 en verano (30/3–25/10/2025), UTC+1 el resto."""
    return 2 if dt.date(2025, 3, 30) <= dia < dt.date(2025, 10, 26) else 1


# ---------------------------------------------------------------- 1. Datos
filas = []
with open(DATOS, newline="") as f:
    for r in csv.DictReader(f):
        if int(r["anio"]) == ANIO:
            filas.append((dt.date.fromisoformat(r["fecha"]), int(r["hora"]),
                          int(r["mes"][5:]), float(r["solar_mwh"])))
assert len(filas) == 8760, f"se esperaban 8760 horas y hay {len(filas)}"

# ---------------------------------------------------------------- 2. Quitar la termosolar nocturna
# La serie de REE incluye la termosolar, que produce de noche con su almacenamiento.
# La planta es fotovoltaica: las horas con el sol bajo el horizonte se ponen a cero.
solar_es = []
for dia, hora, _, mwh in filas:
    inicio_hora = dt.datetime.combine(dia, dt.time()) + dt.timedelta(hours=hora - 1)
    hay_sol = altura_sol(inicio_hora - dt.timedelta(hours=desfase_utc(dia))) > 0
    solar_es.append(mwh if hay_sol else 0.0)
quitado = 1 - sum(solar_es) / sum(f[3] for f in filas)

# ---------------------------------------------------------------- 3. Escalar a la planta
# Se reparten los 426 × 1.915 MWh del año con el mismo perfil horario que España.
energia_anual = SOLAR_MW * HORAS_EQUIVALENTES
k = energia_anual / sum(solar_es)
planta = [x * k for x in solar_es]

# ---------------------------------------------------------------- 4. Simulación hora a hora
def simular(cap_mwh: float, pot_mw: float):
    """Devuelve, para cada hora: (sol directo, baterías, red, vertido) en MW."""
    ef = math.sqrt(RENDIMIENTO)  # pérdidas repartidas entre carga y descarga
    soc = 0.0                    # energía dentro de la batería (empieza vacía)
    out = []
    for g in planta:
        directo = min(g, CONSUMO_MW)
        sobra = g - directo
        falta = CONSUMO_MW - directo
        carga = min(sobra, pot_mw, (cap_mwh - soc) / ef) if cap_mwh else 0.0
        soc += carga * ef
        descarga = min(falta, pot_mw, soc * ef) if cap_mwh else 0.0
        soc -= descarga / ef
        out.append((directo, descarga, falta - descarga, sobra - carga))
    return out


def resumen(res):
    cubierto = [d + b for d, b, _, _ in res]
    total = CONSUMO_MW * len(res)
    def mes(m):
        idx = [i for i, f in enumerate(filas) if f[2] == m]
        return sum(cubierto[i] for i in idx) / (CONSUMO_MW * len(idx))
    return {
        "anual": sum(cubierto) / total,
        "junio": mes(6),
        "diciembre": mes(12),
        "horas_80": sum(c >= UMBRAL_HORA * CONSUMO_MW - 1e-9 for c in cubierto) / len(res),
        "vertido_gwh": sum(v for *_, v in res) / 1000,
    }


solo_sol = resumen(simular(0, 0))
res = simular(BATERIA_MWH, BATERIA_MW)
con_bat = resumen(res)

# ---------------------------------------------------------------- 5. Resultados
print(f"Consumo del centro: {CONSUMO_MW:.1f} MW constantes")
print(f"Energía solar nocturna (termosolar) quitada: {quitado:.1%}")
print(f"Producción de la planta: {energia_anual/1000:,.0f} GWh/año")
print()
print(f"{'':28}{'Solo sol':>10}{'Sol + bat.':>12}")
for clave, nombre in [("anual", "Cobertura anual"), ("junio", "Junio"),
                      ("diciembre", "Diciembre"), ("horas_80", "Horas con >= 80 % cubierto")]:
    print(f"{nombre:28}{solo_sol[clave]:>10.1%}{con_bat[clave]:>12.1%}")
print(f"{'Sol perdido (GWh)':28}{solo_sol['vertido_gwh']:>10.0f}{con_bat['vertido_gwh']:>12.0f}")

SALIDA.mkdir(exist_ok=True)
with open(SALIDA / "cobertura_horaria_2025.csv", "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["fecha", "hora", "solar_planta_mw", "sol_directo_mw",
                "baterias_mw", "red_mw", "sol_perdido_mw"])
    for (dia, hora, _, _), g, (d, b, r, v) in zip(filas, planta, res):
        w.writerow([dia.isoformat(), hora] + [round(x, 2) for x in (g, d, b, r, v)])

# Figura 4: media de cada hora en junio y diciembre
try:
    import matplotlib.pyplot as plt
    fig, ejes = plt.subplots(1, 2, figsize=(12, 4.5), sharey=True)
    for ax, m, titulo in [(ejes[0], 6, "Junio"), (ejes[1], 12, "Diciembre")]:
        medias = []
        for h in range(1, 25):
            idx = [i for i, f in enumerate(filas) if f[2] == m and f[1] == h]
            medias.append([sum(res[i][j] for i in idx) / len(idx) for j in range(3)])
        x = list(range(24))
        d, b, r = zip(*medias)
        ax.stackplot(x, d, b, r, labels=["Sol directo", "Baterías", "Red u otra fuente"],
                     colors=["#F2B134", "#2E86AB", "#C9CED6"])
        ax.set_title(f"{titulo}: {con_bat['junio' if m == 6 else 'diciembre']:.0%} cubierto")
        ax.set_xlabel("Hora")
        ax.set_xlim(0, 23)
    ejes[0].set_ylabel("MW")
    ejes[0].legend(loc="upper left")
    fig.tight_layout()
    fig.savefig(SALIDA / "figura4_junio_diciembre.png", dpi=150)
    print(f"\nFigura guardada en {SALIDA / 'figura4_junio_diciembre.png'}")
except ImportError:
    print("\n(matplotlib no instalado: no se genera la figura)")
