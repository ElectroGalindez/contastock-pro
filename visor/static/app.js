// ContaStock Visor: pide los datos a /api y los pinta. Se actualiza solo
// cada minuto mientras la pantalla está a la vista.
(function () {
    'use strict';

    var REFRESCO_MS = 60000;   // con la app a la vista; al abrirla se lee al instante
    var VIEJO_MIN = 150;  // el almacén avisa cada 2 h aunque no haya cambios
    var vista = 'resumen';
    var cache = {};
    var grafica = null;
    var filtroInv = 'todos';

    var $ = function (id) { return document.getElementById(id); };
    // mismo formato que la app del almacén: $1,234.00
    var dinero = new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' });
    var redondo = new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 0 });
    var corto = new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', notation: 'compact' });
    var num = new Intl.NumberFormat('en-US', { maximumFractionDigits: 2 });
    function $$(v) { return dinero.format(v || 0); }
    // en las tarjetas los centavos de cifras grandes no aportan y parten la línea
    function $k(v) { return Math.abs(v || 0) >= 1000 ? redondo.format(v) : dinero.format(v || 0); }
    // tarjetas angostas (3 por fila): $129K
    function $c(v) { return Math.abs(v || 0) >= 10000 ? corto.format(v) : $k(v); }
    function fechaCorta(iso) { return iso ? iso.slice(8, 10) + '/' + iso.slice(5, 7) + '/' + iso.slice(0, 4) : ''; }

    function esc(t) {
        return String(t == null ? '' : t).replace(/[&<>"']/g, function (c) {
            return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
        });
    }

    function hoyISO() {
        var d = new Date();
        d.setMinutes(d.getMinutes() - d.getTimezoneOffset());
        return d.toISOString().slice(0, 10);
    }

    function hace(iso) {
        if (!iso) return null;
        var min = Math.round((Date.now() - new Date(iso).getTime()) / 60000);
        if (min < 1) return { texto: 'recién', min: 0 };
        if (min < 60) return { texto: 'hace ' + min + ' min', min: min };
        var h = Math.floor(min / 60);
        return { texto: h < 24 ? 'hace ' + h + ' h' : 'hace ' + Math.floor(h / 24) + ' d', min: min };
    }

    function pedir(url) {
        return fetch(url, { cache: 'no-store', credentials: 'same-origin' }).then(function (r) {
            if (r.status === 401) { location.href = '/login'; throw new Error('sesion'); }
            return r.json().then(function (j) {
                if (!r.ok) throw new Error(j.error || 'Error ' + r.status);
                return j;
            });
        });
    }

    function mostrarError(msg) {
        var e = $('error');
        e.hidden = !msg;
        e.textContent = msg || '';
    }

    function frescura(subida) {
        var el = $('frescura');
        var h = hace(subida);
        if (!h) { el.textContent = 'Datos del almacén'; el.className = 'muted'; return; }
        el.textContent = 'Datos del almacén · ' + h.texto;
        el.className = h.min > VIEJO_MIN ? 'aviso-viejo' : 'muted';
        el.title = h.min > VIEJO_MIN
            ? 'Puede que la PC del almacén esté apagada o sin internet.' : '';
    }

    // ---------------- Resumen ----------------
    function pintarResumen(d) {
        frescura(d.ultima_subida);
        $('k-hoy').textContent = $k(d.ventas_hoy.total);
        var dif = d.ventas_hoy.total - d.ventas_ayer.total;
        $('k-hoy-sub').textContent = d.ventas_hoy.cantidad + ' ventas · ayer ' + $$(d.ventas_ayer.total) +
            (d.ventas_ayer.total ? ' (' + (dif >= 0 ? '+' : '') + $$(dif) + ')' : '');
        $('k-cobrado').textContent = $k(d.ventas_hoy.pagado);
        $('k-fiado').textContent = $k(d.ventas_hoy.pendiente);
        $('k-mes').textContent = $k(d.ventas_mes.total);
        $('k-mes-sub').textContent = d.ventas_mes.cantidad + ' ventas';
        $('k-deuda').textContent = $k(d.deuda.total);
        $('k-deuda-sub').textContent = d.deuda.clientes + ' clientes';
        $('k-bajo').textContent = d.inventario.stock_bajo;
        $('k-agotados').textContent = d.inventario.agotados;
        $('k-agotados-sub').textContent = 'de ' + d.inventario.productos + ' productos';
        $('k-bajo-sub').textContent = $k(d.inventario.valor) + ' en stock';

        var top = d.top_mes.map(function (p) {
            return '<li><span>' + esc(p.nombre) + '</span><b>' + num.format(p.cantidad) + '</b></li>';
        }).join('');
        $('top').innerHTML = top || '<li class="vacio">Sin ventas este mes</li>';

        var etiquetas = d.serie.map(function (s) { return s.dia.slice(8, 10) + '/' + s.dia.slice(5, 7); });
        var valores = d.serie.map(function (s) { return s.total; });
        var color = getComputedStyle(document.documentElement).getPropertyValue('--primario').trim();
        var suave = getComputedStyle(document.documentElement).getPropertyValue('--suave').trim();
        if (!window.Chart) return;
        if (grafica) {
            grafica.data.labels = etiquetas;
            grafica.data.datasets[0].data = valores;
            grafica.update('none');
            return;
        }
        grafica = new Chart($('g-serie'), {
            type: 'bar',
            data: { labels: etiquetas, datasets: [{ data: valores, backgroundColor: color, borderRadius: 4 }] },
            options: {
                responsive: true, maintainAspectRatio: false, animation: false,
                plugins: { legend: { display: false },
                           tooltip: { callbacks: { label: function (c) { return $$(c.parsed.y); } } } },
                scales: {
                    x: { ticks: { color: suave, maxRotation: 0, autoSkip: true, maxTicksLimit: 8 }, grid: { display: false } },
                    y: { ticks: { color: suave, callback: function (v) { return corto.format(v); } }, beginAtZero: true }
                }
            }
        });
    }

    // ---------------- Ventas ----------------
    function pintarVentas(d) {
        $('vd-n').textContent = d.totales.cantidad;
        $('vd-total').textContent = $c(d.totales.total);
        $('vd-fiado').textContent = $c(d.totales.pendiente);
        pintarPorProducto(d.por_producto || []);
        $('lista-ventas').innerHTML = d.ventas.length ? d.ventas.map(function (v) {
            var prods = v.productos.map(function (p) {
                return '<li><span>' + num.format(p.cantidad) + ' × ' + esc(p.nombre) + '</span><span>' + $$(p.subtotal) + '</span></li>';
            }).join('');
            return '<div class="fila"><details><summary>' +
                '<div class="fila-cab"><b>' + esc(v.cliente) + '</b><span class="monto">' + $$(v.total) + '</span></div>' +
                '<div class="fila-det">#' + v.id + ' · ' + esc(v.hora) + ' · ' + esc(v.tipo_pago) +
                (v.gestor ? ' · gestor ' + esc(v.gestor) : '') +
                (v.saldo > 0 ? ' · <span class="etiqueta e-pendiente">debe ' + $$(v.saldo) + '</span>' : '') +
                '</div></summary><ul class="productos">' + prods + '</ul>' +
                '<div class="fila-det">Vendió: ' + esc(v.vendedor) + ' · Pagó ' + $$(v.pagado) + '</div>' +
                '</details></div>';
        }).join('') : '<div class="vacio">No hay ventas este día</div>';
    }

    function pintarPorProducto(filas) {
        $('por-producto-tarjeta').hidden = !filas.length;
        $('por-producto').innerHTML = filas.map(function (p) {
            return '<tr><td>' + esc(p.nombre) +
                '<small class="muted"> · ' + p.ventas + (p.ventas === 1 ? ' venta' : ' ventas') + '</small></td>' +
                '<td class="num"><b>' + num.format(p.cantidad) + '</b></td>' +
                '<td class="num">' + $$(p.total) + '</td></tr>';
        }).join('');
        var unidades = filas.reduce(function (a, p) { return a + p.cantidad; }, 0);
        var importe = filas.reduce(function (a, p) { return a + p.total; }, 0);
        $('por-producto-total').innerHTML = filas.length ?
            '<tr><td>Total · ' + filas.length + ' productos</td><td class="num">' + num.format(unidades) +
            '</td><td class="num">' + $$(importe) + '</td></tr>' : '';
    }

    // ---------------- Inventario ----------------
    function pintarInventario(d) {
        var q = $('buscar-prod').value.trim().toLowerCase();
        var lista = d.productos.filter(function (p) {
            if (filtroInv !== 'todos' && p.estado !== filtroInv) return false;
            return !q || (p.nombre + ' ' + p.categoria).toLowerCase().indexOf(q) >= 0;
        });
        var nombres = { ok: 'En stock', bajo: 'Stock bajo', agotado: 'Agotado' };
        $('lista-inv').innerHTML = lista.length ? lista.map(function (p) {
            return '<div class="fila"><div class="fila-cab"><b>' + esc(p.nombre) + '</b>' +
                '<span class="monto">' + num.format(p.cantidad) + ' u</span></div>' +
                '<div class="fila-det">' + (p.categoria ? esc(p.categoria) + ' · ' : '') + $$(p.precio) +
                ' · <span class="etiqueta e-' + p.estado + '">' + nombres[p.estado] + '</span></div></div>';
        }).join('') : '<div class="vacio">Ningún producto</div>';
    }

    // ---------------- Deudas ----------------
    function pintarDeudas(d) {
        $('d-total').textContent = $k(d.total);
        $('d-sub').textContent = d.clientes.length + ' clientes deben';
        var q = $('buscar-cli').value.trim().toLowerCase();
        var lista = d.clientes.filter(function (c) {
            return !q || (c.nombre + ' ' + c.telefono).toLowerCase().indexOf(q) >= 0;
        });
        $('lista-deudas').innerHTML = lista.length ? lista.map(function (c) {
            return '<div class="fila"><details data-cliente="' + c.id + '"><summary>' +
                '<div class="fila-cab"><b>' + esc(c.nombre) + '</b><span class="monto" style="color:var(--alerta)">' + $$(c.debe) + '</span></div>' +
                '<div class="fila-det">' + (c.telefono ? esc(c.telefono) + ' · ' : '') + 'debe desde ' + esc(fechaCorta(c.desde)) + '</div>' +
                '</summary><ul class="productos"><li class="muted">Cargando…</li></ul></details></div>';
        }).join('') : '<div class="vacio">Nadie debe nada 🎉</div>';
    }

    $('lista-deudas').addEventListener('toggle', function (ev) {
        var det = ev.target;
        if (!det.open || det.dataset.cargado) return;
        det.dataset.cargado = '1';
        pedir('/api/deudas/' + det.dataset.cliente).then(function (filas) {
            det.querySelector('.productos').innerHTML = filas.map(function (f) {
                return '<li><span>' + esc(f.fecha) + ' · ' + num.format(f.cantidad) + ' × ' + esc(f.producto) +
                    (f.venta_id ? ' (venta #' + f.venta_id + ')' : '') + '</span><span>' + $$(f.monto) + '</span></li>';
            }).join('') || '<li class="muted">Sin detalle</li>';
        }).catch(function () { det.dataset.cargado = ''; });
    }, true);

    // ---------------- Carga ----------------
    var fuentes = {
        resumen: function () { return '/api/resumen'; },
        ventas: function () { return '/api/ventas?dia=' + $('dia').value; },
        inventario: function () { return '/api/inventario'; },
        deudas: function () { return '/api/deudas'; }
    };
    var pintores = { resumen: pintarResumen, ventas: pintarVentas, inventario: pintarInventario, deudas: pintarDeudas };

    function cargar() {
        var v = vista;
        $('recargar').classList.add('girando');
        return pedir(fuentes[v]()).then(function (d) {
            cache[v] = d;
            if (v === vista) pintores[v](d);
            mostrarError(null);
            if (v !== 'resumen') return pedir('/api/resumen').then(function (r) { frescura(r.ultima_subida); });
        }).catch(function (e) {
            if (e.message !== 'sesion') mostrarError(navigator.onLine ? e.message : 'Sin internet. Se muestran los últimos datos cargados.');
        }).finally(function () { $('recargar').classList.remove('girando'); });
    }

    var titulos = { resumen: 'Resumen', ventas: 'Ventas', inventario: 'Inventario', deudas: 'Deudas' };
    function ir(v) {
        vista = v;
        document.querySelectorAll('.vista').forEach(function (s) { s.hidden = s.id !== 'v-' + v; });
        document.querySelectorAll('.pestanas button').forEach(function (b) { b.classList.toggle('activo', b.dataset.vista === v); });
        $('titulo').textContent = titulos[v];
        if (cache[v]) pintores[v](cache[v]);
        window.scrollTo(0, 0);
        cargar();
    }

    document.querySelectorAll('.pestanas button').forEach(function (b) {
        b.addEventListener('click', function () { ir(b.dataset.vista); });
    });
    document.querySelectorAll('.kpi[data-ir]').forEach(function (k) {
        k.addEventListener('click', function () { ir(k.dataset.ir); });
    });
    $('recargar').addEventListener('click', cargar);

    $('dia').value = hoyISO();
    $('dia').addEventListener('change', cargar);
    document.querySelectorAll('[data-dia]').forEach(function (b) {
        b.addEventListener('click', function () {
            var d = new Date($('dia').value + 'T12:00:00');
            d.setDate(d.getDate() + Number(b.dataset.dia));
            $('dia').value = d.toISOString().slice(0, 10);
            cargar();
        });
    });

    $('buscar-prod').addEventListener('input', function () { if (cache.inventario) pintarInventario(cache.inventario); });
    $('buscar-cli').addEventListener('input', function () { if (cache.deudas) pintarDeudas(cache.deudas); });
    document.querySelectorAll('.chip').forEach(function (c) {
        c.addEventListener('click', function () {
            filtroInv = c.dataset.filtro;
            document.querySelectorAll('.chip').forEach(function (x) { x.classList.toggle('activo', x === c); });
            if (cache.inventario) pintarInventario(cache.inventario);
        });
    });

    // Refresco: solo con la pantalla visible, para no gastar batería ni
    // despertar a Neon cuando nadie mira.
    setInterval(function () { if (!document.hidden) cargar(); }, REFRESCO_MS);
    document.addEventListener('visibilitychange', function () { if (!document.hidden) cargar(); });
    // iPhone: al volver a la app instalada la página sale de memoria sin recargar
    window.addEventListener('pageshow', function (e) { if (e.persisted) cargar(); });

    if ('serviceWorker' in navigator) {
        navigator.serviceWorker.register('/sw.js').catch(function () {});
    }

    cargar();
})();
