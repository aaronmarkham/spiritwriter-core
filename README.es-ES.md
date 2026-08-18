

# spiritwriter

**Memoria de agente que es tuya.**

Memoria duradera y con dirección por contenido · trazas verificables · delegación con ámbito · resolución de entidades — local primero, sin ningún servicio que ejecutar y sin ceder datos. Intégralo bajo cualquier orquestador y recuperador que ya utilices.

---

Si has construido más de un sistema basado en agentes, has reconstruido el mismo pegamento cada vez. ¿Dónde se almacena lo que el agente ha aprendido y cómo evitas que se deslice hacia la contradicción? ¿Cómo delegas una subtarea a otro agente sin entregar las llaves de todo? Y tres pasos después, cuando algo sale mal: ¿puedes demostrar qué ocurrió realmente?

La mayoría de los equipos reconstruyen esa capa por aplicación, sufren desalineaciones, fallos de delegación y deriva de memoria, o la rentan a un servicio administrado que toma custodia de sus datos y su presupuesto de latencia. Pocos equipos realizan trazas *verificables* o permisos con ámbito sin desplegar infraestructura pesada. No existe una biblioteca estándar para la capa **debajo** del agente.

spiritwriter es esa capa.

## Qué es

Un **sustrato de memoria configurable en términos de confianza**. Un conjunto de primitivas: fragmentos de memoria con dirección por contenido, trazas encadenadas por hash, permisos con ámbito y resolución de entidades determinística — que puedes ajustar desde *totalmente público y verificable* hasta *totalmente privado y de conocimiento cero* cambiando una sola cosa: la [postura](docs/shard-postures.md) del fragmento.

No es **un** marco de trabajo para agentes ni **una** base de datos vectorial. Es la capa sobre la que se asientan:

```
  your orchestrator   (LangGraph / CrewAI / a raw loop)     ← bring your own
  your retriever      (vector DB / RAG / full-text search)  ← bring your own
  ─────────────────────────────────────────────────────────
  spiritwriter        memory · provenance · delegation       ← yours: local-first, data never leaves
                      · entity resolution
```

Aporta todo lo demás por tu cuenta. spiritwriter se encarga de lo que esas capas omiten: memoria duradera que no deriva, trazas que puedes demostrar ante un tercero, delegación que realmente puedes delimitar y registros de entidades que no se duplican ni colisionan. Es aditiva: no migras, simplemente lo colocas debajo de lo que ya ejecutas. Y al ser local primero, tus datos nunca abandonan tu máquina: nada que aprovisionar, nada que medir, casi nada que desactivar si cambias de opinión. El registro es un archivo que te pertenece, no una fila en la base de datos de otro.

## Comprobado en ambos extremos

El sustrato es real porque dos productos en producción lo ejecutan en extremos opuestos del dial de confianza: mismas primitivas, posturas opuestas:

| | [news.spiritwriter.ai](https://news.spiritwriter.ai) | [frio.help](https://frio.help) |
|---|---|---|
| **Postura** | transparencia máxima | privacidad máxima |
| **Qué hace** | artículos atomizados, reescritos en todo el espectro, cada variante vinculada a su origen | familias que buscan a un familiar encarcelado; listas coincidentes, alertas enviadas |
| **Qué puedes ver** | el linaje completo: sigue un hecho mientras se transforma | nada: las búsquedas están selladas para el operador; la coincidencia ocurre en memoria |

Una sola biblioteca construyó ambas. La única diferencia es la postura.

## Instalación

```bash
pip install -e .                        # core
pip install -e ".[sealed]"              # + NaCl sealed boxes (zero-knowledge)
pip install -e ".[network]"             # + IPFS backend
pip install -e ".[dev,sealed,network]"  # everything
```

Requiere Python 3.9+.

## Inicio Rápido

```python
from spiritwriter.fabric.shard import MemoryShard, ShardAtom, AtomKind, DecayClass
from spiritwriter.fabric.store import ShardStore

store = ShardStore("~/.myapp/shards")

shard = MemoryShard(
    atoms=[
        ShardAtom(text="Project uses FastAPI", kind=AtomKind.FACT,
                  entity="myproject", key="framework", value="FastAPI"),
        ShardAtom(text="Always run migrations before deploying",
                  kind=AtomKind.CONVENTION, entity="myproject",
                  key="deploy_rule", value="migrations-first"),
    ],
    scope="project:myproject",
    origin="dev-agent",
    decay_class=DecayClass.STABLE,
)

ref = store.put(shard)              # idempotent — same content, same ID
context = store.hydrate([ref])      # XML-tagged context, ready for prompt injection
```

Ese es todo el punto de entrada: almacena lo que el agente aprendió e hidrátalo de nuevo en un prompt más tarde. Todo lo demás es el mismo sustrato escalado: cifrado, delegación, procedencia y resolución. Consulta [docs/getting-started.md](docs/getting-started.md) para el modelo de capas y rutas de lectura por caso de uso.

> **Enseñalo a tu agente, no solo a tu aplicación.** Cada capacidad incluye una habilidad legible por agentes (`skills/*/SKILL.md`). Un agente puede aprender las primitivas leyendo una habilidad: sin instalación, sin código de integración por tu parte.

## Qué resuelve

Cada capacidad se mapea a un problema que, de otro modo, resolverías manualmente:

- **Memoria que no deriva** — *Fragmentos de Memoria (Memory Shards).* El conocimiento crece sin perder historial: las nuevas observaciones reemplazan a las antiguas mediante enlaces de linaje; el contenido idéntico de diferentes agentes se deduplica en un solo registro; las clases de decaimiento (`PERMANENT`, `STABLE`, `ACTIVE`, `SESSION`, `CHECKPOINT`) eliminan lo que no debería sobrevivir a su propósito. Con dirección por contenido (SHA-256 sobre átomos + ámbito + origen), por lo que el mismo contenido siempre tiene el mismo ID.
- **Almacenamiento de tu propiedad** — *Shard Store (Repositorio de Fragmentos).* Local primero en disco, distribución de objetos al estilo Git. Las referencias con nombre (punteros mutables a fragmentos inmutables) te dan "la última versión de X" sin romper la dirección por contenido. Obtención en red opcional cuando se configura un backend.
- **Privacidad como configuración** — *Cifrado.* AES-256-GCM cuando el operador y el titular de la clave cooperan; cajas selladas NaCl cuando el operador *no debe* ver el contenido (alojamiento multiinquilino, protección de origen, servicios de conocimiento cero).
- **Delegación que puedes delimitar** — *Permisos (Entitlements) + Trabajos (Jobs).* Entrega a un subagente un token que agrupa claves de descifrado + patrones de ámbito + capacidades + presupuesto; el repositorio aplica cada restricción antes de descifrar. Empaqueta contenido + tarea + permiso en una sola unidad de trabajo; el subagente se hidrata, ejecuta y devuelve un fragmento de resultado. Cada paso queda registrado.
- **Prueba de lo ocurrido** — *Trazabilidad (Tracing).* JSONL encadenado por hash, opcionalmente firmado con Ed25519. Reproduce una ejecución, demuestra que nada fue editado, represéntalo como flujos de trabajo / genealogías / diagramas multiagente — para depurar fallos costosos, auditar antes de desplegar o demostrar la integridad de una ejecución a un tercero.
- **Entidades que no colisionan** — *Resolución de Entidades.* Diferencia a "Bear" el perro de "Bear" la marca; fusiona "Carlos Martinez" y "MARTINEZ, CARLOS A" en uno. Determinístico primero y luego difuso, sin embeddings, sin LLM en la ruta de fusión. (Consulta [The Bear Problem](#the-bear-problem) a continuación.)
- **Compartir sin una base de datos** — *Distribución IPFS.* Publica fragmentos en un enjambre privado; los consumidores obtienen fragmentos faltantes de la red y los almacenan en caché localmente.
- **Auditorías con evidencia de manipulación** — *Auditorías de APK de Android.* Entradas, evidencia, hallazgos y informe vinculados en una traza encadenada por hash más un testigo auto-hash — cualquiera con el APK puede volver a ejecutar la verificación sin conexión.

## Cifrado

```python
from spiritwriter.fabric.crypto import generate_job_key

key = generate_job_key()
encrypted = store.encrypt_and_store(shard, key)        # AES-256-GCM, operator can decrypt with the key
decrypted = store.decrypt_and_get(encrypted.shard_id, key)
```

Conocimiento cero (el operador no puede descifrar: esta es la postura que ejecuta `frio`):

```python
from spiritwriter.fabric.sealed import generate_owner_keypair

keypair = generate_owner_keypair()
sealed = store.seal_and_store(shard, keypair.public_key)   # only the owner's private key opens it
decrypted = store.unseal_and_get(sealed.shard_id, keypair.private_key)
```

## Resolución de Entidades

```python
from spiritwriter.fabric.canonicalize import CanonicalRegistry, CanonicalSchema

schema = CanonicalSchema(
    name="person",
    ess_fields=["last_name", "first_name", "dob"],
    fuzzy_fields={"last_name": 0.90, "first_name": 0.80},
)

candidate = {"last_name": "Smith", "first_name": "John", "dob": "1990-05-12"}
with CanonicalRegistry("/tmp/people.db", schema) as registry:
    result = registry.resolve(candidate)
    cid = registry.upsert(candidate, result, "source_a", "001")
```

La parte interesante es *por qué* esto resuelve correctamente sin un modelo de embedding ni un LLM en el ciclo: ese es el Problema de Bear, a continuación.

## El Problema de Bear

Estás extrayendo hechos sobre Aaron de un montón de documentos. El Documento 1 revela "Bear es el favorito de Aaron". El Documento 2: "Aaron y Bear estaban en el parque". El Documento 3: "Bear, el perro de Aaron, una mezcla de labrador negro / border collie de 10 años (un Borador)".

Cada documento ofrece una cobertura parcial de campos definitorios, y tu extractor clasifica a Bear de tres maneras diferentes: un nombre en el Documento 1, un animal genérico en el Documento 2, un perro específico en el Documento 3. Tres identificadores para la misma entidad, y no se alinean. Un sistema ingenuo los mantiene separados (tienes tres Bears, sin convergencia a medida que llegan más documentos) o colapsa solo por el nombre superficial (ahora Bear-el-perro se fusiona con Bear-la-marca-de-cerveza mencionada en el Documento 4). Los sistemas basados en embeddings alucinan los límites: puntuaron a "Bear" el perro cerca de "Bear" el oso cerca de "Bear" la marca, y las decisiones de fusión se vuelven inauditorables.

El resolvedor hace hash de los *campos definitorios* (nombre + tipo de entidad + propietario + …) en una **Firma de Sentido de Entidad (ESS)**, un hash de identidad determinístico. A medida que llegan más documentos, los campos definitorios se acumulan por entidad. El Documento 1 da `name=Bear, owner=Aaron`. El Documento 3 añade `entity_type=dog, breed=borador`. El conjunto de campos en crecimiento produce una ESS estable en el momento en que tienes suficientes campos para desambiguar. Los campos aún no conocidos no penalizan la coincidencia: están ausentes del hash, y la superposición de ESS recompensa los campos que *sí* compartes.

La misma primitiva maneja el caso inverso: "Carlos Martinez", "MARTINEZ, CARLOS A" y "C. Martinez" en tres listas se deduplican en una entidad, porque sus campos definitorios se normalizan al mismo hash independientemente de la ortografía superficial. (Una advertencia que vale la pena conocer de antemano: el registro solo normaliza mayúsculas/minúsculas y espacios en blanco; cualquier otra cosa es responsabilidad del llamante. Consulta [Normaliza antes de resolver](docs/entity-resolution.md#normalize-before-you-resolve).)

### Niveles de Resolución

| Nivel | Coincidencia | Acción |
|------|-------|--------|
| T1 | Digesto ESS exacto | Fusión automática |
| T2 | Alta calidad difusa + alta superposición de ESS | Fusión automática |
| T3 | Difuso con puntuación combinada más baja | Señalar, no fusionar |
| T4 | Débil superposición de contexto | Solo señalar |

### Stack Tecnológico

Dos capas, una por preocupación:

- **`CanonicalRegistry`** — un solo archivo SQLite. El índice de resolución de entidades: tres tablas (`entities`, `sightings`, `merges`), modo WAL para lectores concurrentes.
- **`ShardStore`** — átomos JSON con dirección por contenido en disco. El conocimiento subyacente al que apunta el registro.

El registro contiene *a qué entidad canónica mapea cada avistamiento*; los fragmentos contienen *qué es realmente la entidad*. La misma arquitectura tanto si estás en una laptop como en un despliegue multi-nodo. Consulta [Memory Shards](docs/memory-shards.md) y [Shard Store](docs/shard-store.md).

### ¿Por Qué Estas Decisiones de Diseño?

- **Local primero.** Un `CanonicalRegistry` es un archivo SQLite; los fragmentos a los que apunta son JSON plano. Sin servicio que ejecutar, sin base de datos vectorial que alojar, sin demonio que mantener activo. El registro *es* el artefacto: envíalo por correo, controla su versión, cópialo entre máquinas, restáuralo desde un respaldo.
- **Determinístico antes que difuso.** Fusión automática solo en T1 y T2. Cualquier cosa más débil se convierte en un evento señalado para revisión humana. Las fusiones falsas son el peor modo de fallo en la resolución de entidades, y las silenciosas son inauditorables. El resolvedor falla en voz alta.
- **Sin LLM en la ruta de fusión automática.** Los LLMs alucinan, y para la resolución de entidades eso significa combinar silenciosamente registros de dos personas diferentes. Determinístico + difuso con niveles explícitos es verificable de extremo a extremo; el juicio de un LLM no lo es. Usa un LLM aguas arriba para extraer átomos si quieres; mantenlo fuera de la decisión de fusión.
- **Impulsado por esquemas, independiente del dominio.** El mismo motor maneja personas, productos, documentos, artículos: cualquier cosa donde puedas nombrar los campos definitorios. Los umbrales de nivel se ajustan por dominio. El hash del esquema se almacena al abrirlo por primera vez; volver a abrirlo con un esquema diferente levanta `ValueError` en lugar de clasificar erróneamente los registros en silencio.
- **Ligero para inicializar.** Sin modelo de embedding que entrenar o alojar, sin GPU, sin índice vectorial que reconstruir al cambiar el esquema. Desde `pip install` hasta resolver entidades en segundos, en una laptop, sin conexión.

### Los Números

**Precisión del 100% en fusión automática: 0 fusiones incorrectas en 5 corpus de referencia**, y presenta el 100% de las coincidencias de la misma entidad para revisión: fusionadas automáticamente en T1/T2 cuando es seguro, señaladas en caso contrario — para que nada se escape en silencio. Sin embeddings, sin llamadas a LLM: SQLite, normalización y coincidencia de cadenas. Consulta [docs/benchmarks/runs-log.md](docs/benchmarks/runs-log.md) para las mediciones y la batería de falsificación detrás de ellas.

La especificación completa ([docs/specs/cmc-spec-v0.1.md](docs/specs/cmc-spec-v0.1.md)) se basa en arte previo académico (EDC/EMNLP 2024, Graphiti/Zep, SimpleMem, EMem-G); la implementación adopta las tres ideas de mayor impacto: identidad con dirección por contenido, escalación por niveles y [extracción en solapas (shingled extraction)](docs/shingled-extraction.md) — y las entrega sin nueva infraestructura.

**Más profundo:** [Guía de Resolución de Entidades](docs/entity-resolution.md), [Extracción en Solapas](docs/shingled-extraction.md), [Especificación CMC-Lite](docs/specs/cmc-lite-v0.1.md).

## Documentación

| Guía | Descripción |
|-------|-------------|
| [Getting Started](docs/getting-started.md) | instalación, el modelo de capas, rutas de lectura por caso de uso |
| [Memory Shards](docs/memory-shards.md) | átomos, clases de decaimiento, hidratación, dirección por contenido |
| [Atoms](docs/atoms.md) | qué es flexible y qué no, ejemplos prácticos para cada AtomKind |
| [Shard Store](docs/shard-store.md) | disposición de almacenamiento, refs con nombre, consultas de ámbito, mantenimiento |
| [Shard Postures](docs/shard-postures.md) | el dial de confianza: cifrado, firma, ámbito, decaimiento y distribución como una sola configuración |
| [Encryption](docs/encryption.md) | AES-GCM, cajas selladas NaCl, modelo de amenazas |
| [Entitlements](docs/entitlements.md) | tokens portadores, capacidades, presupuesto, aplicación de ámbito |
| [Jobs](docs/jobs.md) | empaquetado del trabajo delegado de subagentes; lados emisor / ejecutor |
| [Entity Resolution](docs/entity-resolution.md) | ESS, coincidencia por niveles, normalización, procesamiento por lotes |
| [Shingled Extraction](docs/shingled-extraction.md) | extracción con ventanas superpuestas y consenso multi-paso |
| [Tracing](docs/tracing.md) | procedencia encadenada por hash, verificación de cadena, trazas firmadas |
| [Traced Workflows](docs/traced-workflows.md) | pipelines multi-etapa con punto de control/reanudación |
| [Network Distribution](docs/network-distribution.md) | backend IPFS, manifiestos, enjambre privado, resolución L1/L2 |
| [Substrate Flavor](docs/substrate-flavor.md) | formato de transmisión + reglas de verificación para implementadores sin biblioteca en cualquier lenguaje |
| [Audit](docs/audit.md) | auditorías de seguridad de APK de Android con evidencia de manipulación |
| [Integration Guide](docs/integration-guide.md) | cómo frio, perseus-news y Claude Studio Producer lo utilizan |
| [API Reference](docs/api-reference.md) | superficie completa de la API pública |

## Ejemplos

Demostraciones autocontenidas que ejercitan las APIs de fabric de extremo a extremo: sin llamadas a LLM, sin red, Python puro componiendo fragmentos, trazas, permisos, trabajos y resolución. Cada uno se ejecuta con `python examples/NN_xxx/run.py` y sale con código 0.

| Demo | Qué muestra |
|------|---------------|
| [01_simple_trace](examples/01_simple_trace/) | El padre empaqueta un trabajo, genera un subagente, recibe un fragmento de resultado: dos trazas encadenadas por hash independientes |
| [02_todo_fanout](examples/02_todo_fanout/) | Solicitud compuesta dividida en 4 subagentes, cada uno escribe un fragmento de resultado con linaje `source_ref`, ensamblado por el padre |
| [03_skills_and_tools](examples/03_skills_and_tools/) | El agente usa habilidades y herramientas para planificar un viaje; cada invocación se registra con hashes de entrada/salida |
| [04_governance_divergence](examples/04_governance_divergence/) | El mismo trabajo se ejecuta dos veces: Ejecución A se comporta, Ejecución B excede presupuesto y capacidades; el padre detecta violaciones vía traza |
| [05_delegation_with_trace](examples/05_delegation_with_trace/) | Delegación por clave: raíz → orquestador → 3 trabajadores, cada uno con su propia capacidad hoja Ed25519; los fragmentos firmados rastrean de vuelta al evento que los produjo |
| [06_phalanx_flow](examples/06_phalanx_flow/) | Pipeline completo: documento → división en solapas → átomos → fragmento de memoria → trabajo delegado → resolución de entidades, todo bajo una traza |

Ejecútalos bajo pruebas con `python -m pytest tests/test_demos.py -v`.

## Benchmarks

```bash
python -m pytest benchmarks/ -v -s
```

Consulta [benchmarks/README.md](benchmarks/README.md) para saber qué se mide y cómo interpretarlo, y [docs/benchmarks/runs-log.md](docs/benchmarks/runs-log.md) para las mediciones rastreadas a lo largo del tiempo.

## Arquitectura

```
spiritwriter/
├── audit/          # Auditorías de seguridad de APK de Android con evidencia de manipulación
├── classify/       # Clasificación de contenido/tema
├── fabric/         # Fragmentos, repositorio, cifrado, permisos, trabajos, trazas, red
│   ├── shard.py         # MemoryShard, ShardAtom, ShardRef
│   ├── store.py         # ShardStore (dirección por contenido al estilo Git)
│   ├── crypto.py        # Cifrado AES-256-GCM
│   ├── sealed.py        # Cajas selladas NaCl, firma Ed25519
│   ├── entitlement.py   # Tokens de acceso con ámbito
│   ├── canonicalize.py  # Resolución de entidades (CanonicalRegistry, ESS, niveles)
│   ├── emitter.py       # Eventos de traza encadenados por hash
│   ├── extract.py       # Utilidades de extracción de átomos
│   ├── visualize.py     # Renderizado de diagramas Mermaid
│   ├── network.py       # Protocolo NetworkResolver
│   ├── jobs.py          # JobSpec, package_job
│   ├── runner.py        # hydrate_job, BudgetTracker, create_result_shard
│   └── backends/
│       └── ipfs.py      # Backend IPFS / Kubo
├── geo/            # Tipos geográficos y fragmentos de vista (experimental)
├── ingest/         # Ingestión de documentos (PDF)
├── integrations/   # Adaptadores de proveedores de memoria de terceros (mempalace, ...)
├── kb/             # CRUD de base de conocimiento
├── llm/            # Abstracción de proveedor LLM (Anthropic)
├── models/         # DocumentAtom, KnowledgeProject
├── secrets/        # Gestión de claves API del llavero del SO
├── sw_vocab/       # Canonización de terminología para los propios docs de spiritwriter
└── stopwords.py    # Lista centralizada de stopword
```

## Integraciones

spiritwriter incluye un protocolo de proveedor de memoria enlazable (`spiritwriter/integrations/base.py`) para que cualquier sistema de memoria externo pueda tener soporte de fragmentos con dirección por contenido. Un adaptador está en el árbol:

- **[mempalace](https://github.com/aaronmarkham/mempalace)** — almacén de memoria atómica con recuperación basada en decaimiento y ponderación contextual de entidades. El adaptador `spiritwriter/integrations/mempalace/` lo conecta al repositorio de fragmentos y al registro de entidades.

El mismo protocolo puede conectar **Mem0**, **Zep**, **Mastra** o cualquier capa de memoria personalizada: implementa `MemoryProvider` y `MemoryBackend`, y spiritwriter se encarga del almacenamiento de fragmentos, resolución de entidades, cifrado y trazabilidad por debajo.

## Usado Por

Dos posturas, varios productos:

- **[frio.help](https://frio.help)** — *conocimiento cero.* Monitoreo de listas de encarcelados con fragmentos de búsqueda cifrados y coincidencia difusa de nombres; el operador no puede ver quién buscó.
- **[news.spiritwriter.ai](https://news.spiritwriter.ai)** / **[texascrime.org](https://texascrime.org)** — *totalmente transparente.* Origen → agente → noticias variantes con linaje público y compartición de fragmentos entre consumidores.
- **[podcasts.spiritwriter.ai](https://podcasts.spiritwriter.ai)** — podcasts generados por IA desde producción de video multiagente.
- **[Claude Studio Producer](https://github.com/aaronmarkham/claude-studio-producer)** — pipeline de producción de medios; el ejemplo práctico canónico en [traced-workflows.md](docs/traced-workflows.md).

## Pruebas

```bash
python -m pytest tests/ -v                              # suite completa
python -m pytest tests/test_demos.py -v                 # las demos anteriores
python -m pytest tests/test_ipfs_backend.py -v -m ipfs  # integración IPFS (requiere Kubo)
```

## Registro de Cambios

Consulta [CHANGELOG.md](CHANGELOG.md) para las notas de lanzamiento (0.8.0+). SemVer pre-1.0: **menor** para cambios disruptivos, **parche** para cambios aditivos/no disruptivos.

## Licencia

Apache 2.0
