# CAMPO P2P

Demo local de arquitectura peer-to-peer aplicada a un campo de detectores de radiación simulados. Seis contenedores ejecutan el mismo software peer; cuatro comienzan activos y dos quedan preparados para incorporarse a la red.

> Todas las mediciones son sintéticas. El proyecto no representa un instrumento científico real.

## Estado actual

La demostración está completa: backend P2P, gateway, panel visual interactivo y pruebas automatizadas. El mapa permite observar el recorrido de los paquetes, mover nodos, cortar enlaces y modificar el estado de los peers durante la simulación.

## Funciones

- Medición sintética autónoma cada dos segundos.
- Descubrimiento directo y por gossip entre peers.
- Topología lógica full mesh con enlaces controlables.
- Evento global correlacionado y falso positivo individual.
- Confirmación estricta `N/N` o configurable `K/N` (de `0` a `N`) sin usar el gateway.
- Inyección simultánea de falsos positivos en uno o varios peers seleccionados desde el panel.
- Mensajes con ID único, TTL y rechazo de duplicados.
- Rutas alternativas calculadas con Dijkstra.
- Activación y desactivación lógica de peers.
- Sincronización de eventos recientes al reconectar.
- Retención temporal de cinco minutos.
- Gateway que recibe únicamente eventos confirmados.
- Reproducción visual en tiempo real, lenta o muy lenta, con pausa y continuación.
- Notificación visible cuando el TTL se agota antes de alcanzar el destino.

## Servicios

| Servicio | Puerto local | Estado inicial |
|---|---:|---|
| Dashboard / API de control | 8080 | Activo |
| Gateway | 8090 | Activo |
| Peer A | 8101 | Activo |
| Peer B | 8102 | Activo |
| Peer C | 8103 | Activo |
| Peer D | 8104 | Activo |
| Peer E | 8105 | Inactivo lógico |
| Peer F | 8106 | Inactivo lógico |

## Ejecución

```bash
docker compose up --build -d
```

Abrir:

- Interfaz de la demostración: `http://localhost:8080`
- Control y estado agregado: `http://localhost:8080/api/system`
- Documentación interactiva: `http://localhost:8080/docs`
- Gateway: `http://localhost:8090/state`

Detener sin borrar información de Docker:

```bash
docker compose stop
```

## Restaurar la demostración

Con los contenedores activos:

```bash
python scripts/reset_demo.py
```

Esto deja A-D activos, E-F inactivos, todos los enlaces habilitados, política estricta y TTL 5.

## Pruebas

La suite contiene 885 pruebas automatizadas ejecutables: consenso estricto, matriz K/N de 0 a 6,
combinaciones de votos, rutas alternativas, aislamiento de nodos y validaciones
contra los contenedores reales.

Pruebas locales rápidas:

```bash
pytest -q tests
```

Pruebas completas con Docker activo:

```powershell
$env:CAMPO_P2P_LIVE="1"
pytest -q tests
```

También pueden ejecutarse las pruebas locales dentro de la imagen:

```bash
docker run --rm -v "%cd%:/work" -w /work campo-p2p-dashboard pytest -q tests
```

Prueba integral, después de restaurar la demo:

```bash
python scripts/smoke_test.py
```

La prueba integral comprueba un evento `4/4`, el rechazo de un falso positivo y el reenvío de Peer A por otro peer cuando se corta su enlace al gateway.

## Documentación

La descripción detallada está en [`DOCUMENTACION_TECNICA.md`](DOCUMENTACION_TECNICA.md).

Repositorio público: [Miguel-ACD/DESSOF-LAB6](https://github.com/Miguel-ACD/DESSOF-LAB6)
