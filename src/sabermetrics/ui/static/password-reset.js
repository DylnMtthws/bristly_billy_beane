// Fragments never reach the server or its access logs. Move the token into
// the protected form body and remove it from browser history immediately.
(() => {
  const token = window.location.hash.slice(1);
  if (token) {
    window.history.replaceState(null, "", window.location.pathname);
    const field = document.getElementById("token");
    if (field && /^[A-Za-z0-9_-]{43}$/.test(token)) field.value = token;
  }
})();
