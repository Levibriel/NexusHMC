// app/static/js/loader.js (v2 - SPA Orchestration Fix)

const VIEWS = {
    monitoreo: '/static/views/horahora.html',
    periodo: '/static/views/periodo.html'
};

const COMPONENTS = {
    sidebar: '/static/components/sidebar.html',
    header: '/static/components/header.html'
};

let currentView = 'monitoreo';

async function loadComponent(containerId, componentUrl) {
    try {
        const res = await fetch(componentUrl);
        if (!res.ok) throw new Error(`Error HTTP ${res.status} al cargar ${componentUrl}`);
        const html = await res.text();
        const container = document.getElementById(containerId);
        if (container) {
            container.innerHTML = html;
        }
    } catch (err) {
        console.error(`Error cargando componente ${componentUrl}:`, err);
    }
}

function inicializarFechaYTurnoDefault() {
    const ahora = new Date();
    const hora = ahora.getHours();
    let turnoAuto = 'A';
    let fechaOperacional = new Date(ahora);

    if (hora >= 8 && hora < 20) {
        turnoAuto = 'A';
    } else {
        turnoAuto = 'B';
        if (hora < 8) {
            fechaOperacional.setDate(fechaOperacional.getDate() - 1);
        }
    }

    const yyyy = fechaOperacional.getFullYear();
    const mm = String(fechaOperacional.getMonth() + 1).padStart(2, '0');
    const dd = String(fechaOperacional.getDate()).padStart(2, '0');
    const fechaStr = `${yyyy}-${mm}-${dd}`;

    const inputFecha = document.getElementById('query-date');
    if (inputFecha) inputFecha.value = fechaStr;

    const selectTurno = document.getElementById('query-shift');
    if (selectTurno) selectTurno.value = turnoAuto;
}

async function switchView(viewKey) {
    if (!VIEWS[viewKey]) return;
    currentView = viewKey;

    const mainContainer = document.getElementById('main-content-area');
    const progressBar = document.getElementById('top-progress-bar');

    if (progressBar) {
        progressBar.style.width = '30%';
        progressBar.style.opacity = '1';
    }

    try {
        const res = await fetch(VIEWS[viewKey]);
        if (!res.ok) throw new Error(`Error HTTP ${res.status} al cargar vista ${viewKey}`);
        const html = await res.text();
        
        if (mainContainer) {
            mainContainer.innerHTML = html;
        }

        // Re-inicializar íconos Lucide
        if (typeof lucide !== 'undefined') {
            lucide.createIcons();
        }

        // Cargar datos automáticamente al inyectar la vista de monitoreo
        if (viewKey === 'monitoreo' && typeof loadShiftData === 'function') {
            loadShiftData();
        }

    } catch (err) {
        console.error(`Error cargando vista ${viewKey}:`, err);
    } finally {
        if (progressBar) {
            progressBar.style.width = '100%';
            setTimeout(() => {
                progressBar.style.width = '0';
                progressBar.style.opacity = '0';
            }, 300);
        }
    }
}

async function initLayout() {
    // 1. Cargar componentes del Layout (Sidebar y Header)
    await Promise.all([
        loadComponent('sidebar-container', COMPONENTS.sidebar),
        loadComponent('header-container', COMPONENTS.header)
    ]);

    // 2. Una vez inyectado header.html en el DOM, inicializar fecha/turno y reloj
    inicializarFechaYTurnoDefault();
    if (typeof iniciarRelojEnVivo === 'function') {
        iniciarRelojEnVivo();
    }

    if (typeof lucide !== 'undefined') {
        lucide.createIcons();
    }

    // 3. Cargar vista principal (Monitoreo) con los filtros ya poblados
    await switchView('monitoreo');
}

document.addEventListener('DOMContentLoaded', () => {
    initLayout();
});
