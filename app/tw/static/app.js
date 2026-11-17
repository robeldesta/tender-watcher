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
(function () {  // Bid Room: autosave, price lines, drafting status
  var room = document.getElementById("room");
  if (!room) return;
  var id = room.dataset.bid, csrf = room.dataset.csrf, timer = null, busy = false, again = false;
  var saveEl = room.querySelector("[data-save]");
  function num(v) { v = String(v || "").replace(/[^\d.]/g, ""); return v ? parseFloat(v) : NaN; }
  function money(n) { return n.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 }); }
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
    var d = { done: [], financial: fin() };
    room.querySelectorAll("[data-field]").forEach(function (f) { d[f.dataset.field] = f.value; });
    room.querySelectorAll("[data-done]").forEach(function (c) { if (c.checked) d.done.push(+c.dataset.done); });
    var sub = room.querySelector("[data-submitted]");
    if (sub) d.state = sub.checked ? "submitted" : "open";
    return d;
  }
  function show(text, bad) { if (saveEl) { saveEl.textContent = text; saveEl.classList.toggle("bad", !!bad); } }
  function save() {
    if (busy) { again = true; return; }
    busy = true; show("Saving…");
    fetch("/bids/" + id + "/save", { method: "POST", headers: { "content-type": "application/json", "x-csrf": csrf },
      body: JSON.stringify(collect()), credentials: "same-origin" })
      .then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); })
      .then(function (j) {
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
      })
      .catch(function () { show("Not saved. Check your connection; it retries when you type.", true); })
      .then(function () { busy = false; if (again) { again = false; save(); } });
  }
  function soon() { clearTimeout(timer); timer = setTimeout(save, 800); }
  room.addEventListener("input", function (e) { if (e.target.closest("#fin")) totals(); soon(); });
  room.addEventListener("change", function (e) {
    var c = e.target.closest("[data-done]");
    if (c) { var li = c.closest(".req"); li.classList.toggle("done", c.checked);
      var mk = li.querySelector("[data-mark]"); if (mk) mk.classList.toggle("ok", c.checked); }
    soon();
  });
  room.addEventListener("click", function (e) {
    var del = e.target.closest(".del-row");
    if (del) {
      var body = del.closest("tbody"), tr = del.closest("tr");
      if (body.children.length > 1) tr.remove(); else tr.querySelectorAll("input").forEach(function (i) { i.value = ""; });
      totals(); soon();
    }
  });
  var add = document.getElementById("add-line");
  if (add) add.addEventListener("click", function () {
    var body = room.querySelector("#fin tbody"), row = body.lastElementChild.cloneNode(true);
    row.querySelectorAll("input").forEach(function (i) { i.value = ""; });
    row.querySelector("[data-amount]").textContent = "";
    body.appendChild(row); row.querySelector("input").focus();
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
  totals();
  if (room.dataset.state === "drafting") {
    room.querySelectorAll(".room-main input, .room-main textarea, .room-main button:not([role=tab])").forEach(function (el) { el.disabled = true; });
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
