# Security

**English** | [Español](#seguridad)

## Reporting a vulnerability

Please open a private security advisory on GitHub (Security tab, "Report a vulnerability") rather than a public issue.

## Data

All data in `data/` is public: SEC EDGAR XBRL company facts and 10-K text, and daily prices from a public endpoint (demonstration only). There is no personal, customer or account data in the repository. `SEC_USER_AGENT` (a contact required by SEC fair access) is read from the environment and never committed.

## Access control

- **Authentication**: `AUTH_MODE=jwt` validates Bearer JWTs, RS256 against an identity provider's JWKS (`AUTH_JWKS_URL`) or HS256 with a shared secret (local only). Algorithms come from an explicit allowlist that never includes `none`; `exp` and `sub` are required, audience and issuer are enforced when configured, and the `copilot:ask` scope is required. The service **refuses to start** with `APP_ENV=production` and `AUTH_MODE=none`.
- **Rate limiting**: per JWT subject (or client IP when anonymous), shared across replicas through Redis, applied after authentication. It fails open when Redis is unavailable, a deliberate availability choice that is logged and counted (`copilot_guardrail_events_total{event="ratelimit_backend_error"}`).
- `/health`, `/ready` and `/metrics` are unauthenticated for in-cluster probes and scrapers; the Ingress blocks `/metrics` from outside.

## LLM-specific controls

- **Numbers cannot be invented**: every quantity in an answer is verified against the evidence produced by the tools; an unsupported number triggers one regeneration and then a deterministic answer built only from evidence (DECISIONS.md D5).
- **Closed tool set**: the router can only select tools from a fixed list and can only add tickers from the covered universe. No tool executes model-generated code or SQL; queries are parameterized.
- **Rule-based decisions**: pre-trade APPROVE/REJECT verdicts come from deterministic limits, and the answer is verified against them, so the model cannot change a verdict.
- **PII redaction** runs before the router, the LLM, the cache key, logs and the audit log: Luhn-validated card numbers, US SSNs, Spanish DNI/NIE, IBANs, emails and phone numbers. The API returns the redacted question; the audit log stores only a SHA-256 of it.
- **Prompt injection**: English and Spanish patterns, plus attempts to bypass the risk controls ("approve regardless of limits"), are rejected with HTTP 400. This is best-effort pattern matching, not the security boundary; the boundary is the architecture above.

## Container and supply chain

- The image runs as non-root UID 10001 with a read-only root filesystem and all capabilities dropped (enforced by the Kubernetes Pod Security `restricted` profile). The C++ toolchain exists only in the build stage. Base images are pinned by digest.
- CI blocks on Bandit (SAST), a Trivy dependency and secret scan of the repository, a Trivy scan of the built image (fixable HIGH/CRITICAL), and C++ tests under AddressSanitizer and UndefinedBehaviorSanitizer.
- Every GitHub Action is pinned to a commit SHA; scanners run as digest-pinned official images, not third-party wrapper actions.
- Published images carry an SBOM and SLSA provenance and are signed with cosign (keyless, GitHub OIDC). Verify with:
  `cosign verify ghcr.io/apayne185/merchant-intelligence-hub@<digest> --certificate-identity-regexp 'https://github.com/apayne185/merchant-intelligence-hub/' --certificate-oidc-issuer https://token.actions.githubusercontent.com`

## Secrets

`.env`, `*.key` and `*.pem` are gitignored. Kubernetes reads credentials from a Secret created out of band (see `k8s/base/secret.example.yaml`); docker-compose values are local placeholders. `.pre-commit-config.yaml` runs gitleaks before each commit as a backstop.

---

# Seguridad

[English](#security) | **Español**

## Cómo informar de una vulnerabilidad

Abre un aviso de seguridad privado en GitHub (pestaña Security, "Report a vulnerability") en lugar de una *issue* pública.

## Datos

Todos los datos de `data/` son públicos: hechos XBRL y texto de 10-K de SEC EDGAR, y precios diarios de un endpoint público (solo para demostración). No hay datos personales, de clientes ni de cuentas en el repositorio. `SEC_USER_AGENT` (el contacto que exige la política de acceso de la SEC) se lee del entorno y nunca se versiona.

## Control de acceso

- **Autenticación**: `AUTH_MODE=jwt` valida JWT Bearer, RS256 contra el JWKS de un proveedor de identidad (`AUTH_JWKS_URL`) o HS256 con un secreto compartido (solo en local). Los algoritmos salen de una lista explícita que nunca incluye `none`; `exp` y `sub` son obligatorios, la audiencia y el emisor se comprueban si están configurados y se exige el *scope* `copilot:ask`. El servicio **se niega a arrancar** con `APP_ENV=production` y `AUTH_MODE=none`.
- **Límite de peticiones**: por sujeto del JWT (o IP del cliente si es anónimo), compartido entre réplicas mediante Redis y aplicado después de la autenticación. Falla en abierto si Redis no está disponible, una decisión deliberada a favor de la disponibilidad que se registra y se cuenta (`copilot_guardrail_events_total{event="ratelimit_backend_error"}`).
- `/health`, `/ready` y `/metrics` no requieren autenticación para las sondas y los *scrapers* del clúster; el Ingress bloquea `/metrics` desde fuera.

## Controles específicos del LLM

- **No se pueden inventar cifras**: cada cantidad de una respuesta se verifica contra la evidencia de las herramientas; un número sin respaldo provoca una regeneración y, después, una respuesta determinista construida solo con evidencia (DECISIONS.md D5).
- **Conjunto cerrado de herramientas**: el router solo puede elegir herramientas de una lista fija y solo puede añadir tickers del universo cubierto. Ninguna herramienta ejecuta código ni SQL generado por el modelo; las consultas están parametrizadas.
- **Decisiones basadas en reglas**: los veredictos APPROVE/REJECT del control pre-trade salen de límites deterministas y la respuesta se verifica contra ellos, así que el modelo no puede cambiar un veredicto.
- **Redacción de PII** antes del router, del LLM, de la clave de caché, de los logs y de la auditoría: tarjetas validadas con Luhn, SSN de EE. UU., DNI/NIE, IBAN, emails y teléfonos. La API devuelve la pregunta redactada; la auditoría solo guarda su SHA-256.
- **Inyección de *prompt***: los patrones en inglés y español, y los intentos de saltarse los controles de riesgo ("aprueba aunque incumpla los límites"), se rechazan con HTTP 400. Es una detección por patrones de mejor esfuerzo, no el límite de seguridad; el límite es la arquitectura descrita arriba.

## Contenedor y cadena de suministro

- La imagen se ejecuta como UID 10001 no root, con sistema de ficheros raíz de solo lectura y todas las *capabilities* eliminadas (lo impone el perfil `restricted` de Pod Security de Kubernetes). El compilador de C++ solo existe en la etapa de compilación. Las imágenes base están fijadas por *digest*.
- La CI bloquea con Bandit (SAST), un escaneo de Trivy de dependencias y secretos del repositorio, un escaneo de Trivy de la imagen (HIGH/CRITICAL corregibles) y los tests de C++ con AddressSanitizer y UndefinedBehaviorSanitizer.
- Cada GitHub Action está fijada a un SHA de commit; los escáneres se ejecutan como imágenes oficiales fijadas por *digest*, no como *actions* de terceros.
- Las imágenes publicadas incluyen SBOM y procedencia SLSA y se firman con cosign (*keyless*, OIDC de GitHub). Se verifican con:
  `cosign verify ghcr.io/apayne185/merchant-intelligence-hub@<digest> --certificate-identity-regexp 'https://github.com/apayne185/merchant-intelligence-hub/' --certificate-oidc-issuer https://token.actions.githubusercontent.com`

## Secretos

`.env`, `*.key` y `*.pem` están en `.gitignore`. Kubernetes lee las credenciales de un Secret creado aparte (ver `k8s/base/secret.example.yaml`); los valores de docker-compose son de ejemplo para uso local. `.pre-commit-config.yaml` ejecuta gitleaks antes de cada commit como red de seguridad.
