/*
 * Frontend configuration -- the ONLY place the backend URL is defined.
 *
 * API_BASE_URL
 *   ""  (empty)  -> API disabled; the dashboard shows the precomputed data in
 *                   data/dashboard_data.js exactly as before.
 *   local dev    -> "http://localhost:8000"
 *   Render       -> your backend's URL, e.g. "https://warehouse-api.onrender.com"
 *                   (no trailing slash)
 *
 * If the API is set but unreachable, slow, or returns something unexpected,
 * the dashboard automatically falls back to the precomputed data.
 *
 * Note: index.html's Content-Security-Policy `connect-src` allows
 * https://*.onrender.com and localhost. If you use a custom domain for the
 * backend, add it to `connect-src` in index.html as well.
 */
window.APP_CONFIG = {
  API_BASE_URL: "",

  // Routing algorithm requested from POST /optimize: "greedy",
  // "nearest_neighbor" or "2-opt". (The dashboard still receives all three
  // and its algorithm switcher works client-side, as before.)
  API_ALGORITHM: "2-opt",

  // How long to wait for the backend before falling back to precomputed
  // data. Render's free tier can take ~30-60 s to wake from sleep.
  API_TIMEOUT_MS: 15000
};
