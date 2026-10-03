/*
 * Sincronizacion de clientes entre las vistas de la app (/ventas, /clientes).
 *
 * /ventas guarda una copia de los clientes en TODOS_CLIENTES al renderizar la
 * pagina. Si un cliente se edita en otra pestana, esa copia quedaba vieja y el
 * selector mostraba el nombre anterior. Con este modulo, /clientes avisa del
 * cambio y /ventas se actualiza al instante, sin recargar.
 */
(function (global) {
    'use strict';

    var CANAL = 'contastock-clientes';
    var CLAVE = 'contastock:clientes:cambio';
    var clientes = [];

    function canal() {
        if (clientes.length) return clientes[0];
        var bc = null;
        try {
            if (global.BroadcastChannel) bc = new global.BroadcastChannel(CANAL);
        } catch (e) { bc = null; }
        clientes.push(bc);
        return bc;
    }

    function avisar(mensaje) {
        // localStorage dispara 'storage' en las OTRAS pestanas, no en esta.
        try {
            global.localStorage.setItem(CLAVE, JSON.stringify(
                Object.assign({ ts: Date.now() }, mensaje)));
        } catch (e) { /* modo privado o cuota llena */ }
        var bc = canal();
        if (bc) { try { bc.postMessage(mensaje); } catch (e) {} }
    }

    function formatear(c) {
        var f = {
            i: c.id != null ? c.id : c.i,
            n: c.nombre != null ? c.nombre : (c.n || ''),
            t: c.telefono != null ? (c.telefono || '') : (c.t || ''),
            c: c.ci != null ? (c.ci || '') : (c.c || ''),
            h: c.chapa != null ? (c.chapa || '') : (c.h || ''),
            d: c.deuda != null ? c.deuda : (c.d || 0)
        };
        // La bandera de deuda pendiente viene de la tabla deudas; si el que
        // publica no la trae, no se manda antes que la de quien ya la tenia.
        if (c.deb != null) f.deb = !!c.deb;
        // misma idea con la marca de gestor
        if (c.es_gestor != null) f.g = !!c.es_gestor;
        else if (c.g != null) f.g = !!c.g;
        return f;
    }

    global.ClientesSync = {
        /** Notifica a las demás vistas. accion: crear|editar|eliminar. */
        publicar: function (accion, cliente) {
            avisar({ accion: accion, cliente: formatear(cliente) });
        },

        /** Avisa que hay que recargar todo desde el servidor. */
        recargar: function () {
            avisar({ accion: 'recargar' });
        },

        suscribir: function (fn) {
            function manejar(ev) {
                var datos = ev && ev.data;
                if (datos && datos.accion) fn(datos);
            }
            try {
                global.addEventListener('storage', function (ev) {
                    if (ev.key !== CLAVE || !ev.newValue) return;
                    try { manejar(JSON.parse(ev.newValue)); } catch (e) {}
                });
            } catch (e) {}
            var bc = canal();
            if (bc) bc.addEventListener('message', manejar);
            return function () {
                if (bc) bc.removeEventListener('message', manejar);
            };
        }
    };
})(window);
