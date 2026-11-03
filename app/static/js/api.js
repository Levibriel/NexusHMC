// app/static/js/api.js
const API = {
    async fetchJSON(url) {
        const response = await fetch(url);
        if (!response.ok) {
            throw new Error(`Error HTTP ${response.status}: ${response.statusText}`);
        }
        return await response.json();
    },

    async getBalance(fecha, turno) {
        return this.fetchJSON(`/api/v1/horahora/balance?fecha=${encodeURIComponent(fecha)}&turno=${encodeURIComponent(turno)}`);
    },

    async getCarguio(fecha, turno) {
        return this.fetchJSON(`/api/v1/horahora/carguio?fecha=${encodeURIComponent(fecha)}&turno=${encodeURIComponent(turno)}`);
    },

    async getTransporte(fecha, turno) {
        return this.fetchJSON(`/api/v1/horahora/transporte?fecha=${encodeURIComponent(fecha)}&turno=${encodeURIComponent(turno)}`);
    },

    async getAcumSemanaMes(fecha) {
        return this.fetchJSON(`/api/v1/horahora/semanames?fecha=${encodeURIComponent(fecha)}`);
    },

    async getFleetStatus(fecha, turno) {
        return this.fetchJSON(`/api/v1/horahora/fleetstatus?fecha=${encodeURIComponent(fecha)}&turno=${encodeURIComponent(turno)}`);
    },

    async getCaexVsTon(fecha, turno) {
        return this.fetchJSON(`/api/v1/horahora/caex-vs-ton?fecha=${encodeURIComponent(fecha)}&turno=${encodeURIComponent(turno)}`);
    },

    async getFleetTimeline(fecha, turno) {
        return this.fetchJSON(`/api/v1/horahora/statustimeline?fecha=${encodeURIComponent(fecha)}&turno=${encodeURIComponent(turno)}`);
    }
};