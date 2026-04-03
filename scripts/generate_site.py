#!/usr/bin/env python3
"""
Generate a static HTML site from implementation analysis YAML files.

Usage: python3 generate_site.py [--data-dir ../data] [--output-dir ../site]
"""

import argparse
import os
import sys
from pathlib import Path

import yaml

SCRIPT_DIR = Path(__file__).parent
DEFAULT_DATA_DIR = SCRIPT_DIR.parent / "data"
DEFAULT_OUTPUT_DIR = SCRIPT_DIR.parent / "site"


def load_implementations(data_dir: Path) -> list[dict]:
    impls = []
    for f in sorted(data_dir.glob("*.yaml")):
        if f.name == "schema.yaml":
            continue
        with open(f) as fh:
            data = yaml.safe_load(fh)
        if data and "metadata" in data:
            data["_filename"] = f.stem
            impls.append(data)
    return impls


# Module-level repo URL for inline_code context
_current_repo_url = ""


def inline_code(text: str) -> str:
    """Convert backtick-wrapped text to <code> tags and linkify PR/issue/GEP references."""
    import re
    # First: backticks → <code> tags
    text = re.sub(r'`([^`]+)`', r'<code>\1</code>', text)

    # Linkify GEP references: GEP-NNNN → link to gateway-api GEPs
    text = re.sub(
        r'GEP-(\d+)',
        r'<a href="https://gateway-api.sigs.k8s.io/geps/gep-\1/" target="_blank">GEP-\1</a>',
        text
    )

    # Linkify PR/issue references using current repo context
    if _current_repo_url:
        base = _current_repo_url.rstrip("/")
        # Collect all PR/issue numbers already linked to avoid double-linking
        linked = set()

        # PR #NNNN (explicit)
        def _link_pr(m):
            linked.add(m.group(1))
            return f'<a href="{base}/pull/{m.group(1)}" target="_blank">PR #{m.group(1)}</a>'
        text = re.sub(r'PR #(\d+)', _link_pr, text)

        # issue #NNNN (explicit)
        def _link_issue(m):
            linked.add(m.group(1))
            return f'<a href="{base}/issues/{m.group(1)}" target="_blank">issue #{m.group(1)}</a>'
        text = re.sub(r'(?<![/"])issue #(\d+)', _link_issue, text)

        # Standalone #NNNN (4+ digits, not already linked above)
        def _link_standalone(m):
            if m.group(1) in linked:
                return m.group(0)  # already linked, skip
            return f'<a href="{base}/issues/{m.group(1)}" target="_blank">#{m.group(1)}</a>'
        text = re.sub(r'(?<!["\w/])#(\d{4,})\b', _link_standalone, text)

    return text


def difficulty_badge(level: str) -> str:
    colors = {
        "trivial": "#28a745",
        "moderate": "#ffc107",
        "significant": "#fd7e14",
        "requires_dataplane_changes": "#dc3545",
    }
    color = colors.get(level, "#6c757d")
    label = level.replace("_", " ").title()
    return f'<span class="badge" style="background:{color}">{label}</span>'


def mapping_badge(level: str) -> str:
    colors = {
        "direct": "#28a745",
        "translation_needed": "#ffc107",
        "not_supported": "#dc3545",
        "no_native_equivalent": "#6c757d",
    }
    color = colors.get(level, "#6c757d")
    label = level.replace("_", " ").title()
    return f'<span class="badge" style="background:{color}">{label}</span>'


def bool_icon(val) -> str:
    if val is True:
        return '<span style="color:#28a745;font-weight:bold">✓</span>'
    elif val is False:
        return '<span style="color:#dc3545;font-weight:bold">✗</span>'
    return "?"


def render_api_stack_diagram(impl: dict) -> str:
    """Render a Mermaid diagram showing the API stack layers."""
    api_stack = impl.get("api_stack", {})
    layers = api_stack.get("layers", [])
    model = api_stack.get("translation_model", "unknown")

    if not layers:
        return ""

    lines = ["graph TB"]

    # Style definitions
    lines.append('    classDef gwapi fill:#fff3e0,stroke:#ff9800,stroke-width:2px,color:#e65100')
    lines.append('    classDef intermediate fill:#fce4ec,stroke:#e91e63,stroke-width:2px,color:#880e4f')
    lines.append('    classDef dataplane fill:#e3f2fd,stroke:#2196f3,stroke-width:2px,color:#0d47a1')
    lines.append('    classDef modelBadge fill:#f5f5f5,stroke:#9e9e9e,stroke-width:1px,font-size:12px')

    # Model badge
    model_label = "DIRECT — No intermediate API" if model == "direct" else "INTERMEDIATE — Has its own API layer"
    lines.append(f'    MODEL["{model_label}"]')
    lines.append(f'    class MODEL modelBadge')

    prev_id = "MODEL"
    for i, layer in enumerate(layers):
        node_id = f"L{i}"
        ltype = layer.get("type", "")
        resources = layer.get("resources", [])
        desc = layer.get("description", "")

        # Build resource list for the node
        res_lines = "<br/>".join(f"<code>{r}</code>" for r in resources[:4])
        if len(resources) > 4:
            res_lines += f"<br/><i>+{len(resources)-4} more</i>"

        lines.append(f'    {node_id}["{layer["name"]}<br/>---<br/>{res_lines}"]')
        lines.append(f'    class {node_id} {ltype}')

        # Arrow between layers
        if i == 0:
            lines.append(f'    {prev_id} --- {node_id}')
        else:
            # Label the arrow with what happens
            arrow_label = "translates to" if ltype == "dataplane" else "configures"
            lines.append(f'    L{i-1} -->|"{arrow_label}"| {node_id}')

    return "\n".join(lines)


def render_part2(impl, ga, ri, btp, fm_rows, ri_gotchas, ri_changes, btp_gotchas, btp_changes, devs_html):
    """Render Part 2 — GWAPI Assessment for implementations, or Ingress Model Mapping for dataplanes."""
    is_dataplane = impl["metadata"].get("type") == "dataplane_only"

    if is_dataplane:
        return render_dataplane_part2(impl)

    return f"""
        <!-- Part 2: GWAPI Assessment -->
        <div class="section-group gwapi-section">
            <h3 class="section-title">Part 2: Gateway API Session Persistence Assessment</h3>

            <details open>
                <summary><h4>Overview</h4></summary>
                <div class="overview-text">{inline_code(ga.get('overview','').strip())}</div>
            </details>

            <details open>
                <summary><h4>Route-Inline Session Persistence</h4></summary>
                <p><strong>Difficulty:</strong> {difficulty_badge(ri.get('difficulty',''))}</p>
                <div class="assessment-text">{inline_code(ri.get('description','').strip())}</div>
                <h5>Mapping Strategy</h5>
                <div class="strategy-text">{inline_code(ri.get('mapping_strategy','').strip())}</div>
                <h5>Gotchas</h5>
                {ri_gotchas}
                <h5>Code Changes Needed</h5>
                {ri_changes}
            </details>

            <details open>
                <summary><h4>BackendTrafficPolicy</h4></summary>
                <p><strong>Difficulty:</strong> {difficulty_badge(btp.get('difficulty',''))}</p>
                <div class="assessment-text">{inline_code(btp.get('description','').strip())}</div>
                <h5>Mapping Strategy</h5>
                <div class="strategy-text">{inline_code(btp.get('mapping_strategy','').strip())}</div>
                <h5>Gotchas</h5>
                {btp_gotchas}
                <h5>Code Changes Needed</h5>
                {btp_changes}
            </details>

            <details open>
                <summary><h4>Field Mapping</h4></summary>
                <table class="field-mapping-table">
                    <thead><tr><th>GWAPI Field</th><th>Native Field</th><th>Mapping</th><th>Notes</th></tr></thead>
                    <tbody>{fm_rows}</tbody>
                </table>
            </details>

            <details open>
                <summary><h4>Deviations from GEP-1619</h4></summary>
                {devs_html}
            </details>

            <details>
                <summary><h4>Summary</h4></summary>
                <div class="summary-text">{inline_code(ga.get('summary','').strip())}</div>
            </details>
        </div>"""


def render_dataplane_part2(impl):
    """Render Part 2 for dataplane-only reports: ingress model mapping + downstream implementations."""
    np = impl.get("native_profile", {})
    imm = np.get("ingress_model_mapping", {})
    downstream = impl.get("downstream_implementations", [])
    name = impl["metadata"]["name"]

    # Ingress model mapping table
    imm_rows = ""
    layers = ["client", "gateway", "route_rule", "backend_service", "endpoint"]
    layer_labels = {
        "client": "Client",
        "gateway": "Gateway / Listener",
        "route_rule": "Route Rule",
        "backend_service": "Backend / Service",
        "endpoint": "Endpoint",
    }
    for layer in layers:
        info = imm.get(layer, {})
        if not info:
            continue
        concept = info.get("envoy_concept", "")
        desc = info.get("description", "").strip()
        sp_config = info.get("session_persistence_config", [])
        sp_html = ""
        if sp_config:
            sp_html = "<ul>" + "".join(f"<li>{inline_code(c)}</li>" for c in sp_config) + "</ul>"
        else:
            sp_html = "<em>none</em>"
        imm_rows += f"""<tr>
            <td><strong>{layer_labels.get(layer, layer)}</strong></td>
            <td><code>{concept}</code></td>
            <td>{inline_code(desc)}</td>
            <td>{sp_html}</td>
        </tr>"""

    # Ingress model mapping diagram
    imm_diagram = render_ingress_model_diagram(impl)

    # Downstream implementations table
    ds_rows = ""
    for ds in downstream:
        mechs = ", ".join(f"<code>{m}</code>" for m in ds.get("uses_mechanisms", []))
        ds_rows += f"""<tr>
            <td><strong>{ds.get('name', '')}</strong></td>
            <td>{mechs}</td>
            <td>{inline_code(ds.get('notes', ''))}</td>
        </tr>"""

    return f"""
        <!-- Part 2: Ingress Model Mapping -->
        <div class="section-group gwapi-section">
            <h3 class="section-title">Part 2: Ingress Model Mapping</h3>

            <details open>
                <summary><h4>How {name} Maps to the Standard Ingress Model</h4></summary>
                <p>How {name}'s configuration model maps to the standard
                   Client → Gateway → Route Rule → Backend/Service → Endpoint model,
                   and where session persistence config lives at each layer.</p>
    </div>
    <div class="mermaid-container">
                <pre class="mermaid">{imm_diagram}</pre>
    </div>
    <div class="content-wrapper">
                <table class="field-mapping-table">
                    <thead><tr>
                        <th>Ingress Layer</th>
                        <th>{name} Concept</th>
                        <th>Description</th>
                        <th>Session Persistence Config</th>
                    </tr></thead>
                    <tbody>{imm_rows}</tbody>
                </table>
            </details>

            <details open>
                <summary><h4>Downstream Implementations</h4></summary>
                <p>Gateway API controllers that use {name} as their dataplane and which
                   session persistence mechanisms they leverage.</p>
                <table class="comparison-table">
                    <thead><tr>
                        <th>Implementation</th>
                        <th>Mechanisms Used</th>
                        <th>Notes</th>
                    </tr></thead>
                    <tbody>{ds_rows}</tbody>
                </table>
            </details>
        </div>"""


def render_ingress_model_diagram(impl):
    """Render a diagram showing how a dataplane's config model maps to the standard ingress model."""
    name = impl["metadata"]["name"]
    np = impl.get("native_profile", {})
    imm = np.get("ingress_model_mapping", {})

    if not imm:
        return """graph LR
    A["No ingress model mapping data"] """

    gw = imm.get("gateway", {})
    rr = imm.get("route_rule", {})
    svc = imm.get("backend_service", {})
    ep = imm.get("endpoint", {})

    gw_concept = gw.get("envoy_concept", "Gateway")
    rr_concept = rr.get("envoy_concept", "Route")
    svc_concept = svc.get("envoy_concept", "Service")
    ep_concept = ep.get("envoy_concept", "Endpoint")

    # Session persistence config items
    gw_sp = gw.get("session_persistence_config", [])
    rr_sp = rr.get("session_persistence_config", [])
    svc_sp = svc.get("session_persistence_config", [])

    def sp_lines(items):
        return "<br/>".join(items[:3]) if items else ""

    gw_sp_html = f"<br/>---<br/>{sp_lines(gw_sp)}" if gw_sp else ""
    rr_sp_html = f"<br/>---<br/>{sp_lines(rr_sp)}" if rr_sp else ""
    svc_sp_html = f"<br/>---<br/>{sp_lines(svc_sp)}" if svc_sp else ""

    return f"""graph LR
    CLIENT["Client"] --> GW
    GW --> RR
    RR --> SVC
    SVC --> EP

    subgraph "Standard Ingress Model"
        direction LR
        GW_STD["Gateway<br/><i>(Listener)</i>"]
        RR_STD["Route Rule<br/><i>(matches + filters)</i>"]
        SVC_STD["Backend / Service<br/><i>(backendRef)</i>"]
        EP_STD["Endpoint<br/><i>(Pod)</i>"]
    end

    subgraph "{name} Config Model"
        direction LR
        GW["{gw_concept}{gw_sp_html}"]
        RR["{rr_concept}{rr_sp_html}"]
        SVC["{svc_concept}{svc_sp_html}"]
        EP["{ep_concept}"]
    end

    GW_STD -.-|"maps to"| GW
    RR_STD -.-|"maps to"| RR
    SVC_STD -.-|"maps to"| SVC
    EP_STD -.-|"maps to"| EP

    classDef standard fill:#e8eaf6,stroke:#3f51b5,stroke-width:2px,color:#1a237e
    classDef dataplane fill:#e8f5e9,stroke:#2e7d32,stroke-width:2px,color:#1b5e20
    classDef spconfig fill:#fff3e0,stroke:#ff9800,stroke-width:1px
    class GW_STD,RR_STD,SVC_STD,EP_STD,CLIENT standard
    class GW,RR,SVC,EP dataplane"""


def render_implementation(impl: dict) -> str:
    global _current_repo_url
    m = impl["metadata"]
    _current_repo_url = m.get("repo_url", "")
    np = impl.get("native_profile", {})
    ga = impl.get("gwapi_assessment", {})

    mechanisms_html = ""
    for mech in np.get("mechanisms", []):
        config_rows = ""
        for opt in mech.get("config_options", []):
            config_rows += f"""<tr>
                <td><code>{opt['field']}</code></td>
                <td>{opt.get('type','')}</td>
                <td>{'Yes' if opt.get('required') else 'No'}</td>
                <td><code>{opt.get('default','—')}</code></td>
                <td>{inline_code(opt.get('description',''))}</td>
            </tr>"""

        attach = mech.get("attachment_model", {})
        mech_type_class = "soft" if mech.get("type") == "soft_affinity" else "strong"
        mechanisms_html += f"""
        <div class="mechanism {mech_type_class}">
            <h4>{mech['name']}
                <span class="badge {'badge-soft' if mech_type_class == 'soft' else 'badge-strong'}">
                    {'Soft Affinity' if mech_type_class == 'soft' else 'Strong Persistence'}
                </span>
            </h4>
            <p>{inline_code(mech.get('description','').strip())}</p>
            <p><strong>Enabled by default:</strong> {'Yes' if mech.get('enabled_by_default') else 'No'}</p>
            <p><strong>Configuration method:</strong> <code>{mech.get('configuration_method','')}</code></p>
            <h5>Configuration Options</h5>
            <table class="config-table">
                <thead><tr><th>Field</th><th>Type</th><th>Required</th><th>Default</th><th>Description</th></tr></thead>
                <tbody>{config_rows}</tbody>
            </table>
            <h5>Attachment Model</h5>
            <div class="attachment-info">
                <p><strong>Attaches to:</strong> {attach.get('attaches_to','')}</p>
                <p><strong>Resource type:</strong> <code>{attach.get('resource_type','')}</code></p>
                <p><strong>Scope:</strong> {attach.get('scope','')}</p>
            </div>
        </div>"""

    # Capabilities table
    caps = np.get("capabilities", {})
    cookie_attrs = caps.get("cookie_attributes", {})
    caps_html = f"""
    <table class="caps-table">
        <tr><td>Cookie Persistence</td><td>{bool_icon(caps.get('cookie_persistence'))}</td></tr>
        <tr><td>Header Persistence</td><td>{bool_icon(caps.get('header_persistence'))}</td></tr>
        <tr><td>Source IP Affinity</td><td>{bool_icon(caps.get('source_ip_affinity'))}</td></tr>
        <tr><td>URL-Encoded Sessions</td><td>{bool_icon(caps.get('url_encoded_sessions'))}</td></tr>
        <tr><td>Idle Timeout</td><td>{bool_icon(caps.get('idle_timeout'))}</td></tr>
        <tr><td>Absolute Timeout</td><td>{bool_icon(caps.get('absolute_timeout'))}</td></tr>
        <tr><td>Lifetime: Session</td><td>{bool_icon(caps.get('lifetime_type_session'))}</td></tr>
        <tr><td>Lifetime: Permanent</td><td>{bool_icon(caps.get('lifetime_type_permanent'))}</td></tr>
    </table>
    <h5>Cookie Attributes</h5>
    <table class="caps-table">
        <tr><td>Name</td><td>{bool_icon(cookie_attrs.get('name'))}</td></tr>
        <tr><td>Path</td><td>{bool_icon(cookie_attrs.get('path'))}</td></tr>
        <tr><td>Domain</td><td>{bool_icon(cookie_attrs.get('domain'))}</td></tr>
        <tr><td>TTL</td><td>{bool_icon(cookie_attrs.get('ttl'))}</td></tr>
        <tr><td>Secure</td><td>{bool_icon(cookie_attrs.get('secure'))}</td></tr>
        <tr><td>HttpOnly</td><td>{bool_icon(cookie_attrs.get('http_only'))}</td></tr>
        <tr><td>SameSite</td><td>{bool_icon(cookie_attrs.get('same_site'))}</td></tr>
    </table>"""

    # Limitations
    lims = np.get("limitations", [])
    lims_html = "<ul>" + "".join(f"<li>{inline_code(l)}</li>" for l in lims) + "</ul>" if lims else "<p>None documented</p>"

    # Known issues
    issues = np.get("known_issues", [])
    issues_html = "<ul>" + "".join(
        f'<li><a href="{i["url"]}" target="_blank">{i["url"].split("/")[-1]}</a> — {i["description"]}</li>'
        for i in issues
    ) + "</ul>" if issues else "<p>None</p>"

    # GWAPI Assessment
    ri = ga.get("route_inline", {})
    btp = ga.get("backend_traffic_policy", {})

    ri_gotchas = "<ul>" + "".join(f"<li>{inline_code(g)}</li>" for g in ri.get("gotchas", [])) + "</ul>"
    ri_changes = "<ul>" + "".join(f"<li>{inline_code(c)}</li>" for c in ri.get("code_changes_needed", [])) + "</ul>"
    btp_gotchas = "<ul>" + "".join(f"<li>{inline_code(g)}</li>" for g in btp.get("gotchas", [])) + "</ul>"
    btp_changes = "<ul>" + "".join(f"<li>{inline_code(c)}</li>" for c in btp.get("code_changes_needed", [])) + "</ul>"

    # Field mapping table
    fm_rows = ""
    for fm in ga.get("field_mapping", []):
        fm_rows += f"""<tr>
            <td><code>{fm['gwapi_field']}</code></td>
            <td><code>{fm.get('native_field','—') or '—'}</code></td>
            <td>{mapping_badge(fm.get('mapping_difficulty',''))}</td>
            <td>{inline_code(fm.get('notes',''))}</td>
        </tr>"""

    # Deviations
    devs = ga.get("deviations", [])
    devs_html = "<ul>" + "".join(f"<li>{inline_code(d)}</li>" for d in devs) + "</ul>" if devs else "<p>None</p>"

    # Dataplane translation
    dt = np.get("dataplane_translation", {})
    dp_features = dt.get("envoy_features_used", []) or dt.get("nginx_features_used", []) or []
    envoy_features = ", ".join(f"<code>{f}</code>" for f in dp_features)
    code_paths = "<ul>" + "".join(f"<li><code>{p}</code></li>" for p in dt.get("key_code_paths", [])) + "</ul>"

    # Architecture diagrams (Mermaid)
    attachment_diagram = render_attachment_diagram(impl)
    detail_diagram = render_detail_diagram(impl)
    api_stack_diagram = render_api_stack_diagram(impl)

    # API stack section
    api_stack = impl.get("api_stack", {})
    model = api_stack.get("translation_model", "")
    model_desc = {
        "direct": "This implementation translates Gateway API resources directly to dataplane configuration with no intermediate API layer.",
        "intermediate": "This implementation has its own API layer between Gateway API and the dataplane. Gateway API config must be translated through (or bypass) this intermediate layer.",
    }.get(model, "")

    api_stack_html = ""
    if api_stack_diagram:
        api_stack_html = f"""
        <div class="api-stack-section">
            <h3>API Stack</h3>
            <p>{model_desc}</p>
        </div>
    </div>
    <div class="mermaid-container">
        <pre class="mermaid">{api_stack_diagram}</pre>
    </div>
    <div class="content-wrapper">"""

    return f"""
    <div class="content-wrapper">
    <div class="impl-report" id="{impl['_filename']}">
        <div class="impl-header">
            <h2>{m['name']}</h2>
            <div class="impl-meta">
                <span class="meta-item"><strong>Type:</strong> <a href="ecosystem.html">{m.get('type','')}</a></span>
                {'<span class="meta-item"><strong>Dataplane:</strong> ' + m.get('dataplane','') + '</span>' if m.get('type') != 'dataplane_only' else ''}
                {'<span class="meta-item"><strong>Translation:</strong> <a href="ecosystem.html#translation-' + model + '">' + model + '</a></span>' if m.get('type') != 'dataplane_only' else ''}
                <span class="meta-item"><strong>Version:</strong> {m.get('version_analyzed','')}</span>
                <span class="meta-item"><strong>Updated:</strong> {m.get('last_updated','')}</span>
                <span class="meta-item"><a href="{m.get('repo_url','')}" target="_blank">Repo</a></span>
                <span class="meta-item"><a href="{m.get('docs_url','')}" target="_blank">Docs</a></span>
            </div>
            {'<div class="overall-rating"><strong>Overall GWAPI Difficulty:</strong> ' + difficulty_badge(ga.get("overall_difficulty","")) + '</div>' if m.get('type') != 'dataplane_only' else ''}
        </div>

        {api_stack_html}

        <!-- Part 1: Native Profile -->
        <div class="section-group">
            <h3 class="section-title">{'Part 1: Dataplane Session Persistence Profile' if m.get('type') == 'dataplane_only' else 'Part 1: Native Implementation Profile'}</h3>

            <details open>
                <summary><h4>Overview</h4></summary>
                <div class="overview-text">{inline_code(np.get('overview','').strip())}</div>
            </details>

            <details open>
                <summary><h4>Session Persistence Mechanisms</h4></summary>
                {mechanisms_html}
            </details>

            <h4>Attachment Model (Standardized)</h4>
            <p>Where native session persistence config and GEP-1619 APIs attach in the standard ingress model.
               Green/purple = native mechanisms. Orange dashed = GEP-1619 route-inline. Blue dashed = GEP-1619 BTP.
               A "?" label indicates an uncertain or unresolved translation path to the native API.</p>
        </div>
    </div>
    <div class="mermaid-container">
        <pre class="mermaid">{attachment_diagram}</pre>
    </div>
    <div class="content-wrapper">
        <div class="section-group">

            <details>
                <summary><h4>Detailed Architecture &amp; Dataplane Translation</h4></summary>
                <div class="mermaid-container">
                    <pre class="mermaid">{detail_diagram}</pre>
                </div>
                <h5>Dataplane Features Used</h5>
                <p>{envoy_features}</p>
                <h5>Key Code Paths</h5>
                {code_paths}
                <h5>Config Flow</h5>
                <pre class="flow-text">{inline_code(dt.get('flow','').strip())}</pre>
            </details>

            <details>
                <summary><h4>Capabilities</h4></summary>
                {caps_html}
            </details>

            <details>
                <summary><h4>Limitations</h4></summary>
                {lims_html}
            </details>

            <details>
                <summary><h4>Known Issues</h4></summary>
                {issues_html}
            </details>
        </div>

        {render_part2(impl, ga, ri, btp, fm_rows, ri_gotchas, ri_changes, btp_gotchas, btp_changes, devs_html)}
    </div>
    </div>"""


def render_reference_model_diagram() -> str:
    """The canonical Gateway API ingress model with GEP-1619 attachment points."""
    return """graph LR
    CLIENT["Client"] --> GW["Gateway<br/><i>(Listener)</i>"]
    GW --> RR["Route Rule<br/><i>(matches + filters)</i>"]
    RR --> SVC["Backend / Service<br/><i>(backendRef)</i>"]
    SVC --> EP["Endpoint<br/><i>(Pod)</i>"]

    RI["GEP-1619<br/>Route-Inline<br/><code>sessionPersistence</code>"] -.->|"attaches here"| RR
    BTP["GEP-1619<br/>BackendTrafficPolicy<br/><code>sessionPersistence</code>"] -.->|"attaches here"| SVC

    classDef layer fill:#e8eaf6,stroke:#3f51b5,stroke-width:2px,color:#1a237e
    classDef gwapi_ri fill:#fff3e0,stroke:#ff9800,stroke-width:2px,stroke-dasharray: 5 5
    classDef gwapi_btp fill:#e3f2fd,stroke:#2196f3,stroke-width:2px,stroke-dasharray: 5 5
    class CLIENT,GW,RR,SVC,EP layer
    class RI gwapi_ri
    class BTP gwapi_btp"""


def render_attachment_diagram(impl: dict) -> str:
    """Generate a standardized attachment model diagram showing where native mechanisms
    and GEP-1619 APIs attach in the standard ingress model.

    For implementations with gwapi_attachment data (intermediate translation models),
    shows how GEP-1619 attachment points relate to native APIs — including open questions
    about which native mechanism to translate through.
    """
    name = impl["metadata"]["name"]
    np = impl.get("native_profile", {})
    attachment_points = np.get("attachment_points", [])
    gwapi_attach = np.get("gwapi_attachment", {})

    # Color palette for native mechanisms
    mech_colors = [
        ("#28a745", "#d4edda"),  # green
        ("#6f42c1", "#e8dff5"),  # purple
        ("#17a2b8", "#d1ecf1"),  # teal
        ("#e83e8c", "#fce4ec"),  # pink
    ]

    # Build native mechanism attachment nodes and links
    native_nodes = ""
    native_links = ""
    native_classes = ""

    layer_ids = {
        "global": "GL",
        "gateway": "GW",
        "route_rule": "RR",
        "backend_service": "SVC",
        "endpoint": "EP",
    }

    # Map mechanism names to node IDs for gwapi_attachment cross-references
    mech_to_node = {}

    for i, ap in enumerate(attachment_points):
        node_id = f"NATIVE{i}"
        mech_name = ap.get("mechanism", "")
        resource = ap.get("native_resource", "")
        target = layer_ids.get(ap.get("layer", ""), "SVC")
        stroke, fill = mech_colors[i % len(mech_colors)]

        mech_to_node[mech_name] = node_id
        native_nodes += f'    {node_id}["{name} Native<br/><b>{mech_name}</b><br/><code>{resource}</code>"]\n'
        native_links += f'    {node_id} ==>|"attaches here"| {target}\n'
        native_classes += f"    classDef native{i} fill:{fill},stroke:{stroke},stroke-width:2px\n"
        native_classes += f"    class {node_id} native{i}\n"

    # GEP-1619 attachment — customized if gwapi_attachment data exists
    gwapi_nodes = ""
    gwapi_links = ""

    if gwapi_attach:
        # Route-Inline
        ri = gwapi_attach.get("route_inline", {})
        ri_label = ri.get("label", "attaches here")
        ri_target = layer_ids.get(ri.get("target_layer", "route_rule"), "RR")
        gwapi_nodes += '    RI["GEP-1619<br/>Route-Inline<br/><code>sessionPersistence</code>"]\n'
        gwapi_links += f'    RI -.->|"{ri_label}"| {ri_target}\n'

        # BTP
        btp = gwapi_attach.get("btp", {})
        btp_target = layer_ids.get(btp.get("target_layer", "backend_service"), "SVC")
        gwapi_nodes += '    BTP["GEP-1619<br/>BackendTrafficPolicy<br/><code>sessionPersistence</code>"]\n'

        native_translations = btp.get("native_translations", [])
        if native_translations:
            # Show relationship to each native mechanism with custom labels
            for nt in native_translations:
                mech_name = nt.get("mechanism", "")
                label = nt.get("label", "?")
                target_node = mech_to_node.get(mech_name, btp_target)
                gwapi_links += f'    BTP -.->|"{label}"| {target_node}\n'
        else:
            gwapi_links += f'    BTP -.->|"attaches here"| {btp_target}\n'

        # Dataplane target node — shows what dataplane config gets generated
        # Use a shared node if RI and BTP target the same dataplane API,
        # otherwise create separate nodes.
        ri_dp = ri.get("dataplane_target", {})
        btp_dp = btp.get("dataplane_target", {})
        if ri_dp or btp_dp:
            dp_name = (ri_dp or btp_dp).get("name", "")
            # Check if both target the same dataplane API
            if ri_dp and btp_dp and ri_dp.get("name") == btp_dp.get("name"):
                gwapi_nodes += f'    DP_TARGET["{dp_name}"]\n'
                gwapi_links += f'    RI -.->|"{ri_dp.get("label", "generates")}"| DP_TARGET\n'
                gwapi_links += f'    BTP -.->|"{btp_dp.get("label", "generates")}"| DP_TARGET\n'
                native_classes += "    classDef dptarget fill:#e8f5e9,stroke:#2e7d32,stroke-width:2px\n"
                native_classes += "    class DP_TARGET dptarget\n"
            else:
                if ri_dp:
                    gwapi_nodes += f'    DP_RI["{ri_dp.get("name", "")}"]\n'
                    gwapi_links += f'    RI -.->|"{ri_dp.get("label", "generates")}"| DP_RI\n'
                    native_classes += "    classDef dptarget_ri fill:#e8f5e9,stroke:#2e7d32,stroke-width:2px\n"
                    native_classes += "    class DP_RI dptarget_ri\n"
                if btp_dp:
                    gwapi_nodes += f'    DP_BTP["{btp_dp.get("name", "")}"]\n'
                    gwapi_links += f'    BTP -.->|"{btp_dp.get("label", "generates")}"| DP_BTP\n'
                    native_classes += "    classDef dptarget_btp fill:#e8f5e9,stroke:#2e7d32,stroke-width:2px\n"
                    native_classes += "    class DP_BTP dptarget_btp\n"

        # Open question node if present
        open_q = btp.get("open_question", "")
        if open_q:
            # Truncate for diagram readability
            short_q = open_q[:80] + "..." if len(open_q) > 80 else open_q
            gwapi_nodes += f'    OQ["Open Question:<br/>{short_q}"]\n'
            gwapi_links += f'    BTP -.-|"unresolved"| OQ\n'
            native_classes += "    classDef openq fill:#fff3cd,stroke:#ffc107,stroke-width:2px,stroke-dasharray: 3 3\n"
            native_classes += "    class OQ openq\n"
    else:
        # Default: generic attachment to abstract layers
        gwapi_nodes += '    RI["GEP-1619<br/>Route-Inline<br/><code>sessionPersistence</code>"]\n'
        gwapi_nodes += '    BTP["GEP-1619<br/>BackendTrafficPolicy<br/><code>sessionPersistence</code>"]\n'
        gwapi_links += '    RI -.->|"attaches here"| RR\n'
        gwapi_links += '    BTP -.->|"attaches here"| SVC\n'

    return f"""graph LR
    CLIENT["Client"] --> GW["Gateway<br/><i>(Listener)</i>"]
    GW --> RR["Route Rule<br/><i>(matches + filters)</i>"]
    RR --> SVC["Backend / Service<br/><i>(backendRef)</i>"]
    SVC --> EP["Endpoint<br/><i>(Pod)</i>"]

{native_nodes}
{native_links}
{gwapi_nodes}
{gwapi_links}
    classDef layer fill:#e8eaf6,stroke:#3f51b5,stroke-width:2px,color:#1a237e
    classDef gwapi_ri fill:#fff3e0,stroke:#ff9800,stroke-width:2px,stroke-dasharray: 5 5
    classDef gwapi_btp fill:#e3f2fd,stroke:#2196f3,stroke-width:2px,stroke-dasharray: 5 5
    class CLIENT,GW,RR,SVC,EP layer
    class RI gwapi_ri
    class BTP gwapi_btp
{native_classes}"""


def render_detail_diagram(impl: dict) -> str:
    """Implementation-specific detailed architecture diagram (config flow)."""
    name = impl["metadata"]["name"]

    if name == "Istio":
        return """graph TB
    subgraph "User Configuration"
        DR["DestinationRule<br/><i>trafficPolicy.loadBalancer<br/>.consistentHash</i>"]
        SvcLabel["Service Label<br/><i>istio.io/persistent-session<br/>= 'name:/path'</i>"]
        GWAPI_RI["HTTPRoute Rule<br/><i>sessionPersistence</i><br/><b>(GEP-1619 route-inline)</b>"]
        GWAPI_BTP["BackendTrafficPolicy<br/><i>sessionPersistence</i><br/><b>(GEP-1619 BTP)</b>"]
    end

    subgraph "Istio Pilot (istiod)"
        HVS["hashForVirtualService()"]
        ARLB["ApplyRingHashLoadBalancer()"]
        MBSS["MaybeBuildStatefulSession<br/>FilterConfig()"]
        GWCTRL["Gateway API Controller<br/><b>TODO: #55839</b>"]
    end

    subgraph "Envoy Configuration"
        subgraph "Cluster"
            LB["lb_policy: RING_HASH<br/>ring_hash_lb_config"]
        end
        subgraph "Route"
            HP["hash_policy[]<br/>.cookie{name,path,ttl}<br/>.header{name}"]
            TPFC["typed_per_filter_config<br/>.stateful_session"]
        end
        subgraph "Listener"
            SSF["http_filters[]<br/>.stateful_session<br/>(disabled by default)"]
        end
    end

    DR -->|"ConsistentHashLB<br/>(soft affinity)"| HVS
    HVS --> HP
    DR --> ARLB
    ARLB --> LB

    SvcLabel -->|"StatefulSession<br/>(strong persistence)"| MBSS
    MBSS --> SSF
    MBSS --> TPFC

    GWAPI_RI -.->|"NOT YET<br/>IMPLEMENTED"| GWCTRL
    GWAPI_BTP -.->|"NOT YET<br/>IMPLEMENTED"| GWCTRL
    GWCTRL -.->|"?"| HP
    GWCTRL -.->|"?"| TPFC

    classDef notimpl fill:#fff3cd,stroke:#ffc107,stroke-dasharray: 5 5
    classDef existing fill:#d4edda,stroke:#28a745
    classDef envoy fill:#cce5ff,stroke:#004085
    class GWAPI_RI,GWAPI_BTP,GWCTRL notimpl
    class DR,SvcLabel,HVS,ARLB,MBSS existing
    class LB,HP,TPFC,SSF envoy"""

    if name == "Envoy Gateway":
        return """graph TB
    subgraph "User Configuration"
        GWAPI_RI["HTTPRoute Rule<br/><i>sessionPersistence</i><br/><b>(GEP-1619 route-inline)</b><br/><span style='color:green'>IMPLEMENTED</span>"]
        GWAPI_BTP["BackendTrafficPolicy<br/><i>sessionPersistence</i><br/><b>(GEP-1619 BTP — NOT YET)</b>"]
    end

    subgraph "Envoy Gateway Controller"
        ROUTE["route.go<br/><i>buildRouteRuleIR()</i><br/>reads rule.SessionPersistence"]
        IR["IR SessionPersistence<br/><i>Cookie{Name,TTL}<br/>or Header{Name}</i>"]
        PHCM["session_persistence.go<br/><i>patchHCM()</i><br/>adds disabled filter"]
        PR["session_persistence.go<br/><i>patchRoute()</i><br/>enables per-route"]
        CPTH["routePathToCookiePath()<br/><i>exact / prefix /<br/>longest-non-regex-prefix</i>"]
    end

    subgraph "Envoy Configuration"
        subgraph "Listener (HCM)"
            SSF["http_filters[]<br/>.stateful_session<br/><b>(disabled by default)</b>"]
        end
        subgraph "Route"
            TPFC["typed_per_filter_config<br/>.stateful_session<br/><i>CookieBasedSessionState<br/>or HeaderBasedSessionState</i>"]
        end
    end

    GWAPI_RI -->|"reads directly"| ROUTE
    ROUTE --> IR
    IR --> PHCM
    PHCM --> SSF
    IR --> PR
    PR --> TPFC
    ROUTE --> CPTH
    CPTH -->|"sets cookie path"| TPFC

    GWAPI_BTP -.->|"NOT YET<br/>IMPLEMENTED"| IR

    classDef notimpl fill:#fff3cd,stroke:#ffc107,stroke-dasharray: 5 5
    classDef existing fill:#d4edda,stroke:#28a745
    classDef envoy fill:#cce5ff,stroke:#004085
    classDef pipeline fill:#f0f0f0,stroke:#666
    class GWAPI_BTP notimpl
    class GWAPI_RI existing
    class SSF,TPFC envoy
    class ROUTE,IR,PHCM,PR,CPTH pipeline"""

    if name == "NGINX Gateway Fabric":
        return """graph TB
    subgraph "User Configuration"
        GWAPI_RI["HTTPRoute/GRPCRoute Rule<br/><i>sessionPersistence</i><br/><b>(GEP-1619 route-inline)</b>"]
        USP["UpstreamSettingsPolicy<br/><i>loadBalancingMethod: ip_hash</i>"]
        GWAPI_BTP["BackendTrafficPolicy<br/><i>sessionPersistence</i><br/><b>(GEP-1619 BTP — NOT YET)</b>"]
    end

    subgraph "NGF Controller Pipeline"
        CP["Change Processor<br/><i>watches ~25+ resource types</i>"]
        GB["Graph Builder<br/><i>processSessionPersistence<br/>Config()</i>"]
        DB["Dataplane Builder<br/><i>buildUpstream() populates<br/>SessionPersistenceConfig</i>"]
        CG["Config Generator<br/><i>createUpstream() →<br/>getSessionPersistenceConfig()</i>"]
    end

    subgraph "NGINX Config"
        subgraph "upstream block"
            SC["sticky cookie {name}<br/>expires={expiry}<br/>path={path};<br/><i>(Plus only)</i>"]
            IH["ip_hash;<br/><i>(OSS and Plus)</i>"]
        end
        subgraph "location block"
            PP["proxy_pass<br/>http://upstream"]
        end
    end

    GWAPI_RI -->|"cookie persistence<br/>(Plus only)"| CP
    USP -->|"ip_hash<br/>(OSS + Plus)"| CP
    CP --> GB
    GB --> DB
    DB --> CG
    CG --> SC
    CG --> IH
    CG --> PP

    GWAPI_BTP -.->|"NOT YET<br/>IMPLEMENTED"| CP

    classDef notimpl fill:#fff3cd,stroke:#ffc107,stroke-dasharray: 5 5
    classDef existing fill:#d4edda,stroke:#28a745
    classDef nginx fill:#cce5ff,stroke:#004085
    classDef pipeline fill:#f0f0f0,stroke:#666
    class GWAPI_BTP notimpl
    class GWAPI_RI,USP existing
    class SC,IH,PP nginx
    class CP,GB,DB,CG pipeline"""

    if name == "Contour":
        return """graph TB
    subgraph "User Configuration"
        HTTPPROXY["HTTPProxy<br/><i>loadBalancerPolicy:<br/>strategy: Cookie | RequestHash</i>"]
        GWAPI_RI["HTTPRoute Rule<br/><i>sessionPersistence</i><br/><b>(NOT IMPLEMENTED — issue #6427)</b>"]
        GWAPI_BTP["BackendLBPolicy<br/><i>sessionPersistence</i><br/><b>(NOT IMPLEMENTED)</b>"]
    end

    subgraph "Contour Control Plane"
        HPP["httpproxy_processor.go<br/><i>loadBalancerRequestHashPolicies()</i>"]
        GAP["gatewayapi_processor.go<br/><i>clusterRoutes()<br/><b>NO session affinity code</b></i>"]
        DAG["DAG IR<br/><i>Route.RequestHashPolicies[]<br/>Cluster.LoadBalancerPolicy</i>"]
        XDS["Envoy xDS Translation<br/><i>envoy_cluster.go<br/>envoy_route.go</i>"]
    end

    subgraph "Envoy Configuration"
        subgraph "Cluster"
            RINGHASH["lb_policy: RING_HASH<br/><i>ring_hash_lb_config</i>"]
        end
        subgraph "Route"
            HP["hash_policy[]<br/><i>cookie{X-Contour-Session-Affinity}<br/>header{name}<br/>query_parameter{name}<br/>connection_properties{source_ip}</i>"]
        end
        NO_SSF["stateful_session filter<br/><b>NOT USED</b>"]
    end

    HTTPPROXY -->|"native path"| HPP
    HPP -->|"sets RequestHashPolicies"| DAG
    DAG --> XDS
    XDS --> RINGHASH
    XDS --> HP

    GWAPI_RI -.->|"NOT<br/>IMPLEMENTED"| GAP
    GWAPI_BTP -.->|"NOT<br/>IMPLEMENTED"| GAP
    GAP -.->|"no session affinity<br/>fields set"| DAG

    classDef notimpl fill:#fff3cd,stroke:#ffc107,stroke-dasharray: 5 5
    classDef existing fill:#d4edda,stroke:#28a745
    classDef envoy fill:#cce5ff,stroke:#004085
    classDef pipeline fill:#f0f0f0,stroke:#666
    classDef unused fill:#f8d7da,stroke:#dc3545,stroke-dasharray: 3 3
    class GWAPI_RI,GWAPI_BTP,GAP notimpl
    class HTTPPROXY existing
    class RINGHASH,HP envoy
    class HPP,DAG,XDS pipeline
    class NO_SSF unused"""

    if name == "Traefik":
        return """graph TB
    subgraph "User Configuration"
        GWAPI_RI["HTTPRoute Rule<br/><i>sessionPersistence</i><br/><b>(GEP-1619 route-inline)</b><br/><span style='color:orange'>PR #12537 — NOT MERGED</span>"]
        IR_CRD["IngressRoute / TraefikService<br/><i>sticky.cookie{name, secure,<br/>httpOnly, sameSite, maxAge,<br/>path, domain}</i>"]
        GWAPI_BTP["XBackendTrafficPolicy<br/><i>sessionPersistence</i><br/><span style='color:orange'>PR #12537 — NOT MERGED</span>"]
    end

    subgraph "Traefik Gateway API Provider"
        CONV["convertSessionPersistence()<br/><i>sessionPersistence → dynamic.Sticky</i>"]
        RESOLVE["resolveSessionPersistence()<br/><i>route-inline > BTP precedence</i>"]
        APPLY["applyStickyToServersLoadBalancer()<br/><i>sets lb.Sticky</i>"]
        TSPRE["TraefikService<br/>precedence check<br/><i>(silently overrides)</i>"]
    end

    subgraph "Traefik Built-in Proxy (Go)"
        WRR["WRR Load Balancer<br/><i>wrr.ServeHTTP()</i>"]
        STICKY["Sticky Handler<br/><i>StickyHandler(req)<br/>WriteStickyResponse(rw)</i>"]
        COOKIE_RT["Cookie Mode<br/><i>SHA-256 hash of backend<br/>→ Set-Cookie header</i>"]
        HEADER_RT["Header Mode<br/><i>SHA-256 hash of backend<br/>→ response header</i>"]
    end

    GWAPI_RI -.->|"PR #12537"| CONV
    GWAPI_BTP -.->|"PR #12537"| CONV
    CONV --> RESOLVE
    RESOLVE --> APPLY
    APPLY --> WRR

    IR_CRD -->|"native path"| TSPRE
    TSPRE -->|"overrides GWAPI"| APPLY

    WRR --> STICKY
    STICKY --> COOKIE_RT
    STICKY --> HEADER_RT

    classDef notimpl fill:#fff3cd,stroke:#ffc107,stroke-dasharray: 5 5
    classDef existing fill:#d4edda,stroke:#28a745
    classDef runtime fill:#cce5ff,stroke:#004085
    classDef pipeline fill:#f0f0f0,stroke:#666
    class GWAPI_RI,GWAPI_BTP notimpl
    class IR_CRD existing
    class WRR,STICKY,COOKIE_RT,HEADER_RT runtime
    class CONV,RESOLVE,APPLY,TSPRE pipeline"""

    if name == "kgateway":
        return """graph TB
    subgraph "User Configuration"
        GWAPI_RI["HTTPRoute / GRPCRoute Rule<br/><i>sessionPersistence</i><br/><b>(GEP-1619 route-inline)</b><br/><span style='color:green'>IMPLEMENTED</span>"]
        BCP["BackendConfigPolicy<br/><i>loadBalancer.ringHash / maglev</i><br/><b>(soft affinity)</b>"]
        GWAPI_BTP["BackendTrafficPolicy<br/><i>sessionPersistence</i><br/><b>(GEP-1619 BTP — NOT YET)</b>"]
    end

    subgraph "kgateway Controller (Plugin Architecture)"
        BUILTIN["Builtin Plugin<br/><i>convertSessionPersistence()</i><br/><code>pkg/krtcollections/builtin.go</code>"]
        BCPPLUGIN["BackendConfigPolicy Plugin<br/><i>translateLoadBalancerConfig()</i><br/><code>plugins/backendconfigpolicy/lb.go</code>"]
        SANITIZE["SanitizeCookieName()<br/>SanitizeHeaderName()"]
    end

    subgraph "Envoy Configuration"
        subgraph "Listener (HCM)"
            SSF["http_filters[]<br/>.stateful_session<br/><b>(disabled by default)</b>"]
        end
        subgraph "Route"
            TPFC["typed_per_filter_config<br/>.stateful_session<br/><i>CookieBasedSessionState<br/>or HeaderBasedSessionState</i>"]
        end
        subgraph "Cluster"
            LBPOLICY["load_balancing_policy<br/><i>RingHash / Maglev<br/>with HashPolicy[]</i>"]
        end
    end

    GWAPI_RI -->|"reads directly"| BUILTIN
    BUILTIN --> SANITIZE
    BUILTIN --> SSF
    BUILTIN --> TPFC
    SANITIZE -->|"sanitized name"| TPFC

    BCP -->|"soft affinity"| BCPPLUGIN
    BCPPLUGIN --> LBPOLICY

    GWAPI_BTP -.->|"NOT YET<br/>IMPLEMENTED"| BUILTIN

    classDef notimpl fill:#fff3cd,stroke:#ffc107,stroke-dasharray: 5 5
    classDef existing fill:#d4edda,stroke:#28a745
    classDef envoy fill:#cce5ff,stroke:#004085
    classDef pipeline fill:#f0f0f0,stroke:#666
    class GWAPI_BTP notimpl
    class GWAPI_RI,BCP existing
    class SSF,TPFC,LBPOLICY envoy
    class BUILTIN,BCPPLUGIN,SANITIZE pipeline"""

    if name == "Envoy":
        return """graph TB
    subgraph "ConsistentHashLB (Soft Affinity)"
        direction TB
        HP["RouteAction.hash_policy[]<br/><i>cookie{name,path,ttl,attributes[]}<br/>header{header_name}<br/>connection_properties{source_ip}<br/>query_parameter{name}<br/>filter_state{key}</i>"]
        LB["Cluster.lb_policy<br/><i>RING_HASH or MAGLEV</i>"]
        RING["ring_hash_lb_config<br/><i>min/max_ring_size<br/>hash_function</i>"]
        MAG["maglev_lb_config<br/><i>table_size (prime)</i>"]
        HP -->|"per-route<br/>what to hash"| LB
        LB --> RING
        LB --> MAG
    end

    subgraph "Stateful Session Filter (Strong Persistence)"
        direction TB
        SSF["HCM http_filters[]<br/><i>stateful_session<br/>(disabled by default)</i>"]
        SSPR["Route.typed_per_filter_config<br/><i>StatefulSessionPerRoute</i>"]
        COOKIE["CookieBasedSessionState<br/><i>cookie{name, path, ttl,<br/>attributes[]}</i>"]
        HEADER["HeaderBasedSessionState<br/><i>header{name}</i>"]
        STRICT["strict: true/false<br/><i>503 vs fallback to LB</i>"]
        SSF -->|"per-route<br/>override"| SSPR
        SSPR --> COOKIE
        SSPR --> HEADER
        SSPR --> STRICT
    end

    subgraph "Cookie Capabilities (shared)"
        COOKIEATTR["CookieAttribute{name, value}<br/><i>Generic — supports:<br/>Domain, Secure, HttpOnly,<br/>SameSite, etc.</i>"]
    end

    HP -.->|"cookie attrs"| COOKIEATTR
    COOKIE -.->|"cookie attrs"| COOKIEATTR

    classDef hash fill:#fff3e0,stroke:#ff9800,stroke-width:2px
    classDef stateful fill:#e8f5e9,stroke:#2e7d32,stroke-width:2px
    classDef shared fill:#e3f2fd,stroke:#2196f3,stroke-width:2px
    class HP,LB,RING,MAG hash
    class SSF,SSPR,COOKIE,HEADER,STRICT stateful
    class COOKIEATTR shared"""

    # Generic fallback
    return """graph TB
    A["User Config"] --> B["Controller"]
    B --> C["Dataplane"]"""


def render_index(impls: list[dict]) -> str:
    # Split into implementations and dataplanes
    impl_list = [i for i in impls if i["metadata"].get("type") != "dataplane_only"]
    dp_list = [i for i in impls if i["metadata"].get("type") == "dataplane_only"]

    rows = ""
    for impl in impl_list:
        m = impl["metadata"]
        ga = impl.get("gwapi_assessment", {})
        ri = ga.get("route_inline", {})
        btp = ga.get("backend_traffic_policy", {})
        np = impl.get("native_profile", {})
        mechs = ", ".join(mech["name"] for mech in np.get("mechanisms", []))

        rows += f"""<tr>
            <td><a href="{impl['_filename']}.html">{m['name']}</a></td>
            <td>{m.get('type','')}</td>
            <td>{m.get('dataplane','')}</td>
            <td>{mechs}</td>
            <td>{difficulty_badge(ri.get('difficulty',''))}</td>
            <td>{difficulty_badge(btp.get('difficulty',''))}</td>
            <td>{difficulty_badge(ga.get('overall_difficulty',''))}</td>
        </tr>"""

    dp_rows = ""
    for impl in dp_list:
        m = impl["metadata"]
        np = impl.get("native_profile", {})
        mechs = ", ".join(mech["name"] for mech in np.get("mechanisms", []))
        caps = np.get("capabilities", {})
        downstream = impl.get("downstream_implementations", [])
        ds_names = ", ".join(d.get("name", "") for d in downstream)

        dp_rows += f"""<tr>
            <td><a href="{impl['_filename']}.html">{m['name']}</a></td>
            <td>{mechs}</td>
            <td>{bool_icon(caps.get('cookie_persistence'))}</td>
            <td>{bool_icon(caps.get('header_persistence'))}</td>
            <td>{bool_icon(caps.get('source_ip_affinity'))}</td>
            <td>{ds_names or '—'}</td>
        </tr>"""

    ref_diagram = render_reference_model_diagram()

    return f"""
    <div class="content-wrapper">
        <h2>Reference Model: Gateway API Ingress Architecture</h2>
        <p>This is the standard ingress model used across all implementation reports.
           Every implementation's attachment diagram uses these same layers for comparison.</p>
    </div>
    <div class="mermaid-container">
        <pre class="mermaid">{ref_diagram}</pre>
    </div>
    <div class="content-wrapper">
        <div class="legend">
            <p><strong>Legend:</strong>
                <span style="color:#3f51b5">Blue boxes</span> = standard ingress layers |
                <span style="color:#ff9800">Orange dashed</span> = GEP-1619 route-inline attachment |
                <span style="color:#2196f3">Blue dashed</span> = GEP-1619 BackendTrafficPolicy attachment |
                <span style="color:#28a745">Green solid</span> / <span style="color:#6f42c1">Purple solid</span> = native implementation mechanisms
            </p>
        </div>

        <h2>Implementation Comparison</h2>
        <table class="comparison-table">
            <thead>
                <tr>
                    <th>Implementation</th>
                    <th>Type</th>
                    <th>Dataplane</th>
                    <th>Native Mechanisms</th>
                    <th>Route-Inline Difficulty</th>
                    <th>BTP Difficulty</th>
                    <th>Overall</th>
                </tr>
            </thead>
            <tbody>{rows}</tbody>
        </table>

        {'<h2>Dataplane Comparison</h2><p>Dataplanes do not implement Gateway API directly, but their APIs determine what session persistence capabilities are available to controllers.</p><table class="comparison-table"><thead><tr><th>Dataplane</th><th>Mechanisms</th><th>Cookie</th><th>Header</th><th>Source IP</th><th>Used By</th></tr></thead><tbody>' + dp_rows + '</tbody></table>' if dp_rows else ''}
    </div>"""



def load_ecosystem(data_dir: Path) -> dict:
    eco_file = data_dir / "ecosystem.yaml"
    if not eco_file.exists():
        return {}
    with open(eco_file) as fh:
        return yaml.safe_load(fh) or {}


def render_ecosystem_page(ecosystem: dict) -> str:
    defs = ecosystem.get("definitions", {})
    impls = ecosystem.get("implementations", [])

    # Group by dataplane for the main diagram
    dataplane_groups: dict[str, list[dict]] = {}
    for impl in impls:
        dp = impl.get("dataplane", "Unknown")
        if dp == "self":
            dp = impl["name"]  # dataplane-only entries use their own name
        dataplane_groups.setdefault(dp, []).append(impl)

    # Group by type for the architecture type diagram
    type_groups: dict[str, list[dict]] = {}
    for impl in impls:
        t = impl.get("type", "unknown")
        type_groups.setdefault(t, []).append(impl)

    # --- Mermaid: Dataplane grouping diagram ---
    # Group controllers by their dataplane
    dp_diagram_lines = ["graph LR"]

    # Envoy ecosystem
    envoy_impls = [i for i in impls if i.get("dataplane") == "Envoy" and i["type"] == "controller"]
    dp_diagram_lines.append('    subgraph ENVOY_GROUP["Envoy Dataplane"]')
    dp_diagram_lines.append('        ENVOY_DP["Envoy"]')
    for i, impl in enumerate(envoy_impls):
        node_id = f"ENV_{i}"
        sp = "SP" if impl.get("has_session_persistence") else "noSP"
        dp_diagram_lines.append(f'        {node_id}["{impl["name"]}"]')
    dp_diagram_lines.append('    end')
    for i, impl in enumerate(envoy_impls):
        dp_diagram_lines.append(f'    ENV_{i} -->|"programs"| ENVOY_DP')

    # Nginx ecosystem
    nginx_impls = [i for i in impls if i.get("dataplane") == "Nginx" and i["type"] == "controller"]
    dp_diagram_lines.append('    subgraph NGINX_GROUP["Nginx Dataplane"]')
    dp_diagram_lines.append('        NGINX_DP["Nginx"]')
    for i, impl in enumerate(nginx_impls):
        node_id = f"NGX_{i}"
        dp_diagram_lines.append(f'        {node_id}["{impl["name"]}"]')
    dp_diagram_lines.append('    end')
    for i, impl in enumerate(nginx_impls):
        dp_diagram_lines.append(f'    NGX_{i} -->|"programs"| NGINX_DP')

    # HAProxy ecosystem
    haproxy_impls = [i for i in impls if i.get("dataplane") == "HAProxy" and i["type"] == "controller"]
    dp_diagram_lines.append('    subgraph HAPROXY_GROUP["HAProxy Dataplane"]')
    dp_diagram_lines.append('        HAPROXY_DP["HAProxy"]')
    for i, impl in enumerate(haproxy_impls):
        node_id = f"HAP_{i}"
        dp_diagram_lines.append(f'        {node_id}["{impl["name"]}"]')
    dp_diagram_lines.append('    end')
    for i, impl in enumerate(haproxy_impls):
        dp_diagram_lines.append(f'    HAP_{i} -->|"programs"| HAPROXY_DP')

    # Other controller dataplanes
    other_ctrl = [i for i in impls if i["type"] == "controller"
                  and i.get("dataplane") not in ("Envoy", "Nginx", "HAProxy")]
    if other_ctrl:
        dp_diagram_lines.append('    subgraph OTHER_GROUP["Other Dataplanes"]')
        for i, impl in enumerate(other_ctrl):
            dp_diagram_lines.append(f'        OTH_{i}["{impl["name"]}<br/><i>({impl.get("dataplane", "?")})</i>"]')
        dp_diagram_lines.append('    end')

    # Integrated
    integrated = [i for i in impls if i["type"] == "integrated"]
    dp_diagram_lines.append('    subgraph INTEGRATED_GROUP["Integrated (Built-in Dataplane)"]')
    for i, impl in enumerate(integrated):
        dp_label = impl.get("dataplane", "Built-in")
        dp_diagram_lines.append(f'        INT_{i}["{impl["name"]}<br/><i>{dp_label}</i>"]')
    dp_diagram_lines.append('    end')

    # Cloud managed
    cloud = [i for i in impls if i["type"] == "cloud_managed"]
    dp_diagram_lines.append('    subgraph CLOUD_GROUP["Cloud Managed"]')
    for i, impl in enumerate(cloud):
        dp_diagram_lines.append(f'        CLD_{i}["{impl["name"]}<br/><i>{impl.get("dataplane", "?")}</i>"]')
    dp_diagram_lines.append('    end')

    # Styling
    dp_diagram_lines.append('    classDef envoyNode fill:#cce5ff,stroke:#004085,stroke-width:2px')
    dp_diagram_lines.append('    classDef nginxNode fill:#d4edda,stroke:#155724,stroke-width:2px')
    dp_diagram_lines.append('    classDef haproxyNode fill:#e8dff5,stroke:#6f42c1,stroke-width:2px')
    dp_diagram_lines.append('    classDef integratedNode fill:#fff3cd,stroke:#856404,stroke-width:2px')
    dp_diagram_lines.append('    classDef cloudNode fill:#fce4ec,stroke:#c62828,stroke-width:2px')
    dp_diagram_lines.append('    classDef dataplaneNode fill:#1a1a2e,color:#fff,stroke:#1a1a2e,stroke-width:3px')

    env_ids = ",".join(f"ENV_{i}" for i in range(len(envoy_impls)))
    ngx_ids = ",".join(f"NGX_{i}" for i in range(len(nginx_impls)))
    hap_ids = ",".join(f"HAP_{i}" for i in range(len(haproxy_impls)))
    int_ids = ",".join(f"INT_{i}" for i in range(len(integrated)))
    cld_ids = ",".join(f"CLD_{i}" for i in range(len(cloud)))

    if env_ids:
        dp_diagram_lines.append(f'    class {env_ids} envoyNode')
    if ngx_ids:
        dp_diagram_lines.append(f'    class {ngx_ids} nginxNode')
    if hap_ids:
        dp_diagram_lines.append(f'    class {hap_ids} haproxyNode')
    if int_ids:
        dp_diagram_lines.append(f'    class {int_ids} integratedNode')
    if cld_ids:
        dp_diagram_lines.append(f'    class {cld_ids} cloudNode')
    dp_diagram_lines.append('    class ENVOY_DP,NGINX_DP,HAPROXY_DP dataplaneNode')

    dp_diagram = "\n".join(dp_diagram_lines)

    # --- Definitions section ---
    defs_html = ""
    for key in ["controller", "integrated", "cloud_managed", "dataplane_only"]:
        d = defs.get(key, {})
        defs_html += f"""
        <div class="def-card">
            <h4>{d.get('title', key)}</h4>
            <p>{d.get('description', '').strip()}</p>
            <p class="def-examples"><strong>Examples:</strong> {d.get('examples', '')}</p>
        </div>"""

    # --- Translation models section ---
    trans_models = ecosystem.get("translation_models", {})
    trans_html = ""
    for key in ["direct", "intermediate"]:
        tm = trans_models.get(key, {})
        if tm:
            trans_html += f"""
        <div class="def-card" id="translation-{key}">
            <h4>{tm.get('title', key)}</h4>
            <p>{tm.get('description', '').strip()}</p>
            <p class="def-examples"><strong>Examples:</strong> {tm.get('examples', '')}</p>
            <p class="def-implications"><strong>GEP-1619 implications:</strong> {tm.get('implications', '').strip()}</p>
        </div>"""

    return f"""
    <div class="content-wrapper">
        <h2>Architecture Types</h2>
        <p>Gateway API implementations fall into four categories based on how they relate to their dataplane:</p>
        <div class="def-grid">
            {defs_html}
        </div>

        <h2 id="translation-models">Translation Models</h2>
        <p>How does Gateway API session persistence config reach the dataplane? Implementations
           use one of two translation models:</p>
    </div>
    <div class="mermaid-container">
        <pre class="mermaid">graph LR
    subgraph DIRECT["Direct Translation"]
        direction LR
        D_GWAPI["Gateway API<br/><code>sessionPersistence</code>"] -->|"generates"| D_DP["Dataplane Config<br/><i>e.g. nginx.conf,<br/>Envoy xDS</i>"]
    end

    subgraph INTERMEDIATE["Intermediate API Translation"]
        direction LR
        I_GWAPI["Gateway API<br/><code>sessionPersistence</code>"] -->|"maps to"| I_NATIVE["Intermediate API<br/><i>e.g. DestinationRule,<br/>HTTPProxy</i>"] -->|"generates"| I_DP["Dataplane Config<br/><i>e.g. Envoy xDS</i>"]
        I_GWAPI -.->|"or bypasses"| I_DP
    end

    classDef gwapi fill:#fff3e0,stroke:#ff9800,stroke-width:2px
    classDef native fill:#fce4ec,stroke:#e91e63,stroke-width:2px
    classDef dp fill:#e3f2fd,stroke:#2196f3,stroke-width:2px
    class D_GWAPI,I_GWAPI gwapi
    class I_NATIVE native
    class D_DP,I_DP dp</pre>
    </div>
    <div class="content-wrapper">
        <div class="def-grid">
            {trans_html}
        </div>

        <h2>Dataplane Ecosystem Map</h2>
        <p>Which implementations share a dataplane? Controllers that share a dataplane often share
           session persistence capabilities and limitations at the proxy level.</p>
    </div>
    <div class="mermaid-container">
        <pre class="mermaid">{dp_diagram}</pre>
    </div>
    <div class="content-wrapper">
        <div class="legend">
            <p><strong>Legend:</strong>
                <span style="color:#004085">Blue</span> = Envoy-based |
                <span style="color:#155724">Green</span> = Nginx-based |
                <span style="color:#6f42c1">Purple</span> = HAProxy-based |
                <span style="color:#856404">Yellow</span> = Integrated (built-in) |
                <span style="color:#c62828">Pink</span> = Cloud managed
            </p>
        </div>

    </div>"""


def wrap_html(title: str, content: str, nav_links: list[tuple[str, str]],
              impl_links: list[tuple[str, str]] = None,
              dataplane_links: list[tuple[str, str]] = None) -> str:
    nav_items = "".join(f'<a href="{url}">{label}</a>' for label, url in nav_links)

    # Build dropdowns
    def _build_dropdown(label, links):
        if not links:
            return ""
        items = "".join(f'<a href="{url}">{name}</a>' for name, url in links)
        return f"""
        <div class="nav-dropdown">
            <button class="nav-dropdown-btn">{label} &#9662;</button>
            <div class="nav-dropdown-content">
                {items}
            </div>
        </div>"""

    tech_dropdown = _build_dropdown("Implementations", impl_links)
    tech_dropdown += _build_dropdown("Dataplanes", dataplane_links)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{title} — GEP-1619 Implementation Analysis</title>
    <script src="https://cdn.jsdelivr.net/npm/mermaid/dist/mermaid.min.js"></script>
    <script>mermaid.initialize({{startOnLoad: true, theme: 'default', flowchart: {{useMaxWidth: false}}}});</script>
    <style>
        :root {{
            --bg: #ffffff;
            --fg: #1a1a2e;
            --accent: #0066cc;
            --border: #e0e0e0;
            --section-bg: #f8f9fa;
            --code-bg: #f0f0f0;
        }}
        * {{ box-sizing: border-box; margin: 0; padding: 0; }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            color: var(--fg);
            background: var(--bg);
            line-height: 1.6;
            margin: 0;
            padding: 0;
        }}
        nav {{
            background: var(--fg);
            padding: 12px 20px;
            border-radius: 8px;
            margin-bottom: 24px;
            display: flex;
            gap: 4px;
            align-items: center;
        }}
        nav > a {{
            color: #fff;
            text-decoration: none;
            font-size: 14px;
            padding: 6px 12px;
            border-radius: 4px;
        }}
        nav > a:hover {{ background: rgba(255,255,255,0.15); }}
        .nav-dropdown {{
            position: relative;
        }}
        .nav-dropdown-btn {{
            background: none;
            border: none;
            color: #fff;
            font-size: 14px;
            padding: 6px 12px;
            border-radius: 4px;
            cursor: pointer;
            font-family: inherit;
        }}
        .nav-dropdown-btn:hover {{ background: rgba(255,255,255,0.15); }}
        .nav-dropdown-content {{
            display: none;
            position: absolute;
            top: 100%;
            left: 0;
            background: var(--fg);
            border-radius: 6px;
            min-width: 220px;
            box-shadow: 0 8px 24px rgba(0,0,0,0.3);
            z-index: 100;
            padding: 6px 0;
            margin-top: 4px;
        }}
        .nav-dropdown:hover .nav-dropdown-content {{ display: block; }}
        .nav-dropdown-content a {{
            display: block;
            color: #fff;
            text-decoration: none;
            padding: 8px 16px;
            font-size: 14px;
        }}
        .nav-dropdown-content a:hover {{ background: rgba(255,255,255,0.15); }}
        .page-title {{
            font-size: 28px;
            margin-bottom: 8px;
            color: var(--fg);
        }}
        .page-subtitle {{
            color: #666;
            font-size: 14px;
            margin-bottom: 24px;
        }}
        .impl-header {{
            background: var(--section-bg);
            padding: 20px;
            border-radius: 8px;
            margin-bottom: 24px;
            border-left: 4px solid var(--accent);
        }}
        .impl-meta {{
            display: flex;
            flex-wrap: wrap;
            gap: 16px;
            margin-top: 8px;
            font-size: 14px;
        }}
        .overall-rating {{
            margin-top: 12px;
            font-size: 16px;
        }}
        .section-group {{
            margin-bottom: 32px;
        }}
        .section-title {{
            font-size: 20px;
            padding: 12px 0;
            border-bottom: 2px solid var(--accent);
            margin-bottom: 16px;
        }}
        .gwapi-section .section-title {{
            border-color: #fd7e14;
        }}
        details {{
            margin-bottom: 16px;
            border: 1px solid var(--border);
            border-radius: 6px;
            overflow: hidden;
        }}
        details > summary {{
            padding: 12px 16px;
            background: var(--section-bg);
            cursor: pointer;
            list-style: none;
        }}
        details > summary::-webkit-details-marker {{ display: none; }}
        details > summary::before {{
            content: "▶ ";
            font-size: 12px;
            margin-right: 8px;
        }}
        details[open] > summary::before {{ content: "▼ "; }}
        details > summary h4 {{ display: inline; font-size: 16px; }}
        details > :not(summary) {{ padding: 0 16px; }}
        details > *:last-child {{ margin-bottom: 16px; }}
        .mechanism {{
            border: 1px solid var(--border);
            border-radius: 6px;
            padding: 16px;
            margin: 12px 0;
        }}
        .mechanism.soft {{ border-left: 4px solid #ffc107; }}
        .mechanism.strong {{ border-left: 4px solid #28a745; }}
        .badge {{
            display: inline-block;
            padding: 2px 10px;
            border-radius: 12px;
            font-size: 12px;
            font-weight: 600;
            color: #fff;
            vertical-align: middle;
        }}
        .badge-soft {{ background: #ffc107; color: #333; }}
        .badge-strong {{ background: #28a745; }}
        table {{
            width: 100%;
            border-collapse: collapse;
            margin: 12px 0;
            font-size: 14px;
        }}
        th, td {{
            padding: 8px 12px;
            text-align: left;
            border: 1px solid var(--border);
        }}
        th {{ background: var(--section-bg); font-weight: 600; }}
        .caps-table {{ max-width: 400px; }}
        .caps-table td:last-child {{ text-align: center; font-size: 18px; }}
        .capability-matrix td.cap-cell {{ text-align: center; font-size: 18px; }}
        .comparison-table {{ font-size: 14px; }}
        code {{
            background: var(--code-bg);
            padding: 2px 6px;
            border-radius: 3px;
            font-size: 13px;
        }}
        pre {{
            background: var(--code-bg);
            padding: 16px;
            border-radius: 6px;
            overflow-x: auto;
            font-size: 13px;
            margin: 12px 0;
        }}
        pre.mermaid {{
            background: #fff;
            text-align: center;
        }}
        .overview-text, .assessment-text, .strategy-text, .summary-text {{
            white-space: pre-wrap;
            line-height: 1.7;
        }}
        ul {{ padding-left: 24px; margin: 8px 0; }}
        li {{ margin: 4px 0; }}
        a {{ color: var(--accent); }}
        .attachment-info p {{ margin: 4px 0; }}
        .content-wrapper {{
            max-width: 1400px;
            margin: 0 auto;
            padding: 0 20px;
        }}
        .mermaid-container {{
            margin: 16px 0;
            overflow-x: auto;
            padding: 16px 20px;
            width: 100vw;
            margin-left: calc(-1 * (100vw - 100%) / 2);
            box-sizing: border-box;
        }}
        .mermaid-container .mermaid svg {{
            font-size: 14px;
        }}
        .flow-text {{ white-space: pre-wrap; }}
        .def-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
            gap: 16px;
            margin: 16px 0;
        }}
        .def-card {{
            border: 1px solid var(--border);
            border-radius: 8px;
            padding: 16px;
            background: var(--section-bg);
        }}
        .def-card h4 {{ margin-bottom: 8px; color: var(--accent); }}
        .def-examples {{ font-size: 13px; color: #666; margin-top: 8px; }}
        .def-implications {{ font-size: 13px; color: #4a148c; margin-top: 8px; font-style: italic; }}
        .legend {{
            background: var(--section-bg);
            padding: 12px 16px;
            border-radius: 6px;
            margin: 12px 0 24px;
            font-size: 14px;
        }}
        .note {{ color: #888; font-size: 12px; }}
        .api-stack-section {{
            margin: 16px 0;
            padding: 16px;
            background: var(--section-bg);
            border-radius: 8px;
            border-left: 4px solid #9c27b0;
        }}
        .api-stack-section h3 {{
            margin-bottom: 8px;
            color: #6a1b9a;
        }}

        /* API Support page styles */
        .score-hero {{
            display: flex;
            align-items: center;
            gap: 32px;
            padding: 32px;
            background: var(--section-bg);
            border-radius: 12px;
            margin-bottom: 32px;
        }}
        .score-circle {{
            width: 140px;
            height: 140px;
            border-radius: 50%;
            border: 6px solid;
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
            flex-shrink: 0;
        }}
        .score-number {{
            font-size: 42px;
            font-weight: 700;
            line-height: 1;
        }}
        .score-label {{
            font-size: 11px;
            text-transform: uppercase;
            letter-spacing: 1px;
            color: #666;
            margin-top: 4px;
        }}
        .score-summary h3 {{ margin-bottom: 8px; }}
        .score-summary p {{ color: #555; font-size: 14px; }}
        .score-legend {{
            display: flex;
            gap: 16px;
            margin-top: 12px;
            flex-wrap: wrap;
        }}
        .legend-item {{
            display: flex;
            align-items: center;
            gap: 6px;
            font-size: 13px;
            color: #555;
        }}
        .legend-dot {{
            width: 10px;
            height: 10px;
            border-radius: 50%;
            display: inline-block;
        }}
        .status-cards {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 16px;
            margin: 16px 0 32px;
        }}
        .status-card {{
            background: var(--section-bg);
            border-radius: 8px;
            padding: 16px;
        }}
        .status-card-header {{
            display: flex;
            align-items: baseline;
            gap: 8px;
        }}
        .status-card-count {{
            font-size: 32px;
            font-weight: 700;
        }}
        .status-card-label {{
            font-size: 16px;
            font-weight: 600;
        }}
        .status-card-subtitle {{
            font-size: 12px;
            color: #888;
            margin-top: 2px;
        }}
        .status-card-names {{
            font-size: 13px;
            color: #555;
            margin-top: 8px;
        }}
        .ap-status-columns {{
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 32px;
            margin-bottom: 32px;
        }}
        .ap-status-column h3 {{
            margin-bottom: 12px;
        }}
        @media (max-width: 800px) {{
            .ap-status-columns {{
                grid-template-columns: 1fr;
            }}
        }}
        .field-scores {{
            margin: 16px 0 32px;
        }}
        .field-score-row {{
            display: flex;
            align-items: center;
            gap: 16px;
            padding: 10px 0;
            border-bottom: 1px solid var(--border);
        }}
        .field-score-label {{
            width: 280px;
            flex-shrink: 0;
        }}
        .field-score-label code {{
            font-size: 13px;
        }}
        .field-desc {{
            display: block;
            font-size: 11px;
            color: #888;
            margin-top: 2px;
        }}
        .field-score-bar-container {{
            flex: 1;
            background: #eee;
            border-radius: 6px;
            height: 22px;
            position: relative;
            min-width: 120px;
        }}
        .field-score-bar {{
            height: 100%;
            border-radius: 6px;
            transition: width 0.3s;
        }}
        .field-score-pct {{
            position: absolute;
            right: 8px;
            top: 2px;
            font-size: 12px;
            font-weight: 600;
            color: #333;
        }}
        .field-score-dots {{
            display: flex;
            gap: 4px;
            flex-shrink: 0;
        }}
        .field-dot {{
            width: 14px;
            height: 14px;
            border-radius: 50%;
            cursor: default;
            border: 1px solid rgba(0,0,0,0.1);
        }}

        /* Capability matrix — dataplane vs implementation columns */
        .cap-group-impl {{
            background: var(--section-bg);
            text-align: center;
            border-bottom: 2px solid var(--accent);
        }}
        .cap-group-dp {{
            background: #e3f2fd;
            text-align: center;
            border-bottom: 2px solid #1565c0;
            color: #0d47a1;
        }}
        .cap-impl-name {{
            background: var(--section-bg);
        }}
        .cap-dp-name {{
            background: #e3f2fd;
            color: #0d47a1;
        }}
        .cap-dp-cell {{
            background: #f5f9ff;
        }}
        .cap-group-separator td {{
            background: var(--section-bg);
            font-size: 13px;
            padding: 6px 12px;
            border-top: 2px solid var(--border);
        }}
    </style>
</head>
<body>
    <div class="content-wrapper">
        <nav>{nav_items}{tech_dropdown}</nav>
        <h1 class="page-title">{title}</h1>
        <p class="page-subtitle">Gateway API Implementation Survey — Session Persistence (GEP-1619)</p>
    </div>
    {content}
</body>
</html>"""


def render_api_support_page(impls: list[dict], ecosystem: dict) -> str:
    """Render a page showing session persistence API field-level implementability."""
    # Only include implementations (not dataplanes) for scoring
    impl_list = [i for i in impls if i["metadata"].get("type") != "dataplane_only"]

    # Define the canonical GEP-1619 API fields to score
    api_fields = [
        ("type: Cookie", "type: Cookie", "Session persistence using cookies"),
        ("type: Header", "type: Header", "Session persistence using headers"),
        ("sessionName", "sessionName", "User-specified session name (cookie/header name)"),
        ("absoluteTimeout", "absoluteTimeout", "Maximum session lifetime (maps to cookie TTL)"),
        ("idleTimeout", "idleTimeout", "Session expires after inactivity period"),
        ("cookieConfig.lifetimeType: Session", "cookie.lifetimeType: Session", "Session cookie (no expiry, cleared on browser close)"),
        ("cookieConfig.lifetimeType: Permanent", "cookie.lifetimeType: Permanent", "Persistent cookie with expiry from absoluteTimeout"),
        ("cookie path (computed)", "cookie path (computed)", "Cookie path derived from route path matches"),
    ]

    # Score each field across implementations
    # Map gwapi_field values to their canonical key (handle slight naming variations)
    def normalize_field(f):
        f = f.lower().strip()
        for key, _, _ in api_fields:
            if key.lower() in f or f in key.lower():
                return key
        return f

    # Difficulty scoring: direct=3, translation_needed=2, not_supported=0, no_native_equivalent=0
    diff_scores = {"direct": 3, "translation_needed": 2, "not_supported": 0, "no_native_equivalent": 0}
    diff_colors = {"direct": "#28a745", "translation_needed": "#ffc107", "not_supported": "#dc3545", "no_native_equivalent": "#6c757d"}
    diff_labels = {"direct": "Direct", "translation_needed": "Needs Translation", "not_supported": "Not Supported", "no_native_equivalent": "No Equivalent"}

    # Build per-field, per-implementation matrix
    field_data = {}
    for key, _, _ in api_fields:
        field_data[key] = {}

    for impl in impl_list:
        name = impl["metadata"]["name"]
        ga = impl.get("gwapi_assessment", {})
        for fm in ga.get("field_mapping", []):
            gwapi_field = fm.get("gwapi_field", "")
            difficulty = fm.get("mapping_difficulty", "")
            # Match to canonical field
            matched = None
            for key, _, _ in api_fields:
                if key.lower() in gwapi_field.lower() or gwapi_field.lower() in key.lower():
                    matched = key
                    break
            if matched and matched in field_data:
                field_data[matched][name] = difficulty

    # Compute implementation status for both attachment points
    def compute_ap_status(ap_key):
        status = {}
        for impl in impl_list:
            name = impl["metadata"]["name"]
            ga = impl.get("gwapi_assessment", {})
            ap = ga.get(ap_key, {})
            difficulty = ap.get("difficulty", "")
            if difficulty:
                status[name] = difficulty
        return status

    ri_status = compute_ap_status("route_inline")
    btp_status = compute_ap_status("backend_traffic_policy")

    def compute_score(status_dict):
        total = len(status_dict)
        if total == 0:
            return 0, "#6c757d"
        score_points = sum(
            100 if d == "trivial" else 60 if d == "moderate" else 20
            for d in status_dict.values()
        )
        score = round(score_points / total)
        if score >= 70:
            color = "#28a745"
        elif score >= 40:
            color = "#ffc107"
        else:
            color = "#dc3545"
        return score, color

    ri_score, ri_score_color = compute_score(ri_status)
    btp_score, btp_score_color = compute_score(btp_status)
    total = len(impl_list)

    # Per-field scores
    field_scores_html = ""
    for key, display, desc in api_fields:
        mappings = field_data.get(key, {})
        if not mappings:
            pct = 0
        else:
            total_score = sum(diff_scores.get(d, 0) for d in mappings.values())
            pct = round((total_score / (len(mappings) * 3)) * 100) if mappings else 0

        if pct >= 70:
            bar_color = "#28a745"
        elif pct >= 40:
            bar_color = "#ffc107"
        else:
            bar_color = "#dc3545"

        # Per-impl dots
        dots = ""
        for impl in impl_list:
            name = impl["metadata"]["name"]
            d = mappings.get(name, "")
            if d:
                color = diff_colors.get(d, "#ccc")
                label = diff_labels.get(d, d)
                dots += f'<span class="field-dot" style="background:{color}" title="{name}: {label}"></span>'
            else:
                dots += f'<span class="field-dot" style="background:#e0e0e0" title="{name}: not analyzed"></span>'

        field_scores_html += f"""
        <div class="field-score-row">
            <div class="field-score-label">
                <code>{display}</code>
                <span class="field-desc">{desc}</span>
            </div>
            <div class="field-score-bar-container">
                <div class="field-score-bar" style="width:{pct}%;background:{bar_color}"></div>
                <span class="field-score-pct">{pct}%</span>
            </div>
            <div class="field-score-dots">{dots}</div>
        </div>"""

    # Implementation status cards — generate for both attachment points
    status_order = [("trivial", "Implemented", "#28a745", "Already shipping"),
                    ("moderate", "Moderate Effort", "#ffc107", "Feasible but non-trivial"),
                    ("significant", "Significant Effort", "#fd7e14", "Major work needed"),
                    ("requires_dataplane_changes", "Blocked", "#dc3545", "Dataplane changes needed")]

    def build_status_cards(status_dict):
        cards = ""
        for diff, label, color, subtitle in status_order:
            names = [n for n, d in status_dict.items() if d == diff]
            if not names:
                continue
            name_list = ", ".join(names)
            cards += f"""
            <div class="status-card" style="border-left:4px solid {color}">
                <div class="status-card-header">
                    <span class="status-card-count" style="color:{color}">{len(names)}</span>
                    <span class="status-card-label">{label}</span>
                </div>
                <div class="status-card-subtitle">{subtitle}</div>
                <div class="status-card-names">{name_list}</div>
            </div>"""
        return cards

    ri_status_cards = build_status_cards(ri_status)
    btp_status_cards = build_status_cards(btp_status)

    # Detailed field mapping table (the full matrix)
    matrix_headers = "<th>GEP-1619 Field</th>" + "".join(
        f"<th>{impl['metadata']['name']}</th>" for impl in impl_list
    )
    matrix_rows = ""
    for key, display, desc in api_fields:
        mappings = field_data.get(key, {})
        row = f"<td><code>{display}</code></td>"
        for impl in impl_list:
            name = impl["metadata"]["name"]
            d = mappings.get(name, "")
            if d:
                color = diff_colors.get(d, "#ccc")
                label = diff_labels.get(d, d)
                row += f'<td class="cap-cell"><span class="badge" style="background:{color}">{label}</span></td>'
            else:
                row += '<td class="cap-cell"><span style="color:#ccc">—</span></td>'
        matrix_rows += f"<tr>{row}</tr>"

    # Attachment point comparison
    attach_headers = "<th>Attachment Point</th>" + "".join(
        f"<th>{impl['metadata']['name']}</th>" for impl in impl_list
    )
    attach_rows = ""
    for ap_key, ap_label in [("route_inline", "Route-Inline"), ("backend_traffic_policy", "BackendTrafficPolicy")]:
        row = f"<td><strong>{ap_label}</strong></td>"
        for impl in impl_list:
            ga = impl.get("gwapi_assessment", {})
            ap = ga.get(ap_key, {})
            d = ap.get("difficulty", "")
            if d:
                colors = {"trivial": "#28a745", "moderate": "#ffc107", "significant": "#fd7e14", "requires_dataplane_changes": "#dc3545"}
                color = colors.get(d, "#6c757d")
                row += f'<td class="cap-cell">{difficulty_badge(d)}</td>'
            else:
                row += '<td class="cap-cell">—</td>'
        attach_rows += f"<tr>{row}</tr>"

    # Capability matrix — split implementations and dataplanes
    # (label, capability path, GEP-1619 field name or None)
    # GEP-1619 fields first, then other capabilities
    cap_fields = [
        ("Cookie Persistence", "cookie_persistence", "type: Cookie"),
        ("Header Persistence", "header_persistence", "type: Header"),
        ("Session Name", "cookie_attributes.name", "sessionName"),
        ("Absolute Timeout", "absolute_timeout", "absoluteTimeout"),
        ("Idle Timeout", "idle_timeout", "idleTimeout"),
        ("Cookie Lifetime: Session", "lifetime_type_session", "cookieConfig.lifetimeType: Session"),
        ("Cookie Lifetime: Permanent", "lifetime_type_permanent", "cookieConfig.lifetimeType: Permanent"),
        ("Cookie Path", "cookie_attributes.path", "cookie path (computed)"),
        ("Cookie TTL", "cookie_attributes.ttl", "absoluteTimeout (cookie TTL)"),
        # Other capabilities (not in GEP-1619)
        ("Source IP Affinity", "source_ip_affinity", None),
        ("Cookie Domain", "cookie_attributes.domain", None),
        ("Cookie Secure", "cookie_attributes.secure", None),
        ("Cookie HttpOnly", "cookie_attributes.http_only", None),
        ("Cookie SameSite", "cookie_attributes.same_site", None),
    ]
    dp_list = [i for i in impls if i["metadata"].get("type") == "dataplane_only"]
    n_impls = len(impl_list)
    n_dps = len(dp_list)

    # Header row with group spans
    n_data_cols = n_impls + n_dps
    cap_headers = f'<th rowspan="2">Capability</th>'
    cap_headers += f'<th rowspan="2">GEP-1619 Field</th>'
    cap_headers += f'<th colspan="{n_impls}" class="cap-group-impl">Implementations</th>' if n_impls else ''
    cap_headers += f'<th colspan="{n_dps}" class="cap-group-dp">Dataplanes</th>' if n_dps else ''
    cap_subheaders = "".join(
        f'<th class="cap-impl-name">{i["metadata"]["name"]}</th>' for i in impl_list
    ) + "".join(
        f'<th class="cap-dp-name">{i["metadata"]["name"]}</th>' for i in dp_list
    )

    # Order: implementations first, then dataplanes
    cap_ordered = list(impl_list) + list(dp_list)
    cap_rows = ""
    prev_had_field = True  # track group transitions
    for label, path, gwapi_field in cap_fields:
        # Insert separator row when transitioning from GEP-1619 fields to other capabilities
        if prev_had_field and gwapi_field is None:
            cap_rows += f'<tr class="cap-group-separator"><td colspan="{n_data_cols + 2}"><em>Additional Native Capabilities (not in GEP-1619)</em></td></tr>'
        prev_had_field = gwapi_field is not None

        field_cell = f"<td><code>{gwapi_field}</code></td>" if gwapi_field else "<td></td>"
        row = f"<td>{label}</td>{field_cell}"
        for impl in cap_ordered:
            is_dp = impl["metadata"].get("type") == "dataplane_only"
            caps = impl.get("native_profile", {}).get("capabilities", {})
            parts = path.split(".")
            val = caps
            for p in parts:
                if isinstance(val, dict):
                    val = val.get(p)
                else:
                    val = None
            td_class = "cap-cell cap-dp-cell" if is_dp else "cap-cell"
            row += f'<td class="{td_class}">{bool_icon(val)}</td>'
        cap_rows += f"<tr>{row}</tr>"

    return f"""
    <div class="content-wrapper">
        <!-- Overall Scores — Route-Inline vs BTP -->
        <div class="score-hero">
            <div class="score-circle" style="border-color:{ri_score_color}">
                <span class="score-number" style="color:{ri_score_color}">{ri_score}</span>
                <span class="score-label">Route-Inline</span>
            </div>
            <div class="score-circle" style="border-color:{btp_score_color}">
                <span class="score-number" style="color:{btp_score_color}">{btp_score}</span>
                <span class="score-label">BTP</span>
            </div>
            <div class="score-summary">
                <h3>GEP-1619 Session Persistence API</h3>
                <p>Implementability scores across {total} analyzed implementations.
                   Scoring: Implemented (trivial) = 100, Easy (moderate) = 60, Hard = 20.</p>
                <div class="score-legend">
                    <span class="legend-item"><span class="legend-dot" style="background:#28a745"></span> Direct / Implemented</span>
                    <span class="legend-item"><span class="legend-dot" style="background:#ffc107"></span> Needs translation / Moderate</span>
                    <span class="legend-item"><span class="legend-dot" style="background:#dc3545"></span> Not supported / Blocked</span>
                    <span class="legend-item"><span class="legend-dot" style="background:#6c757d"></span> No equivalent</span>
                </div>
            </div>
        </div>

        <!-- Implementation Status Cards — side by side -->
        <div class="ap-status-columns">
            <div class="ap-status-column">
                <h3>Route-Inline Status</h3>
                <div class="status-cards">
                    {ri_status_cards}
                </div>
            </div>
            <div class="ap-status-column">
                <h3>BackendTrafficPolicy Status</h3>
                <div class="status-cards">
                    {btp_status_cards}
                </div>
            </div>
        </div>

        <!-- Per-Field Implementability -->
        <h3>Field-Level Implementability</h3>
        <p>How easily each GEP-1619 API field maps to native mechanisms across implementations.
           Each dot represents one implementation; hover for details.</p>
        <div class="field-scores">
            {field_scores_html}
        </div>

        <!-- Attachment Point Comparison -->
        <h3>Attachment Point Difficulty</h3>
        <table class="comparison-table">
            <thead><tr>{attach_headers}</tr></thead>
            <tbody>{attach_rows}</tbody>
        </table>

        <!-- Detailed Field Mapping Matrix -->
        <h3>Detailed Field Mapping</h3>
        <p>How each GEP-1619 field maps in each implementation.</p>
        <table class="comparison-table">
            <thead><tr>{matrix_headers}</tr></thead>
            <tbody>{matrix_rows}</tbody>
        </table>

        <!-- Full Capability Matrix (all impls + dataplanes) -->
        <h3>Native Capability Matrix</h3>
        <p>What each implementation and dataplane natively supports, regardless of GEP-1619.</p>
        <table class="capability-matrix">
            <thead>
                <tr>{cap_headers}</tr>
                <tr>{cap_subheaders}</tr>
            </thead>
            <tbody>{cap_rows}</tbody>
        </table>
    </div>"""


def main():
    parser = argparse.ArgumentParser(description="Generate implementation analysis site")
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args()

    impls = load_implementations(args.data_dir)
    if not impls:
        print("No implementation YAML files found", file=sys.stderr)
        sys.exit(1)

    args.output_dir.mkdir(parents=True, exist_ok=True)

    # Load ecosystem data
    ecosystem = load_ecosystem(args.data_dir)

    # Build nav links — top-level pages only
    nav = [("Home", "index.html"), ("Ecosystem", "ecosystem.html"), ("API Support", "api-support.html")]

    # Split into implementations vs dataplanes for separate dropdowns
    impl_links = [
        (impl["metadata"]["name"], f"{impl['_filename']}.html")
        for impl in impls
        if impl["metadata"].get("type") != "dataplane_only"
    ]
    dataplane_links = [
        (impl["metadata"]["name"], f"{impl['_filename']}.html")
        for impl in impls
        if impl["metadata"].get("type") == "dataplane_only"
    ]

    def gen_html(title, content):
        return wrap_html(title, content, nav, impl_links, dataplane_links)

    # Generate index
    index_content = render_index(impls)
    (args.output_dir / "index.html").write_text(gen_html("Implementation Overview", index_content))
    print(f"Generated: index.html")

    # Generate ecosystem page
    if ecosystem:
        eco_content = render_ecosystem_page(ecosystem)
        (args.output_dir / "ecosystem.html").write_text(gen_html("Ecosystem Overview", eco_content))
        print(f"Generated: ecosystem.html")

    # Generate API support page
    api_support_content = render_api_support_page(impls, ecosystem)
    (args.output_dir / "api-support.html").write_text(gen_html("API Support", api_support_content))
    print(f"Generated: api-support.html")

    # Generate per-implementation pages
    for impl in impls:
        content = render_implementation(impl)
        outfile = args.output_dir / f"{impl['_filename']}.html"
        outfile.write_text(gen_html(impl["metadata"]["name"], content))
        print(f"Generated: {impl['_filename']}.html")

    print(f"\nSite generated in {args.output_dir}")
    print(f"Open: file://{args.output_dir.resolve()}/index.html")


if __name__ == "__main__":
    main()
