/* GTA VI PC Tracker — minimal vanilla JS. Everything here is an enhancement: the page works without it. */
(function () {
  "use strict";
  var doc = document.documentElement;
  var reduce = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  function store(key, value) {
    try {
      if (value === undefined) return window.localStorage.getItem(key);
      window.localStorage.setItem(key, value);
    } catch (e) { return null; }
    return null;
  }

  /* ---------------- theme (night default, "day" optional) */
  var toggle = document.querySelector("[data-theme-toggle]");
  function applyTheme(t) {
    doc.setAttribute("data-theme", t);
    if (toggle) {
      toggle.setAttribute("aria-pressed", t === "day" ? "true" : "false");
      toggle.title = t === "day" ? toggle.dataset.night : toggle.dataset.day;
    }
  }
  applyTheme(store("gta6pt:theme") === "day" ? "day" : "night");
  if (toggle) {
    toggle.hidden = false;
    toggle.addEventListener("click", function () {
      var next = doc.getAttribute("data-theme") === "day" ? "night" : "day";
      applyTheme(next);
      store("gta6pt:theme", next);
    });
  }

  /* ---------------- language switch keeps the section */
  var langLink = document.querySelector("[data-keep-hash]");
  if (langLink) {
    langLink.addEventListener("click", function () {
      store("gta6pt:lang", langLink.getAttribute("hreflang"));
      if (location.hash) langLink.href = langLink.href.split("#")[0] + location.hash;
    });
  }

  /* ---------------- relative times */
  var T = doc.dataset;
  function rel(iso) {
    var t = Date.parse(iso);
    if (isNaN(t)) return null;
    var mins = Math.floor((Date.now() - t) / 60000);
    if (mins < 2) return T.tJust;
    if (mins < 60) return T.tMin.replace("{n}", mins);
    if (mins < 48 * 60) return T.tH.replace("{n}", Math.floor(mins / 60));
    return T.tD.replace("{n}", Math.floor(mins / 1440));
  }
  function refreshTimes() {
    if (!T.tMin) return;
    document.querySelectorAll("time[data-rel]").forEach(function (el) {
      var r = rel(el.getAttribute("datetime"));
      if (r) el.textContent = r;
    });
  }
  refreshTimes();
  setInterval(refreshTimes, 60000);

  var local = document.querySelector("[data-local]");
  if (local) {
    var d = new Date(local.getAttribute("data-local"));
    if (!isNaN(d)) {
      local.textContent = "· " + d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) + " (" + local.dataset.label + ")";
    }
  }

  /* ---------------- "new since your last visit" */
  var last = Date.parse(store("gta6pt:lastVisit") || "");
  var counter = document.querySelector(".newcount");
  if (!isNaN(last) && counter) {
    var seen = {};
    var n = 0;
    document.querySelectorAll("main [data-ts]").forEach(function (el) {
      var ts = Date.parse(el.getAttribute("data-ts"));
      if (isNaN(ts) || ts <= last) return;
      el.classList.add("is-new");
      var badge = document.createElement("span");
      badge.className = "new-badge";
      badge.textContent = counter.dataset.badge;
      var anchor = el.querySelector("a, .plain-name, span");
      (anchor && anchor.parentNode ? anchor.parentNode : el).insertBefore(badge, anchor ? anchor.nextSibling : null);
      var key = el.textContent.trim().slice(0, 80);
      if (!seen[key]) { seen[key] = 1; n++; }
    });
    if (n > 0) {
      counter.textContent = n === 1 ? counter.dataset.one : counter.dataset.many.replace("{n}", n);
      counter.hidden = false;
    }
  }
  store("gta6pt:lastVisit", new Date().toISOString());

  /* ---------------- tabs (updates: last hour / 24 h) */
  document.querySelectorAll("[data-tabs]").forEach(function (box) {
    var list = box.querySelector("[role=tablist]");
    var tabs = box.querySelectorAll("[role=tab]");
    var panels = box.querySelectorAll("[role=tabpanel]");
    if (!list || !tabs.length) return;
    list.hidden = false;
    box.classList.add("is-enhanced");
    function select(i, focus) {
      tabs.forEach(function (t, j) {
        t.setAttribute("aria-selected", i === j ? "true" : "false");
        t.tabIndex = i === j ? 0 : -1;
        panels[j].hidden = i !== j;
      });
      if (focus) tabs[i].focus();
    }
    tabs.forEach(function (t, i) {
      t.addEventListener("click", function () { select(i); });
      t.addEventListener("keydown", function (e) {
        if (e.key === "ArrowRight" || e.key === "ArrowLeft") {
          select((i + (e.key === "ArrowRight" ? 1 : tabs.length - 1)) % tabs.length, true);
          e.preventDefault();
        }
      });
    });
    // If the last hour is empty, open on 24 h.
    var firstEmpty = panels[0] && !panels[0].querySelector(".entry");
    select(firstEmpty && tabs.length > 1 ? 1 : 0);
  });

  /* ---------------- motion layer (skipped entirely with reduced motion) */
  if (reduce || !("IntersectionObserver" in window)) return;
  doc.classList.add("motion");

  // Ticker: duplicate the items once for a seamless loop; clones are hidden from AT and keyboard.
  var track = document.querySelector(".ticker__track");
  if (track && track.children.length) {
    Array.prototype.slice.call(track.children).forEach(function (li) {
      var c = li.cloneNode(true);
      c.setAttribute("aria-hidden", "true");
      c.querySelectorAll("a").forEach(function (a) { a.tabIndex = -1; });
      track.appendChild(c);
    });
    var width = track.scrollWidth / 2;
    track.style.setProperty("--ticker-s", Math.max(25, Math.round(width / 40)) + "s");
    track.parentNode.classList.add("is-loop");
  }

  // Stagger index for grids of cards and phase segments.
  var groups = [".summary > .stat", ".projects > .pcard", ".mediagrid > .mcard", ".linkgroups > .linkgroup",
    ".newsgroups > .newsgroup", ".faq > .faq__item", ".glossary > div", ".alerts > .alert", ".timeline > li",
    ".sheet__grid > .card", ".proscons > .card", ".areas > .card"];
  groups.forEach(function (sel) {
    document.querySelectorAll(sel).forEach(function (el, i) {
      el.classList.add("reveal");
      el.style.setProperty("--i", String(i % 6));
    });
  });
  document.querySelectorAll(".phasebar").forEach(function (bar) {
    bar.querySelectorAll(".phasebar__seg").forEach(function (s, i) { s.style.setProperty("--i", String(i)); });
  });

  // Animated percentage counters (final value is already in the HTML).
  function count(el) {
    var target = parseInt(el.textContent, 10);
    if (isNaN(target) || target <= 0) return;
    var start = null;
    var dur = 1100;
    function step(ts) {
      if (start === null) start = ts;
      var p = Math.min(1, (ts - start) / dur);
      var eased = 1 - Math.pow(1 - p, 3);
      el.textContent = String(Math.round(target * eased));
      if (p < 1) requestAnimationFrame(step);
    }
    el.textContent = "0";
    requestAnimationFrame(step);
  }

  var io = new IntersectionObserver(function (entries) {
    entries.forEach(function (e) {
      if (!e.isIntersecting) return;
      var el = e.target;
      el.classList.add("is-in");
      if (el.classList.contains("num")) count(el);
      io.unobserve(el);
    });
  }, { rootMargin: "0px 0px -8% 0px", threshold: 0.12 });

  document.querySelectorAll(".reveal, .meter, .bars__bar, .phasebar, .h-section, .hero__pct .num, .pcard__pct .num")
    .forEach(function (el) { io.observe(el); });
})();
