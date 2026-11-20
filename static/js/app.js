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

  /* ---- confirmaciones con el diseño del sistema (en vez del confirm() del navegador) ---- */
  var ventana = null;
  function ventanaConfirmar() {
    if (ventana) return ventana;
    ventana = document.createElement("dialog");
    ventana.className = "confirmar";
    ventana.setAttribute("aria-labelledby", "confirmar-titulo");
    var cuerpo = document.createElement("form");
    cuerpo.method = "dialog";
    cuerpo.className = "confirmar-cuerpo";
    var icono = document.createElement("div");
    icono.className = "confirmar-icono";
    icono.setAttribute("aria-hidden", "true");
    icono.textContent = "?";
    var titulo = document.createElement("h2");
    titulo.id = "confirmar-titulo";
    var botones = document.createElement("div");
    botones.className = "confirmar-botones";
    var no = document.createElement("button");
    no.value = "no"; no.className = "btn secundario"; no.textContent = "Cancelar";
    var si = document.createElement("button");
    si.value = "si"; si.className = "btn"; si.textContent = "Sí, continuar";
    botones.append(no, si);
    cuerpo.append(icono, titulo, botones);
    ventana.appendChild(cuerpo);
    document.body.appendChild(ventana);
    return ventana;
  }

  function preguntar(texto, peligro) {
    var v = ventanaConfirmar();
    v.querySelector("h2").textContent = texto;
    v.classList.toggle("peligro", !!peligro);
    v.querySelector(".confirmar-icono").textContent = peligro ? "!" : "?";
    var si = v.querySelector("button[value=si]");
    si.className = peligro ? "btn peligro" : "btn";
    return new Promise(function (resolver) {
      v.returnValue = "";
      v.addEventListener("close", function alCerrar() {
        v.removeEventListener("close", alCerrar);
        resolver(v.returnValue === "si");
      });
      if (v.showModal) v.showModal(); else v.setAttribute("open", "");
      v.querySelector("button[value=no]").focus();
    });
  }

  document.addEventListener("click", function (e) {
    if (ventana && e.target === ventana) ventana.close("no");  // tocar fuera = cancelar
  });

  // Se escucha en captura: corre antes que el envío sin recarga y lo detiene hasta que confirmen.
  document.addEventListener("submit", function (e) {
    var form = e.target;
    var texto = form.getAttribute && form.getAttribute("data-confirmar");
    if (!texto) return;
    if (form.getAttribute("data-confirmado") === "1") { form.removeAttribute("data-confirmado"); return; }
    e.preventDefault();
    e.stopImmediatePropagation();
    var boton = e.submitter && e.submitter.form === form ? e.submitter : null;
    preguntar(texto, form.hasAttribute("data-confirmar-peligro")).then(function (ok) {
      if (!ok) return;
      form.setAttribute("data-confirmado", "1");
      if (form.requestSubmit) form.requestSubmit(boton || undefined); else form.submit();
    });
  }, true);

  document.addEventListener("change", function (e) {
    var campo = e.target;
    if (campo.matches && campo.matches("[data-enviar-al-cambiar]") && campo.form && campo.checkValidity()) {
      if (campo.form.requestSubmit) campo.form.requestSubmit(); else campo.form.submit();
    }
  });

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
              nodoActual.classList.add("refrescado");
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
    paginas[grupo] = 1;
    paginar(grupo);
  }

  /* ---- paginación de listas largas (respeta los filtros) ----------------------------- */
  var paginas = {};
  function paginar(grupo) {
    var nav = document.querySelector('[data-paginar="' + grupo + '"]');
    if (!nav) return;
    var porPagina = parseInt(nav.getAttribute("data-por-pagina"), 10) || 15;
    var filas = Array.prototype.filter.call(document.querySelectorAll('[data-filtrable="' + grupo + '"]'), function (el) {
      return !el.hasAttribute("data-oculto-filtro");
    });
    var total = Math.max(Math.ceil(filas.length / porPagina), 1);
    var actual = Math.min(paginas[grupo] || 1, total);
    paginas[grupo] = actual;
    document.querySelectorAll('[data-filtrable="' + grupo + '"]').forEach(function (el) { el.removeAttribute("data-oculto-pagina"); });
    filas.forEach(function (el, i) {
      if (i < (actual - 1) * porPagina || i >= actual * porPagina) el.setAttribute("data-oculto-pagina", "");
    });
    nav.replaceChildren();
    nav.hidden = total <= 1;
    if (total <= 1) return;
    function boton(texto, pagina, deshabilitado) {
      var b = document.createElement("button");
      b.type = "button";
      b.className = "btn sm secundario";
      b.textContent = texto;
      b.disabled = deshabilitado;
      b.setAttribute("data-ir-pagina", pagina);
      b.setAttribute("data-grupo", grupo);
      return b;
    }
    var info = document.createElement("span");
    info.className = "obs";
    info.textContent = "Página " + actual + " de " + total + " · " + filas.length + " en total";
    nav.append(boton("← Anterior", actual - 1, actual === 1), info, boton("Siguiente →", actual + 1, actual === total));
  }
  document.addEventListener("click", function (e) {
    var b = e.target.closest && e.target.closest("[data-ir-pagina]");
    if (!b) return;
    var grupo = b.getAttribute("data-grupo");
    paginas[grupo] = parseInt(b.getAttribute("data-ir-pagina"), 10) || 1;
    paginar(grupo);
    var nav = document.querySelector('[data-paginar="' + grupo + '"]');
    var contenedor = nav && (nav.previousElementSibling || nav.parentElement);
    if (contenedor && contenedor.getBoundingClientRect().top < 0) contenedor.scrollIntoView({ block: "start", behavior: "smooth" });
  });

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

  function actualizarRelojes() {
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
  }
  setInterval(actualizarRelojes, 1000);

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

  /* Cocina y shawarmero: alarma distinta (más insistente) cuando anulan algo que ya les llegó.
     Suena al aparecer el aviso y se repite cada 30 s mientras nadie pulse «Entendido». */
  function alarma() {
    try {
      audio = audio || new (window.AudioContext || window.webkitAudioContext)();
      [0, 0.22, 0.44, 0.9, 1.12, 1.34].forEach(function (retraso, i) {
        var osc = audio.createOscillator();
        var vol = audio.createGain();
        osc.type = "square";
        osc.frequency.value = i % 3 === 0 ? 880 : (i % 3 === 1 ? 660 : 440);
        vol.gain.setValueAtTime(0.0001, audio.currentTime + retraso);
        vol.gain.exponentialRampToValueAtTime(0.18, audio.currentTime + retraso + 0.02);
        vol.gain.exponentialRampToValueAtTime(0.0001, audio.currentTime + retraso + 0.18);
        osc.connect(vol).connect(audio.destination);
        osc.start(audio.currentTime + retraso);
        osc.stop(audio.currentTime + retraso + 0.2);
      });
    } catch (e) { /* el navegador no permite audio */ }
  }

  var avisoPrevio = null;
  var ultimaAlarma = 0;
  function revisarAvisos() {
    var nodo = document.querySelector("[data-aviso-ultimo]");
    if (!nodo) return;
    var id = parseInt(nodo.getAttribute("data-aviso-ultimo"), 10) || 0;
    var ahora = Date.now();
    var nuevo = avisoPrevio !== null && id > avisoPrevio;
    var repetir = id > 0 && ahora - ultimaAlarma > 30000 && avisoPrevio !== null;
    if ((nuevo || repetir) && sonidoActivo()) { alarma(); ultimaAlarma = ahora; }
    if (avisoPrevio === null && id > 0) ultimaAlarma = ahora;
    avisoPrevio = id;
  }

  /* Salón: suena cuando una mesa llama al mesero desde el menú digital. */
  var enLineaPrevios = null;
  function revisarEnLinea() {
    if (!document.getElementById("en-linea")) return;
    var n = document.querySelectorAll("#en-linea [data-por-verificar], #en-linea [data-listo-retirar]").length;
    if (enLineaPrevios !== null && n > enLineaPrevios) campana();
    enLineaPrevios = n;
  }

  var llamadosPrevios = null;
  function revisarLlamados() {
    if (!document.getElementById("mapa")) return;
    var n = document.querySelectorAll("#mapa .mesa.llamando").length;
    if (llamadosPrevios !== null && n > llamadosPrevios) campana();
    llamadosPrevios = n;
  }

  /* ---- caja: cálculo del vuelto en vivo -------------------------------------- */
  function numero(texto) {
    var v = parseFloat(String(texto || "").replace(/\s/g, "").replace(",", "."));
    return isNaN(v) ? 0 : v;
  }

  function bs(n) { return "Bs " + n.toLocaleString("es-VE", { minimumFractionDigits: 2, maximumFractionDigits: 2 }); }

  function filasDePago(form) {
    var marcado = form.querySelector("input[name=metodo]:checked");
    var filas = [{ metodo: marcado ? marcado.value : "EFE", monto: numero((form.querySelector("#monto_recibido") || {}).value) }];
    form.querySelectorAll("[data-fila-pago]:not([hidden])").forEach(function (f) {
      var monto = numero(f.querySelector("[name=monto_recibido]").value);
      if (monto > 0) filas.push({ metodo: f.querySelector("[name=metodo]").value, monto: monto });
    });
    return filas;
  }

  var METODOS_USD = ["DIV", "BIN", "ZIN", "CAS"];
  var SIN_VUELTO = ["PMO", "TAR", "BIN", "ZIN", "CAS"];
  function enDolares(m) { return METODOS_USD.indexOf(m) >= 0; }

  /* Simula lo que hará el servidor: primero los métodos sin vuelto y al final efectivo y divisa. */
  function simularCobro(filas, saldo, tasa, igtf, conIgtf) {
    igtf = igtf || 0; conIgtf = conIgtf || [];
    var igtfTotal = 0;
    var orden = filas.map(function (f, i) { return { f: f, i: i }; }).sort(function (a, b) {
      var va = (a.f.metodo === "EFE" || a.f.metodo === "DIV") ? 1 : 0;
      var vb = (b.f.metodo === "EFE" || b.f.metodo === "DIV") ? 1 : 0;
      return va - vb || a.i - b.i;
    });
    var aplicado = 0, vuelto = 0, error = "", restante = saldo;
    orden.forEach(function (x) {
      if (error) return;
      var f = x.f, esUsd = enDolares(f.metodo);
      if (restante <= 0.004) { error = "Sobra un pago: con los anteriores ya se cubre el total."; return; }
      var equivalente = esUsd ? Math.round(f.monto * tasa * 100) / 100 : f.monto;
      var cubre = esUsd ? f.monto >= Math.round(restante / tasa * 100) / 100 : equivalente >= restante;
      if (conIgtf.indexOf(f.metodo) >= 0) {
        /* Divisa en efectivo: además de la deuda se cobra el IGTF sobre lo que cubre. */
        var factor = 1 + igtf / 100;
        var necesario = Math.round(restante * factor * 100) / 100;
        var cubreConIgtf = equivalente >= necesario - 0.004 || f.monto >= Math.round(necesario / tasa * 100) / 100;
        var aplicaI = cubreConIgtf ? restante : Math.min(Math.round(equivalente / factor * 100) / 100, restante);
        var impuesto = cubreConIgtf ? necesario - restante : equivalente - aplicaI;
        igtfTotal += impuesto;
        if (cubreConIgtf) vuelto += Math.max(equivalente - necesario, 0);
        aplicado += aplicaI;
        restante = Math.round((restante - aplicaI) * 100) / 100;
        return;
      }
      var excede = esUsd ? f.monto > Math.round(restante / tasa * 100) / 100 + 0.004 : equivalente > restante + 0.004;
      if (SIN_VUELTO.indexOf(f.metodo) >= 0 && excede) {
        error = "Un pago sin vuelto (pago móvil, tarjeta, Binance o Zinli) supera lo que falta."; return;
      }
      var aplica = cubre ? restante : Math.min(equivalente, restante);
      if (SIN_VUELTO.indexOf(f.metodo) < 0) vuelto += Math.max(equivalente - restante, 0);
      aplicado += aplica;
      restante = Math.round((restante - aplica) * 100) / 100;
    });
    return { aplicado: aplicado, vuelto: vuelto, restante: Math.max(restante, 0), error: error, igtf: igtfTotal };
  }

  function recalcularCobro(form) {
    var saldo = numero(form.getAttribute("data-saldo"));
    var tasa = numero(form.getAttribute("data-tasa"));
    var marcado = form.querySelector("input[name=metodo]:checked");
    var metodo = marcado ? marcado.value : "EFE";
    var esUsd = enDolares(metodo);
    var filas = filasDePago(form);
    var conIgtf = (form.getAttribute("data-igtf-metodos") || "").split(",");
    var r = simularCobro(filas, saldo, tasa, numero(form.getAttribute("data-igtf")), conIgtf);
    var poner = function (sel, texto) { var n = form.querySelector(sel); if (n) n.textContent = texto; };
    var filaIgtf = form.querySelector("[data-fila-igtf]");
    if (filaIgtf) { filaIgtf.hidden = !(r.igtf > 0.004) || !!r.error; poner("[data-igtf-monto]", bs(r.igtf)); }
    poner("[data-simbolo]", esUsd ? "$" : "Bs");
    poner("[data-moneda]", esUsd ? "dólares" : "bolívares");
    poner("[data-aplicado]", bs(r.aplicado) + (filas.length > 1 ? " (" + filas.length + " pagos)" : ""));
    poner("[data-vuelto]", bs(r.error ? 0 : r.vuelto));
    var aviso = form.querySelector("[data-aviso]");
    if (aviso) { aviso.hidden = !r.error; aviso.textContent = r.error; }
    var caja = form.querySelector(".calculo");
    if (caja) caja.classList.toggle("error", !!r.error);
    var ref = form.querySelector("[data-referencia]");
    if (ref) {
      ref.hidden = !(metodo === "PMO" || metodo === "TAR" || metodo === "BIN" || metodo === "ZIN" || metodo === "CAS");
      var etiqueta = ref.querySelector("label");
      var campo = ref.querySelector("input");
      if (etiqueta && !etiqueta.hasAttribute("data-original")) etiqueta.setAttribute("data-original", etiqueta.innerHTML);
      if (etiqueta) etiqueta.innerHTML = metodo === "CAS" ? "N.º de orden del financiamiento <span class=\"obs\">(obligatorio)</span>" : etiqueta.getAttribute("data-original");
      if (campo) campo.required = metodo === "CAS";
    }
    var enviar = form.querySelector("[data-cobrar]");
    if (enviar) {
      enviar.disabled = filas[0].monto <= 0 || !!r.error;
      enviar.textContent = r.restante <= 0.004 && !r.error ? "Cobrar y cerrar cuenta" : "Registrar pago parcial";
    }
    form.querySelectorAll("[data-rapido]").forEach(function (b) {
      var valor = b.getAttribute(esUsd ? "data-usd" : "data-bs");
      b.hidden = !valor;
    });
  }

  document.addEventListener("input", function (e) {
    var form = e.target.closest && e.target.closest("form[data-cobro]");
    if (form) recalcularCobro(form);
  });
  document.addEventListener("change", function (e) {
    var form = e.target.closest && e.target.closest("form[data-cobro]");
    if (!form) return;
    if (e.target.name === "metodo" && e.target.type === "radio") {
      var exacto = form.querySelector("[data-rapido][data-exacto]");
      var campo = form.querySelector("#monto_recibido");
      var igtfM = (form.getAttribute("data-igtf-metodos") || "").split(",");
      if (exacto && campo) campo.value = exacto.getAttribute(igtfM.indexOf(e.target.value) >= 0 ? "data-usd-igtf" : enDolares(e.target.value) ? "data-usd" : "data-bs");
    }
    recalcularCobro(form);
  });
  document.addEventListener("click", function (e) {
    var agregar = e.target.closest && e.target.closest("[data-agregar-pago]");
    if (agregar) {
      var formA = agregar.closest("form[data-cobro]");
      var libre = formA.querySelector("[data-fila-pago][hidden]");
      if (libre) {
        libre.hidden = false;
        libre.querySelectorAll("select, input").forEach(function (c) { c.disabled = false; });
        libre.querySelector("[name=monto_recibido]").focus();
      }
      agregar.hidden = !formA.querySelector("[data-fila-pago][hidden]");
      recalcularCobro(formA);
      return;
    }
    var quitar = e.target.closest && e.target.closest("[data-quitar-fila]");
    if (quitar) {
      var fila = quitar.closest("[data-fila-pago]");
      var formQ = quitar.closest("form[data-cobro]");
      fila.hidden = true;
      fila.querySelectorAll("select, input").forEach(function (c) { c.disabled = true; if (c.tagName === "INPUT") c.value = ""; });
      formQ.querySelector("[data-agregar-pago]").hidden = false;
      recalcularCobro(formQ);
      return;
    }
    var b = e.target.closest && e.target.closest("[data-rapido]");
    if (!b) return;
    var form = b.closest("form[data-cobro]");
    var marcado = form.querySelector("input[name=metodo]:checked");
    var esUsd = marcado && enDolares(marcado.value);
    var conIgtfR = marcado && (form.getAttribute("data-igtf-metodos") || "").split(",").indexOf(marcado.value) >= 0;
    form.querySelector("#monto_recibido").value = b.getAttribute(conIgtfR && b.hasAttribute("data-usd-igtf") ? "data-usd-igtf" : esUsd ? "data-usd" : "data-bs");
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

  /* ---- caja: clientes guardados en «Datos de la factura» ----------------------
     Al escribir la cédula, el RIF o el nombre aparecen los clientes que ya vinieron; al elegir uno
     se llenan sus datos. */
  var esperaCliente = null;
  function pintarSugerencias(caja, clientes) {
    var lista = caja.querySelector("[data-sugerencias]");
    lista.replaceChildren();
    clientes.forEach(function (c) {
      var b = document.createElement("button");
      b.type = "button";
      b.setAttribute("role", "option");
      var nombre = document.createElement("strong");
      nombre.textContent = c.nombre;
      var detalle = document.createElement("small");
      detalle.textContent = c.documento + (c.telefono ? " · " + c.telefono : "");
      b.append(nombre, detalle);
      b.addEventListener("click", function () {
        var form = caja.closest("form");
        ["documento", "nombre", "direccion", "telefono", "correo"].forEach(function (campo) {
          var input = form.querySelector("[name=" + campo + "]");
          if (input) input.value = c[campo] || "";
        });
        lista.hidden = true;
      });
      lista.appendChild(b);
    });
    lista.hidden = clientes.length === 0;
  }
  document.addEventListener("input", function (e) {
    var caja = e.target.closest && e.target.closest("[data-buscar-cliente]");
    if (!caja) return;
    clearTimeout(esperaCliente);
    var texto = e.target.value.trim();
    if (texto.length < 3) { caja.querySelector("[data-sugerencias]").hidden = true; return; }
    esperaCliente = setTimeout(function () {
      fetch(caja.getAttribute("data-buscar-cliente") + "?q=" + encodeURIComponent(texto), { credentials: "same-origin" })
        .then(function (r) { return r.ok ? r.json() : { clientes: [] }; })
        .then(function (d) { pintarSugerencias(caja, d.clientes || []); })
        .catch(function () {});
    }, 250);
  });
  document.addEventListener("click", function (e) {
    document.querySelectorAll("[data-sugerencias]").forEach(function (lista) {
      if (!lista.parentNode.contains(e.target)) lista.hidden = true;
    });
  });
  document.addEventListener("keydown", function (e) {
    if (e.key !== "Escape") return;
    document.querySelectorAll("[data-sugerencias]").forEach(function (lista) { lista.hidden = true; });
  });

  /* ---- inicialización ------------------------------------------------------- */
  function inicializar(raiz) {
    leerDesfase(raiz);
    actualizarRelojes();
    restaurarFiltros(raiz);
    (raiz || document).querySelectorAll("[data-paginar]").forEach(function (nav) { paginar(nav.getAttribute("data-paginar")); });
    pintarBotonSonido();
    revisarNuevos(raiz);
    revisarAvisos();
    revisarLlamados();
    revisarEnLinea();
    document.querySelectorAll("form[data-cobro]").forEach(recalcularCobro);
    document.querySelectorAll("form[data-movimiento]").forEach(pintarMovimiento);
  }

  document.addEventListener("contenido-actualizado", function (e) { inicializar(e.detail && e.detail.raiz); });
  vigilar();
  inicializar(document);
})();

