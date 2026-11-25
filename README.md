# Euskotren Topo para Home Assistant

[English version](README.en.md)

Consulta las salidas del Topo desde la barra lateral de Home Assistant. Elige el origen, el destino, la fecha y la hora a partir de la que quieres salir. Cada resultado muestra la salida, la llegada, la duración, las líneas, las paradas y un mapa interactivo. Si el viaje requiere un transbordo en una misma estación, el buscador combina hasta dos trenes.

## Instalación

Requiere Home Assistant 2026.11 o posterior.

[![Abrir Euskotren Topo en Marketplace](https://my.home-assistant.io/badges/marketplace_repository.svg)](https://my.home-assistant.io/redirect/marketplace_repository/?owner=alfre&repository=home-assistant-euskotren&category=integration)

Abre el enlace, añade el repositorio personalizado si Marketplace te lo pide, instala la integración y reinicia Home Assistant.

Para la instalación manual, copia la carpeta `custom_components/euskotren` del repositorio dentro de `custom_components` en el directorio de configuración de Home Assistant y reinicia.

En **Ajustes → Dispositivos y servicios → Añadir integración**, busca **Euskotren Topo**. Una vez configurada, abre **Euskotren Topo** en la barra lateral.

Para elegir tu estación habitual, abre **Ajustes → Dispositivos y servicios → Euskotren Topo** y entra en las opciones de la integración. El buscador seleccionará ese origen al abrirse y podrás cambiarlo para cada viaje. Elige **Sin origen predeterminado** si prefieres que aparezca en blanco.

## Horarios y mapa

Cada resultado indica si hay transbordo. Cuando lo hay, muestra la estación, la hora de llegada, la hora de salida del siguiente tren y la espera. Al seleccionar el trayecto, encima del mapa verás de qué línea bajar y en cuál continuar.

Al abrir el planificador, el mapa muestra los trenes en servicio sobre el trazado de las líneas. Puedes usar **Ocultar posiciones estimadas** y **Mostrar posiciones estimadas** con o sin un trayecto seleccionado. Cada marcador avanza entre dos paradas según sus horas de salida y llegada. La capa calcula las posiciones con los horarios, aplica las previsiones y cancelaciones recientes de Moveuskadi cuando están disponibles y se actualiza cada 15 segundos mientras está abierta. Los marcadores indican posiciones aproximadas.

- El buscador cubre las líneas T0 y T1 y el tramo que comparten con la E1. Los andenes de una misma estación se agrupan en el selector.
- El calendario aplica los servicios especiales y las excepciones festivas publicados. El panel muestra el intervalo de fechas disponible, que puede incluir días sin salidas publicadas. Las horas se muestran en la zona `Europe/Madrid`, incluidas las llegadas después de medianoche.
- Las estimaciones se aplican cuando hay una actualización reciente asociada al tren y al día de servicio. Si falta una previsión, se muestra el horario programado de ese punto. Las cancelaciones publicadas se excluyen y las paradas saltadas se indican en el recorrido.
- Home Assistant consulta Moveuskadi para cargar los horarios, las estaciones, la geometría y las previsiones. El navegador solicita el fondo del mapa a OpenStreetMap. El trazado y las paradas se pueden consultar si el fondo del mapa no carga.

## Automatizaciones

`euskotren.list_stations` devuelve las estaciones, el origen predeterminado y las fechas cubiertas. Usa los identificadores con `euskotren.search_journeys`. Puedes omitir el origen si has configurado uno en la integración. La fecha, la hora y el límite también son opcionales. La fecha predeterminada es hoy. La hora predeterminada es la actual para hoy y las 00:00 para otro día. El límite predeterminado es 10, con un máximo de 30.

```yaml
- action: euskotren.search_journeys
  data:
    origin: "ES:Euskotren:StopPlace:2580:"
    destination: "ES:Euskotren:StopPlace:2582:"
    time: "17:00"
    limit: 5
  response_variable: topo
```

La respuesta contiene `journeys` con horas ISO 8601, duración en minutos, indicadores de previsión y un listado de tramos con paradas y coordenadas.

`euskotren.get_train_positions` devuelve las posiciones estimadas de los trenes en servicio, la próxima parada, su hora estimada de llegada y la procedencia de los tiempos. Si el tren está detenido, también indica la estación en la que está.

## Fuentes y licencia

- Horarios, coordenadas y previsiones de Euskotren, publicados por el Gobierno Vasco. [Open Data Euskadi / Moveuskadi](https://opendata.euskadi.eus/catalogo/-/moveuskadi-datos-de-la-red-de-transporte-publico-de-euskadi-operadores-horarios-paradas-calendario-tarifas-etc/)
- Fondo cartográfico con atribución visible en el panel. [© OpenStreetMap contributors](https://www.openstreetmap.org/copyright)
- El mapa usa [Leaflet 1.9.4](https://leafletjs.com/), incluido con su licencia BSD de 2 cláusulas en la carpeta `frontend/leaflet`.
- El código de la integración se distribuye bajo la [licencia MIT](LICENSE).
