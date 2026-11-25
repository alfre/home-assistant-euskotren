import {
  AttributionControl, Map as MapLibreMap, Marker, NavigationControl, Popup,
  setWorkerUrl,
} from "./maplibre/maplibre-gl.mjs";

const workerUrl = new URL("./maplibre/maplibre-gl-worker.mjs", import.meta.url);
workerUrl.search = new URL(import.meta.url).search;
setWorkerUrl(workerUrl.href);

const collection = (features = []) => ({ type: "FeatureCollection", features });
const coordinates = ({ lat, lng }) => [lng, lat];

export class TerrainMap {
  constructor(container, flatMap, contextLost) {
    this.trains = new Map();
    this.map = new MapLibreMap({
      container,
      center: coordinates(flatMap.getCenter()),
      zoom: flatMap.getZoom() - 1,
      pitch: 52,
      scrollZoom: false,
      attributionControl: false,
      style: {
        version: 8,
        sources: {
          streets: {
            type: "raster", tileSize: 256, maxzoom: 19,
            tiles: ["https://tile.openstreetmap.org/{z}/{x}/{y}.png"],
            attribution: '<a href="https://www.openstreetmap.org/copyright">© OpenStreetMap</a>',
          },
          elevation: {
            type: "raster-dem", encoding: "terrarium",
            url: "https://tiles.mapterhorn.com/tilejson.json",
            attribution: '<a href="https://mapterhorn.com/attribution/">© Mapterhorn</a>',
          },
          rails: { type: "geojson", data: collection() },
        },
        terrain: { source: "elevation", exaggeration: 1 },
        layers: [
          { id: "streets", source: "streets", type: "raster" },
          {
            id: "tracks", source: "rails", type: "line",
            filter: ["==", ["geometry-type"], "LineString"],
            layout: { "line-cap": "round", "line-join": "round", "line-sort-key": ["get", "order"] },
            paint: { "line-color": ["get", "color"], "line-width": ["get", "weight"], "line-opacity": ["get", "opacity"] },
          },
          {
            id: "stops", source: "rails", type: "circle",
            filter: ["==", ["geometry-type"], "Point"],
            paint: {
              "circle-radius": ["get", "radius"], "circle-color": ["get", "fillColor"],
              "circle-stroke-color": ["get", "color"], "circle-stroke-width": ["get", "weight"],
            },
          },
        ],
      },
    });
    this.map.addControl(new NavigationControl(), "top-left");
    this.map.addControl(new AttributionControl());
    this.map.on("webglcontextlost", contextLost);
    this.map.on("click", "stops", ({ features }) => {
      const { geometry, properties } = features[0];
      new Popup().setLngLat(geometry.coordinates).setText(properties.name).addTo(this.map);
    });
    this.ready = new Promise((resolve, reject) => {
      const timeout = setTimeout(() => reject(new Error("Terrain map did not load")), 20000);
      this.map.once("load", () => {
        clearTimeout(timeout);
        resolve();
      });
      this.map.once("remove", () => {
        clearTimeout(timeout);
        resolve();
      });
    });
  }

  setTracks(networkLayer, routeLayer) {
    const source = this.map.getSource("rails");
    if (!source) return;
    const features = [...networkLayer.getLayers(), ...routeLayer.getLayers()].map((layer, order) => {
      const { color, weight, opacity, radius, fillColor } = layer.options;
      return layer.getLatLngs
        ? {
            type: "Feature", properties: { order, color, weight, opacity },
            geometry: { type: "LineString", coordinates: layer.getLatLngs().map(coordinates) },
          }
        : {
            type: "Feature",
            properties: { color, weight, radius, fillColor, name: layer.getTooltip().getContent().textContent },
            geometry: { type: "Point", coordinates: coordinates(layer.getLatLng()) },
          };
    });
    source.setData(collection(features));
  }

  setTrains(markers) {
    for (const [id, { marker }] of this.trains) {
      if (markers.has(id)) continue;
      marker.remove();
      this.trains.delete(id);
    }
    for (const [id, leafletMarker] of markers) {
      let train = this.trains.get(id);
      if (!train) {
        const badge = leafletMarker.options.icon.options.html.cloneNode(true);
        badge.classList.add("terrain-train");
        badge.title = leafletMarker.options.title;
        badge.setAttribute("aria-label", badge.title);
        const popup = new Popup({ offset: 20, closeButton: false });
        const marker = new Marker({ element: badge, pitchAlignment: "viewport" })
          .setLngLat(coordinates(leafletMarker.getLatLng())).setPopup(popup).addTo(this.map);
        train = { marker, popup };
        this.trains.set(id, train);
      }
      train.marker.setLngLat(coordinates(leafletMarker.getLatLng()));
      const content = leafletMarker.getPopup().getContent();
      if (train.content !== content) {
        train.content = content;
        train.popup.setDOMContent(content.cloneNode(true));
      }
    }
  }

  fit(bounds, maxZoom) {
    this.map.fitBounds([coordinates(bounds.getSouthWest()), coordinates(bounds.getNorthEast())], {
      padding: 25, pitch: this.map.getPitch(), bearing: this.map.getBearing(),
      maxZoom: maxZoom === undefined ? 18 : maxZoom - 1, duration: 0,
    });
  }

  remove() {
    for (const { marker } of this.trains.values()) marker.remove();
    this.map.remove();
  }
}
