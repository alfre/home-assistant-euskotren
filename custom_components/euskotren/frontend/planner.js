import * as L from "./leaflet/leaflet-src.esm.js";

const zone = "Europe/Madrid";
const fleetRefresh = 15000;
const lineColors = new Map([
  ["T0", { background: "#B77FA0", text: "#21111C", stroke: "#884768" }],
  ["T1", { background: "#B0BC22", text: "#202600", stroke: "#5C6900" }],
  ["E1", { background: "#005A8B", text: "#FFFFFF", stroke: "#005A8B" }],
]);
const copy = {
  es: {
    title: "Planifica tu viaje",
    origin: "Origen", destination: "Destino", date: "Fecha",
    time: "Salida a partir de", choose: "Elige una estación",
    swap: "Intercambiar origen y destino", search: "Buscar trenes",
    loading: "Cargando estaciones…", searching: "Buscando trenes…", retry: "Reintentar",
    coverage: (first, last) => `Horarios disponibles del ${first} al ${last} · hora local`,
    results: "Trayectos", route: "Mapa del trayecto", stops: "Paradas",
    empty: "El horario publicado no muestra salidas para este trayecto a partir de la hora elegida.",
    stationError: "No se pudieron cargar las estaciones.",
    searchError: "No se pudo consultar el horario.",
    sameStation: "Elige dos estaciones distintas.",
    direct: "Sin transbordo", transfer: "1 transbordo",
    transferAt: (station) => `1 transbordo en ${station}`,
    live: "En tiempo real", scheduled: "Programado", partial: "Previsión parcial",
    planned: "Hora programada", skipped: "Sin parada",
    towards: (station) => `hacia ${station}`,
    showFleet: "Ver trenes estimados ahora", hideFleet: "Ocultar trenes estimados",
    network: "Mapa del Topo", fleetLoading: "Calculando posiciones estimadas…",
    fleetUnavailable: "No se pudieron actualizar las posiciones estimadas.",
    fleetEmpty: "No hay trenes en servicio según el horario disponible.",
    fleetStatus: (count, time) => `Posiciones estimadas · ${count} ${count === 1 ? "tren" : "trenes"} · ${time}`,
    estimate: "Posición estimada", nextStation: (station) => `Próxima parada: ${station}`,
    atStation: (station) => `En ${station}`,
    predictedTimes: "Según el horario y las previsiones", scheduledTimes: "Según el horario",
  },
  en: {
    title: "Plan your journey",
    origin: "Origin", destination: "Destination", date: "Date",
    time: "Depart from", choose: "Choose a station",
    swap: "Swap origin and destination", search: "Find trains",
    loading: "Loading stations…", searching: "Finding trains…", retry: "Retry",
    coverage: (first, last) => `Timetable available from ${first} to ${last} · local time`,
    results: "Journeys", route: "Journey map", stops: "Stops",
    empty: "The published timetable has no departures for this journey from the selected time.",
    stationError: "The stations could not be loaded.",
    searchError: "The timetable could not be queried.",
    sameStation: "Choose two different stations.",
    direct: "No changes", transfer: "1 change",
    transferAt: (station) => `1 change at ${station}`,
    live: "Real time", scheduled: "Scheduled", partial: "Partial prediction",
    planned: "Scheduled time", skipped: "Does not stop",
    towards: (station) => `towards ${station}`,
    showFleet: "Show estimated trains now", hideFleet: "Hide estimated trains",
    network: "Topo network map", fleetLoading: "Calculating estimated positions…",
    fleetUnavailable: "Estimated train positions could not be refreshed.",
    fleetEmpty: "No trains are in service according to the available timetable.",
    fleetStatus: (count, time) => `Estimated positions · ${count} ${count === 1 ? "train" : "trains"} · ${time}`,
    estimate: "Estimated position", nextStation: (station) => `Next stop: ${station}`,
    atStation: (station) => `At ${station}`,
    predictedTimes: "Based on the timetable and predictions", scheduledTimes: "Based on the timetable",
  },
};

function element(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (className) node.className = className;
  return node;
}

function lineBadge(line) {
  const badge = element("span", line, "line-badge");
  const color = lineColors.get(line);
  if (color) {
    badge.style.setProperty("--line-background", color.background);
    badge.style.setProperty("--line-text", color.text);
  }
  return badge;
}

function journeyLines(legs) {
  const group = element("span", undefined, "journey-lines");
  const icon = element("ha-icon", undefined, "train-icon");
  icon.setAttribute("icon", "mdi:train");
  icon.setAttribute("aria-hidden", "true");
  group.append(icon);
  for (const [index, leg] of legs.entries()) {
    if (index) {
      const arrow = element("span", "→", "line-arrow");
      arrow.setAttribute("aria-hidden", "true");
      group.append(arrow);
    }
    group.append(lineBadge(leg.line));
  }
  return group;
}

function madridDateAndTime() {
  const parts = new Intl.DateTimeFormat("en-GB", {
    timeZone: zone, year: "numeric", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit", hourCycle: "h23",
  }).formatToParts();
  const values = Object.fromEntries(parts.map(({ type, value }) => [type, value]));
  return [`${values.year}-${values.month}-${values.day}`, `${values.hour}:${values.minute}`];
}

function trainPosition(train, timestamp) {
  const path = train.path;
  if (!path || path.length < 2) return [train.latitude, train.longitude];
  const duration = train.arrival_timestamp - train.departure_timestamp;
  const fraction = Math.max(0, Math.min(1, (timestamp - train.departure_timestamp) / duration));
  const distance = path.at(-1)[2] * fraction;
  const index = path.findIndex((point) => point[2] >= distance);
  if (index <= 0) return (index === 0 ? path[0] : path.at(-1)).slice(0, 2);
  const [a, b] = [path[index - 1], path[index]];
  const progress = (distance - a[2]) / (b[2] - a[2]);
  return [a[0] + (b[0] - a[0]) * progress, a[1] + (b[1] - a[1]) * progress];
}

class EuskotrenPlanner extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this.fleetEnabled = false;
    this.fleetCycle = 0;
    this.fleetMarkers = new Map();
  }

  set hass(hass) {
    this._hass = hass;
    if (this.menu) this.menu.hass = hass;
    if (this.isConnected && !this.form) this.render();
  }

  set narrow(narrow) {
    this._narrow = narrow;
    if (this.menu) this.menu.narrow = narrow;
  }

  connectedCallback() {
    if (this._hass && !this.form) this.render();
    if (this.selected) this.showJourney(this.selected);
  }

  disconnectedCallback() {
    if (this.fleetEnabled) this.setFleet(false);
    this.map?.remove();
    this.map = undefined;
  }

  render() {
    this.locale = this._hass.language || "en";
    this.t = copy[this.locale.split("-")[0]] || copy.en;
    this.dateFormat = new Intl.DateTimeFormat(this.locale, {
      timeZone: zone, day: "numeric", month: "short",
    });
    this.timeFormat = new Intl.DateTimeFormat(this.locale, {
      timeZone: zone, hour: "2-digit", minute: "2-digit", hourCycle: "h23",
    });
    this.shadowRoot.innerHTML = `
      <link rel="stylesheet" href="${new URL("./leaflet/leaflet.css", import.meta.url)}">
      <link rel="stylesheet" href="${new URL("./planner.css", import.meta.url)}">
      <header><ha-menu-button></ha-menu-button><span>Euskotren Topo</span></header>
      <main>
        <section class="search">
          <h1></h1>
          <form>
            <div class="stations">
              <label><span></span><select name="origin" required disabled></select></label>
              <button class="swap" type="button"><ha-icon icon="mdi:swap-horizontal"></ha-icon></button>
              <label><span></span><select name="destination" required disabled></select></label>
            </div>
            <div class="when">
              <label><span></span><input name="date" type="date" required disabled></label>
              <label><span></span><input name="time" type="time" required disabled></label>
              <button class="primary" type="submit" disabled></button>
            </div>
          </form>
          <div class="search-footer">
            <div class="coverage"></div>
            <button class="fleet-toggle" type="button" aria-pressed="false" disabled></button>
          </div>
        </section>
        <div class="status" role="status" aria-live="polite"></div>
        <div class="results" hidden>
          <section class="journeys"><h2></h2><div class="options"></div></section>
          <section class="detail">
            <div class="map-heading"><h2></h2><div class="map-lines"></div></div>
            <p class="fleet-status" hidden></p>
            <div class="map"></div>
            <h3></h3>
            <div class="stops"></div>
          </section>
        </div>
      </main>`;
    const find = (selector) => this.shadowRoot.querySelector(selector);
    this.menu = find("ha-menu-button");
    this.menu.hass = this._hass;
    this.menu.narrow = this._narrow;
    this.form = find("form");
    this.status = find(".status");
    this.coverage = find(".coverage");
    this.results = find(".results");
    this.journeys = find(".journeys");
    this.options = find(".options");
    this.mapNode = find(".map");
    this.mapLines = find(".map-lines");
    this.mapTitle = find(".detail h2");
    this.stopsTitle = find(".detail h3");
    this.stopList = find(".stops");
    this.fleetToggle = find(".fleet-toggle");
    this.fleetStatus = find(".fleet-status");
    this.button = find(".primary");
    this.fields = Object.fromEntries(
      ["origin", "destination", "date", "time"].map((name) => [name, this.form.elements.namedItem(name)]),
    );
    for (const [name, control] of Object.entries(this.fields)) {
      control.previousElementSibling.textContent = this.t[name];
    }
    find("h1").textContent = this.t.title;
    find(".journeys h2").textContent = this.t.results;
    this.mapTitle.textContent = this.t.route;
    this.stopsTitle.textContent = this.t.stops;
    this.fleetToggle.textContent = this.t.showFleet;
    this.fleetToggle.addEventListener("click", () => this.setFleet(!this.fleetEnabled));
    this.button.textContent = this.t.search;
    const swap = find(".swap");
    swap.setAttribute("aria-label", this.t.swap);
    swap.title = this.t.swap;
    swap.addEventListener("click", () => {
      const { origin, destination } = this.fields;
      [origin.value, destination.value] = [destination.value, origin.value];
      destination.setCustomValidity("");
    });
    this.form.addEventListener("input", () => this.fields.destination.setCustomValidity(""));
    this.fields.date.addEventListener("change", () => {
      const [today, now] = madridDateAndTime();
      this.fields.time.value = this.fields.date.value === today ? now : "00:00";
    });
    this.form.addEventListener("submit", (event) => {
      event.preventDefault();
      this.search();
    });
    this.loadStations();
  }

  async action(service, serviceData = {}) {
    const { response } = await this._hass.callWS({
      type: "call_service", domain: "euskotren", service,
      service_data: serviceData, return_response: true,
    });
    return response;
  }

  async loadStations() {
    this.status.textContent = this.t.loading;
    try {
      const catalog = await this.action("list_stations");
      this.stationBounds = L.latLngBounds(
        catalog.stations.map((station) => [station.latitude, station.longitude]),
      );
      for (const name of ["origin", "destination"]) {
        const select = this.fields[name];
        select.replaceChildren(new Option(this.t.choose, ""));
        for (const station of catalog.stations) select.add(new Option(station.name, station.id));
      }
      if (catalog.default_origin) this.fields.origin.value = catalog.default_origin;
      const [today, now] = madridDateAndTime();
      this.fields.date.min = catalog.first_date;
      this.fields.date.max = catalog.last_date;
      this.fields.date.value = [today, catalog.first_date, catalog.last_date].sort()[1];
      this.fields.time.value = this.fields.date.value === today ? now : "00:00";
      this.coverage.textContent = this.t.coverage(
        this.dateFormat.format(new Date(`${catalog.first_date}T12:00:00Z`)),
        this.dateFormat.format(new Date(`${catalog.last_date}T12:00:00Z`)),
      );
      for (const control of Object.values(this.fields)) control.disabled = false;
      this.button.disabled = false;
      this.fleetToggle.disabled = !this.stationBounds.isValid();
      this.status.textContent = "";
    } catch {
      const retry = element("button", this.t.retry, "retry");
      retry.addEventListener("click", () => this.loadStations());
      this.status.replaceChildren(element("span", this.t.stationError), retry);
    }
  }

  async search() {
    const data = Object.fromEntries(new FormData(this.form));
    if (data.origin === data.destination) {
      this.fields.destination.setCustomValidity(this.t.sameStation);
      this.fields.destination.reportValidity();
      return;
    }
    this.button.disabled = true;
    this.status.textContent = this.t.searching;
    this.selected = undefined;
    this.route?.clearLayers();
    this.mapLines.replaceChildren();
    this.updateMapVisibility();
    if (this.fleetEnabled) this.map.invalidateSize({ pan: false });
    try {
      const result = await this.action("search_journeys", data);
      this.resultDate = result.date;
      this.options.replaceChildren(...result.journeys.map((journey) => this.journeyButton(journey)));
      this.status.textContent = result.journeys.length ? "" : this.t.empty;
      if (result.journeys.length) this.selectJourney(result.journeys[0], this.options.firstElementChild);
    } catch (error) {
      this.status.textContent = error?.message || this.t.searchError;
    } finally {
      this.button.disabled = false;
    }
  }

  formatTime(value) {
    const formatted = this.timeFormat.format(new Date(value));
    const nextDay = value.slice(0, 10) !== this.resultDate;
    return nextDay ? `${formatted} · ${this.dateFormat.format(new Date(value))}` : formatted;
  }

  journeyButton(journey) {
    const button = element("button", undefined, "journey");
    button.type = "button";
    button.setAttribute("aria-pressed", "false");
    const top = element("span", undefined, "journey-top");
    top.append(
      element("strong", `${this.formatTime(journey.departure)} → ${this.formatTime(journey.arrival)}`),
      element("span", `${Math.ceil(journey.duration_minutes)} min`),
    );
    const detail = element("span", undefined, "journey-meta");
    const bothLive = journey.departure_realtime && journey.arrival_realtime;
    const info = journey.realtime ? (bothLive ? this.t.live : this.t.partial) : this.t.scheduled;
    const change = journey.transfers ? this.t.transferAt(journey.legs[0].destination.name) : this.t.direct;
    detail.append(journeyLines(journey.legs), element("span", change, "journey-transfer"),
      element("span", info, journey.realtime ? "live" : ""));
    button.title = `${this.t.planned} · ${this.formatTime(journey.scheduled_departure)} → ${this.formatTime(journey.scheduled_arrival)}`;
    button.append(top, detail);
    button.addEventListener("click", () => this.selectJourney(journey, button));
    return button;
  }

  selectJourney(journey, button) {
    this.options.querySelector('[aria-pressed="true"]')?.setAttribute("aria-pressed", "false");
    button.setAttribute("aria-pressed", "true");
    this.selected = journey;
    this.updateMapVisibility();
    this.showJourney(journey);
  }

  updateMapVisibility() {
    const hasJourney = Boolean(this.selected);
    this.results.hidden = !hasJourney && !this.fleetEnabled;
    this.results.classList.toggle("network-only", !hasJourney);
    this.journeys.hidden = !hasJourney;
    this.stopsTitle.hidden = !hasJourney;
    this.stopList.hidden = !hasJourney;
    this.mapTitle.textContent = hasJourney ? this.t.route : this.t.network;
    this.fleetStatus.hidden = !this.fleetEnabled;
  }

  ensureMap() {
    if (!this.map) {
      this.map = L.map(this.mapNode, { scrollWheelZoom: false, attributionControl: false });
      const attribution = L.control.attribution({ prefix: false }).addTo(this.map);
      L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
        maxZoom: 19, referrerPolicy: "strict-origin-when-cross-origin",
        attribution: '&copy; <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap</a>',
      }).addTo(this.map);
      this.route = L.featureGroup().addTo(this.map);
      this.fleetLayer = L.layerGroup().addTo(this.map);
    }
  }

  setFleet(enabled) {
    this.fleetEnabled = enabled;
    this.fleetCycle++;
    this.fleetToggle.setAttribute("aria-pressed", String(enabled));
    this.fleetToggle.textContent = enabled ? this.t.hideFleet : this.t.showFleet;
    this.updateMapVisibility();
    if (!enabled) {
      clearTimeout(this.fleetTimer);
      clearInterval(this.fleetAnimation);
      this.clearFleet();
      return;
    }
    this.ensureMap();
    this.map.invalidateSize({ pan: false });
    if (!this.selected) {
      this.map.fitBounds(this.stationBounds, { padding: [25, 25], animate: false });
    }
    this.fleetStatus.textContent = this.t.fleetLoading;
    this.refreshFleet(this.fleetCycle);
    this.fleetAnimation = setInterval(() => this.moveTrains(), 1000);
  }

  clearFleet() {
    this.fleetSnapshot = undefined;
    this.fleetLayer?.clearLayers();
    this.fleetMarkers.clear();
  }

  async refreshFleet(cycle) {
    if (!document.hidden) {
      try {
        const snapshot = await this.action("get_train_positions");
        if (cycle !== this.fleetCycle) return;
        this.fleetSnapshot = snapshot;
        this.fleetReceivedAt = performance.now();
        this.updateTrains(snapshot);
      } catch {
        if (cycle !== this.fleetCycle) return;
        this.clearFleet();
        this.fleetStatus.textContent = this.t.fleetUnavailable;
      }
    }
    if (cycle === this.fleetCycle) {
      this.fleetTimer = setTimeout(() => this.refreshFleet(cycle), fleetRefresh);
    }
  }

  updateTrains(snapshot) {
    const ids = new Set(snapshot.trains.map((train) => train.id));
    for (const [id, marker] of this.fleetMarkers) {
      if (ids.has(id)) continue;
      this.fleetLayer.removeLayer(marker);
      this.fleetMarkers.delete(id);
    }
    for (const train of snapshot.trains) {
      const heading = [train.line, train.headsign && this.t.towards(train.headsign)].filter(Boolean).join(" ");
      const stop = train.at_station ? this.t.atStation(train.at_station) : this.t.nextStation(train.next_station);
      const information = element("div");
      const source = train.realtime ? this.t.predictedTimes : this.t.scheduledTimes;
      information.append(element("strong", heading), element("div", stop),
        element("div", `${this.t.estimate} · ${source}`));
      let marker = this.fleetMarkers.get(train.id);
      if (!marker) {
        marker = L.marker([train.latitude, train.longitude], {
          title: `${this.t.estimate} · ${heading}`,
          icon: L.divIcon({ html: lineBadge(train.line), className: "estimate-marker", iconSize: [36, 28] }),
        }).bindPopup(information, { autoPan: false }).addTo(this.fleetLayer);
        this.fleetMarkers.set(train.id, marker);
      } else {
        marker.setPopupContent(information);
      }
    }
    this.moveTrains();
    this.fleetStatus.textContent = snapshot.trains.length
      ? this.t.fleetStatus(snapshot.trains.length, this.timeFormat.format(snapshot.calculated_at * 1000))
      : this.t.fleetEmpty;
  }

  moveTrains() {
    if (!this.fleetSnapshot || document.hidden) return;
    const now = this.fleetSnapshot.calculated_at + (performance.now() - this.fleetReceivedAt) / 1000;
    for (const train of this.fleetSnapshot.trains) {
      this.fleetMarkers.get(train.id).setLatLng(trainPosition(train, now));
    }
  }

  showJourney(journey) {
    if (!this.isConnected) return;
    this.ensureMap();
    this.route.clearLayers();
    this.mapLines.replaceChildren(journeyLines(journey.legs));
    this.stopList.replaceChildren();
    const markers = new Map();
    for (const [index, leg] of journey.legs.entries()) {
      const color = lineColors.get(leg.line)?.stroke ?? "#087c6a";
      L.polyline(leg.polyline, { color, weight: 5, opacity: 0.95 }).addTo(this.route);
      const label = element("h4", undefined, "leg-heading");
      label.append(lineBadge(leg.line), element("span", this.t.towards(leg.headsign || leg.destination.name)));
      const list = element("ol");
      list.style.setProperty("--route-color", color);
      for (const [stopIndex, stop] of leg.stops.entries()) {
        const endpoint = stopIndex === 0 || stopIndex === leg.stops.length - 1;
        const transfer = journey.transfers && (
          (index === 0 && stopIndex === leg.stops.length - 1) || (index === 1 && stopIndex === 0)
        );
        if (!markers.has(stop.id)) {
          const marker = L.circleMarker([stop.latitude, stop.longitude], {
            radius: endpoint ? 7 : 4, color, fillColor: stop.skipped ? "#778a88" : "#fff",
            fillOpacity: 1, weight: endpoint ? 3 : 2,
          }).bindTooltip(element("span", transfer ? `${stop.name} · ${this.t.transfer}` : stop.name));
          markers.set(stop.id, marker);
        }
        const item = element("li");
        item.append(
          element("span", stop.name),
          element("span", stop.skipped ? this.t.skipped : this.formatTime(stopIndex ? stop.arrival : stop.departure)),
        );
        list.append(item);
      }
      this.stopList.append(label, list);
    }
    for (const marker of markers.values()) marker.addTo(this.route);
    this.map.invalidateSize({ pan: false });
    this.map.fitBounds(this.route.getBounds(), { padding: [25, 25], maxZoom: 15, animate: false });
  }
}

if (!customElements.get("euskotren-planner")) {
  customElements.define("euskotren-planner", EuskotrenPlanner);
}
