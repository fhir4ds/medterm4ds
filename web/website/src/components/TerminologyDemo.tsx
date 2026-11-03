import React, {useCallback, useEffect, useMemo, useRef, useState} from 'react';
import {
  FhirParam,
  fhirParams,
  lookupSummary,
  searchRows,
  SearchRow,
  translateMatches,
} from './terminologyFhir';

/**
 * Live terminology demo backed by the fhir4ds terminology-demo-server HF
 * Space (https://fhir4ds-terminology-demo-server.hf.space), which serves
 * the REAL medterm4ds FHIR R4 terminology API. All operations below are
 * native FHIR R4 operations using FHIR Parameters request bodies:
 *
 *  - compare search: POST /fhir/CodeSystem/$search for ALL FOUR modes at
 *    once (lexical | semantic | canonical | hybrid), top 3 each, best
 *    result per mode bolded — response is a FHIR Bundle searchset with
 *    ranking (score + match-grade extension) on each ENTRY's `search`
 *    element. Canonical entries' display IS the patient-friendly name.
 *  - annotations: POST /fhir/CodeSystem/$extract — FHIR Bundle whose
 *    entries carry matched_text, span_start/end, ner_label (type),
 *    status and canonical_id; rendered as an annotation viewer over the
 *    note, with a per-term canonical/patient-friendly match (canonical
 *    $search) and an ICD-10-CM mapping (ConceptMap/$translate).
 *  - code detail: POST /fhir/CodeSystem/$lookup on the first found
 *    concept.
 *
 * Startup gating: the form stays disabled until the Space is actually
 * usable. GET /health is polled every 2.5 s (5 s timeout per attempt —
 * the first request wakes a sleeping container and may hang, so
 * timeout-then-retry is the expected path, not an error). The gate is on
 * search_ready (models loaded), not db_ready. Any 503 is treated as
 * not-ready and re-enters the same polling loop.
 */

const SPACE_URL = 'https://fhir4ds-terminology-demo-server.hf.space';

const HEALTH_POLL_INTERVAL_MS = 2500;
const FETCH_TIMEOUT_MS = 5000;
const GIVE_UP_AFTER_MS = 150000; // ~2.5 min of consecutive failures
const COMPARE_TOP = 3; // results per search mode
const MAX_TERM_DETAILS = 3; // concepts enriched with canonical + mapping

const SEARCH_MODES = [
  {id: 'lexical', label: 'Lexical', hint: 'exact words'},
  {id: 'semantic', label: 'Semantic', hint: 'meaning'},
  {id: 'canonical', label: 'Canonical', hint: 'grouped codes'},
  {id: 'hybrid', label: 'Hybrid', hint: 'lexical + semantic'},
] as const;

const GRADE_STYLES: Record<string, string> = {
  certain: 'badge badge--success',
  exact: 'badge badge--success',
  probable: 'badge badge--info',
  possible: 'badge badge--warning',
};

const STATUS_STYLES: Record<string, string> = {
  affirmed: 'badge badge--success',
  family: 'badge badge--info',
  historical: 'badge badge--warning',
  negated: 'badge badge--danger',
  uncertain: 'badge badge--warning',
};

type ExtractConcept = {
  code?: string;
  source?: string;
  system?: string;
  display?: string;
  matched_text?: string;
  status?: string;
  ner_label?: string;
  result_type?: string;
  canonical_id?: string;
  span_start?: number;
  span_end?: number;
};

type TermDetail = {
  concept: ExtractConcept;
  patientFriendly?: string;
  canonicalId?: string;
  icd10?: string;
};

type StartupStatus = 'checking' | 'starting' | 'ready' | 'failed';

type SearchState =
  | {phase: 'idle'}
  | {phase: 'loading'}
  | {phase: 'error'; message: string}
  | {phase: 'done'};

type ExtractState = {phase: 'idle' | 'loading'} | {phase: 'error'; message: string};

function fetchWithTimeout(url: string, init?: RequestInit): Promise<Response> {
  const ac = new AbortController();
  const timer = setTimeout(() => ac.abort(), FETCH_TIMEOUT_MS);
  return fetch(url, {...init, signal: ac.signal}).finally(() => clearTimeout(timer));
}

async function postSearch(query: string, mode: string, count: number): Promise<SearchRow[]> {
  const res = await fetch(`${SPACE_URL}/fhir/CodeSystem/$search`, {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: fhirParams([
      ['query', query],
      ['searchMode', mode],
      ['count', count],
    ]),
  });
  if (!res.ok) return [];
  return searchRows(await res.json());
}

/**
 * Polls GET /health until the Space reports search_ready (models loaded),
 * then confirms once with GET /capabilities (which touches the DB) before
 * declaring ready. /health itself is cheap and never loads models or the DB.
 */
function useSpaceStartup(): {
  status: StartupStatus;
  notReady: () => void;
  retry: () => void;
} {
  const [status, setStatus] = useState<StartupStatus>('checking');
  const [epoch, setEpoch] = useState(0);
  const startedAtRef = useRef(Date.now());
  const aliveRef = useRef(true);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const attemptRef = useRef(0);

  useEffect(() => {
    aliveRef.current = true;
    startedAtRef.current = Date.now();
    attemptRef.current = 0;
    setStatus('checking');

    const scheduleNext = (delay: number) => {
      timerRef.current = setTimeout(runAttempt, delay);
    };

    const runAttempt = async () => {
      if (!aliveRef.current) return;
      attemptRef.current += 1;
      try {
        const res = await fetchWithTimeout(`${SPACE_URL}/health`);
        if (!aliveRef.current) return;
        if (res.status === 503) throw new Error('starting');
        if (!res.ok) throw new Error(`health ${res.status}`);
        const data = (await res.json()) as {search_ready?: boolean};
        // Gate on search_ready (models loaded), not just db_ready — a
        // DB-only "ready" would let the first search stall behind the
        // SapBERT/GLiNER load.
        if (!data.search_ready) throw new Error('not ready yet');
        // Health is up — confirm the DB/indexes actually loaded.
        const caps = await fetchWithTimeout(`${SPACE_URL}/capabilities`);
        if (!aliveRef.current) return;
        if (caps.status === 503 || !caps.ok) throw new Error('capabilities not ready');
        setStatus('ready');
        return;
      } catch {
        if (!aliveRef.current) return;
        const elapsed = Date.now() - startedAtRef.current;
        if (elapsed >= GIVE_UP_AFTER_MS) {
          setStatus('failed');
          return;
        }
        setStatus(attemptRef.current <= 2 ? 'checking' : 'starting');
        scheduleNext(HEALTH_POLL_INTERVAL_MS);
      }
    };

    void runAttempt();
    return () => {
      aliveRef.current = false;
      if (timerRef.current) clearTimeout(timerRef.current);
    };
  }, [epoch]);

  // Drop back to polling (e.g. a mid-session 503 / network drop).
  const notReady = useCallback(() => {
    setStatus((s) => (s === 'ready' ? 'checking' : s));
    setEpoch((n) => n + 1);
  }, []);

  const retry = useCallback(() => setEpoch((n) => n + 1), []);

  return {status, notReady, retry};
}

export default function TerminologyDemo(): React.JSX.Element {
  const [query, setQuery] = useState('myocardial infarction');
  const [text, setText] = useState(
    'Patient with type 2 diabetes on metformin. Family history of heart attack.',
  );
  const [state, setState] = useState<SearchState>({phase: 'idle'});
  const [modeRows, setModeRows] = useState<Record<string, SearchRow[]>>({});
  const [extractState, setExtractState] = useState<ExtractState>({phase: 'idle'});
  const [concepts, setConcepts] = useState<ExtractConcept[]>([]);
  const [termDetails, setTermDetails] = useState<TermDetail[]>([]);
  const [lookup, setLookup] = useState<ReturnType<typeof lookupSummary> | null>(null);
  const {status: startup, notReady, retry} = useSpaceStartup();
  const abortRef = useRef<AbortController | null>(null);

  const runSearch = useCallback(async () => {
    if (startup !== 'ready') return;
    abortRef.current?.abort();
    const ac = new AbortController();
    abortRef.current = ac;
    setState({phase: 'loading'});
    setModeRows({});
    try {
      // All four modes at once, top COMPARE_TOP each — the side-by-side
      // comparison table. (~4 requests of the 30/min budget.)
      const results = await Promise.all(
        SEARCH_MODES.map(async (m) => [m.id, await postSearch(query, m.id, COMPARE_TOP)] as const),
      );
      if (ac.signal.aborted) return;
      const byMode: Record<string, SearchRow[]> = {};
      let any = false;
      for (const [mode, rows] of results) {
        byMode[mode] = rows;
        if (rows.length) any = true;
      }
      setModeRows(byMode);
      setState(any ? {phase: 'done'} : {phase: 'done'});
    } catch {
      if (ac.signal.aborted) return;
      notReady();
      setState({
        phase: 'error',
        message:
          'Lost contact with the demo Space — it may have gone back to sleep. ' +
          'Waiting for it to come back; the form will re-enable when it is ready.',
      });
    }
  }, [query, startup, notReady]);

  // Canonical/patient-friendly match + ICD-10-CM mapping for the first few
  // terms (rate-limit friendly). Defined before runExtract, which uses it.
  const enrichTerms = useCallback(async (found: ExtractConcept[]) => {
    const details: TermDetail[] = [];
    for (const concept of found.slice(0, MAX_TERM_DETAILS)) {
      const detail: TermDetail = {concept};
      try {
        // Canonical search: display IS the patient-friendly name.
        const rows = await postSearch(concept.matched_text ?? '', 'canonical', 1);
        if (rows[0]) {
          detail.patientFriendly = rows[0].display;
          detail.canonicalId = concept.canonical_id ?? rows[0].code;
        }
      } catch {
        // best-effort enrichment
      }
      if (concept.system && concept.code) {
        try {
          const res = await fetch(`${SPACE_URL}/fhir/ConceptMap/$translate`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: fhirParams([
              ['system', concept.system],
              ['code', concept.code],
              ['targetSystem', 'http://hl7.org/fhir/sid/icd-10-cm'],
            ]),
          });
          if (res.ok) {
            const params = (await res.json()) as {parameter?: FhirParam[]};
            const match = translateMatches(params)[0];
            if (match?.coding?.code) {
              detail.icd10 = `${match.coding.code} — ${match.coding.display ?? ''}`.trim();
            }
          }
        } catch {
          // best-effort enrichment
        }
      }
      details.push(detail);
    }
    setTermDetails(details);
    const first = found[0];
    if (first?.system && first?.code) {
      try {
        const res = await fetch(`${SPACE_URL}/fhir/CodeSystem/$lookup`, {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: fhirParams([
            ['system', first.system],
            ['code', first.code],
          ]),
        });
        if (res.ok) {
          const params = (await res.json()) as {parameter?: FhirParam[]};
          setLookup(lookupSummary(params));
        }
      } catch {
        // best-effort detail enrichment
      }
    }
  }, []);

  // Extraction is deliberately independent of search: its own trigger,
  // with `text` passed explicitly so it can never run against a stale
  // closure capture of the note.
  const runExtract = useCallback(
    async (noteText: string) => {
      setExtractState({phase: 'loading'});
      setConcepts([]);
      setTermDetails([]);
      setLookup(null);
      try {
        const res = await fetch(`${SPACE_URL}/fhir/CodeSystem/$extract`, {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: fhirParams([
            ['text', noteText],
            ['format', 'codes'],
          ]),
        });
        if (res.status === 503) {
          notReady();
          return;
        }
        if (!res.ok) throw new Error(`Space answered ${res.status}`);
        const bundle = (await res.json()) as {entry?: Array<{resource?: ExtractConcept}>};
        const found = (bundle.entry ?? [])
          .map((e) => e.resource ?? {})
          .filter((c) => c.matched_text);
        setConcepts(found);
        setExtractState({phase: 'idle'});
        void enrichTerms(found);
      } catch {
        setExtractState({
          phase: 'error',
          message: 'Extraction failed — the Space may have gone back to sleep.',
        });
      }
    },
    [notReady, enrichTerms],
  );

  const startupBanner = useMemo(() => {
    if (startup === 'ready')
      return (
        <p className="demo-status demo-status--ready">
          ✅ <strong>Ready.</strong> Connected to the terminology demo Space
          (native FHIR R4 —{' '}
          <a href={`${SPACE_URL}/fhir/metadata`} target="_blank" rel="noreferrer">
            CapabilityStatement
          </a>
          ).
        </p>
      );
    if (startup === 'checking')
      return <p className="demo-status">🔎 Checking the terminology server…</p>;
    if (startup === 'starting')
      return (
        <p className="demo-status">
          🌅 <strong>Starting up</strong> — the demo Space sleeps when idle;
          waking it and loading the terminology models takes about a minute,
          and the first start after a cold stop can take up to about
          2 minutes.
        </p>
      );
    return (
      <div className="demo-status demo-status--error">
        <p>
          ⚠️ <strong>Could not reach the demo Space</strong> after several
          minutes of trying. It may still be waking up, or temporarily
          unavailable.
        </p>
        <button className="button button--secondary" onClick={retry}>
          Retry
        </button>
      </div>
    );
  }, [startup, retry]);

  const formDisabled = startup !== 'ready' || state.phase === 'loading';

  // Side-by-side table: one row per (mode, result); best score per mode bolded.
  const compareBody = useMemo(() => {
    if (state.phase === 'loading') return <p>Searching all four modes…</p>;
    if (state.phase === 'error')
      return <p className="demo-error">⚠️ {state.message}</p>;
    if (state.phase === 'idle') return <p>Type a term and press Compare.</p>;
    const trs: React.ReactNode[] = [];
    for (const m of SEARCH_MODES) {
      const rows = modeRows[m.id] ?? [];
      if (!rows.length) {
        trs.push(
          <tr key={m.id} className="demo-mode-row">
            <td><strong>{m.label}</strong></td>
            <td colSpan={5}><em>no results</em></td>
          </tr>,
        );
        continue;
      }
      const best = Math.max(...rows.map((r) => r.score ?? 0));
      rows.forEach((r, i) => {
        const isTop = r.score === best;
        trs.push(
          <tr key={`${m.id}-${i}`} className={i === 0 ? 'demo-mode-row' : undefined}>
            {i === 0 ? (
              <td><strong>{m.label}</strong></td>
            ) : (
              <td></td>
            )}
            <td>{isTop ? <strong>{r.display}</strong> : r.display}</td>
            <td>
              <code>{r.code}</code>
            </td>
            <td>
              <code>{r.system?.replace(/^https?:\/\//, '')}</code>
            </td>
            <td>{isTop ? <strong>{r.score?.toFixed(3)}</strong> : r.score?.toFixed(3)}</td>
            <td>
              {r.grade ? (
                <span className={GRADE_STYLES[r.grade] ?? 'badge'}>{r.grade}</span>
              ) : null}
            </td>
          </tr>,
        );
      });
    }
    return (
      <table>
        <thead>
          <tr>
            <th>Mode</th>
            <th>Display</th>
            <th>Code</th>
            <th>System</th>
            <th>Score</th>
            <th>Grade</th>
          </tr>
        </thead>
        <tbody>{trs}</tbody>
      </table>
    );
  }, [state, modeRows]);

  // Annotation viewer: the note with each identified term highlighted.
  const annotatedNote = useMemo(() => {
    if (!concepts.length) return null;
    const marks = concepts
      .filter((c) => c.span_start != null && c.span_end != null)
      .sort((a, b) => (a.span_start ?? 0) - (b.span_start ?? 0));
    if (!marks.length) return null;
    const parts: React.ReactNode[] = [];
    let cursor = 0;
    marks.forEach((c, i) => {
      if ((c.span_start ?? 0) > cursor) parts.push(<span key={`t${i}`}>{text.slice(cursor, c.span_start)}</span>);
      parts.push(
        <mark key={`m${i}`} title={`${c.ner_label ?? ''} · ${c.status ?? ''} · ${c.display ?? ''}`}>
          {text.slice(c.span_start, c.span_end)}
        </mark>,
      );
      cursor = c.span_end ?? cursor;
    });
    if (cursor < text.length) parts.push(<span key="tail">{text.slice(cursor)}</span>);
    return <p className="terminology-demo__annotated">{parts}</p>;
  }, [concepts, text]);

  return (
    <div className="terminology-demo">
      {startupBanner}

      <div className="terminology-demo__controls">
        <input
          type="text"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && runSearch()}
          placeholder="e.g. heart attack, HbA1c, amoxicillin"
          maxLength={200}
          disabled={formDisabled}
        />
        <button
          className="button button--primary"
          onClick={() => void runSearch()}
          disabled={formDisabled}>
          Compare all modes
        </button>
      </div>
      <p className="demo-note">
        First request after idle may take up to ~2 minutes while the Space
        wakes. <strong>CPT is excluded</strong> due to AMA licensing. Limited
        to 30 requests/min per IP —{' '}
        <a href={`${SPACE_URL}`} target="_blank" rel="noreferrer">
          Space details
        </a>
        .
      </p>

      <h4>Side-by-side — FHIR <code>CodeSystem/$search</code>, all four modes (top {COMPARE_TOP}, best per mode in bold)</h4>
      {compareBody}

      <h4>Notes extraction — <code>CodeSystem/$extract</code></h4>
      <div className="terminology-demo__controls">
        <textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          rows={3}
          maxLength={4000}
          disabled={startup !== 'ready'}
        />
        <button
          className="button button--secondary"
          onClick={() => void runExtract(text)}
          disabled={startup !== 'ready' || extractState.phase === 'loading'}>
          {extractState.phase === 'loading' ? 'Extracting…' : 'Extract concepts'}
        </button>
      </div>
      {extractState.phase === 'error' && (
        <p className="demo-error">⚠️ {extractState.message}</p>
      )}
      {annotatedNote}
      {termDetails.length > 0 && (
        <table>
          <thead>
            <tr>
              <th>Term</th>
              <th>Type</th>
              <th>Status</th>
              <th>Patient-friendly (canonical)</th>
              <th>ICD-10-CM</th>
            </tr>
          </thead>
          <tbody>
            {termDetails.map((d, i) => (
              <tr key={i}>
                <td>
                  <strong>{d.concept.matched_text}</strong>
                  <br />
                  <small>
                    <code>{d.concept.source}/{d.concept.code}</code>
                  </small>
                </td>
                <td>{d.concept.ner_label}</td>
                <td>
                  {d.concept.status ? (
                    <span className={STATUS_STYLES[d.concept.status] ?? 'badge'}>
                      {d.concept.status}
                    </span>
                  ) : null}
                </td>
                <td>
                  {d.patientFriendly ?? '—'}
                  {d.canonicalId ? (
                    <br />
                  ) : null}
                  {d.canonicalId ? (
                    <small>
                      <code>{d.canonicalId}</code>
                    </small>
                  ) : null}
                </td>
                <td>{d.icd10 ? <code>{d.icd10}</code> : '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {lookup && (lookup.display || lookup.systemName) && (
        <>
          <h4>Code detail — <code>CodeSystem/$lookup</code></h4>
          <p>
            <strong>{lookup.display ?? lookup.systemName}</strong>
            {lookup.cui ? (
              <>
                {' '}
                — UMLS CUI <code>{lookup.cui}</code>
              </>
            ) : null}
          </p>
        </>
      )}
    </div>
  );
}
