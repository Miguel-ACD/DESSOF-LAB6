# Documentación técnica de CAMPO P2P

Repositorio: [https://github.com/Miguel-ACD/DESSOF-LAB6](https://github.com/Miguel-ACD/DESSOF-LAB6)

## Propósito

CAMPO P2P es una demostración local de arquitectura peer-to-peer. Cada contenedor emula un nodo hardware autónomo que produce mediciones sintéticas, descubre otros peers, valida eventos candidatos y reenvía reportes confirmados.

El dashboard es un controlador de prueba y observación. El gateway es un destino externo. Ninguno participa en la decisión distribuida, por lo que la confirmación sigue perteneciendo a la red P2P.

## Componentes

- `peer-a` a `peer-f`: seis instancias equivalentes del mismo servicio FastAPI.
- `gateway`: recibe y agrupa reportes confirmados.
- `dashboard`: configura la simulación y agrega estados para la interfaz.
- Red Docker: proporciona DNS y comunicación HTTP entre contenedores.

Todos los servicios se construyen desde el mismo `Dockerfile` y se coordinan mediante `compose.yaml`. No se requieren servicios en la nube ni credenciales.

## Tecnologías

- Python 3.13 y FastAPI para peers, gateway y API de control.
- HTML, CSS y JavaScript sin frameworks para la interfaz.
- Docker Compose para ejecutar servicios aislados en una sola computadora.
- Pytest para pruebas unitarias, de combinación y de integración.

## Ciclo normal

Cada peer activo genera una medición de fondo cada dos segundos y conserva cinco minutos de información. Los peers envían mensajes `HELLO`, mantienen la última presencia conocida y comparten su membresía mediante gossip. Un peer se considera desconectado después de ocho segundos sin señales.

## Validación de eventos

El controlador puede inyectar un evento global con pequeñas variaciones de valor y timestamp en todos los peers activos. Un coordinador crea un evento candidato y consulta a los demás peers mediante mensajes P2P.

Cada peer responde si encontró una lectura por encima del umbral dentro de la ventana temporal de tres segundos. El coordinador aplica una de dos políticas:

- Estricta: `N/N` peers activos deben confirmar.
- Configurable: el usuario elige `K` votos requeridos entre `0` y `N` peers activos.

Una falta de respuesta produce un resultado inconcluso cuando todavía sería posible alcanzar el mínimo. Una respuesta negativa puede provocar el rechazo. El falso positivo individual demuestra este comportamiento.

## Enrutamiento

La topología lógica comienza como una malla completa. Todos los peers mantienen una copia de los enlaces habilitados y calculan rutas localmente con Dijkstra. Cada paquete contiene ruta, identificador único y TTL.

Si el enlace directo de Peer A al gateway se corta, A puede producir una ruta como `peer-a → peer-b → gateway`. Los intermediarios reenvían el paquete y registran cada salto. Un TTL insuficiente descarta el paquete antes de llegar al destino.

El TTL se reduce en cada salto real. Por ejemplo, en una ruta `A → B → C → D`, un paquete con TTL 1 llega a B y se descarta allí. La interfaz muestra el recorrido alcanzado, el punto de descarte y la alerta solamente después de terminar la animación.

## Gateway

El gateway no recibe mediciones normales ni decide si un evento es verdadero. Después de una confirmación, cada peer que observó el evento envía su propia medición y timestamp. El gateway agrupa los reportes por `event_id` y conserva la ruta utilizada por cada uno.

## Fallos y recuperación

Desactivar un peer cambia su estado lógico sin darle al navegador acceso al motor Docker. El resto de la red detecta su ausencia y continúa trabajando. Al reactivarse, el peer vuelve a anunciarse y solicita eventos recientes a otro nodo.

Los peers E y F están desplegados desde el inicio, pero permanecen inactivos hasta que la interfaz los agrega a la simulación.

## Interfaz y controles

El panel disponible en `http://localhost:8080` permite:

- Arrastrar peers y gateway sin romper la geometría de enlaces o paquetes.
- Activar o desactivar peers lógicos.
- Cortar o restaurar enlaces haciendo clic sobre ellos.
- Inyectar eventos globales y falsos positivos seleccionados.
- Cambiar consenso, votos requeridos y TTL.
- Consultar vecinos, rutas y actividad de cada peer.
- Reproducir el tráfico en `Tiempo real`, `Lento` o `Muy lento` y usar `Pausa/Continuar`.

La actividad lateral se sincroniza con la animación para que cada registro aparezca cuando el paquete correspondiente recorre la red.

## Estructura del código

- `common/core.py`: modelos, consenso, topología y enrutamiento.
- `peer/app.py`: comportamiento autónomo y comunicación entre peers.
- `gateway/app.py`: recepción y agrupamiento de reportes confirmados.
- `dashboard/app.py`: API de control y estado agregado.
- `dashboard/static/`: interfaz visual.
- `scripts/`: restauración y prueba integral de la demostración.
- `tests/`: pruebas automatizadas locales y contra Docker.

## Ejecución local

Requisitos: Docker Desktop con Docker Compose.

```bash
docker compose up --build -d
```

Después, abrir `http://localhost:8080`. Para restaurar el escenario inicial:

```bash
python scripts/reset_demo.py
```

Para detener la demostración:

```bash
docker compose down
```

## Pruebas

Pruebas locales:

```bash
pytest -q
```

Pruebas de integración con los contenedores activos en PowerShell:

```powershell
$env:CAMPO_P2P_LIVE="1"
pytest -q tests/test_live_system.py
```

## Seguridad de la demostración

- La web no monta el socket de Docker.
- No existen credenciales ni servicios externos.
- Los valores se etiquetan como simulados.
- El gateway no es una dependencia para la comunicación y validación P2P.

## Resultados verificados

- Confirmación estricta con cuatro observaciones de cuatro peers.
- Rechazo de un falso positivo con una observación y tres votos negativos.
- Reenvío de Peer A por Peer B al cortar su enlace directo al gateway.
- Descubrimiento indirecto de Peer B a través de Peer C después de cortar A-B.
- Activación de Peer E, sincronización y confirmación posterior de cinco peers.
- 852 pruebas locales aprobadas.
- 33 pruebas de integración aprobadas contra los contenedores reales.
- TTL 1 descartado correctamente en B para una ruta de varios saltos y TTL 3 completado correctamente.
