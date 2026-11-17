// Small page behaviours. No inline scripts: the Content-Security-Policy allows only files from this site.
(function () {
  var m = /^#t\/(.+)$/.exec(location.hash);
  if (m && document.body.dataset.page === "list") location.replace("/t/" + m[1]);
  var f = document.getElementById("collect");
  if (f) f.addEventListener("submit", function () {
    var b = document.getElementById("go"); b.disabled = true; b.textContent = "Reading…";
    document.getElementById("wait").hidden = false;
  });
})();
(function () {  // console sidebar starts closed on phones
  var nav = document.querySelector(".side-nav");
  if (nav && window.matchMedia("(max-width: 900px)").matches) nav.removeAttribute("open");
})();
(function () {  // app shell: phone drawer, collapsed sidebar, "/" to search, one open menu at a time
  var body = document.body, root = document.documentElement;
  document.addEventListener("click", function (e) {
    if (e.target.closest("[data-nav-open]")) body.classList.add("nav-open");
    else if (e.target.closest("[data-nav-close]")) body.classList.remove("nav-open");
    if (e.target.closest("[data-nav-min]")) {
      var on = root.classList.toggle("nav-min");
      try { localStorage.setItem("tw-nav-min", on ? "1" : "0"); } catch (err) {}
    }
    document.querySelectorAll("details.menu[open]").forEach(function (d) { if (!d.contains(e.target)) d.removeAttribute("open"); });
  });
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape") {
      body.classList.remove("nav-open");
      document.querySelectorAll("details.menu[open]").forEach(function (d) { d.removeAttribute("open"); });
    }
    var t = e.target, typing = t.closest && t.closest("input,textarea,select,[contenteditable]");
    if (e.key === "/" && !typing && !e.metaKey && !e.ctrlKey) {
      var q = document.getElementById("q") || document.getElementById("gq");
      if (q) { e.preventDefault(); q.focus(); q.select(); }
    }
  });
})();
(function () {  // toasts fade out after a few seconds
  document.querySelectorAll(".toast").forEach(function (t) {
    setTimeout(function () { t.classList.add("gone"); }, 4000);
    t.addEventListener("click", function () { t.classList.add("gone"); });
  });
})();
(function () {  // confirmation dialog for forms marked data-ask
  var dlg = document.getElementById("confirm-dialog");
  document.addEventListener("submit", function (e) {
    var f = e.target, msg = f.getAttribute("data-ask");
    if (!msg || f.dataset.confirmed || !dlg || !dlg.showModal) return;
    e.preventDefault();
    dlg.querySelector("[data-cd-text]").textContent = msg;
    var ok = dlg.querySelector("[data-cd-ok]");
    ok.textContent = f.getAttribute("data-ask-label") || "Confirm";
    dlg.returnValue = "";
    dlg.showModal();
    dlg.addEventListener("close", function once() {
      dlg.removeEventListener("close", once);
      if (dlg.returnValue === "ok") { f.dataset.confirmed = "1"; if (f.requestSubmit) f.requestSubmit(); else f.submit(); }
    });
  }, true);
})();
(function () {  // filter bars submit when a select changes
  document.querySelectorAll("form[data-autosubmit] select").forEach(function (s) {
    s.addEventListener("change", function () { s.form.submit(); });
  });
})();
(function () {  // tabs inside one page (Bid Room): panels stay in the page so autosave still sees every field
  document.querySelectorAll("[data-tabs]").forEach(function (bar) {
    var tabs = bar.querySelectorAll("[role=tab]");
    function show(id, push) {
      tabs.forEach(function (t) {
        var on = t.getAttribute("aria-controls") === id;
        t.setAttribute("aria-selected", on ? "true" : "false");
        t.tabIndex = on ? 0 : -1;
        var p = document.getElementById(t.getAttribute("aria-controls"));
        if (p) p.hidden = !on;
      });
      if (push) try { history.replaceState(null, "", "#" + id.replace(/^tab-/, "")); } catch (e) {}
    }
    tabs.forEach(function (t, i) {
      t.addEventListener("click", function () { show(t.getAttribute("aria-controls"), true); });
      t.addEventListener("keydown", function (e) {
        var n = e.key === "ArrowRight" ? i + 1 : e.key === "ArrowLeft" ? i - 1 : -9;
        if (n === -9) return;
        var next = tabs[(n + tabs.length) % tabs.length]; next.focus(); next.click();
      });
    });
    document.querySelectorAll("[data-tab-link]").forEach(function (a) {
      a.addEventListener("click", function (e) { e.preventDefault(); show("tab-" + a.getAttribute("data-tab-link"), true); window.scrollTo(0, 0); });
    });
    var want = "tab-" + location.hash.slice(1);
    show(document.getElementById(want) ? want : tabs[0].getAttribute("aria-controls"), false);
  });
})();
(function () {  // document vault: find as you type
  var box = document.querySelector("[data-filter-rows]");
  if (!box) return;
  var rows = document.querySelectorAll(box.getAttribute("data-filter-rows"));
  box.addEventListener("input", function () {
    var q = box.value.trim().toLowerCase();
    rows.forEach(function (r) { r.hidden = q && r.textContent.toLowerCase().indexOf(q) < 0; });
  });
})();
(function () {  // print button on the printable bid
  var b = document.querySelector("[data-print]");
  if (b) b.addEventListener("click", function () { window.print(); });
})();
(function () {  // profile tables: add a row by copying the last one, empty
  document.querySelectorAll(".add-row").forEach(function (btn) {
    btn.addEventListener("click", function () {
      var body = document.querySelector('[data-rows="' + btn.dataset.for + '"] tbody');
      var last = body.lastElementChild, row = last.cloneNode(true);
      row.querySelectorAll("input").forEach(function (i) { i.value = ""; });
      body.appendChild(row); row.querySelector("input").focus();
    });
  });
})();
(function () {  // Bid Room: the bid's forms, autosave, price lines, drafting status
  var room = document.getElementById("room");
  if (!room) return;
  var id = room.dataset.bid, csrf = room.dataset.csrf, timer = null, busy = false, again = false, reload = false;
  var saveEl = room.querySelector("[data-save]");
  var values = {};
  try { values = JSON.parse(document.getElementById("bid-values").textContent); } catch (e) {}
  function num(v) { v = String(v || "").replace(/[^\d.]/g, ""); return v ? parseFloat(v) : NaN; }
  function money(n) { return n.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 }); }
  function esc(t) { var d = document.createElement("div"); d.textContent = t; return d.innerHTML; }

  // -- one form at a time, chosen in the rail
  var panes = room.querySelectorAll("[data-pane]");
  function openForm(key, push) {
    var found = false;
    panes.forEach(function (p) { var on = p.dataset.pane === key; p.hidden = !on; found = found || on; });
    if (!found && panes.length) { panes[0].hidden = false; key = panes[0].dataset.pane; }
    room.querySelectorAll(".rail-item").forEach(function (a) { a.setAttribute("aria-current", a.dataset.form === key ? "true" : "false"); });
    if (push) try { history.replaceState(null, "", "#f-" + key); } catch (e) {}
  }
  room.addEventListener("click", function (e) {
    var a = e.target.closest("a[data-form]");
    if (a) { e.preventDefault(); openForm(a.dataset.form, true);
      var tab = room.querySelector("[aria-controls=tab-forms]"); if (tab && tab.getAttribute("aria-selected") !== "true") tab.click();
      var top = room.querySelector(".forms-grid"); if (top && top.getBoundingClientRect().top < 0) top.scrollIntoView(); }
  });
  function fromHash() { openForm(location.hash.indexOf("#f-") === 0 ? location.hash.slice(3) : "", false); }
  window.addEventListener("hashchange", fromHash);
  fromHash();

  // -- letters: fill {tokens} the way the server does
  var LABELS = { total_figures: "bid price (fill the price schedule)", total_words: "bid price in words", validity_days: "bid validity in days",
    completion: "completion or delivery period", signatory_name: "name of the person signing", signatory_title: "title of the person signing",
    legal_name: "company name", tin: "TIN", reference: "procurement reference", address: "company address", warranty: "warranty period",
    security_amount: "bid security amount", performance_security: "performance security" };
  function fill(text) {
    return text.replace(/\{([a-z_]+)\}/g, function (m, k) {
      if (!(k in values)) return m;
      var v = values[k];
      return v === null || String(v).trim() === "" ? "[TO CONFIRM: " + (LABELS[k] || k.replace(/_/g, " ")) + "]" : String(v);
    });
  }
  function preview(ta) {
    var box = room.querySelector('[data-preview="' + ta.dataset.letter + '"]');
    if (!box) return;
    box.innerHTML = fill(ta.value).split(/\n\s*\n/).filter(function (p) { return p.trim(); }).map(function (p) {
      return "<p>" + esc(p.trim()).replace(/\[TO CONFIRM[^\]]*\]/g, function (m) { return '<mark class="tc">' + m + "</mark>"; }) + "</p>";
    }).join("");
  }
  function previews() { room.querySelectorAll("[data-letter]").forEach(preview); }

  // -- tables
  function rowsOf(tbody) {
    var rows = [];
    tbody.querySelectorAll("tr").forEach(function (tr) {
      var r = {}, any = false;
      tr.querySelectorAll("[data-col]").forEach(function (i) { r[i.dataset.col] = i.value; if (i.value.trim()) any = true; });
      if (any) rows.push(r);
    });
    return rows;
  }
  function addRow(tbody, data) {
    var row = tbody.lastElementChild.cloneNode(true);
    row.querySelectorAll("input").forEach(function (i) { i.value = data && data[i.dataset.col] != null ? data[i.dataset.col] : ""; });
    var amt = row.querySelector("[data-amount]"); if (amt) amt.textContent = "";
    var last = tbody.lastElementChild;
    if (data && !Array.prototype.some.call(last.querySelectorAll("input"), function (i) { return i.value; })) tbody.insertBefore(row, last);
    else tbody.appendChild(row);
    return row;
  }
  function average() {
    room.querySelectorAll("[data-average]").forEach(function (p) {
      var t = room.querySelector('[data-table="' + p.dataset.average + '"] tbody'), vals = [];
      rowsOf(t).forEach(function (r) { var v = num(r.amount_etb); if (!isNaN(v)) vals.push(v); });
      p.textContent = vals.length ? "Average over " + vals.length + " years: ETB " + money(vals.reduce(function (a, b) { return a + b; }, 0) / vals.length) : "";
    });
  }

  // -- price schedule
  function fin() {
    var rows = [];
    room.querySelectorAll("#fin tbody tr").forEach(function (tr) {
      var r = {};
      tr.querySelectorAll("[data-col]").forEach(function (i) { r[i.dataset.col] = i.value; });
      var q = num(r.qty), p = num(r.price), cell = tr.querySelector("[data-amount]");
      cell.textContent = isNaN(q) || isNaN(p) ? "" : money(q * p);
      if (r.item || r.unit || r.qty || r.price) rows.push(r);
    });
    return rows;
  }
  function totals() {
    var t = 0;
    fin().forEach(function (r) { var q = num(r.qty), p = num(r.price); if (!isNaN(q) && !isNaN(p)) t += q * p; });
    var el = room.querySelector("[data-total]"); if (el) el.textContent = money(t);
  }

  function collect() {
    var d = { done: [], financial: fin(), forms: {}, details: {} };
    room.querySelectorAll("[data-field]").forEach(function (f) { if (f.dataset.field) d[f.dataset.field] = f.value; });
    room.querySelectorAll("[data-done]").forEach(function (c) { if (c.checked) d.done.push(+c.dataset.done); });
    function form(k) { return d.forms[k] || (d.forms[k] = {}); }
    room.querySelectorAll("[data-on]").forEach(function (c) { form(c.dataset.on).on = c.checked; });
    room.querySelectorAll("[data-letter]").forEach(function (t) { form(t.dataset.letter).text = t.value; });
    room.querySelectorAll("[data-text]").forEach(function (t) { form(t.dataset.text).text = t.value; });
    room.querySelectorAll("[data-table]").forEach(function (t) { form(t.dataset.table).rows = rowsOf(t.querySelector("tbody")); });
    room.querySelectorAll("[data-detail]").forEach(function (i) { d.details[i.dataset.detail] = i.value; });
    var k = room.querySelector("[data-kind-select]"); if (k) d.kind = k.value;
    var sub = room.querySelector("[data-submitted]");
    if (sub) d.state = sub.checked ? "submitted" : "open";
    return d;
  }
  function show(text, bad) { if (saveEl) { saveEl.textContent = text; saveEl.classList.toggle("bad", !!bad); } }
  function after(j) {
    show("Saved " + j.saved);
    var pr = j.progress, p = room.querySelectorAll("[data-progress]"), bar = room.querySelector("[data-bar]");
    p.forEach(function (el) { el.textContent = pr.done + " of " + pr.total + " done"; });
    if (bar) bar.style.width = (pr.total ? Math.round(pr.done * 100 / pr.total) : 0) + "%";
    var box = room.querySelector("[data-confirm-box]"), ul = room.querySelector("[data-confirm]");
    if (box && ul) {
      ul.textContent = "";
      j.confirm.forEach(function (c) { var li = document.createElement("li"); li.textContent = c; ul.appendChild(li); });
      box.hidden = !j.confirm.length;
    }
    if (j.values) { values = j.values; previews(); }
    Object.keys(j.forms || {}).forEach(function (k) {
      var ok = j.forms[k];
      room.querySelectorAll('.rail-item[data-form="' + k + '"]').forEach(function (a) { a.classList.toggle("done", ok); });
      var st = room.querySelector('[data-state-for="' + k + '"]'); if (st) { st.textContent = ok ? "Ready" : "To finish"; st.classList.toggle("ok", ok); }
    });
    var c = room.querySelector("[data-forms-count]"); if (c) c.textContent = j.forms_done + "/" + j.forms_total;
    var ft = room.querySelector("[data-forms-text]"); if (ft) ft.textContent = j.forms_done + " of " + j.forms_total + " ready";
    var fb = room.querySelector("[data-forms-bar]"); if (fb) fb.style.width = (j.forms_total ? Math.round(j.forms_done * 100 / j.forms_total) : 0) + "%";
    var v = room.querySelector("[data-vat]"), vr = room.querySelector("[data-vat-row]"), g = room.querySelector("[data-grand]"), w = room.querySelector("[data-words]");
    if (v) v.textContent = money(j.vat); if (vr) vr.hidden = !j.vat;
    if (g) g.textContent = money(j.total + j.vat); if (w) w.textContent = (j.values && j.values.total_words) || "";
  }
  function save() {
    if (busy) { again = true; return; }
    busy = true; show("Saving…");
    fetch("/bids/" + id + "/save", { method: "POST", headers: { "content-type": "application/json", "x-csrf": csrf },
      body: JSON.stringify(collect()), credentials: "same-origin" })
      .then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); })
      .then(function (j) { if (reload) { location.reload(); return; } after(j); })
      .catch(function () { show("Not saved. Check your connection; it retries when you type.", true); })
      .then(function () { busy = false; if (again) { again = false; save(); } });
  }
  function soon() { clearTimeout(timer); timer = setTimeout(save, 800); }
  room.addEventListener("input", function (e) {
    if (e.target.closest("#fin")) totals();
    if (e.target.dataset.letter) preview(e.target);
    if (e.target.closest("[data-table]")) average();
    soon();
  });
  room.addEventListener("change", function (e) {
    var c = e.target.closest("[data-done]");
    if (c) { var li = c.closest(".req"); li.classList.toggle("done", c.checked);
      var mk = li.querySelector("[data-mark]"); if (mk) mk.classList.toggle("ok", c.checked); }
    var on = e.target.closest("[data-on]");
    if (on) room.querySelectorAll('.rail-item[data-form="' + on.dataset.on + '"]').forEach(function (a) { a.classList.toggle("off", !on.checked); });
    if (e.target.matches("[data-kind-select], [data-reload]")) { reload = true; clearTimeout(timer); save(); return; }
    soon();
  });
  room.addEventListener("click", function (e) {
    var del = e.target.closest(".del-row");
    if (del) {
      var body = del.closest("tbody"), tr = del.closest("tr");
      if (body.children.length > 1) tr.remove(); else tr.querySelectorAll("input").forEach(function (i) { i.value = ""; });
      totals(); average(); soon(); return;
    }
    var add = e.target.closest("[data-add-row]");
    if (add) { var r = addRow(room.querySelector('[data-table="' + add.dataset.addRow + '"] tbody')); r.querySelector("input").focus(); return; }
    var refill = e.target.closest("[data-refill]");
    if (refill) {
      var tb = room.querySelector('[data-table="' + refill.dataset.refill + '"] tbody'), rows = JSON.parse(refill.dataset.rows || "[]");
      while (tb.children.length > 1) tb.removeChild(tb.firstElementChild);
      tb.lastElementChild.querySelectorAll("input").forEach(function (i) { i.value = ""; });
      rows.forEach(function (r) { addRow(tb, r); });
      average(); soon(); return;
    }
    var std = e.target.closest("[data-standard-for]");
    if (std) { var ta = room.querySelector('[data-letter="' + std.dataset.standardFor + '"]'); ta.value = ta.dataset.standard; preview(ta); soon(); return; }
    var out = e.target.closest("[data-use-outline]");
    if (out) { var t = out.closest("[data-pane]").querySelector("textarea[data-outline]");
      if (!t.value.trim() || t.value.indexOf(t.dataset.outline) === -1) t.value = t.dataset.outline + (t.value.trim() ? "\n\n" + t.value : "");
      t.focus(); soon(); return; }
    var po = e.target.closest("[data-paste-open]");
    if (po) { var bx = room.querySelector("[data-paste-box]"); bx.hidden = !bx.hidden; if (!bx.hidden) bx.querySelector("textarea").focus(); return; }
    if (e.target.closest("[data-paste-add]")) {
      var area = room.querySelector("[data-paste]"), body = room.querySelector("#fin tbody"), n = 0;
      area.value.split(/\r?\n/).forEach(function (line) {
        if (!line.trim()) return;
        var c = line.split("\t").map(function (x) { return x.trim(); });
        if (c.length < 2) c = line.split(/\s{2,}/).map(function (x) { return x.trim(); });
        var r = {};
        if (c.length >= 5) r = { no: c[0], item: c[1], unit: c[2], qty: c[3], price: c[4] };
        else if (c.length === 4) r = isNaN(num(c[0])) || /[a-z]{3}/i.test(c[0]) ? { item: c[0], unit: c[1], qty: c[2], price: c[3] } : { no: c[0], item: c[1], unit: c[2], qty: c[3] };
        else if (c.length === 3) r = { item: c[0], unit: c[1], qty: c[2] };
        else r = { item: c.join(" ") };
        addRow(body, r); n++;
      });
      area.value = ""; room.querySelector("[data-paste-box]").hidden = true;
      totals(); soon(); show(n + " line" + (n === 1 ? "" : "s") + " added. Saving…");
    }
  });
  var addLine = document.getElementById("add-line");
  if (addLine) addLine.addEventListener("click", function () {
    var row = addRow(room.querySelector("#fin tbody")); row.querySelector("input").focus();
  });
  function flush() {
    clearTimeout(timer); timer = null;
    return fetch("/bids/" + id + "/save", { method: "POST", keepalive: true, credentials: "same-origin",
      headers: { "content-type": "application/json", "x-csrf": csrf }, body: JSON.stringify(collect()) });
  }
  window.addEventListener("pagehide", function () { if (timer) flush(); });
  var form = document.getElementById("draft-form");
  if (form) form.addEventListener("submit", function (e) {
    e.preventDefault();
    var b = form.querySelector("button"); b.disabled = true; b.textContent = "Drafting…";
    flush().catch(function () {}).then(function () { form.submit(); });
  });
  totals(); average();
  if (room.dataset.state === "drafting") {
    room.querySelectorAll(".room-main input, .room-main textarea, .room-main select, .room-main button:not([role=tab])").forEach(function (el) { el.disabled = true; });
    var tries = 0;
    (function poll() {
      fetch("/bids/" + id + "/state", { credentials: "same-origin" }).then(function (r) { return r.json(); }).then(function (j) {
        if (j.state !== "drafting" || ++tries > 120) location.reload(); else setTimeout(poll, 4000);
      }).catch(function () { setTimeout(poll, 8000); });
    })();
  }
})();
(function () {  // links to #add open the add drawer
  function open() { var d = document.getElementById("add"); if (d && d.tagName === "DETAILS" && location.hash === "#add") d.open = true; }
  window.addEventListener("hashchange", open); open();
  document.addEventListener("click", function (e) {
    if (e.target.closest("[data-open-add]")) { var d = document.getElementById("add"); if (d) d.open = true; }
  });
})();
