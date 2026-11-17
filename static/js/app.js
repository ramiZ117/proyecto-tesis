/* El Amir · comportamiento de la interfaz.
 * Todo es progresivo: sin JavaScript los formularios se envían de forma clásica
 * y el servidor responde igual. Los eventos se delegan en `document`, así que
 * siguen funcionando cuando una parte de la página se reemplaza.
 */
(function () {
  "use strict";

  var guardado = {
    leer: function (clave) { try { return window.localStorage.getItem(clave); } catch (e) { return null; } },
    escribir: function (clave, valor) { try { window.localStorage.setItem(clave, valor); } catch (e) { /* sin almacenamiento */ } }
  };

  function avisar(raiz) {
    document.dispatchEvent(new CustomEvent("contenido-actualizado", { detail: { raiz: raiz || document } }));
  }

  /* ---- refresco de pantallas vivas (mapa, cocina) ------------------------------ */
  var vigias = {};

  function vigilar() {
    document.querySelectorAll("[data-refresco]").forEach(function (nodo) {
      var id = nodo.id;
      if (!id || vigias[id]) return;
      vigias[id] = setInterval(function () {
        var actual = document.getElementById(id);
        if (!actual) { clearInterval(vigias[id]); delete vigias[id]; return; }
        var url = actual.getAttribute("data-refresco");
        fetch(url, { credentials: "same-origin" })
          .then(function (r) { return r.ok ? r.text() : null; })
          .then(function (html) {
            if (html === null) return;
            var nodoActual = document.getElementById(id);
            if (!nodoActual) return;
            if (nodoActual.getAttribute("data-ultimo") === null) { nodoActual.setAttribute("data-ultimo", html); return; }
            if (nodoActual.getAttribute("data-ultimo") !== html) {
              nodoActual.innerHTML = html;
              nodoActual.setAttribute("data-ultimo", html);
              avisar(nodoActual);
            }
          })
          .catch(function () {});
      }, 5000);
    });
  }

  /* ---- envío de formularios sin recargar ------------------------------------ */
  function aplicar(html, urlFinal, rutaAnterior, scrollY) {
    var doc = new DOMParser().parseFromString(html, "text/html");
    var nuevo = doc.getElementById("pagina-parcial");
    var pagina = document.getElementById("pagina");
    if (!nuevo || !pagina) { location.assign(urlFinal); return; }
    if (document.activeElement && document.activeElement.blur) document.activeElement.blur();
    pagina.innerHTML = nuevo.innerHTML;
    var destino = new URL(urlFinal, location.origin);
    var mostrada = destino.pathname + destino.search + destino.hash;
    var misma = destino.pathname === rutaAnterior;
    if (misma) history.replaceState({}, "", mostrada); else history.pushState({}, "", mostrada);
    var y = misma ? scrollY : 0;
    var hasta = Date.now() + (misma ? 500 : 0);
    (function mantener() {
      if (Math.abs((window.scrollY || 0) - y) > 1) window.scrollTo(0, y);
      if (Date.now() < hasta) requestAnimationFrame(mantener);
    })();
    if (destino.hash && !misma) {
      var ancla = document.getElementById(destino.hash.slice(1));
      if (ancla) ancla.scrollIntoView({ block: "start" });
    }
    vigilar();
    avisar(pagina);
  }

  document.addEventListener("submit", function (evento) {
    var form = evento.target;
    var pagina = document.getElementById("pagina");
    if (!pagina || !pagina.contains(form) || evento.defaultPrevented || form.hasAttribute("data-sin-parcial")) return;
    evento.preventDefault();
    var metodo = (form.getAttribute("method") || "get").toLowerCase();
    // Los datos se leen ANTES de desactivar los botones: un botón desactivado no se envía, y
    // botones como los platos de la carta (name="producto_id") o las acciones de cocina
    // (name="accion") llevan justamente el dato que el servidor necesita.
    var datos = new FormData(form);
    var pulsado = evento.submitter;
    if (pulsado && pulsado.name && !pulsado.disabled) datos.append(pulsado.name, pulsado.value);
    var botones = form.querySelectorAll("button, input[type=submit]");
    botones.forEach(function (b) { b.disabled = true; });
    var opciones = { credentials: "same-origin", headers: { "X-Partial": "1" } };
    var direccion = form.getAttribute("action") || location.pathname;
    if (metodo === "post") {
      opciones.method = "POST";
      opciones.body = datos;
    } else {
      direccion = direccion.split("#")[0];
      var params = new URLSearchParams(datos);
      direccion += (direccion.indexOf("?") >= 0 ? "&" : "?") + params.toString();
    }
    var rutaAnterior = location.pathname;
    var scrollY = window.scrollY || document.documentElement.scrollTop || 0;
    fetch(direccion, opciones)
      .then(function (respuesta) {
        if (respuesta.status === 204) {
          var siguiente = respuesta.headers.get("X-Next");
          if (!siguiente) throw new Error("sin destino");
          return fetch(siguiente, { credentials: "same-origin", headers: { "X-Partial": "1" } })
            .then(function (r) { return { respuesta: r, url: siguiente }; });
        }
        return { respuesta: respuesta, url: respuesta.url || direccion };
      })
      .then(function (resultado) {
        if (!resultado.respuesta.ok) {
          // Un error del servidor en un POST no debe convertirse en otra petición POST por GET.
          location.assign(metodo === "post" ? location.pathname + location.search : resultado.url);
          return;
        }
        return resultado.respuesta.text().then(function (html) { aplicar(html, resultado.url, rutaAnterior, scrollY); });
      })
      .catch(function () { botones.forEach(function (b) { b.disabled = false; }); });
  });

  window.addEventListener("popstate", function () { location.reload(); });

  /* ---- filtros por texto y por categoría ------------------------------------ */
  function aplicarFiltro(grupo) {
    var texto = "";
    var campo = document.querySelector('[data-filtro-texto="' + grupo + '"]');
    if (campo) texto = (campo.value || "").trim().toLowerCase();
    var chip = document.querySelector('[data-filtro-chip="' + grupo + '"][aria-pressed="true"]');
    var categoria = chip ? chip.getAttribute("data-valor") : "";
    document.querySelectorAll('[data-filtrable="' + grupo + '"]').forEach(function (el) {
      var okTexto = !texto || (el.getAttribute("data-texto") || el.textContent).toLowerCase().indexOf(texto) >= 0;
      var okCat = !categoria || el.getAttribute("data-categoria") === categoria;
      if (okTexto && okCat) el.removeAttribute("data-oculto-filtro"); else el.setAttribute("data-oculto-filtro", "");
    });
  }

  document.addEventListener("input", function (e) {
    var grupo = e.target.getAttribute && e.target.getAttribute("data-filtro-texto");
    if (grupo) aplicarFiltro(grupo);
  });

  document.addEventListener("click", function (e) {
    var chip = e.target.closest && e.target.closest("[data-filtro-chip]");
    if (chip) {
      var grupo = chip.getAttribute("data-filtro-chip");
      document.querySelectorAll('[data-filtro-chip="' + grupo + '"]').forEach(function (c) { c.setAttribute("aria-pressed", c === chip ? "true" : "false"); });
      guardado.escribir("filtro-" + grupo, chip.getAttribute("data-valor"));
      aplicarFiltro(grupo);
    }
  });

  function restaurarFiltros(raiz) {
    (raiz || document).querySelectorAll("[data-filtro-chip][data-recordar]").forEach(function (chip) {
      var grupo = chip.getAttribute("data-filtro-chip");
      var valor = guardado.leer("filtro-" + grupo);
      if (valor !== null && chip.getAttribute("data-valor") === valor) {
        document.querySelectorAll('[data-filtro-chip="' + grupo + '"]').forEach(function (c) { c.setAttribute("aria-pressed", c === chip ? "true" : "false"); });
        aplicarFiltro(grupo);
      }
    });
  }

  /* ---- cronómetros de cocina ------------------------------------------------- */
  var desfase = 0;

  function leerDesfase(raiz) {
    var nodo = (raiz || document).querySelector("[data-ahora]");
    if (!nodo) return;
    var servidor = Date.parse(nodo.getAttribute("data-ahora"));
    if (!isNaN(servidor)) desfase = servidor - Date.now();
  }

  function dosDigitos(n) { return (n < 10 ? "0" : "") + n; }

  setInterval(function () {
    var ahora = Date.now() + desfase;
    document.querySelectorAll("[data-desde]").forEach(function (el) {
      var desde = Date.parse(el.getAttribute("data-desde"));
      if (isNaN(desde)) return;
      var seg = Math.max(0, Math.floor((ahora - desde) / 1000));
      var horas = Math.floor(seg / 3600);
      var texto = (horas ? horas + ":" + dosDigitos(Math.floor(seg / 60) % 60) : dosDigitos(Math.floor(seg / 60))) + ":" + dosDigitos(seg % 60);
      var destino = el.querySelector("[data-reloj-texto]") || el;
      destino.textContent = texto;
    });
    document.querySelectorAll("[data-reloj-pared]").forEach(function (el) {
      var d = new Date(ahora);
      el.textContent = dosDigitos(d.getHours()) + ":" + dosDigitos(d.getMinutes()) + ":" + dosDigitos(d.getSeconds());
    });
  }, 1000);

  /* ---- aviso sonoro de cocina ------------------------------------------------ */
  var nuevosPrevios = null;
  var audio = null;

  function sonidoActivo() { return guardado.leer("kds-sonido") !== "0"; }

  function pintarBotonSonido() {
    document.querySelectorAll("[data-sonido]").forEach(function (b) {
      var activo = sonidoActivo();
      b.setAttribute("aria-pressed", activo ? "true" : "false");
      var t = b.querySelector("[data-sonido-texto]");
      if (t) t.textContent = activo ? "Sonido activo" : "Sonido apagado";
    });
  }

  function campana() {
    try {
      audio = audio || new (window.AudioContext || window.webkitAudioContext)();
      [0, 0.18].forEach(function (retraso, i) {
        var osc = audio.createOscillator();
        var vol = audio.createGain();
        osc.type = "sine";
        osc.frequency.value = i ? 1046 : 784;
        vol.gain.setValueAtTime(0.0001, audio.currentTime + retraso);
        vol.gain.exponentialRampToValueAtTime(0.25, audio.currentTime + retraso + 0.02);
        vol.gain.exponentialRampToValueAtTime(0.0001, audio.currentTime + retraso + 0.35);
        osc.connect(vol).connect(audio.destination);
        osc.start(audio.currentTime + retraso);
        osc.stop(audio.currentTime + retraso + 0.4);
      });
    } catch (e) { /* el navegador no permite audio */ }
  }

  document.addEventListener("click", function (e) {
    var boton = e.target.closest && e.target.closest("[data-sonido]");
    if (!boton) return;
    guardado.escribir("kds-sonido", sonidoActivo() ? "0" : "1");
    pintarBotonSonido();
    if (sonidoActivo()) campana();
  });

  function revisarNuevos(raiz) {
    var nodo = (raiz || document).querySelector("[data-nuevos]") || document.querySelector("[data-nuevos]");
    if (!nodo) return;
    var n = parseInt(nodo.getAttribute("data-nuevos"), 10) || 0;
    if (nuevosPrevios !== null && n > nuevosPrevios && sonidoActivo()) campana();
    nuevosPrevios = n;
  }

  /* ---- caja: cálculo del vuelto en vivo -------------------------------------- */
  function numero(texto) {
    var v = parseFloat(String(texto || "").replace(/\s/g, "").replace(",", "."));
    return isNaN(v) ? 0 : v;
  }

  function bs(n) { return "Bs " + n.toLocaleString("es-VE", { minimumFractionDigits: 2, maximumFractionDigits: 2 }); }

  function recalcularCobro(form) {
    var saldo = numero(form.getAttribute("data-saldo"));
    var tasa = numero(form.getAttribute("data-tasa"));
    var marcado = form.querySelector("input[name=metodo]:checked");
    var metodo = marcado ? marcado.value : "EFE";
    var esUsd = metodo === "DIV";
    var monto = numero((form.querySelector("[name=monto_recibido]") || {}).value);
    var equivalente = esUsd ? Math.round(monto * tasa * 100) / 100 : monto;
    var cubre = esUsd ? (saldo > 0 && monto >= Math.round(saldo / tasa * 100) / 100) : equivalente >= saldo;
    var aplicado = cubre ? saldo : Math.min(equivalente, saldo);
    var vuelto = Math.max(equivalente - saldo, 0);
    var sinVuelto = (metodo === "PMO" || metodo === "TAR") && vuelto > 0.004;
    var poner = function (sel, texto) { var n = form.querySelector(sel); if (n) n.textContent = texto; };
    poner("[data-simbolo]", esUsd ? "$" : "Bs");
    poner("[data-moneda]", esUsd ? "dólares" : "bolívares");
    poner("[data-aplicado]", bs(aplicado));
    poner("[data-vuelto]", bs(sinVuelto ? 0 : vuelto));
    var aviso = form.querySelector("[data-aviso]");
    if (aviso) aviso.hidden = !sinVuelto;
    var caja = form.querySelector(".calculo");
    if (caja) caja.classList.toggle("error", sinVuelto);
    var ref = form.querySelector("[data-referencia]");
    if (ref) ref.hidden = !(metodo === "PMO" || metodo === "TAR");
    var enviar = form.querySelector("[data-cobrar]");
    if (enviar) {
      enviar.disabled = monto <= 0 || sinVuelto;
      enviar.textContent = cubre && !sinVuelto ? "Cobrar y cerrar cuenta" : "Registrar pago parcial";
    }
    form.querySelectorAll("[data-rapido]").forEach(function (b) {
      var valor = b.getAttribute(esUsd ? "data-usd" : "data-bs");
      b.hidden = !valor;
      if (valor && b.hasAttribute("data-etiqueta-usd")) b.textContent = esUsd ? b.getAttribute("data-etiqueta-usd") : b.getAttribute("data-etiqueta-bs");
    });
  }

  document.addEventListener("input", function (e) {
    var form = e.target.closest && e.target.closest("form[data-cobro]");
    if (form) recalcularCobro(form);
  });
  document.addEventListener("change", function (e) {
    var form = e.target.closest && e.target.closest("form[data-cobro]");
    if (!form) return;
    if (e.target.name === "metodo") {
      var exacto = form.querySelector("[data-rapido][data-exacto]");
      var campo = form.querySelector("[name=monto_recibido]");
      if (exacto && campo) campo.value = exacto.getAttribute(e.target.value === "DIV" ? "data-usd" : "data-bs");
    }
    recalcularCobro(form);
  });
  document.addEventListener("click", function (e) {
    var b = e.target.closest && e.target.closest("[data-rapido]");
    if (!b) return;
    var form = b.closest("form[data-cobro]");
    var marcado = form.querySelector("input[name=metodo]:checked");
    var esUsd = marcado && marcado.value === "DIV";
    form.querySelector("[name=monto_recibido]").value = b.getAttribute(esUsd ? "data-usd" : "data-bs");
    recalcularCobro(form);
  });

  /* ---- inventario: formulario de movimientos ---------------------------------- */
  function pintarMovimiento(form) {
    var tipo = form.querySelector("[name=tipo]").value;
    form.querySelectorAll("[data-tipo-mov]").forEach(function (b) {
      b.setAttribute("aria-selected", b.getAttribute("data-tipo-mov") === tipo ? "true" : "false");
    });
    form.querySelectorAll("[data-solo]").forEach(function (n) {
      n.hidden = n.getAttribute("data-solo").split(" ").indexOf(tipo) < 0;
      n.querySelectorAll("select, input").forEach(function (c) { c.disabled = n.hidden; });
    });
    var etiquetas = { ENT: "Cantidad recibida", MER: "Cantidad perdida", AJU: "Cantidad contada" };
    var botones = { ENT: "Registrar entrada", MER: "Registrar merma", AJU: "Guardar conteo" };
    var et = form.querySelector("[data-etiqueta-cantidad]");
    if (et) et.textContent = etiquetas[tipo];
    var enviar = form.querySelector("[data-enviar-mov]");
    if (enviar) {
      enviar.textContent = botones[tipo];
      enviar.classList.toggle("peligro-lleno", tipo === "MER");
    }
    var opcion = form.querySelector("[name=insumo_id]");
    opcion = opcion && opcion.options[opcion.selectedIndex];
    var previa = form.querySelector("[data-previa]");
    var unidad = form.querySelector("[data-unidad]");
    if (!opcion || !previa) return;
    var stock = numero(opcion.getAttribute("data-stock"));
    var abrev = opcion.getAttribute("data-unidad") || "";
    if (unidad) unidad.textContent = abrev;
    var prov = form.querySelector("[data-proveedor]");
    if (prov) prov.textContent = opcion.getAttribute("data-proveedor") || "Sin proveedor";
    var cant = numero(form.querySelector("[name=cantidad]").value);
    var nuevo = tipo === "ENT" ? stock + cant : tipo === "MER" ? stock - cant : cant;
    var fmt = function (n) { return (Math.round(n * 1000) / 1000).toLocaleString("es-VE"); };
    if (tipo === "MER" && cant > stock) previa.textContent = "La merma no puede superar el stock actual (" + fmt(stock) + " " + abrev + ").";
    else previa.textContent = "Stock de " + opcion.textContent.trim() + ": " + fmt(stock) + " → " + fmt(Math.max(nuevo, 0)) + " " + abrev;
  }

  document.addEventListener("click", function (e) {
    var tab = e.target.closest && e.target.closest("[data-tipo-mov]");
    if (tab) {
      var form = tab.closest("form");
      form.querySelector("[name=tipo]").value = tab.getAttribute("data-tipo-mov");
      pintarMovimiento(form);
      return;
    }
    var atajo = e.target.closest && e.target.closest("[data-mov-atajo]");
    if (atajo) {
      var f = document.querySelector("form[data-movimiento]");
      if (!f) return;
      f.querySelector("[name=tipo]").value = atajo.getAttribute("data-mov-atajo");
      f.querySelector("[name=insumo_id]").value = atajo.getAttribute("data-insumo");
      var sugerida = atajo.getAttribute("data-sugerida");
      f.querySelector("[name=cantidad]").value = sugerida || "";
      pintarMovimiento(f);
      f.scrollIntoView({ behavior: "smooth", block: "center" });
      f.querySelector("[name=cantidad]").focus({ preventScroll: true });
    }
  });
  ["input", "change"].forEach(function (tipoEvento) {
    document.addEventListener(tipoEvento, function (e) {
      var form = e.target.closest && e.target.closest("form[data-movimiento]");
      if (form) pintarMovimiento(form);
    });
  });

  /* ---- inicialización ------------------------------------------------------- */
  function inicializar(raiz) {
    leerDesfase(raiz);
    restaurarFiltros(raiz);
    pintarBotonSonido();
    revisarNuevos(raiz);
    document.querySelectorAll("form[data-cobro]").forEach(recalcularCobro);
    document.querySelectorAll("form[data-movimiento]").forEach(pintarMovimiento);
  }

  document.addEventListener("contenido-actualizado", function (e) { inicializar(e.detail && e.detail.raiz); });
  vigilar();
  inicializar(document);
})();
