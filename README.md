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
- Base de datos **portátil (SQLite)** que viaja con la app — sin servidores.
  La app usa SQLite por defecto; `LOCAL_DATABASE_URL` permite apuntar a
  PostgreSQL si hiciera falta. Neon quedó solo para traer datos a local
  (ver *Migración desde Neon*).

## Instalación (desktop)

### macOS
1. Descargá `ContaStockPro-1.0.0-macos.dmg` desde la *release*.
2. Abrí el DMG y arrastrá **ContaStock Pro** a Aplicaciones.
3. La primera vez hacé clic derecho → *Abrir* (Gatekeeper) y confirmá.

### Windows
1. Descargá `ContaStockPro-setup.exe` desde la *release*.
2. Ejecutalo y seguí el asistente de instalación.
3. Ajustá las credenciales del usuario inicial en `Usuarios`.

#### Probar en otra PC con Windows (desde el código, sin instalador)
Para una prueba rápida en una PC con Windows (p. ej. la del negocio) sin
compilar nada:

1. **Copiá la carpeta del proyecto** completa (incluido `.env` con la URL de
   Neon) a la PC de Windows, o cloná el repositorio y creá `.env` con la línea
   `NEON_DATABASE_URL=...`.
2. En esa PC instalá **Python 3.10+** desde python.org (marcá
   *"Add python.exe to PATH"*).
3. Hacé doble clic en **`instalar_windows.bat`**. Él solo:

   - crea el entorno virtual `.venv` e instala las dependencias;
   - **baja los datos desde Neon** a la base SQLite local
     (`scripts\migrar_neon_a_local.py`);
   - abre la ventana de escritorio.

4. Ingresá con **admin / admin1234**, cambiá la contraseña y verificá que
   aparezcan clientes, ventas y deudas (se copian ~20.000 registros).

> Una vez verificada la migración podés borrar `NEON_DATABASE_URL` del `.env`
> en esa PC: la app sigue funcionando con la base local. Los datos también
> viajan copiando la carpeta `%APPDATA%\ContaStockPro` a otra PC.

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

Usuario inicial: **admin / admin1234** (se crea si no existe; cambiá la
contraseña desde *Usuarios*).

### Migración desde Neon

Para traer los datos de Neon a la base SQLite local:

```bash
python scripts/migrar_neon_a_local.py
```

Requiere `NEON_DATABASE_URL` en `.env`. Copia todas las tablas y deja el
usuario `admin/admin1234`. Una vez verificada la migración, borrá la conexión
a Neon del `.env`.

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