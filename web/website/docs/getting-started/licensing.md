---
title: Licensing
---

Medical Terminology for Data Science code is licensed under the GNU General Public License version 3.0 only (`GPL-3.0-only`).

Terminology data is separate from the software. UMLS data requires a UMLS Metathesaurus License and a UMLS Terminology Services account.

Start here:

- [How to License and Access UMLS Data](https://www.nlm.nih.gov/databases/umls.html)
- [UMLS FAQ](https://www.nlm.nih.gov/research/umls/faq_main.html)
- [UTS sign-up](https://uts.nlm.nih.gov/uts/signup-login)
- [UMLS API authentication and API key instructions](https://documentation.uts.nlm.nih.gov/rest/authentication.html)

High-level process:

1. Create or sign in with an accepted identity provider on UTS.
2. Accept the UMLS Metathesaurus License terms.
3. Submit the license request form.
4. Wait for NLM approval.
5. Find your API key in your UTS profile.
6. Use `UMLS_API_KEY` with `mt.download_umls_release(...)` or `medterm4ds data download`.

Practical guidance:

- Do not commit derived DuckDB files to a public repository.
- Keep `data/` ignored in git.
- Treat exported terminology artifacts according to source vocabulary licenses.
- Document the source release used for downstream value sets and ConceptMaps.

Some included vocabularies may have additional licensing or reporting requirements depending on geography and use case. Review the relevant source vocabulary terms before redistributing derived artifacts.
