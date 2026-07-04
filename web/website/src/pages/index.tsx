import {useState} from 'react';
import Link from '@docusaurus/Link';
import Layout from '@theme/Layout';
import Heading from '@theme/Heading';
import styles from './index.module.css';

const stats = [
  {value: '8', label: 'Code systems'},
  {value: '1.1M', label: 'Codes indexed'},
  {value: '2.8M', label: 'Associations'},
  {value: '429', label: 'Tests passing'},
];

const snippets = [
  {
    title: 'Lookup',
    code: `import medterm4ds as mt

terms = mt.connect("/path/to/umls.duckdb")
info = terms.lookup("SNOMEDCT_US", "44054006")
print(info.name)
# → "Type 2 diabetes mellitus"

friendly = terms.patient_friendly("SNOMEDCT_US", "44054006")
print(friendly.name)
# → "Diabetes Type 2"`,
    desc: 'Look up any code and get its display name, properties, and patient-friendly name.',
  },
  {
    title: 'Search',
    code: `import medterm4ds as mt

# Lexical: BM25 token matching (~1ms)
results = mt.search("diabetes", mode="lexical")

# Semantic: catches novel phrasings (~100ms)
results = mt.search("high blood sugar", mode="semantic")
# → Hyperglycemia (0.80, probable)

# Hybrid: best accuracy (~110ms)
results = mt.search("metformin pill", mode="hybrid")
# → Metformin Pill (1.00, certain)`,
    desc: 'Search for medical codes by natural language. BM25 for known terms, SapBERT embeddings for novel phrasings.',
  },
  {
    title: 'Extract',
    code: `import medterm4ds as mt

concepts = mt.extract(
    "65yo M with T2DM on metformin. No CKD.",
    format="codes",
    categories=["condition", "medication"],
)
for c in concepts:
    print(f"  {c.code} {c.display} matched='{c.matched_text}'")
# → 44054006  Type 2 diabetes  matched='T2DM'
# → 860975    Metformin        matched='metformin'
# CKD excluded — negated by ConText`,
    desc: 'Extract coded concepts from clinical notes. NER + ConText negation filtering + code resolution.',
  },
  {
    title: 'FHIR Server',
    code: `pip install medterm4ds[fhir]
python -m medterm4ds.apps.fhir_api

# 7 FHIR R4 operations:
# $lookup, $validate-code, $translate,
# $subsumes, $expand, $closure, $search

curl "http://127.0.0.1:8001/fhir/CodeSystem/\\$lookup?\\
system=http://snomed.info/sct&code=44054006"`,
    desc: 'Full FHIR R4 terminology server with 7 standard operations plus custom $search.',
  },
];

const features = [
  {
    icon: '\u{1F4D6}',
    title: 'Built from UMLS',
    body: '8 clinical code systems (SNOMED CT, ICD-10, RxNorm, LOINC, CPT, HCPCS, CVX, ICD-10-PCS). Hierarchy, mappings, and patient-friendly names for 1.1M codes.',
  },
  {
    icon: '\u{1F50D}',
    title: 'Intelligent Search',
    body: 'Text-to-code inference with BM25 (lexical, ~1ms) and fine-tuned SapBERT embeddings (semantic, ~100ms). Catches "high blood sugar" → Hyperglycemia.',
  },
  {
    icon: '\u{1F4DD}',
    title: 'Text Extraction',
    body: 'Extract coded medical concepts from clinical notes. NER + medspaCy ConText (negation filtering) + code resolution. ~250ms/note on CPU.',
  },
  {
    icon: '\u{1F5C4}\u{FE0F}',
    title: 'Four Interfaces',
    body: 'Python library, CLI, MCP server (37+ tools), and FHIR R4 terminology server. All backed by the same engine.',
  },
  {
    icon: '\u{1F476}',
    title: 'Patient-Friendly',
    body: '1.1M codes resolved to consumer-comprehensible display names. MEDLINEPLUS and CHV preferred, with hierarchy fallback.',
  },
  {
    icon: '✅',
    title: 'FHIR R4 Conformant',
    body: '7 standard terminology operations ($lookup, $validate-code, $translate, $subsumes, $expand, $closure). Validated against HAPI FHIR reference server.',
  },
];

function InteractiveSnippet() {
  const [active, setActive] = useState(0);
  const snippet = snippets[active];

  return (
    <section className={styles.section}>
      <div className="container">
        <div className="row">
          <div className="col col--5">
            <Heading as="h2">Five capabilities, one engine</Heading>
            <p className={styles.sectionLead}>
              medterm4ds handles the full terminology workflow: look up codes,
              walk hierarchies, map between systems, search by natural language,
              and extract concepts from clinical text. No external API calls needed.
            </p>
            <div className={styles.snippetNav || ''} style={{display: 'flex', gap: '0.5rem', flexWrap: 'wrap', marginBottom: '1rem'}}>
              {snippets.map((s, i) => (
                <button
                  key={s.title}
                  onClick={() => setActive(i)}
                  style={{
                    padding: '0.5rem 1rem',
                    border: '1px solid rgba(84, 198, 255, 0.34)',
                    background: i === active ? 'rgba(142, 230, 193, 0.15)' : 'transparent',
                    color: i === active ? '#8ee6c1' : '#94a8bd',
                    borderRadius: '6px',
                    cursor: 'pointer',
                    fontSize: '0.9rem',
                    fontWeight: 600,
                  }}
                >
                  {s.title}
                </button>
              ))}
            </div>
            <p style={{color: '#a9bad0', fontSize: '0.95rem'}}>{snippet.desc}</p>
          </div>
          <div className="col col--7">
            <div className={styles.codePanel}>
              <div className={styles.codeHeader}>
                <span className={styles.dot} style={{background: '#ff5f56'}} />
                <span className={styles.dot} style={{background: '#ffbd2e'}} />
                <span className={styles.dot} style={{background: '#27c93f'}} />
                <span className={styles.codeLabel}>python</span>
              </div>
              <pre>
                <code>{snippet.code}</code>
              </pre>
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}

function FeatureCard({icon, title, body}) {
  return (
    <div className={styles.feature}>
      <div style={{fontSize: '2rem', marginBottom: '0.75rem'}}>{icon}</div>
      <Heading as="h3">{title}</Heading>
      <p>{body}</p>
    </div>
  );
}

export default function Home() {
  return (
    <Layout>
      {/* Hero */}
      <header className={styles.hero}>
        <div className={styles.grid} />
        <div className={`container ${styles.heroInner}`}>
          <span className={styles.badge}>medterm4ds v0.0.1 · UMLS 2026AA</span>
          <Heading as="h1" className={styles.title}>
            Medical Terminology<br />for Data Science
          </Heading>
          <p className={styles.lead}>
            UMLS-powered terminology lookup, intelligent text search, and clinical
            text extraction. Built from the ground up for Python, CLI, MCP, and FHIR R4.
          </p>
          <div className={styles.actions}>
            <Link className={styles.primaryAction} to="/docs/getting-started/quickstart">
              Get Started
            </Link>
            <a className={styles.secondaryAction} href="https://github.com/fhir4ds/medterm4ds">
              GitHub →
            </a>
          </div>
        </div>
      </header>

      {/* Stats */}
      <section className={styles.stats}>
        <div className="container">
          <div className={styles.statsRow}>
            {stats.map((s) => (
              <div key={s.label} className={styles.stat}>
                <span className={styles.statValue}>{s.value}</span>
                <span className={styles.statLabel}>{s.label}</span>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* Interactive snippets */}
      <InteractiveSnippet />

      {/* Features */}
      <section className={styles.sectionAlt}>
        <div className="container">
          <Heading as="h2">Why medterm4ds?</Heading>
          <div className={styles.featureGrid}>
            {features.map((f) => (
              <FeatureCard key={f.title} {...f} />
            ))}
          </div>
        </div>
      </section>

      {/* Architecture pipeline */}
      <section className={styles.section}>
        <div className="container">
          <Heading as="h2">How it works</Heading>
          <p className={styles.sectionLead}>
            UMLS Metathesaurus data is loaded into a DuckDB engine.
            Service functions provide the API. Four surfaces wrap the same services.
          </p>
          <div className={styles.pipeline}>
            <div className={styles.step}>UMLS Data</div>
            <div className={styles.step}>DuckDB Engine</div>
            <div className={styles.step}>Services</div>
            <div className={styles.step}>Interfaces</div>
            <div className={styles.step}>Your App</div>
          </div>
        </div>
      </section>
    </Layout>
  );
}
