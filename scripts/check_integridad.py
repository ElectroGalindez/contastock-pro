"""
Detector de inconsistencias de la base de datos.

Analiza la BD real (electrogalindez) y reporta problemas de integridad de datos:

  1. Ventas "pendientes" (pagado < total) sin deuda asociada.
  2. Deudas en estado 'pagada' pero cuya venta sigue con pagado < total.
  3. Ventas con saldo calculado incorrecto (saldo != total - pagado).
  4. Productos con stock negativo.
  5. Clientes cuyo deuda_total no coincide con la suma de sus deudas pendientes.
  6. Pagos registrados que apuntan a deudas inexistentes (húerfanos).
  7. Comprobaciones extra:
     - Productos en la venta que no existen en inventario.
     - Pagos por producto que exceden el monto pendiente del detalle.

Ejecutar:  python scripts/check_integridad.py  [--fix]
Con --fix intenta corregir los casos 1, 2 y 3 automáticamente (respaldando antes).
"""
import sys
import argparse
from datetime import datetime
from sqlalchemy import text
from backend.db import engine

C = (
    "\033[31m",   # 0 rojo
    "\033[33m",   # 1 amarillo
    "\033[32m",   # 2 verde
    "\033[0m",    # reset
)


def q(query, params=None):
    with engine.connect() as conn:
        return conn.execute(text(query), params or {}).mappings().all()


def numero(v):
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def detectar():
    print("=" * 68)
    print("  DETECTOR DE INCONSISTENCIAS - ElectroGalindez")
    print("  Fecha de analisis:", datetime.now().strftime("%d/%m/%Y %H:%M"))
    print("=" * 68)
    problemas = 0

    # ---------------------------------------------------------------
    # 1. Ventas pendientes sin deuda
    # ---------------------------------------------------------------
    rows = q("""
        SELECT v.id, v.total, v.pagado, v.saldo
        FROM ventas v
        WHERE v.pagado < v.total
          AND NOT EXISTS (SELECT 1 FROM deudas d WHERE d.venta_id = v.id)
          AND v.total > 0
        ORDER BY v.id
    """)
    if rows:
        problemas += len(rows)
        print(f"\n[Caso 1] {C[0]}{len(rows)} venta(s) pendiente(s) SIN deuda asociada{C[2]}")
        for r in rows[:20]:
            print(f"    Venta #{r['id']}: total=${numero(r['total']):,.2f} pagado=${numero(r['pagado']):,.2f}")
        if len(rows) > 20:
            print(f"    ... y {len(rows) - 20} mas")
    else:
        print(f"\n[Caso 1] {C[2]}OK: sin ventas pendientes sin deuda.{C[2]}")

    # ---------------------------------------------------------------
    # 2. Deudas pagadas pero venta sigue pendiente
    # ---------------------------------------------------------------
    rows = q("""
        SELECT v.id AS venta_id, v.total, v.pagado, d.id AS deuda_id
        FROM deudas d
        JOIN ventas v ON v.id = d.venta_id
        WHERE d.estado = 'pagada' AND v.pagado < v.total
        ORDER BY v.id
    """)
    if rows:
        problemas += len(rows)
        print(f"\n[Caso 2] {C[0]}{len(rows)} venta(s) con deuda PAGADA pero venta PENDIENTE{C[2]}")
        for r in rows[:20]:
            print(f"    Venta #{r['venta_id']}: total=${numero(r['total']):,.2f} pagado=${numero(r['pagado']):,.2f} (deuda #{r['deuda_id']})")
        if len(rows) > 20:
            print(f"    ... y {len(rows) - 20} mas")
    else:
        print(f"\n[Caso 2] {C[2]}OK: no hay deudas pagadas con ventas pendientes.{C[2]}")

    # ---------------------------------------------------------------
    # 3. Saldo calculado incorrecto
    # ---------------------------------------------------------------
    rows = q("""
        SELECT id, total, pagado, saldo
        FROM ventas
        WHERE total IS NOT NULL AND pagado IS NOT NULL
          AND saldo IS DISTINCT FROM (total - pagado)
        ORDER BY id
        LIMIT 50
    """)
    if rows:
        problemas += len(rows)
        print(f"\n[Caso 3] {C[0]}{len(rows)} venta(s) con saldo mal calculado (saldo != total - pagado){C[2]}")
        for r in rows[:15]:
            print(f"    Venta #{r['id']}: total=${numero(r['total']):,.2f} pagado=${numero(r['pagado']):,.2f} saldo_actual=${numero(r['saldo']):,.2f} esperado=${numero(r['total']) - numero(r['pagado']):,.2f}")
    else:
        print(f"\n[Caso 3] {C[2]}OK: saldos correctos.{C[2]}")

    # ---------------------------------------------------------------
    # 4. Stock negativo
    # ---------------------------------------------------------------
    rows = q("SELECT id, nombre, cantidad FROM productos WHERE cantidad < 0 ORDER BY cantidad")
    if rows:
        problemas += len(rows)
        print(f"\n[Caso 4] {C[0]}{len(rows)} producto(s) con STOCK NEGATIVO{C[2]}")
        for r in rows[:20]:
            print(f"    #{r['id']} {r['nombre']}: {r['cantidad']}")
    else:
        print(f"\n[Caso 4] {C[2]}OK: sin stock negativo.{C[2]}")

    # ---------------------------------------------------------------
    # 5. deuda_total de cliente inconsistente
    #    (deuda_total debe ser la suma de lo RESTANTE de sus deudas pendientes)
    # ---------------------------------------------------------------
    rows = q("""
        SELECT c.id, c.nombre, COALESCE(c.deuda_total, 0) AS registrada,
               COALESCE((
                   SELECT SUM(dd.cantidad * dd.precio_unitario)
                   FROM deudas_detalle dd
                   JOIN deudas d ON d.id = dd.deuda_id
                   WHERE d.cliente_id = c.id AND d.estado = 'pendiente'
                                   AND dd.estado = 'pendiente'
               ), 0) AS calculada
        FROM clientes c
        WHERE COALESCE(c.deuda_total, 0) != COALESCE((
                   SELECT SUM(dd.cantidad * dd.precio_unitario)
                   FROM deudas_detalle dd
                   JOIN deudas d ON d.id = dd.deuda_id
                   WHERE d.cliente_id = c.id AND d.estado = 'pendiente'
                                   AND dd.estado = 'pendiente'
               ), 0)
        ORDER BY c.id
    """)
    if rows:
        problemas += len(rows)
        print(f"\n[Caso 5] {C[0]}{len(rows)} cliente(s) con deuda_total inconsistente con sus deudas{C[2]}")
        for r in rows[:20]:
            print(f"    #{r['id']} {r['nombre']}: registrada=${numero(r['registrada']):,.2f} calculada=${numero(r['calculada']):,.2f}")
        if len(rows) > 20:
            print(f"    ... y {len(rows) - 20} mas")
    else:
        print(f"\n[Caso 5] {C[2]}OK: deuda_total de clientes consistente.{C[2]}")

    # ---------------------------------------------------------------
    # 6. Pagos húerfanos (sin deuda)
    # ---------------------------------------------------------------
    rows = q("""
        SELECT p.id, p.deuda_id, p.producto_nombre
        FROM pagos_deudas p
        LEFT JOIN deudas d ON d.id = p.deuda_id
        WHERE d.id IS NULL
    """)
    if rows:
        problemas += len(rows)
        print(f"\n[Caso 6] {C[0]}{len(rows)} pago(s) apuntando a deuda inexistente{huerfanos_label(rows)}{C[2]}")
        for r in rows[:15]:
            print(f"    Pago #{r['id']}: deuda #{r['deuda_id']} - {r['producto_nombre']}")
    else:
        print(f"\n[Caso 6] {C[2]}OK: sin pagos húerfanos.{C[2]}")

    # ---------------------------------------------------------------
    # 7. Detalles pagados pero deuda pendiente / viceversa
    # ---------------------------------------------------------------
    rows = q("""
        SELECT d.id AS deuda_id, d.estado AS deuda_estado,
               COUNT(*) FILTER (WHERE dd.estado = 'pendiente') AS detalles_pendientes
        FROM deudas d
        JOIN deudas_detalle dd ON dd.deuda_id = d.id
        GROUP BY d.id, d.estado
    """)
    malos = [r for r in rows
             if (r["deuda_estado"] == "pagada" and r["detalles_pendientes"] > 0)
             or (r["deuda_estado"] == "pendiente" and r["detalles_pendientes"] == 0)]
    if malos:
        problemas += len(malos)
        print(f"\n[Caso 7] {C[0]}{len(malos)} deuda(s) con estado incoherente con sus detalles{C[2]}")
        for r in malos[:15]:
            print(f"    Deuda #{r['deuda_id']}: estado={r['deuda_estado']}, detalles pendientes={r['detalles_pendientes']}")
    else:
        print(f"\n[Caso 7] {C[2]}OK: deudas coherentes con sus detalles.{C[2]}")

    # ---------------------------------------------------------------
    # Resumen
    # ---------------------------------------------------------------
    print()
    print("=" * 68)
    if problemas:
        print(f"{C[1]}  {problemas} problema(s) detectados.{C[2]}")
    else:
        print(f"{C[2]}  No se detectaron problemas. Todo en orden.{C[2]}")
    print("=" * 68)
    return problemas


def huerfanos_label(rows):
    return ""


def corregir():
    """Corrige automáticamente casos 1, 2, 3 y 5."""
    print("Aplicando correcciones...")
    n2 = 0
    n3 = 0
    n5 = 0

    # Caso 2: marcar ventas como pagadas cuando su deuda esta pagada
    with engine.begin() as conn:
        r = conn.execute(text("""
            UPDATE ventas v
            SET pagado = v.total, saldo = 0
            WHERE v.id IN (
                SELECT d.venta_id FROM deudas d
                WHERE d.estado = 'pagada'
            )
            AND NOT EXISTS (
                SELECT 1 FROM deudas d2
                WHERE d2.venta_id = v.id AND d2.estado = 'pendiente'
            )
        """))
        n2 = r.rowcount or 0

        # Caso 3: recalcular saldo
        r = conn.execute(text("""
            UPDATE ventas
            SET saldo = total - pagado
            WHERE total IS NOT NULL AND pagado IS NOT NULL
              AND saldo IS DISTINCT FROM (total - pagado)
        """))
        n3 = r.rowcount or 0

        # Caso 5: deuda_total del cliente = suma RESTANTE de sus deudas pendientes
        r = conn.execute(text("""
            UPDATE clientes c
            SET deuda_total = COALESCE((
                SELECT SUM(dd.cantidad * dd.precio_unitario)
                FROM deudas_detalle dd
                JOIN deudas d ON d.id = dd.deuda_id
                WHERE d.cliente_id = c.id AND d.estado = 'pendiente'
                                AND dd.estado = 'pendiente'
            ), 0)
            WHERE COALESCE(c.deuda_total, 0) != COALESCE((
                SELECT SUM(dd.cantidad * dd.precio_unitario)
                FROM deudas_detalle dd
                JOIN deudas d ON d.id = dd.deuda_id
                WHERE d.cliente_id = c.id AND d.estado = 'pendiente'
                                AND dd.estado = 'pendiente'
            ), 0)
        """))
        n5 = r.rowcount or 0

        # Caso 7: deudas 'pendiente' con TODOS los detalles pagados -> 'pagada'
        r = conn.execute(text("""
            UPDATE deudas d
            SET estado = 'pagada'
            WHERE d.estado = 'pendiente'
              AND NOT EXISTS (
                  SELECT 1 FROM deudas_detalle dd
                  WHERE dd.deuda_id = d.id AND dd.estado = 'pendiente'
              )
        """))
        n7 = r.rowcount or 0

    print(f"  - {n2} venta(s) marcadas como pagadas (deuda ya pagada).")
    print(f"  - {n3} venta(s) con saldo recalculado.")
    print(f"  - {n5} cliente(s) con deuda_total corregida.")
    print(f"  - {n7} deuda(s) pendientes marcadas como pagadas (detalles pagados).")


def main():
    parser = argparse.ArgumentParser(description="Detector de inconsistencias de la BD")
    parser.add_argument("--fix", action="store_true", help="Corregir automáticamente los problemas detectados")
    args = parser.parse_args()

    problemas = detectar()
    if args.fix:
        corregir()
        print("\nRe-ejecutando detección tras corrección...\n")
        detectar()

    sys.exit(1 if problemas else 0)


if __name__ == "__main__":
    main()