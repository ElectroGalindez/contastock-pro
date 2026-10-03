"""Revisa las deudas pendientes y dice cuales quedaron mal por el bug del saldo.

Motivo: add_debt guardaba los detalles por producto al precio total de la venta,
pero la deuda se creaba por el saldo (lo que faltaba pagar). Con una venta de
500 y 200 pagados, la deuda valia 300 y sus productos 500, asi que al pagar los
300 sobraba 200 y la deuda nunca pasaba a "pagada".

Este script NO modifica nada. Solo informa. Para arreglar de verdad hay que
decidir caso por caso, porque en las deudas viejas el detalle ya no dice con
certeza cuanto faltaba:

    python3 scripts/revisar_deudas_pendientes.py

Con --aplicar marca como pagadas solo las deudas que no tienen nada pendiente
(los dust de céntimos y los sobrepagos), que es lo único que se puede decidir
sin adivinar. El resto lo deja como está y lo lista para revisarlo a mano.
"""
import sys
from pathlib import Path

# En este intérprete os.path no viene cargado como atributo, asi que se usa
# pathlib para ubicar la raíz del proyecto.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text  # noqa: E402

from backend.db import engine  # noqa: E402

TOLERANCIA = 0.01


def clasificar():
    with engine.connect() as conn:
        filas = conn.execute(text("""
            SELECT d.id, d.cliente_id, c.nombre, d.monto_total, d.estado,
                   COALESCE(SUM(CASE WHEN dd.estado = 'pendiente'
                                     THEN dd.cantidad * dd.precio_unitario END), 0)
                       AS detalle_pendiente,
                   (SELECT COALESCE(SUM(p.monto_pagado), 0)
                      FROM pagos_deudas p WHERE p.deuda_id = d.id) AS pagado
            FROM deudas d
            JOIN clientes c ON c.id = d.cliente_id
            LEFT JOIN deudas_detalle dd ON dd.deuda_id = d.id
            WHERE d.estado = 'pendiente'
            GROUP BY d.id, d.cliente_id, c.nombre, d.monto_total, d.estado
            ORDER BY d.id DESC
        """)).mappings().all()

    saldadas, a_evaluar = [], []
    for f in filas:
        pendiente = float(f["detalle_pendiente"] or 0)
        total = float(f["monto_total"] or 0)
        pagado = float(f["pagado"] or 0)

        if pendiente <= TOLERANCIA:
            # no le queda nada por producto: solo dust o sobrepago
            saldo_real = total - pagado
            if saldo_real <= TOLERANCIA:
                saldadas.append((f, saldo_real))
                continue
        if abs(pendiente - total) <= TOLERANCIA:
            continue                       # esta bien: pendiente == lo que debe
        a_evaluar.append(f)

    return saldadas, a_evaluar


def mostrar(saldadas, a_evaluar):
    print(f"\n{'=' * 78}")
    print(f"Deudas que ya estan saldadas pero siguen marcadas como pendientes: "
          f"{len(saldadas)}")
    print("=" * 78)
    print(f"{'id':>6}  {'cliente':<26} {'deuda':>10} {'pagado':>10} {'sobra':>8}")
    for f, sobra in saldadas:
        print(f"{f['id']:>6}  {(f['nombre'] or '')[:26]:<26} "
              f"{float(f['monto_total'] or 0):>10.2f} {float(f['pagado'] or 0):>10.2f} "
              f"{sobra:>8.2f}")

    print(f"\n{'=' * 78}")
    print(f"Deudas con detalle y monto_total que no coinciden: {len(a_evaluar)}")
    print("=" * 78)
    print("Estas NO se tocan: no hay forma de saber cuanto falta realmente.")
    print(f"{'id':>6}  {'cliente':<26} {'deuda':>10} {'detalle':>10} {'pagado':>10}")
    for f in a_evaluar:
        print(f"{f['id']:>6}  {(f['nombre'] or '')[:26]:<26} "
              f"{float(f['monto_total'] or 0):>10.2f} "
              f"{float(f['detalle_pendiente'] or 0):>10.2f} "
              f"{float(f['pagado'] or 0):>10.2f}")


def aplicar(saldadas):
    with engine.begin() as conn:
        for f, _ in saldadas:
            conn.execute(text(
                "UPDATE deudas SET estado='pagada' WHERE id=:i AND estado='pendiente'"
            ), {"i": f["id"]})
            if f["venta_id"]:
                conn.execute(text(
                    "UPDATE ventas SET pagado = total WHERE id = :v"
                ), {"v": f["venta_id"]})
    print(f"\nMarcadas como pagadas: {len(saldadas)}")


def main():
    saldadas, a_evaluar = clasificar()
    mostrar(saldadas, a_evaluar)

    if "--aplicar" in sys.argv:
        if not input("\nAplicar? (si/no): ").strip().lower().startswith("s"):
            print("Cancelado.")
            return
        aplicar(saldadas)
    else:
        print("\nSolo informe. Usa --aplicar para marcar como pagadas las "
              f"{len(saldadas)} que ya estan saldadas.")


if __name__ == "__main__":
    main()
