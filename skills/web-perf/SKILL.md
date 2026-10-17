---
name: web-perf
description: Audit, diagnose, or optimize website loading and interaction performance, Core Web Vitals, and Lighthouse performance scores using Chrome DevTools MCP or the ChatGPT App browser.
---

# Web Performance Audit

Your knowledge of web performance metrics, thresholds, and tooling APIs may be outdated. **Prefer retrieval over pre-training** when citing specific numbers or recommendations.

## Retrieval Sources

| Source | How to retrieve | Use for |
|--------|----------------|---------|
| web.dev | `https://web.dev/articles/vitals` | Core Web Vitals thresholds, definitions |
| Chrome DevTools docs | `https://developer.chrome.com/docs/devtools/performance` | Tooling APIs, trace analysis |
| Lighthouse scoring | `https://developer.chrome.com/docs/lighthouse/performance/performance-scoring` | Score weights, metric thresholds |

## FIRST: Select Available Browser Tooling

Use the browser specified by the user. Chrome DevTools MCP and the ChatGPT App browser are both valid measurement routes; the absence of Chrome DevTools MCP alone must not stop an audit or trigger a configuration request.

- With Chrome DevTools MCP, use its navigation, performance trace, and network tools below.
- With the ChatGPT App browser, initialize the available browser tool according to its own instructions and read the returned API documentation. Use only exposed, documented capabilities for navigation, interaction, timing, network inspection, and tracing. Do not assume that CDP or arbitrary JavaScript evaluation is available.
- When supported, collect browser Navigation/Resource Timing, Server-Timing, and PerformanceObserver entries for the relevant page interactions. If the browser exposes only UI operations, report observed behavior and continue code analysis; do not invent precise timings from tool-call elapsed time.
- Report unavailable metrics individually and continue the supported work. Request additional tooling only when a specific unresolved question requires it and the current browser cannot answer it. If no browser route is available, continue independent code analysis and explain which measurements remain blocked.

### Measurement Contract

Record the URL/path without sensitive parameters, deployed version when available, browser, authentication state, cache/throttling settings, foreground visibility, sample count, and metric definition. Keep cold and warm observations separate. Compare like-for-like measurements and report variation as well as the median.

Distinguish click-to-paint, selected-tab update, and data-ready time. A double-requestAnimationFrame interval is a paint approximation, not INP or proof that content has finished loading. Navigation load time is not LCP. Use metric-specific collection and aggregation before labeling a value INP, CLS, TBT, or Speed Index; unsupported metrics are unavailable, not zero. Resource Timing visibility can be restricted, so missing entries or zero sizes do not prove that no request or transfer occurred.

Keep credentials, personal response bodies, and sensitive request details out of evidence. Remove temporary observers/listeners and restore any changed browser cache or throttling settings after measurement.

### Chrome DevTools MCP Setup

If the user wants Chrome DevTools MCP setup, consult its [installation guide](https://github.com/ChromeDevTools/chrome-devtools-mcp#quick-start) and use the latest package version. Only change MCP configuration when setup is within the user's authorized scope; otherwise ask first. For clients using `command` and `args`, an example server entry is:

```json
"chrome-devtools": {
  "command": "npx",
  "args": ["-y", "chrome-devtools-mcp@latest"]
}
```

## Key Guidelines

- **Be assertive**: Verify claims by checking network requests, DOM, or codebase—then state findings definitively.
- **Verify before recommending**: Confirm something is unused before suggesting removal.
- **Quantify impact**: Use estimated savings from insights. Don't prioritize changes with 0ms impact.
- **Skip non-issues**: If render-blocking resources have 0ms estimated impact, note but don't recommend action.
- **Be specific**: Say "compress hero.png (450KB) to WebP" not "optimize images".
- **Prioritize ruthlessly**: A site with 200ms LCP and 0 CLS is already excellent—say so.

## Quick Reference (Chrome DevTools MCP)

| Task | Tool Call |
|------|-----------|
| Load page | `navigate_page(url: "...")` |
| Start trace | `performance_start_trace(autoStop: true, reload: true)` |
| Analyze insight | `performance_analyze_insight(insightSetId: "...", insightName: "...")` |
| List requests | `list_network_requests(resourceTypes: ["Script", "Stylesheet", ...])` |
| Request details | `get_network_request(reqid: <id>)` |
| A11y snapshot | `take_snapshot(verbose: true)` |

## Workflow

The concrete tool calls below describe the Chrome DevTools MCP route. For the ChatGPT App browser, perform the equivalent steps with documented capabilities; skip unavailable trace/insight APIs and state the resulting evidence limits.

Copy this checklist to track progress:

```
Audit Progress:
- [ ] Phase 1: Browser measurement (trace or supported timing APIs)
- [ ] Phase 2: Core Web Vitals analysis (includes CLS culprits)
- [ ] Phase 3: Network analysis
- [ ] Phase 4: Accessibility snapshot
- [ ] Phase 5: Codebase analysis (skip if third-party site)
```

### Phase 1: Browser Measurement

For the ChatGPT App route, navigate and reproduce the target interaction, collecting the supported timing/network evidence under the measurement contract above. For Chrome DevTools MCP:

1. Navigate to the target URL:
   ```
   navigate_page(url: "<target-url>")
   ```

2. Start a performance trace with reload to capture cold-load metrics:
   ```
   performance_start_trace(autoStop: true, reload: true)
   ```

3. Wait for trace completion, then retrieve results.

**Troubleshooting:**
- If trace returns empty or fails, verify the page loaded correctly with `navigate_page` first
- If insight names don't match, inspect the trace response to list available insights

### Phase 2: Core Web Vitals Analysis

With Chrome DevTools MCP, use `performance_analyze_insight` to extract key metrics. With the ChatGPT App browser, report only metrics supported by the collected entries and their required aggregation; do not infer trace insights from a screenshot.

**Note:** Insight names may vary across Chrome DevTools versions. If an insight name doesn't work, check the `insightSetId` from the trace response to discover available insights.

Common insight names:

| Metric | Insight Name | What to Look For |
|--------|--------------|------------------|
| LCP | `LCPBreakdown` | Time to largest contentful paint; breakdown of TTFB, resource load, render delay |
| CLS | `CLSCulprits` | Elements causing layout shifts (images without dimensions, injected content, font swaps) |
| Render Blocking | `RenderBlocking` | CSS/JS blocking first paint |
| Document Latency | `DocumentLatency` | Server response time issues |
| Network Dependencies | `NetworkRequestsDepGraph` | Request chains delaying critical resources |

Example:
```
performance_analyze_insight(insightSetId: "<id-from-trace>", insightName: "LCPBreakdown")
```

**Key thresholds (good/needs-improvement/poor):**
- TTFB: < 800ms / < 1.8s / > 1.8s
- FCP: < 1.8s / < 3s / > 3s
- LCP: < 2.5s / < 4s / > 4s
- INP: < 200ms / < 500ms / > 500ms
- TBT: < 200ms / < 600ms / > 600ms
- CLS: < 0.1 / < 0.25 / > 0.25
- Speed Index: < 3.4s / < 5.8s / > 5.8s

### Phase 3: Network Analysis

List all network requests to identify optimization opportunities:
```
list_network_requests(resourceTypes: ["Script", "Stylesheet", "Document", "Font", "Image"])
```

**Look for:**

1. **Render-blocking resources**: JS/CSS in `<head>` without `async`/`defer`/`media` attributes
2. **Network chains**: Resources discovered late because they depend on other resources loading first (e.g., CSS imports, JS-loaded fonts)
3. **Missing preloads**: Critical resources (fonts, hero images, key scripts) not preloaded
4. **Caching issues**: Missing or weak `Cache-Control`, `ETag`, or `Last-Modified` headers
5. **Large payloads**: Uncompressed or oversized JS/CSS bundles
6. **Unused preconnects**: If flagged, verify by checking if ANY requests went to that origin. If zero requests, it's definitively unused—recommend removal. If requests exist but loaded late, the preconnect may still be valuable.

For detailed request info:
```
get_network_request(reqid: <id>)
```

### Phase 4: Accessibility Snapshot

Take an accessibility tree snapshot:
```
take_snapshot(verbose: true)
```

**Flag high-level gaps:**
- Missing or duplicate ARIA IDs
- Elements with poor contrast ratios (check against WCAG AA: 4.5:1 for normal text, 3:1 for large text)
- Focus traps or missing focus indicators
- Interactive elements without accessible names

## Phase 5: Codebase Analysis

**Skip if auditing a third-party site without codebase access.**

Analyze the codebase to understand where improvements can be made.

### Detect Framework & Bundler

Search for configuration files to identify the stack:

| Tool | Config Files |
|------|--------------|
| Webpack | `webpack.config.js`, `webpack.*.js` |
| Vite | `vite.config.js`, `vite.config.ts` |
| Rollup | `rollup.config.js`, `rollup.config.mjs` |
| esbuild | `esbuild.config.js`, build scripts with `esbuild` |
| Parcel | `.parcelrc`, `package.json` (parcel field) |
| Next.js | `next.config.js`, `next.config.mjs` |
| Nuxt | `nuxt.config.js`, `nuxt.config.ts` |
| SvelteKit | `svelte.config.js` |
| Astro | `astro.config.mjs` |

Also check `package.json` for framework dependencies and build scripts.

### Tree-Shaking & Dead Code

- **Webpack**: Check for `mode: 'production'`, `sideEffects` in package.json, `usedExports` optimization
- **Vite/Rollup**: Tree-shaking enabled by default; check for `treeshake` options
- **Look for**: Barrel files (`index.js` re-exports), large utility libraries imported wholesale (lodash, moment)

### Unused JS/CSS

- Check for CSS-in-JS vs. static CSS extraction
- Look for PurgeCSS/UnCSS configuration (Tailwind's `content` config)
- Identify dynamic imports vs. eager loading

### Polyfills

- Check for `@babel/preset-env` targets and `useBuiltIns` setting
- Look for `core-js` imports (often oversized)
- Check `browserslist` config for overly broad targeting

### Compression & Minification

- Check for `terser`, `esbuild`, or `swc` minification
- Look for gzip/brotli compression in build output or server config
- Check for source maps in production builds (should be external or disabled)

## Output Format

Present findings as:

1. **Core Web Vitals Summary** - Table with metric, value, and rating (good/needs-improvement/poor)
2. **Top Issues** - Prioritized list of problems with estimated impact (high/medium/low)
3. **Recommendations** - Specific, actionable fixes with code snippets or config changes
4. **Codebase Findings** - Framework/bundler detected, optimization opportunities (omit if no codebase access)
