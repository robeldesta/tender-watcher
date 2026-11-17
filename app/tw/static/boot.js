// Runs before the page paints: keeps the sidebar collapsed if the person collapsed it last time.
try { if (localStorage.getItem("tw-nav-min") === "1") document.documentElement.classList.add("nav-min"); } catch (e) {}
