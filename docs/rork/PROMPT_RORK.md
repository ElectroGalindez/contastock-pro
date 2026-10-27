# Prompt para Rork — ContaStock Pro Dashboard

Copiá todo el bloque de abajo y pegalo como primer mensaje en un proyecto nuevo de Rork.

---

Crea una app móvil (iPhone, iPad y Android, y que también funcione en Mac) llamada
**"ContaStock Pro"**: es el panel de solo lectura de la app de contabilidad e
inventario de nuestro almacén. Toda la interfaz en **español**. La app NO crea ni
edita datos: solo los muestra.

## Pantallas

1. **Inicio de sesión**: logo, campo usuario y contraseña, botón "Entrar".
   Guarda la sesión de forma segura (SecureStore) y permite activar Face ID / Touch ID
   para entrar después.
2. **Panel (pantalla principal)** con pull-to-refresh y la hora de "Última
   actualización" arriba:
   - 3 tarjetas grandes con degradado:
     - **Ventas hoy** (azul) — monto + "N ventas"
     - **Ventas del mes** (verde)
     - **Deudas pendientes** (rojo) — monto + "N clientes con deuda"
   - 3 tarjetas pequeñas: **Productos**, **Clientes**, **Stock bajo** (este último en
     naranja si es mayor que 0; al tocarlo abre la lista de stock bajo).
   - Gráfico de barras **Ventas últimos 7 días**.
   - Gráfico de línea **Ventas mensuales** (últimos 12 meses).
   - Lista **Top 5 productos más vendidos** del mes.
3. **Stock bajo**: lista de productos con cantidad ≤ 5 (nombre, categoría, cantidad).
4. **Deudas**: lista de clientes con deuda pendiente ordenada de mayor a menor, con
   buscador.
5. **Ajustes**: URL del servidor, cerrar sesión, versión.

Navegación con tabs abajo: Panel · Stock · Deudas · Ajustes.

## Datos

Todo viene de una API REST (la URL base se configura en Ajustes). Mientras no haya
API, usa datos de ejemplo con exactamente esta forma:

`POST /api/movil/login` → body `{ "usuario": "...", "password": "..." }` →
`{ "token": "...", "nombre": "...", "rol": "admin" }`

Todas las demás llamadas llevan `Authorization: Bearer <token>`.

`GET /api/movil/dashboard` →
```json
{
  "actualizado": "2026-10-27T13:00:00",
  "ventas_hoy": 1250.50,
  "ventas_hoy_count": 14,
  "ventas_mes": 38420.00,
  "deuda_total": 9310.75,
  "clientes_con_deuda": 23,
  "total_productos": 412,
  "total_clientes": 186,
  "stock_bajo": 7,
  "ventas_7_dias": [{ "dia": "2026-10-21", "total": 980.0 }],
  "ventas_por_mes": [{ "mes": "2026-10", "total": 38420.0 }],
  "top_productos": [{ "nombre": "Cable 2.5mm", "cantidad": 120, "total": 3600.0 }]
}
```

`GET /api/movil/stock-bajo` → `[{ "nombre": "...", "categoria": "...", "cantidad": 3 }]`

`GET /api/movil/deudas` → `[{ "cliente": "...", "monto": 450.0, "desde": "2026-09-02" }]`

Si la API responde 401, vuelve al login. Si no hay conexión, muestra los últimos
datos guardados en caché con un aviso "Sin conexión — datos de las HH:MM".

## Diseño

- Moderno, limpio, tipo app bancaria. Soporta modo claro y oscuro.
- Colores: primario `#2563eb`, fondo `#f1f5f9`, tarjetas `#ffffff`,
  barra oscura `#0f172a`, verde `#16a34a`, rojo `#dc2626`, naranja `#f59e0b`.
- Montos con formato `$1,250.50`. Fechas en formato `27/10/2026`.
- En pantallas anchas (iPad / Mac) las tarjetas se ponen en una grilla de 3 columnas y
  los dos gráficos lado a lado.
