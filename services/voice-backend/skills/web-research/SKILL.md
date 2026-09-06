---
name: Web research
description: Research current facts with private SearXNG and verify them against fetched primary sources.
version: 1
---
# Web research

Use this skill when an answer depends on current, uncertain, or source-backed
information.

1. Search only with `web_search`; it is backed by the configured private
   SearXNG instance.
2. Prefer official or primary sources. Use `web_fetch` on promising URLs rather
   than relying only on result snippets.
3. Compare publication dates with the date an event actually happened.
4. For consequential claims, confirm with a second independent source when
   practical.
5. Clearly separate sourced facts from your inference. Include compact source
   links in the displayed answer.
6. Never substitute a public search API or a shell command for `web_search`.
