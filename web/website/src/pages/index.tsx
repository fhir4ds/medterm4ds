import {useState} from 'react';
import Link from '@docusaurus/Link';
import Layout from '@theme/Layout';
import Heading from '@theme/Heading';
import styles from './index.module.css';

const snippets = [
  {
    title: 'Lookup',
    code: `import medterm4ds as mt

terms = mt.connect("/path/to/umls.duckdb")
info = terms.lookup("SNOMEDCT_US", "44054006")
print(info.name)
# → "Type 2 diabetes mellitus"

# Any code system, same API
info = terms.lookup("RXNORM", "860975")
print(info.name)
# → "Metformin Oral Product"`,
    desc: 'Look up any code and get its display name, properties, and metadata.',
  },
  {
    title: 'Hierarchy',
    code: `import medterm4ds as mt

terms = mt.connect("/path/to/umls.duckdb")

# Walk up: find all ancestors of T2DM
ancestors = terms.ancestors("SNOMEDCT_US", "44054006")
# → Diabetes mellitus, Endocrine disorder, ...

# Walk down: find all descendants
descendants = terms.descendants("SNOMEDCT_US", "73211009", max_depth=3)
# → T2DM, T1DM, secondary diabetes, ...

# Test subsumption
terms.subsumes("SNOMEDCT_US", "73211009", "44054006")
# → True (DM subsumes T2DM)`,
    desc: 'Traverse SNOMED CT, ICD-10, ATC, and LOINC hierarchies. Find ancestors, descendants, and subsumption relationships.',
  },
  {
    title: 'Crosswalk',
    code: `import medterm4ds as mt

terms = mt.connect("/path/to/umls.duckdb")

# Map SNOMED to ICD-10
mappings = terms.map("SNOMEDCT_US", "44054006",
                     target_sources=["ICD10CM"])
# → E11 (Type 2 diabetes mellitus, equivalent)

# Map RxNorm to ATC
mappings = terms.map("RXNORM", "860975",
                     target_sources=["ATC"])
# → A10BA02 (metformin, equivalent)

# Any-to-any via shared UMLS concepts`,
    desc: 'Translate between code systems. SNOMED to ICD-10, RxNorm to ATC, any-to-any.',
  },
  {
    title: 'Search',
    code: `import medterm4ds as mt

# Fast keyword search
results = mt.search("diabetes", mode="lexical")

# Semantic: catches novel phrasings
results = mt.search("high blood sugar", mode="semantic")
# → Hyperglycemia (probable)

# Best accuracy: combined approach
results = mt.search("metformin pill", mode="hybrid")
# → Metformin Pill (certain)`,
    desc: 'Search for medical codes by natural language. Fast keyword matching, semantic understanding for novel phrasings, or both.',
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
# CKD excluded — negated by clinical context`,
    desc: 'Extract coded concepts from clinical text. Identifies conditions, medications, labs, and procedures — with negation and context awareness.',
  },
  {
    title: 'Patient-Friendly',
    code: `import medterm4ds as mt

terms = mt.connect("/path/to/umls.duckdb")

# Get consumer-comprehensible names
friendly = terms.patient_friendly("SNOMEDCT_US", "44054006")
# → "Diabetes Type 2"

friendly = terms.patient_friendly("ICD10CM", "E11.9")
# → "Diabetes Type 2"

# 1M+ codes resolved to plain language`,
    desc: 'Translate clinical terminology into plain language for patients and consumers.',
  },
];

const features = [
  {
    icon: '🔌',
    title: 'One Engine, Four Surfaces',
    body: 'Python library, CLI, MCP server (38 tools), and FHIR R4 terminology server. Same API, same data — use whichever surface fits your workflow.',
  },
  {
    icon: '🌲',
    title: 'Walk Any Hierarchy',
    body: 'Traverse SNOMED CT, ICD-10-CM, ATC, LOINC, and more. Find ancestors, descendants, test subsumption, navigate multi-level taxonomies.',
  },
  {
    icon: '🔀',
    title: 'Crosswalk Between Systems',
    body: 'Map SNOMED to ICD-10, RxNorm to ATC, LOINC to SNOMED — any-to-any translation via shared UMLS concepts with equivalence values.',
  },
  {
    icon: '🔍',
    title: 'Intelligent Search',
    body: 'Find medical codes by natural language. Four modes: fast keyword, semantic understanding, combined, and canonical anchor resolution.',
  },
  {
    icon: '📝',
    title: 'Clinical Text Extraction',
    body: 'Extract coded concepts from clinical notes. Handles negation ("no evidence of..."), historical context, family history, and lab-vs-medication disambiguation.',
  },
  {
    icon: '👶',
    title: 'Patient-Friendly Names',
    body: '1M+ clinical codes resolved to consumer-comprehensible display names. "E11.9" becomes "Diabetes Type 2" for patient-facing interfaces.',
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
            <Heading as="h2">Six capabilities, one engine</Heading>
            <p className={styles.sectionLead}>
              medterm4ds handles the full terminology workflow: look up codes,
              walk hierarchies, crosswalk between systems, search by natural
              language, get patient-friendly names, and extract concepts from
              clinical text. No external API calls needed.
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
          <span className={styles.badge}>medterm4ds v0.0.2 · UMLS 2026AA</span>
          <Heading as="h1" className={styles.title}>
            Medical Terminology<br />for Data Science
          </Heading>
          <p className={styles.lead}>
            Walk hierarchies. Crosswalk between systems. Search by natural
            language. Extract from clinical notes. One engine, four surfaces.
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
    </Layout>
  );
}
