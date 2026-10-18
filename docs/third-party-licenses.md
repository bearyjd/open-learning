# Third-party licenses

Original application source: GPL-3.0-only; see [LICENSE](../LICENSE). No model
weights, copyrighted books, library exports, or personal reading data are bundled.
The test article is original project text and distributed under the project
license.

Primary dependencies are listed below. Their own notices remain applicable.
This is an inventory of direct dependencies, not a replacement for each shipped
dependency's license files. Before distributing a release APK or container,
retain the resolved transitive dependency notices and provide corresponding
source as required by GPLv3.

| Dependency | License |
| --- | --- |
| Python | PSF License |
| FastAPI, Pydantic, pydantic-settings | MIT |
| Uvicorn | BSD-3-Clause |
| SQLAlchemy, Alembic | MIT |
| PostgreSQL, pgvector extension | PostgreSQL License |
| pgvector Python adapter | MIT |
| psycopg2-binary | LGPL-3.0-or-later with exceptions; bundled libraries have their own notices |
| Trafilatura | Apache-2.0 |
| HTTPX | BSD-3-Clause |
| uv | MIT or Apache-2.0 |
| pytest | MIT |
| Ruff | MIT |
| Kotlin, kotlinx.coroutines | Apache-2.0 |
| AndroidX Compose, Material 3, Room, WorkManager, Lifecycle, AndroidX Test | Apache-2.0 |
| Retrofit, OkHttp (including test-only MockWebServer), Gson | Apache-2.0 |
| Gradle | Apache-2.0 |
| JUnit 4 (tests only, not shipped in the app) | EPL-1.0 |

The database image and Python base image contain additional operating-system
packages. Preserve their notices when redistributing images. The Android SDK
and JDK are build tools, installed separately under their own terms. Local model
licenses are independent of the application license. Do not assume a model is
GPL merely because this application can connect to it.

Upstream references: [pgvector extension](https://github.com/pgvector/pgvector/blob/master/LICENSE),
[Python adapter](https://github.com/pgvector/pgvector-python/blob/master/LICENSE),
[Psycopg](https://github.com/psycopg/psycopg2/blob/master/LICENSE),
[Trafilatura](https://trafilatura.readthedocs.io/en/stable/).
