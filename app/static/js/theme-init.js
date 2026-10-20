/* Runs before first paint so the page never flashes the wrong theme. */
(function () {
  var t = null;
  try { t = localStorage.getItem('waypoint:theme'); } catch (e) { /* storage blocked */ }
  if (t !== 'dark' && t !== 'light') t = matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
  document.documentElement.setAttribute('data-theme', t);
})();
