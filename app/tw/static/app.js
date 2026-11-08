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
(function () {
  var nav = document.querySelector(".side-nav");
  if (nav && window.matchMedia("(max-width: 900px)").matches) nav.removeAttribute("open");
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
        var mb = room.querySelector(".room-side .meter-box span"); if (mb) mb.textContent = pr.done + " of " + pr.total + " requirements done";
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
    if (c) c.closest(".req").classList.toggle("done", c.checked);
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
    room.querySelectorAll(".room-main input, .room-main textarea, .room-main button").forEach(function (el) { el.disabled = true; });
    var tries = 0;
    (function poll() {
      fetch("/bids/" + id + "/state", { credentials: "same-origin" }).then(function (r) { return r.json(); }).then(function (j) {
        if (j.state !== "drafting" || ++tries > 120) location.reload(); else setTimeout(poll, 4000);
      }).catch(function () { setTimeout(poll, 8000); });
    })();
  }
})();
