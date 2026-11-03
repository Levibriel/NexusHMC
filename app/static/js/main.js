// app/static/js/main.js
lucide.createIcons();

let seagullChartInstance = null;
let sidebarExpanded = true;

// Inicialización del Tema: Por defecto Modo Claro
if (localStorage.getItem('theme') === 'dark') {
    document.documentElement.classList.add('dark');
} else {
    document.documentElement.classList.remove('dark');
    localStorage.setItem('theme', 'light');
}

// Registrar Plugin DataLabels de forma segura si está disponible
if (typeof ChartDataLabels !== 'undefined') {
    Chart.register(ChartDataLabels);
}

function fmt(num) {
    if (num === null || num === undefined || isNaN(num)) return '0';
    return Math.round(num)
        .toString()
        .replace(/\B(?=(\d{3})+(?!\d))/g, ',');
}

let sidebarVisible = true;

function toggleSidebar() {
  const sidebar = document.getElementById('sidebar');
  const mainContainer = document.getElementById('main-container');
  const toggleIcon = document.getElementById('sidebar-toggle-icon');

  if (window.innerWidth < 768) {
    sidebar.classList.toggle('-translate-x-full');
  } else {
    sidebarVisible = !sidebarVisible;
    if (sidebarVisible) {
      sidebar.classList.remove(
        'md:-translate-x-full',
        '-translate-x-full',
        'md:translate-x-full'
      );
      sidebar.classList.add('md:translate-x-0');
      mainContainer.classList.remove('md:ml-0');
      mainContainer.classList.add('md:ml-64');
      if (toggleIcon) toggleIcon.setAttribute('data-lucide', 'chevrons-left');
    } else {
      sidebar.classList.remove('md:translate-x-0', 'translate-x-0');
      sidebar.classList.add('md:-translate-x-full');
      mainContainer.classList.remove('md:ml-64');
      mainContainer.classList.add('md:ml-0');
      if (toggleIcon) toggleIcon.setAttribute('data-lucide', 'chevrons-right');
    }

    if (window.lucide) lucide.createIcons();
    setTimeout(() => {
      if (seagullChartInstance) {
        seagullChartInstance.resize();
      }
    }, 310);
  }
}

function toggleTheme() {
  const html = document.documentElement;
  const isDark = html.classList.toggle('dark');
  document.getElementById('theme-text').textContent = isDark
    ? 'Oscuro'
    : 'Claro';
  document
    .getElementById('theme-icon-sun')
    .setAttribute('data-lucide', isDark ? 'moon' : 'sun');
  lucide.createIcons();
  if (seagullChartInstance) loadShiftData();
}

function iniciarRelojEnVivo() {
    const relojEl = document.getElementById('live-clock');
    if (!relojEl) return;

    const meses = [
        'Ene',
        'Feb',
        'Mar',
        'Abr',
        'May',
        'Jun',
        'Jul',
        'Ago',
        'Sep',
        'Oct',
        'Nov',
        'Dic',
    ];

    function actualizar() {
        const ahora = new Date();
        const dia = String(ahora.getDate()).padStart(2, '0');
        const mes = meses[ahora.getMonth()];
        const anio = ahora.getFullYear();
        const hh = String(ahora.getHours()).padStart(2, '0');
        const mm = String(ahora.getMinutes()).padStart(2, '0');
        const ss = String(ahora.getSeconds()).padStart(2, '0');

        relojEl.textContent = `${dia} ${mes} ${anio} - ${hh}:${mm}:${ss}`;
    }

    actualizar();
    setInterval(actualizar, 1000);
}

