/* Root page: send the visitor to /es/ or /en/ (fallback /en/). Manual links remain on the page. */
(function () {
  "use strict";
  var base = document.documentElement.getAttribute("data-base") || "/";
  var pick = null;
  try { pick = window.localStorage.getItem("gta6pt:lang"); } catch (e) { pick = null; }
  if (pick !== "es" && pick !== "en") {
    var langs = navigator.languages && navigator.languages.length ? navigator.languages : [navigator.language || "en"];
    pick = "en";
    for (var i = 0; i < langs.length; i++) {
      var l = String(langs[i]).toLowerCase();
      if (l.indexOf("es") === 0) { pick = "es"; break; }
      if (l.indexOf("en") === 0) { pick = "en"; break; }
    }
  }
  window.location.replace(base + pick + "/" + window.location.hash);
})();
