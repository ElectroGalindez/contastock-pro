# ContaStock Pro

> Sistema profesional de contabilidad, ventas e inventario para almacenes.

Gestione inventario, registre ventas, controle deudas de clientes, genere
facturas PDF y reportes Excel. Disponible como **app de escritorio**
(macOS y Windows), como **PWA instalable en el teléfono** y como **aplicación web**.

## Funcionalidades

- Inventario con categorías y alertas de stock bajo
- Registro de ventas con descuento automático de stock y factura PDF (2 en 1)
- Deudas por cliente con pagos parciales, comprobantes PDF y estados sincronizados
- Panel general con KPIs y gráficas (ventas hoy, mes, deudas, top productos)
- Usuarios con roles (admin / empleado), bloqueo por intentos y auditoría completa
- Exportaciones a Excel (inventario, ventas por día, deudas)
- Base de datos **portátil (SQLite)** que viaja con la app — sin servidores
  (compatible también con PostgreSQL/Neon configurando `.env`)

## Instalación (desktop)

### macOS
1. Descargá `ContaStockPro-1.0.0-macos.dmg` desde la *release*.
2. Abrí el DMG y arrastrá **ContaStock Pro** a Aplicaciones.
3. La primera vez hacé clic derecho → *Abrir* (Gatekeeper) y confirmá.

### Windows
1. Descargá `ContaStockPro-setup.exe` desde la *release*.
2. Ejecutalo y seguí el asistente de instalación.
3. Ajustá las credenciales del usuario inicial en `Usuarios`.

Los datos se guardan en `Documentos` / carpeta de datos de usuario; se puede
**copiar la carpeta ContaStockPro de un equipo a otro** para llevar la base de datos.

## App de teléfono (PWA)

No hace falta instalar nada desde una tienda:

1. Ejecutá la app en la red local:
   `python3 app.py` (por defecto `http://127.0.0.1:5555`).
   Para usarla desde el teléfono: `python3 app.py --host 0.0.0.0`.
2. En el teléfono, abrí la dirección de la red (ej. `http://192.168.1.10:5555`).
3. Buscá **"Agregar a pantalla de inicio"** (Chrome: menú → *Añadir a pantalla de inicio*,
   Safari: *Compartir* → *Agregar a pantalla de inicio*).
4. Se instalará con su icono y abrirá a pantalla completa.

> Nota: el service worker (instalación PWA) requiere HTTPS o `localhost`.
> Para uso en la red local podés agregar un certificado local (mkcert) o usar
> la PWA desde `https://localhost`.

## Desarrollo

```bash
pip install -r requirements.txt
python app.py                 # aplicación web en http://127.0.0.1:5555
python desktop.py             # ventana de escritorio (pywebview)
python -m pytest              # pruebas (SQLite portátil)
```

Requisitos: Python 3.10+.
Las variables de base de datos se leen de `.env` (opcional). Sin `.env`, la app
usa **SQLite portátil** automáticamente en su carpeta de datos.

## Empaquetar instaladores

### macOS (.dmg)
```bash
bash packaging/build_mac.sh
```

### Windows (.exe con instalador)
```bash
packaging\build_windows.bat   # requiere Inno Setup 6
```

### Automático (GitHub Actions)
Al crear un tag `v*` se compilan y publican automáticamente los instaladores
de macOS y Windows como *release* en GitHub.

## Actualizaciones futuras

La app está preparada para evolucionar:

- **Versión única** en `backend/app_meta.py` (nombre y versión).
- **Esquema declarado** con SQLAlchemy en `backend/db.py` (migraciones con Alembic en el futuro).
- **Configuración central** en `backend/config.py` (directorio de datos, secret key, BD).
- **Paquetes por plataforma** generados desde `packaging/` con el mismo spec.

## Licencia

Uso interno / comercial. © 2025-2026 ElectroGalindez.