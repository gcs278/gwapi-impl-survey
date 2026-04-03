# Gateway API Implementation Survey

A deep-dive survey of how [Gateway API](https://gateway-api.sigs.k8s.io/) implementations handle specific features at the native API and dataplane level. Provides a comparative reference for API designers, implementors, and the Gateway API community.

## Current Feature: Session Persistence (GEP-1619)

The first survey covers [GEP-1619: Session Persistence](https://gateway-api.sigs.k8s.io/geps/gep-1619/) — how each implementation supports cookie/header-based session persistence, what Envoy/Nginx mechanisms they use, and how difficult it is to implement the Gateway API `sessionPersistence` field.

### Implementations Analyzed

| Implementation | Type | Dataplane | Route-Inline | BTP |
|---|---|---|---|---|
| Contour | Controller | Envoy | Not implemented | Not implemented |
| Envoy Gateway | Controller | Envoy | Implemented | Not yet |
| Istio | Controller | Envoy | Not implemented | Not implemented |
| kgateway | Controller | Envoy | Implemented | Not yet |
| NGINX Gateway Fabric | Controller | Nginx | Implemented | Not yet |
| Traefik | Integrated | Built-in | PR open | PR open |

**Dataplanes:** Envoy (standalone dataplane analysis)

## Generating the Site

```
pip install -r requirements.txt
python3 scripts/generate_site.py
```

Open `site/index.html` in a browser.

## Project Structure

```
data/            YAML analysis files per implementation/dataplane
scripts/         Static site generator
site/            Generated HTML output (gitignored)
```

## Contributing

To add an implementation, create a YAML file in `data/` following the schema of existing files. PRs welcome.

## License

Apache 2.0 — see [LICENSE](LICENSE).
