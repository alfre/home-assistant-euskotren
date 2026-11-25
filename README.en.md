# Euskotren Topo for Home Assistant

[Versión en castellano](README.md)

Plan a journey on Euskotren's Topo from the Home Assistant sidebar. Choose a departure station, destination, date, and earliest departure time. Each result shows departure, arrival, travel time, lines, stops, and an interactive map. The planner returns direct journeys and journeys with one change at the same station.

## Installation

Requires Home Assistant 2026.11 or later.

[![Open Euskotren Topo in the Marketplace](https://my.home-assistant.io/badges/marketplace_repository.svg)](https://my.home-assistant.io/redirect/marketplace_repository/?owner=alfre&repository=home-assistant-euskotren&category=integration)

Open the link, add the custom repository if prompted, install the integration, and restart Home Assistant.

For a manual installation, copy the repository's `custom_components/euskotren` folder into `custom_components` in your Home Assistant configuration directory and restart.

Go to **Settings → Devices & services → Add integration** and search for **Euskotren Topo**. After setup, open **Euskotren Topo** from the sidebar.

To set your usual departure station, open **Settings → Devices & services → Euskotren Topo** and go to the integration options. The planner selects it when it opens, and you can change it for each journey. Choose **No default origin** to leave the field blank.

## Timetables and map

Each result states whether a change is needed. For journeys with a change, it shows the station, arrival time, the next train's departure time, and the wait. Selecting a journey shows the two lines and when to get off and board above the map.

The map shows active trains on the railway lines when you open the planner. Use **Hide estimated positions** and **Show estimated positions** with or without a journey selected. Each marker moves between two stops using their departure and arrival times. The layer uses published timetables and applies recent Moveuskadi predictions and cancellations when available. It refreshes every 15 seconds while open. Markers show approximate positions.

The four-corner icon in the upper-right corner opens the map in full screen. Select it again or press `Esc` to exit.

- The planner covers lines T0 and T1 and the section they share with E1. Platforms at the same station are grouped in the station picker.
- The planner applies published special services and holiday exceptions. It shows the date range covered by the timetable, which can include days with no published departures. Times are shown in `Europe/Madrid`, including arrivals after midnight.
- The planner uses a recent prediction when it matches the train and service date. Where a prediction is unavailable, the planner shows the scheduled time. It omits published cancellations and marks skipped stops on the journey.
- Home Assistant retrieves timetables, stops, route geometry, and predictions from Moveuskadi. Your browser requests the map background from OpenStreetMap. The route and stops remain visible if the map background fails to load.

## Automations

`euskotren.list_stations` returns station IDs, the default origin, and the dates covered. Pass the station IDs to `euskotren.search_journeys`. You can omit the origin if you have set a default in the integration. The date, time, and result limit are optional. The default is today from the current time, or from midnight if you choose another date. The default limit is 10, with a maximum of 30.

```yaml
- action: euskotren.search_journeys
  data:
    origin: "ES:Euskotren:StopPlace:2580:"
    destination: "ES:Euskotren:StopPlace:2582:"
    time: "17:00"
    limit: 5
  response_variable: topo
```

The response includes a `journeys` list with ISO 8601 times, duration in minutes, prediction flags, and the legs of each journey with their stops and coordinates.

`euskotren.get_train_positions` returns each active train's estimated position, next stop, estimated arrival time, and timing source. If a train is stopped, it also includes the station it is at.

## Sources and license

- Euskotren timetables, coordinates, and predictions are published by the Basque Government. [Open Data Euskadi / Moveuskadi](https://opendata.euskadi.eus/catalogo/-/moveuskadi-datos-de-la-red-de-transporte-publico-de-euskadi-operadores-horarios-paradas-calendario-tarifas-etc/)
- The map background attribution is shown in the planner. [© OpenStreetMap contributors](https://www.openstreetmap.org/copyright)
- The map uses [Leaflet 1.9.4](https://leafletjs.com/), bundled with its BSD 2-Clause license in `frontend/leaflet`.
- The integration code is distributed under the [MIT license](LICENSE).
