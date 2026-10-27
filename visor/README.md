# ContaStock Visor

Dashboard de **solo lectura** para el teléfono y la Mac: ventas del día,
inventario, deudas pendientes y gráfica de los últimos 30 días. Lee la copia
que la PC del almacén sube a Neon (ver `backend/sync_neon.py`).

- No puede modificar nada: no tiene formularios de cambio y cada consulta corre
  en una transacción `READ ONLY` de Postgres.
- Por defecto solo entran usuarios con rol `admin` (los mismos de la app).
- Lee Neon al momento cada vez que se abre o se vuelve a la app, y cada
  minuto mientras está a la vista. Arriba
  muestra hace cuánto se conectó el almacén por última vez.

## Publicarlo en internet (Render, gratis)

1. Subí el repo a GitHub (ya está en `vicent-cao02/contastock-pro`).
2. En [render.com](https://render.com): **New → Blueprint** → elegí el repo.
   Render lee `render.yaml` y crea el servicio `contastock-visor`.
3. Cuando pida `VISOR_DATABASE_URL`, pegá la URL de Neon.
4. Al terminar da una dirección tipo `https://contastock-visor.onrender.com`.

En el plan gratis el servicio se duerme tras 15 min sin uso; la primera
apertura después tarda unos 30–60 s.

## Instalarlo como app

- **iPhone (Safari):** abrir la dirección → Compartir → *Agregar a inicio*.
- **Android (Chrome):** menú ⋮ → *Instalar app*.
- **Mac (Safari 17+):** abrir la dirección → Archivo → *Agregar al Dock*.
  En Chrome: menú ⋮ → *Transmitir, guardar y compartir* → *Instalar página como app*.

Queda con su icono y abre en su propia ventana, sin barra del navegador.

## Probar en la computadora

```bash
pip install -r visor/requirements.txt
VISOR_DATABASE_URL="postgresql://..." python visor/app.py   # http://127.0.0.1:5600
```

Sin `VISOR_DATABASE_URL` usa `NEON_DATABASE_URL` del `.env` del proyecto.

## Variables

| Variable | Para qué | Por defecto |
|---|---|---|
| `VISOR_DATABASE_URL` | URL de Neon | — (obligatoria) |
| `VISOR_SECRET_KEY` | firma la sesión; en Render se genera sola | aleatoria |
| `VISOR_ROLES` | roles que pueden entrar, separados por coma | `admin` |
| `VISOR_TZ` | zona horaria del almacén (qué es "hoy") | `America/Havana` |
