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
