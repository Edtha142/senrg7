/**
 * theme.js — Gestión de tema claro/oscuro para SENRG7
 * Incluir en todas las páginas antes de cualquier otro script.
 */
(function () {
  const CLAVE = 'senrg7_tema';

  function aplicarTema(tema) {
    document.documentElement.setAttribute('data-theme', tema);
    localStorage.setItem(CLAVE, tema);
    // Actualizar todos los botones de tema que existan en la página
    document.querySelectorAll('[data-btn-tema]').forEach(btn => {
      btn.setAttribute('title', tema === 'dark' ? 'Cambiar a tema claro' : 'Cambiar a tema oscuro');
      btn.innerHTML = tema === 'dark'
        ? '<i class="bi bi-sun"></i>'
        : '<i class="bi bi-moon-stars"></i>';
    });
  }

  function toggleTema() {
    const actual = document.documentElement.getAttribute('data-theme') || 'dark';
    aplicarTema(actual === 'dark' ? 'light' : 'dark');
  }

  // Aplicar tema guardado o el del sistema antes de que el DOM pinte
  const guardado = localStorage.getItem(CLAVE);
  const prefiereOscuro = window.matchMedia('(prefers-color-scheme: dark)').matches;
  aplicarTema(guardado || (prefiereOscuro ? 'dark' : 'light'));

  // Exponer al scope global
  window.SENRG7 = window.SENRG7 || {};
  window.SENRG7.toggleTema = toggleTema;
  window.SENRG7.aplicarTema = aplicarTema;

  // Reasignar botones cuando el DOM esté listo
  document.addEventListener('DOMContentLoaded', function () {
    const tema = localStorage.getItem(CLAVE) || 'dark';
    aplicarTema(tema);
    document.querySelectorAll('[data-btn-tema]').forEach(btn => {
      btn.addEventListener('click', toggleTema);
    });
  });
})();
