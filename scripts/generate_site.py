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

        # Build resource list for the node — strip backticks (invalid in Mermaid labels)
        cleaned = [r.replace("`", "") for r in resources[:4]]
        res_lines = "<br/>".join(cleaned)
        if len(resources) > 4:
            res_lines += f"<br/><i>+{len(resources)-4} more</i>"

        lines.append(f'    {node_id}["{layer["name"]}<br/><i>────────</i><br/>{res_lines}"]')
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

    def mermaid_safe(s):
        return s.replace("`", "").replace('"', "'")

    # Try envoy_concept first, fall back to haproxy_concept/nginx_concept, then generic
    def get_concept(layer, default):
        for key in ("envoy_concept", "haproxy_concept", "nginx_concept", "concept"):
            if key in layer:
                return mermaid_safe(layer[key])
        return default

    gw_concept = get_concept(gw, "Gateway")
    rr_concept = get_concept(rr, "Route")
    svc_concept = get_concept(svc, "Service")
    ep_concept = get_concept(ep, "Endpoint")

    # Session persistence config items
    gw_sp = gw.get("session_persistence_config", [])
    rr_sp = rr.get("session_persistence_config", [])
    svc_sp = svc.get("session_persistence_config", [])

    def sp_lines(items):
        if not items:
            return ""
        # Sanitize for Mermaid: strip chars that break Mermaid syntax
        cleaned = []
        for item in items[:3]:
            s = item.replace("`", "").replace('"', "'").replace("<", "‹").replace(">", "›").replace("|", "/")
            if len(s) > 60:
                s = s[:57] + "..."
            cleaned.append(s)
        return "<br/>".join(cleaned)

    gw_sp_html = f"<br/><i>────</i><br/>{sp_lines(gw_sp)}" if gw_sp else ""
    rr_sp_html = f"<br/><i>────</i><br/>{sp_lines(rr_sp)}" if rr_sp else ""
    svc_sp_html = f"<br/><i>────</i><br/>{sp_lines(svc_sp)}" if svc_sp else ""

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

    if name == "Amazon EKS":
        return """graph TB
    subgraph "User Configuration"
        GWAPI_RI["HTTPRoute Rule<br/><i>sessionPersistence</i><br/><b>(NOT SUPPORTED)</b>"]
        GWAPI_BTP["BackendTrafficPolicy<br/><b>(NOT SUPPORTED)</b>"]
    end

    subgraph "AWS Gateway API Controller"
        CTRL["aws-application-networking-k8s<br/><i>Translates to VPC Lattice API</i>"]
    end

    subgraph "AWS VPC Lattice"
        TG["Target Group<br/><i>Round-robin only<br/>No session persistence<br/>No sticky sessions</i>"]
    end

    GWAPI_RI -.->|"NOT<br/>SUPPORTED"| CTRL
    GWAPI_BTP -.->|"NOT<br/>SUPPORTED"| CTRL
    CTRL --> TG

    classDef notimpl fill:#fff3cd,stroke:#ffc107,stroke-dasharray: 5 5
    classDef aws fill:#cce5ff,stroke:#004085
    classDef pipeline fill:#f0f0f0,stroke:#666
    class GWAPI_RI,GWAPI_BTP notimpl
    class TG aws
    class CTRL pipeline"""

    if name == "Kong":
        return """graph TB
    subgraph "User Configuration"
        GWAPI_RI["HTTPRoute Rule<br/><i>sessionPersistence</i><br/><b>(NOT SUPPORTED)</b>"]
        KUP["KongUpstreamPolicy<br/><i>algorithm: consistent-hashing<br/>hash_on: cookie/header/ip</i>"]
        KUP_STICKY["KongUpstreamPolicy<br/><i>algorithm: sticky-sessions<br/>(Enterprise only)</i>"]
        GWAPI_BTP["BackendTrafficPolicy<br/><b>(NOT SUPPORTED)</b>"]
    end

    subgraph "Kong Ingress Controller"
        KIC["KIC / Kong Operator<br/><i>Translates to Kong Admin API</i>"]
    end

    subgraph "Kong Gateway (OpenResty/NGINX + Lua)"
        UPSTREAM["Upstream<br/><i>hash_on: cookie<br/>hash_on_cookie_path: /</i>"]
        STICKY["Sticky Sessions<br/><i>Encoded cookie<br/>(Enterprise only)</i>"]
    end

    GWAPI_RI -.->|"NOT<br/>SUPPORTED"| KIC
    GWAPI_BTP -.->|"NOT<br/>SUPPORTED"| KIC
    KUP -->|"consistent hashing"| KIC
    KUP_STICKY -->|"sticky sessions"| KIC
    KIC --> UPSTREAM
    KIC --> STICKY

    classDef notimpl fill:#fff3cd,stroke:#ffc107,stroke-dasharray: 5 5
    classDef existing fill:#d4edda,stroke:#28a745
    classDef kong fill:#cce5ff,stroke:#004085
    classDef pipeline fill:#f0f0f0,stroke:#666
    class GWAPI_RI,GWAPI_BTP notimpl
    class KUP,KUP_STICKY existing
    class UPSTREAM,STICKY kong
    class KIC pipeline"""

    if name == "GKE":
        return """graph TB
    subgraph "User Configuration"
        GWAPI_RI["HTTPRoute Rule<br/><i>sessionPersistence</i><br/><b>(NOT SUPPORTED)</b>"]
        GCPBP["GCPBackendPolicy<br/><i>sessionAffinity:<br/>CLIENT_IP / GENERATED_COOKIE</i>"]
        GCPTDP["GCPTrafficDistributionPolicy<br/><i>sessionAffinity:<br/>HTTP_COOKIE / HEADER_FIELD</i>"]
        GCPSAF["GCPSessionAffinityFilter<br/><i>ExtensionRef on HTTPRoute<br/>(strong persistence / GSSA)</i>"]
        GCPSAP["GCPSessionAffinityPolicy<br/><i>service-level GSSA<br/>(strong persistence)</i>"]
    end

    subgraph "GKE Gateway Controller (closed-source)"
        CTRL["GKE Gateway Controller<br/><i>networking.gke.io/gateway</i>"]
    end

    subgraph "Google Cloud Load Balancer"
        BA["Backend Service<br/><i>sessionAffinity config<br/>affinityCookieTtlSec</i>"]
        GSSA["Stateful Session Affinity<br/><i>GSSA cookie (encoded host)</i>"]
    end

    GWAPI_RI -.->|"NOT<br/>SUPPORTED"| CTRL
    GCPBP -->|"basic affinity"| CTRL
    GCPTDP -->|"advanced affinity"| CTRL
    GCPSAF -->|"strong persistence"| CTRL
    GCPSAP -->|"strong persistence"| CTRL
    CTRL --> BA
    CTRL --> GSSA

    classDef notimpl fill:#fff3cd,stroke:#ffc107,stroke-dasharray: 5 5
    classDef existing fill:#d4edda,stroke:#28a745
    classDef gcloud fill:#cce5ff,stroke:#004085
    classDef pipeline fill:#f0f0f0,stroke:#666
    class GWAPI_RI notimpl
    class GCPBP,GCPTDP,GCPSAF,GCPSAP existing
    class BA,GSSA gcloud
    class CTRL pipeline"""

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

    if name == "HAProxy Ingress":
        return """graph TB
    subgraph "User Configuration"
        ANNOT["Service Annotations<br/><i>haproxy-ingress.github.io/<br/>affinity: cookie<br/>session-cookie-name: SERVERID<br/>session-cookie-strategy: insert</i>"]
        GWAPI_RI["HTTPRoute Rule<br/><i>sessionPersistence</i><br/><b>(NOT IMPLEMENTED)</b>"]
        GWAPI_BTP["BackendTrafficPolicy<br/><i>sessionPersistence</i><br/><b>(NOT IMPLEMENTED)</b>"]
    end

    subgraph "HAProxy Ingress Controller"
        CONV["Converter<br/><i>annotations → hatypes model</i>"]
        TMPL["Go Templates<br/><i>haproxy.tmpl</i>"]
    end

    subgraph "HAProxy Configuration"
        subgraph "backend (per-Service)"
            COOKIE["cookie SERVERID insert<br/><i>indirect nocache<br/>domain, httponly, secure</i>"]
            BALANCE["balance roundrobin"]
            SERVERS["server s1 10.0.0.1:80<br/>cookie s1"]
        end
    end

    ANNOT -->|"annotation-driven"| CONV
    CONV --> TMPL
    TMPL --> COOKIE
    TMPL --> BALANCE
    TMPL --> SERVERS

    GWAPI_RI -.->|"NOT<br/>IMPLEMENTED"| CONV
    GWAPI_BTP -.->|"NOT<br/>IMPLEMENTED"| CONV

    classDef notimpl fill:#fff3cd,stroke:#ffc107,stroke-dasharray: 5 5
    classDef existing fill:#d4edda,stroke:#28a745
    classDef haproxy fill:#cce5ff,stroke:#004085
    classDef pipeline fill:#f0f0f0,stroke:#666
    class GWAPI_RI,GWAPI_BTP notimpl
    class ANNOT existing
    class COOKIE,BALANCE,SERVERS haproxy
    class CONV,TMPL pipeline"""

    if name == "Cilium":
        return """graph TB
    subgraph "User Configuration"
        GWAPI_RI["HTTPRoute Rule<br/><i>sessionPersistence</i><br/><b>(NOT IMPLEMENTED)</b>"]
        CEC["CiliumEnvoyConfig<br/><i>custom Envoy listener/cluster<br/>config (advanced)</i>"]
        GWAPI_BTP["BackendTrafficPolicy<br/><i>sessionPersistence</i><br/><b>(NOT IMPLEMENTED)</b>"]
    end

    subgraph "Cilium Agent"
        GWAPI_PROC["Gateway API<br/>Resource Processor"]
        XDS_SERVER["Embedded xDS Server<br/><i>CDS/LDS/RDS/EDS</i>"]
    end

    subgraph "Dataplane"
        subgraph "eBPF (L3/L4)"
            EBPF["Socket-level LB<br/><i>Maglev / random<br/>session affinity via<br/>ct_state / lb4_affinity_map</i>"]
        end
        subgraph "Envoy (L7 HTTP)"
            ENVOY_LB["Cluster LB Policy<br/><i>(no session persistence<br/>config generated)</i>"]
            ENVOY_ROUTE["Route Config<br/><i>(no hash_policy or<br/>stateful_session)</i>"]
        end
    end

    GWAPI_RI -.->|"NOT<br/>IMPLEMENTED"| GWAPI_PROC
    GWAPI_BTP -.->|"NOT<br/>IMPLEMENTED"| GWAPI_PROC
    GWAPI_PROC --> XDS_SERVER
    XDS_SERVER --> ENVOY_LB
    XDS_SERVER --> ENVOY_ROUTE

    CEC -->|"advanced users"| XDS_SERVER

    EBPF -.->|"L3/L4 only"| ENVOY_LB

    classDef notimpl fill:#fff3cd,stroke:#ffc107,stroke-dasharray: 5 5
    classDef existing fill:#d4edda,stroke:#28a745
    classDef envoy fill:#cce5ff,stroke:#004085
    classDef ebpf fill:#e8f5e9,stroke:#2e7d32,stroke-width:2px
    classDef pipeline fill:#f0f0f0,stroke:#666
    class GWAPI_RI,GWAPI_BTP notimpl
    class CEC existing
    class ENVOY_LB,ENVOY_ROUTE envoy
    class EBPF ebpf
    class GWAPI_PROC,XDS_SERVER pipeline"""

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

    # Native API mapping: implementation name → (native API name, is it GWAPI-only?)
    native_api_map = {
        "Cilium": ("—", True),
        "Contour": ("<code>HTTPProxy</code> loadBalancerPolicy", False),
        "Amazon EKS": ("—", True),
        "Envoy Gateway": ("—", True),
        "GKE": ("<code>GCPBackendPolicy</code>, <code>GCPSessionAffinityFilter/Policy</code>", False),
        "HAProxy Ingress": ("Annotations (<code>session-cookie-*</code>)", False),
        "Istio": ("<code>DestinationRule</code> consistentHash", False),
        "kgateway": ("<code>BackendConfigPolicy</code>", False),
        "Kong": ("<code>KongUpstreamPolicy</code>", False),
        "NGINX Gateway Fabric": ("—", True),
        "Traefik": ("<code>IngressRoute</code> / <code>TraefikService</code> sticky", False),
    }

    rows = ""
    for impl in impl_list:
        m = impl["metadata"]
        ga = impl.get("gwapi_assessment", {})
        ri = ga.get("route_inline", {})
        btp = ga.get("backend_traffic_policy", {})
        np = impl.get("native_profile", {})
        model = impl.get("api_stack", {}).get("translation_model", "")
        model_badge = f'<span class="badge" style="background:{"#17a2b8" if model == "direct" else "#6f42c1"}">{model}</span>' if model else "—"

        native_api, gwapi_only = native_api_map.get(m["name"], ("—", True))
        if gwapi_only:
            native_api = '<span style="color:#888">GWAPI-only</span>'

        rows += f"""<tr>
            <td><a href="{impl['_filename']}.html">{m['name']}</a></td>
            <td>{m.get('dataplane','')}</td>
            <td>{model_badge}</td>
            <td>{native_api}</td>
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
                    <th>Dataplane</th>
                    <th>Translation</th>
                    <th>Native API</th>
                    <th>Route-Inline</th>
                    <th>BTP</th>
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


TOPIC_LINKS = [
    ("Cookie Path", "topic-cookie-path.html"),
    ("Session Name Collisions", "topic-name-collisions.html"),
    ("Mesh (East-West)", "topic-mesh.html"),
    ("Route-Inline vs BTP", "topic-route-vs-btp.html"),
]


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
    tech_dropdown += _build_dropdown("Topics", TOPIC_LINKS)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{title} — Gateway API Implementation Survey</title>
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
            padding-bottom: 4px;
            margin-bottom: -4px;
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

        /* Topic page styles */
        .topic-header {{
            background: var(--section-bg);
            padding: 20px;
            border-radius: 8px;
            margin-bottom: 24px;
            border-left: 4px solid #9c27b0;
        }}
        .topic-links {{
            margin-top: 12px;
            font-size: 14px;
        }}
        .spec-quote {{
            background: #f5f5f5;
            border-left: 4px solid var(--accent);
            padding: 16px 20px;
            border-radius: 0 6px 6px 0;
            margin: 16px 0;
            font-size: 14px;
        }}
        .spec-quote ol {{ margin: 8px 0 8px 20px; }}
        .spec-source {{ font-size: 12px; color: #888; margin-top: 8px; }}
        .callout {{
            padding: 16px 20px;
            border-radius: 6px;
            margin: 16px 0;
            font-size: 14px;
        }}
        .callout-warning {{
            background: #fff3cd;
            border-left: 4px solid #ffc107;
        }}
        .callout-info {{
            background: #e3f2fd;
            border-left: 4px solid #2196f3;
        }}
        .proposals-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
            gap: 16px;
            margin: 16px 0 32px;
        }}
        .proposal-card {{
            border: 2px solid #28a745;
            border-radius: 8px;
            padding: 16px;
            background: #f8fff8;
        }}
        .proposal-card.current {{
            border-color: #ffc107;
            background: #fffef5;
        }}
        .proposal-card h4 {{ margin-bottom: 8px; }}
        .proposal-pro-con {{ margin: 12px 0; font-size: 15px; }}
        .proposal-pro-con .pro::before {{ content: "✓ "; color: #28a745; font-weight: bold; font-size: 18px; }}
        .proposal-pro-con .con::before {{ content: "✗ "; color: #dc3545; font-weight: bold; font-size: 18px; }}
        .proposal-pro-con p {{ margin: 6px 0; }}
        .impl-count {{ font-size: 12px; color: #666; margin-top: 8px; font-style: italic; }}
        .notes-cell {{ font-size: 13px; max-width: 300px; }}
        .topic-link {{
            text-decoration: none;
            font-size: 14px;
            margin-left: 4px;
            vertical-align: middle;
            opacity: 0.7;
        }}
        .topic-link:hover {{ opacity: 1; }}
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
    # (canonical_key, display_name, description, topic_url or None)
    api_fields = [
        ("type: Cookie", "type: Cookie", "Session persistence using cookies", None),
        ("type: Header", "type: Header", "Session persistence using headers", None),
        ("sessionName", "sessionName", "User-specified session name (cookie/header name)", None),
        ("absoluteTimeout", "absoluteTimeout", "Maximum session lifetime (maps to cookie TTL)", None),
        ("idleTimeout", "idleTimeout", "Session expires after inactivity period", None),
        ("cookieConfig.lifetimeType: Session", "cookie.lifetimeType: Session", "Session cookie (no expiry, cleared on browser close)", None),
        ("cookieConfig.lifetimeType: Permanent", "cookie.lifetimeType: Permanent", "Persistent cookie with expiry from absoluteTimeout", None),
        ("cookie path (computed)", "cookie path (computed)", "Cookie path derived from route path matches", "topic-cookie-path.html"),
    ]

    # Alias map: normalize YAML gwapi_field values to canonical keys
    field_aliases = {
        "type: cookie": "type: Cookie",
        "type: header": "type: Header",
        "sessionname": "sessionName",
        "cookie.name": "sessionName",
        "header.name": "sessionName",
        "absolutetimeout": "absoluteTimeout",
        "idletimeout": "idleTimeout",
        "cookie.lifetimetype: session": "cookieConfig.lifetimeType: Session",
        "cookieconfig.lifetimetype: session": "cookieConfig.lifetimeType: Session",
        "cookie.lifetimetype: permanent": "cookieConfig.lifetimeType: Permanent",
        "cookieconfig.lifetimetype: permanent": "cookieConfig.lifetimeType: Permanent",
        "cookie path (computed)": "cookie path (computed)",
        "cookie path (not in spec)": "cookie path (computed)",
        "cookie path": "cookie path (computed)",
    }

    def normalize_field(f):
        return field_aliases.get(f.lower().strip(), f)

    # Difficulty scoring: direct=3, translation_needed=2, not_supported=0, no_native_equivalent=0
    diff_scores = {"direct": 3, "translation_needed": 2, "not_supported": 0, "no_native_equivalent": 0}
    diff_colors = {"direct": "#28a745", "translation_needed": "#ffc107", "not_supported": "#dc3545", "no_native_equivalent": "#6c757d"}
    diff_labels = {"direct": "Direct", "translation_needed": "Needs Translation", "not_supported": "Not Supported", "no_native_equivalent": "No Equivalent"}

    # Build per-field, per-implementation matrix
    canonical_keys = {key for key, _, _, _ in api_fields}
    field_data = {}
    for key, _, _, _ in api_fields:
        field_data[key] = {}

    for impl in impl_list:
        name = impl["metadata"]["name"]
        ga = impl.get("gwapi_assessment", {})
        for fm in ga.get("field_mapping", []):
            gwapi_field = fm.get("gwapi_field", "")
            difficulty = fm.get("mapping_difficulty", "")
            canonical = normalize_field(gwapi_field)
            if canonical in canonical_keys:
                field_data[canonical][name] = difficulty

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
    for key, display, desc, topic_url in api_fields:
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

        # Topic link
        topic_link = f' <a href="{topic_url}" class="topic-link" title="Deep dive: {display}">&#x1f50d;</a>' if topic_url else ""

        field_scores_html += f"""
        <div class="field-score-row">
            <div class="field-score-label">
                <code>{display}</code>{topic_link}
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
    for key, display, desc, topic_url in api_fields:
        mappings = field_data.get(key, {})
        topic = f' <a href="{topic_url}" title="Deep dive">&#x1f50d;</a>' if topic_url else ""
        row = f"<td><code>{display}</code>{topic}</td>"
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
    # (label, capability path, GEP-1619 field name or None, topic URL or None)
    # GEP-1619 fields first, then other capabilities
    cap_fields = [
        ("Cookie Persistence", "cookie_persistence", "type: Cookie", None),
        ("Header Persistence", "header_persistence", "type: Header", None),
        ("Session Name", "cookie_attributes.name", "sessionName", None),
        ("Absolute Timeout", "absolute_timeout", "absoluteTimeout", None),
        ("Idle Timeout", "idle_timeout", "idleTimeout", None),
        ("Cookie Lifetime: Session", "lifetime_type_session", "cookieConfig.lifetimeType: Session", None),
        ("Cookie Lifetime: Permanent", "lifetime_type_permanent", "cookieConfig.lifetimeType: Permanent", None),
        ("Cookie Path", "cookie_attributes.path", "cookie path (computed)", "topic-cookie-path.html"),
        ("Cookie TTL", "cookie_attributes.ttl", "absoluteTimeout (cookie TTL)", None),
        # Other capabilities (not in GEP-1619)
        ("Source IP Affinity", "source_ip_affinity", None, None),
        ("Cookie Domain", "cookie_attributes.domain", None, None),
        ("Cookie Secure", "cookie_attributes.secure", None, None),
        ("Cookie HttpOnly", "cookie_attributes.http_only", None, None),
        ("Cookie SameSite", "cookie_attributes.same_site", None, None),
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
    for label, path, gwapi_field, cap_topic_url in cap_fields:
        # Insert separator row when transitioning from GEP-1619 fields to other capabilities
        if prev_had_field and gwapi_field is None:
            cap_rows += f'<tr class="cap-group-separator"><td colspan="{n_data_cols + 2}"><em>Additional Native Capabilities (not in GEP-1619)</em></td></tr>'
        prev_had_field = gwapi_field is not None

        cap_topic = f' <a href="{cap_topic_url}" class="topic-link" title="Deep dive">&#x1f50d;</a>' if cap_topic_url else ""
        field_cell = f"<td><code>{gwapi_field}</code>{cap_topic}</td>" if gwapi_field else "<td></td>"
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


def render_cookie_path_page(impls: list[dict]) -> str:
    """Render a deep-dive topic page on cookie path handling across implementations."""
    impl_list = [i for i in impls if i["metadata"].get("type") != "dataplane_only"]
    dp_list = [i for i in impls if i["metadata"].get("type") == "dataplane_only"]

    # Extract cookie path info from field_mapping for each implementation
    path_data = {}
    for impl in impl_list:
        name = impl["metadata"]["name"]
        ga = impl.get("gwapi_assessment", {})
        for fm in ga.get("field_mapping", []):
            gf = fm.get("gwapi_field", "").lower()
            if "cookie path" in gf:
                path_data[name] = {
                    "native_field": fm.get("native_field", "—"),
                    "difficulty": fm.get("mapping_difficulty", ""),
                    "notes": fm.get("notes", ""),
                }
                break
        if name not in path_data:
            path_data[name] = {"native_field": "—", "difficulty": "", "notes": ""}

    # Build comparison table rows
    comp_rows = ""
    for impl in impl_list:
        name = impl["metadata"]["name"]
        repo_url = impl["metadata"].get("repo_url", "")
        d = path_data.get(name, {})
        diff = d.get("difficulty", "")
        badge = mapping_badge(diff) if diff else "—"
        native = d.get("native_field", "—") or "—"
        notes = inline_code(d.get("notes", "").strip().replace("\n", " "))

        # Determine behavior
        caps = impl.get("native_profile", {}).get("capabilities", {})
        cookie_attrs = caps.get("cookie_attributes", {})
        has_path = cookie_attrs.get("path", False)

        if diff == "direct":
            behavior = '<span style="color:#28a745;font-weight:600">Computes from route</span>'
        elif diff == "translation_needed":
            behavior = '<span style="color:#ffc107;font-weight:600">Needs translation</span>'
        elif diff == "not_supported":
            behavior = '<span style="color:#dc3545;font-weight:600">Not set / hardcoded</span>'
        elif diff == "no_native_equivalent":
            behavior = '<span style="color:#6c757d;font-weight:600">No equivalent</span>'
        else:
            behavior = '<span style="color:#6c757d">N/A</span>'

        # Prior art: did this behavior exist before GEP-1619?
        gwapi_impls_with_computed = {"Envoy Gateway", "NGINX Gateway Fabric"}
        if name in gwapi_impls_with_computed and diff == "direct":
            prior_art = '<span style="color:#888;font-size:12px">Implements GEP-1619 spec</span>'
        elif diff == "direct":
            prior_art = '<span style="color:#28a745;font-size:12px">Native prior art</span>'
        elif diff == "not_supported" and name in ("Contour", "HAProxy Ingress"):
            prior_art = '<span style="color:#888;font-size:12px">Hardcodes <code>/</code> (pre-dates GEP-1619)</span>'
        else:
            prior_art = ""

        comp_rows += f"""<tr>
            <td><a href="{impl['_filename']}.html"><strong>{name}</strong></a></td>
            <td>{behavior}</td>
            <td>{prior_art}</td>
            <td>{badge}</td>
            <td><code>{native}</code></td>
            <td class="notes-cell">{notes}</td>
        </tr>"""

    # Dataplane path support
    dp_rows = ""
    for impl in dp_list:
        name = impl["metadata"]["name"]
        caps = impl.get("native_profile", {}).get("capabilities", {})
        cookie_attrs = caps.get("cookie_attributes", {})
        has_path = cookie_attrs.get("path", False)
        dp_rows += f"""<tr>
            <td><a href="{impl['_filename']}.html"><strong>{name}</strong></a></td>
            <td>{bool_icon(has_path)}</td>
        </tr>"""

    # Mermaid diagram showing the problem
    problem_diagram = """graph LR
    CLIENT["Client browser<br/><i>requests /</i>"] -->|"GET /"| EDGE["Edge Proxy<br/><i>rewrites to /foo/bar</i>"]
    EDGE -->|"GET /foo/bar"| GW["Gateway API<br/><i>matches /foo/bar</i>"]
    GW -->|"Set-Cookie:<br/>Path=/foo/bar"| CLIENT

    CLIENT2["Client browser<br/><i>next request to /</i>"] -.->|"Cookie NOT sent<br/>(path mismatch)"| EDGE2["Edge Proxy"]

    classDef client fill:#e3f2fd,stroke:#1565c0,stroke-width:2px
    classDef proxy fill:#fff3e0,stroke:#ff9800,stroke-width:2px
    classDef gw fill:#fce4ec,stroke:#e91e63,stroke-width:2px
    classDef broken fill:#ffebee,stroke:#c62828,stroke-width:2px,stroke-dasharray: 5 5
    class CLIENT,CLIENT2 client
    class EDGE,EDGE2 proxy
    class GW gw"""

    # Proposals comparison
    proposals_diagram = """graph TB
    subgraph STATUS_QUO["Status Quo (current GEP-1619)"]
        SQ["Always derive Path<br/>from route match<br/><i>Path=/foo/bar</i>"]
    end

    subgraph OPTION_A["Option A: Relax (issue #4713)"]
        OA["Allow omitting Path<br/><i>browser computes<br/>default from request URL</i>"]
    end

    subgraph OPTION_B["Option B: Explicit field"]
        OB["Add cookieConfig.path<br/><i>operator sets exact path<br/>e.g. path: /</i>"]
    end

    SQ -.->|"breaks with<br/>upstream rewrites"| PROBLEM["Session persistence<br/>broken"]
    OA -->|"browser defaults<br/>to request path"| WORKS["Session persistence<br/>works"]
    OB -->|"operator controls<br/>exact scope"| WORKS

    classDef current fill:#fff3cd,stroke:#ffc107,stroke-width:2px
    classDef proposal fill:#d4edda,stroke:#28a745,stroke-width:2px
    classDef broken fill:#f8d7da,stroke:#dc3545,stroke-width:2px
    classDef works fill:#d4edda,stroke:#28a745,stroke-width:2px
    class SQ current
    class OA,OB proposal
    class PROBLEM broken
    class WORKS works"""

    return f"""
    <div class="content-wrapper">
        <div class="topic-header">
            <h2>Cookie Path: The Scope Problem</h2>
            <p>How should the cookie <code>Path</code> attribute be set for session persistence?
               This is one of the most contentious aspects of GEP-1619, with active discussion
               on whether implementations should compute it, omit it, or let operators configure it.</p>
            <div class="topic-links">
                <strong>Related:</strong>
                <a href="https://github.com/kubernetes-sigs/gateway-api/issues/4713" target="_blank">issue #4713</a> —
                <a href="https://github.com/kubernetes-sigs/gateway-api/pull/4649" target="_blank">PR #4649</a> —
                <a href="https://github.com/kubernetes-sigs/gateway-api/issues/4268" target="_blank">issue #4268</a> —
                <a href="https://gateway-api.sigs.k8s.io/geps/gep-1619/#path" target="_blank">GEP-1619 Path section</a>
            </div>
        </div>

        <h3>What the Spec Says Today</h3>
        <div class="spec-quote">
            <p>When session persistence is enabled on an xRoute rule, the implementor should interpret the path
               as configured on the xRoute:</p>
            <ol>
                <li>For a route that matches all paths, set <code>Path=/</code></li>
                <li>For multiple paths, use the matched route path</li>
                <li>For regex paths, use the longest non-regex prefix</li>
            </ol>
            <p>When attached via <code>BackendLBPolicy</code> to a Service, the <code>Path</code> attribute
               <strong>MUST be left unset</strong>.</p>
            <p class="spec-source">— <a href="https://gateway-api.sigs.k8s.io/geps/gep-1619/#path" target="_blank">GEP-1619, Path section</a></p>
        </div>

        <h3>The Problem</h3>
        <p>The current guidance breaks when the path seen by the Gateway API implementation differs from the
           path the client browser requested — e.g. when an upstream edge proxy rewrites the URL.</p>
    </div>
    <div class="mermaid-container">
        <pre class="mermaid">{problem_diagram}</pre>
    </div>
    <div class="content-wrapper">
        <div class="callout callout-warning">
            <strong>Result:</strong> The browser scopes the cookie to <code>/foo/bar</code>, but the user's
            next request goes to <code>/</code>. The browser doesn't send the cookie, and session persistence breaks.
        </div>

        <h3>Why Cookie Path Scoping Matters</h3>
        <p>Beyond the rewrite problem, there's a practical reason to care about cookie path scope:
           <strong>cookie proliferation and bandwidth overhead.</strong></p>
        <div class="callout callout-info">
            <p>A cookie scoped to <code>Path=/</code> is sent with <strong>every request</strong> to the domain —
               API calls, static assets, images, websockets, everything. In a setup with multiple backends:</p>
            <pre style="background:#f5f5f5;padding:12px;border-radius:4px;font-size:13px;margin:8px 0">/app    → backend_app    (cookie APP_SRV)
/api    → backend_api    (cookie API_SRV)
/static → backend_static (cookie STATIC_SRV)</pre>
            <p>Every request to <code>/static/logo.png</code> sends all three cookies, even though only
               <code>STATIC_SRV</code> is relevant. With enough backends, this adds hundreds of bytes to every request.</p>
            <p>Scoping each cookie to its route path (<code>Path=/app</code>, <code>Path=/api</code>, etc.) avoids
               this overhead and follows the principle of least privilege. This is a real complaint in the
               <a href="https://discourse.haproxy.org/t/preventing-proliferation-of-sticky-session-cookies-with-multiple-backends/8924" target="_blank">HAProxy community</a>,
               where the cookie path is hardcoded to <code>/</code> with no way to change it.</p>
        </div>

        <h3>Prior Art: How Cookie Path Was Handled Before GEP-1619</h3>
        <p>No implementation or dataplane had "compute path from route matches" behavior before GEP-1619.
           The prior art is either user-configured, hardcoded to <code>/</code>, or omitted entirely.</p>
        <table class="comparison-table">
            <thead><tr>
                <th>Implementation</th>
                <th>API</th>
                <th>Cookie Path</th>
                <th>Source</th>
            </tr></thead>
            <tbody>
                <tr>
                    <td><strong>Istio</strong></td>
                    <td><code>DestinationRule httpCookie.path</code></td>
                    <td>User-configured (default: omitted)</td>
                    <td><a href="https://github.com/istio/istio/blob/master/pilot/pkg/networking/core/route/route.go#L1487" target="_blank">source (L1487)</a></td>
                </tr>
                <tr>
                    <td><strong>Contour</strong></td>
                    <td><code>HTTPProxy loadBalancerPolicy</code></td>
                    <td>Hardcoded <code>/</code></td>
                    <td><a href="https://github.com/projectcontour/contour/blob/main/internal/dag/policy.go#L717" target="_blank">source (L717)</a></td>
                </tr>
                <tr>
                    <td><strong>HAProxy Ingress</strong></td>
                    <td>Annotations</td>
                    <td>Hardcoded <code>/</code></td>
                    <td><a href="https://github.com/jcmoraisjr/haproxy-ingress/blob/master/rootfs/etc/templates/haproxy/haproxy.tmpl#L740" target="_blank">template (L740)</a>;
                        <a href="https://github.com/jcmoraisjr/haproxy-ingress/issues/528" target="_blank">#528</a> requests it</td>
                </tr>
                <tr>
                    <td><strong>Traefik</strong></td>
                    <td><code>IngressRoute sticky.cookie</code></td>
                    <td>User-configured (default: <code>/</code>)</td>
                    <td><a href="https://github.com/traefik/traefik/blob/master/pkg/server/service/loadbalancer/sticky.go#L58" target="_blank">source (L58)</a></td>
                </tr>
                <tr>
                    <td><strong>GKE</strong></td>
                    <td><code>GCPTrafficDistributionPolicy</code></td>
                    <td>User-configured (default: undocumented)</td>
                    <td><a href="https://cloud.google.com/kubernetes-engine/docs/how-to/configure-gateway-resources#configure_http_cookie-based_session_affinity" target="_blank">docs</a></td>
                </tr>
                <tr>
                    <td><strong>Kong</strong></td>
                    <td><code>upstream hash_on_cookie_path</code></td>
                    <td>User-configured (default: <code>/</code>)</td>
                    <td><a href="https://github.com/Kong/kong/blob/master/kong/db/schema/entities/upstreams.lua#L194" target="_blank">source (L194)</a></td>
                </tr>
                <tr>
                    <td><strong>Amazon EKS</strong></td>
                    <td>N/A (VPC Lattice)</td>
                    <td>No session persistence at all</td>
                    <td><a href="https://docs.aws.amazon.com/vpc-lattice/latest/ug/target-groups.html" target="_blank">docs</a></td>
                </tr>
                <tr>
                    <td><strong>Cilium</strong></td>
                    <td>N/A</td>
                    <td>No session persistence at all</td>
                    <td><a href="https://github.com/cilium/proxy/blob/main/envoy_build_config/extensions_build_config.bzl" target="_blank">build config</a> (stateful_session commented out)</td>
                </tr>
                <tr class="cap-group-separator"><td colspan="4"><em>Implements GEP-1619 (computed path built for Gateway API)</em></td></tr>
                <tr>
                    <td><strong>Envoy Gateway</strong></td>
                    <td><code>routePathToCookiePath()</code></td>
                    <td>Computes from route (GEP-1619)</td>
                    <td><a href="https://github.com/envoyproxy/gateway" target="_blank">repo</a></td>
                </tr>
                <tr>
                    <td><strong>NGINX Gateway Fabric</strong></td>
                    <td><code>processSessionPersistenceConfig()</code></td>
                    <td>Computes from route (GEP-1619)</td>
                    <td><a href="https://github.com/nginx/nginx-gateway-fabric" target="_blank">repo</a></td>
                </tr>
                <tr>
                    <td><strong>kgateway</strong></td>
                    <td><code>convertSessionPersistence()</code></td>
                    <td>Not set (GEP-1619, no path computation)</td>
                    <td><a href="https://github.com/kgateway-dev/kgateway" target="_blank">repo</a></td>
                </tr>
                <tr class="cap-group-separator"><td colspan="4"><em>Dataplanes</em></td></tr>
                <tr>
                    <td><strong>NGINX</strong></td>
                    <td><code>sticky cookie path=</code></td>
                    <td>User-configured (default: omitted)</td>
                    <td><a href="https://nginx.org/en/docs/http/ngx_http_upstream_module.html#sticky" target="_blank">docs</a></td>
                </tr>
                <tr>
                    <td><strong>HAProxy</strong></td>
                    <td><code>cookie</code> directive</td>
                    <td>Hardcoded <code>/</code></td>
                    <td><a href="https://docs.haproxy.org/3.0/configuration.html#4-cookie" target="_blank">docs</a></td>
                </tr>
                <tr>
                    <td><strong>Envoy</strong></td>
                    <td><code>hash_policy cookie.path</code></td>
                    <td>User-configured (default: omitted)</td>
                    <td><a href="https://www.envoyproxy.io/docs/envoy/latest/api-v3/config/route/v3/route_components.proto#envoy-v3-api-msg-config-route-v3-routeaction-hashpolicy-cookie" target="_blank">API ref</a></td>
                </tr>
            </tbody>
        </table>
        <p><strong>Prior art summary:</strong> User-configured (6), Hardcoded <code>/</code> (3), No session persistence (2), Computed from route before GEP-1619 (0).
           Implementations that compute path today (Envoy Gateway, NGINX Gateway Fabric) built this specifically for GEP-1619.</p>

        <h3>The Proposals</h3>
    </div>
    <div class="mermaid-container">
        <pre class="mermaid">{proposals_diagram}</pre>
    </div>
    <div class="content-wrapper">
        <div class="proposals-grid">
            <div class="proposal-card current">
                <h4>Status Quo</h4>
                <p><strong>Always compute from route match</strong></p>
                <p>Implementations SHOULD derive <code>Path</code> from the matched xRoute path.
                   Works well when the client URL matches what the gateway sees.</p>
                <div class="proposal-pro-con">
                    <p class="pro">Deterministic — no ambiguity about cookie scope</p>
                    <p class="pro">Avoids cookie proliferation — each cookie only sent for its route's path</p>
                    <p class="pro">Least privilege — cookie not exposed to unrelated routes</p>
                    <p class="con">Breaks with upstream rewrites, edge proxies, CDNs</p>
                    <p class="con">Browsers won't send cookie if paths don't match</p>
                </div>
                <p class="impl-count">{sum(1 for d in path_data.values() if d['difficulty'] == 'direct')}/{len(impl_list)} implementations compute path</p>
            </div>
            <div class="proposal-card">
                <h4>Option A: Relax to Allow Omitting</h4>
                <p><strong>Let implementations leave Path unset</strong></p>
                <p>When <code>Path</code> is omitted from <code>Set-Cookie</code>, the browser computes
                   the default path from the URL the user actually requested — not the internally rewritten URL.</p>
                <div class="proposal-pro-con">
                    <p class="pro">Fixes the upstream rewrite problem</p>
                    <p class="pro">Simple — just don't set the attribute</p>
                    <p class="con">Less predictable — browser behavior varies</p>
                    <p class="con">Cookie scope may be broader than intended</p>
                    <p class="con">Cookie proliferation — all cookies sent with all requests</p>
                </div>
                <p class="impl-count">{sum(1 for d in path_data.values() if d['difficulty'] in ('not_supported', 'no_native_equivalent'))}/{len(impl_list)} implementations already omit path</p>
            </div>
            <div class="proposal-card">
                <h4>Option B: Add Explicit <code>cookieConfig.path</code></h4>
                <p><strong>Let operators configure the exact path</strong></p>
                <p>Add a <code>path</code> field to <code>cookieConfig</code> so operators can explicitly
                   set the cookie scope. This gives full control without relying on computed or browser-default behavior.</p>
                <div class="proposal-pro-con">
                    <p class="pro">Full operator control</p>
                    <p class="pro">Portable — same behavior everywhere</p>
                    <p class="pro">Can be combined with Option A as the default</p>
                    <p class="con">More API surface to maintain</p>
                    <p class="con">Security consideration: could scope cookie broader than the route</p>
                </div>
                <p class="impl-count">Proposed in <a href="https://github.com/kubernetes-sigs/gateway-api/issues/4713" target="_blank">issue #4713</a></p>
            </div>
        </div>

        <h3>Security Considerations</h3>
        <div class="callout callout-info">
            <p>A <a href="https://github.com/kubernetes-sigs/gateway-api/issues/4713#issuecomment-2813977919" target="_blank">maintainer comment on #4713</a> raised that an explicit or unset path
               could set a cookie with <em>broader scope</em> than the HTTPRoute, potentially affecting requests
               arriving through other routes in other namespaces. For <code>BackendLBPolicy</code>, explicit path
               is safer because the policy is co-located with the Service.</p>
        </div>

        <h3>Related Issues</h3>
        <table class="comparison-table">
            <thead><tr><th>Issue</th><th>Title</th><th>Relevance</th></tr></thead>
            <tbody>
                <tr>
                    <td><a href="https://github.com/kubernetes-sigs/gateway-api/issues/4713" target="_blank">#4713</a></td>
                    <td>Relax route-level cookie Path handling and consider explicit Path configuration</td>
                    <td>Primary issue — proposes both relaxing and adding explicit path</td>
                </tr>
                <tr>
                    <td><a href="https://github.com/kubernetes-sigs/gateway-api/pull/4649" target="_blank">PR #4649</a></td>
                    <td>Session Name Refactoring and Behavior Clarification</td>
                    <td>WIP PR addressing session name scoping — related path considerations</td>
                </tr>
                <tr>
                    <td><a href="https://github.com/kubernetes-sigs/gateway-api/issues/4268" target="_blank">#4268</a></td>
                    <td>Clarify sessionPersistence scoping and conflict resolution</td>
                    <td>Session name uniqueness across routes — cookie path scope is a factor</td>
                </tr>
                <tr>
                    <td><a href="https://github.com/kubernetes-sigs/gateway-api/issues/4385" target="_blank">#4385</a></td>
                    <td>SessionPersistence based on URL</td>
                    <td>URL-based persistence — different use case but related to path handling</td>
                </tr>
                <tr>
                    <td><a href="https://github.com/kubernetes-sigs/gateway-api/issues/4258" target="_blank">#4258</a></td>
                    <td>STD: Session Persistence (GEP 1619) — standardization tracking</td>
                    <td>Standardization umbrella — cookie path semantics must be resolved before Standard</td>
                </tr>
            </tbody>
        </table>
    </div>"""


def render_mesh_topic_page(impls: list[dict]) -> str:
    """Render a deep-dive topic page on mesh (east-west) session persistence."""
    impl_list = [i for i in impls if i["metadata"].get("type") != "dataplane_only"]

    # Mesh support survey
    mesh_data = {
        "Istio": {"mesh": True, "mesh_sp": "Partial", "notes": "ConsistentHashLB (soft) via DestinationRule. Experimental stateful session via Service label. Gateway API SP not implemented."},
        "Cilium": {"mesh": True, "mesh_sp": "No", "notes": "L4 source IP affinity via eBPF only. No L7 cookie/header session persistence."},
        "Contour": {"mesh": False, "mesh_sp": "No", "notes": "No mesh support."},
        "Envoy Gateway": {"mesh": False, "mesh_sp": "No", "notes": "No mesh support."},
        "kgateway": {"mesh": False, "mesh_sp": "No", "notes": "No mesh support."},
        "NGINX Gateway Fabric": {"mesh": False, "mesh_sp": "No", "notes": "No mesh support."},
        "Traefik": {"mesh": False, "mesh_sp": "No", "notes": "No mesh/GAMMA support."},
        "HAProxy Ingress": {"mesh": False, "mesh_sp": "No", "notes": "No mesh support."},
        "Kong": {"mesh": False, "mesh_sp": "No", "notes": "No mesh support."},
        "GKE": {"mesh": False, "mesh_sp": "No", "notes": "No mesh support via Gateway API."},
        "Amazon EKS": {"mesh": False, "mesh_sp": "No", "notes": "No mesh support."},
    }

    mesh_rows = ""
    for impl in impl_list:
        name = impl["metadata"]["name"]
        d = mesh_data.get(name, {"mesh": False, "mesh_sp": "No", "notes": ""})
        mesh_icon = bool_icon(d["mesh"])
        sp_val = d["mesh_sp"]
        if sp_val == "Partial":
            sp_html = '<span style="color:#ffc107;font-weight:600">Partial</span>'
        elif sp_val == "No":
            sp_html = '<span style="color:#dc3545;font-weight:600">No</span>'
        else:
            sp_html = '<span style="color:#28a745;font-weight:600">Yes</span>'
        mesh_rows += f"""<tr>
            <td><a href="{impl['_filename']}.html"><strong>{name}</strong></a></td>
            <td class="cap-cell">{mesh_icon}</td>
            <td class="cap-cell">{sp_html}</td>
            <td class="notes-cell">{d['notes']}</td>
        </tr>"""

    # North-south vs east-west diagram
    ns_ew_diagram = """graph TB
    subgraph NS["North-South (Ingress)"]
        direction TB
        BROWSER["Browser"] -->|"GET /app"| GW["Gateway"]
        GW -->|"picks 10.0.0.5"| BACKEND_NS["Backend Pod"]
        BACKEND_NS -->|"200 OK"| GW
        GW -->|"Set-Cookie: session=encoded-10.0.0.5"| BROWSER
        BROWSER -->|"Cookie: session=encoded-10.0.0.5<br/>(automatic replay)"| GW
    end

    subgraph EW["East-West (Mesh / Sidecar)"]
        direction TB
        APP_A["Service A App"] -->|"GET /api"| SIDECAR_A["A's Sidecar"]
        SIDECAR_A -->|"picks 10.0.0.5"| SIDECAR_B["B's Sidecar"]
        SIDECAR_B --> BACKEND_EW["Service B Pod"]
        BACKEND_EW --> SIDECAR_B
        SIDECAR_B -->|"200 OK"| SIDECAR_A
        SIDECAR_A -->|"Set-Cookie: session=encoded-10.0.0.5"| APP_A
        APP_A -->|"Cookie: session=encoded-10.0.0.5<br/>(MANUAL replay required)"| SIDECAR_A
    end

    classDef browser fill:#e3f2fd,stroke:#1565c0,stroke-width:2px
    classDef gateway fill:#fff3e0,stroke:#ff9800,stroke-width:2px
    classDef sidecar fill:#fce4ec,stroke:#e91e63,stroke-width:2px
    classDef backend fill:#e8f5e9,stroke:#2e7d32,stroke-width:2px
    classDef app fill:#e3f2fd,stroke:#1565c0,stroke-width:2px
    class BROWSER,APP_A browser
    class GW gateway
    class SIDECAR_A,SIDECAR_B sidecar
    class BACKEND_NS,BACKEND_EW backend"""

    # Multi-hop problem diagram
    multihop_diagram = """graph LR
    APP["Service A"] --> SC_A["A's Sidecar"]
    SC_A -->|"cookie pins to<br/>waypoint IP"| WP["Waypoint"]
    WP -->|"picks backend"| SC_B["B's Sidecar"]
    SC_B --> POD["Service B<br/>Pod 10.0.0.5"]

    SC_A -.->|"Problem: pinned to<br/>waypoint, not backend"| WP

    classDef problem fill:#fff3cd,stroke:#ffc107,stroke-width:2px
    classDef normal fill:#e8f5e9,stroke:#2e7d32,stroke-width:2px
    class SC_A,WP problem
    class APP,SC_B,POD normal"""

    # Per-field analysis
    field_rows = """
        <tr>
            <td><code>type: Cookie</code></td>
            <td><span style="color:#28a745">Natural</span> (browser cookie jar)</td>
            <td><span style="color:#dc3545">Problematic</span> (no cookie jar, app must manually extract and replay)</td>
        </tr>
        <tr>
            <td><code>type: Header</code></td>
            <td>Works, but less common</td>
            <td><span style="color:#28a745">More natural</span> (maps to gRPC metadata, devs already pass headers)</td>
        </tr>
        <tr>
            <td><code>cookie.name</code></td>
            <td>Gateway controls it, browser doesn't care</td>
            <td>Client sidecar controls it. App may need to know it to replay. Costin argues name is
               pointless since apps should copy all cookies.</td>
        </tr>
        <tr>
            <td><code>cookie.lifetimeType</code></td>
            <td>Meaningful (browser session vs persistent)</td>
            <td><span style="color:#dc3545">Mostly meaningless</span>. No "browser session" concept.
               Costin recommends avoiding persistent cookies for privacy reasons.</td>
        </tr>
        <tr>
            <td><code>absoluteTimeout</code></td>
            <td>Sets cookie Max-Age/Expires</td>
            <td>Relevant for proxy-tracked lifetime, but less meaningful as a cookie attribute.</td>
        </tr>
        <tr>
            <td><code>cookie path</code></td>
            <td>Meaningful (URL path scoping)</td>
            <td><span style="color:#dc3545">Meaningless</span>. Service-to-service calls are to a DNS name,
               not path-scoped.</td>
        </tr>
        <tr>
            <td>Secure / HttpOnly / SameSite</td>
            <td>Important security attributes</td>
            <td><span style="color:#dc3545">Irrelevant</span>. HttpOnly: no JS. SameSite: no cross-site.
               Secure: mTLS handles encryption.</td>
        </tr>"""

    return f"""
    <div class="content-wrapper">
        <div class="topic-header">
            <h2>Mesh (East-West) Session Persistence</h2>
            <p>How does session persistence work when there's no browser? In service mesh traffic,
               the fundamental assumptions of cookie-based session persistence break down.</p>
            <div class="topic-links">
                <strong>Related:</strong>
                <a href="https://gateway-api.sigs.k8s.io/geps/gep-1619/#session-persistence-api-with-gamma" target="_blank">GEP-1619 GAMMA section</a> —
                <a href="https://gateway-api.sigs.k8s.io/mesh/" target="_blank">Gateway API Mesh (GAMMA)</a> —
                <a href="https://github.com/istio/api/pull/3502" target="_blank">Istio API PR #3502</a> —
                <a href="https://github.com/grpc/proposal/blob/master/A55-xds-stateful-session-affinity.md" target="_blank">gRPC A55</a>
            </div>
        </div>

        <h3>The Core Problem</h3>
        <p>In north-south traffic, the browser automatically stores and replays cookies. In east-west
           (mesh) traffic, there's no browser. The calling application must manually extract the
           session token from the response and include it in subsequent requests. This one difference
           changes everything about how session persistence works.</p>
    </div>
    <div class="mermaid-container">
        <pre class="mermaid">{ns_ew_diagram}</pre>
    </div>
    <div class="content-wrapper">
        <div class="callout callout-warning">
            <strong>Key difference:</strong> In north-south, cookie replay is automatic (browser).
            In east-west, the application must explicitly extract and replay session tokens. This
            breaks the mesh transparency promise — the application becomes aware of a proxy concern.
        </div>

        <h3>How Each GEP-1619 Field Applies to Mesh</h3>
        <p>Most fields designed for north-south cookie-based persistence have different (or no) meaning
           in east-west traffic:</p>
        <table class="comparison-table">
            <thead><tr>
                <th>GEP-1619 Field</th>
                <th>North-South (Ingress)</th>
                <th>East-West (Mesh)</th>
            </tr></thead>
            <tbody>{field_rows}</tbody>
        </table>

        <h3>The Multi-Hop Problem</h3>
        <p>In mesh topologies with multiple proxies in the path (sidecar, waypoint, east-west gateway),
           the client sidecar pins to the <em>next hop</em>, not the final backend. A second session
           token would be needed at each hop.</p>
    </div>
    <div class="mermaid-container">
        <pre class="mermaid">{multihop_diagram}</pre>
    </div>
    <div class="content-wrapper">
        <div class="callout callout-info">
            <p>As discussed in <a href="https://github.com/istio/api/pull/3502" target="_blank">Istio API PR #3502</a>,
               this is why Istio's Costin Manolache recommends keeping sidecar persistence
               (DestinationRule) separate from gateway/waypoint persistence (Gateway API). The mechanisms
               behave differently depending on topology.</p>
        </div>

        <h3>Producer vs Consumer Routes</h3>
    </div>
    <div class="mermaid-container">
        <pre class="mermaid">graph TB
    subgraph PROD_TITLE["Producer Route: Route in same namespace as Service"]
        direction TB
        subgraph FACES_NS1["namespace: faces"]
            direction TB
            PR_ROUTE["HTTPRoute: smiley-route<br/><i>sessionPersistence: ...</i>"]
            PR_SVC["Service: smiley"]
            PR_ROUTE -.->|"parentRef"| PR_SVC
        end
        PR_AFFECT["Affects: ALL callers from ANY namespace"]
    end

    subgraph CONS_TITLE["Consumer Route: Route in different namespace from Service"]
        direction TB
        subgraph FAST_NS["namespace: fast-clients"]
            CR_ROUTE["HTTPRoute: smiley-route<br/><i>sessionPersistence: ...</i>"]
        end
        subgraph FACES_NS2["namespace: faces"]
            CR_SVC["Service: smiley"]
        end
        CR_ROUTE -.->|"parentRef<br/>(cross-namespace)"| CR_SVC
        CR_AFFECT["Affects: ONLY callers in fast-clients namespace"]
    end

    classDef ns fill:#f5f5f5,stroke:#999,stroke-width:2px
    classDef producer fill:#e8f5e9,stroke:#2e7d32,stroke-width:2px
    classDef consumer fill:#e3f2fd,stroke:#1565c0,stroke-width:2px
    classDef svc fill:#fff3e0,stroke:#ff9800,stroke-width:2px
    class FACES_NS1,FACES_NS2,FAST_NS ns
    classDef affect fill:#fff3cd,stroke:#ffc107,stroke-width:1px,font-size:12px
    class PR_ROUTE producer
    class CR_ROUTE consumer
    class PR_SVC,CR_SVC svc
    class PR_AFFECT,CR_AFFECT affect</pre>
    </div>
    <div class="content-wrapper">
        <div class="callout callout-info">
            <p>Session persistence is always the same mechanism (proxy pins client to backend).
               Producer vs consumer just determines the scope: producer applies it to all traffic
               reaching the service, consumer applies it only to traffic from a specific namespace.</p>
        </div>
        <p>In Gateway API mesh (GAMMA), routes are either
           <a href="https://gateway-api.sigs.k8s.io/mesh/#producer-routes" target="_blank">producer</a> or
           <a href="https://gateway-api.sigs.k8s.io/mesh/#consumer-routes" target="_blank">consumer</a>
           routes. This distinction matters for session persistence because it determines who configures
           it and who is affected.</p>
        <table class="comparison-table">
            <thead><tr>
                <th></th>
                <th>Producer Route</th>
                <th>Consumer Route</th>
            </tr></thead>
            <tbody>
                <tr>
                    <td><strong>Definition</strong></td>
                    <td>Route in the <strong>same namespace</strong> as the Service</td>
                    <td>Route in a <strong>different namespace</strong> from the Service</td>
                </tr>
                <tr>
                    <td><strong>Created by</strong></td>
                    <td>Service owner</td>
                    <td>Caller / consumer of the service</td>
                </tr>
                <tr>
                    <td><strong>Affects</strong></td>
                    <td>All traffic to the Service, from any namespace</td>
                    <td>Only traffic from the consumer's namespace</td>
                </tr>
                <tr>
                    <td><strong>N-S equivalent</strong></td>
                    <td>BackendTrafficPolicy</td>
                    <td>Route-inline <code>sessionPersistence</code></td>
                </tr>
                <tr>
                    <td><strong>SP use case</strong></td>
                    <td>"All clients calling my service should get persistent sessions"</td>
                    <td>"My service's calls to this backend should be persistent"</td>
                </tr>
            </tbody>
        </table>
        <p>Session persistence is always enforced on the <strong>consumer side</strong> (the proxy closest
           to the caller). Whether configured by a producer route or consumer route, it's the calling
           proxy (gateway, sidecar, or waypoint) that reads the session token and pins to a backend.
           The backend is unaware that persistence is happening.</p>
        <div class="callout callout-info">
            <p>An open question: what happens when a producer route sets <code>sessionPersistence</code>
               with one configuration and a consumer route in a different namespace sets it with a
               different configuration for the same Service? This conflict scenario is not yet addressed
               in GEP-1619.</p>
        </div>

        <h3>Do We Need Consumer Routes for Session Persistence?</h3>
        <p>For north-south, consumer-configured persistence (route-inline) makes sense because the
           route author controls how the gateway handles their traffic. But for mesh, the question is
           who should be allowed to configure it.</p>
    </div>
    <div class="mermaid-container">
        <pre class="mermaid">graph TB
    subgraph OPTION1["Service Owner Configures (producer-side)"]
        direction TB
        subgraph FACES1["namespace: faces"]
            direction TB
            BTP["BackendTrafficPolicy<br/><i>sessionPersistence: ...</i>"]
            PROD_RT["OR Producer HTTPRoute<br/><i>sessionPersistence: ...</i>"]
            SVC1["Service: smiley"]
            BTP -.->|"targetRef"| SVC1
            PROD_RT -.->|"parentRef"| SVC1
        end
        ALL1["All callers get persistence"]
    end

    subgraph OPTION2["Caller Configures (consumer-side)"]
        direction TB
        subgraph TEAM_NS["namespace: team-a"]
            CONS_RT["Consumer HTTPRoute<br/><i>sessionPersistence: ...</i>"]
        end
        subgraph FACES2["namespace: faces"]
            SVC2["Service: smiley"]
        end
        CONS_RT -.->|"parentRef<br/>(cross-namespace)"| SVC2
        ONLY1["Only team-a gets persistence"]
    end

    classDef owner fill:#e8f5e9,stroke:#2e7d32,stroke-width:2px
    classDef caller fill:#e3f2fd,stroke:#1565c0,stroke-width:2px
    classDef svc fill:#fff3e0,stroke:#ff9800,stroke-width:2px
    classDef ns fill:#f5f5f5,stroke:#999,stroke-width:2px
    classDef affect fill:#fff3cd,stroke:#ffc107,stroke-width:1px
    classDef question fill:#fce4ec,stroke:#e91e63,stroke-width:2px
    class BTP,PROD_RT owner
    class CONS_RT caller
    class SVC1,SVC2 svc
    class FACES1,FACES2,TEAM_NS ns
    class ALL1,ONLY1 affect</pre>
    </div>
    <div class="content-wrapper">
        <p>A <strong>producer route</strong> with session persistence behaves almost identically to
           attaching a <strong>BackendTrafficPolicy</strong> to the Service — both say "all traffic to
           my service gets persistence," and both are configured by the service owner. For mesh,
           BTP may be the cleaner option since it's already a policy targeting Services without
           overloading HTTPRoute semantics.</p>
        <p>A <strong>consumer route</strong> with session persistence means one namespace is configuring
           persistence for another namespace's service. While there are valid use cases (e.g., a
           specific caller has a stateful workflow requiring persistence while other callers don't),
           it raises a boundary concern — should a consumer be able to change how traffic is handled
           for a service they don't own? For north-south this isn't an issue (the gateway owner
           controls their own routes), but in mesh it means reaching across namespace boundaries.</p>
        <div class="callout callout-info">
            <p>An open question: is BTP + producer routes sufficient for mesh session persistence,
               or are consumer routes needed too? This affects both the API design and the conflict
               resolution model.</p>
        </div>

        <h3>Implementation Survey: Mesh Session Persistence</h3>
        <p>Which implementations support mesh traffic via Gateway API, and do any support session
           persistence for east-west?</p>
        <table class="comparison-table">
            <thead><tr>
                <th>Implementation</th>
                <th>Mesh (GAMMA)</th>
                <th>Mesh Session Persistence</th>
                <th>Notes</th>
            </tr></thead>
            <tbody>{mesh_rows}</tbody>
        </table>
        <p>No implementation supports Gateway API session persistence for mesh traffic today.
           Istio has partial support via its native DestinationRule API, not through Gateway API.</p>

        <h3>Prior Art</h3>
        <table class="comparison-table">
            <thead><tr><th>Implementation</th><th>Mechanism</th><th>Type</th><th>Source</th></tr></thead>
            <tbody>
                <tr>
                    <td><strong>Istio ConsistentHashLB</strong></td>
                    <td>DestinationRule <code>consistentHash.httpCookie</code></td>
                    <td>Soft affinity (hash-based)</td>
                    <td><a href="https://istio.io/latest/docs/reference/config/networking/destination-rule/#LoadBalancerSettings-ConsistentHashLB" target="_blank">docs</a></td>
                </tr>
                <tr>
                    <td><strong>Istio Persistent Session</strong></td>
                    <td>Service label <code>istio.io/persistent-session</code></td>
                    <td>Strong (stateful_session filter)</td>
                    <td><a href="https://github.com/istio/istio/issues/39740" target="_blank">issue #39740</a></td>
                </tr>
                <tr>
                    <td><strong>gRPC A55</strong></td>
                    <td>xDS-based stateful session affinity (proxyless)</td>
                    <td>Strong (cookie-based, app manages cookie jar)</td>
                    <td><a href="https://github.com/grpc/proposal/blob/master/A55-xds-stateful-session-affinity.md" target="_blank">proposal</a></td>
                </tr>
                <tr>
                    <td><strong>Linkerd</strong></td>
                    <td>None</td>
                    <td>N/A — proxy takes over LB, conflicts with sticky sessions</td>
                    <td><a href="https://github.com/linkerd/linkerd2/issues/3504" target="_blank">issue #3504</a></td>
                </tr>
            </tbody>
        </table>

        <h3>Open Questions for GEP-1619</h3>
        <p>GEP-1619's graduation criteria requires
           <a href="https://gateway-api.sigs.k8s.io/geps/gep-1619/#standard" target="_blank">GAMMA lead sign-off</a>.
           These questions need answers:</p>
        <ul>
            <li>Should mesh-mode routes (parentRef = Service) default to <code>type: Header</code>
                instead of <code>type: Cookie</code>?</li>
            <li>Should cookie-specific fields (<code>lifetimeType</code>, <code>path</code>) be documented
                as irrelevant for mesh?</li>
            <li>How should multi-hop topologies (sidecar → waypoint → backend) be handled?</li>
            <li>Should the spec acknowledge that mesh session persistence breaks application
                transparency?</li>
            <li>Is header-based persistence sufficient for mesh, or do we need a mesh-specific
                mechanism?</li>
        </ul>
    </div>"""


def render_name_collisions_page(impls: list[dict]) -> str:
    """Render a topic page on session name collision handling."""

    # Collision flow diagram
    collision_flow = """graph TB
    USER["User creates two route rules<br/>with same cookie name"]

    USER --> Q1{"Same xRoute or<br/>different xRoutes?"}

    Q1 -->|"Same xRoute"| SAME
    Q1 -->|"Different xRoutes"| DIFF

    subgraph SAME["Within Same xRoute"]
        direction TB
        S1["Option A: CEL rejects at admission"]
        S2["Option B: First rule wins"]
        S3["Option C: Allow, let browser sort it out"]
    end

    subgraph DIFF["Across xRoutes"]
        direction TB
        D1["Option A: Oldest route wins, newer PartiallyInvalid"]
        D2["Option B: Both work independently, risk collision"]
        D3["Option C: Merge into shared session"]
    end

    classDef question fill:#e3f2fd,stroke:#1565c0,stroke-width:2px
    classDef option fill:#f5f5f5,stroke:#666,stroke-width:1px
    class Q1 question
    class S1,S2,S3,D1,D2,D3 option"""

    # Browser behavior diagram
    browser_diagram = """graph LR
    subgraph NONOVERLAP["Non-overlapping paths: works"]
        direction TB
        R1_NO["Rule 1: /cart<br/>cookie: session=ABC, Path=/cart"]
        R2_NO["Rule 2: /checkout<br/>cookie: session=XYZ, Path=/checkout"]
        BROWSER_NO["Browser keeps 2 separate cookies<br/>(different path = different identity)"]
        R1_NO --> BROWSER_NO
        R2_NO --> BROWSER_NO
    end

    subgraph OVERLAP["Overlapping paths: broken"]
        direction TB
        R1_OV["Rule 1: /api<br/>cookie: session=ABC, Path=/api"]
        R2_OV["Rule 2: /api/v2<br/>cookie: session=XYZ, Path=/api/v2"]
        BROWSER_OV["Request to /api/v2:<br/>Cookie: session=ABC; session=XYZ<br/>Server receives 2 values, undefined behavior"]
        R1_OV --> BROWSER_OV
        R2_OV --> BROWSER_OV
    end

    classDef ok fill:#e8f5e9,stroke:#2e7d32,stroke-width:2px
    classDef broken fill:#ffebee,stroke:#c62828,stroke-width:2px
    class R1_NO,R2_NO,BROWSER_NO ok
    class R1_OV,R2_OV,BROWSER_OV broken"""

    # Prior art table — split into native APIs (prior art) and GWAPI implementations
    prior_art = """
                <tr class="cap-group-separator"><td colspan="5"><em>Native APIs (prior art — pre-dates Gateway API session persistence)</em></td></tr>
                <tr>
                    <td><strong>Contour</strong></td>
                    <td><code>HTTPProxy</code></td>
                    <td>Always collides</td>
                    <td>Cookie name hardcoded to <code>X-Contour-Session-Affinity</code> with <code>Path=/</code>.
                        Users cannot configure the name. All routes with cookie affinity share the same
                        cookie — if two routes target different services, the cookie value gets overwritten
                        on each navigation. Open request for configurable names
                        (<a href="https://github.com/projectcontour/contour/issues/2856" target="_blank">#2856</a>,
                        since 2020).</td>
                    <td><a href="https://github.com/projectcontour/contour/blob/main/internal/dag/policy.go#L717" target="_blank">source</a></td>
                </tr>
                <tr>
                    <td><strong>Istio</strong></td>
                    <td><code>DestinationRule</code></td>
                    <td>No detection</td>
                    <td>User configures cookie name via <code>httpCookie.name</code>. No validation for
                        duplicate names across DestinationRules.</td>
                    <td><a href="https://istio.io/latest/docs/reference/config/networking/destination-rule/#LoadBalancerSettings-ConsistentHashLB" target="_blank">docs</a></td>
                </tr>
                <tr>
                    <td><strong>HAProxy</strong></td>
                    <td><code>cookie</code> directive</td>
                    <td>Structurally prevented</td>
                    <td>One <code>cookie</code> directive per backend section. Two backends can use the
                        same cookie name, but HAProxy does not detect this — operator's responsibility.
                        Docs warn to "choose a name which does not conflict with any likely application cookie."</td>
                    <td><a href="https://docs.haproxy.org/3.0/configuration.html#4-cookie" target="_blank">docs</a></td>
                </tr>
                <tr>
                    <td><strong>Traefik</strong></td>
                    <td><code>sticky.cookie</code></td>
                    <td>Warns in docs</td>
                    <td>Cookie name is per-service load balancer. With chained WRR, each level needs a distinct
                        name. Docs recommend unique names; duplicates cause "garbled" cookie values.</td>
                    <td><a href="https://doc.traefik.io/traefik/routing/services/#sticky-sessions" target="_blank">docs</a></td>
                </tr>
                <tr>
                    <td><strong>Kong</strong></td>
                    <td><code>KongUpstreamPolicy</code></td>
                    <td>No detection</td>
                    <td>Cookie name configured per upstream via <code>hash_on_cookie</code>. No cross-upstream
                        validation. Two upstreams with same cookie name on same hostname would conflict
                        in the browser.</td>
                    <td><a href="https://github.com/Kong/kong/blob/master/kong/db/schema/entities/upstreams.lua#L194" target="_blank">source</a></td>
                </tr>
                <tr class="cap-group-separator"><td colspan="5"><em>Gateway API Implementations (implementing GEP-1619 experimental)</em></td></tr>
                <tr>
                    <td><strong>Envoy Gateway</strong></td>
                    <td>GWAPI <code>sessionPersistence</code></td>
                    <td>No detection</td>
                    <td>Auto-generates unique names when user omits <code>sessionName</code> (based on
                        route namespace/name/rule index). When user specifies a name, no cross-rule or
                        cross-route collision check.</td>
                    <td><a href="https://github.com/envoyproxy/gateway" target="_blank">repo</a></td>
                </tr>
                <tr>
                    <td><strong>NGINX Gateway Fabric</strong></td>
                    <td>GWAPI <code>sessionPersistence</code></td>
                    <td>No detection (planned)</td>
                    <td>Auto-generates unique key per rule (<code>sp_{routeName}_{ns}_{idx}</code>). No
                        duplicate detection for user-specified names today. Tracking addition of
                        <code>PartiallyInvalid</code> rejection.</td>
                    <td><a href="https://github.com/nginx/nginx-gateway-fabric/issues/4571" target="_blank">#4571</a></td>
                </tr>
                <tr>
                    <td><strong>kgateway</strong></td>
                    <td>GWAPI <code>sessionPersistence</code></td>
                    <td>No detection</td>
                    <td>When user omits name, defaults to static strings (<code>sessionPersistence</code>
                        for cookies, <code>x-session-persistence</code> for headers). No collision check.
                        Two rules without explicit names would silently share a name.</td>
                    <td><a href="https://github.com/kgateway-dev/kgateway" target="_blank">repo</a></td>
                </tr>"""

    return f"""
    <div class="content-wrapper">
        <div class="topic-header">
            <h2>Session Name Collisions</h2>
            <p>What happens when two session persistence configurations use the same cookie or header name?
               This is one of the most complex design questions in GEP-1619, with implications for user
               experience, implementation complexity, and API portability.</p>
            <div class="topic-links">
                <strong>Related:</strong>
                <a href="https://github.com/kubernetes-sigs/gateway-api/issues/4268" target="_blank">issue #4268</a> —
                <a href="https://github.com/kubernetes-sigs/gateway-api/pull/4649" target="_blank">PR #4649</a> —
                <a href="topic-route-vs-btp.html">Route vs BTP</a>
            </div>
        </div>

        <h3>The Problem</h3>
        <p>Session persistence is configured per route rule. Multiple rules can target the same Service.
           What should happen when two rules use the same cookie name?</p>

        <div class="spec-quote">
            <p>Two possible interpretations when rules share a session name:</p>
            <ol>
                <li><strong>Per-rule scope:</strong> Each rule has its own session, even with the same name.
                    Client could hit different pods for different paths.</li>
                <li><strong>Shared scope:</strong> Same name means shared session. Client hits the same pod
                    for both paths.</li>
            </ol>
            <p class="spec-source">— <a href="https://github.com/kubernetes-sigs/gateway-api/issues/4268" target="_blank">issue #4268</a></p>
        </div>

        <h3>Where Collisions Happen</h3>
    </div>
    <div class="mermaid-container">
        <pre class="mermaid">{collision_flow}</pre>
    </div>
    <div class="content-wrapper">

        <h3>What the Browser Does with Duplicate Cookie Names</h3>
        <p>Cookie identity in the browser is the tuple <code>(name, domain, path)</code>. Same name with
           different paths creates separate cookies. Same name with overlapping paths creates
           undefined behavior per
           <a href="https://datatracker.ietf.org/doc/html/rfc6265#section-5.4" target="_blank">RFC 6265</a>.</p>
    </div>
    <div class="mermaid-container">
        <pre class="mermaid">{browser_diagram}</pre>
    </div>
    <div class="content-wrapper">

        <h3>Use Cases: Sharing and Conflicts</h3>

        <details><summary><h4>Use Case 1: Intentional Sharing (Route-Inline)</h4></summary>
        <p>Two route rules with different filters need the user pinned to the same backend pod.</p>
        <pre style="background:#f5f5f5;padding:16px;border-radius:6px;font-size:13px;margin:12px 0">kind: HTTPRoute
metadata:
  name: shop-routes
spec:
  hostnames: ["shop.example.com"]
  rules:
  - matches:
    - path: {{"type: PathPrefix, value: /cart"}}
    filters:
    - type: RequestHeaderModifier
      requestHeaderModifier: {{"set": [{{"name": "X-Flow", "value": "cart"}}]}}
    backendRefs: [{{"name": "shop-app"}}]
    sessionPersistence:
      type: Cookie
      cookie: {{"name": "shop-session"}}

  - matches:
    - path: {{"type: PathPrefix, value: /checkout"}}
    filters:
    - type: RequestHeaderModifier
      requestHeaderModifier: {{"set": [{{"name": "X-Flow", "value": "checkout"}}]}}
    backendRefs: [{{"name": "shop-app"}}]
    sessionPersistence:
      type: Cookie
      cookie: {{"name": "shop-session"}}    # same name as rule 1</pre>

        <div class="mermaid-container" style="width:100%;margin-left:0">
        <pre class="mermaid">graph LR
    R1["Rule 1 /cart<br/>cookie = shop-session"] --> SVC["Service shop-app<br/>Pods A, B, C"]
    R2["Rule 2 /checkout<br/>cookie = shop-session"] --> SVC
    classDef route fill:#fff3e0,stroke:#ff9800,stroke-width:2px
    classDef svc fill:#e3f2fd,stroke:#1565c0,stroke-width:2px
    class R1,R2 route
    class SVC svc</pre>
        </div>
        <p><strong>What happens in the browser (depends on cookie path default):</strong></p>
        <table class="comparison-table">
            <thead><tr><th></th><th>If Path=/ (default to /)</th><th>If computed path</th></tr></thead>
            <tbody>
                <tr>
                    <td><strong>Step 1</strong></td>
                    <td>User visits <code>/cart</code> → <code>Set-Cookie: shop-session=pod-A; Path=/</code></td>
                    <td>User visits <code>/cart</code> → <code>Set-Cookie: shop-session=pod-A; Path=/cart</code></td>
                </tr>
                <tr>
                    <td><strong>Step 2</strong></td>
                    <td>User visits <code>/checkout</code> → browser sends <code>Cookie: shop-session=pod-A</code>
                        (<code>Path=/</code> matches) → routes to pod-A</td>
                    <td>User visits <code>/checkout</code> → browser does NOT send cookie
                        (<code>Path=/cart</code> doesn't match <code>/checkout</code>)</td>
                </tr>
                <tr>
                    <td><strong>Result</strong></td>
                    <td><span style="color:#28a745;font-weight:600">Sessions shared.</span>
                        Same pod for both paths. But configs must match or they'll overwrite each other.</td>
                    <td><span style="color:#dc3545;font-weight:600">Sessions NOT shared.</span>
                        Different pods per path despite same cookie name.</td>
                </tr>
            </tbody>
        </table>
        <div class="callout callout-warning">
            <p>Whether route-inline sharing works depends entirely on the cookie path default.
               With <code>Path=/</code>, same name = shared. With computed path, same name = separate cookies.
               See <a href="topic-cookie-path.html">Cookie Path topic</a>.</p>
        </div>
        </details>

        <details><summary><h4>Use Case 2: Intentional Sharing (BTP)</h4></summary>
        <p>Same goal — different filters, shared session — using BackendTrafficPolicy.</p>
        <pre style="background:#f5f5f5;padding:16px;border-radius:6px;font-size:13px;margin:12px 0">kind: HTTPRoute
metadata:
  name: shop-routes
spec:
  hostnames: ["shop.example.com"]
  rules:
  - matches:
    - path: {{"type: PathPrefix, value: /cart"}}
    filters:
    - type: RequestHeaderModifier
      requestHeaderModifier: {{"set": [{{"name": "X-Flow", "value": "cart"}}]}}
    backendRefs: [{{"name": "shop-app"}}]
    # no sessionPersistence here

  - matches:
    - path: {{"type: PathPrefix, value: /checkout"}}
    filters:
    - type: RequestHeaderModifier
      requestHeaderModifier: {{"set": [{{"name": "X-Flow", "value": "checkout"}}]}}
    backendRefs: [{{"name": "shop-app"}}]
    # no sessionPersistence here
---
kind: BackendTrafficPolicy
metadata:
  name: shop-persistence
spec:
  targetRefs: [{{"kind": "Service", "name": "shop-app"}}]
  sessionPersistence:
    type: Cookie
    cookie: {{"name": "shop-session"}}</pre>

        <div class="mermaid-container" style="width:100%;margin-left:0">
        <pre class="mermaid">graph LR
    R1B["Rule 1 /cart<br/><i>no persistence</i>"] --> SVCB["Service shop-app<br/>Pods A, B, C"]
    R2B["Rule 2 /checkout<br/><i>no persistence</i>"] --> SVCB
    BTPB["BTP<br/>cookie = shop-session"] -.->|"targets"| SVCB
    classDef route fill:#fff3e0,stroke:#ff9800,stroke-width:2px
    classDef svc fill:#e3f2fd,stroke:#1565c0,stroke-width:2px
    classDef btp fill:#e8f5e9,stroke:#2e7d32,stroke-width:2px
    class R1B,R2B route
    class SVCB svc
    class BTPB btp</pre>
        </div>
        <p><strong>What happens in the browser:</strong></p>
        <ol>
            <li>User visits <code>/cart</code> → Gateway picks pod-A, responds with
                <code>Set-Cookie: shop-session=pod-A; Path=/</code></li>
            <li>User visits <code>/checkout</code> → browser sends <code>Cookie: shop-session=pod-A</code>
                (path <code>/</code> matches everything)</li>
            <li>Gateway reads cookie, routes to pod-A</li>
            <li><strong>Result: sessions ARE shared.</strong> User stays on pod-A for both paths.
                No duplicate names, no conflict resolution needed.</li>
        </ol>
        <div class="callout callout-info">
            <p>BTP shares sessions naturally. One cookie, one config, one Service. Routes stay focused
               on routing and filters.</p>
        </div>
        </details>

        <details><summary><h4>Use Case 3: Accidental Collision (Route-Inline, Path=/)</h4></summary>
        <p>Two teams independently create routes on the same hostname with the same cookie name.</p>
        <pre style="background:#f5f5f5;padding:16px;border-radius:6px;font-size:13px;margin:12px 0"># Team A (namespace: team-a)
kind: HTTPRoute
metadata:
  name: admin-route
  namespace: team-a
spec:
  hostnames: ["example.com"]
  rules:
  - matches:
    - path: {{"type: PathPrefix, value: /admin"}}
    backendRefs: [{{"name": "admin-svc"}}]
    sessionPersistence:
      type: Cookie
      cookie: {{"name": "my-session"}}
      absoluteTimeout: 1h
---
# Team B (namespace: team-b)
kind: HTTPRoute
metadata:
  name: public-route
  namespace: team-b
spec:
  hostnames: ["example.com"]
  rules:
  - matches:
    - path: {{"type: PathPrefix, value: /public"}}
    backendRefs: [{{"name": "public-svc"}}]
    sessionPersistence:
      type: Cookie
      cookie: {{"name": "my-session"}}    # same name, different timeout
      absoluteTimeout: 24h</pre>

        <div class="mermaid-container" style="width:100%;margin-left:0">
        <pre class="mermaid">graph LR
    subgraph NS_A["namespace team-a"]
        RA["HTTPRoute admin-route<br/>/admin<br/>cookie = my-session<br/>timeout 1h"]
        SA["Service admin-svc"]
        RA --> SA
    end
    subgraph NS_B["namespace team-b"]
        RB["HTTPRoute public-route<br/>/public<br/>cookie = my-session<br/>timeout 24h"]
        SB["Service public-svc"]
        RB --> SB
    end
    classDef ns fill:#f5f5f5,stroke:#999,stroke-width:2px
    classDef route fill:#fff3e0,stroke:#ff9800,stroke-width:2px
    classDef svc fill:#e3f2fd,stroke:#1565c0,stroke-width:2px
    class NS_A,NS_B ns
    class RA,RB route
    class SA,SB svc</pre>
        </div>
        <p><strong>What happens in the browser (with <code>Path=/</code>):</strong></p>
        <ol>
            <li>User visits <code>/admin</code> → responds with
                <code>Set-Cookie: my-session=admin-pod-2; Path=/; Max-Age=3600</code></li>
            <li>User visits <code>/public</code> → browser sends <code>Cookie: my-session=admin-pod-2</code>
                (<code>Path=/</code> matches everything)</li>
            <li>Public-svc's persistence logic sees a cookie encoding an admin-svc pod address — undefined behavior</li>
            <li>Gateway responds with
                <code>Set-Cookie: my-session=public-pod-1; Path=/; Max-Age=86400</code> — <strong>overwrites</strong> the cookie</li>
            <li>User goes back to <code>/admin</code> → cookie now points to public-pod-1</li>
        </ol>
        <div class="callout callout-warning">
            <p><strong>Result:</strong> Session hijacking. Each navigation overwrites the other team's cookie.
               The user bounces between pods in different Services. The timeout alternates between 1h and 24h.
               Neither team knows the other exists.</p>
        </div>
        </details>

        <details><summary><h4>Use Case 4: Config Conflict (Same Route, Same Name, Different Settings)</h4></summary>
        <p>Two rules in the same HTTPRoute use the same cookie name with different lifetime settings.</p>
        <pre style="background:#f5f5f5;padding:16px;border-radius:6px;font-size:13px;margin:12px 0">kind: HTTPRoute
metadata:
  name: file-routes
spec:
  hostnames: ["files.example.com"]
  rules:
  - matches:
    - path: {{"type: PathPrefix, value: /app/upload"}}
    backendRefs: [{{"name": "file-svc"}}]
    sessionPersistence:
      type: Cookie
      cookie:
        name: file-session
        lifetimeType: Permanent
      absoluteTimeout: 1h

  - matches:
    - path: {{"type: PathPrefix, value: /app/download"}}
    backendRefs: [{{"name": "file-svc"}}]
    sessionPersistence:
      type: Cookie
      cookie:
        name: file-session          # same name
        lifetimeType: Session       # different lifetime!</pre>

        <div class="mermaid-container" style="width:100%;margin-left:0">
        <pre class="mermaid">graph LR
    R4A["Rule 1 /app/upload<br/>cookie = file-session<br/>lifetimeType = Permanent<br/>absoluteTimeout = 1h"] --> SVC4["Service file-svc<br/>Pods A, B, C"]
    R4B["Rule 2 /app/download<br/>cookie = file-session<br/>lifetimeType = Session"] --> SVC4
    CONFLICT4["Same cookie name<br/>different lifetime!"]
    classDef route fill:#fff3e0,stroke:#ff9800,stroke-width:2px
    classDef svc fill:#e3f2fd,stroke:#1565c0,stroke-width:2px
    classDef conflict fill:#ffebee,stroke:#c62828,stroke-width:2px
    class R4A,R4B route
    class SVC4 svc
    class CONFLICT4 conflict</pre>
        </div>
        <p><strong>What happens in the browser (depends on cookie path default):</strong></p>
        <table class="comparison-table">
            <thead><tr><th></th><th>If Path=/ or computed Path=/app</th><th>If computed Path=/app/upload vs /app/download</th></tr></thead>
            <tbody>
                <tr>
                    <td><strong>Step 1</strong></td>
                    <td>User visits <code>/app/upload</code> →
                        <code>Set-Cookie: file-session=pod-A; Max-Age=3600</code></td>
                    <td>User visits <code>/app/upload</code> →
                        <code>Set-Cookie: file-session=pod-A; Path=/app/upload; Max-Age=3600</code></td>
                </tr>
                <tr>
                    <td><strong>Step 2</strong></td>
                    <td>User visits <code>/app/download</code> → browser sends cookie → routes to pod-A →
                        response overwrites with <code>Set-Cookie: file-session=pod-A</code> (no Max-Age, session cookie)</td>
                    <td>User visits <code>/app/download</code> → browser does NOT send cookie →
                        Gateway picks new pod, sets separate cookie</td>
                </tr>
                <tr>
                    <td><strong>Result</strong></td>
                    <td><span style="color:#dc3545;font-weight:600">Broken.</span> Cookie lifetime flip-flops
                        between permanent (1h) and session (deleted on browser close) on every navigation.</td>
                    <td><span style="color:#ffc107;font-weight:600">Works but misleading.</span> Two separate
                        cookies despite same name. User may expect shared session.</td>
                </tr>
            </tbody>
        </table>
        <div class="callout callout-warning">
            <p>With overlapping or shared paths, different configs on the same cookie name cause the cookie
               attributes to flip-flop. With non-overlapping computed paths, the configs don't conflict but
               the sessions aren't shared despite the same name. Neither outcome is what the user intended.</p>
        </div>
        </details>

        <details><summary><h4>Use Case 5: BTP Collision (Same Cookie Name, Different Services)</h4></summary>
        <p>Two BTPs on different Services use the same cookie name on the same hostname.</p>
        <pre style="background:#f5f5f5;padding:16px;border-radius:6px;font-size:13px;margin:12px 0">kind: BackendTrafficPolicy
metadata:
  name: cart-persistence
spec:
  targetRefs: [{{"kind": "Service", "name": "cart-svc"}}]
  sessionPersistence:
    type: Cookie
    cookie: {{"name": "my-session"}}
---
kind: BackendTrafficPolicy
metadata:
  name: account-persistence
spec:
  targetRefs: [{{"kind": "Service", "name": "account-svc"}}]
  sessionPersistence:
    type: Cookie
    cookie: {{"name": "my-session"}}    # same name, different service
---
kind: HTTPRoute
spec:
  hostnames: ["shop.example.com"]
  rules:
  - matches:
    - path: {{"type: PathPrefix, value: /cart"}}
    backendRefs: [{{"name": "cart-svc"}}]
  - matches:
    - path: {{"type: PathPrefix, value: /account"}}
    backendRefs: [{{"name": "account-svc"}}]</pre>

        <div class="mermaid-container" style="width:100%;margin-left:0">
        <pre class="mermaid">graph LR
    RT5["HTTPRoute<br/>/cart and /account"] --> CART["Service cart-svc"]
    RT5 --> ACCT["Service account-svc"]
    BTP_CART["BTP<br/>cookie = my-session"] -.->|"targets"| CART
    BTP_ACCT["BTP<br/>cookie = my-session"] -.->|"targets"| ACCT
    classDef route fill:#fff3e0,stroke:#ff9800,stroke-width:2px
    classDef svc fill:#e3f2fd,stroke:#1565c0,stroke-width:2px
    classDef btp fill:#e8f5e9,stroke:#2e7d32,stroke-width:2px
    class RT5 route
    class CART,ACCT svc
    class BTP_CART,BTP_ACCT btp</pre>
        </div>
        <p><strong>What happens in the browser (with <code>Path=/</code>):</strong></p>
        <ol>
            <li>User visits <code>/cart</code> → Gateway routes to cart-svc, picks cart-pod-1, responds with
                <code>Set-Cookie: my-session=cart-pod-1; Path=/</code></li>
            <li>User visits <code>/account</code> → browser sends <code>Cookie: my-session=cart-pod-1</code>
                (<code>Path=/</code> matches everything)</li>
            <li>Gateway routes to account-svc, but the cookie encodes a cart-svc pod address — the
                persistence logic either ignores it (falls back to LB) or tries to route to cart-pod-1
                (wrong service)</li>
            <li>Gateway responds with <code>Set-Cookie: my-session=account-pod-3; Path=/</code> — overwrites</li>
            <li>User goes back to <code>/cart</code> — cookie now points to account-pod-3</li>
        </ol>
        <div class="callout callout-warning">
            <p><strong>Result:</strong> Same collision pattern as route-inline Use Case 3. Different service
               owners can still independently pick the same cookie name. However, the collision surface is
               smaller — BTP has one config per Service, so collisions only happen between Services, not
               between individual route rules. With route-inline, every rule on every route is a potential
               collision point.</p>
        </div>
        </details>

        <h3>Design Options</h3>
        <p>Five possible approaches, each with different tradeoffs for users and implementations:</p>

        <table class="comparison-table">
            <thead><tr>
                <th>Option</th>
                <th>Description</th>
                <th>User Foot Guns</th>
                <th>Impl Complexity</th>
                <th>Session Sharing</th>
            </tr></thead>
            <tbody>
                <tr>
                    <td><strong>1. No Validation</strong></td>
                    <td>Do nothing. Let the browser sort it out.</td>
                    <td><span style="color:#dc3545">Severe</span> — overlapping paths cause silent session breakage,
                        RFC 6265 undefined behavior</td>
                    <td><span style="color:#28a745">None</span></td>
                    <td>Undefined</td>
                </tr>
                <tr>
                    <td><strong>2. Require Unique Names</strong></td>
                    <td>Reject duplicate names. CEL within xRoute, oldest-wins across xRoutes.</td>
                    <td><span style="color:#28a745">Minimal</span> — clear error at admission or via status</td>
                    <td><span style="color:#ffc107">Moderate</span> — CEL (easy) + cross-route checking (harder)</td>
                    <td>Not via route-inline. Use BTP or single rule with multiple matches.</td>
                </tr>
                <tr>
                    <td><strong>3. Tuple Conflict Detection</strong></td>
                    <td>Only reject when <code>(name, domain, path)</code> overlaps.</td>
                    <td><span style="color:#ffc107">Moderate</span> — complex rules, regex paths can't be checked</td>
                    <td><span style="color:#dc3545">Very High</span> — path overlap detection, domain computation,
                        regex handling</td>
                    <td>Allowed when tuples don't overlap</td>
                </tr>
                <tr>
                    <td><strong>4. Share Sessions</strong></td>
                    <td>Same name = shared session. Compute LCD path. Merge configs.</td>
                    <td><span style="color:#dc3545">Severe</span> — action at a distance (adding a route changes
                        existing route's path), config conflicts</td>
                    <td><span style="color:#dc3545">High</span> — LCD computation, config merging, conflict detection</td>
                    <td>Implicit via matching names</td>
                </tr>
                <tr>
                    <td><strong>5. Independent Sessions</strong></td>
                    <td>Same name creates separate cookies scoped by computed path.</td>
                    <td><span style="color:#dc3545">Severe</span> — overlapping paths cause RFC 6265 undefined behavior,
                        user confusion ("same name but not shared?")</td>
                    <td><span style="color:#28a745">Low</span></td>
                    <td>No sharing despite same name</td>
                </tr>
            </tbody>
        </table>

        <h3>The "Action at a Distance" Problem (Option 4)</h3>
        <div class="callout callout-warning">
            <p><strong>Example:</strong> HTTPRoute 1 exists with path <code>/api/upload</code> and cookie name
               <code>file-session</code>. Cookie path is <code>/api/upload</code>.</p>
            <p>User creates HTTPRoute 2 with path <code>/api/download</code> and same cookie name
               <code>file-session</code>.</p>
            <p>HTTPRoute 1's cookie path silently changes from <code>/api/upload</code> to <code>/api</code>
               (longest common denominator). HTTPRoute 1's behavior changed without being modified.</p>
        </div>

        <h3>The Config Conflict Problem (Option 4)</h3>
        <div class="callout callout-warning">
            <p><strong>Example:</strong> Two routes share cookie name <code>shop-session</code>:</p>
            <pre style="background:#f5f5f5;padding:12px;border-radius:4px;font-size:13px;margin:8px 0">Route A: sessionPersistence.cookie.lifetimeType: Permanent, absoluteTimeout: 1h
Route B: sessionPersistence.cookie.lifetimeType: Session (no timeout)</pre>
            <p>Which config wins? The cookie gets overwritten each time the user navigates between
               paths, alternating between permanent (1h expiry) and session (no expiry).</p>
        </div>

        <h3>Route-Inline vs BTP: Different Collision Surfaces</h3>
        <table class="comparison-table">
            <thead><tr>
                <th></th>
                <th>Route-Inline</th>
                <th>BackendTrafficPolicy</th>
            </tr></thead>
            <tbody>
                <tr>
                    <td><strong>Collision surface</strong></td>
                    <td>Every rule on every route attached to a Gateway</td>
                    <td>One config per Service</td>
                </tr>
                <tr>
                    <td><strong>Within-resource duplicates</strong></td>
                    <td>Possible (multiple rules in same xRoute)</td>
                    <td>Not possible (one SP config per BTP)</td>
                </tr>
                <tr>
                    <td><strong>Cross-resource duplicates</strong></td>
                    <td>Likely (multiple routes, multiple teams)</td>
                    <td>Only when two Services share a hostname</td>
                </tr>
                <tr>
                    <td><strong>Validation needed</strong></td>
                    <td>CEL (within) + controller (across) + route-vs-BTP precedence</td>
                    <td>Controller only (across BTPs on same hostname)</td>
                </tr>
            </tbody>
        </table>

        <h3>How Implementations Handle It Today</h3>
        <p>No implementation currently detects or rejects duplicate session names. The table is split
           between native APIs (prior art that pre-dates GEP-1619) and Gateway API implementations
           (which are implementing GEP-1619's experimental design and should not be used to justify
           the design itself).</p>
        <table class="comparison-table">
            <thead><tr>
                <th>Implementation</th>
                <th>API</th>
                <th>Collision Handling</th>
                <th>Details</th>
                <th>Source</th>
            </tr></thead>
            <tbody>{prior_art}</tbody>
        </table>

        <h3>The Zhaohuabing Objection</h3>
        <div class="callout callout-info">
            <p>The strongest argument against strict uniqueness (Option 2): it prevents legitimate use cases
               where routes need different filters/policies but shared session affinity to the same backend.
               For example, <code>/cart</code> and <code>/checkout</code> need different
               <code>RequestHeaderModifier</code> filters but should pin to the same pod.</p>
            <p>This can't be solved by merging into one rule (different filters require separate rules).
               But it <em>can</em> be solved by using BackendTrafficPolicy for the persistence and
               keeping the routes filter-only.
               (<a href="https://github.com/kubernetes-sigs/gateway-api/pull/4649" target="_blank">PR #4649 discussion</a>)</p>
        </div>

        <h3>Open Questions</h3>
        <ul>
            <li>Is Option 2 (unique names) too strict? Does the zhaohuabing use case warrant relaxing it?</li>
            <li>If we allow duplicates, which model — shared (Option 4) or independent (Option 5)?</li>
            <li>Should the spec define collision behavior, or leave it implementation-specific?</li>
            <li>Does the collision complexity of route-inline argue for BTP-only session persistence?
                (see <a href="topic-route-vs-btp.html">Route vs BTP</a>)</li>
        </ul>
    </div>"""


def render_route_vs_btp_page(impls: list[dict]) -> str:
    """Render a topic page comparing route-inline vs BTP attachment models."""

    # Prior art survey
    prior_art_rows = """
                <tr><td><strong>Contour</strong></td><td>Route</td>
                    <td><code>HTTPProxy</code> route-level <code>loadBalancerPolicy</code></td></tr>
                <tr><td><strong>Istio</strong></td><td>Service</td>
                    <td><code>DestinationRule</code> targets service hostname</td></tr>
                <tr><td><strong>HAProxy Ingress</strong></td><td>Service</td>
                    <td>Annotations on Service</td></tr>
                <tr><td><strong>Kong</strong></td><td>Service</td>
                    <td><code>KongUpstreamPolicy</code> targets Service</td></tr>
                <tr><td><strong>Traefik</strong></td><td>Service</td>
                    <td><code>IngressRoute</code> / <code>TraefikService</code> sticky on service LB</td></tr>
                <tr><td><strong>Cilium</strong></td><td>Service</td>
                    <td>Kubernetes Service <code>sessionAffinity</code> (L3/L4)</td></tr>
                <tr><td><strong>GKE</strong></td><td>Service</td>
                    <td><code>GCPBackendPolicy</code> targets Service</td></tr>
                <tr><td><strong>kgateway</strong></td><td>Service</td>
                    <td><code>BackendConfigPolicy</code> targets Service (for consistent hashing)</td></tr>"""

    # Decision flowchart
    decision_diagram = """graph TB
    START["API Design: Do we need both<br/>route-inline AND BTP?"]

    START -->|"Yes, keep both"| BOTH
    START -->|"No, pick one"| PICK

    subgraph BOTH["Keep Both Attachment Points"]
        direction TB
        B1["Must define precedence:<br/>route-inline overrides BTP"]
        B1 --> B2["Must handle name collisions<br/>across routes + BTPs"]
        B2 --> B3["Mesh: 3-way precedence<br/>(producer route, consumer route, BTP)"]
        B3 --> B4{"Restrict mesh to BTP-only?"}
        B4 -->|"Yes"| B5["Mesh uses BTP.<br/>North-south uses both.<br/>Reduces mesh complexity."]
        B4 -->|"No"| B6["Full 3-way precedence<br/>needed for mesh."]
    end

    subgraph PICK["Pick One Attachment Point"]
        direction TB
        PICK_Q{"Which one?"}
        PICK_Q -->|"Route-Inline only"| RI
        PICK_Q -->|"BTP only"| BTPONLY

        subgraph RI["Route-Inline Only"]
            direction TB
            RI1["Per-path scoping available"]
            RI1 --> RI2["Must solve session sharing<br/>(multi-path to same service)"]
            RI2 --> RI3["Cookie path computation<br/>needed per route"]
            RI3 --> RI4["Name collision complexity<br/>across all route rules"]
            RI4 --> RI5["Mesh: producer + consumer<br/>route precedence needed"]
        end

        subgraph BTPONLY["BTP Only"]
            direction TB
            BTP1["Session sharing works<br/>naturally (per-service)"]
            BTP1 --> BTP2["No per-path scoping<br/>(use separate Services)"]
            BTP2 --> BTP3["Cookie path: just use /"]
            BTP3 --> BTP4["Minimal collision surface<br/>(one config per Service)"]
            BTP4 --> BTP5["Mesh: clean fit, same<br/>model as north-south"]
        end
    end

    classDef start fill:#e3f2fd,stroke:#1565c0,stroke-width:2px
    classDef both fill:#fce4ec,stroke:#e91e63,stroke-width:1px
    classDef pick fill:#f5f5f5,stroke:#999,stroke-width:1px
    classDef ri fill:#fff3e0,stroke:#ff9800,stroke-width:1px
    classDef btponly fill:#e8f5e9,stroke:#2e7d32,stroke-width:1px
    classDef question fill:#e3f2fd,stroke:#1565c0,stroke-width:2px
    classDef outcome fill:#f5f5f5,stroke:#666,stroke-width:1px
    class START start
    class B1,B2,B3,RI1,RI2,RI3,RI4,RI5,BTP1,BTP2,BTP3,BTP4,BTP5,B5,B6 outcome
    class B4,PICK_Q question"""

    # Collision diagram
    collision_diagram = """graph TB
    subgraph ROUTE_MODEL["Route-Inline: Many configs, wide collision surface"]
        direction TB
        HR_A["HTTPRoute A<br/>rule 1: cookie name=my-session<br/>rule 2: cookie name=my-session"]
        HR_B["HTTPRoute B<br/>rule 1: cookie name=my-session"]
        HR_C["HTTPRoute C<br/>rule 1: cookie name=my-session"]
        COLLISION["4 potential collisions<br/>across 3 routes"]
        HR_A --> COLLISION
        HR_B --> COLLISION
        HR_C --> COLLISION
    end

    subgraph BTP_MODEL["BTP: One config per Service, minimal collisions"]
        direction TB
        SVC_A["Service A ← BTP: cookie name=session-a"]
        SVC_B["Service B ← BTP: cookie name=session-b"]
        SVC_C["Service C ← BTP: cookie name=session-a"]
        NO_COLLISION["Only collides if A and C<br/>share a hostname"]
    end

    classDef route fill:#fff3e0,stroke:#ff9800,stroke-width:2px
    classDef btp fill:#e8f5e9,stroke:#2e7d32,stroke-width:2px
    classDef warn fill:#fff3cd,stroke:#ffc107,stroke-width:2px
    classDef ok fill:#d4edda,stroke:#28a745,stroke-width:2px
    class HR_A,HR_B,HR_C route
    class SVC_A,SVC_B,SVC_C btp
    class COLLISION warn
    class NO_COLLISION ok"""

    return f"""
    <div class="content-wrapper">
        <div class="topic-header">
            <h2>Route-Inline vs BackendTrafficPolicy</h2>
            <p>Session persistence can be attached at the route rule level (per-path) or at the service
               level (per-service via BackendTrafficPolicy). This choice affects complexity, portability,
               session sharing, and mesh support.</p>
            <div class="topic-links">
                <strong>Related:</strong>
                <a href="https://github.com/kubernetes-sigs/gateway-api/discussions/4462" target="_blank">Discussion #4462</a> —
                <a href="https://github.com/kubernetes-sigs/gateway-api/pull/4649" target="_blank">PR #4649</a> —
                <a href="topic-mesh.html">Mesh Topic</a>
            </div>
        </div>

        <div class="proposals-grid">
            <div class="proposal-card" style="border-color:#ff9800">
                <h4>Route-Inline</h4>
                <p><strong>Session persistence on HTTPRoute rules</strong></p>
                <div class="proposal-pro-con">
                    <p class="pro">Per-path scoping (<code>/checkout</code> gets persistence, <code>/browse</code> doesn't)</p>
                    <p class="pro">Route author controls persistence for their traffic</p>
                    <p class="pro">More discoverable — lives where users are already configuring routes</p>
                    <p class="pro">Implementations have converged here — no implementations have shipped BTP yet</p>
                    <p class="con">Session sharing requires same rule with multiple path matches</p>
                    <p class="con">Duplicating SP config across multiple routes to the same Service</p>
                    <p class="con">Name collision complexity across routes (CEL + cross-route conflict resolution)</p>
                    <p class="con">Same session name creates implicit cross-rule linking,
                       <a href="https://github.com/kubernetes-sigs/gateway-api/issues/4268#issuecomment-2619834283" target="_blank">inconsistent with API design</a></p>
                    <p class="con">Cookie path computation needed (no prior art, breaks with edge proxy rewrites)</p>
                    <p class="con">Cookie scope can be broader than the route,
                       <a href="https://github.com/kubernetes-sigs/gateway-api/issues/4713#issuecomment-2813977919" target="_blank">affecting other namespaces</a></p>
                    <p class="con">Mesh: 3-way precedence problem (producer, consumer, BTP)</p>
                    <p class="con">Prior art: 1 implementation (Contour)</p>
                </div>
            </div>
            <div class="proposal-card" style="border-color:#2e7d32">
                <h4>BackendTrafficPolicy</h4>
                <p><strong>Session persistence targeting a Service</strong></p>
                <div class="proposal-pro-con">
                    <p class="pro">Session sharing works naturally across all routes to the Service</p>
                    <p class="pro">Minimal collision surface (one config per Service)</p>
                    <p class="pro">Cookie path is simply <code>/</code> — no computation, no edge proxy issues</p>
                    <p class="pro">Clean mesh fit (same model for north-south and east-west)</p>
                    <p class="pro">Service owner controls scope — safer for multi-tenant
                       (<a href="https://github.com/kubernetes-sigs/gateway-api/issues/4713#issuecomment-2813977919" target="_blank">ref</a>)</p>
                    <p class="pro">Cloud implementations (GKE) requested this model</p>
                    <p class="pro">Istio recommends Gateway API for gateways/waypoints over DestinationRule
                       (<a href="https://github.com/istio/api/pull/3502" target="_blank">ref</a>)</p>
                    <p class="pro">Prior art: 7 implementations</p>
                    <p class="con">No per-path scoping (all-or-nothing per Service)</p>
                    <p class="con">Per-path needs require separate Services</p>
                    <p class="con">Discoverability: users must know about a separate resource
                       (not visible from HTTPRoute alone)</p>
                    <p class="con">Policy attachment adds implementation complexity
                       (<a href="https://github.com/kubernetes-sigs/gateway-api/discussions/4462" target="_blank">discussion</a>)</p>
                    <p class="con">No implementations have shipped BTP session persistence yet</p>
                </div>
            </div>
        </div>

        <h3>The Core Question</h3>
        <p>Is session persistence a <strong>routing</strong> concern (which path gets persistence) or a
           <strong>load balancing</strong> concern (how clients reach pods within a service)?</p>
        <div class="callout callout-info">
            <p>HTTPRoute decides which <strong>Service</strong> handles a request. Session persistence
               decides which <strong>Pod</strong> within that Service handles it. These are two different
               decisions at two different layers.
               (<a href="https://github.com/kubernetes-sigs/gateway-api/pull/4649#discussion_r2104012685" target="_blank">ref</a>)</p>
        </div>

    </div>
    <div class="mermaid-container">
        <pre class="mermaid">graph TB
    subgraph RI_MODEL["Route-Inline: One Service concern, fragmented across routes"]
        direction TB
        RI_R1["Route Rule /cart<br/>cookie = shop-session<br/>timeout = 1h"]
        RI_R2["Route Rule /checkout<br/>cookie = shop-session<br/>timeout = 24h"]
        RI_R3["Route Rule /browse<br/>cookie = other-session"]
        RI_SVC["Service shop-app<br/>Pods: A, B, C<br/><i>One pod pool, three configs</i>"]
        RI_R1 -->|"config 1"| RI_SVC
        RI_R2 -->|"config 2"| RI_SVC
        RI_R3 -->|"config 3"| RI_SVC
        RI_CONFLICT["Same pods, conflicting configs.<br/>Which cookie wins?"]
    end

    subgraph BTP_MODEL2["BTP: One Service, one config"]
        direction TB
        BTP_R1["Route Rule /cart<br/>filter X-Flow=cart"]
        BTP_R2["Route Rule /checkout<br/>filter X-Flow=checkout"]
        BTP_R3["Route Rule /browse"]
        BTP_SVC["Service shop-app<br/>Pods: A, B, C"]
        BTP_POL["BTP<br/>cookie = shop-session<br/>timeout = 1h"]
        BTP_R1 --> BTP_SVC
        BTP_R2 --> BTP_SVC
        BTP_R3 --> BTP_SVC
        BTP_POL -.->|"one config"| BTP_SVC
    end

    classDef route fill:#fff3e0,stroke:#ff9800,stroke-width:1px
    classDef svc fill:#e3f2fd,stroke:#1565c0,stroke-width:2px
    classDef btp fill:#e8f5e9,stroke:#2e7d32,stroke-width:2px
    classDef conflict fill:#ffebee,stroke:#c62828,stroke-width:2px
    class RI_R1,RI_R2,RI_R3,BTP_R1,BTP_R2,BTP_R3 route
    class RI_SVC,BTP_SVC svc
    class BTP_POL btp
    class RI_CONFLICT conflict</pre>
    </div>
    <div class="content-wrapper">
        <p>Route-inline fragments a single Service's persistence config across multiple route rules, each
           potentially with different settings — creating room for conflict. BTP keeps it as one config
           attached to the Service, and routes stay focused on routing and filters.</p>

        <h3>Comparison</h3>
        <table class="comparison-table">
            <thead><tr>
                <th></th>
                <th>Route-Inline</th>
                <th>BackendTrafficPolicy</th>
            </tr></thead>
            <tbody>
                <tr>
                    <td><strong>Scope</strong></td>
                    <td>Per route rule (per-path)</td>
                    <td>Per Service (all routes to that service)</td>
                </tr>
                <tr>
                    <td><strong>Configured by</strong></td>
                    <td>Route author</td>
                    <td>Service owner</td>
                </tr>
                <tr>
                    <td><strong>Session sharing</strong></td>
                    <td>Only within same rule (multiple path matches)</td>
                    <td>Automatic across all routes to the Service</td>
                </tr>
                <tr>
                    <td><strong>Name collisions</strong></td>
                    <td>Many configs → wide collision surface. Needs CEL + cross-route conflict resolution.</td>
                    <td>One config per Service → minimal collisions.</td>
                </tr>
                <tr>
                    <td><strong>Cookie path</strong></td>
                    <td>Can be computed from route match (but <a href="topic-cookie-path.html">contentious</a>)</td>
                    <td>Set to <code>/</code> (simple, no computation needed)</td>
                </tr>
                <tr>
                    <td><strong>Mesh support</strong></td>
                    <td>Creates 3-way precedence problem (producer route, consumer route, BTP)</td>
                    <td>Clean fit — one config per service, works same as north-south</td>
                </tr>
                <tr>
                    <td><strong>Complexity</strong></td>
                    <td>CEL validation, cross-route conflict resolution, cookie path computation,
                        route-vs-BTP precedence</td>
                    <td>One config per service, no cross-resource conflicts within scope</td>
                </tr>
                <tr>
                    <td><strong>Flexibility</strong></td>
                    <td>Different persistence per path (<code>/checkout</code> vs <code>/browse</code>)</td>
                    <td>All-or-nothing per service (use separate Services for different behavior)</td>
                </tr>
                <tr>
                    <td><strong>Prior art</strong></td>
                    <td>1 implementation (Contour)</td>
                    <td>7 implementations</td>
                </tr>
            </tbody>
        </table>

        <h3>Name Collision Surface</h3>
        <p>The narrower scope of route-inline means more persistence configs, which means more
           chances for naming collisions:</p>
    </div>
    <div class="mermaid-container">
        <pre class="mermaid">{collision_diagram}</pre>
    </div>
    <div class="content-wrapper">

        <h3>Prior Art</h3>
        <p>Where do existing implementations (pre-GEP-1619) attach session persistence?</p>
        <table class="comparison-table">
            <thead><tr><th>Implementation</th><th>Attaches To</th><th>Mechanism</th></tr></thead>
            <tbody>{prior_art_rows}</tbody>
        </table>
        <p><strong>Prior art:</strong> Service-level (7) vs Route-level (1).</p>

        <h3>API Design Decision Tree</h3>
        <p>A flowchart for evaluating the attachment model design. Each path shows the
           consequences and complexity that follow from that choice.</p>
    </div>
    <div class="mermaid-container">
        <pre class="mermaid">{decision_diagram}</pre>
    </div>
    <div class="content-wrapper">

        <h3>Open Questions</h3>
        <ul>
            <li>Is the per-path scoping of route-inline valuable enough to justify the added complexity
                (CEL validation, conflict resolution, cookie path computation)?</li>
            <li>Should session persistence go Standard with both attachment points, or should one be
                deferred?</li>
            <li>If both are kept, how should mesh handle the 3-way precedence between producer routes,
                consumer routes, and BTP?
                (see <a href="topic-mesh.html">Mesh topic</a>)</li>
            <li>Can per-path persistence needs be solved by using separate Services instead of
                route-inline?</li>
        </ul>

        <div class="callout callout-warning">
            <p>Active discussion at
               <a href="https://github.com/kubernetes-sigs/gateway-api/discussions/4462" target="_blank">
               Discussion #4462</a>.</p>
        </div>
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

    # Generate topic pages
    cookie_path_content = render_cookie_path_page(impls)
    (args.output_dir / "topic-cookie-path.html").write_text(gen_html("Cookie Path", cookie_path_content))
    print(f"Generated: topic-cookie-path.html")

    name_collisions_content = render_name_collisions_page(impls)
    (args.output_dir / "topic-name-collisions.html").write_text(gen_html("Session Name Collisions", name_collisions_content))
    print(f"Generated: topic-name-collisions.html")

    mesh_content = render_mesh_topic_page(impls)
    (args.output_dir / "topic-mesh.html").write_text(gen_html("Mesh (East-West)", mesh_content))
    print(f"Generated: topic-mesh.html")

    route_btp_content = render_route_vs_btp_page(impls)
    (args.output_dir / "topic-route-vs-btp.html").write_text(gen_html("Route-Inline vs BTP", route_btp_content))
    print(f"Generated: topic-route-vs-btp.html")

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
