(function(){
"use strict";

/*
 * Decides where the dashboard's data comes from, then starts app.js.
 *
 *   1. data/dashboard_data.js has already set window.__DASHBOARD_DATA__
 *      (the precomputed fallback).
 *   2. If config.js defines API_BASE_URL, ask POST {API_BASE_URL}/optimize.
 *      A valid response replaces window.__DASHBOARD_DATA__.
 *   3. Anything else (no URL, network error, timeout, HTTP error, unexpected
 *      shape) leaves the precomputed data in place.
 *   4. app.js is loaded last, so it renders whichever data won. app.js itself
 *      is unaware of the API.
 */

var cfg = window.APP_CONFIG || {};
var ALGOS = ["greedy", "nearest_neighbor", "two_opt"];

function setStatus(text){
  var el = document.querySelector(".run-status");
  if (el) el.textContent = text;
}

function startApp(source){
  window.__DATA_SOURCE__ = source; // "api" | "precomputed" (handy when debugging)
  var s = document.createElement("script");
  s.src = "app.js";
  document.body.appendChild(s);
}

// Cheap structural check so a malformed response can never blank the dashboard.
function isUsablePayload(d){
  if (!d || typeof d !== "object") return false;
  if (!d.meta || !d.warehouse || !Array.isArray(d.batches) || d.batches.length === 0) return false;
  var wh = d.warehouse;
  if (!wh.depot || !Array.isArray(wh.locations) || !Array.isArray(wh.aisles) || !Array.isArray(wh.racks)) return false;
  return d.batches.every(function(b){
    return b && Array.isArray(b.stops) && b.routes && ALGOS.every(function(k){
      var r = b.routes[k];
      return r && Array.isArray(r.sequence) && typeof r.distance_m === "number" && typeof r.time_min === "number";
    });
  });
}

function fetchLive(){
  var base = String(cfg.API_BASE_URL || "").replace(/\/+$/, "");
  if (!base || typeof fetch !== "function") return Promise.reject(new Error("API not usable in this browser/config"));

  var controller = typeof AbortController === "function" ? new AbortController() : null;
  var timer = controller ? setTimeout(function(){ controller.abort(); }, cfg.API_TIMEOUT_MS || 15000) : null;

  return fetch(base + "/optimize", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({algorithm: cfg.API_ALGORITHM || "2-opt"}),
    signal: controller ? controller.signal : undefined
  }).then(function(res){
    if (!res.ok) throw new Error("HTTP " + res.status);
    return res.json();
  }).then(function(data){
    if (!isUsablePayload(data)) throw new Error("unexpected response shape");
    return data;
  }).then(function(data){
    if (timer) clearTimeout(timer);
    return data;
  }, function(err){
    if (timer) clearTimeout(timer);
    throw err;
  });
}

var apiConfigured = !!String(cfg.API_BASE_URL || "").trim();

if (!apiConfigured){
  // No backend configured: behave exactly like the original static dashboard.
  startApp("precomputed");
} else {
  setStatus("Contacting optimization API\u2026");
  fetchLive().then(function(data){
    window.__DASHBOARD_DATA__ = data;
    setStatus("Sample data \u00b7 live results from API");
    startApp("api");
  }, function(err){
    if (window.console && console.warn) console.warn("Optimization API unavailable, using precomputed data:", err && err.message ? err.message : err);
    setStatus("Sample data \u00b7 precomputed results (API unavailable)");
    startApp("precomputed");
  });
}
})();
