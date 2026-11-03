import React, {useCallback, useEffect, useMemo, useRef, useState} from 'react';
import {
  FhirParam,
  SearchRow,
  fhirParams,
  lookupSummary,
  searchRows,
  translateMatches,
} from './terminologyFhir';

/**
 * Live terminology demo backed by the fhir4ds terminology-demo-server HF
 * Space (https://fhir4ds-terminology-demo-server.hf.space), which serves
 * the REAL medterm4ds FHIR R4 terminology API. All operations below are
 * native FHIR R4 operations using FHIR Parameters request bodies:
 *
 *  - term search: POST /fhir/CodeSystem/$search   (searchMode lexical |
 *    semantic | canonical | hybrid) — response is a FHIR Bundle searchset
 *    with ranking (score + match-grade extension) on each ENTRY's
 *    `search` element
 *  - annotations: POST /fhir/CodeSystem/$extract  — FHIR Bundle of found
 *    concepts (own button; deliberately NOT chained to search, so it can
 *    never run against a stale `text` closure)
 *  - code detail: POST /fhir/CodeSystem/$lookup   — FHIR Parameters
 *  - code mapping: POST /fhir/ConceptMap/$translate — FHIR Parameters
 *  - CapabilityStatement: GET /fhir/metadata
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

const SEARCH_MODES = [
  {id: 'lexical', label: 'Lexical', hint: 'BM25 exact-term matching'},
  {id: 'semantic', label: 'Semantic', hint: 'SapBERT embedding similarity'},
  {id: 'canonical', label: 'Canonical', hint: 'anchored value-set cards'},
  {id: 'hybrid', label: 'Hybrid', hint: 'lexical + semantic combined'},
];

const GRADE_STYLES: Record<string, string> = {
  certain: 'badge badge--success',
  exact: 'badge badge--success',
  probable: 'badge badge--info',
  possible: 'badge badge--warning',
};

type ExtractEntry = {
  resource?: {
    code?: string;
    display?: string;
    matched_text?: string;
    status?: string;
    system?: string;
  };
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
  const [mode, setMode] = useState('hybrid');
  const [text, setText] = useState(
    'Patient with type 2 diabetes on metformin. Family history of heart attack.',
  );
  const [state, setState] = useState<SearchState>({phase: 'idle'});
  const [rows, setRows] = useState<SearchRow[]>([]);
  const [extractState, setExtractState] = useState<ExtractState>({phase: 'idle'});
  const [extractBundle, setExtractBundle] = useState<ExtractEntry[]>([]);
  const [lookup, setLookup] = useState<ReturnType<typeof lookupSummary> | null>(null);
  const [translate, setTranslate] = useState<ReturnType<typeof translateMatches>>([]);
  const {status: startup, notReady, retry} = useSpaceStartup();
  const abortRef = useRef<AbortController | null>(null);

  const runSearch = useCallback(async () => {
    if (startup !== 'ready') return;
    abortRef.current?.abort();
    const ac = new AbortController();
    abortRef.current = ac;
    setState({phase: 'loading'});
    setRows([]);
    try {
      const res = await fetch(`${SPACE_URL}/fhir/CodeSystem/$search`, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: fhirParams([
          ['query', query],
          ['searchMode', mode],
          ['count', 10],
        ]),
        signal: ac.signal,
      });
      if (res.status === 503) {
        notReady();
        return;
      }
      if (res.status === 429) {
        setState({phase: 'error', message: 'Rate limit reached — wait a minute and try again.'});
        return;
      }
      if (!res.ok) throw new Error(`Space answered ${res.status}`);
      const bundle = await res.json();
      setRows(searchRows(bundle));
      setState({phase: 'done'});
    } catch (err) {
      if (ac.signal.aborted) return;
      notReady();
      setState({
        phase: 'error',
        message:
          'Lost contact with the demo Space — it may have gone back to sleep. ' +
          'Waiting for it to come back; the form will re-enable when it is ready.',
      });
    }
  }, [query, mode, startup, notReady]);

  // Extraction is deliberately independent of search: its own trigger,
  // with `text` passed explicitly so it can never run against a stale
  // closure capture of the note.
  const runExtract = useCallback(
    async (noteText: string) => {
      setExtractState({phase: 'loading'});
      setExtractBundle([]);
      setLookup(null);
      setTranslate([]);
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
        const bundle = (await res.json()) as {entry?: ExtractEntry[]};
        setExtractBundle(bundle.entry ?? []);
        setExtractState({phase: 'idle'});
        const first = bundle.entry?.find(
          (e) => e.resource?.system && e.resource?.code,
        )?.resource;
        if (first?.system && first?.code) {
          void runDetail(first.system, first.code);
        }
      } catch {
        setExtractState({
          phase: 'error',
          message: 'Extraction failed — the Space may have gone back to sleep.',
        });
      }
    },
    [notReady],
  );

  const runDetail = useCallback(async (system: string, code: string) => {
    try {
      const res = await fetch(`${SPACE_URL}/fhir/CodeSystem/$lookup`, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: fhirParams([
          ['system', system],
          ['code', code],
        ]),
      });
      if (res.ok) {
        const params = (await res.json()) as {parameter?: FhirParam[]};
        setLookup(lookupSummary(params));
      }
      const tr = await fetch(`${SPACE_URL}/fhir/ConceptMap/$translate`, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: fhirParams([
          ['system', system],
          ['code', code],
          ['targetSystem', 'http://hl7.org/fhir/sid/icd-10-cm'],
        ]),
      });
      if (tr.ok) {
        const tp = (await tr.json()) as {parameter?: FhirParam[]};
        setTranslate(translateMatches(tp));
      }
    } catch {
      // best-effort detail enrichment
    }
  }, []);

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

  const resultsBody = useMemo(() => {
    if (state.phase === 'loading') return <p>Searching…</p>;
    if (state.phase === 'error')
      return <p className="demo-error">⚠️ {state.message}</p>;
    if (state.phase === 'idle') return <p>Type a term and press Search.</p>;
    if (!rows.length) return <p>No results for this query.</p>;
    return (
      <table>
        <thead>
          <tr>
            <th>Display</th>
            <th>Code</th>
            <th>System</th>
            <th>Score</th>
            <th>Grade</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i}>
              <td>{r.display}</td>
              <td>
                <code>{r.code}</code>
              </td>
              <td>
                <code>{r.system?.replace(/^https?:\/\//, '')}</code>
              </td>
              <td>{r.score != null ? r.score.toFixed(3) : ''}</td>
              <td>
                {r.grade ? (
                  <span className={GRADE_STYLES[r.grade] ?? 'badge'}>{r.grade}</span>
                ) : null}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    );
  }, [state, rows]);

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
        <select
          value={mode}
          onChange={(e) => setMode(e.target.value)}
          disabled={formDisabled}>
          {SEARCH_MODES.map((m) => (
            <option key={m.id} value={m.id}>
              {m.label} — {m.hint}
            </option>
          ))}
        </select>
        <button
          className="button button--primary"
          onClick={() => void runSearch()}
          disabled={formDisabled}>
          Search
        </button>
      </div>

      <h4>Results — FHIR Bundle from <code>CodeSystem/$search</code></h4>
      {resultsBody}

      <h4>Clinical-note extraction — <code>CodeSystem/$extract</code></h4>
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
      {extractBundle.length > 0 && (
        <table>
          <thead>
            <tr>
              <th>Matched text</th>
              <th>Concept</th>
              <th>Code</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>
            {extractBundle.slice(0, 12).map((e, i) => (
              <tr key={i}>
                <td>{e.resource?.matched_text}</td>
                <td>{e.resource?.display}</td>
                <td>
                  <code>{e.resource?.code}</code>
                </td>
                <td>{e.resource?.status}</td>
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

      {translate.length > 0 && (
        <>
          <h4>Mapping to ICD-10-CM — <code>ConceptMap/$translate</code></h4>
          <table>
            <thead>
              <tr>
                <th>Equivalence</th>
                <th>Target code</th>
                <th>Target display</th>
              </tr>
            </thead>
            <tbody>
              {translate.map((m, i) => (
                <tr key={i}>
                  <td>{m.equivalence}</td>
                  <td>
                    <code>{m.coding?.code}</code>
                  </td>
                  <td>{m.coding?.display}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
    </div>
  );
}
