import Link from '@docusaurus/Link';
import Layout from '@theme/Layout';
import Heading from '@theme/Heading';
import styles from './index.module.css';

const stats = [
  {value: '2026AA', label: 'UMLS release verified'},
  {value: '8', label: 'Core vocabularies'},
  {value: '~1.1M', label: 'Active source codes'},
  {value: '7.1GB', label: 'Local DuckDB build'},
];

const features = [
  {
    title: 'One terminology service layer',
    body: 'Lookup, patient-friendly names, mapping, hierarchy, discovery, optimize, bulk exports, API, and MCP share the same service layer.',
  },
  {
    title: 'Local-first when it matters',
    body: 'The local DuckDB engine keeps terminology workflows on the workstation while preserving fast startup and bounded memory profiles.',
  },
  {
    title: 'Explainable mappings',
    body: 'Mapping results preserve match type, depth, route, source, target, and review flags so downstream teams can audit how names and crosswalks were produced.',
  },
  {
    title: 'Data science ergonomics',
    body: 'Python helpers return model objects or DataFrames, and CLI outputs can be JSON, JSONL, CSV, FHIR ConceptMap, compact tables, or ASCII trees.',
  },
  {
    title: 'Historical code handling',
    body: 'Resolution supports active, obsolete, replacement, and NDC-to-RxCUI paths so historical data does not fall out of the pipeline.',
  },
  {
    title: 'Production-scale exports',
    body: 'Bulk workflows stream source inventories through shared checkpointing and output writers for ConceptMaps and patient-friendly datasets.',
  },
];

const quickstart = `import medterm4ds as mt

terms = mt.connect(
    "/mnt/d/medterm4ds/data/umls_current.duckdb",
    memory_profile="low",
)

friendly = terms.patient_friendly_df("ICD10CM", ["E11.9", "E11.40"])
mapping = terms.map_df(
    "ICD10CM",
    ["E11.9"],
    target_sources=["SNOMEDCT_US"],
)

friendly[["source", "code", "name", "match_type", "match_depth"]]`;

export default function Home() {
  return (
    <Layout
      title="Medical Terminology for Data Science"
      description="medterm4ds documentation for UMLS-backed terminology lookup, mapping, patient-friendly names, valuesets, and MCP tools">
      <header className={styles.hero}>
        <div className={styles.grid} />
        <div className={styles.heroInner}>
          <span className={styles.badge}>Python package: medterm4ds</span>
          <Heading as="h1" className={styles.title}>
            Medical Terminology for Data Science
          </Heading>
          <p className={styles.lead}>
            UMLS-backed terminology lookup, mapping, value set optimization,
            patient-friendly names, and interoperability outputs for Python
            notebooks and data science workflows.
          </p>
          <div className={styles.actions}>
            <Link className={styles.primaryAction} to="/docs/getting-started/quickstart">
              Start with a code
            </Link>
            <Link className={styles.secondaryAction} to="/docs/workflows/valuesets">
              Optimize valuesets
            </Link>
            <Link className={styles.secondaryAction} to="/docs/user-guide/interfaces/mcp-server">
              Use MCP tools
            </Link>
          </div>
        </div>
      </header>

      <section className={styles.stats}>
        <div className="container">
          <div className={styles.statsRow}>
            {stats.map(stat => (
              <div className={styles.stat} key={stat.label}>
                <span className={styles.statValue}>{stat.value}</span>
                <span className={styles.statLabel}>{stat.label}</span>
              </div>
            ))}
          </div>
        </div>
      </section>

      <main>
        <section className={styles.section}>
          <div className="container">
            <Heading as="h2">Built around the terminology work people actually do</Heading>
            <p className={styles.sectionLead}>
              The project keeps broad terminology tool coverage, but organizes
              behavior around reusable services instead of separate local, API,
              bulk, CLI, and MCP implementations.
            </p>
            <div className={styles.featureGrid}>
              {features.map(feature => (
                <article className={styles.feature} key={feature.title}>
                  <Heading as="h3">{feature.title}</Heading>
                  <p>{feature.body}</p>
                </article>
              ))}
            </div>
          </div>
        </section>

        <section className={styles.sectionAlt}>
          <div className="container">
            <Heading as="h2">Same primitives, every interface</Heading>
            <p className={styles.sectionLead}>
              Python, CLI, API, MCP, and bulk exports all call the same lookup,
              mapping, hierarchy, optimize, resolution, and patient-friendly
              service functions. Python notebooks are the primary interface;
              CLI commands are available for file exports and automation.
            </p>
            <div className={styles.codePanel}>
              <div className={styles.codeHeader}>
                <span className={styles.dot} style={{background: '#ff5f56'}} />
                <span className={styles.dot} style={{background: '#ffbd2e'}} />
                <span className={styles.dot} style={{background: '#27c93f'}} />
                <span className={styles.codeLabel}>python</span>
              </div>
              <pre>
                <code>{quickstart}</code>
              </pre>
            </div>
          </div>
        </section>

        <section className={styles.section}>
          <div className="container">
            <Heading as="h2">A compact terminology pipeline</Heading>
            <p className={styles.sectionLead}>
              Download UMLS, build a compact DuckDB database, run source-aware
              services, export evidence-rich outputs, and keep quality gates
              close to real terminology data.
            </p>
            <div className={styles.pipeline}>
              <div className={styles.step}>UMLS release</div>
              <div className={styles.step}>Local DuckDB</div>
              <div className={styles.step}>Shared services</div>
              <div className={styles.step}>CLI / API / MCP</div>
              <div className={styles.step}>ConceptMap / DataFrame</div>
            </div>
          </div>
        </section>
      </main>
    </Layout>
  );
}
