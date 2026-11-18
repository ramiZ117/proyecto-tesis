/* Menú digital de El Amir: búsqueda, ficha de cada plato y asistente.
 * Las respuestas del asistente se insertan siempre como texto (textContent), nunca como HTML. */
(function () {
  "use strict";

  var platos = JSON.parse(document.getElementById("datos-platos").textContent || "{}");
  var config = JSON.parse(document.getElementById("config-menu").textContent || "{}");
  var $ = function (sel) { return document.querySelector(sel); };
  var guardado = {
    leer: function (k) { try { return JSON.parse(sessionStorage.getItem(k)); } catch (e) { return null; } },
    escribir: function (k, v) { try { sessionStorage.setItem(k, JSON.stringify(v)); } catch (e) { /* sin almacenamiento */ } }
  };

  function elemento(tag, clase, texto) {
    var el = document.createElement(tag);
    if (clase) el.className = clase;
    if (texto !== undefined) el.textContent = texto;
    return el;
  }

  function foto(plato) {
    if (plato.foto) {
      var img = elemento("img");
      img.src = plato.foto;
      img.alt = plato.nombre;
      img.decoding = "async";
      return img;
    }
    var vacia = elemento("span", "foto-vacia");
    vacia.appendChild(elemento("span", "", plato.nombre.charAt(0)));
    return vacia;
  }

  /* Aviso con el diseño del menú (en vez del avisar() del navegador). */
  var dlgAviso = null;
  function avisar(texto) {
    if (!dlgAviso) {
      dlgAviso = elemento("dialog", "aviso-menu");
      var cuerpo = elemento("form", "aviso-cuerpo");
      cuerpo.method = "dialog";
      var parrafo = elemento("p", "aviso-texto");
      var ok = elemento("button", "boton-agregar", "Entendido");
      cuerpo.append(parrafo, ok);
      dlgAviso.appendChild(cuerpo);
      dlgAviso.addEventListener("click", function (e) { if (e.target === dlgAviso) dlgAviso.close(); });
      document.body.appendChild(dlgAviso);
    }
    dlgAviso.querySelector(".aviso-texto").textContent = texto;
    if (dlgAviso.showModal) dlgAviso.showModal(); else dlgAviso.setAttribute("open", "");
  }

  /* Pregunta con dos botones y una casilla opcional. Resuelve {ok, marcada}. */
  var dlgPregunta = null;
  function preguntar(texto, casilla, textoSi, textoNo) {
    if (!dlgPregunta) {
      dlgPregunta = elemento("dialog", "aviso-menu");
      var cuerpo = elemento("form", "aviso-cuerpo");
      cuerpo.method = "dialog";
      var parrafo = elemento("p", "aviso-texto");
      var etiqueta = elemento("label", "check-factura casilla-pregunta");
      var caja = elemento("input");
      caja.type = "checkbox";
      etiqueta.append(caja, elemento("span"));
      var botones = elemento("div", "botones-pregunta");
      var no = elemento("button", "enlace-cambiar");
      no.value = "no";
      var si = elemento("button", "boton-agregar");
      si.value = "si";
      botones.append(si, no);
      cuerpo.append(parrafo, etiqueta, botones);
      dlgPregunta.appendChild(cuerpo);
      dlgPregunta.addEventListener("click", function (e) { if (e.target === dlgPregunta) dlgPregunta.close("no"); });
      document.body.appendChild(dlgPregunta);
    }
    dlgPregunta.querySelector(".aviso-texto").textContent = texto;
    var fila = dlgPregunta.querySelector(".casilla-pregunta");
    fila.hidden = !casilla;
    fila.querySelector("input").checked = false;
    fila.querySelector("span").textContent = casilla || "";
    dlgPregunta.querySelector("button[value=si]").textContent = textoSi;
    dlgPregunta.querySelector("button[value=no]").textContent = textoNo;
    return new Promise(function (resolver) {
      dlgPregunta.returnValue = "";
      dlgPregunta.addEventListener("close", function alCerrar() {
        dlgPregunta.removeEventListener("close", alCerrar);
        resolver({ ok: dlgPregunta.returnValue === "si", marcada: fila.querySelector("input").checked });
      });
      if (dlgPregunta.showModal) dlgPregunta.showModal(); else dlgPregunta.setAttribute("open", "");
    });
  }

  function normalizar(t) {
    return (t || "").toLowerCase().normalize("NFD").replace(/[̀-ͯ]/g, "");
  }

  /* ---- búsqueda ---------------------------------------------------------------- */
  var buscar = $("#buscar");
  buscar.addEventListener("input", function () {
    var q = normalizar(buscar.value.trim());
    var visibles = 0;
    document.querySelectorAll(".plato, [data-promo-tarjeta]").forEach(function (b) {
      var ok = !q || normalizar(b.getAttribute("data-buscar")).indexOf(q) >= 0;
      b.hidden = !ok;
      if (ok) visibles++;
    });
    document.querySelectorAll("[data-seccion]").forEach(function (s) {
      s.hidden = !s.querySelector(".plato:not([hidden])");
    });
    var rec = $("#recomendados");
    if (rec) rec.hidden = !!q;
    var promos = $("#promociones");
    if (promos) promos.hidden = !promos.querySelector("[data-promo-tarjeta]:not([hidden])");
    $("#sin-resultados").hidden = visibles > 0;
  });

  /* ---- categoría visible resaltada ---------------------------------------------- */
  var chips = document.querySelectorAll(".chip");
  var contenedorChips = $("#chips");
  if ("IntersectionObserver" in window) {
    var observador = new IntersectionObserver(function (entradas) {
      entradas.forEach(function (e) {
        if (!e.isIntersecting) return;
        chips.forEach(function (c) {
          var activo = c.getAttribute("href") === "#" + e.target.id;
          c.classList.toggle("activo", activo);
          if (activo && contenedorChips) {
            var destino = c.offsetLeft - (contenedorChips.clientWidth - c.offsetWidth) / 2;
            contenedorChips.scrollTo({ left: Math.max(destino, 0), behavior: "smooth" });
          }
        });
      });
    }, { rootMargin: "-140px 0px -60% 0px" });
    document.querySelectorAll("[data-seccion], #recomendados, #promociones").forEach(function (s) { observador.observe(s); });
  }

  document.addEventListener("click", function (e) {
    var chip = e.target.closest && e.target.closest("#chips a.chip");
    if (!chip) return;
    var seccion = document.querySelector(chip.getAttribute("href"));
    if (!seccion) return;
    e.preventDefault();
    var barra = document.querySelector(".barra");
    var y = seccion.getBoundingClientRect().top + window.scrollY - (barra ? barra.offsetHeight : 0) - 8;
    window.scrollTo({ top: y, behavior: "smooth" });
  });

  /* ---- ficha del plato --------------------------------------------------------- */
  var ficha = $("#ficha");
  var platoAbierto = null;

  function abrirFicha(id) {
    var p = platos[id];
    if (!p) return;
    platoAbierto = Number(id);
    var contenedor = $("#ficha-foto");
    contenedor.replaceChildren(foto(p));
    $("#ficha-nombre").textContent = p.nombre;
    var precio = $("#ficha-precio");
    precio.replaceChildren(document.createTextNode("$" + p.precio_usd));
    if (p.precio_bs) precio.appendChild(elemento("small", "", "Bs " + p.precio_bs));
    var etiquetas = $("#ficha-etiquetas");
    etiquetas.replaceChildren();
    if (p.agotado) etiquetas.appendChild(elemento("span", "etiqueta", "Agotado hoy"));
    p.etiquetas.forEach(function (e) {
      etiquetas.appendChild(elemento("span", "etiqueta e-" + normalizar(e).replace(/\s+/g, "-"), e));
    });
    $("#ficha-desc").textContent = p.descripcion || "Uno de nuestros platos de " + p.categoria.toLowerCase() + ".";
    var ing = $("#ficha-ingredientes");
    ing.replaceChildren();
    p.ingredientes.forEach(function (i) { ing.appendChild(elemento("span", "", i)); });
    $("#ficha-ingredientes-bloque").hidden = !p.ingredientes.length;
    $("#ficha-credito").textContent = p.credito ? "Foto: " + p.credito : "";
    var tiempo = $("#ficha-tiempo");
    tiempo.textContent = p.minutos ? "Preparación: ~" + p.minutos + " min" : "";
    var opciones = $("#ficha-opciones");
    if (opciones) {
      opciones.replaceChildren();
      (p.opciones || []).forEach(function (o) {
        var etiqueta = elemento("label", "opcion-ficha");
        var caja = elemento("input");
        caja.type = "checkbox";
        caja.value = o;
        etiqueta.append(caja, document.createTextNode(" " + o));
        opciones.appendChild(etiqueta);
      });
      opciones.hidden = !(p.opciones || []).length;
    }
    var pedir = $("#ficha-pedir");
    if (pedir) {
      pedir.hidden = !!p.agotado;
      $("#ficha-cantidad").textContent = "1";
      $("#ficha-nota").value = "";
    }
    if (ficha.showModal) ficha.showModal(); else ficha.setAttribute("open", "");
  }

  document.addEventListener("click", function (e) {
    var boton = e.target.closest("[data-plato]");
    if (boton) { abrirFicha(boton.getAttribute("data-plato")); return; }
    var cerrar = e.target.closest("[data-cerrar]");
    if (cerrar) { cerrar.closest("dialog").close(); return; }
    if (e.target === ficha) ficha.close();
  });

  $("#ficha-preguntar").addEventListener("click", function () {
    var p = platos[platoAbierto];
    ficha.close();
    abrirChat();
    if (p) enviar("Cuéntame sobre el " + p.nombre + ": qué lleva y con qué lo acompaño.", platoAbierto);
  });

  /* ---- asistente ---------------------------------------------------------------- */
  var chat = $("#chat");
  var abrir = $("#abrir-ia");
  var mensajes = $("#chat-mensajes");
  var form = $("#chat-form");
  var texto = $("#chat-texto");
  var historial = guardado.leer("menu-historial") || [];
  var esperando = false;

  function burbuja(rol, contenido) {
    var b = elemento("div", "burbuja " + rol, contenido);
    mensajes.appendChild(b);
    mensajes.scrollTop = mensajes.scrollHeight;
    return b;
  }

  function miniPlatos(ids) {
    var fila = elemento("div", "mini-platos");
    ids.forEach(function (id) {
      var p = platos[id];
      if (!p) return;
      var b = elemento("button", "mini-plato");
      b.type = "button";
      b.setAttribute("data-plato", id);
      b.appendChild(foto(p));
      var t = elemento("span", "", p.nombre);
      t.appendChild(elemento("small", "", "$" + p.precio_usd));
      b.appendChild(t);
      fila.appendChild(b);
    });
    if (fila.children.length) { mensajes.appendChild(fila); mensajes.scrollTop = mensajes.scrollHeight; }
  }

  historial.forEach(function (m) { burbuja(m.rol === "cliente" ? "cliente" : "asistente", m.texto); });

  function abrirChat() {
    chat.hidden = false;
    abrir.hidden = true;
    abrir.setAttribute("aria-expanded", "true");
    setTimeout(function () { texto.focus({ preventScroll: true }); }, 50);
  }

  function cerrarChat() {
    chat.hidden = true;
    abrir.hidden = false;
    abrir.setAttribute("aria-expanded", "false");
    abrir.focus();
  }

  abrir.addEventListener("click", abrirChat);
  $("#cerrar-ia").addEventListener("click", cerrarChat);
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape" && !chat.hidden && !(ficha && ficha.open)) cerrarChat();
  });

  function csrf() {
    var campo = form.querySelector("[name=csrfmiddlewaretoken]");
    return campo ? campo.value : "";
  }

  function enviar(pregunta, platoId) {
    pregunta = (pregunta || "").trim();
    if (!pregunta || esperando) return;
    esperando = true;
    form.querySelector("button").disabled = true;
    burbuja("cliente", pregunta);
    var escribiendo = burbuja("asistente", "");
    escribiendo.setAttribute("aria-label", "El asistente está escribiendo");
    var puntos = elemento("span", "escribiendo");
    puntos.append(elemento("i"), elemento("i"), elemento("i"));
    escribiendo.appendChild(puntos);

    fetch(config.asistente, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-CSRFToken": csrf() },
      body: JSON.stringify({ mensaje: pregunta, historial: historial.slice(-8), plato: platoId || null })
    })
      .then(function (r) { return r.json().then(function (d) { return { ok: r.ok, datos: d }; }); })
      .then(function (res) {
        escribiendo.remove();
        if (!res.ok) { burbuja("asistente error", res.datos.error || "No pude responder ahora. Intenta de nuevo."); return; }
        burbuja("asistente", res.datos.respuesta);
        miniPlatos(res.datos.platos || []);
        historial.push({ rol: "cliente", texto: pregunta }, { rol: "asistente", texto: res.datos.respuesta });
        historial = historial.slice(-16);
        guardado.escribir("menu-historial", historial);
      })
      .catch(function () {
        escribiendo.remove();
        burbuja("asistente error", "Sin conexión con el asistente. Revisa tu internet o pregúntale a tu mesonero.");
      })
      .then(function () {
        esperando = false;
        form.querySelector("button").disabled = false;
      });
  }

  form.addEventListener("submit", function (e) {
    e.preventDefault();
    var pregunta = texto.value;
    texto.value = "";
    enviar(pregunta);
  });

  $("#sugerencias").addEventListener("click", function (e) {
    var b = e.target.closest("button");
    if (b) enviar(b.textContent);
  });

  /* ---- pedido desde la mesa: carrito y «Solicitar mesero» ------------------------- */
  (function () {
    var barra = $("#barra-pedido");
    if (!barra || !config.modo) return;
    var promos = JSON.parse((document.getElementById("datos-promos") || {}).textContent || "{}");
    var hoja = $("#pedido");
    var clave = config.modo === "mesa" ? "menu-pedido-mesa-" + config.mesa : "menu-pedido-retiro";
    var local = {
      leer: function () { try { return JSON.parse(localStorage.getItem(clave)) || []; } catch (e) { return []; } },
      escribir: function (v) { try { localStorage.setItem(clave, JSON.stringify(v)); } catch (e) { /* sin almacenamiento */ } }
    };
    var carrito = local.leer().filter(function (i) {
      return (i.tipo === "producto" ? platos[i.id] && !platos[i.id].agotado : promos[i.id] && !promos[i.id].agotada);
    });
    var tasa = parseFloat(config.tasa) || 0;
    document.body.classList.add("con-pedido");
    // El botón del asistente se ubica siempre encima de la barra, aunque esta crezca con un aviso.
    function medirBarra() { document.documentElement.style.setProperty("--alto-barra", barra.offsetHeight + "px"); }
    if ("ResizeObserver" in window) new ResizeObserver(medirBarra).observe(barra);
    medirBarra();

    function datos(i) { return i.tipo === "producto" ? platos[i.id] : promos[i.id]; }
    function dinero(v) { return "$" + v.toFixed(2).replace(".", ","); }
    function bolivares(v) { return "Bs " + (v * tasa).toLocaleString("es-VE", { minimumFractionDigits: 2, maximumFractionDigits: 2 }); }
    function total() { return carrito.reduce(function (s, i) { return s + parseFloat(datos(i).usd) * i.cantidad; }, 0); }
    function unidades() { return carrito.reduce(function (s, i) { return s + i.cantidad; }, 0); }

    function agregar(tipo, id, cantidad, nota) {
      id = Number(id);
      nota = (nota || "").trim().slice(0, 200);
      var igual = carrito.filter(function (i) { return i.tipo === tipo && i.id === id && (i.nota || "") === nota; })[0];
      if (igual) igual.cantidad = Math.min(igual.cantidad + cantidad, 20);
      else carrito.push({ tipo: tipo, id: id, cantidad: cantidad, nota: nota });
      guardar();
      var vp = $("#ver-pedido");
      vp.classList.remove("rebote"); void vp.offsetWidth; vp.classList.add("rebote");
    }

    function guardar() { local.escribir(carrito); pintar(); }

    function pintar() {
      var n = unidades();
      $("#ver-pedido").hidden = n === 0;
      var botonLlamar = $("#llamar-mesero");
      if (botonLlamar) botonLlamar.classList.toggle("compacto", n > 0);
      $("#pedido-cuenta").textContent = String(n);
      $("#pedido-total").textContent = dinero(total());
      var t2 = $("#pedido-total-2");
      t2.replaceChildren(document.createTextNode(dinero(total())));
      if (tasa) t2.appendChild(elemento("small", "", bolivares(total())));
      var lista = $("#lineas-pedido");
      lista.replaceChildren();
      carrito.forEach(function (i, n) {
        var d = datos(i);
        var li = elemento("li", "linea-pedido");
        var texto = elemento("div", "linea-texto");
        var nombre = elemento("strong", "", d.nombre);
        if (i.tipo === "promocion") nombre.prepend(elemento("span", "mini-promo", "Promo"));
        texto.appendChild(nombre);
        if (i.tipo === "promocion") texto.appendChild(elemento("small", "", d.incluye.join(" · ")));
        if (i.nota) texto.appendChild(elemento("small", "nota", "“" + i.nota + "”"));
        texto.appendChild(elemento("small", "", dinero(parseFloat(d.usd) * i.cantidad)));
        var cont = elemento("div", "contador");
        var menos = elemento("button", "", "−"); menos.type = "button"; menos.setAttribute("data-linea", n); menos.setAttribute("data-delta", "-1");
        menos.setAttribute("aria-label", "Quitar uno de " + d.nombre);
        var mas = elemento("button", "", "+"); mas.type = "button"; mas.setAttribute("data-linea", n); mas.setAttribute("data-delta", "1");
        mas.setAttribute("aria-label", "Agregar uno de " + d.nombre);
        cont.append(menos, elemento("output", "", String(i.cantidad)), mas);
        li.append(texto, cont);
        lista.appendChild(li);
      });
      if (!carrito.length) lista.appendChild(elemento("li", "muted", "Tu pedido está vacío. Toca un plato para agregarlo."));
      var tiempoPedido = $("#pedido-tiempo");
      var lento = carrito.reduce(function (m, i) { return Math.max(m, datos(i).minutos || 0); }, 0);
      tiempoPedido.hidden = !carrito.length;
      tiempoPedido.textContent = "Tiempo estimado en cocina: ~" + ((config.carga || 0) + lento) + " min" + (config.modo === "mesa" ? " desde que el mesero lo envíe" : " desde que verifiquemos el pago");
      var botonEnvio = $("#enviar-pedido") || $("#ir-al-pago");
      if (botonEnvio) botonEnvio.disabled = !carrito.length;
    }

    /* ficha del plato */
    var fichaCantidad = $("#ficha-cantidad");
    document.addEventListener("click", function (e) {
      var paso = e.target.closest("[data-ficha-cantidad]");
      if (paso) {
        var v = Math.max(1, Math.min(20, (parseInt(fichaCantidad.textContent, 10) || 1) + Number(paso.getAttribute("data-ficha-cantidad"))));
        fichaCantidad.textContent = String(v);
        return;
      }
      var promo = e.target.closest("[data-agregar-promo]");
      if (promo) {
        agregar("promocion", promo.getAttribute("data-agregar-promo"), 1, "");
        promo.textContent = "¡Agregada! Agregar otra";
        return;
      }
      var linea = e.target.closest("[data-linea]");
      if (linea) {
        var item = carrito[Number(linea.getAttribute("data-linea"))];
        if (!item) return;
        item.cantidad += Number(linea.getAttribute("data-delta"));
        if (item.cantidad > 20) item.cantidad = 20;
        if (item.cantidad < 1) carrito.splice(carrito.indexOf(item), 1);
        guardar();
      }
    });
    $("#ficha-agregar").addEventListener("click", function () {
      if (platoAbierto === null) return;
      var elegidas = Array.prototype.map.call(document.querySelectorAll("#ficha-opciones input:checked"), function (c) { return c.value; });
      var libre = $("#ficha-nota").value.trim();
      var nota = elegidas.join(", ") + (elegidas.length && libre ? " · " : "") + libre;
      agregar("producto", platoAbierto, parseInt(fichaCantidad.textContent, 10) || 1, nota);
      ficha.close();
    });

    $("#ver-pedido").addEventListener("click", function () { pintar(); if (hoja.showModal) hoja.showModal(); else hoja.setAttribute("open", ""); });
    hoja.addEventListener("click", function (e) { if (e.target === hoja) hoja.close(); });

    /* ---- factura con datos (opcional) ---------------------------------------------- */
    function leerFactura(selector) {
      var caja = $(selector);
      var marcado = caja && caja.querySelector("[data-quiere-factura]");
      if (!marcado || !marcado.checked) return null;
      var datosFactura = {};
      caja.querySelectorAll("[data-fac]").forEach(function (campo) { datosFactura[campo.getAttribute("data-fac")] = campo.value.trim(); });
      return datosFactura;
    }
    function errorFactura(d) {
      if (!d) return "";
      if ((d.nombre || "").length < 3) return "Escribe el nombre o la razón social para la factura.";
      if (!/^[VEJGPvejgp]-?\d{6,9}(-?\d)?$/.test((d.documento || "").replace(/\s/g, ""))) return "La cédula o el RIF no es válido (ej.: V-12345678 o J-123456789).";
      if ((d.direccion || "").length < 5) return "Escribe la dirección fiscal para la factura.";
      if (d.correo && !/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(d.correo)) return "El correo no es válido.";
      return "";
    }
    document.addEventListener("change", function (e) {
      if (!e.target.matches || !e.target.matches("[data-quiere-factura]")) return;
      var campos = e.target.closest(".bloque-factura").querySelector("[data-campos-factura]");
      campos.hidden = !e.target.checked;
      if (e.target.checked) campos.querySelector("input").focus();
    });
    function copiarTexto(texto, boton) {
      if (!navigator.clipboard) return;
      navigator.clipboard.writeText(texto).then(function () {
        var antes = boton.textContent; boton.textContent = "¡Copiado!"; setTimeout(function () { boton.textContent = antes; }, 1500);
      }).catch(function () {});
    }

    if (config.modo === "mesa") {
    /* ---- estado de la mesa: llamado al mesero y cuenta (se consulta cada 12 s) ---------- */
    var estado = $("#estado-llamado");
    var esperandoMesero = false;
    function guardarSesion(k, v) { try { if (v === null) sessionStorage.removeItem(clave + k); else sessionStorage.setItem(clave + k, v); } catch (e) { /* nada */ } }
    function leerSesion(k) { try { return sessionStorage.getItem(clave + k); } catch (e) { return null; } }

    function esperando(mensaje) {
      estado.textContent = mensaje;
      estado.hidden = false;
      estado.classList.remove("atendido");
      esperandoMesero = true;
      guardarSesion("-espera", mensaje);
    }

    var cuentaActual = null;
    function revisar() {
      var url = config.estado + "?mesa=" + encodeURIComponent(config.mesa) + "&c=" + encodeURIComponent(config.codigo);
      var previo = leerSesion("-pedido");
      if (previo) url += "&pedido=" + encodeURIComponent(previo);
      fetch(url, { credentials: "same-origin" })
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (d) {
          if (!d) return;
          if (esperandoMesero && !d.pendiente) {
            esperandoMesero = false;
            guardarSesion("-espera", null);
            estado.textContent = "Tu mesero ya atendió tu mesa. ¡Buen provecho!";
            estado.classList.add("atendido");
            setTimeout(function () { if (!esperandoMesero) estado.hidden = true; }, 8000);
          }
          pintarCuenta(d.cuenta || {});
          var cocina = $("#estado-cocina");
          cocina.hidden = !d.cocina_min;
          if (d.cocina_min) cocina.textContent = "Tu pedido está en cocina: listo en ~" + d.cocina_min + " min.";
        })
        .catch(function () {});
    }
    if (leerSesion("-espera")) esperando(leerSesion("-espera"));
    setInterval(revisar, 12000);

    var enviando = false;
    function solicitar(items, boton, motivo) {
      if (enviando) return;
      enviando = true;
      if (boton) boton.disabled = true;
      fetch(config.solicitar, {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json", "X-CSRFToken": csrf() },
        body: JSON.stringify({ mesa: config.mesa, codigo: config.codigo, items: items, nota: items.length ? $("#pedido-nota").value : (motivo || "") })
      })
        .then(function (r) { return r.json().then(function (d) { return { ok: r.ok, datos: d }; }); })
        .then(function (res) {
          if (!res.ok) { avisar(res.datos.error || "No pudimos avisar al mesero. Intenta de nuevo."); return; }
          if (items.length) { carrito = []; $("#pedido-nota").value = ""; guardar(); if (hoja.open) hoja.close(); }
          esperando(res.datos.mensaje);
        })
        .catch(function () { avisar("Sin conexión. Revisa tu internet o llama al mesero con la mano."); })
        .then(function () { enviando = false; if (boton) boton.disabled = !carrito.length && boton.id === "enviar-pedido"; });
    }

    $("#enviar-pedido").addEventListener("click", function () {
      solicitar(carrito.map(function (i) { return { tipo: i.tipo, id: i.id, cantidad: i.cantidad, nota: i.nota || "" }; }), this);
    });

    /* llamar al mesero en cualquier momento, aunque la mesa ya esté atendida */
    var dlgLlamar = $("#llamar");
    function abrirDialogo(d) { if (d.showModal) d.showModal(); else d.setAttribute("open", ""); }
    $("#llamar-mesero").addEventListener("click", function () { abrirDialogo(dlgLlamar); });
    dlgLlamar.addEventListener("click", function (e) {
      if (e.target === dlgLlamar) { dlgLlamar.close(); return; }
      var b = e.target.closest("[data-motivo]");
      if (!b) return;
      dlgLlamar.close();
      solicitar([], $("#llamar-mesero"), b.getAttribute("data-motivo"));
    });

    /* ---- pedir la cuenta y pagar desde la mesa --------------------------------------- */
    var dlgCuenta = $("#cuenta");
    var btnCuenta = $("#pedir-cuenta");
    var propinaFactor = 0;
    var eligiendo = false;
    var reintentando = false;  // tras «Volver a reportar», el estado REC del servidor no tapa el formulario  // el cliente tocó «Cambiar método»: no se le cambia la pantalla
    var metodos = config.metodos || [];

    var lista = $("#metodos-cliente");
    metodos.forEach(function (m) {
      var b = elemento("button", "metodo-cliente" + (m.digital ? " digital" : ""));
      b.type = "button";
      b.setAttribute("data-metodo", m.clave);
      b.append(elemento("strong", "", m.nombre), elemento("small", "", m.ayuda));
      lista.appendChild(b);
    });

    function formato(valor, usd) {
      var n = Number(valor) || 0;
      var texto = n.toLocaleString("es-VE", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
      return usd ? "$" + texto : "Bs " + texto;
    }
    function mostrarPaso(id) {
      ["cuenta-paso-metodo", "cuenta-paso-digital", "cuenta-paso-caja", "cuenta-paso-estado"].forEach(function (p) {
        document.getElementById(p).hidden = p !== id;
      });
    }
    function montoPropina(s) {
      var base = parseFloat(s.monto) || 0;
      if (propinaFactor === "otra") return Math.max(0, parseFloat(($("#propina-otra").value || "0").replace(",", ".")) || 0);
      return Math.round(base * propinaFactor * 100) / 100;
    }
    function pintarTransferir() {
      var s = cuentaActual && cuentaActual.solicitud;
      if (!s) return;
      var total = (parseFloat(s.monto) || 0) + montoPropina(s);
      $("#monto-transferir").textContent = formato(total, s.en_usd);
      $("#monto-transferir").setAttribute("data-valor", total.toFixed(2));
    }

    function pintarCuenta(c) {
      cuentaActual = c;
      if (c.pedido) guardarSesion("-pedido", String(c.pedido));
      if (c.pagada) {
        btnCuenta.hidden = true;
        guardarSesion("-pedido", null);
        estado.textContent = "¡Pago confirmado! Gracias por visitarnos. Vuelve pronto.";
        estado.classList.add("atendido");
        estado.hidden = false;
        if (dlgCuenta.open) dlgCuenta.close();
        return;
      }
      btnCuenta.hidden = !c.disponible;
      var s = c.solicitud;
      btnCuenta.querySelector("span").textContent = s ? (s.estado === "REP" ? "Pago en revisión" : "Ver mi cuenta") : "Pedir la cuenta";
      $("#cuenta-total").replaceChildren(document.createTextNode("Bs " + (c.total_bs || "")));
      if (c.total_usd) $("#cuenta-total").appendChild(elemento("small", "", "$" + c.total_usd));
      if (!s || (eligiendo && s.estado === "SOL")) { mostrarPaso("cuenta-paso-metodo"); return; }
      if (s.estado === "REP") {
        mostrarPaso("cuenta-paso-estado");
        $("#mensaje-estado").textContent = "Recibimos tu reporte (ref. " + s.referencia + "). Caja está verificando el pago; te avisamos aquí.";
        $("#reintentar-pago").hidden = true;
        return;
      }
      if (s.estado === "REC" && !reintentando) {
        mostrarPaso("cuenta-paso-estado");
        $("#mensaje-estado").textContent = "Caja no encontró tu pago con la referencia " + s.referencia + ". Revísala y repórtala de nuevo, o pasa por caja.";
        $("#reintentar-pago").hidden = false;
        return;
      }
      if (s.digital) {
        if (!document.getElementById("cuenta-paso-digital").hidden) { pintarTransferir(); return; }  // no borrar lo que escribe
        mostrarPaso("cuenta-paso-digital");
        $("#digital-titulo").textContent = "Paga por " + s.nombre + " con estos datos:";
        var dl = $("#datos-pago");
        dl.replaceChildren();
        s.datos.forEach(function (fila) {
          var div = elemento("div");
          var dd = elemento("dd", "", fila.valor);
          var copiar = elemento("button", "copiar", "Copiar");
          copiar.type = "button";
          copiar.setAttribute("data-copiar", fila.valor);
          dd.appendChild(copiar);
          div.append(elemento("dt", "", fila.etiqueta), dd);
          dl.appendChild(div);
        });
        pintarTransferir();
      } else {
        mostrarPaso("cuenta-paso-caja");
        var textoCaja = "Listo: pasa por caja para pagar con " + s.nombre.toLowerCase() + ". Ya tienen tu cuenta lista: " + formato(s.monto, s.en_usd) + ".";
        if (s.igtf) textoCaja += " Al pagar con dólares en efectivo se suma el IGTF (" + s.igtf + " %): total " + formato(s.monto_igtf, true) + ".";
        $("#mensaje-caja").textContent = textoCaja;
      }
    }

    function enviarJson(url, cuerpo, boton) {
      if (boton) boton.disabled = true;
      return fetch(url, {
        method: "POST", credentials: "same-origin",
        headers: { "Content-Type": "application/json", "X-CSRFToken": csrf() },
        body: JSON.stringify(Object.assign({ mesa: config.mesa, codigo: config.codigo }, cuerpo))
      })
        .then(function (r) { return r.json().then(function (d) { return { ok: r.ok, datos: d }; }); })
        .then(function (res) {
          if (!res.ok) { avisar(res.datos.error || "No se pudo completar. Intenta de nuevo."); return; }
          pintarCuenta(res.datos.cuenta || {});
        })
        .catch(function () { avisar("Sin conexión. Revisa tu internet o pasa por caja."); })
        .then(function () { if (boton) boton.disabled = false; });
    }

    var paraLlevar = false;
    btnCuenta.addEventListener("click", function () {
      var c = cuentaActual || {};
      function abrir() { if (cuentaActual) pintarCuenta(cuentaActual); abrirDialogo(dlgCuenta); }
      if (!c.pendientes || c.solicitud) { abrir(); return; }
      var cuando = c.pendientes_min ? "estará lista en aproximadamente " + c.pendientes_min + " min" : "ya está lista y tu mesero te la trae";
      var texto = "Aún falta una parte de tu pedido (" + c.pendientes + " plato" + (c.pendientes === 1 ? "" : "s") + ") que " + cuando + ". Puedes pedir la cuenta igual.";
      preguntar(texto, "Quiero lo que falta para llevar", "Pedir la cuenta igual", "Esperar").then(function (r) {
        if (!r.ok) return;
        paraLlevar = r.marcada;
        abrir();
      });
    });
    dlgCuenta.addEventListener("click", function (e) {
      if (e.target === dlgCuenta) { dlgCuenta.close(); return; }
      var m = e.target.closest("[data-metodo]");
      if (m) {
        propinaFactor = 0;
        eligiendo = false;
        document.getElementById("cuenta-paso-digital").hidden = true;
        var factura = leerFactura("#cuenta-factura");
        var problema = errorFactura(factura);
        if (problema) { avisar(problema); return; }
        enviarJson(config.cuenta, { metodo: m.getAttribute("data-metodo"), factura: factura, para_llevar: paraLlevar }, m);
        return;
      }
      if (e.target.closest("[data-cambiar-metodo]")) { eligiendo = true; mostrarPaso("cuenta-paso-metodo"); return; }
      var p = e.target.closest("[data-propina]");
      if (p) {
        var valor = p.getAttribute("data-propina");
        propinaFactor = valor === "otra" ? "otra" : parseFloat(valor);
        document.querySelectorAll("#chips-propina [data-propina]").forEach(function (b) { b.setAttribute("aria-pressed", b === p ? "true" : "false"); });
        $("#propina-otra").hidden = valor !== "otra";
        if (valor === "otra") $("#propina-otra").focus();
        pintarTransferir();
        return;
      }
      var copiar = e.target.closest("[data-copiar]");
      if (copiar || e.target.id === "copiar-monto") {
        var texto = copiar ? copiar.getAttribute("data-copiar") : $("#monto-transferir").getAttribute("data-valor").replace(".", ",");
        var boton = copiar || e.target;
        if (navigator.clipboard) navigator.clipboard.writeText(texto).then(function () {
          var antes = boton.textContent; boton.textContent = "¡Copiado!"; setTimeout(function () { boton.textContent = antes; }, 1500);
        }).catch(function () {});
      }
    });
    $("#propina-otra").addEventListener("input", pintarTransferir);
    $("#reportar-pago").addEventListener("click", function () {
      var s = cuentaActual && cuentaActual.solicitud;
      if (!s) return;
      var ref = $("#pago-referencia").value.trim();
      if (ref.length < 4) { avisar("Escribe el número de referencia del pago."); $("#pago-referencia").focus(); return; }
      reintentando = false;
      enviarJson(config.reportar, { referencia: ref, origen: $("#pago-origen").value, propina: montoPropina(s).toFixed(2) }, this);
    });
    $("#reintentar-pago").addEventListener("click", function () {
      var s = cuentaActual && cuentaActual.solicitud;
      if (!s) return;
      reintentando = true;
      pintarCuenta(cuentaActual);
    });

    revisar();
    }

    /* ---- pedido para retirar en el local (enlace general del menú) ----------------------- */
    if (config.modo === "retiro") {
      var dlgRetiro = $("#retiro");
      var cotizacion = null;
      var metodoRetiro = null;
      var claveCurso = "menu-retiro-en-curso";
      try {
        var enCurso = localStorage.getItem(claveCurso);
        if (enCurso) { var enlace = $("#retiro-en-curso"); enlace.href = enCurso; enlace.hidden = false; }
      } catch (e) { /* sin almacenamiento */ }

      var listaRetiro = $("#metodos-retiro");
      (config.metodos_retiro || []).forEach(function (m) {
        var b = elemento("button", "metodo-cliente digital");
        b.type = "button";
        b.setAttribute("data-metodo-retiro", m.clave);
        b.append(elemento("strong", "", m.nombre), elemento("small", "", m.ayuda));
        listaRetiro.appendChild(b);
      });

      function pasoRetiro(cual) {
        $("#retiro-paso-datos").hidden = cual !== "datos";
        $("#retiro-paso-pago").hidden = cual !== "pago";
      }
      function errorRetiro(n, texto) {
        var nodo = $("#retiro-error-" + n);
        nodo.textContent = texto || "";
        nodo.hidden = !texto;
      }
      function datosRetiro() {
        return {
          nombre: $("#ret-nombre").value.trim().replace(/\s+/g, " "),
          telefono: $("#ret-telefono").value.replace(/\s/g, ""),
          correo: $("#ret-correo").value.trim(),
          factura: leerFactura("#retiro-factura")
        };
      }
      function validarDatos(d) {
        if (d.nombre.length < 2 || !/^[A-Za-zÁÉÍÓÚÜÑáéíóúüñ' .-]+$/.test(d.nombre)) return ["#ret-nombre", "Escribe tu nombre (solo letras)."];
        if (!/^(\+?58)?0?\d{3}-?\d{7}$/.test(d.telefono)) return ["#ret-telefono", "Escribe un teléfono válido, por ejemplo 0414-1234567."];
        if (d.correo && !/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(d.correo)) return ["#ret-correo", "El correo no es válido."];
        var problema = errorFactura(d.factura);
        if (problema) return ["#retiro-factura input", problema];
        return null;
      }
      function pintarPago(c) {
        cotizacion = c;
        $("#retiro-pago-titulo").textContent = "Paga por " + c.nombre + " con estos datos:";
        var dl = $("#retiro-datos-pago");
        dl.replaceChildren();
        c.datos.forEach(function (fila) {
          var div = elemento("div");
          var dd = elemento("dd", "", fila.valor);
          var copiar = elemento("button", "copiar", "Copiar");
          copiar.type = "button";
          copiar.setAttribute("data-copiar", fila.valor);
          dd.appendChild(copiar);
          div.append(elemento("dt", "", fila.etiqueta), dd);
          dl.appendChild(div);
        });
        var monto = Number(c.monto).toLocaleString("es-VE", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
        $("#retiro-monto").textContent = (c.en_usd ? "$" : "Bs ") + monto;
        $("#retiro-monto").setAttribute("data-valor", String(c.monto).replace(".", ","));
      }
      function itemsRetiro() {
        return carrito.map(function (i) { return { tipo: i.tipo, id: i.id, cantidad: i.cantidad, nota: i.nota || "" }; });
      }

      $("#ir-al-pago").addEventListener("click", function () {
        if (!carrito.length) return;
        hoja.close();
        errorRetiro(1, ""); errorRetiro(2, "");
        pasoRetiro("datos");
        abrirDialogoRetiro();
      });
      function abrirDialogoRetiro() { if (dlgRetiro.showModal) dlgRetiro.showModal(); else dlgRetiro.setAttribute("open", ""); }

      dlgRetiro.addEventListener("click", function (e) {
        if (e.target === dlgRetiro) { dlgRetiro.close(); return; }
        var copiar = e.target.closest("[data-copiar]");
        if (copiar) { copiarTexto(copiar.getAttribute("data-copiar"), copiar); return; }
        if (e.target.id === "retiro-copiar-monto") { copiarTexto($("#retiro-monto").getAttribute("data-valor"), e.target); return; }
        if (e.target.id === "retiro-volver") { pasoRetiro("datos"); return; }
        var m = e.target.closest("[data-metodo-retiro]");
        if (!m) return;
        var d = datosRetiro();
        var problema = validarDatos(d);
        if (problema) { errorRetiro(1, problema[1]); var campo = dlgRetiro.querySelector(problema[0]); if (campo) campo.focus(); return; }
        errorRetiro(1, "");
        metodoRetiro = m.getAttribute("data-metodo-retiro");
        m.disabled = true;
        fetch(config.cotizar, {
          method: "POST", credentials: "same-origin",
          headers: { "Content-Type": "application/json", "X-CSRFToken": csrf() },
          body: JSON.stringify({ metodo: metodoRetiro, items: itemsRetiro() })
        })
          .then(function (r) { return r.json().then(function (j) { return { ok: r.ok, datos: j }; }); })
          .then(function (res) {
            if (!res.ok) { errorRetiro(1, res.datos.error || "No pudimos calcular el monto."); return; }
            pintarPago(res.datos);
            pasoRetiro("pago");
          })
          .catch(function () { errorRetiro(1, "Sin conexión. Revisa tu internet."); })
          .then(function () { m.disabled = false; });
      });

      $("#retiro-enviar").addEventListener("click", function () {
        var boton = this;
        var referencia = $("#ret-referencia").value.trim();
        var archivo = $("#ret-comprobante").files[0];
        if (!/^[A-Za-z0-9#\-./ ]{4,60}$/.test(referencia)) { errorRetiro(2, "Escribe la referencia del pago (al menos 4 caracteres)."); $("#ret-referencia").focus(); return; }
        if (!archivo) { errorRetiro(2, "Sube la captura o el comprobante del pago."); return; }
        if (archivo.size > 6 * 1024 * 1024) { errorRetiro(2, "El comprobante pesa más de 6 MB."); return; }
        errorRetiro(2, "");
        var d = datosRetiro();
        var cuerpo = new FormData();
        cuerpo.append("datos", JSON.stringify({
          nombre: d.nombre, telefono: d.telefono, correo: d.correo, factura: d.factura, metodo: metodoRetiro,
          referencia: referencia, nota: $("#pedido-nota").value, items: itemsRetiro(), monto: cotizacion.monto
        }));
        cuerpo.append("comprobante", archivo);
        boton.disabled = true;
        boton.textContent = "Enviando…";
        fetch(config.retiro, { method: "POST", credentials: "same-origin", headers: { "X-CSRFToken": csrf() }, body: cuerpo })
          .then(function (r) { return r.json().then(function (j) { return { ok: r.ok, datos: j }; }); })
          .then(function (res) {
            if (!res.ok) {
              if (res.datos.cotizacion) pintarPago(res.datos.cotizacion);
              errorRetiro(2, res.datos.error || "No pudimos enviar tu pedido. Intenta de nuevo.");
              return;
            }
            carrito = []; guardar();
            try { localStorage.setItem(claveCurso, res.datos.url); } catch (e) { /* nada */ }
            window.location.href = res.datos.url;
          })
          .catch(function () { errorRetiro(2, "Sin conexión. Revisa tu internet e intenta de nuevo."); })
          .then(function () { boton.disabled = false; boton.textContent = "Enviar pedido"; });
      });
    }

    pintar();
  })();
})();
